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
FACECAM_H = 760          # output pixels given to the facecam panel in the facecam-over-gameplay layout
DET_W = 960


def _sample_faces(src: str | Path, start: float, end: float, w: int, h: int) -> list[list[tuple]]:
    """Returns, for each sampled frame, a list of (centre_x 0..1, area 0..1) faces."""
    det_h = int(round(DET_W * h / w / 2) * 2)
    p = subprocess.run(
        [get_settings().ffmpeg, "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
         "-i", str(src), "-vf", f"fps={SAMPLE_FPS},scale={DET_W}:{det_h}", "-f", "rawvideo",
         "-pix_fmt", "bgr24", "-"], capture_output=True)
    frame_bytes = DET_W * det_h * 3
    n = len(p.stdout) // frame_bytes
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (DET_W, det_h), 0.6, 0.3, 50)
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


def gameplay_window(cam: dict, w: int, h: int, aspect: float) -> tuple[int, int, int, int]:
    """The gameplay part for the lower panel: the largest window of the panel's shape, centred on
    the action (middle of the screen, a little above centre), that does not include the facecam.
    Keeping to the centre also keeps side overlays such as the stream's chat box out."""
    cx0, cy0 = cam["x"] * w, cam["y"] * h
    cx1, cy1 = cx0 + cam["w"] * w, cy0 + cam["h"] * h
    best = None
    for scale in np.linspace(1.0, 0.55, 19):
        gh = int(h * scale) // 2 * 2
        gw = int(min(w, gh * aspect)) // 2 * 2
        gh = int(gw / aspect) // 2 * 2
        for centre_x in (0.5, 0.55, 0.45, 0.6, 0.4):
            gx = int(np.clip(centre_x * w - gw / 2, 0, w - gw))
            gy = int(np.clip(0.47 * h - gh / 2, 0, h - gh))
            overlap_w = max(0.0, min(gx + gw, cx1) - max(gx, cx0))
            overlap_h = max(0.0, min(gy + gh, cy1) - max(gy, cy0))
            if overlap_w * overlap_h <= 0.02 * gw * gh:        # at most a sliver of the cam
                return gx, gy, gw, gh
            if best is None:
                best = (gx, gy, gw, gh)
    return best


