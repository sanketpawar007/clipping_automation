"""Records a live stream as short segments, so any recent moment can be cut out within seconds.

streamlink (Twitch, Kick) or yt-dlp (YouTube, with cookies) writes the stream to stdout and
ffmpeg splits it into 10-second .ts files without re-encoding. ffmpeg's segment list records
where each piece starts and ends on the recording's clock."""
from __future__ import annotations

import csv
import shutil
import subprocess
import time
from pathlib import Path

from app.config import get_settings

SEG_S = 10


class YouTubeBlocked(RuntimeError):
    """YouTube answered "Sign in to confirm you're not a bot": the cookies need re-exporting."""


def _cookie_copy() -> str | None:
    """A private copy of cookies.txt per call: yt-dlp rewrites its cookie file, and several
    processes sharing one file can corrupt it or rotate it into an invalid state."""
    import tempfile
    s = get_settings()
    if not s.ytdlp_cookies_file or not Path(s.ytdlp_cookies_file).exists():
        return None
    fd, tmp = tempfile.mkstemp(prefix="ck_", suffix=".txt")
    import os
    os.close(fd)
    shutil.copyfile(s.ytdlp_cookies_file, tmp)
    return tmp


def _die_with_parent() -> None:
    """Linux: ask the kernel to stop this child if the clipper itself dies, so a crash never
    leaves a recorder running (and filling the disk) on its own."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)   # 1 = PR_SET_PDEATHSIG
    except OSError:
        pass                                                  # not Linux (e.g. a Mac): skip


class Recorder:
    def __init__(self, url: str, outdir: Path, quality: str = "1080p60,1080p,720p60,720p,best"):
        self.url, self.outdir, self.quality = url, outdir, quality
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.src: subprocess.Popen | None = None
        self.mux: subprocess.Popen | None = None
        self.wall_start = 0.0
        self.locked: set[str] = set()

    def _source_cmd(self) -> list[str]:
        if "youtube.com" in self.url or "youtu.be" in self.url:
            cmd = [shutil.which("yt-dlp") or "yt-dlp", "-q", "--no-warnings", "-f",
                   "best[height<=1080]/bv*[height<=1080][vcodec^=avc1]+ba", "--downloader", "ffmpeg",
                   "--hls-use-mpegts", "-o", "-", self.url]
            ck = _cookie_copy()
            if ck:
                cmd[1:1] = ["--cookies", ck]
            return cmd
        return [shutil.which("streamlink") or "streamlink", "--stdout", "--loglevel", "error",
                "--twitch-disable-ads", "--hls-live-edge", "2", self.url, self.quality]

    def start(self) -> None:
        s = get_settings()
        self.src = subprocess.Popen(self._source_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    preexec_fn=_die_with_parent)
        self.mux = subprocess.Popen(
            [s.ffmpeg, "-v", "error", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
             "-f", "segment", "-segment_time", str(SEG_S), "-reset_timestamps", "1",
             "-segment_list", str(self.outdir / "list.csv"), "-segment_list_type", "csv",
             "-segment_list_flags", "+live", str(self.outdir / "seg_%06d.ts")],
            stdin=self.src.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=_die_with_parent)
        self.src.stdout.close()          # ffmpeg owns the pipe now
        self.wall_start = time.time()

    def alive(self) -> bool:
        return bool(self.mux and self.mux.poll() is None and self.src and self.src.poll() is None)

    def stop(self) -> None:
        for p in (self.src, self.mux):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()

    def segments(self) -> list[tuple[Path, float, float]]:
        """(file, start, end) on the recording clock, for every finished segment."""
        lst = self.outdir / "list.csv"
        if not lst.exists():
            return []
        out = []
        with open(lst, newline="") as fh:
            for row in csv.reader(fh):
                if len(row) >= 3 and (self.outdir / row[0]).exists():
                    out.append((self.outdir / row[0], float(row[1]), float(row[2])))
        return out

    def recorded_until(self) -> float:
        segs = self.segments()
        return segs[-1][2] if segs else 0.0

    def window(self, start: float, end: float, out: Path) -> tuple[Path, float] | None:
        """Joins the segments covering [start, end] into one .mp4. Returns (file, its start time
        on the recording clock), or None if that stretch is no longer (or not yet) on disk."""
        picked = [x for x in self.segments() if x[2] > start and x[1] < end]
        if not picked:
            return None
        self.locked.update(p.name for p, _, _ in picked)
        try:
            listing = out.with_suffix(".txt")
            listing.write_text("".join(f"file '{p}'\n" for p, _, _ in picked))
            subprocess.run([get_settings().ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                            "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)],
                           check=True, capture_output=True)
            listing.unlink(missing_ok=True)
        finally:
            self.locked.difference_update(p.name for p, _, _ in picked)
        return out, picked[0][1]

    def cleanup(self, keep_s: float) -> int:
        """Deletes segments older than `keep_s` seconds of recording, keeping disk use flat."""
        cutoff = self.recorded_until() - keep_s
        gone = 0
        for p, _, end in self.segments():
            if end < cutoff and p.name not in self.locked:
                p.unlink(missing_ok=True)
                gone += 1
        return gone


def is_live(url: str) -> bool:
    """True if the channel is streaming right now."""
    if "youtube.com" in url or "youtu.be" in url:
        cmd = [shutil.which("yt-dlp") or "yt-dlp", "-q", "--no-warnings", "--skip-download",
               "--print", "%(is_live)s", url]
        ck = _cookie_copy()
        if ck:
            cmd[1:1] = ["--cookies", ck]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        finally:
            if ck:
                Path(ck).unlink(missing_ok=True)
        if "confirm you" in p.stderr and "not a bot" in p.stderr:
            raise YouTubeBlocked("YouTube wants new cookies")
        return p.stdout.strip().splitlines()[-1:] == ["True"]
    p = subprocess.run([shutil.which("streamlink") or "streamlink", "--json", url],
                       capture_output=True, text=True, timeout=90)
    return '"streams"' in p.stdout and '"error"' not in p.stdout


def youtube_video_id(url: str) -> str | None:
    """The video id of a channel's current live stream (the /live URL redirects to it)."""
    cmd = [shutil.which("yt-dlp") or "yt-dlp", "-q", "--no-warnings", "--skip-download", "--print", "%(id)s",
           url]
    ck = _cookie_copy()
    if ck:
        cmd[1:1] = ["--cookies", ck]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    finally:
        if ck:
            Path(ck).unlink(missing_ok=True)
    out = p.stdout.strip().splitlines()
    return out[-1] if out else None
