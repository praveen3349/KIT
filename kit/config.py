"""Central configuration. Everything comes from environment variables (see .env.example)."""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Vercel/Render supply environment variables directly.  During local development
# this loads the ignored .env file without replacing any variables supplied by the
# shell or the host.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


COLLEGE_NAME = os.getenv("COLLEGE_NAME", "Knotopian Institute of Technology")
COLLEGE_SHORT = os.getenv("COLLEGE_SHORT", "KIT")
COLLEGE_API_URL = os.getenv("COLLEGE_API_URL", "https://ht.knowvally.com").rstrip("/")
COLLEGE_API_KEY = os.getenv("COLLEGE_API_KEY", "")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Models are tried in order; if one is missing or rate limited the next one is used.
CHAT_MODELS = _list("CHAT_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b")
STT_MODELS = _list("STT_MODELS", "whisper-large-v3-turbo,whisper-large-v3")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "groq")        # "groq" or "browser"
TTS_MODEL = os.getenv("TTS_MODEL", "canopylabs/orpheus-v1-english")
TTS_VOICE = os.getenv("TTS_VOICE", "hannah")

TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
# Vercel functions may write only to /tmp. Local Docker/Render deployments keep
# the configured ./data or /data location instead.
DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/kit-data" if os.getenv("VERCEL") else "./data"))
SESSION_HOURS = 7.5                    # college login tokens last 8 hours
MAX_AUDIO_BYTES = 6 * 1024 * 1024
MAX_MESSAGE_CHARS = 1000


def now() -> datetime:
    """Current local time (naive) in the configured timezone."""
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
