"""`make doctor`: checks that every tool and key the pipeline needs is in place."""
from __future__ import annotations

import shutil
import subprocess
import sys

from rich.console import Console
from rich.table import Table

from app.config import ROOT, get_settings

console = Console()


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def checks() -> list[tuple[str, bool, str, bool]]:
    """Returns (name, ok, detail, required)."""
    s = get_settings()
    out: list[tuple[str, bool, str, bool]] = []

    out.append(("python 3.12", sys.version_info[:2] == (3, 12), sys.version.split()[0], True))

    ver = _run([s.ffmpeg, "-hide_banner", "-version"]).splitlines()
    out.append(("ffmpeg", bool(ver), f"{s.ffmpeg} ({ver[0][:40] if ver else 'not found'})", True))
    filters = _run([s.ffmpeg, "-hide_banner", "-filters"])
    has_ass = " ass " in filters or " subtitles " in filters
    out.append(("ffmpeg captions (libass)", has_ass,
                "ok" if has_ass else "missing: brew install ffmpeg-full", True))
    out.append(("ffprobe", bool(_run([s.ffprobe, "-version"])), s.ffprobe, True))

    for tool, required in (("yt-dlp", True), ("streamlink", False)):
        path = shutil.which(tool) or str(ROOT / ".venv/bin" / tool)
        ok = bool(_run([path, "--version"]))
        out.append((tool, ok, path if ok else f"missing: brew install {tool}", required))

    keys = [
        ("ANTHROPIC_API_KEY", s.anthropic_api_key, True),
        ("ASSEMBLYAI_API_KEY", s.assemblyai_api_key, True),
        ("GEMINI_API_KEY", s.gemini_api_key, False),
        ("TELEGRAM_BOT_TOKEN", s.telegram_bot_token, False),
        ("TELEGRAM_CHAT_ID", s.telegram_chat_id, False),
    ]
    for name, val, required in keys:
        out.append((name, bool(val), "set" if val else "empty: add it to .env", required))

    out.append(("data dir", s.data_dir.is_dir(), str(s.data_dir), True))
    n = len(list((ROOT / "campaigns").glob("*.yaml")))
    out.append(("campaign configs", n > 0, f"{n} file(s) in campaigns/", True))
    return out


def main() -> int:
    table = Table(title="viral-clipper doctor")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    failed = 0
    for name, ok, detail, required in checks():
        if ok:
            status = "[green]ok[/]"
        elif required:
            status = "[red]FAIL[/]"
            failed += 1
        else:
            status = "[yellow]optional[/]"
        table.add_row(name, status, detail)
    console.print(table)
    console.print("[green]All required checks pass.[/]" if not failed
                  else f"[red]{failed} required check(s) failing.[/]")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
