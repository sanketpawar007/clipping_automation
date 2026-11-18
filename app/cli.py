"""Command line entry point: `uv run python -m app.cli --help`."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def scan(loop: bool = typer.Option(False, help="Keep scanning every hour."),
         quiet: bool = typer.Option(False, help="Do not send Telegram alerts.")) -> None:
    """Scan the Content Rewards board for new, high-paying and GTA campaigns."""
    from app.scanner import scan as scanner
    if loop:
        scanner.scan_loop()
    else:
        r = scanner.scan_once(notify=not quiet)
        note = " (first run: stored as the baseline)" if r["first_run"] else ""
        console.print(f"Scanned {r['campaigns']} campaigns, {r['alerts']} alert(s){note}.")


@app.command()
def campaigns(top: int = 15, category: str = typer.Option("", help="Filter, e.g. Streamer")) -> None:
    """Show stored campaigns, best rate first."""
    from app import db
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE (?='' OR category LIKE ?) "
            "ORDER BY rate_per_1k DESC LIMIT ?", (category, f"%{category}%", top)).fetchall()
    t = Table()
    for col in ("title", "category", "$/1k", "budget left", "apply?", "platforms"):
        t.add_column(col, overflow="fold")
    for r in rows:
        t.add_row(r["title"], r["category"] or "", f"{r['rate_per_1k'] or 0:.2f}",
                  f"${r['budget_available'] or 0:,.0f}", "yes" if r["requires_application"] else "no",
                  ", ".join(db.loads(r["platforms"], [])))
    console.print(t)


@app.command()
def clip(url: str, campaign: str = typer.Option(..., "--campaign", "-c", help="campaigns/<name>.yaml"),
         n: int = typer.Option(0, help="How many clips to make (0 = the campaign's setting)."),
         no_captions: bool = typer.Option(False, help="Skip burned-in captions."),
         only: str = typer.Option("", help="Render only this kind: single or supercut."),
         start: str = typer.Option("", help="Long streams: where to start, e.g. 2:00:00"),
         end: str = typer.Option("", help="Long streams: where to stop, e.g. 5:00:00 (4 hours max)")) -> None:
    """Turn one video URL into ready-to-review clips for a campaign."""
    from app import pipeline
    from app.ingest.vod import parse_time
    pipeline.run(url, campaign, n_clips=n or None, with_captions=not no_captions,
                 only_kind=only or None, start=parse_time(start), end=parse_time(end))


@app.command()
def live(campaign: str = typer.Option(..., "--campaign", "-c"),
         url: str = typer.Option("", help="Override the campaign's first source channel.")) -> None:
    """Wait for the streamer to go live, then clip the stream while it airs (runs forever)."""
    from app.live import session
    session.watch(campaign, url or None)


@app.command()
def rerender(status: str = typer.Option("pending", help="Which clips: pending, approved...")) -> None:
    """Rebuild existing clips with the current watermark, headline and pacing settings (no AI cost)."""
    from app import pipeline
    console.print(f"Re-rendered {pipeline.rerender(status)} clip(s).")


@app.command()
def track() -> None:
    """Record current view counts for posted clips (YouTube links only for now)."""
    import yt_dlp

    from app import db
    with db.connect() as conn:
        rows = conn.execute("SELECT id, post_url FROM clips WHERE status='posted' "
                            "AND post_url LIKE '%youtu%'").fetchall()
    if not rows:
        console.print("No posted clips with a YouTube link yet. Paste the link when you mark a clip posted.")
        return
    for r in rows:
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
                views = y.extract_info(r["post_url"], download=False).get("view_count")
        except Exception as exc:  # noqa: BLE001
            console.print(f"clip #{r['id']}: could not read views ({str(exc)[:80]})")
            continue
        with db.connect() as conn:
            conn.execute("UPDATE clips SET views=?, views_checked_at=? WHERE id=?",
                         (views, db.now(), r["id"]))
        shown = f"{views:,} views" if views is not None else "no count"
        console.print(f"clip #{r['id']}: {shown}")


@app.command("telegram-id")
def telegram_id() -> None:
    """Print your Telegram chat id (message your bot once first)."""
    from app.notify import telegram
    found = telegram.find_chat_id()
    if not found:
        console.print("No messages found. Open your bot in Telegram, send it 'hi', then retry.")
    for chat_id, name in found:
        console.print(f"TELEGRAM_CHAT_ID={chat_id}   ({name})")


@app.command("telegram-test")
def telegram_test() -> None:
    """Send a test message to your phone."""
    from app.notify import telegram
    ok = telegram.send_text("viral-clipper: Telegram delivery works ✅")
    console.print("sent" if ok else "Telegram is not configured in .env")


if __name__ == "__main__":
    app()
