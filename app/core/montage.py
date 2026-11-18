"""The moment bin and montages.

Some moments are good but too small to post alone: a first look at a car, one more police chase,
another funny death. The video editor tags them with a theme; we keep a short full-quality copy
(the raw stream footage is deleted after clipping) plus its words. When a theme has enough good
pieces, the judge decides whether they make a stronger clip together, in which order, and with
what title, and we render that as a montage."""
from __future__ import annotations

import json
import time
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field
from rich.console import Console

from app import db
from app.config import get_settings
from app.core import media
from app.rules.campaign import Campaign

console = Console()
MIN_PIECES = 3
MAX_PIECES = 6
MAX_AGE_S = 6 * 3600          # moments older than this are stale for a live stream


def bin_dir(camp: Campaign) -> Path:
    d = get_settings().data_dir / "bin" / camp.slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_moments(camp: Campaign, meta: dict, src: str, words: list[dict], moments: list[dict],
                 profile: dict) -> int:
    """Copies each moment's footage (with a little room either side) into the bin."""
    s = get_settings()
    saved = 0
    for m in moments:
        a, b = max(0.0, m["start"] - 0.3), min(meta["duration"], m["end"] + 0.3)
        mid = f"{int(time.time() * 1000)}_{saved}"
        out = bin_dir(camp) / f"{mid}.mp4"
        try:
            media.run([s.ffmpeg, "-y", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
                       "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-c:a", "aac", "-b:a", "192k",
                       "-ar", "44100", "-r", "60" if media.probe(src)["fps"] > 45 else "30", str(out)])
        except RuntimeError as exc:
            console.print(f"[yellow]could not save moment: {exc}[/]")
            continue
        piece_words = [{**w, "start": w["start"] - a, "end": w["end"] - a}
                       for w in words if a <= w["start"] and w["end"] <= b]
        (out.with_suffix(".json")).write_text(json.dumps({
            "theme": m["theme"], "desc": m["desc"], "virality": m.get("virality", 5),
            "file": str(out), "duration": b - a, "words": piece_words, "layout": profile,
            "source": meta.get("url"), "created": time.time(), "used": False}))
        saved += 1
    return saved


def _load(camp: Campaign) -> list[dict]:
    items = []
    for p in sorted(bin_dir(camp).glob("*.json")):
        try:
            it = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if not it.get("used") and time.time() - it["created"] < MAX_AGE_S and Path(it["file"]).exists():
            it["_json"] = str(p)
            items.append(it)
    return items


class MontagePlan(BaseModel):
    make: bool = Field(description="True only if these pieces together make a clip people will watch "
                                   "to the end and share; False if they are repetitive or weak")
    order: list[int] = Field(description="Piece numbers to use, in viewing order (3-6 pieces). Open "
                                         "with the strongest piece and end on the best payoff")
    title: str = Field(description="On-screen title, max 45 characters, naming the streamer and the theme "
                                   "as a hook, e.g. 'Every time Clix met the GTA 6 police'")
    title_style: str = Field(description="box or plain")
    captions: bool = Field(description="True if the spoken words carry the pieces")
    caption_hook: str = Field(description="One caption line for the post, under 60 characters")
    question: str = Field(description="Short question for the end of the post caption")


def _plan(camp: Campaign, theme: str, items: list[dict]) -> MontagePlan | None:
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    pieces = "\n".join(f"[{i}] {it['duration']:.0f}s, editor rated {it['virality']}/10: {it['desc']}"
                       for i, it in enumerate(items))
    prompt = (f"Streamer: {camp.streamer or camp.name}. Theme: {theme}.\n"
              f"Saved moments from the stream, each too small to post alone:\n{pieces}\n\n"
              f"Campaign brief:\n{camp.editing.brief}\n\n"
              "Decide whether a montage of these makes one strong short clip "
              f"({camp.clip.min_seconds:.0f}-{camp.clip.max_seconds:.0f} seconds). Leave out weak or "
              "repetitive pieces. Make it only if the whole is clearly better than any single piece.")
    resp = anthropic.Anthropic(api_key=s.anthropic_api_key).messages.parse(
        model=s.judge_model, max_tokens=4000, messages=[{"role": "user", "content": prompt}],
        output_format=MontagePlan)
    if resp.stop_reason == "refusal":
        return None
    return resp.parsed_output


