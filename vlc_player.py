"""VLC-плеер: приоритет x64, полный try/except, fallback на pygame."""
import os
import sys
import glob
import time
import ctypes
import threading
import platform

VLC_DIR = None
VLC_ARCH_MISMATCH = False
_VLC_IMPORT_ERROR = None


def _python_is_64bit():
    return sys.maxsize > 2 ** 32


def _find_vlc_dir():
    """Ищет x64 VLC в приоритете. Возвращает (dir, mismatch_flag)."""
    global VLC_ARCH_MISMATCH
    sysname = platform.system()
    is64 = _python_is_64bit()

    env_dir = os.environ.get("VLC_DIR") or os.environ.get("VLC_HOME")
    if env_dir and os.path.isdir(env_dir):
        return env_dir, False

    candidates_64 = []
    candidates_32 = []

    if sysname == "Windows":
        candidates_64 += [
            r"C:\Program Files\VideoLAN\VLC",
            os.path.expandvars(r"%ProgramFiles%\VideoLAN\VLC"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\VideoLAN\VLC"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "vlc64"),
        ]
        candidates_32 += [
            r"C:\Program Files (x86)\VideoLAN\VLC",
            os.path.expandvars(r"%ProgramFiles(x86)%\VideoLAN\VLC"),
        ]
        dll_name = "libvlc.dll"
    elif sysname == "Darwin":
        candidates_64 += [
            "/Applications/VLC.app/Contents/MacOS/lib",
            os.path.expanduser("~/Applications/VLC.app/Contents/MacOS/lib"),
        ]
        dll_name = "libvlc.dylib"
    else:
        candidates_64 += ["/usr/lib/x86_64-linux-gnu", "/usr/lib", "/usr/local/lib", "/usr/lib64"]
        dll_name = "libvlc.so"

    def has_dll(d):
        return d and os.path.exists(os.path.join(d, dll_name))

    # 64-битные места в приоритете
    order = candidates_64 + candidates_32 if not is64 else candidates_64
    for d in order:
        if has_dll(d):
            # Проверяем совместимость архитектуры на Windows
            if sysname == "Windows" and is64 and d in candidates_32:
                # x86 VLC, а Python x64 — не подойдёт
                VLC_ARCH_MISMATCH = True
                continue
            return d, False

    # Если 64 не нашли, но 32 есть и Python 32 — вернём 32
    if not is64:
        for d in candidates_32:
            if has_dll(d):
                return d, False

    return None, VLC_ARCH_MISMATCH


VLC_DIR, VLC_ARCH_MISMATCH = _find_vlc_dir()


def _setup_env(d):
    if not d:
        return
    if platform.system() == "Windows":
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
    plugins = os.path.join(d, "plugins")
    if os.path.isdir(plugins):
        os.environ["VLC_PLUGIN_PATH"] = plugins
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_setup_env(VLC_DIR)


def _try_import_vlc():
    global _VLC_IMPORT_ERROR
    if VLC_DIR is None:
        _VLC_IMPORT_ERROR = "VLC не найден на компьютере"
        return None
    try:
        import vlc  # noqa
        return vlc
    except Exception as e:
        _VLC_IMPORT_ERROR = f"{type(e).__name__}: {e}"
        return None


vlc = _try_import_vlc()
VLC_AVAILABLE = vlc is not None


def is_vlc_available():
    return VLC_AVAILABLE


def vlc_diagnostic():
    """Строка для показа в GUI."""
    if VLC_AVAILABLE:
        return f"✓ VLC x{64 if _python_is_64bit() else 32}: {VLC_DIR}"
    if VLC_ARCH_MISMATCH:
        return ("✗ Установлен 32-битный VLC, а Python 64-битный.\n"
                "Скачай VLC 64-bit: https://get.videolan.org/vlc/last/win64/")
    if VLC_DIR is None:
        return "✗ VLC не найден. Установи с https://www.videolan.org/vlc/"
    return f"✗ Ошибка VLC: {_VLC_IMPORT_ERROR}"


# ==================== EQ ====================
EQ_PRESETS = {
    "flat":       (0.0,  [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
    "rock":       (-1.0, [5, 4, -1, -3, -2, 2, 5, 6, 6, 5]),
    "pop":        (-1.0, [-1, -1, 2, 4, 5, 3, -1, -1, -1, -1]),
    "jazz":       (0.0,  [0, 3, 4, 2, 1, 2, 3, 3, 3, 3]),
    "classical":  (0.0,  [0, 0, 0, 0, 0, 0, -3, -3, -3, -5]),
    "bass_boost": (5.0,  [9, 7, 5, 3, 0, 0, 0, 0, 0, 0]),
    "vocal":      (-2.0, [-3, -2, 0, 3, 5, 5, 4, 2, 1, 0]),
    "loudness":   (4.0,  [6, 4, 0, -2, -1, 2, 5, 5, 5, 5]),
    "electronic": (0.0,  [4, 3, 1, 0, -2, 2, 1, 4, 5, 5]),
}


class VLCPlayer:
    def __init__(self):
        self.available = VLC_AVAILABLE
        self.player = None
        self.instance = None
        self._end_callback = None
        self._eq_enabled = False
        self._eq_preset = "flat"
        self._lock = threading.RLock()
        self._volume = 50

        if not self.available:
            print(f"[!] VLC недоступен: {_VLC_IMPORT_ERROR}")
            return
        try:
            # --no-xlib → для Linux без дисплея; на Windows игнорируется
            self.instance = vlc.Instance("--no-video", "--quiet", "--no-xlib")
            self.player = self.instance.media_player_new()
            self.player.event_manager().event_attach(
                vlc.EventType.MediaPlayerEndReached, self._on_end
            )
            print(f"[✓] VLC загружен: {VLC_DIR}")
        except Exception as e:
            print(f"[!] Не удалось создать VLC-плеер: {e}")
            self.available = False

    def set_end_callback(self, cb):
        self._end_callback = cb

    def _on_end(self, _):
        if self._end_callback:
            threading.Thread(target=self._end_callback, daemon=True).start()

    # ==================== PLAYBACK ====================
    def play_file(self, path):
        if not self.available or not self.player:
            return False
        try:
            media = self.instance.media_new_path(path)
            self.player.set_media(media)
            self.player.play()
            self._apply_eq()
            return True
        except Exception as e:
            print(f"[!] VLC play_file: {e}")
            return False

    def stop(self):
        if not self.available or not self.player: return
        try: self.player.stop()
        except Exception: pass

    def pause(self):
        if not self.available or not self.player: return
        try: self.player.pause()
        except Exception: pass

    def resume(self):
        if not self.available or not self.player: return
        try: self.player.play()
        except Exception: pass

    def is_playing(self):
        if not self.available or not self.player: return False
        try: return bool(self.player.is_playing())
        except Exception: return False

    # ==================== TIME ====================
    def get_time(self):
        if not self.available or not self.player: return 0.0
        try:
            t = self.player.get_time()
            return max(0.0, t / 1000.0) if t > 0 else 0.0
        except Exception:
            return 0.0

    def get_length(self):
        if not self.available or not self.player: return 0.0
        try:
            l = self.player.get_length()
            return max(0.0, l / 1000.0) if l > 0 else 0.0
        except Exception:
            return 0.0

    def seek(self, seconds):
        if not self.available or not self.player: return False
        with self._lock:
            try:
                self.player.set_time(int(max(0.0, seconds) * 1000))
                return True
            except Exception as e:
                print(f"[!] seek: {e}")
                return False

    # ==================== VOLUME ====================
    def set_volume(self, v):
        self._volume = int(max(0, min(100, v)))
        if not self.available or not self.player: return
        try: self.player.audio_set_volume(self._volume)
        except Exception: pass

    def get_volume(self):
        return self._volume

    # ==================== EQ ====================
    def set_eq(self, preset_name, enabled=True):
        self._eq_preset = preset_name
        self._eq_enabled = enabled
        if not self.available or not self.player: return
        with self._lock:
            self._apply_eq()

    def _apply_eq(self):
        if not self.available or not self.player: return
        try:
            if not self._eq_enabled or self._eq_preset == "flat":
                self.player.set_equalizer(None)
                return
            preamp, bands = EQ_PRESETS.get(self._eq_preset, (0.0, [0] * 10))
            eq = vlc.AudioEqualizer()
            eq.set_preamp(float(preamp))
            for i, g in enumerate(bands):
                eq.set_amp_at_index(float(g), i)
            self.player.set_equalizer(eq)
        except Exception as e:
            print(f"[!] EQ: {e}")

    def release(self):
        if not self.available or not self.player: return
        try:
            self.player.stop(); self.player.release(); self.instance.release()
        except Exception: pass