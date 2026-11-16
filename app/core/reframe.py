"""9:16 reframing. Decides how a wide 16:9 video becomes a tall phone video.

Layouts:
- track:        crop a tall slice that follows the main face (podcasts, talking heads)
- blur:         whole frame centred on a blurred copy of itself (wide shots, no clear face)
- facecam_top:  streamer's facecam on top, gameplay below (set in profiles/<name>.yaml)
- center:       plain centre crop (gameplay only)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from app.config import ROOT, get_settings

OUT_W, OUT_H = 1080, 1920
MODEL = ROOT / "assets/models/face_detection_yunet_2023mar.onnx"
SAMPLE_FPS = 2
DET_W = 640


def _sample_faces(src: str | Path, start: float, end: float, w: int, h: int) -> list[list[tuple]]:
    """Returns, for each sampled frame, a list of (centre_x 0..1, area 0..1) faces."""
    det_h = int(round(DET_W * h / w / 2) * 2)
    p = subprocess.run(
        [get_settings().ffmpeg, "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
         "-i", str(src), "-vf", f"fps={SAMPLE_FPS},scale={DET_W}:{det_h}", "-f", "rawvideo",
         "-pix_fmt", "bgr24", "-"], capture_output=True)
    frame_bytes = DET_W * det_h * 3
    n = len(p.stdout) // frame_bytes
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (DET_W, det_h), 0.7, 0.3, 50)
    out = []
    for i in range(n):
        frame = np.frombuffer(p.stdout, np.uint8, frame_bytes, i * frame_bytes).reshape(det_h, DET_W, 3)
        _, faces = det.detect(frame)
        out.append([((f[0] + f[2] / 2) / DET_W, (f[2] * f[3]) / (DET_W * det_h))
                    for f in (faces if faces is not None else [])])
    return out


def _mid(values: list[float]) -> float:
    """Median that always returns a real sample. A plain median of two people's positions
    would average them and point the crop at the empty space between."""
    return float(np.percentile(values, 50, method="nearest"))


def _segments(samples: list[list[tuple]], jump: float = 0.12) -> list[tuple[float, float]]:
    """Turns per-frame faces into [(time, centre_x)] holds. The crop stays still while the same
    person is on screen and cuts (rather than slides) when the camera or speaker changes."""
    targets: list[float | None] = []
    prev: float | None = None
    for faces in samples:
        if not faces:
            targets.append(prev)
            continue
        biggest = max(a for _, a in faces)
        strong = [x for x, a in faces if a >= 0.6 * biggest]
        # among similarly sized faces, stay with the one we were already on
        x = min(strong, key=lambda v: abs(v - prev)) if prev is not None else \
            max(faces, key=lambda f: f[1])[0]
        targets.append(x)
        prev = x
    first = next((t for t in targets if t is not None), 0.5)
    xs = [first if t is None else t for t in targets]

    segs: list[tuple[int, list[float]]] = [(0, [xs[0]])] if xs else []
    for i in range(1, len(xs)):
        seg_x = _mid(segs[-1][1])
        moved = abs(xs[i] - seg_x) > jump
        confirmed = i + 1 >= len(xs) or abs(xs[i + 1] - seg_x) > jump   # ignore 1-frame glitches
        if moved and confirmed:
            segs.append((i, [xs[i]]))
        else:
            segs[-1][1].append(xs[i] if not moved else seg_x)
    return [(i / SAMPLE_FPS, _mid(v)) for i, v in segs]


def _crop_x_expr(segs: list[tuple[float, float]], w: int, crop_w: int) -> str:
    def px(cx: float) -> int:
        return int(np.clip(cx * w - crop_w / 2, 0, w - crop_w))
    expr = str(px(segs[-1][1]))
    for (_, cx), (t_next, _) in zip(reversed(segs[:-1]), reversed(segs[1:])):
        expr = f"if(lt(t\\,{t_next:.2f})\\,{px(cx)}\\,{expr})"
    return expr


def build_filter(src: str | Path, start: float, end: float, w: int, h: int,
                 profile: dict | None = None) -> tuple[str, str]:
    """Returns (ffmpeg filter producing [v] from [0:v], layout name)."""
    profile = profile or {}
    layout = profile.get("layout", "auto")

    if layout == "facecam_top" and profile.get("facecam"):
        f = profile["facecam"]
        top_h = int(profile.get("facecam_height", 760))
        bot_h = OUT_H - top_h
        fx, fy, fw, fh = (int(f["x"] * w), int(f["y"] * h), int(f["w"] * w), int(f["h"] * h))
        gw = int(min(w, h * OUT_W / bot_h) // 2 * 2)
        gx = int(np.clip(profile.get("gameplay_focus_x", 0.5) * w - gw / 2, 0, w - gw))
        return (f"[0:v]split[a][b];"
                f"[a]crop={fw}:{fh}:{fx}:{fy},scale={OUT_W}:{top_h}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{top_h}[top];"
                f"[b]crop={gw}:{h}:{gx}:0,scale={OUT_W}:{bot_h}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{bot_h}[bot];[top][bot]vstack,setsar=1[v]"), "facecam_top"

    crop_w = int(h * OUT_W / OUT_H // 2 * 2)
    if layout == "center":
        return f"[0:v]crop={crop_w}:{h}:{(w - crop_w) // 2}:0,scale={OUT_W}:{OUT_H},setsar=1[v]", "center"

    if layout in ("auto", "track"):
        samples = _sample_faces(src, start, end, w, h)
        seen = sum(1 for s in samples if s) / max(1, len(samples))
        if seen >= 0.35:
            x = _crop_x_expr(_segments(samples), w, crop_w)
            return f"[0:v]crop={crop_w}:{h}:{x}:0,scale={OUT_W}:{OUT_H},setsar=1[v]", "track"

    return (f"[0:v]split[a][b];"
            f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
            f"boxblur=24:4,eq=brightness=-0.08[bg];"
            f"[b]scale={OUT_W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"), "blur"
