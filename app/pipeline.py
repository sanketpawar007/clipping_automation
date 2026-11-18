"""VOD pipeline: URL in, ready-to-review clips out."""
from __future__ import annotations

import time

import numpy as np
from rich.console import Console

from app import db
from app.config import get_settings
from app.core import llm, media, render, select, signals, transcript
from app.ingest import vod
from app.rules import campaign as campaigns
from app.rules import check
from app.scanner import board

console = Console()
TOP_CLIPS_MAX_AGE_S = 24 * 3600


def _step(msg: str, t0: float) -> None:
    console.print(f"[dim]{time.time() - t0:6.0f}s[/] {msg}")


def top_clips(camp: campaigns.Campaign) -> list[dict]:
    """The campaign's best-performing clips, read from its public page at most once a day."""
    if not camp.id or not camp.editing.learn_from_top_clips:
        return []
    with db.connect() as conn:
        row = conn.execute("SELECT detail, last_seen FROM campaigns WHERE id=?", (camp.id,)).fetchone()
    detail = db.loads(row["detail"], {}) if row else {}
    fresh = detail.get("fetched_at", 0) > time.time() - TOP_CLIPS_MAX_AGE_S
    if not (fresh and "top_clips" in detail):
        try:
            got = board.fetch_campaign(camp.id)
        except Exception as exc:  # noqa: BLE001 - evidence is a bonus; never fail a run on it
            console.print(f"[yellow]could not read the campaign page: {exc}[/]")
            got = None
        if got:
            detail = {**got, "fetched_at": time.time()}
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO campaigns (id, title, detail, first_seen, last_seen) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET detail=excluded.detail",
                    (camp.id, got.get("name"), db.dumps(detail), db.now(), db.now()))
    return detail.get("top_clips") or []


def _measure(w: select.Window, tracks: dict) -> None:
    """Audience-signal strength for a window: the average over its segments."""
    per = [signals.window_scores(tracks, a, b) for a, b in w.segs]
    w.signals = {k: float(np.mean([p[k] for p in per])) for k in per[0]} if per else {}


def _save_candidates(workdir, windows: list[select.Window], sents) -> None:
    """Writes every candidate with its scores to candidates.json, so a run can be inspected."""
    rows = [{"id": w.id, "kind": w.kind, "source": w.source, "score": round(w.score, 3),
             "judge": w.judge, "signals": w.signals, "segs": w.segs, "cold_line": w.cold_line,
             "headline": w.headline, "hook": w.hook, "reason": w.reason,
             "first_line": sents[w.ranges[0][0]].text[:120]}
            for w in sorted(windows, key=lambda x: x.score, reverse=True)]
    (workdir / "candidates.json").write_text(db.dumps(rows))


def stream_profile(camp: campaigns.Campaign, meta: dict, workdir) -> dict:
    """Layout for this video: the campaign's profile file if it names one; for gaming campaigns
    without one, detected from the video itself (once, then cached in meta.json)."""
    if camp.profile:
        return campaigns.load_profile(camp.profile)
    if camp.niche != "gaming":
        return {}
    if "layout" not in meta:
        from app.core import reframe
        info = media.probe(meta["path"])
        meta["layout"] = reframe.detect_stream_layout(meta["path"], info["duration"],
                                                      info["width"], info["height"])
        (workdir / "meta.json").write_text(db.dumps(meta))
    return meta["layout"]


def run_local(path, campaign_slug: str, title: str, stream_url: str, stream_offset: float,
              n_clips: int, profile: dict | None, chat_times: list[float],
              chat_lines: list[str]) -> list[int]:
    """Clips a local file cut from a live recording (see app/live)."""
    from pathlib import Path
    path = Path(path)
    vid = f"live_{campaign_slug}_{int(time.time())}"
    workdir = vod.video_dir(vid)
    dst = workdir / "source.mp4"
    path.replace(dst)
    meta = {"video_id": vid, "url": f"{stream_url}#live{int(stream_offset)}", "title": title,
            "duration": media.probe(dst)["duration"], "path": str(dst), "offset": stream_offset,
            "heatmap": [], "comment_timestamps": [], "chat_times": chat_times, "live": True}
    if profile:
        meta["layout"] = profile
    (workdir / "meta.json").write_text(db.dumps(meta))
    return run(meta["url"], campaign_slug, n_clips=n_clips, meta=meta, chat_lines=chat_lines)


