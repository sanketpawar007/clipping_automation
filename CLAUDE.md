# Viral Clipper: notes for Claude

- The full plan and every decision so far are in **PLAN.md**. Read it first. Don't re-open decided items unless the user asks.
- The user is new to clipping and coding pipelines. Give a short plain-language explanation of each new concept or step.
- Build order: Phase 0 → Phase 1 (VOD door + clip core + scanner + rules + review + Telegram) → Phase 2 (live door on a VM) → Phase 3 (auto-post, tracking).
- **Main focus is GTA VI** (launches 19 Nov 2026). Ship Phase 1 fast so GTA streamer VODs can be clipped during the launch hype.
- Campaign rules live in `campaigns/*.yaml` and conflict between campaigns. Never hard-code them.
- Content Rewards has no submit API, and campaigns need phone-posting, a 30-minute submit window and analytics screen recordings. Automate up to "ready-to-post on the phone"; the human posts and submits.
- Only clip content the campaign permits. No stolen clips.
- Secrets go in `.env` (git-ignored). Never ask the user to paste keys or passwords into chat. Set spending caps.
- Installed ffmpeg 8.1 lacks libass, so use `ffmpeg-full`. Use Python 3.12 via uv.
- Scrape contentrewards.com pages politely (about hourly). Never call its `/api/` (robots.txt disallows it).
- TikTok is banned in India. Keep it as an optional toggle. Don't automate VPN workarounds.

## Commands (Phase 1 build)
- `make doctor`: check tools and keys. `make test`, `make lint`.
- `make scan` / `make scan-loop`: Content Rewards board scan (hourly loop). `uv run python -m app.cli campaigns` lists stored campaigns.
- `uv run python -m app.cli clip <url> -c <campaign>`: VOD to clips (`app/pipeline.py`). Output and caches live in `data/videos/<id>/`.
- `make review`: dashboard at http://127.0.0.1:8010 (approve sends to Telegram).
- Without `ANTHROPIC_API_KEY` the pipeline ranks on signals only; without `ASSEMBLYAI_API_KEY` it uses local Whisper (slow, no speakers).
- Face tracking uses OpenCV YuNet (`assets/models/`), not MediaPipe: MediaPipe's current release needs a separate model download anyway and pulled in heavy dependencies.

## Live clipping (Phase 2, built 18 Nov 2026)
- `uv run python -m app.cli live -c <campaign>` waits for the campaign's first `sources` channel to go live, records 10s segments (`app/live/recorder.py`), listens to Twitch/Kick chat (`app/live/chat.py`), cuts on chat/audio spikes and every `live.sweep_minutes` (`app/live/session.py`), and sends clips to Telegram.
- On the VM: `sudo systemctl enable --now clipper-live@lacy_gta` (one unit per campaign). Logs: `journalctl -u clipper-live@lacy_gta -f`.
- Long VODs: `clip <url> -c <campaign> --start 2:00:00 --end 5:00:00` (4 hours max per run).
- Gaming campaigns without a `profile:` detect facecam/IRL/gameplay layout automatically.
