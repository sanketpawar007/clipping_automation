"""Turns raw candidates into final clip windows: merge sources, snap boundaries, score, dedupe.

A window is either one continuous moment ("single") or several answers stitched together
("supercut"). Either way it ends up as a list of segments of the source video, played in order.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.transcript import Sentence
from app.rules.campaign import Campaign

JUDGE_MIX = {"hook_score": 0.30, "payoff_score": 0.25, "standalone_score": 0.20,
             "emotion_score": 0.15, "campaign_fit_score": 0.10}
LEAD_IN, TAIL = 0.15, 0.30
COLD_OPEN_MAX_S = 5.0


@dataclass
class Window:
    id: int
    ranges: list[tuple[int, int]]            # sentence ranges (first, last inclusive), in play order
    source: str                              # "llm" or "signals"
    kind: str = "single"                     # "single" or "supercut"
    hook: str = ""
    headline: str = ""
    question: str = ""
    reason: str = ""
    cold_line: int = -1                      # sentence to replay at the very start, or -1
    segs: list[tuple[float, float]] = field(default_factory=list)   # source-time segments
    signals: dict = field(default_factory=dict)
    judge: float | None = None
    video: float | None = None
    ad_like: bool = False
    spoiler: bool = False
    explicit: bool = False
    postable: bool = True
    fmt: str = "reaction"                 # reaction | story | reveal | montage (set by the video editor)
    title_style: str = "box"              # box | plain | none
    captions: bool = True
    zoom_mode: str = "punch"              # punch | push | none
    collect: dict | None = None           # a moment saved for a later montage
    zooms: list[float] = field(default_factory=list)   # source times to punch in
    score: float = 0.0

    @property
    def start(self) -> float:
        return min(a for a, _ in self.segs)

    @property
    def end(self) -> float:
        return max(b for _, b in self.segs)

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.segs)

    def sentence_ids(self) -> list[int]:
        return [i for a, b in self.ranges for i in range(a, b + 1)]


def from_peak(t: float, sents: list[Sentence], before: float = 14, after: float = 24) -> tuple[int, int]:
    """A sentence range around a signal peak (the peak is usually the reaction, so lead in)."""
    s0 = next((s.idx for s in sents if s.end >= t - before), 0)
    s1 = next((s.idx for s in reversed(sents) if s.start <= t + after), len(sents) - 1)
    return s0, max(s0, s1)


def _edges(s0: int, s1: int, sents: list[Sentence], duration: float) -> tuple[float, float]:
    """Start and end times for a sentence range, with a breath of room but no neighbouring words."""
    prev_end = sents[s0 - 1].end if s0 > 0 else 0.0
    next_start = sents[s1 + 1].start if s1 + 1 < len(sents) else duration
    start = max(prev_end, sents[s0].start - LEAD_IN, 0.0)
    end = min(max(sents[s1].end, min(sents[s1].end + TAIL, next_start)), duration)
    return start, end


def _span(s0: int, s1: int, sents: list[Sentence]) -> float:
    return sents[s1].end - sents[s0].start


def snap(w: Window, sents: list[Sentence], campaign: Campaign, duration: float) -> None:
    """Sets the window's segments on sentence boundaries and fits the campaign's length limits."""
    lo, hi = campaign.clip.min_seconds, campaign.clip.max_seconds
    n = len(sents)
    ranges = [(max(0, min(a, n - 1)), max(0, min(b, n - 1))) for a, b in w.ranges]
    ranges = [(a, max(a, b)) for a, b in ranges]

    if w.kind == "supercut":
        sc = campaign.editing.supercut
        ranges = ranges[: sc.max_parts]
        # too long: trim the longest part from its end, then drop whole parts
        while sum(_span(a, b, sents) for a, b in ranges) > hi - 1:
            i = max(range(len(ranges)), key=lambda k: _span(*ranges[k], sents))
            a, b = ranges[i]
            if b > a:
                ranges[i] = (a, b - 1)
            elif len(ranges) > sc.min_parts:
                ranges.pop()
            else:
                break
    else:
        s0, s1 = ranges[0]
        while s1 > s0 and _span(s0, s1, sents) > hi - LEAD_IN - TAIL:
            s1 -= 1                                   # too long: drop trailing sentences
        payoff_first = campaign.editing.end_on_payoff and w.source == "llm"
        while _span(s0, s1, sents) < lo:
            can_back = s0 > 0 and _span(s0 - 1, s1, sents) <= hi
            can_fwd = s1 + 1 < n and _span(s0, s1 + 1, sents) <= hi
            # the AI ended on the payoff: add setup before it rather than rambling after it
            if can_back and (payoff_first or not can_fwd):
                s0 -= 1
            elif can_fwd:
                s1 += 1
            else:
                break
        ranges = [(s0, s1)]

    w.ranges = ranges
    w.segs = [_edges(a, b, sents, duration) for a, b in ranges]

    # Cold open: replay the strongest line first, as a teaser, then play the moment.
    if campaign.editing.cold_open and w.kind == "single" and w.cold_line >= 0:
        s0, s1 = ranges[0]
        c = w.cold_line
        if s0 + 1 < c <= s1:                          # no point teasing a line that is already first
            a, b = _edges(c, c, sents, duration)
            if b - a <= COLD_OPEN_MAX_S and w.duration + (b - a) <= hi:
                w.segs.insert(0, (a, b))


def judge_block(w: Window, sents: list[Sentence], context: int = 2) -> dict:
    lines = []
    for part, (s0, s1) in enumerate(w.ranges, 1):
        if w.kind == "supercut":
            lines.append(f"--- part {part} ---")
        ctx = context if w.kind == "single" else 0
        for s in sents[max(0, s0 - ctx): s1 + ctx + 1]:
            tag = "" if s0 <= s.idx <= s1 else " (context)"
            who = f"Speaker {s.speaker}: " if s.speaker else ""
            lines.append(f"[{s.idx}]{tag} {who}{s.text}")
    return {"id": w.id, "kind": w.kind, "lines": "\n".join(lines), "seconds": w.duration,
            "signals": w.signals}


def apply_verdict(w: Window, v, context: int = 2) -> None:
    w.judge = sum(getattr(v, k) * wt for k, wt in JUDGE_MIX.items()) / 10
    if w.kind == "single":
        lo, hi = w.ranges[0][0] - context, w.ranges[0][1] + context
        if lo <= v.start_line <= v.end_line <= hi:    # only accept edits inside what it was shown
            w.ranges = [(v.start_line, v.end_line)]
        w.cold_line = v.cold_open_line
    w.hook, w.reason = v.caption_hook, v.reason
    w.headline, w.question = v.headline, v.caption_question
    w.ad_like, w.spoiler, w.explicit = v.ad_like, v.spoiler, v.explicit


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


def _overlap(a: Window, b: Window) -> float:
    """Share of the shorter window's footage that also appears in the other."""
    inter = sum(max(0.0, min(e1, e2) - max(s1, s2)) for s1, e1 in a.segs for s2, e2 in b.segs)
    return inter / max(1e-6, min(a.duration, b.duration))


def dedupe(windows: list[Window], max_overlap: float = 0.3) -> list[Window]:
    """Keeps the best-scoring window out of any group that reuses the same footage.
    A supercut may reuse footage from a single clip: they are different posts."""
    kept: list[Window] = []
    for w in sorted(windows, key=lambda x: x.score, reverse=True):
        if all(k.kind != w.kind or _overlap(w, k) <= max_overlap for k in kept):
            kept.append(w)
    return kept
