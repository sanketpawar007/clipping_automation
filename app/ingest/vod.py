"""VOD door: download a video and collect its audience signals (heatmap, comments)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yt_dlp

from app.config import get_settings

_TS = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")


def comment_timestamps(comments: list[dict], duration: float) -> list[dict]:
    """Finds "12:34" style timestamps viewers wrote in comments. Likes make a mention count more."""
    out = []
    for c in comments or []:
        text = c.get("text") or ""
        for h, m, s in _TS.findall(text):
            t = int(h or 0) * 3600 + int(m) * 60 + int(s)
            if 0 < t < duration:
                out.append({"t": t, "likes": int(c.get("like_count") or 0), "text": text[:200]})
    return out


def video_dir(video_id: str) -> Path:
    d = get_settings().data_dir / "videos" / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def ingest(url: str, max_height: int = 1080, max_comments: int = 300) -> dict:
    """Downloads the video (once) and returns its metadata. Results are cached on disk."""
    s = get_settings()
    probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(probe_opts) as y:
        vid = y.extract_info(url, download=False)["id"]
    d = video_dir(vid)
    meta_path, src = d / "meta.json", d / "source.mp4"
    if meta_path.exists() and src.exists():
        return json.loads(meta_path.read_text())

    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/"
                  f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(d / "source.%(ext)s"),
        "ffmpeg_location": str(Path(s.ffmpeg).parent),
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": [str(max_comments), "all", "0", "0"],
                                       "comment_sort": ["top"]}},
    }
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)

    duration = float(info.get("duration") or 0)
    meta = {
        "video_id": vid, "url": info.get("webpage_url") or url, "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": duration, "width": info.get("width"), "height": info.get("height"),
        "view_count": info.get("view_count"),
        "path": str(src),
        # "Most replayed" graph: list of {start_time, end_time, value 0..1}. Missing on small videos.
        "heatmap": info.get("heatmap") or [],
        "comment_timestamps": comment_timestamps(info.get("comments") or [], duration),
        "chapters": [{"start": c.get("start_time"), "title": c.get("title")}
                     for c in (info.get("chapters") or [])],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta
