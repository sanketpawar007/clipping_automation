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


def _offline_id(url: str) -> str | None:
    """Works out the video id from the URL alone, without contacting the site."""
    for ie in yt_dlp.extractor.gen_extractor_classes():
        if ie.IE_NAME != "generic" and ie.suitable(url):
            try:
                return ie.get_temp_id(url)
            except Exception:  # noqa: BLE001
                return None
    return None


MAX_SPAN_S = 4 * 3600      # longest stretch downloaded in one go (1080p60 is about 2.7 GB/hour)


def parse_time(text: str | None) -> float | None:
    """'1:23:45', '83:45', '5025' or '2h' -> seconds."""
    if not text:
        return None
    text = text.strip().lower()
    if text.endswith("h"):
        return float(text[:-1]) * 3600
    if text.endswith("m"):
        return float(text[:-1]) * 60
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def _range_tag(start: float | None, end: float | None) -> str:
    return "" if start is None and end is None else f"_{int(start or 0)}-{int(end or 0)}"


def ingest(url: str, max_height: int = 1080, max_comments: int = 300,
           start: float | None = None, end: float | None = None) -> dict:
    """Downloads the video (once) and returns its metadata. Results are cached on disk.

    `start`/`end` (seconds) download only part of a long video, such as a 10-hour stream.
    All times after that are relative to the downloaded part; `offset` in the metadata says
    where it begins in the original."""
    s = get_settings()
    base: dict = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if s.ytdlp_cookies_file:
        cookies = Path(s.ytdlp_cookies_file).expanduser()
        if not cookies.exists():
            raise FileNotFoundError(f"YTDLP_COOKIES_FILE points to a missing file: {cookies}")
        base["cookiefile"] = str(cookies)
    # Already downloaded? Then do not contact the site at all.
    known = _offline_id(url)
    tag = _range_tag(start, end)
    if known:
        cached = get_settings().data_dir / "videos" / f"{known}{tag}"
        if (cached / "meta.json").exists() and (cached / "source.mp4").exists():
            return json.loads((cached / "meta.json").read_text())
    probe_opts = {**base, "skip_download": True}
    with yt_dlp.YoutubeDL(probe_opts) as y:
        probe = y.extract_info(url, download=False)
    vid = probe["id"]
    full = float(probe.get("duration") or 0)
    lo = start or 0.0
    hi = min(end, full) if end and full else (end or full)
    if full and hi - lo > MAX_SPAN_S:
        raise ValueError(
            f"This video is {full / 3600:.1f} hours long. Pick at most {MAX_SPAN_S // 3600} hours "
            f"with --start and --end, for example --start 2:00:00 --end 5:00:00.")
    d = video_dir(f"{vid}{tag}")
    meta_path, src = d / "meta.json", d / "source.mp4"
    if meta_path.exists() and src.exists():
        return json.loads(meta_path.read_text())

    opts = {
        **base,
        "format": f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/"
                  f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(d / "source.%(ext)s"),
        "ffmpeg_location": str(Path(s.ffmpeg).parent),
        "getcomments": not tag,
        "extractor_args": {"youtube": {"max_comments": [str(max_comments), "all", "0", "0"],
                                       "comment_sort": ["top"]}},
    }
    if tag:
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(lo, hi)])
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    if not src.exists():
        raise RuntimeError("Download finished but no source.mp4 was written.")

    from app.core import media  # local import: media imports config, keep ingest light
    duration = media.probe(src)["duration"] if tag else float(info.get("duration") or 0)
    meta = {
        "video_id": f"{vid}{tag}", "url": f"{info.get('webpage_url') or url}{'#' + tag if tag else ''}",
        "title": info.get("title"), "offset": lo if tag else 0.0,
        "channel": info.get("channel") or info.get("uploader"),
        "duration": duration, "width": info.get("width"), "height": info.get("height"),
        "view_count": info.get("view_count"),
        "path": str(src),
        # "Most replayed" graph: list of {start_time, end_time, value 0..1}. Missing on small videos.
        "heatmap": [] if tag else (info.get("heatmap") or []),
        "comment_timestamps": comment_timestamps(info.get("comments") or [], duration),
        "chapters": [{"start": c.get("start_time"), "title": c.get("title")}
                     for c in (info.get("chapters") or [])],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta
