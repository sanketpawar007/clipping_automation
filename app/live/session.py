"""One live session: record the stream, listen to chat, cut clips on spikes and on a timer.

Timeline: chat messages carry wall-clock times; the recording has its own clock that starts
when recording starts. Both see the stream with a similar few-second delay, so
recording time = wall time - recording start."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from rich.console import Console

from app import db, pipeline
from app.config import get_settings
from app.core import media
from app.live import chat as chatmod
from app.live import detector
from app.live.recorder import Recorder, is_live
from app.notify import telegram
from app.rules import campaign as campaigns

console = Console()


class Session:
    def __init__(self, slug: str, url: str | None = None):
        self.camp = campaigns.load(slug)
        self.slug = slug
        self.url = url or self.camp.sources[0]
        self.cfg = self.camp.live
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.dir = get_settings().data_dir / "live" / slug / stamp
        self._clear_old_sessions()
        self.rec = Recorder(self.url, self.dir / "segments", self.cfg.quality)
        self.chat = chatmod.ChatListener(self.url) if self.cfg.chat else None
        self.loud: list[float] = []
        self.loud_done: set[str] = set()
        self.last_cut = -1e9
        self.last_sweep = 0.0
        self.made: list[float] = []          # wall times of clips made, for the hourly cap
        self.profile: dict | None = None
        self.busy = threading.Lock()

    # ---- helpers ----
    def _clear_old_sessions(self) -> None:
        """Raw footage of earlier sessions is never used again; without this every restart left
        gigabytes behind and filled the disk."""
        import shutil
        for old in self.dir.parent.glob("*/"):
            if old != self.dir:
                shutil.rmtree(old, ignore_errors=True)

    def log(self, msg: str) -> None:
        console.print(f"[dim]{time.strftime('%H:%M:%S')}[/] [{self.slug}] {msg}", highlight=False)

    def rec_now(self) -> float:
        return time.time() - self.rec.wall_start

    def _update_loudness(self) -> None:
        for p, _, _ in self.rec.segments():
            if p.name not in self.loud_done:
                try:
                    self.loud.extend(float(x) for x in media.loudness_per_second(p))
                except Exception:  # noqa: BLE001 - a half-written segment; try next time
                    continue
                self.loud_done.add(p.name)
        self.loud = self.loud[-3600:]

    def _under_cap(self) -> bool:
        hour_ago = time.time() - 3600
        self.made = [t for t in self.made if t > hour_ago]
        return len(self.made) < self.cfg.max_clips_per_hour

    def _detect_layout(self) -> None:
        """Works out facecam/IRL/gameplay once, from the first minutes of the stream."""
        got = self.rec.window(max(0, self.rec.recorded_until() - 180), self.rec.recorded_until(),
                              self.dir / "layout_probe.mp4")
        if not got:
            return
        from app.core import reframe
        info = media.probe(got[0])
        self.profile = (campaigns.load_profile(self.camp.profile) if self.camp.profile else
                        reframe.detect_stream_layout(got[0], info["duration"], info["width"], info["height"]))
        self.log(f"layout: {self.profile.get('layout')} ({self.profile.get('detected', 'profile file')})")
        got[0].unlink(missing_ok=True)

    # ---- clipping ----
    def cut(self, a: float, b: float, reason: str, n: int, chat_from_wall: float) -> None:
        """Waits for footage up to `b`, then runs the clip pipeline on [a, b] in the background."""
        def work() -> None:
            with self.busy:
                deadline = time.time() + 120
                while self.rec.recorded_until() < b and time.time() < deadline and self.rec.alive():
                    time.sleep(2)
                name = f"w{int(a)}-{int(b)}"
                got = self.rec.window(a, b, self.dir / f"{name}.mp4")
                if not got:
                    self.log(f"[yellow]{reason}: footage not available, skipped[/]")
                    return
                path, base = got
                chat_msgs = self.chat.between(self.rec.wall_start + base, self.rec.wall_start + b + 5) \
                    if self.chat else []
                chat_times = [t - self.rec.wall_start - base for t, _, _ in chat_msgs]
                try:
                    ids = pipeline.run_local(
                        path, self.slug,
                        title=f"{self.camp.streamer or self.slug} live {time.strftime('%d %b %H:%M')}",
                        stream_url=self.url, stream_offset=base, n_clips=n, profile=self.profile,
                        chat_times=chat_times, chat_lines=[x for _, _, x in chat_msgs][-150:])
                except Exception as exc:  # noqa: BLE001 - one failed clip must not stop the session
                    self.log(f"[red]{reason}: clip failed: {exc}[/]")
                    return
                finally:
                    path.unlink(missing_ok=True)
                try:
                    from app.core import montage
                    ids += montage.try_build(self.camp)
                except Exception as exc:  # noqa: BLE001 - a failed montage must not stop the session
                    self.log(f"[yellow]montage failed: {exc}[/]")
                self.made.extend(time.time() for _ in ids)
                self.log(f"[green]{reason}: {len(ids)} clip(s) ready: {ids}[/]")
                if self.cfg.send_to_phone:
                    pipeline.send_live_clips(ids)
        threading.Thread(target=work, daemon=True).start()

    def tick(self) -> None:
        now_w, now_r = time.time(), self.rec_now()
        self._update_loudness()
        if self.profile is None and self.rec.recorded_until() > 200:
            self._detect_layout()
        if self.profile is None or not self._under_cap():
            return
        spike = detector.chat_spike(list(self.chat.messages), now_w) if self.chat else {"z": 0, "messages": 0}
        loud = detector.audio_spike(self.loud)
        hit = ((spike["z"] >= self.cfg.chat_z and spike["messages"] >= self.cfg.min_messages)
               or loud >= self.cfg.audio_db)
        if hit and now_r - self.last_cut > self.cfg.cooldown_s:
            self.last_cut = now_r
            why = (f"chat spike z={spike['z']} ({spike['messages']} msgs, {', '.join(spike.get('top', []))})"
                   if spike["z"] >= self.cfg.chat_z else f"audio spike +{loud} dB")
            self.log(why)
            self.cut(max(0, now_r - self.cfg.before_s), now_r + self.cfg.after_s, why, 1, now_w)
        elif self.cfg.sweep_minutes and now_r - self.last_sweep > self.cfg.sweep_minutes * 60 and \
                now_r > self.cfg.sweep_minutes * 60:
            # quotes and stories do not always make chat explode: review the last stretch too
            self.last_sweep = now_r
            self.log(f"sweep of the last {self.cfg.sweep_minutes} min")
            self.cut(now_r - self.cfg.sweep_minutes * 60, now_r, "sweep", self.cfg.sweep_clips, now_w)

    def run(self) -> None:
        self.log(f"recording {self.url}")
        self.rec.start()
        if self.chat and self.chat.start():
            self.log("chat listener started")
        telegram.send_text(f"🔴 {self.camp.streamer or self.slug} is live. Live clipping started.")
        last_progress, last_seen = time.time(), 0.0
        try:
            while True:
                time.sleep(5)
                got = self.rec.recorded_until()
                if got > last_seen:
                    last_progress, last_seen = time.time(), got
                elif time.time() - last_progress > 120:
                    # the recorder is running but nothing new arrives (seen with YouTube live)
                    self.log("[yellow]no new footage for 2 minutes: stopping this recording[/]")
                    break
                if not self.rec.alive():
                    # the stream dropped or ended; watch() starts a fresh session if it is back
                    self.log("[yellow]recording stopped[/]")
                    break
                self.tick()
                self.rec.cleanup(self.cfg.keep_minutes * 60)
        finally:
            self.rec.stop()
            if self.chat:
                self.chat.stop()
            self.log("stream ended")
            telegram.send_text(f"⚫ {self.camp.streamer or self.slug} stream ended. Live clipping stopped.")


def youtube_vod_pass(slug: str, video_id: str, chunk_s: int = 4 * 3600) -> None:
    """Fallback for YouTube, where live recording from a server is unreliable: once the stream
    has ended, clip the saved broadcast in chunks."""
    from app.ingest.vod import MAX_SPAN_S
    url = f"https://www.youtube.com/watch?v={video_id}"
    console.print(f"[{slug}] stream ended: clipping the saved broadcast {url}")
    telegram.send_text(f"⚫ Stream ended. Clipping the saved broadcast now: {url}")
    import yt_dlp

    from app.live.recorder import _cookie_copy
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "cookiefile": _cookie_copy()}) as y:
        duration = float(y.extract_info(url, download=False).get("duration") or 0)
    start = 0.0
    while start < duration:
        end = min(duration, start + min(chunk_s, MAX_SPAN_S))
        try:
            ids = pipeline.run(url, slug, start=start if start or end < duration else None,
                               end=end if start or end < duration else None)
            pipeline.send_live_clips(ids)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red][{slug}] VOD chunk {start:.0f}-{end:.0f} failed: {exc}[/]")
        start = end


def watch(slug: str, url: str | None = None, poll_s: int = 60) -> None:
    """Waits for the streamer to go live, clips the whole stream, then waits again."""
    camp = campaigns.load(slug)
    url = url or camp.sources[0]
    console.print(f"Watching {url} for {camp.name}. Checks every {poll_s}s.")
    if camp.live.not_before:
        from datetime import datetime
        opens = datetime.fromisoformat(camp.live.not_before).timestamp()
        if time.time() < opens:
            console.print(f"Footage before {camp.live.not_before} is not eligible: waiting "
                          f"{(opens - time.time()) / 3600:.1f} h.")
            while time.time() < opens:
                time.sleep(min(300, max(1, opens - time.time())))
    from app.live.recorder import YouTubeBlocked
    last_alert = 0.0
    while True:
        try:
            live = is_live(url)
        except YouTubeBlocked:
            live = False
            if time.time() - last_alert > 2 * 3600:
                last_alert = time.time()
                console.print("[red]YouTube rejected the cookies: re-export cookies.txt[/]")
                telegram.send_text(
                    f"⚠️ {camp.name}: YouTube is blocking the server again, so this stream cannot be "
                    "watched. Re-export cookies.txt (incognito, spare Google account, "
                    "youtube.com/robots.txt, 'Get cookies.txt LOCALLY') and send it to Claude.")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]live check failed: {exc}[/]")
            live = False
        if live:
            youtube = "youtube.com" in url or "youtu.be" in url
            vid = None
            if youtube:
                from app.live.recorder import youtube_video_id
                vid = youtube_video_id(url)
            session = Session(slug, url)
            session.run()
            if youtube and vid and session.rec.recorded_until() < 600:
                # live recording failed early: wait for the broadcast to end, then use its VOD
                while is_live(url):
                    time.sleep(poll_s * 5)
                youtube_vod_pass(slug, vid)
            continue                     # check again at once: it may have been a short drop
        time.sleep(poll_s)


__all__ = ["Session", "watch", "Path", "db"]
