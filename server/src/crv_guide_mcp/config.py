"""Runtime settings, all from environment variables (see .env.example)."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("CRV_GUIDE_DATA", Path.home() / ".crv-guide")).expanduser()
DB_PATH = DATA_DIR / "guides.sqlite3"
VIDEOS_DIR = DATA_DIR / "videos"

# Model that turns a watched video (frames + transcript) into a step-by-step guide.
GUIDE_MODELS = [
    m.strip()
    for m in os.environ.get("CRV_GUIDE_MODELS", "gemini-flash-latest,gemini-3.5-flash,gemini-3.7-flash").split(",")
    if m.strip()
]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
# Fallback guide writer when Gemini is unavailable or not configured.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_GUIDE_MODEL = os.environ.get("CRV_GUIDE_OPENAI_MODEL", "gpt-4.1")

# Public base URL of this server, used to build frame/video links that a screen can load.
PUBLIC_BASE_URL = os.environ.get("CRV_GUIDE_PUBLIC_URL", "http://127.0.0.1:8765").rstrip("/")

# Browser origins allowed to call /mcp directly (a web client such as the Second Look host).
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CRV_GUIDE_ALLOWED_ORIGINS", "http://127.0.0.1:8770,http://localhost:8770").split(",")
    if o.strip()
]

# How many frames are sent to the guide model (evenly thinned when a video has more).
MAX_GUIDE_FRAMES = int(os.environ.get("CRV_GUIDE_MAX_FRAMES", "96"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
