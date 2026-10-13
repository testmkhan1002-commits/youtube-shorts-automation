"""
config.py - Centralized configuration for YouTube Shorts Automation System.
Reads all environment variables and exposes a validated Config object.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

# Load .env file if present (local development)
load_dotenv()

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("config")

# ── YouTube Shorts Specs ───────────────────────────────────────────────────────
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
SHORTS_MAX_DURATION = 59       # seconds (hard cap for YouTube Shorts)
SHORTS_TARGET_DURATION = 55    # seconds (target for script/voice)
SHORTS_FPS = 30
SHORTS_VIDEO_BITRATE = "2000k"
SHORTS_AUDIO_BITRATE = "128k"

# ── Output paths ───────────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
VOICE_PATH = os.path.join(OUTPUT_DIR, "voice.mp3")
VIDEO_RAW_PATH = os.path.join(OUTPUT_DIR, "background.mp4")
FINAL_VIDEO_PATH = os.path.join(OUTPUT_DIR, "final_short.mp4")

# ── API Endpoints ──────────────────────────────────────────────────────────────
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Primary model — verified available via API query
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
# Ordered fallbacks tried automatically on 400, 402, 404, or 429
OPENROUTER_FALLBACK_MODELS = [
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemma-3-12b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "google/gemma-2-9b-it:free",
    "deepseek/deepseek-r1:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "openai/gpt-oss-120b:free",
    "openrouter/free", # Last resort: auto-route to any available free model
]
PIXABAY_API_BASE = "https://api.pixabay.com/videos/search"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
YOUTUBE_TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass
class Config:
    """Validated runtime configuration loaded from environment variables."""

    # API Keys
    openrouter_api_key: str = field(default="")
    pixabay_api_key: str = field(default="")
    youtube_client_id: str = field(default="")
    youtube_client_secret: str = field(default="")
    youtube_refresh_token: str = field(default="")

    # Optional overrides
    tts_language: str = field(default="en")
    tts_slow: bool = field(default=False)
    video_category_id: str = field(default="22")   # People & Blogs
    video_privacy: str = field(default="private")

    def __post_init__(self):
        self.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.pixabay_api_key = os.environ.get("PIXABAY_API_KEY", "")
        self.youtube_client_id = os.environ.get("YOUTUBE_CLIENT_ID", "")
        self.youtube_client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        self.youtube_refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
        self.tts_language = os.environ.get("TTS_LANGUAGE", "en")
        self.tts_slow = os.environ.get("TTS_SLOW", "false").lower() == "true"
        self.video_category_id = os.environ.get("YT_CATEGORY_ID", "22")
        self.video_privacy = os.environ.get("YT_PRIVACY", "private")

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self._validate()

    def _validate(self):
        """Warn about missing critical keys."""
        missing = []
        for key in ["openrouter_api_key", "pixabay_api_key",
                    "youtube_client_id", "youtube_client_secret",
                    "youtube_refresh_token"]:
            if not getattr(self, key):
                missing.append(key.upper())
        if missing:
            logger.warning("Missing environment variables: %s", ", ".join(missing))
        else:
            logger.info("All API credentials loaded successfully.")

    def __repr__(self):
        def mask(v):
            return v[:4] + "****" if len(v) > 4 else "****"
        return (
            f"Config("
            f"openrouter_api_key={mask(self.openrouter_api_key)}, "
            f"pixabay_api_key={mask(self.pixabay_api_key)}, "
            f"youtube_client_id={mask(self.youtube_client_id)}, "
            f"tts_language={self.tts_language}, "
            f"video_privacy={self.video_privacy}"
            f")"
        )
