"""Small ffmpeg helpers."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from app.config import get_settings


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed:\n{p.stderr[-1500:]}")


def probe(path: str | Path) -> dict:
    s = get_settings()
    out = subprocess.run(
        [s.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    st = j["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return {"width": int(st["width"]), "height": int(st["height"]),
            "fps": float(num) / float(den or 1), "duration": float(j["format"]["duration"])}


def extract_audio(src: str | Path, dst: Path) -> Path:
    """Mono 16 kHz audio: small to upload and what speech models expect."""
    if not dst.exists():
        run([get_settings().ffmpeg, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1",
             "-ar", "16000", "-c:a", "aac", "-b:a", "48k", str(dst)])
    return dst


def loudness_per_second(src: str | Path) -> np.ndarray:
    """RMS loudness for each second of audio, in dB. Shouting and laughter show up as peaks."""
    rate = 8000
    p = subprocess.run(
        [get_settings().ffmpeg, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
         "-f", "s16le", "-"], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode()[-800:])
    pcm = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(pcm) // rate
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt((pcm[: n * rate].reshape(n, rate) ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-5))
