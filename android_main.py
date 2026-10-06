"""
Android / Android TV launcher.
Стартует FastAPI + MusicController на localhost и открывает системный браузер
или WebView с TV-интерфейсом.

Сборка: buildozer -v android debug
"""
import os, sys, time, threading, socket

os.environ.setdefault("ELENA_ANDROID", "1")

BASE = os.path.dirname(os.path.abspath(__file__))
# На Android Kivy кладёт файлы в ANDROID_PRIVATE
if "ANDROID_ARGUMENT" in os.environ:
    BASE = os.environ.get("ANDROID_PRIVATE") or BASE
    os.chdir(BASE)

sys.path.insert(0, BASE)

import config
# На мобиле — только локальный HTTP, без HTTPS и без встроенного микрофона
config.SERVER_HOST = "127.0.0.1"
config.SERVER_HTTPS = False
config.VOSK_ENABLED = False
config.SERVER_AUTOSTART = False

from music_controller import MusicController
from ai_brain import AIBrain
from server import Server


def _wait_port(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
            s.close()
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _launch_browser(url):
    try:
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(intent)
        return True
    except Exception as e:
        print(f"[!] jnius: {e}")
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception as e:
        print(f"[!] webbrowser: {e}")
    return False


def main():
    print("=" * 60)
    print("  Elena Android / Android TV")
    print("=" * 60)

    music = MusicController()
    ai = AIBrain()
    try:
        voice = None  # на мобиле используется Web Speech API в браузере
    except Exception:
        voice = None

    srv = Server(music, ai, voice, BASE)
    srv.start()

    if not _wait_port(config.SERVER_PORT, timeout=25):
        print("[!] Локальный сервер не поднялся")
        return

    url = f"http://127.0.0.1:{config.SERVER_PORT}/tv"
    print(f"[✓] Открываю {url}")
    _launch_browser(url)

    # Держим процесс живым, пока пользователь не убьёт приложение
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            music.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    main()