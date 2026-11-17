"""Central settings. Everything secret or machine-specific comes from .env."""
from __future__ import annotations

import shutil
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin")


def _find_bin(name: str) -> str:
    """Prefer ffmpeg-full (it has libass for captions) over the plain ffmpeg on PATH."""
    full = _FFMPEG_FULL / name
    if full.exists():
        return str(full)
    return shutil.which(name) or name


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    assemblyai_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    ffmpeg_bin: str = ""
    ffprobe_bin: str = ""
    data_dir: Path = ROOT / "data"

    recall_model: str = "claude-haiku-5-5"
    judge_model: str = "claude-sonnet-5-5"
    gemini_model: str = "gemini-2.5-flash"

    # Caption font must be installed on the machine: Arial Black on macOS, Roboto Black on Linux.
    caption_font: str = "Arial Black" if sys.platform == "darwin" else "Roboto Black"

    @property
    def ffmpeg(self) -> str:
        return self.ffmpeg_bin or _find_bin("ffmpeg")

    @property
    def ffprobe(self) -> str:
        return self.ffprobe_bin or _find_bin("ffprobe")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clipper.db"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.data_dir.is_absolute():
        s.data_dir = ROOT / s.data_dir
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