def run(url: str, campaign_slug: str, n_clips: int | None = None, with_captions: bool = True,
        only_kind: str | None = None, start: float | None = None, end: float | None = None,
        meta: dict | None = None, chat_lines: list[str] | None = None) -> list[int]:
    t0 = time.time()
    s = get_settings()
    camp = campaigns.load(campaign_slug)
    weights = camp.signal_weights()
    n_clips = n_clips or camp.editing.candidates

    if meta is None:
        _step("downloading video, heatmap and comments", t0)
        meta = vod.ingest(url, start=start, end=end)
    workdir = vod.video_dir(meta["video_id"])
    src = meta["path"]
    profile = stream_profile(camp, meta, workdir)
    if profile.get("detected"):
        _step(f"layout: {profile['layout']} ({profile['detected']})", t0)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO videos (url, video_id, title, duration, campaign, path, status, created_at)
               VALUES (?,?,?,?,?,?, 'downloaded', ?)
               ON CONFLICT(url) DO UPDATE SET status='downloaded', error=NULL""",
            (meta["url"], meta["video_id"], meta["title"], meta["duration"], campaign_slug, src, db.now()))
        video_row = conn.execute("SELECT id FROM videos WHERE url=?", (meta["url"],)).fetchone()["id"]

    try:
        _step(f"transcribing ({'AssemblyAI' if s.assemblyai_api_key else 'local Whisper, slower'})", t0)
        words = transcript.get_words(src, workdir)
        sents = transcript.to_sentences(words)
        if not sents:
            raise RuntimeError("Transcript is empty: no speech found in this video.")
        context = ""
        if chat_lines:
            context = ("\n<live_chat>\nWhat the stream's chat was typing during this stretch, oldest "
                       "first. Bursts of laughter or shock show where the moment is.\n"
                       + "\n".join(x[:120] for x in chat_lines[-150:]) + "\n</live_chat>\n")

        _step("measuring audience and audio signals", t0)
        tracks = signals.build(meta, media.loudness_per_second(src))
        active = {k for k, v in tracks.items() if np.any(v)}

        _step("recall: scanning the whole transcript for candidate moments", t0)
        windows: list[select.Window] = []
        found = llm.recall(transcript.render_for_llm(sents), camp, meta["duration"],
                           want=max(8, n_clips * 3), context=context)
        last = len(sents) - 1
        if found:
            for c in found.candidates:
                if 0 <= c.start_line <= c.end_line <= last:
                    windows.append(select.Window(len(windows), [(c.start_line, c.end_line)], "llm",
                                                 hook=c.hook))
            sc = camp.editing.supercut
            for sup in found.supercuts if sc.enabled else []:
                ranges = [(p.start_line, p.end_line) for p in sup.parts
                          if 0 <= p.start_line <= p.end_line <= last]
                if len(ranges) >= sc.min_parts:
                    windows.append(select.Window(len(windows), ranges, "llm", kind="supercut",
                                                 hook=sup.hook, headline=sup.theme))
        n_single = sum(w.kind == "single" for w in windows)
        n_super = len(windows) - n_single
        # add signal peaks the transcript pass did not already cover
        for t, _ in signals.peaks(tracks, weights):
            if not any(sents[a].start - 5 <= t <= sents[b].end + 5 for w in windows for a, b in w.ranges):
                windows.append(select.Window(len(windows), [select.from_peak(t, sents)], "signals"))
        for w in windows:
            select.snap(w, sents, camp, meta["duration"])
            _measure(w, tracks)
        _step(f"candidates: {n_single} moments + {n_super} supercuts from the transcript, "
              f"{len(windows) - n_single - n_super} from signal peaks", t0)

        winners = top_clips(camp)
        _step("rerank: judging candidates against the rubric"
              + (f" and {len(winners)} top clips from this campaign" if winners else ""), t0)
        verdicts = llm.judge([select.judge_block(w, sents) for w in windows], camp, winners, context)
        for w in windows:
            if w.id in verdicts:
                select.apply_verdict(w, verdicts[w.id])
                select.snap(w, sents, camp, meta["duration"])
                _measure(w, tracks)
        if verdicts:
            active.add("judge")
        else:
            console.print("[yellow]No AI judge (ANTHROPIC_API_KEY not set): ranking on signals only.[/]")

        for w in windows:
            w.score = select.final_score(w, weights, active)
        ranked = select.dedupe(windows)
        if only_kind:
            ranked = [w for w in ranked if w.kind == only_kind]
        # Supercuts are the campaign's preferred format but score lower on a rubric built for
        # single moments, so the best few always get a place.
        reserved = [w for w in ranked if w.kind == "supercut"][: camp.editing.supercut.reserve]
        rest = [w for w in ranked if w not in reserved]
        shortlist = reserved + rest[: max(0, n_clips + 3 - len(reserved))]
        _save_candidates(workdir, windows, sents)

        if s.gemini_api_key:
            _step(f"video judge: Gemini watches the top {len(shortlist)}", t0)
            for w in shortlist:
                a, b = max(w.segs, key=lambda seg: seg[1] - seg[0])   # its longest segment
                v = llm.video_judge(src, a, b, workdir)
                if v:
                    w.video = v.visual_score / 10
            if any(w.video is not None for w in shortlist):
                active.add("video")
                for w in shortlist:
                    w.score = select.final_score(w, weights, active)
        rest = sorted((w for w in shortlist if w not in reserved), key=lambda w: w.score, reverse=True)
        final = sorted(reserved + rest[: max(0, n_clips - len(reserved))],
                       key=lambda w: w.score, reverse=True)

        clip_ids: list[int] = []
        for i, w in enumerate(final):
            _step(f"rendering clip {i + 1}/{len(final)}  {w.kind}  {transcript.fmt_time(w.start)}"
                  f"  {w.duration:.0f}s  score {w.score:.2f}", t0)
            tag = "sc" if w.kind == "supercut" else "c"
            out = workdir / "clips" / f"{meta['video_id']}_{tag}{int(w.start)}.mp4"
            r = render.render_clip(src, w.segs, words, out, camp, profile, with_captions, w.headline)
            text = " ".join(sents[k].text for k in w.sentence_ids())
            hook = w.hook or text.split(". ")[0][:60]
            caption = check.build_caption(camp, hook, i, w.question)
            report = check.check(camp, caption=caption, duration=r["duration"], render=r,
                                 clip_text=text, ad_like=w.ad_like, spoiler=w.spoiler)
            with db.connect() as conn:
                cur = conn.execute(
                    """INSERT INTO clips (video_id, campaign, start, end, score, hook, reason, caption,
                           pinned_comment, path, rule_report, signals, spoiler, created_at,
                           kind, headline, segments)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (video_row, campaign_slug, w.start, w.end, w.score, hook, w.reason, caption,
                     check.pinned_comment(camp, i), str(out), db.dumps(report),
                     db.dumps({**w.signals, "judge": w.judge, "video": w.video, "source": w.source,
                               "layout": r["layout"], "text": text, "duration": r["duration"],
                               "cut_seconds": r["cut_seconds"], "parts": r["parts"]}),
                     int(w.spoiler), db.now(), w.kind, w.headline, db.dumps(w.segs)))
                clip_ids.append(cur.lastrowid)
            ok = "[green]compliant[/]" if check.compliant(report) else "[red]rule issues[/]"
            console.print(f"         {ok}  {r['duration']:.0f}s after cutting {r['cut_seconds']}s of "
                          f"dead air  {out.name}")

        with db.connect() as conn:
            conn.execute("UPDATE videos SET status='clipped' WHERE id=?", (video_row,))
        _step(f"done: {len(clip_ids)} clips ready for review (run `make review`)", t0)
        return clip_ids
    except Exception as exc:
        with db.connect() as conn:
            conn.execute("UPDATE videos SET status='failed', error=? WHERE id=?", (str(exc)[:500], video_row))
        raise


