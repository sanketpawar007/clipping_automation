"""`make selftest`: one small real call to each paid service, to prove the keys work.

Needs one already-downloaded video in data/videos/. Costs a few cents.
"""
from __future__ import annotations

import json
import subprocess
import time

from rich.console import Console

from app.config import get_settings
from app.core import llm, select, transcript
from app.notify import telegram
from app.rules import campaign as campaigns

console = Console()


def main() -> int:
    s = get_settings()
    camp = campaigns.load("lhm")
    dirs = [p.parent for p in (s.data_dir / "videos").glob("*/words.json")
            if (p.parent / "source.mp4").exists()]
    if not dirs:
        console.print("[red]No downloaded video with a transcript in data/videos/. Run one clip job first.[/]")
        return 1
    d = dirs[0]
    src = d / "source.mp4"
    sents = transcript.to_sentences(json.loads((d / "words.json").read_text()))
    duration = sents[-1].end
    state: dict = {"cands": []}
    failed = 0

    def step(name: str, fn) -> None:
        nonlocal failed
        t = time.time()
        try:
            console.print(f"[green]PASS[/] {name} ({time.time() - t:.0f}s): {fn()}", highlight=False)
        except Exception as exc:  # noqa: BLE001 - report every failure, keep testing the rest
            failed += 1
            console.print(f"[red]FAIL[/] {name}: {type(exc).__name__}: {str(exc)[:500]}", highlight=False)

    def recall() -> str:
        state["cands"] = llm.recall(transcript.render_for_llm(sents), camp, duration, want=8)
        assert state["cands"], "no candidates returned"
        return f"{len(state['cands'])} candidates, e.g. {state['cands'][0].hook!r}"

    def judge() -> str:
        picks = [(c.start_line, c.end_line) for c in state["cands"][:4]] or [(10, 20)]
        windows = [select.Window(i, a, b, "llm") for i, (a, b) in enumerate(picks)]
        for w in windows:
            select.snap(w, sents, camp, duration)
            w.signals = {"loudness": 0.5}
        verdicts = llm.judge([select.judge_block(w, sents) for w in windows], camp)
        assert verdicts, "no verdicts returned"
        v = next(iter(verdicts.values()))
        return f"{len(verdicts)} verdicts, hook {v.hook_score}/10, caption {v.caption_hook!r}"

    def gemini() -> str:
        start = min(200.0, max(0.0, duration - 40))
        v = llm.video_judge(src, start, start + 30, d)
        assert v is not None, "no verdict (the reason is printed above)"
        return f"visual {v.visual_score}/10: {v.note[:90]}"

    def assembly() -> str:
        clip = d / "selftest.m4a"
        subprocess.run([s.ffmpeg, "-y", "-v", "error", "-ss", "60", "-t", "45", "-i", str(src),
                        "-vn", "-ac", "1", "-ar", "16000", str(clip)], check=True)
        try:
            words = transcript._assemblyai(clip)
        finally:
            clip.unlink(missing_ok=True)
        speakers = sorted({w["speaker"] for w in words if w["speaker"]})
        return f"{len(words)} words, speakers {speakers}: " + " ".join(w["text"] for w in words[:8])

    def tele() -> str:
        assert telegram.send_text("viral-clipper selftest: Telegram delivery works ✅"), "not configured"
        return "message sent"

    step(f"Claude recall ({s.recall_model})", recall)
    step(f"Claude judge ({s.judge_model})", judge)
    step(f"Gemini video judge ({s.gemini_model})", gemini)
    step("AssemblyAI transcript", assembly)
    step("Telegram", tele)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
