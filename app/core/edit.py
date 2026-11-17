"""Pacing: cuts dead air and filler words out of a clip (jump cuts), and keeps track of where
every word lands on the shortened timeline so captions stay in sync."""
from __future__ import annotations

import math
import re

from app.rules.campaign import Tighten

FILLERS = {"um", "uh", "uhm", "umm", "uhh", "erm", "er", "hmm", "mm", "mhm"}
HEAD_PAD, TAIL_PAD, MIN_PIECE = 0.10, 0.22, 0.35


def _is_filler(word: dict) -> bool:
    return re.sub(r"[^a-z]", "", word["text"].lower()) in FILLERS


def keep_intervals(words: list[dict], segs: list[tuple[float, float]], cfg: Tighten,
                   fps: float) -> list[list[tuple[float, float]]]:
    """For each segment, the pieces of it worth keeping (source times, in order).
    With tightening off, each segment is kept whole."""
    out: list[list[tuple[float, float]]] = []
    for a, b in segs:
        inside = [w for w in words if a <= (w["start"] + w["end"]) / 2 <= b]
        if cfg.remove_fillers and cfg.enabled:
            inside = [w for w in inside if not _is_filler(w)]
        if not cfg.enabled or not inside:
            out.append([(a, b)])
            continue
        pieces: list[list[float]] = [[max(a, inside[0]["start"] - HEAD_PAD), inside[0]["end"]]]
        for prev, w in zip(inside, inside[1:]):
            gap = w["start"] - prev["end"]
            if gap > cfg.max_pause:
                pieces[-1][1] = prev["end"] + cfg.keep_pause / 2
                pieces.append([w["start"] - cfg.keep_pause / 2, w["end"]])
            else:
                pieces[-1][1] = w["end"]
        pieces[-1][1] = min(b, inside[-1]["end"] + TAIL_PAD)
        # a sliver between two cuts sounds like a glitch: glue it to its neighbour
        merged: list[list[float]] = []
        for p in pieces:
            if merged and (p[1] - p[0] < MIN_PIECE or merged[-1][1] - merged[-1][0] < MIN_PIECE):
                merged[-1][1] = p[1]
            else:
                merged.append(p)
        out.append([(s, e) for s, e in merged])
    return _frame_align(out, segs, fps)


def _frame_align(kept: list[list[tuple[float, float]]], segs: list[tuple[float, float]],
                 fps: float) -> list[list[tuple[float, float]]]:
    """Moves every cut onto a video frame boundary (counted from its segment's start), so the
    picture and the sound are cut at exactly the same instant and never drift apart."""
    out = []
    for pieces, (a, _) in zip(kept, segs):
        aligned = []
        for s, e in pieces:
            s2 = a + math.floor((s - a) * fps + 1e-6) / fps
            e2 = a + math.ceil((e - a) * fps - 1e-6) / fps
            if aligned and s2 < aligned[-1][1]:
                s2 = aligned[-1][1]
            if e2 - s2 > 1 / fps:
                aligned.append((s2, e2))
        out.append(aligned)
    return out


def seg_frames(seg: tuple[float, float], fps: float) -> int:
    return max(1, round((seg[1] - seg[0]) * fps))


def remap_words(words: list[dict], kept: list[list[tuple[float, float]]]) -> tuple[list[dict], float]:
    """Words re-timed onto the output timeline, plus the output duration.
    A segment can repeat footage (a cold open), so a word may appear more than once."""
    out: list[dict] = []
    offset = 0.0
    for pieces in kept:
        for s, e in pieces:
            for w in words:
                mid = (w["start"] + w["end"]) / 2
                if s <= mid < e and not _is_filler(w):
                    out.append({**w, "start": offset + max(0.0, w["start"] - s),
                                "end": offset + min(e - s, w["end"] - s)})
            offset += e - s
    return out, offset