def rerender(status: str = "pending") -> int:
    """Renders existing clips again with the current settings (watermark, headline, pacing).
    The AI's choices are kept; only the video file is rebuilt, so this costs nothing."""
    import json
    from pathlib import Path
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT c.*, v.path AS src FROM clips c JOIN videos v ON v.id=c.video_id "
            "WHERE c.status=? AND c.segments IS NOT NULL ORDER BY c.id", (status,))]
    done = 0
    for r in rows:
        camp = campaigns.load(r["campaign"])
        words_path = Path(r["src"]).parent / "words.json"
        if not words_path.exists() or not Path(r["src"]).exists():
            console.print(f"[yellow]clip #{r['id']}: source files missing, skipped[/]")
            continue
        segs = [tuple(x) for x in db.loads(r["segments"], [])]
        meta_path = Path(r["src"]).parent / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"path": r["src"]}
        profile = stream_profile(camp, meta, meta_path.parent)
        out = render.render_clip(r["src"], segs, json.loads(words_path.read_text()), Path(r["path"]),
                                 camp, profile, True, r["headline"] or "")
        sig = db.loads(r["signals"], {})
        report = check.check(camp, caption=r["caption"], duration=out["duration"], render=out,
                             clip_text=sig.get("text", ""), spoiler=bool(r["spoiler"]),
                             ad_like=any(x["rule"] == "not ad-like" and not x["ok"]
                                         for x in db.loads(r["rule_report"], [])))
        sig.update(duration=out["duration"], cut_seconds=out["cut_seconds"], layout=out["layout"])
        with db.connect() as conn:
            conn.execute("UPDATE clips SET rule_report=?, signals=? WHERE id=?",
                         (db.dumps(report), db.dumps(sig), r["id"]))
        ok = "[green]compliant[/]" if check.compliant(report) else "[red]rule issues[/]"
        console.print(f"clip #{r['id']}: {out['duration']:.0f}s  {ok}")
        done += 1
    return done


def send_live_clips(ids: list[int]) -> None:
    """Sends live clips straight to the phone: speed matters while a stream is trending."""
    from pathlib import Path

    from app.notify import telegram
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids)] if ids else []
    for r in rows:
        try:
            telegram.send_video(Path(r["path"]), f"LIVE clip #{r['id']}: {r['headline'] or r['hook']}"
                                f" (score {r['score']:.2f})")
            telegram.send_text(r["caption"])
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Telegram send failed for #{r['id']}: {exc}[/]")
