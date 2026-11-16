"""Word-by-word captions in ASS format (the subtitle format ffmpeg/libass can burn in).

Two or three words show at a time and the word being spoken is highlighted.
"""
from __future__ import annotations

import re
from pathlib import Path

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
HIGHLIGHT = "&H0000E6FF&"   # ASS colours are blue-green-red: this is yellow
MAX_WORDS, MAX_CHARS, MAX_GAP = 3, 18, 0.45


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _clean(word: str) -> str:
    w = re.sub(r"[{}\\]", "", word).strip().upper()
    return re.sub(r"[.,;:\"“”]+$", "", w) or w


def chunk(words: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            chars = sum(len(_clean(x["text"])) for x in cur) + len(cur) + len(_clean(w["text"]))
            if (len(cur) >= MAX_WORDS or chars > MAX_CHARS
                    or w["start"] - cur[-1]["end"] > MAX_GAP
                    or cur[-1]["text"].endswith((".", "?", "!"))):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def write_ass(words: list[dict], clip_start: float, clip_end: float, path: Path,
              font: str = "Arial Black", size: int = 86, margin_v: int = 560) -> Path:
    """`words` use source-video times; captions are written relative to the clip start."""
    inside = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    lines = [HEADER.format(font=font, size=size, margin_v=margin_v)]
    groups = chunk(inside)
    for gi, group in enumerate(groups):
        group_end = min(group[-1]["end"] + 0.25,
                        groups[gi + 1][0]["start"] if gi + 1 < len(groups) else clip_end)
        for wi, w in enumerate(group):
            start = w["start"] if wi else group[0]["start"]
            end = group[wi + 1]["start"] if wi + 1 < len(group) else group_end
            if end - start < 0.02:
                continue
            text = " ".join(
                f"{{\\c{HIGHLIGHT}}}{_clean(x['text'])}{{\\c&HFFFFFF&}}" if j == wi else _clean(x["text"])
                for j, x in enumerate(group))
            lines.append(f"Dialogue: 0,{_ts(start - clip_start)},{_ts(end - clip_start)},Cap,,0,0,0,,"
                         f"{{\\fad(0,0)}}{text}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path
