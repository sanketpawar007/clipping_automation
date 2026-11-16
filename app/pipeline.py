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

console = Console()


def _step(msg: str, t0: float) -> None:
    console.print(f"[dim]{time.time() - t0:6.0f}s[/] {msg}")


def run(url: str, campaign_slug: str, n_clips: int = 6, with_captions: bool = True) -> list[int]:
    t0 = time.time()
    s = get_settings()
    camp = campaigns.load(campaign_slug)
    profile = campaigns.load_profile(camp.profile)
    weights = camp.signal_weights()

    _step("downloading video, heatmap and comments", t0)
    meta = vod.ingest(url)
    workdir = vod.video_dir(meta["video_id"])
    src = meta["path"]
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

        _step("measuring audience and audio signals", t0)
        tracks = signals.build(meta, media.loudness_per_second(src))
        active = {k for k, v in tracks.items() if np.any(v)}

        _step("recall: scanning the whole transcript for candidate moments", t0)
        windows: list[select.Window] = []
        for c in llm.recall(transcript.render_for_llm(sents), camp, meta["duration"]):
            if 0 <= c.start_line <= c.end_line < len(sents):
                windows.append(select.Window(len(windows), c.start_line, c.end_line, "llm", hook=c.hook))
        n_llm = len(windows)
        # add signal peaks the transcript pass did not already cover
        for t, _ in signals.peaks(tracks, weights):
            if not any(sents[w.s0].start - 5 <= t <= sents[w.s1].end + 5 for w in windows):
                s0, s1 = select.from_peak(t, sents)
                windows.append(select.Window(len(windows), s0, s1, "signals"))
        for w in windows:
            select.snap(w, sents, camp, meta["duration"])
            w.signals = signals.window_scores(tracks, w.start, w.end)
        _step(f"{n_llm} candidates from the transcript, {len(windows) - n_llm} from signal peaks", t0)

        _step("rerank: judging candidates against the rubric", t0)
        verdicts = llm.judge([select.judge_block(w, sents) for w in windows], camp)
        for w in windows:
            if w.id in verdicts:
                select.apply_verdict(w, verdicts[w.id])
                select.snap(w, sents, camp, meta["duration"])
                w.signals = signals.window_scores(tracks, w.start, w.end)
        if verdicts:
            active.add("judge")
        else:
            console.print("[yellow]No AI judge (ANTHROPIC_API_KEY not set): ranking on signals only.[/]")

        for w in windows:
            w.score = select.final_score(w, weights, active)
        shortlist = select.dedupe(windows)[: n_clips + 3]

        if s.gemini_api_key:
            _step(f"video judge: Gemini watches the top {len(shortlist)}", t0)
            for w in shortlist:
                v = llm.video_judge(src, w.start, w.end, workdir)
                if v:
                    w.video = v.visual_score / 10
            if any(w.video is not None for w in shortlist):
                active.add("video")
                for w in shortlist:
                    w.score = select.final_score(w, weights, active)
        final = sorted(shortlist, key=lambda w: w.score, reverse=True)[:n_clips]

        clip_ids: list[int] = []
        for i, w in enumerate(final):
            _step(f"rendering clip {i + 1}/{len(final)}  {transcript.fmt_time(w.start)}-"
                  f"{transcript.fmt_time(w.end)}  score {w.score:.2f}", t0)
            out = workdir / "clips" / f"{meta['video_id']}_{int(w.start)}.mp4"
            r = render.render_clip(src, w.start, w.end, words, out, camp, profile, with_captions)
            text = " ".join(x.text for x in sents[w.s0: w.s1 + 1])
            hook = w.hook or text.split(". ")[0][:60]
            caption = check.build_caption(camp, hook, i)
            report = check.check(camp, caption=caption, duration=r["duration"], render=r,
                                 clip_text=text, ad_like=w.ad_like, spoiler=w.spoiler)
            with db.connect() as conn:
                cur = conn.execute(
                    """INSERT INTO clips (video_id, campaign, start, end, score, hook, reason, caption,
                           pinned_comment, path, rule_report, signals, spoiler, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (video_row, campaign_slug, w.start, w.end, w.score, hook, w.reason, caption,
                     check.pinned_comment(camp, i), str(out), db.dumps(report),
                     db.dumps({**w.signals, "judge": w.judge, "video": w.video, "source": w.source,
                               "layout": r["layout"], "text": text}),
                     int(w.spoiler), db.now()))
                clip_ids.append(cur.lastrowid)
            ok = "[green]compliant[/]" if check.compliant(report) else "[red]rule issues[/]"
            console.print(f"         {ok}  {r['layout']} layout  {out.name}")

        with db.connect() as conn:
            conn.execute("UPDATE videos SET status='clipped' WHERE id=?", (video_row,))
        _step(f"done: {len(clip_ids)} clips ready for review (run `make review`)", t0)
        return clip_ids
    except Exception as exc:
        with db.connect() as conn:
            conn.execute("UPDATE videos SET status='failed', error=? WHERE id=?", (str(exc)[:500], video_row))
        raise