def build_filter(src: str | Path, start: float, end: float, w: int, h: int,
                 profile: dict | None = None) -> tuple[str, str]:
    """Returns (ffmpeg filter producing [v] from [0:v], layout name)."""
    profile = profile or {}
    layout = profile.get("layout", "auto")

    if layout == "gameplay_pip" and profile.get("facecam"):
        # reveals: the game fills the screen, the streamer stays as a small inset
        f = profile["facecam"]
        fx, fy, fw, fh = (int(f["x"] * w), int(f["y"] * h), int(f["w"] * w), int(f["h"] * h))
        gw = int(h * OUT_W / OUT_H // 2 * 2)
        gx = int(np.clip(profile.get("gameplay_focus_x", 0.5) * w - gw / 2, 0, w - gw))
        return (f"[0:v]split[a][b];"
                f"[b]crop={gw}:{h}:{gx}:0,scale={OUT_W}:{OUT_H}:flags=lanczos[bg];"
                f"[a]crop={fw}:{fh}:{fx}:{fy},scale=380:-2:flags=lanczos,unsharp=5:5:0.6:5:5:0[fg];"
                f"[bg][fg]overlay=40:330,setsar=1[v]"), "gameplay_pip"

    if layout == "facecam_top" and profile.get("facecam"):
        f = profile["facecam"]
        top_h = int(profile.get("facecam_height", FACECAM_H))
        bot_h = OUT_H - top_h
        fx, fy, fw, fh = (int(f["x"] * w), int(f["y"] * h), int(f["w"] * w), int(f["h"] * h))
        gx, gy, gw, gh = gameplay_window(f, w, h, OUT_W / bot_h)
        return (f"[0:v]split[a][b];"
                f"[a]crop={fw}:{fh}:{fx}:{fy},scale={OUT_W}:{top_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"unsharp=5:5:0.7:5:5:0,"
                f"crop={OUT_W}:{top_h}[top];"
                f"[b]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{bot_h}:flags=lanczos[bot];"
                f"[top][bot]vstack,setsar=1[v]"), "facecam_top"

    crop_w = int(h * OUT_W / OUT_H // 2 * 2)
    if layout == "center":
        return (f"[0:v]crop={crop_w}:{h}:{(w - crop_w) // 2}:0,"
                f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[v]"), "center"

    if layout in ("auto", "track"):
        samples = _sample_faces(src, start, end, w, h)
        seen = sum(1 for s in samples if s) / max(1, len(samples))
        if seen >= 0.35:
            x = _crop_x_expr(_segments(samples), w, crop_w)
            return f"[0:v]crop={crop_w}:{h}:{x}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[v]", "track"

    return (f"[0:v]split[a][b];"
            f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
            f"boxblur=24:4,eq=brightness=-0.08[bg];"
            f"[b]scale={OUT_W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"), "blur"


def _detect_boxes(src: str | Path, times: list[float], w: int, h: int) -> tuple[list[list[tuple]], list]:
    """Face boxes (x, y, w, h as fractions of the frame) at each of the given times, plus the
    grey frames themselves (used to find the webcam's border)."""
    det_h = int(round(DET_W * h / w / 2) * 2)
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (DET_W, det_h), 0.7, 0.3, 50)
    frame_bytes = DET_W * det_h * 3
    out, greys = [], []
    for t in times:
        p = subprocess.run(
            [get_settings().ffmpeg, "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
             "-vf", f"scale={DET_W}:{det_h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            capture_output=True)
        if len(p.stdout) < frame_bytes:
            out.append([])
            continue
        frame = np.frombuffer(p.stdout, np.uint8, frame_bytes).reshape(det_h, DET_W, 3)
        greys.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        _, faces = det.detect(frame)
        out.append([(f[0] / DET_W, f[1] / det_h, f[2] / DET_W, f[3] / det_h)
                    for f in (faces if faces is not None else [])])
    return out, greys


def _snap_to_border(greys: list, guess: tuple[float, float, float, float],
                    face: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Moves each side of the guessed webcam box onto the webcam overlay's border.

    The border stays put while the game behind it changes, so it shows as a long straight
    line in the median of many frames. Things in the streamer's room (door frames, shelves)
    are static too, so a side is the OUTERMOST line that runs along most of the box, and a
    cam tucked into a corner uses the frame edge itself."""
    if len(greys) < 5:
        return guess
    med = np.median(np.stack(greys[:40]).astype(np.float32), axis=0)
    H, W = med.shape
    ex = np.abs(np.diff(med, axis=1)) > 14       # vertical edge pixels
    ey = np.abs(np.diff(med, axis=0)) > 14       # horizontal edge pixels
    x, y, bw, bh = guess
    fx, fy, fw, fh = face
    fl, fr, ft, fb = int(fx * W), int((fx + fw) * W), int(fy * H), int((fy + fh) * H)

    def side(edges: np.ndarray, along: slice, lo: int, hi: int, outward: int, limit: int) -> int | None:
        """Outermost position in [lo, hi) whose line covers most of `along`; frame edge if the
        search reaches it without a better line."""
        lo, hi = max(0, lo), min(edges.shape[1] if outward else edges.shape[0], hi)
        if hi <= lo:
            return None
        cover = edges[along, lo:hi].mean(axis=0) if edges is ex else edges[lo:hi, along].mean(axis=1)
        # allow a 1px wobble in the line
        cover = np.maximum(cover, np.maximum(np.r_[cover[1:], 0], np.r_[0, cover[:-1]]))
        good = np.where(cover >= 0.6)[0]
        if len(good):
            return lo + int(good.max() if outward > 0 else good.min())
        reach = hi if outward > 0 else lo
        return limit if abs(reach - limit) <= 2 else None

    # top and bottom first, measured across the face's width and a bit more
    cols = slice(max(0, fl - (fr - fl)), min(W - 1, fr + (fr - fl)))
    top = side(ey, cols, ft - int(bh * H), ft - int(fh * H * 0.3), -1, 0)
    bottom = side(ey, cols, fb + int(fh * H * 0.4), fb + int(bh * H), +1, H - 1)
    t = top if top is not None else int(max(0, y) * H)
    btm = bottom if bottom is not None else int(min(1, y + bh) * H)
    rows = slice(t + 2, max(t + 3, btm - 1))
    reach_w = int(bw * W * 1.2)
    left = side(ex, rows, 0 if fx < 0.2 else fl - reach_w, fl - int(fw * W * 0.5), -1, 0)
    right = side(ex, rows, fr + int(fw * W * 0.5), W - 1 if fx + fw > 0.8 else fr + reach_w, +1, W - 1)

    x0 = (left + 1) / W if left is not None else x
    x1 = (right + 1) / W if right is not None else x + bw
    y0 = (top + 1) / H if top is not None else y
    y1 = (bottom + 1) / H if bottom is not None else y + bh
    x0, y0 = max(0.0, x0 if left not in (0,) else 0.0), max(0.0, y0 if top not in (0,) else 0.0)
    x1, y1 = min(1.0, x1), min(1.0, y1)
    if x1 - x0 < fw * 2 or y1 - y0 < fh * 1.5:
        return guess
    return x0, y0, x1 - x0, y1 - y0


def _moving_background(greys: list, face: tuple[float, float, float, float]) -> float:
    """How much the picture around (not on) the face changes over time. A room behind a
    full-screen camera barely changes; gameplay behind a cut-out facecam changes constantly."""
    if len(greys) < 5:
        return 0.0
    stack = np.stack(greys[:40]).astype(np.float32)
    std = stack.std(axis=0)
    H, W = std.shape
    fx, fy, fw, fh = face
    x0, x1 = int(max(0, fx - fw) * W), int(min(1, fx + 2 * fw) * W)
    y0, y1 = int(max(0, fy - 0.6 * fh) * H), int(min(1, fy + 2 * fh) * H)
    mask = np.zeros_like(std, dtype=bool)
    mask[y0:y1, x0:x1] = True
    mask[int(fy * H):int((fy + fh) * H), int(fx * W):int((fx + fw) * W)] = False   # not the face itself
    return float(std[mask].mean()) if mask.any() else 0.0


def _cutout_box(fx: float, fy: float, fw: float, fh: float) -> tuple[float, float, float, float]:
    """Box around a cut-out streamer: head and shoulders, pushed to the nearest corner edges."""
    bw, bh = min(0.42, fw * 3.0), min(0.6, fh * 3.4)
    bx = float(np.clip(fx + fw / 2 - bw / 2, 0, 1 - bw))
    by = float(np.clip(fy - 0.45 * fh, 0, 1 - bh))
    if fx + fw / 2 < 0.35:
        bx = 0.0                                   # cam in the left part: reach the left edge
    elif fx + fw / 2 > 0.65:
        bx = 1 - bw
    if fy + fh / 2 > 0.55:
        bh = min(0.6, 1 - by)                      # cam low in the frame: reach the bottom edge
    return bx, by, bw, bh


def detect_stream_layout(src: str | Path, duration: float, w: int, h: int, samples: int = 40) -> dict:
    """Works out a streamer's layout from the video itself, so no profile file is needed.

    - A small face that stays in the same spot in most frames is a facecam overlay:
      facecam on top, gameplay below.
    - A large face on screen most of the time is an IRL or just-chatting stream: follow the face.
    - Otherwise it is gameplay only: whole frame over a blurred copy.
    Game characters have faces too, but they move around, so they fail the "same spot" test."""
    times = [duration * (i + 0.5) / samples for i in range(samples)]
    frames, greys = _detect_boxes(src, times, w, h)
    small = [b for faces in frames for b in faces if b[2] < 0.22]
    big_share = sum(1 for faces in frames if any(b[2] >= 0.22 for b in faces)) / samples

    if small:
        # the most common small-face position, then how often a face is there
        cx = np.array([b[0] + b[2] / 2 for b in small])
        cy = np.array([b[1] + b[3] / 2 for b in small])
        best = max(range(len(small)),
                   key=lambda i: int(np.sum((np.abs(cx - cx[i]) < 0.04) & (np.abs(cy - cy[i]) < 0.05))))
        near = [b for b, x, y in zip(small, cx, cy)
                if abs(x - cx[best]) < 0.04 and abs(y - cy[best]) < 0.05]
        share = sum(1 for faces in frames
                    if any(abs(b[0] + b[2] / 2 - cx[best]) < 0.04 and abs(b[1] + b[3] / 2 - cy[best]) < 0.05
                           for b in faces)) / samples
        if share >= 0.4:
            fx = float(np.median([b[0] for b in near]))
            fy = float(np.median([b[1] for b in near]))
            fw = float(np.median([b[2] for b in near]))
            fh = float(np.median([b[3] for b in near]))
            # first guess: a 16:9 webcam about 7 face-widths wide, face a little above centre
            bw = min(1.0, fw * 7.5)
            bh = min(1.0, bw * w * 9 / 16 / h)
            bx = float(np.clip(fx + fw / 2 - bw / 2, 0, 1 - bw))
            by = float(np.clip(fy + fh / 2 - bh * 0.45, 0, 1 - bh))
            bx, by, bw, bh = _snap_to_border(greys, (bx, by, bw, bh), (fx, fy, fw, fh))
            if bw >= 0.5 or bh >= 0.6:
                if _moving_background(greys, (fx, fy, fw, fh)) < 12:
                    # a still room around the face: a full-screen camera (just chatting / IRL)
                    return {"layout": "track", "detected": f"full-screen camera ({share:.0%} of frames)"}
                # the game keeps moving around the face: a cut-out (green-screen) facecam with no
                # frame. Take the corner around the person, reaching the nearest frame edges.
                bx, by, bw, bh = _cutout_box(fx, fy, fw, fh)
            face_cx = fx + fw / 2
            # keep the gameplay under the cam rather than the side the cam covers
            focus = 0.5 if 0.3 < face_cx < 0.7 else (0.6 if face_cx < 0.3 else 0.4)
            return {"layout": "facecam_top", "facecam": {"x": bx, "y": by, "w": bw, "h": bh},
                    "gameplay_focus_x": focus, "detected": f"facecam in {share:.0%} of frames"}
    if big_share >= 0.5:
        return {"layout": "track", "detected": f"large face in {big_share:.0%} of frames"}
    return {"layout": "blur", "detected": "no steady facecam: gameplay only"}
