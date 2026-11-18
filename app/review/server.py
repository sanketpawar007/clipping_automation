"""Review dashboard: watch each clip, fix the caption, approve or reject.

Approve sends the clip, caption and pinned-comment text to your phone on Telegram.
"Mark posted" starts the campaign's submit countdown and reminds you before it runs out.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.notify import telegram
from app.rules import campaign as campaigns
from app.rules import check

app = FastAPI(title="viral-clipper review")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["hhmm"] = lambda ts: time.strftime("%H:%M", time.localtime(ts)) if ts else ""


def _clip(conn, clip_id: int) -> dict:
    row = conn.execute(
        "SELECT c.*, v.title AS video_title, v.url AS video_url FROM clips c "
        "JOIN videos v ON v.id = c.video_id WHERE c.id=?", (clip_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "clip not found")
    d = dict(row)
    d["report"] = db.loads(d["rule_report"], [])
    d["sig"] = db.loads(d["signals"], {})
    d["compliant"] = check.compliant(d["report"])
    d["seconds"] = d["sig"].get("duration") or (d["end"] - d["start"])
    return d


def _card(request: Request, clip_id: int, note: str = "") -> HTMLResponse:
    with db.connect() as conn:
        clip = _clip(conn, clip_id)
    return templates.TemplateResponse(request, "card.html", {"c": clip, "note": note})


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str = "pending"):
    with db.connect() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM clips WHERE status=? ORDER BY created_at DESC, score DESC", (status,))]
        clips = [_clip(conn, i) for i in ids]
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM clips GROUP BY status")}
    return templates.TemplateResponse(request, "index.html", {
        "clips": clips, "status": status, "counts": counts,
        "telegram": telegram.configured(),
        "tabs": ["pending", "approved", "posted", "rejected"]})


@app.get("/video/{clip_id}")
def video(clip_id: int):
    with db.connect() as conn:
        clip = _clip(conn, clip_id)
    if not Path(clip["path"]).exists():
        raise HTTPException(404, "clip file missing")
    return FileResponse(clip["path"], media_type="video/mp4")


@app.get("/download/{clip_id}")
def download(clip_id: int):
    """Same file as /video, but the browser saves it instead of playing it."""
    with db.connect() as conn:
        clip = _clip(conn, clip_id)
    if not Path(clip["path"]).exists():
        raise HTTPException(404, "clip file missing")
    return FileResponse(clip["path"], media_type="video/mp4",
                        filename=f"{clip['campaign']}_clip{clip_id}.mp4")


@app.post("/clips/{clip_id}/caption", response_class=HTMLResponse)
def save_caption(request: Request, clip_id: int, caption: str = Form(...)):
    """Saves an edited caption and re-checks it against the campaign rules."""
    caption = caption.replace("\r\n", "\n").strip()
    with db.connect() as conn:
        clip = _clip(conn, clip_id)
        camp = campaigns.load(clip["campaign"])
        render = {"watermark": any(r["rule"] == "watermark" and r["ok"] for r in clip["report"])}
        report = check.check(camp, caption=caption, duration=clip["seconds"],
                             render=render, clip_text=clip["sig"].get("text", ""),
                             ad_like=any(r["rule"] == "not ad-like" and not r["ok"] for r in clip["report"]),
                             spoiler=bool(clip["spoiler"]))
        conn.execute("UPDATE clips SET caption=?, rule_report=? WHERE id=?",
                     (caption, db.dumps(report), clip_id))
    return _card(request, clip_id, "Caption saved and re-checked.")


@app.post("/clips/{clip_id}/approve", response_class=HTMLResponse)
def approve(request: Request, clip_id: int):
    with db.connect() as conn:
        clip = _clip(conn, clip_id)
    if not clip["compliant"]:
        return _card(request, clip_id, "Fix the failing rules before approving.")
    note = "Approved."
    try:
        from app.notify import package
        sent = package.send(clip_id)
        if sent:
            note = "Approved and sent to Telegram."
        else:
            note = "Approved. Telegram is not set up, so nothing was sent to your phone."
    except Exception as exc:
        return _card(request, clip_id, f"Telegram send failed, clip left as pending: {exc}")
    with db.connect() as conn:
        conn.execute("UPDATE clips SET status='approved', approved_at=? WHERE id=?", (db.now(), clip_id))
    return _card(request, clip_id, note)


@app.post("/clips/{clip_id}/reject", response_class=HTMLResponse)
def reject(request: Request, clip_id: int):
    with db.connect() as conn:
        conn.execute("UPDATE clips SET status='rejected' WHERE id=?", (clip_id,))
    return _card(request, clip_id, "Rejected.")


@app.post("/clips/{clip_id}/posted", response_class=HTMLResponse)
async def posted(request: Request, clip_id: int, post_url: str = Form("")):
    with db.connect() as conn:
        if post_url.strip():
            conn.execute("UPDATE clips SET post_url=? WHERE id=?", (post_url.strip(), clip_id))
        clip = _clip(conn, clip_id)
        camp = campaigns.load(clip["campaign"])
        deadline = db.now() + camp.submit_within_minutes * 60
        conn.execute("UPDATE clips SET status='posted', submit_by=? WHERE id=?", (deadline, clip_id))
    by = time.strftime("%H:%M", time.localtime(deadline))
    try:
        telegram.send_text(f"Clip #{clip_id} posted. Submit on Content Rewards by {by}.")
    except Exception:
        pass
    asyncio.get_running_loop().create_task(_remind(clip_id, deadline, by))
    return _card(request, clip_id, f"Marked as posted. Submit by {by}.")


async def _remind(clip_id: int, deadline: float, by: str) -> None:
    await asyncio.sleep(max(0, deadline - 10 * 60 - time.time()))
    try:
        telegram.send_text(f"10 minutes left: submit clip #{clip_id} on Content Rewards by {by}.")
    except Exception:
        pass
