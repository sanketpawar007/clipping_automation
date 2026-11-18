"""Renders one clip: stitch its segments, reframe to 9:16, cut dead air, burn captions and
the headline, add campaign overlays, encode. Everything happens in a single ffmpeg run."""
from __future__ import annotations

import re
from pathlib import Path

from app.config import ROOT, get_settings
from app.core import captions, edit, media, reframe
from app.rules import campaign as campaigns
from app.rules.campaign import Campaign

WM_POS = {
    "top_right": "W-w-40:150", "top_left": "40:150",
    "bottom_right": "W-w-40:H-h-420", "bottom_left": "40:H-h-420",
    "top_center": "(W-w)/2:150",
}


def _out_times(t: float, kept: list[list[tuple[float, float]]]) -> list[float]:
    """Where source time `t` lands in the output (more than once if footage repeats, e.g. a teaser)."""
    out, offset = [], 0.0
    for pieces in kept:
        for a, b in pieces:
            if a <= t < b:
                out.append(offset + t - a)
            offset += b - a
    return out


def _esc(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _relabel(graph: str, i: int) -> str:
    """Makes one segment's reframe filter use input `i` and labels that cannot clash."""
    graph = graph.replace("[0:v]", f"[{i}:v]")
    return re.sub(r"\[(a|b|bg|fg|top|bot|v)\]", lambda m: f"[{m.group(1)}{i}]", graph)


def render_clip(src: str | Path, segs: list[tuple[float, float]], words: list[dict],
                out: Path, campaign: Campaign, profile: dict | None = None,
                with_captions: bool = True, headline: str = "", zooms: list[float] | None = None,
                spoiler: bool = False, style: dict | None = None) -> dict:
    """`segs` are source-time segments played in order (one for a plain clip, several for a
    supercut or a cold open)."""
    s = get_settings()
    style = style or {}
    fmt = style.get("fmt", "reaction")
    if fmt in ("reveal", "montage") and (profile or {}).get("layout") == "facecam_top":
        profile = {**profile, "layout": "gameplay_pip"}      # the game is the star
    info = media.probe(src)
    fps = 60 if info["fps"] > 45 else 30
    ed = campaign.editing
    out.parent.mkdir(parents=True, exist_ok=True)

    kept = edit.keep_intervals(words, segs, ed.tighten, fps)
    inputs: list[str] = []
    chain: list[str] = []
    layouts: list[str] = []
    offsets: list[float] = []
    total = 0.0
    for i, (a, b) in enumerate(segs):
        frames = edit.seg_frames((a, b), fps)
        dur = frames / fps
        inputs += ["-ss", f"{a:.3f}", "-t", f"{dur + 0.5:.3f}", "-i", str(src)]
        vf, layout = reframe.build_filter(src, a, b, info["width"], info["height"], profile)
        layouts.append(layout)
        chain.append(_relabel(vf, i))
        chain.append(f"[v{i}]fps={fps},trim=end_frame={frames},setpts=PTS-STARTPTS[f{i}]")
        chain.append(f"[{i}:a]aresample=44100,apad,atrim=duration={dur:.5f},asetpts=PTS-STARTPTS[s{i}]")
        offsets.append(total)
        total += dur

    n = len(segs)
    if n > 1:
        chain.append("".join(f"[f{i}][s{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")
        vlast, alast = "cv", "ca"
    else:
        vlast, alast = "f0", "s0"

    # Jump cuts: keep only the worthwhile pieces of the stitched timeline.
    pieces = [(offsets[i] + (ps - segs[i][0]), offsets[i] + (pe - segs[i][0]))
              for i, ps_list in enumerate(kept) for ps, pe in ps_list]
    kept_total = sum(e - a for a, e in pieces)
    # never let pause-cutting push a clip under the campaign's minimum length
    tightened = (kept_total < total - 0.2 and len(pieces) > 0
                 and kept_total >= campaign.clip.min_seconds)
    if tightened:
        eps = 0.5 / fps
        expr = "+".join(f"gte(t,{a - eps:.4f})*lt(t,{e - eps:.4f})" for a, e in pieces)
        chain.append(f"[{vlast}]select='{expr}',setpts=N/{fps}/TB[tv]")
        k = len(pieces)
        chain.append(f"[{alast}]asplit={k}" + "".join(f"[x{j}]" for j in range(k)))
        for j, (a, e) in enumerate(pieces):
            chain.append(f"[x{j}]atrim=start={a:.5f}:end={e:.5f},asetpts=PTS-STARTPTS[y{j}]")
        chain.append("".join(f"[y{j}]" for j in range(k)) + f"concat=n={k}:v=0:a=1[ta]")
        vlast, alast = "tv", "ta"
    else:
        kept = [[seg] for seg in segs]

    out_words, out_dur = edit.remap_words(words, kept)

    # Punch-in zooms on peak reactions, mapped from source time to the output timeline.
    zt = [o for z in (zooms or []) for o in _out_times(z, kept)]
    if style.get("zoom") == "push" and ed.zoom > 0:
        # slow push-in across the whole clip: lets a reveal breathe
        z_expr = f"1+{ed.zoom * 0.8}*on/{max(1, int(total * fps))}"
        chain.append(f"[{vlast}]zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                     f":d=1:s={reframe.OUT_W}x{reframe.OUT_H}:fps={fps}[zv]")
        vlast = "zv"
    elif zt and ed.zoom > 0 and style.get("zoom", "punch") == "punch":
        a_in, hold, a_out = 0.12, 0.8, 0.25
        bumps = "+".join(
            f"{ed.zoom}*clip((T-{t - a_in:.3f})/{a_in},0,1)*clip(({t + hold:.3f}-T)/{a_out},0,1)" for t in zt)
        z_expr = f"1+{bumps}".replace("T", f"(on/{fps})")
        chain.append(f"[{vlast}]zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                     f":d=1:s={reframe.OUT_W}x{reframe.OUT_H}:fps={fps}[zv]")
        vlast = "zv"

    # On-screen text the campaign asks for: credit, paid label, spoiler warning, end card.
    facecam = "facecam_top" in layouts
    pip = "gameplay_pip" in layouts
    extra: list[dict] = []
    if ed.credit:
        extra.append(captions.overlay("Credit", ed.credit, 0, out_dur, 50, 230))
    if ed.disclosure:
        extra.append(captions.overlay("Label", ed.disclosure, 0, out_dur, 1030, 230))
    # positions: facecam layout = title on the panel seam, captions over the gameplay;
    # face-tracked / full-frame = title at the top, captions in the lower third, clear of the face
    warn_y, cta_y = (1000, 1060) if facecam else (560, 1120)   # end card sits above the captions
    if spoiler and ed.spoiler_warning:
        extra.append(captions.overlay("Warn", "SPOILER WARNING", 0, min(2.0, out_dur), 540, warn_y))
    brand = campaigns.load_brand(campaign.page)
    end_from = max(0.0, out_dur - 2.8)
    if ed.cta and out_dur > 6:
        extra.append(captions.overlay("Cta", ed.cta, end_from, out_dur, 540, cta_y, fade=150, width=26))
    if brand.follow and out_dur > 6:
        # the page's own sign-off, under the campaign's call to action
        extra.append(captions.overlay("Follow", brand.follow, end_from, out_dur, 540,
                                      cta_y + (150 if ed.cta else 0), fade=150, width=34))
    title_style = style.get("title", "box")
    show_headline = bool(headline) and ed.headline.enabled and title_style != "none"
    if (with_captions and out_words) or show_headline or extra:
        # facecam layout: title on the seam between the two panels, captions over the gameplay
        margin = 470 if facecam else 400
        head = reframe.OUT_H - reframe.FACECAM_H - 45 if facecam else reframe.OUT_H - 470
        if pip:
            head = 700                          # reveal title low and short, the picture leads
        seed = int(style.get("seed", 0))
        ass = captions.write_ass(out_words if with_captions else [], 0.0, out_dur,
                                 out.with_suffix(".ass"), font=s.caption_font, margin_v=margin,
                                 headline=headline if show_headline else "", head_margin=head,
                                 overlays=extra,
                                 head_style="HeadPlain" if title_style == "plain" or pip else "Head",
                                 highlight=captions.ass_colour(brand.highlight[seed % len(brand.highlight)],
                                                              inline=True),
                                 colours={"head_text": captions.ass_colour(brand.title_text),
                                          "head_box": captions.ass_colour(brand.title_box),
                                          "end_text": captions.ass_colour(brand.end_text),
                                          "end_box": captions.ass_colour(brand.end_box)},
                                 # a reveal's title shows for the first seconds only
                                 headline_until=min(3.5, out_dur) if fmt == "reveal" else None)
        chain.append(f"[{vlast}]ass=filename='{_esc(ass)}'[c]")
        vlast = "c"

    wm = campaign.overlays.watermark
    wm_applied = False
    if wm and wm.image and "watermark" not in campaign.forbid:
        wm_path = ROOT / wm.image
        if not wm_path.exists():
            raise FileNotFoundError(f"Watermark image missing: {wm_path}")
        inputs += ["-i", str(wm_path)]
        pos = WM_POS.get(wm.position, WM_POS["top_right"])
        chain.append(f"[{n}:v]format=rgba,colorchannelmixer=aa={wm.opacity},scale={wm.width}:-1[wm]")
        chain.append(f"[{vlast}][wm]overlay={pos}[o]")
        vlast, wm_applied = "o", True

    media.run([s.ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chain),
               "-map", f"[{vlast}]", "-map", f"[{alast}]", "-c:v", "libx264", "-preset", "fast",
               "-crf", "18", "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    return {"layout": "+".join(dict.fromkeys(layouts)), "watermark": wm_applied,
            "captions": with_captions and bool(out_words), "headline": show_headline,
            "parts": n, "cut_seconds": round(total - kept_total, 1) if tightened else 0.0,
            "duration": media.probe(out)["duration"]}
