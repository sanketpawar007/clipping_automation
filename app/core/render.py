"""Cuts one clip: reframe to 9:16, burn captions, add campaign overlays, encode."""
from __future__ import annotations

from pathlib import Path

from app.config import ROOT, get_settings
from app.core import captions, media, reframe
from app.rules.campaign import Campaign

WM_WIDTH = 460
WM_POS = {
    "top_right": "W-w-40:150", "top_left": "40:150",
    "bottom_right": "W-w-40:H-h-420", "bottom_left": "40:H-h-420",
    "top_center": "(W-w)/2:150",
}


def _esc(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_clip(src: str | Path, start: float, end: float, words: list[dict], out: Path,
                campaign: Campaign, profile: dict | None = None, with_captions: bool = True) -> dict:
    s = get_settings()
    info = media.probe(src)
    vf, layout = reframe.build_filter(src, start, end, info["width"], info["height"], profile)
    chain, last = [vf], "v"
    inputs = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src)]
    out.parent.mkdir(parents=True, exist_ok=True)

    if with_captions and words:
        # with facecam on top, captions sit on the seam between the two panels
        margin = OUT_SEAM if layout == "facecam_top" else 560
        ass = captions.write_ass(words, start, end, out.with_suffix(".ass"), margin_v=margin)
        chain.append(f"[{last}]ass=filename='{_esc(ass)}'[c]")
        last = "c"

    wm = campaign.overlays.watermark
    wm_applied = False
    if wm and wm.image and "watermark" not in campaign.forbid:
        wm_path = ROOT / wm.image
        if not wm_path.exists():
            raise FileNotFoundError(f"Watermark image missing: {wm_path}")
        inputs += ["-i", str(wm_path)]
        pos = WM_POS.get(wm.position, WM_POS["top_right"])
        chain.append(f"[1:v]format=rgba,colorchannelmixer=aa={wm.opacity},scale={WM_WIDTH}:-1[wm]")
        chain.append(f"[{last}][wm]overlay={pos}[o]")
        last, wm_applied = "o", True

    out.parent.mkdir(parents=True, exist_ok=True)
    media.run([s.ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chain),
               "-map", f"[{last}]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "21", "-maxrate", "5M", "-bufsize", "10M", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", str(out)])
    return {"layout": layout, "watermark": wm_applied, "captions": with_captions and bool(words),
            "duration": media.probe(out)["duration"]}


OUT_SEAM = reframe.OUT_H - 760 - 60
