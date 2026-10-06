"""Elena — нативное окно pywebview + трей + голосовой цикл (Vosk)."""
import os, sys, time, threading
import webview
import config
from music_controller import MusicController
from voice_controller import VoiceController
from ai_brain import AIBrain
from server import Server, get_local_ip
from wake_listener import WakeListener
import autostart


class TrayIcon:
    def __init__(self, window, music):
        self.window = window
        self.music = music
        self.icon = None
        self._available = False
        self._start()

    def _start(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            print("[i] pystray/Pillow не установлены — трей выключен")
            return
        try:
            size = 64
            img = Image.new("RGB", (size, size), (168, 85, 247))
            d = ImageDraw.Draw(img)
            for y in range(size):
                t = y / size
                d.line([(0, y), (size, y)], fill=(
                    int(168 * (1 - t) + 236 * t), int(85 * (1 - t) + 72 * t),
                    int(247 * (1 - t) + 153 * t)))
            cx, cy = size // 2, size // 2; f = int(size * 0.5)
            d.ellipse([cx - f // 2, cy - f // 3, cx + f // 4, cy + f // 2], fill="white")
            d.rectangle([cx + f // 4 - f // 10, cy - f // 2, cx + f // 4, cy + f // 6],
                        fill="white")

            def on_show(i, item):
                try: self.window.show()
                except Exception: pass

            def on_play(i, item):
                threading.Thread(target=self.music.toggle_play, daemon=True).start()

            def on_next(i, item):
                threading.Thread(target=self.music.next_track, daemon=True).start()

            def on_prev(i, item):
                threading.Thread(target=self.music.previous_track, daemon=True).start()

            def on_wave(i, item):
                threading.Thread(target=self.music.play_my_wave, daemon=True).start()

            def on_quit(i, item):
                try: i.stop()
                except Exception: pass
                os._exit(0)

            menu = pystray.Menu(
                pystray.MenuItem("Открыть окно", on_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Играть / Пауза", on_play),
                pystray.MenuItem("Следующий трек", on_next),
                pystray.MenuItem("Предыдущий трек", on_prev),
                pystray.MenuItem("Моя волна", on_wave),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", on_quit),
            )
            self.icon = pystray.Icon("Elena", img,
                                     f"🎵 {config.ASSISTANT_NAME}", menu)
            threading.Thread(target=self.icon.run, daemon=True).start()
            self._available = True
            print("[✓] Трей готов")
        except Exception as e:
            print(f"[!] Трей: {e}")

    def stop(self):
        if self._available and self.icon:
            try: self.icon.stop()
            except Exception: pass


class ElenaApp:
    def __init__(self):
        print("=" * 60)
        print(f"  Елена — {config.ASSISTANT_NAME}")
        print("=" * 60)

        self.music = MusicController()
        self.ai = AIBrain()
        self.voice = VoiceController(music_controller=self.music)

        # ==================== VOSK wake-listener ====================
        self._wake = None
        self._voice_running = True
        self._start_wake_listener()

        # ==================== HTTP server ====================
        self.server = Server(self.music, self.ai, self.voice,
                             os.path.dirname(os.path.abspath(__file__)),
                             on_event=None)
        self.server.start()
        print("[i] Ожидание готовности сервера...")
        if self.server.wait_ready(timeout=10.0):
            print("[✓] Сервер отвечает")
        else:
            print("[!] Сервер не ответил — открываю окно всё равно")

        ip = get_local_ip()
        http_url = f"http://127.0.0.1:{config.SERVER_PORT}/"
        print(f"[✓] Локально:   {http_url}")
        print(f"[✓] В сети:     http://{ip}:{config.SERVER_PORT}")
        print(f"[✓] TV/Phone:   http://{ip}:{config.SERVER_PORT}/tv")
        if config.SERVER_HTTPS and self.server.cert:
            print(f"[✓] В сети SSL: https://{ip}:{config.SERVER_HTTPS_PORT}")

        # ==================== Window ====================
        self.window = webview.create_window(
            f"🎵 {config.ASSISTANT_NAME}",
            http_url,
            width=1280, height=820,
            min_size=(900, 600),
            background_color="#0a0a0d",
            text_select=False,
        )
        self.tray = TrayIcon(self.window, self.music)
        threading.Timer(1.2, self._dark_title).start()
        threading.Timer(3.0, self._ask_autostart).start()

    # ==================== Vosk ====================
    def _start_wake_listener(self):
        if not config.VOSK_ENABLED:
            print("[i] Vosk выключен в config.py")
            self._start_voice_loop_fallback()
            return
        model_dir = config.VOSK_MODEL_DIR
        if not model_dir or not os.path.isdir(model_dir):
            print(f"[!] Vosk-модель не найдена: {model_dir}")
            print("    Запусти: python download_vosk.py")
            self._start_voice_loop_fallback()
            return

        self._wake = WakeListener(
            model_dir=model_dir,
            wake_words=config.WAKE_WORDS,
            sample_rate=config.VOSK_SAMPLE_RATE,
            block_size=config.VOSK_BLOCK_SIZE,
            on_wake=self._on_wake_word,
            on_command=self._on_voice_command,
            on_empty=self._on_voice_empty,
            big_model_dir=getattr(config, "VOSK_MODEL_DIR_BIG", None),
            device=getattr(config, "SELECTED_MIC_INDEX", None),
        )
        self.voice.set_wake_listener(self._wake)
        self._wake.start()

    def _on_wake_word(self):
        """Мгновенная реакция на wake-слово — beep + визуальная активация."""
        self.voice.play_activation_sound()
        self.music.activate()

    def _on_voice_empty(self):
        """Пользователь сказал wake-слово и промолчал."""
        print("[i] wake без команды — говорю 'Слушаю'")
        self.voice.speak("Слушаю")

    def _on_voice_command(self, text):
        """Пришла команда из Vosk. Google STT не участвует."""
        text = (text or "").strip()
        if not text:
            self._on_voice_empty()
            return

        clean = self.ai.extract_wake_word(text)
        if clean is None:
            clean = text
        elif clean == "просто_имя":
            self._on_voice_empty()
            return

        print(f"[🎤 vosk]: {text}  →  {clean}")

        try:
            ct = self.music.get_track_metadata()
            ctid = ct.get("id") if ct else None
            action, params, reply = self.ai.process(
                clean, current_track_id=ctid, current_track=ct)
            print(f"[👩] {reply}")
            self.voice.speak(reply)
            try:
                self.server._exec(action, params)
            except Exception as e:
                print(f"[!] exec {action}: {e}")
        except Exception as e:
            print(f"[!] voice cmd: {e}")

    # ==================== Google STT fallback ====================
    def _start_voice_loop_fallback(self):
        """Резервный цикл (если Vosk недоступен)."""
        def loop():
            time.sleep(3)
            while self._voice_running:
                try:
                    text = self.voice.listen()
                    if not text or self.voice.is_speaking:
                        continue
                    clean = self.ai.extract_wake_word(text)
                    if clean is None:
                        continue
                    self.music.activate()
                    if clean == "просто_имя":
                        self.voice.play_activation_sound()
                        self.voice.speak("Слушаю")
                        continue
                    self.voice.play_activation_sound()
                    ct = self.music.get_track_metadata()
                    ctid = ct.get("id") if ct else None
                    action, params, reply = self.ai.process(
                        clean, current_track_id=ctid, current_track=ct)
                    print(f"[👩] {reply}")
                    self.voice.speak(reply)
                    try:
                        self.server._exec(action, params)
                    except Exception as e:
                        print(f"[!] exec {action}: {e}")
                except Exception as e:
                    print(f"[!] voice loop: {e}")
                    time.sleep(0.3)
        threading.Thread(target=loop, daemon=True).start()

    # ==================== Dark title ====================
    def _dark_title(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            title = f"🎵 {config.ASSISTANT_NAME}"
            u32 = ctypes.windll.user32
            hwnd = u32.FindWindowW(None, title)
            if not hwnd:
                found = []
                EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                              ctypes.c_void_p, ctypes.c_void_p)
                def cb(h, _):
                    buf = ctypes.create_unicode_buffer(512)
                    u32.GetWindowTextW(h, buf, 512)
                    if title in buf.value or "Elena" in buf.value or "Лена" in buf.value:
                        found.append(h)
                    return True
                u32.EnumWindows(EnumProc(cb), 0)
                hwnd = found[0] if found else 0
            if not hwnd:
                print("[i] HWND окна не найден — тёмный тайтлбар пропущен")
                return
            dwm = ctypes.windll.dwmapi
            for attr in (19, 20):  # DWMWA_USE_IMMERSIVE_DARK_MODE
                v = ctypes.c_int(1)
                dwm.DwmSetWindowAttribute(hwnd, attr,
                                          ctypes.byref(v), ctypes.sizeof(v))
            caption = ctypes.c_int(0x0022221A)
            dwm.DwmSetWindowAttribute(hwnd, 35,
                                      ctypes.byref(caption), ctypes.sizeof(caption))
            border = ctypes.c_int(0x0022221A)
            dwm.DwmSetWindowAttribute(hwnd, 34,
                                      ctypes.byref(border), ctypes.sizeof(border))
        except Exception as e:
            print(f"[!] dark title: {e}")

    # ==================== Autostart ====================
    def _ask_autostart(self):
        if config.AUTOSTART_ASKED:
            return
        try:
            already = autostart.is_enabled()
        except Exception:
            already = False
        config.AUTOSTART_ASKED = True
        config.AUTOSTART_ENABLED = already
        config.save_state()
        if already:
            print("[✓] Автозапуск уже включён")
            return
        try:
            ok = self.window.create_confirmation_dialog(
                "Автозапуск Елены",
                "Добавить Елену в автозапуск Windows?\n\n"
                "Программа будет запускаться автоматически при входе в систему.")
        except Exception:
            ok = False
        if ok:
            try:
                autostart.set_autostart(True)
                config.AUTOSTART_ENABLED = True
                config.save_state()
                print("[✓] Автозапуск включён")
            except Exception as e:
                print(f"[!] autostart enable: {e}")


def run():
    app = ElenaApp()
    try:
        webview.start(debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        try: app._voice_running = False
        except Exception: pass
        try:
            if app._wake: app._wake.stop()
        except Exception: pass
        try: app.tray.stop()
        except Exception: pass
        try: app.voice.cleanup()
        except Exception: pass
        try: app.music.cleanup()
        except Exception: pass


if __name__ == "__main__":
    run()
