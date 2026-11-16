"""Turns raw candidates into final clip windows: merge sources, snap boundaries, score, dedupe."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.transcript import Sentence
from app.rules.campaign import Campaign

JUDGE_MIX = {"hook_score": 0.30, "payoff_score": 0.25, "standalone_score": 0.20,
             "emotion_score": 0.15, "campaign_fit_score": 0.10}
LEAD_IN, TAIL = 0.15, 0.35


@dataclass
class Window:
    id: int
    s0: int                       # first sentence index
    s1: int                       # last sentence index (inclusive)
    source: str                   # "llm" or "signals"
    hook: str = ""
    reason: str = ""
    start: float = 0.0
    end: float = 0.0
    signals: dict = field(default_factory=dict)
    judge: float | None = None
    video: float | None = None
    ad_like: bool = False
    spoiler: bool = False
    score: float = 0.0


def from_peak(t: float, sents: list[Sentence], before: float = 14, after: float = 24) -> tuple[int, int]:
    """A sentence range around a signal peak (the peak is usually the reaction, so lead in)."""
    s0 = next((s.idx for s in sents if s.end >= t - before), 0)
    s1 = next((s.idx for s in reversed(sents) if s.start <= t + after), len(sents) - 1)
    return s0, max(s0, s1)


def snap(w: Window, sents: list[Sentence], campaign: Campaign, duration: float) -> None:
    """Sets start/end on sentence boundaries and fits the campaign's length limits."""
    lo, hi = campaign.clip.min_seconds, campaign.clip.max_seconds
    n = len(sents)
    w.s0 = max(0, min(w.s0, n - 1))
    w.s1 = max(w.s0, min(w.s1, n - 1))
    while w.s1 > w.s0 and sents[w.s1].end - sents[w.s0].start > hi - LEAD_IN - TAIL:
        w.s1 -= 1                                   # too long: drop trailing sentences
    while sents[w.s1].end - sents[w.s0].start < lo:
        if w.s1 + 1 < n and sents[w.s1 + 1].end - sents[w.s0].start <= hi:
            w.s1 += 1                               # too short: let it run on
        elif w.s0 > 0 and sents[w.s1].end - sents[w.s0 - 1].start <= hi:
            w.s0 -= 1                               # or start earlier
        else:
            break
    prev_end = sents[w.s0 - 1].end if w.s0 > 0 else 0.0
    next_start = sents[w.s1 + 1].start if w.s1 + 1 < n else duration
    w.start = max(prev_end, sents[w.s0].start - LEAD_IN, 0.0)
    w.end = min(max(sents[w.s1].end, min(sents[w.s1].end + TAIL, next_start)), duration)


def judge_block(w: Window, sents: list[Sentence], context: int = 2) -> dict:
    lines = []
    for s in sents[max(0, w.s0 - context): w.s1 + context + 1]:
        tag = "" if w.s0 <= s.idx <= w.s1 else " (context)"
        who = f"Speaker {s.speaker}: " if s.speaker else ""
        lines.append(f"[{s.idx}]{tag} {who}{s.text}")
    return {"id": w.id, "lines": "\n".join(lines), "seconds": w.end - w.start, "signals": w.signals}


def apply_verdict(w: Window, v, context: int = 2) -> None:
    w.judge = sum(getattr(v, k) * wt for k, wt in JUDGE_MIX.items()) / 10
    lo, hi = w.s0 - context, w.s1 + context
    if lo <= v.start_line <= v.end_line <= hi:      # only accept edits inside what it was shown
        w.s0, w.s1 = v.start_line, v.end_line
    w.hook, w.reason = v.caption_hook, v.reason
    w.ad_like, w.spoiler = v.ad_like, v.spoiler


def final_score(w: Window, weights: dict[str, float], active: set[str]) -> float:
    """Weighted average over the signals that actually exist for this video."""
    values = {**w.signals, "judge": w.judge, "video": w.video}
    num = den = 0.0
    for name, wt in weights.items():
        v = values.get(name)
        if v is None or name not in active:
            continue
        num += wt * v
        den += wt
    return num / den if den else 0.0


def dedupe(windows: list[Window], max_overlap: float = 0.3) -> list[Window]:
    """Keeps the best-scoring window out of any group that overlaps heavily."""
    kept: list[Window] = []
    for w in sorted(windows, key=lambda x: x.score, reverse=True):
        def overlap(a: Window, b: Window) -> float:
            inter = min(a.end, b.end) - max(a.start, b.start)
            return inter / min(a.end - a.start, b.end - b.start) if inter > 0 else 0.0
        if all(overlap(w, k) <= max_overlap for k in kept):
            kept.append(w)
    return kept
