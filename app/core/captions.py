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
Style: Head,{font},74,&H00101010,&H00101010,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,2,70,70,{head_margin},1
Style: Cta,{font},72,&H00FFFFFF,&H00FFFFFF,&H00E83A2B,&H00E83A2B,-1,0,0,0,100,100,0,0,3,22,0,5,80,80,0,1
Style: Credit,{font},42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,7,50,50,0,1
Style: Label,{font},36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,10,0,9,50,50,0,1
Style: Warn,{font},64,&H0000E6FF,&H0000E6FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,18,0,5,80,80,0,1

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


def wrap_headline(text: str, width: int = 20, max_lines: int = 2) -> str:
    """Breaks the headline into at most two balanced lines (ASS uses \\N for a line break)."""
    words, lines = _strip(text).split(), [""]
    for w in words:
        if lines[-1] and len(lines[-1]) + 1 + len(w) > width and len(lines) < max_lines:
            lines.append(w)
        else:
            lines[-1] = f"{lines[-1]} {w}".strip()
    return "\\N".join(lines)


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def _strip(text: str) -> str:
    """Text safe for burned-in titles: no ASS control characters, and no emoji (the caption font
    has none, so they would render as empty boxes; emoji stay in the post caption)."""
    text = _EMOJI.sub("", text)
    return re.sub(r"\s+", " ", re.sub(r"[{}\\\n]", " ", text)).strip()


def overlay(style: str, text: str, start: float, end: float, x: int, y: int, fade: int = 0) -> dict:
    return {"style": style, "text": text, "start": start, "end": end, "x": x, "y": y, "fade": fade}


def write_ass(words: list[dict], clip_start: float, clip_end: float, path: Path,
              font: str = "Arial Black", size: int = 86, margin_v: int = 560,
              headline: str = "", head_margin: int | None = None,
              overlays: list[dict] | None = None) -> Path:
    """`words` use source-video times; captions are written relative to the clip start.
    `headline` is shown for the whole clip: black text on a white box."""
    inside = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    # the headline sits just above the captions, clear of faces at the top of the frame
    lines = [HEADER.format(font=font, size=size, margin_v=margin_v,
                           head_margin=head_margin if head_margin is not None else margin_v + 150)]
    for o in overlays or []:
        fade = f"\\fad({o['fade']},0)" if o.get("fade") else ""
        lines.append(f"Dialogue: 2,{_ts(o['start'])},{_ts(o['end'])},{o['style']},,0,0,0,,"
                     f"{{\\pos({o['x']},{o['y']}){fade}}}{wrap_headline(o['text'], 22, 3)}\n")
    if headline:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(clip_end - clip_start)},Head,,0,0,0,,"
                     f"{wrap_headline(headline)}\n")
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
                f"{{\\c{HIGHLIGHT}\\fscx112\\fscy112}}{_clean(x['text'])}{{\\c&HFFFFFF&\\fscx100\\fscy100}}"
                if j == wi else _clean(x["text"])
                for j, x in enumerate(group))
            lines.append(f"Dialogue: 0,{_ts(start - clip_start)},{_ts(end - clip_start)},Cap,,0,0,0,,"
                         f"{{\\fad(0,0)}}{text}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path
