"""
Vosk-listener:
- small-модель (vosk-small-ru) мгновенно ловит wake-word на partial/final
- команда распознаётся большой моделью (vosk-ru-0.42) офлайн
- отличает хвост wake-слова от реальной команды по озвученному времени
- big-модель грузится в фоне, wake доступен сразу
"""
import io
import json
import os
import queue
import threading
import time
import wave
import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer, SetLogLevel
import config

SetLogLevel(-1)


class WakeListener:
    def __init__(self, model_dir, wake_words,
                 sample_rate=16000, block_size=2000,
                 on_wake=None, on_command=None, on_empty=None,
                 big_model_dir=None, device=None):

        self.model_dir = model_dir
        self.big_model_dir = big_model_dir
        self.wake_words = [w.lower().strip() for w in wake_words if w.strip()]
        self.sr = sample_rate
        self.bs = block_size
        self.on_wake = on_wake
        self.on_command = on_command
        self.on_empty = on_empty
        self.device = device

        self.model_small = None
        self.model_big = None
        self.rec = None

        self._stream = None
        self._q = queue.Queue()
        self._run = False
        self._thread = None

        # Состояние
        self._state = "idle"
        self._collected_audio = []
        self._voiced_ms = 0.0
        self._last_voice_ts = 0.0
        self._wake_ts = 0.0
        self._silence_start = 0.0
        self._muted = threading.Event()

        self._silence_rms = 0.006

        self.cmd_silence_ms = getattr(config, "VOSK_COMMAND_SILENCE_MS", 1100)
        self.await_ms       = getattr(config, "VOSK_AWAIT_MS", 3000)
        self.min_voiced_ms  = getattr(config, "VOSK_MIN_VOICED_MS", 400)

    # ---------- lifecycle ----------
    def _load_small(self):
        if not os.path.isdir(self.model_dir):
            print(f"[!] Vosk small: нет модели {self.model_dir}")
            return False
        print(f"[i] Загружаю Vosk-small (wake)…")
        t0 = time.time()
        self.model_small = Model(self.model_dir)
        self.rec = KaldiRecognizer(self.model_small, self.sr)
        self.rec.SetWords(True)
        print(f"[✓] Vosk-small готов за {time.time()-t0:.1f} сек")
        return True

    def _load_big_bg(self):
        """FIX: big-модель грузим в фоне, чтобы wake работал мгновенно."""
        if not self.big_model_dir or not os.path.isdir(self.big_model_dir):
            print(f"[i] Vosk-BIG не найден ({self.big_model_dir}) — команды через small")
            return
        try:
            t0 = time.time()
            self.model_big = Model(self.big_model_dir)
            print(f"[✓] Vosk-BIG готов за {time.time()-t0:.1f} сек")
        except Exception as e:
            print(f"[!] Vosk-BIG: {e}")

    def load(self):
        """Совместимость со старым API — грузит small синхронно, big в фоне."""
        if not self._load_small():
            return False
        threading.Thread(target=self._load_big_bg, daemon=True).start()
        return True

    def start(self):
        if self._run:
            return
        if self.model_small is None and not self._load_small():
            return
        # FIX: big — в фоне, wake доступен сразу
        if self.big_model_dir and self.model_big is None:
            threading.Thread(target=self._load_big_bg, daemon=True).start()
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._run = False
        if self._stream:
            try:
                self._stream.stop(); self._stream.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=1.5)

    def mute(self):   self._muted.set()
    def unmute(self): self._muted.clear()

    # ---------- audio callback ----------
    def _callback(self, indata, frames, t, status):
        if status:
            pass
        if self._muted.is_set():
            return
        self._q.put(bytes(indata))

    # ---------- main loop ----------
    def _loop(self):
        try:
            kwargs = dict(
                samplerate=self.sr, blocksize=self.bs,
                dtype='int16', channels=1, callback=self._callback,
            )
            if self.device is not None:
                kwargs["device"] = self.device
            self._stream = sd.RawInputStream(**kwargs)
            self._stream.start()
            try:
                dev = sd.query_devices(self.device) if self.device is not None \
                    else sd.query_devices(kind='input')
                name = dev.get('name', '?') if isinstance(dev, dict) else '?'
            except Exception:
                name = '?'
            print(f"[✓] Vosk wake-listener запущен (микрофон: {name})")
        except Exception as e:
            print(f"[!] Vosk stream: {e}")
            try:
                self._stream = sd.RawInputStream(
                    samplerate=self.sr, blocksize=self.bs,
                    dtype='int16', channels=1, callback=self._callback)
                self._stream.start()
                print("[✓] Vosk wake-listener запущен (микрофон по умолчанию)")
            except Exception as e2:
                print(f"[!] Vosk stream fallback: {e2}")
                return

        chunk_ms = self.bs / self.sr * 1000.0

        while self._run:
            try:
                data = self._q.get(timeout=0.4)
            except queue.Empty:
                self._check_timeouts()
                continue

            try:
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            except Exception:
                continue
            rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
            voiced = rms > self._silence_rms

            if self._state == "collecting":
                self._collected_audio.append(samples.copy())
                if voiced:
                    self._voiced_ms += chunk_ms

            if voiced:
                self._last_voice_ts = time.time()
                self._silence_start = 0.0
            else:
                if self._state == "collecting" and self._silence_start == 0.0:
                    self._silence_start = time.time()

            is_final = self.rec.AcceptWaveform(data)
            if not is_final:
                self._process_partial()
            else:
                # FIX: даже в final проверяем wake — «лена» сказанное слитно
                # без пауз терялось на partial-обработчике
                if self._state == "idle":
                    try:
                        final_txt = json.loads(
                            self.rec.Result()).get("text", "").lower().strip()
                    except Exception:
                        final_txt = ""
                    if final_txt and self._find_wake(final_txt):
                        self._state = "collecting"
                        self._wake_ts = time.time()
                        self._collected_audio = []
                        self._voiced_ms = 0.0
                        self._silence_start = 0.0
                        if self.on_wake:
                            try:
                                self.on_wake()
                            except Exception as e:
                                print(f"[!] on_wake: {e}")
                self.rec.Reset()

            self._check_silence_end()

    # ---------- partial ----------
    def _process_partial(self):
        try:
            partial = json.loads(self.rec.PartialResult()).get("partial", "").lower().strip()
        except Exception:
            return
        if len(partial) < 2:
            return

        if self._state == "idle":
            w = self._find_wake(partial)
            if w:
                self._state = "collecting"
                self._wake_ts = time.time()
                self._collected_audio = []
                self._voiced_ms = 0.0
                self._silence_start = 0.0
                self.rec.Reset()
                if self.on_wake:
                    try:
                        self.on_wake()
                    except Exception as e:
                        print(f"[!] on_wake: {e}")

    # ---------- silence end ----------
    def _check_silence_end(self):
        if self._state != "collecting":
            return
        now = time.time()

        if now - self._wake_ts > 15:
            self._finalize()
            return

        if not self._silence_start:
            return

        silence_ms = (now - self._silence_start) * 1000

        if self._voiced_ms < self.min_voiced_ms:
            if silence_ms > self.await_ms:
                self._reset_state()
                if self.on_empty:
                    try:
                        self.on_empty()
                    except Exception:
                        pass
            return

        if silence_ms > self.cmd_silence_ms:
            self._finalize()

    def _check_timeouts(self):
        if self._state != "collecting":
            return
        now = time.time()
        if now - self._wake_ts > 15:
            self._finalize()
            return
        if self._silence_start:
            silence_ms = (now - self._silence_start) * 1000
            if self._voiced_ms < self.min_voiced_ms and silence_ms > self.await_ms:
                self._reset_state()
                if self.on_empty:
                    try:
                        self.on_empty()
                    except Exception:
                        pass

    # ---------- finalize ----------
    def _finalize(self):
        audio = self._collect_audio()
        voiced_ms = self._voiced_ms
        self._reset_state()

        if audio is None or voiced_ms < self.min_voiced_ms:
            return

        text = self._transcribe_command(audio)
        if not text:
            return

        stripped = text.strip().lower().strip(",.!? ")
        for ww in self.wake_words:
            if stripped == ww:
                print(f"[i] только wake-word в аудио ('{stripped}') — игнор")
                return

        if self.on_command:
            try:
                print(f"[🎤 vosk]: {text}")
                self.on_command(text)
            except Exception as e:
                print(f"[!] on_command: {e}")

    def _reset_state(self):
        self._state = "idle"
        self._collected_audio = []
        self._voiced_ms = 0.0
        self._silence_start = 0.0
        self._last_voice_ts = 0.0
        try:
            self.rec.Reset()
        except Exception:
            pass

    # ---------- transcribe ----------
    def _collect_audio(self):
        if not self._collected_audio:
            return None
        arr = np.concatenate(self._collected_audio).astype(np.float32)
        window = int(self.sr * 0.05)
        while len(arr) > window:
            tail = arr[-window:]
            if float(np.sqrt(np.mean(tail ** 2))) > self._silence_rms:
                break
            arr = arr[:-window]
        if len(arr) < self.sr * 0.3:
            return None
        return arr

    def _transcribe_command(self, arr):
        engine = getattr(config, "COMMAND_ENGINE", "auto").lower()

        if engine == "google":
            txt = self._google_transcribe(arr)
            if txt:
                print("[🎤 google]")
                return txt
            if self.model_big:
                txt = self._vosk_transcribe(arr, self.model_big)
                if txt:
                    print("[🎤 vosk-big]")
                    return txt
            if self.model_small:
                txt = self._vosk_transcribe(arr, self.model_small)
                if txt:
                    print("[🎤 vosk-small]")
                    return txt
            return None

        if engine == "auto":
            g = self._google_transcribe(arr)
            v = None
            if self.model_big:
                v = self._vosk_transcribe(arr, self.model_big)
            elif self.model_small:
                v = self._vosk_transcribe(arr, self.model_small)
            if g and v:
                if len(g.split()) >= len(v.split()):
                    print("[🎤 google]")
                    return g
                print("[🎤 vosk-big]")
                return v
            if g:
                print("[🎤 google]")
                return g
            if v:
                print("[🎤 vosk]")
                return v
            return None

        if self.model_big:
            txt = self._vosk_transcribe(arr, self.model_big)
            if txt:
                print("[🎤 vosk-big]")
                return txt
        if self.model_small:
            txt = self._vosk_transcribe(arr, self.model_small)
            if txt:
                print("[🎤 vosk-small]")
                return txt
        return None

    def _vosk_transcribe(self, arr, model):
        try:
            rec = KaldiRecognizer(model, self.sr)
            rec.SetWords(False)
            pcm16 = np.clip(arr * 32767, -32768, 32767).astype(np.int16).tobytes()
            CHUNK = 4000
            for i in range(0, len(pcm16), CHUNK):
                rec.AcceptWaveform(pcm16[i:i + CHUNK])
            final = json.loads(rec.FinalResult()).get("text", "").lower().strip()
            return final or None
        except Exception as e:
            print(f"[!] vosk transcribe: {e}")
            return None

    def _google_transcribe(self, arr):
        try:
            import speech_recognition as sr
            pcm16 = np.clip(arr * 32767, -32768, 32767).astype(np.int16).tobytes()
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.sr)
                w.writeframes(pcm16)
            buf.seek(0)
            r = sr.Recognizer()
            with sr.AudioFile(buf) as src:
                audio = r.record(src)
            text = r.recognize_google(audio, language=config.LANGUAGE)
            return text.lower().strip() if text else None
        except Exception as e:
            print(f"[!] google stt: {e}")
            return None

    # ---------- wake matching ----------
    def _find_wake(self, text):
        variants = {
            "лена":    ["лена", "ленна", "ле на", "лёна", "лену", "лены", "лене"],
            "елена":   ["елена", "элена", "е лена"],
            "леночка": ["леночка", "лёночка"],
            "лен":     ["лен", "лён"],
            "алена":   ["алена", "алёна"],
            "алиса":   ["алиса", "алису", "алисы"],
            "вася":    ["вася", "васю", "васе", "вась", "васёк", "бася",
                        "васи", "басю"],
        }
        tokens = " " + text + " "
        for w in self.wake_words:
            for v in variants.get(w, [w]):
                if (f" {v} " in tokens
                        or tokens.startswith(f" {v} ")
                        or tokens.endswith(f" {v} ")
                        or f" {v}," in tokens):
                    return v
        for w in self.wake_words:
            if w in text:
                return w
        return None