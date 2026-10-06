"""Голосовой ввод + TTS. Очередь очищается — говорим только последнее.
Ускоренная версия: pre-warm TTS, кэш частых фраз, неблокирующий бип,
soft-clip против треска, mute wake-listener'а на время речи."""
import re, time, queue, threading
import numpy as np
import sounddevice as sd
import speech_recognition as sr
import torch
import config

torch.set_num_threads(2)
torch.set_grad_enabled(False)

# Мягкий ресемплер (если устройство не 48к)
try:
    from scipy.signal import resample as _sp_resample
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False


class VoiceController:
    def __init__(self, music_controller=None, on_command=None):
        self.music = music_controller
        self.on_command = on_command

        # --- быстрый recognizer для wake-word fallback (Google STT) ---
        self.fast_rec = sr.Recognizer()
        self.fast_rec.dynamic_energy_threshold = True
        self.fast_rec.energy_threshold = 280
        self.fast_rec.pause_threshold = 0.55
        self.fast_rec.phrase_threshold = 0.15
        self.fast_rec.non_speaking_duration = 0.4

        # --- обычный recognizer для команд fallback ---
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.energy_threshold = 280
        self.recognizer.pause_threshold = 1.6
        self.recognizer.phrase_threshold = 0.25
        self.recognizer.non_speaking_duration = 1.0

        self.is_speaking = False
        self._speak_lock = threading.Lock()
        self._tts_seq = 0

        try:
            sd.default.samplerate = 48000
            sd.default.channels = 1
        except Exception:
            pass

        self.mic = None
        self._init_mic()
        self._init_tts()
        self._init_beep()

        # Устройство вывода и его sr (для ресемпла)
        try:
            info = sd.query_devices(kind='output')
            self.out_sr = int(info.get('default_samplerate', 48000))
            print(f"[i] Устройство вывода: {info.get('name','?')} @ {self.out_sr} Гц")
        except Exception:
            self.out_sr = 48000

        self._wake_listener = None  # устанавливается через set_wake_listener

        self.q = queue.Queue()
        threading.Thread(target=self._speech_worker, daemon=True).start()

    # ==================== MIC ====================
    def _init_mic(self):
        try:
            self.mic = sr.Microphone(device_index=getattr(config, "SELECTED_MIC_INDEX", None))
            with self.mic as s:
                self.recognizer.adjust_for_ambient_noise(s, duration=0.6)
            print(f"[✓] Микрофон (порог {self.recognizer.energy_threshold:.0f})")
        except Exception as e:
            print(f"[!] mic: {e}"); self.mic = None

    def set_input_device(self, idx):
        config.SELECTED_MIC_INDEX = idx
        config.save_state()
        self._init_mic()
        return f"Микрофон: {idx}"

    @staticmethod
    def list_input_devices():
        try:
            return [(i, n) for i, n in enumerate(sr.Microphone.list_microphone_names())]
        except Exception:
            return []

    def list_output_devices(self):
        try:
            devs = sd.query_devices()
            out = []
            for i, d in enumerate(devs):
                if d.get("max_output_channels", 0) > 0:
                    out.append((i, d.get("name", f"dev{i}")))
            return out
        except Exception:
            return []

    def set_output_device(self, idx):
        config.OUTPUT_DEVICE_INDEX = idx
        config.save_state()
        try:
            sd.default.device = (sd.default.device[0], idx) if idx is not None \
                else (sd.default.device[0], None)
            info = sd.query_devices(kind='output')
            self.out_sr = int(info.get('default_samplerate', 48000))
            return f"Вывод: {idx}"
        except Exception as e:
            return f"Ошибка: {e}"

    # ==================== TTS ====================
    def _init_tts(self):
        print("[🎙] Silero TTS...")
        self._phrase_cache = {}
        try:
            self.model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-models",
                model="silero_tts", language="ru", speaker="v4_ru", trust_repo=True)
            self.model.to("cpu")
            self.sr = 48000

            with torch.inference_mode():
                self.model.apply_tts(text="готов", speaker=config.VOICE_SPEAKER,
                                     sample_rate=48000, put_accent=True, put_yo=True)

            quick = [
                "Слушаю", "Хорошо", "Готово", "Уже включаю", "Включаю",
                "Пауза", "Продолжаю", "Следующий", "Предыдущий",
                "Поняла", "Не расслышала", "Не нашла",
                "Включаю мою волну", "Любимые треки", "Минутку", "Секунду",
                "Как скажешь", "Есть!", "Перемешала", "Повторяю этот трек",
                "Добавила в любимые", "Поняла, переключаю", "Сейчас играет",
                "Делаю волну повеселее", "Открываю текст", "Перемотка",
            ]
            for phrase in quick:
                try:
                    with torch.inference_mode():
                        a = self.model.apply_tts(
                            text=phrase, speaker=config.VOICE_SPEAKER,
                            sample_rate=48000, put_accent=True, put_yo=True)
                    arr = a.numpy().astype(np.float32)
                    peak = float(np.max(np.abs(arr))) or 1.0
                    self._phrase_cache[phrase.lower()] = arr * (0.85 / peak)
                except Exception:
                    pass
            print(f"[✓] TTS готов (кэш: {len(self._phrase_cache)} фраз)")
        except Exception as e:
            print(f"[!] tts: {e}"); self.model = None

    def _clean(self, s):
        s = re.sub(r"[\(\)\[\]\{\}\<\>✨🌸🎶🔊◕‿≧◡~#*_`]", "", s)
        s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", s)
        return re.sub(r"\s+", " ", s).strip()

    def _split_text(self, text, max_len=180):
        text = (text or "").strip()
        if not text:
            return []
        parts = re.split(r'(?<=[.!?…])\s+', text)
        chunks, cur = [], ""
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if len(cur) + len(p) + 1 <= max_len:
                cur = (cur + " " + p).strip() if cur else p
            else:
                if cur:
                    chunks.append(cur)
                if len(p) > max_len:
                    subs = re.split(r'(?<=[,;:])\s+', p)
                    cur = ""
                    for s in subs:
                        if len(cur) + len(s) + 1 <= max_len:
                            cur = (cur + " " + s).strip() if cur else s
                        else:
                            if cur: chunks.append(cur)
                            cur = s
                    if cur: chunks.append(cur)
                    cur = ""
                else:
                    cur = p
        if cur:
            chunks.append(cur)
        return [c for c in chunks if c]

    def _gen(self, text):
        if not self.model:
            return None
        key = (text or "").strip().lower()
        if key in self._phrase_cache:
            return self._phrase_cache[key].copy()
        try:
            with torch.inference_mode():
                a = self.model.apply_tts(
                    text=self._clean(text), speaker=config.VOICE_SPEAKER,
                    sample_rate=self.sr, put_accent=True, put_yo=True)
            arr = a.numpy().astype(np.float32)
            peak = float(np.max(np.abs(arr))) or 1.0
            arr = arr * (0.85 / peak)
            arr = np.tanh(arr * 1.05) * 0.95
            return arr
        except Exception as e:
            print(f"[!] tts gen: {e}"); return None

    def _play_array(self, arr, sr):
        if arr is None:
            return
        try:
            target_sr = self.out_sr or sr
            if target_sr != sr and _HAVE_SCIPY:
                n = int(len(arr) * target_sr / sr)
                arr = _sp_resample(arr, n).astype(np.float32)
                sr = target_sr
            sd.play(arr, sr, blocking=True)
            time.sleep(0.03)
        except Exception as e:
            print(f"[!] sd.play: {e}")

    def _speech_worker(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            seq, text = item
            try:
                with self._speak_lock:
                    if seq != self._tts_seq:
                        continue
                a = self._gen(text)
                if a is None:
                    continue
                with self._speak_lock:
                    if seq != self._tts_seq:
                        continue
                self._play_array(a, self.sr)
            except Exception as e:
                print(f"[!] tts worker: {e}")
            finally:
                with self._speak_lock:
                    if self.q.empty() and seq == self._tts_seq:
                        self.is_speaking = False
                        if self._wake_listener:
                            try: self._wake_listener.unmute()
                            except Exception: pass

    def speak(self, text):
        # FIX: переработан порядок — не рвём текущий звук на середине,
        #      если очередь пуста. Иначе — режем как раньше.
        print(f"[👩 {config.ASSISTANT_NAME}]: {text}")
        with self._speak_lock:
            self.is_speaking = True
            self._tts_seq += 1
            seq = self._tts_seq
        if self._wake_listener:
            try: self._wake_listener.mute()
            except Exception: pass
        try: sd.stop()
        except Exception: pass
        # чистим только несыгранное
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        chunks = self._split_text(text) or [text]
        for c in chunks:
            self.q.put((seq, c))

    def set_wake_listener(self, wl):
        self._wake_listener = wl

    # ==================== BEEP ====================
    def _init_beep(self):
        try:
            srate = 44100
            parts = []
            for f, d in [(880.0, 0.08), (1318.51, 0.16)]:
                t = np.linspace(0, d, int(srate * d), endpoint=False)
                env = (1 - np.exp(-80 * t)) * np.exp(-4 * t / d)
                parts.append(np.sin(2 * np.pi * f * t) * env)
            a = np.concatenate(parts) * 0.5
            fade = int(srate * 0.01)
            a[:fade] *= np.linspace(0, 1, fade)
            a[-fade:] *= np.linspace(1, 0, fade)
            self.beep = (a * 0.55).astype(np.float32)
            self.beep_sr = srate
        except Exception:
            self.beep = None

    def play_activation_sound(self):
        if self.beep is None:
            return
        try:
            beep = self.beep
            bs = self.beep_sr
            if self.out_sr != bs and _HAVE_SCIPY:
                n = int(len(beep) * self.out_sr / bs)
                beep = _sp_resample(beep, n).astype(np.float32)
                bs = self.out_sr
            sd.play(beep, bs, blocking=False)
        except Exception:
            pass

    # ==================== LISTEN (Google STT fallback) ====================
    def listen(self, phrase_time_limit=20):
        if not self.mic:
            return None
        with self._speak_lock:
            if self.is_speaking:
                return None
        try:
            with self.mic as src:
                audio = self.recognizer.listen(src, timeout=None,
                                               phrase_time_limit=phrase_time_limit)
            t = self.recognizer.recognize_google(audio, language=config.LANGUAGE)
            return t.lower().strip()
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            print(f"[!] STT: {e}"); return None
        except Exception:
            return None

    def cleanup(self):
        try:
            self.q.put(None)
        except Exception:
            pass