"""Finds hype moments while a stream is live.

Chat is the strongest signal on a stream: when something funny or shocking happens, message
rate jumps, many different people type at once, and the same reactions repeat (KEKW, LMAO, 💀).
Each streamer has a different normal level, so a spike is measured against that stream's own
recent baseline, not a fixed number."""
from __future__ import annotations

import re
import statistics
from collections import Counter

BUCKET_S = 5
REACTION = re.compile(
    r"(kekw|omegalul|lul|lmao+|lmfao|icant|hahah+|😂|🤣|💀|w+ +|^w$|^l$|no way|clip (it|that)|"
    r"pog|holy|wtf|ayo|nah+|bro+)", re.I)


def _buckets(msgs: list[tuple[float, str, str]], t0: float, t1: float) -> list[list]:
    n = max(1, int((t1 - t0) // BUCKET_S))
    out: list[list] = [[] for _ in range(n)]
    for t, user, text in msgs:
        i = int((t - t0) // BUCKET_S)
        if 0 <= i < n:
            out[i].append((user, text))
    return out


def chat_spike(msgs: list[tuple[float, str, str]], now: float, recent_s: float = 15,
               history_s: float = 600) -> dict:
    """Spike strength for the last `recent_s` seconds against the previous `history_s`.
    Returns z (how unusual the activity is), the counts behind it, and the top repeated words."""
    hist = _buckets(msgs, now - history_s - recent_s, now - recent_s)
    rec = [m for b in _buckets(msgs, now - recent_s, now) for m in b]

    def score(bucket_msgs: list) -> float:
        users = {u for u, _ in bucket_msgs}
        reacts = sum(1 for _, x in bucket_msgs if REACTION.search(x))
        return len(users) + 0.5 * reacts

    base = [score(b) for b in hist] or [0.0]
    med = statistics.median(base)
    mad = statistics.median([abs(x - med) for x in base]) or 1.0
    per_bucket = score(rec) / max(1, recent_s / BUCKET_S)
    z = (per_bucket - med) / (1.4826 * mad)
    words = Counter(w.lower() for _, x in rec for w in re.findall(r"\w+|[💀😂🤣]", x) if len(w) > 1)
    return {"z": round(z, 2), "messages": len(rec), "users": len({u for u, _ in rec}),
            "baseline": round(med, 2), "top": [w for w, _ in words.most_common(5)]}


def audio_spike(loud_db: list[float], recent_s: int = 8, history_s: int = 300) -> float:
    """How far the last few seconds' loudness sits above the recent normal, in dB."""
    if len(loud_db) < recent_s + 30:
        return 0.0
    hist = loud_db[-(history_s + recent_s):-recent_s]
    return round(max(loud_db[-recent_s:]) - statistics.median(hist), 2)