def try_build(camp: Campaign) -> list[int]:
    """Builds montages from themes that have enough saved pieces. Returns new clip ids."""
    from app.core import render
    from app.rules import check
    items = _load(camp)
    themes: dict[str, list[dict]] = {}
    for it in items:
        themes.setdefault(it["theme"], []).append(it)
    made: list[int] = []
    for theme, group in themes.items():
        if len(group) < MIN_PIECES:
            continue
        group = sorted(group, key=lambda x: -x["virality"])[:MAX_PIECES * 2]
        plan = _plan(camp, theme, group)
        if not plan or not plan.make:
            continue
        chosen = [group[i] for i in plan.order if 0 <= i < len(group)][:MAX_PIECES]
        total, picked = 0.0, []
        for it in chosen:
            if total + it["duration"] > camp.clip.max_seconds:
                break
            picked.append(it)
            total += it["duration"]
        if len(picked) < 2 or total < camp.clip.min_seconds:
            continue
        clip_id = _render(camp, theme, picked, plan, render, check)
        if clip_id:
            made.append(clip_id)
            for it in picked:                       # each moment is used once
                it2 = {k: v for k, v in it.items() if k != "_json"}
                it2["used"] = True
                Path(it["_json"]).write_text(json.dumps(it2))
    return made


def _render(camp: Campaign, theme: str, picked: list[dict], plan: MontagePlan, render, check) -> int | None:
    s = get_settings()
    stamp = int(time.time())
    workdir = s.data_dir / "videos" / f"montage_{camp.slug}_{stamp}"
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "source.mp4"
    listing = workdir / "pieces.txt"
    listing.write_text("".join(f"file '{it['file']}'\n" for it in picked))
    media.run([s.ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
               "-c", "copy", str(src)])
    words, segs, offset = [], [], 0.0
    for it in picked:
        words += [{**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in it["words"]]
        segs.append((offset, offset + it["duration"]))
        offset += it["duration"]
    out = workdir / "clips" / f"montage_{stamp}.mp4"
    profile = picked[0].get("layout") or {}
    r = render.render_clip(src, segs, words, out, camp, profile, plan.captions, plan.title,
                           style={"fmt": "montage", "title": plan.title_style, "zoom": "none",
                                  "seed": stamp})
    caption = check.build_caption(camp, plan.caption_hook, stamp, plan.question)
    report = check.check(camp, caption=caption, duration=r["duration"], render=r)
    with db.connect() as conn:
        conn.execute("INSERT INTO videos (url, video_id, title, duration, campaign, path, status, "
                     "created_at) VALUES (?,?,?,?,?,?, 'clipped', ?)",
                     (f"montage:{camp.slug}:{stamp}", workdir.name, f"{camp.streamer} montage: {theme}",
                      offset, camp.slug, str(src), db.now()))
        vrow = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        cur = conn.execute(
            """INSERT INTO clips (video_id, campaign, start, end, score, hook, reason, caption,
                   pinned_comment, path, rule_report, signals, spoiler, created_at, kind, headline,
                   segments)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vrow, camp.slug, 0.0, offset, sum(it["virality"] for it in picked) / len(picked) / 10,
             plan.caption_hook,
             f"Montage of {len(picked)} moments: " + " | ".join(it["desc"] for it in picked),
             caption, check.pinned_comment(camp, stamp), str(out), db.dumps(report),
             db.dumps({"duration": r["duration"], "layout": r["layout"], "parts": len(picked),
                       "fmt": "montage", "title_style": plan.title_style, "captions_on": plan.captions,
                       "zoom_mode": "none",
                       "text": " ".join(w["text"] for w in words)}),
             0, db.now(), "montage", plan.title, db.dumps(segs)))
        clip_id = cur.lastrowid
    console.print(f"[green]montage #{clip_id}: {plan.title} ({len(picked)} pieces, {r['duration']:.0f}s)[/]")
    return clip_id
