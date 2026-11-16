"""Telegram delivery. If no bot is configured, messages are printed instead of sent."""
from __future__ import annotations

from pathlib import Path

import httpx
from rich.console import Console

from app.config import get_settings

console = Console()
API = "https://api.telegram.org/bot{token}/{method}"


def configured() -> bool:
    s = get_settings()
    return bool(s.telegram_bot_token and s.telegram_chat_id)


def _call(method: str, data: dict, files: dict | None = None, timeout: float = 300) -> dict:
    s = get_settings()
    url = API.format(token=s.telegram_bot_token, method=method)
    r = httpx.post(url, data=data, files=files, timeout=timeout)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    return body["result"]


def send_text(text: str) -> bool:
    if not configured():
        console.print(f"[dim]\\[telegram not configured][/] {text}")
        return False
    _call("sendMessage", {"chat_id": get_settings().telegram_chat_id, "text": text[:4096],
                          "disable_web_page_preview": "true"})
    return True


def send_video(path: Path, caption: str = "") -> bool:
    """Sends as a document so Telegram does not re-compress the clip (bot limit: 50 MB)."""
    if not configured():
        console.print(f"[dim]\\[telegram not configured][/] would send {path}")
        return False
    with open(path, "rb") as fh:
        _call("sendDocument", {"chat_id": get_settings().telegram_chat_id, "caption": caption[:1024]},
              files={"document": (path.name, fh, "video/mp4")})
    return True


def find_chat_id() -> list[tuple[str, str]]:
    """Returns (chat_id, name) for everyone who has messaged the bot recently."""
    s = get_settings()
    r = httpx.get(API.format(token=s.telegram_bot_token, method="getUpdates"), timeout=30).json()
    seen: dict[str, str] = {}
    for upd in r.get("result", []):
        chat = (upd.get("message") or {}).get("chat") or {}
        if chat.get("id"):
            seen[str(chat["id"])] = chat.get("first_name") or chat.get("title") or ""
    return list(seen.items())
