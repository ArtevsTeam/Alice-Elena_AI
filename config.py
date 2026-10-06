import json, os

_BASE = os.path.dirname(os.path.abspath(__file__))
_STATE = os.path.join(_BASE, "state.json")

YANDEX_MUSIC_TOKEN = "y0__wgBEJ6O1N8DGN74BiC9ka2kGTCilr2kCKfNcqgo_8gMIGFPocEqiC9QM6lG"

YANDEX_OAUTH_CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
YANDEX_OAUTH_URL = (
    "https://oauth.yandex.ru/authorize?response_type=token"
    f"&client_id={YANDEX_OAUTH_CLIENT_ID}"
)

# ==================== AI ====================
AI_TYPE = "ollama"
OLLAMA_MODEL = "qwen2.5:1.5b"
OLLAMA_URL = "http://localhost:11434/v1"

# ==================== VOICE ====================
VOICE_SPEAKER = "kseniya"
ASSISTANT_NAME = "Лена"
LANGUAGE = "ru-RU"
SELECTED_MIC_INDEX = None
OUTPUT_DEVICE_INDEX = None
WAKE_WORDS = ["лена", "елена", "леночка", "лен", "алена", "лёна", "элена", "алиса"]

# ==================== MUSIC ====================
AUDIO_QUALITY = "lossless"
EQ_ENABLED = False
EQ_PRESET = "flat"
MY_WAVE_MAX_TRACKS = 50
SIMILAR_BATCH_SIZE = 15
ARTIST_MODE = "top"

# FIX: ускоряем параллельные загрузки и волну
DOWNLOAD_WORKERS = 16            # было 8
WAVE_REFILL_AHEAD = 5            # было 3
WAVE_REFILL_BATCH = 20           # было 10
PREFETCH_AHEAD = 3               # FIX: префетчим 3 трека вперёд

# ==================== SERVER ====================
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
SERVER_HTTPS_PORT = 8443
SERVER_HTTPS = True
DISCOVERY_PORT = 8766
SERVER_AUTOSTART = True

# ==================== UI ====================
START_MINIMIZED = False
MINIMIZE_TO_TRAY = True
AUTOSTART_ASKED = False
AUTOSTART_ENABLED = False


def _load():
    g = globals()
    if not os.path.exists(_STATE):
        return
    try:
        with open(_STATE, "r", encoding="utf-8") as f:
            s = json.load(f)
        for k in ("ASSISTANT_NAME", "WAKE_WORDS", "SELECTED_MIC_INDEX",
                  "OUTPUT_DEVICE_INDEX",
                  "AUDIO_QUALITY", "VOICE_SPEAKER", "AUTOSTART_ENABLED",
                  "AUTOSTART_ASKED", "START_MINIMIZED", "MINIMIZE_TO_TRAY",
                  "SERVER_HTTPS", "EQ_ENABLED", "EQ_PRESET"):
            if k in s:
                g[k] = s[k]
    except Exception as e:
        print(f"[!] config load: {e}")


def save_state():
    d = {k: globals().get(k) for k in (
        "ASSISTANT_NAME", "WAKE_WORDS", "SELECTED_MIC_INDEX", "OUTPUT_DEVICE_INDEX",
        "AUDIO_QUALITY", "VOICE_SPEAKER", "AUTOSTART_ENABLED", "AUTOSTART_ASKED",
        "START_MINIMIZED", "MINIMIZE_TO_TRAY", "SERVER_HTTPS",
        "EQ_ENABLED", "EQ_PRESET")}
    try:
        with open(_STATE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


_load()

# ==================== VOSK ====================
VOSK_ENABLED = True


def _find_model(*candidates):
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


VOSK_MODEL_DIR_SMALL = _find_model(
    os.path.join(_BASE, "models", "vosk-small-ru"),
    os.path.join(_BASE, "vosk-small-ru"),
    os.path.join(_BASE, "models", "vosk-model-small-ru-0.22"),
)
VOSK_MODEL_DIR_BIG = _find_model(
    os.path.join(_BASE, "models", "vosk-ru-0.42"),
    os.path.join(_BASE, "models", "vosk-model-ru-0.42"),
    os.path.join(_BASE, "vosk-model-ru-0.42"),
)
VOSK_MODEL_DIR = VOSK_MODEL_DIR_SMALL or VOSK_MODEL_DIR_BIG

VOSK_SAMPLE_RATE = 16000
VOSK_BLOCK_SIZE = 2000

# Какая модель для команд:
#   "google"   — Google STT (нужен интернет)
#   "vosk_big" — большая Vosk (офлайн)
#   "vosk"     — small (офлайн, с ошибками)
#   "auto"     — Google + Vosk, берём лучший
COMMAND_ENGINE = "vosk_big"

# FIX: увеличены окна тишины — юзер успевает договорить команду,
#      а короткое «лена» не режется как шум
VOSK_COMMAND_SILENCE_MS = 1100   # было 800
VOSK_AWAIT_MS = 3000             # было 2500
VOSK_MIN_VOICED_MS = 400         # было 500