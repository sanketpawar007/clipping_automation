"""Audience and audio signals, each turned into a 0..1 score for every second of the video."""
from __future__ import annotations

import numpy as np


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    if len(x) == 0 or k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def _norm(x: np.ndarray) -> np.ndarray:
    """Scales so the 50th percentile maps to 0 and the 99th to 1. Robust to outliers."""
    if len(x) == 0 or not np.any(x):
        return np.zeros_like(x, dtype=float)
    lo, hi = np.percentile(x, 50), np.percentile(x, 99)
    if hi - lo < 1e-9:
        return np.zeros_like(x, dtype=float)
    return np.clip((x - lo) / (hi - lo), 0, 1)


def heatmap_track(heatmap: list[dict], n: int) -> np.ndarray:
    """YouTube "most replayed" graph."""
    x = np.zeros(n)
    for h in heatmap:
        a, b = int(h["start_time"]), min(n, int(np.ceil(h["end_time"])))
        x[a:b] = h["value"]
    if n > 60:
        x[:30] *= 0.3   # the intro is always "replayed"; it says nothing about the content
    return _norm(x)


def comments_track(stamps: list[dict], n: int) -> np.ndarray:
    """Timestamps viewers wrote in comments. A mention points at the seconds that follow it."""
    x = np.zeros(n)
    for c in stamps:
        t = int(c["t"])
        x[t: min(n, t + 30)] += 1 + np.log1p(c["likes"])
    return np.clip(x / x.max(), 0, 1) if x.max() > 0 else x


def loudness_track(db: np.ndarray, n: int) -> np.ndarray:
    """Loudness relative to the surrounding minute, so a quiet podcast and a loud stream compare."""
    x = np.zeros(n)
    m = min(n, len(db))
    if m == 0:
        return x
    local = db[:m] - _smooth(db[:m], 61)
    x[:m] = _norm(_smooth(np.maximum(local, 0), 5))
    return x


def build(meta: dict, loudness_db: np.ndarray) -> dict[str, np.ndarray]:
    n = int(np.ceil(meta["duration"])) + 1
    return {
        "heatmap": heatmap_track(meta.get("heatmap") or [], n),
        "comments": comments_track(meta.get("comment_timestamps") or [], n),
        "loudness": loudness_track(loudness_db, n),
    }


def window_scores(tracks: dict[str, np.ndarray], start: float, end: float) -> dict[str, float]:
    """Each signal's strength inside a clip window: mean of its top third of seconds."""
    out = {}
    for name, x in tracks.items():
        seg = x[int(start): max(int(start) + 1, int(np.ceil(end)))]
        if len(seg) == 0:
            out[name] = 0.0
            continue
        k = max(1, len(seg) // 3)
        out[name] = float(np.sort(seg)[-k:].mean())
    return out


def peaks(tracks: dict[str, np.ndarray], weights: dict[str, float], top: int = 12,
          min_gap: int = 45, window: int = 30) -> list[tuple[int, float]]:
    """Seconds where the weighted signals peak. Used to add candidates the transcript pass missed."""
    n = len(next(iter(tracks.values())))
    combined = np.zeros(n)
    total = 0.0
    for name, x in tracks.items():
        if np.any(x):                      # only signals this video actually has
            combined += weights.get(name, 0) * x
            total += weights.get(name, 0)
    if total == 0:
        return []
    sm = _smooth(combined / total, window)
    out: list[tuple[int, float]] = []
    for t in np.argsort(sm)[::-1]:
        if sm[t] <= 0.05 or len(out) >= top:
            break
        if all(abs(int(t) - p) >= min_gap for p, _ in out):
            out.append((int(t), float(sm[t])))
    return out
