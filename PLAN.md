# Viral Clipper: Build Plan

_Written 16 Nov 2026 at the end of the planning session. Everything before "Build phases" is decided; don't re-open it unless the user asks._

## 1. Goal

An affordable automation that finds the most viral moments in long videos and live streams and turns them into 9:16 short clips with captions. The clips are posted to YouTube Shorts and Instagram Reels (TikTok later) for **Whop Content Rewards** campaigns, which pay per 1,000 views.

- **Accuracy.** Fuse several audience signals with LLM judgement, then learn from real view data. Don't rely on one prompt.
- **Cost.** About $0.30–0.60 per source hour. For comparison, Klap's API costs about $8 per 10 clips.
- **Modes.** Both **live streams (while live)** and **uploaded VODs**.
- **Main focus:** **GTA VI** (see section 7). The first campaign is Let Him Mansplain.

## 2. What we learned (short)

**Money**
- Content Rewards pays about $0.20–6 per 1,000 views (often around $1). Whop keeps 10%. Payout takes up to 7 days after approval.
- YouTube Shorts revenue is tiny (about $0.04–0.10 per 1,000 views), so it isn't the plan.
- Long-term upside: clipping retainers for podcasters ($200–3,000 a month per client, via Upwork). The campaign portfolio is the proof.

**Whop / Content Rewards**
- **No submit API.** Content Rewards is a third-party Whop app; the Bounties API doesn't cover it. Submitting is a manual one-click step.
- **Discovery can be automated.** The public board https://contentrewards.com/c/discover/clipping has about 100 campaigns embedded as Next.js JSON: title, platforms, budget, payout, requiresApplication.
- **Campaign detail.** The page `/c/campaigns/{uuid}` has `payoutModel` per platform in **cents** (rate 75 means $0.75 per 1,000 views), plus maxPayout, minPayout and top submissions.
- **Scraping rules.** robots.txt allows these pages and disallows `/api/`. Scrape politely, about once an hour.
- **Rules after joining.** Many rules are only visible after joining a campaign (Notion or Google Docs briefs). The user pastes them into the dashboard once per campaign.
- **Rules conflict between campaigns.** LHM *requires* a watermark; Stream House *forbids* one. So rules must be **per-campaign config**, not hard-coded.
- **Common campaign requirements:** a dedicated account per campaign (or a niche theme page), an audience-location rule (for example 40% USA), sometimes TikTok, and a minimum engagement rate.

**Approval and anti-fraud rules (ClipLaunch guides, used by LHM)**
- **Warm-up.** Days 1–2 engagement only. Day 3: one test post **from the phone**. 1,000+ views means the account is healthy; under 200 means start a fresh account.
- **Phone posting.** The **first 10 posts must come from the phone app**, not a scheduler or desktop.
- **Submit fast.** Submit each clip to the campaign **within 30 minutes of posting**.
- **Analytics proof.** After the minimum views, submit a **phone screen recording** of that video's analytics showing viewer locations. This is manual by design.
- **US audience.** The guide recommends one consistent US VPN server, English only, niche hashtags, and posting at 11am–2pm or 5–9pm Eastern time (9:30pm–12:30am or 3:30–7:30am in India).
- **Rejection reasons:** looks like an ad, botting, low-tier-country audience, banned words, missing analytics.

**Tools and platform facts**
- **autoshorts (JayWebtech).** One LLM prompt over a transcript plus a centre crop. A useful reference, not a base to build on.
- **Most Replayed heatmap.** YouTube's heatmap is in `ytInitialData`, but only on videos with roughly 50K+ views.
- **Chat sources.**
  - Twitch: anonymous IRC (`justinfan` login).
  - Kick: public Pusher websocket. This is unofficial, and the chatroom lookup needs a browser-like TLS fingerprint (`curl_cffi`).
  - YouTube live chat: Data API.
- **ffmpeg.** The installed ffmpeg 8.1 has **no libass/drawtext**, so captions can't be burned in. Install `ffmpeg-full`.
- **TikTok.** Banned in India. It's deferred and kept as a toggle. The user decides how to handle it; don't automate VPN workarounds for it.

## 3. Accounts (ready)

All accounts are on Content Rewards user **sanketpc5** (earlier shown as sanket.p7420, so confirm which one is used). There are no passwords in this file.

| Brand | Niche | Gmail | YouTube | Instagram | Status |
|---|---|---|---|---|---|
| **LHM Truth Bombs** | Let Him Mansplain campaign (dedicated) | lhmtruthbombs@gmail.com | @lhmtruthbombs (UCbHFtIHEM4gsX6cH0TR8erA) | @lhmtruthbombs (Creator) | Warm-up. IG bio link still to add from the phone. YouTube phone verification pending |
| **Hype Replay** | Gaming and streamers, **becomes the GTA VI page** | hypereplayhq@gmail.com | @hypereplayhq (UCjcBL0GF-yY64dyyDGcOtrw) | @hypereplayhq (Creator, Gaming video creator) | Ready |
| **Money Cuts** | Business, money, podcasts | moneycutshq@gmail.com | @moneycutshq (UCJp4UJS7KEEuz1G7my-Qp4Q) | @moneycutshq (Creator, Entrepreneur) | Ready. YouTube phone-verified |

Brand images and the generator scripts are in `brand/`.

**VPN:** ProtonVPN, US server. Use the same server every time for each account.

## 4. Campaigns

**Let Him Mansplain** (`705f6e21-38af-408c-9e55-c77e4611bf09`). **Active.**
- **Pay:** Instagram $1 and YouTube $0.50 per 1,000 views (the brief says $1.25). Maximum $300 per clip.
- **Required:**
  - a transparent watermark with the **YouTube logo and @lethimmansplain** for the **whole clip**
  - **tag @lethimmansplain in the first caption line**
  - bio tag on dedicated accounts
  - a **pinned author reply** on Instagram and TikTok pointing to the full episode
  - at least 0.8% engagement
  - at least 40% US audience
  - clips stay live for 30 days
- **Content:** no stolen clips; cut every clip yourself from full episodes. Preferred styles: multi-guest compilations and supercuts, humour, big reactions, truth bombs. B-roll is allowed if it's relevant. No more than 5 near-identical posts.
- **Example caption:**
  ```
  Full episode on @lethimmansplain
  He had an answer for EVERYTHING 💀
  #lethimmansplain #hottakes #podcast
  ```
- **Source:** youtube.com/@lethimmansplain

**Stream House LA** (`5f6b6ac5-…`). **Dropped.** It requires TikTok plus stories, forbids watermarks, and needs a 2,000+ follower page.

**TJR** (`33c3d5a9-…`, streamer, $0.75 per 1,000 views). **Candidate** for Hype Replay. Check its rules first.

## 5. Architecture

```
            ┌───────────── LIVE door (Phase 2, always-on VM) ─────────────┐
 watcher ──►│ streamlink 1-min segments + chat listener (Twitch/Kick/YT)  │
            │ spike detector: rolling baseline, unique chatters, emotes,  │
            │ per-streamer calibration ──► candidate windows              │
            └──────────────────────────────┬──────────────────────────────┘
            ┌──────── VOD door (Phase 1) ──┤
 yt-dlp ───►│ heatmap + timestamped comments ──► candidate windows
            └──────────────────────────────┤
                                           ▼
                         SHARED CLIP CORE
  1. transcript: word timestamps + speakers (AssemblyAI)
  2. RECALL: cheap LLM pass over the transcript, merged with heatmap,
     comment, chat and audio-loudness/laughter peaks → about 30 candidates
  3. RERANK: strong LLM judge with a rubric (hook in the first 2s, payoff,
     standalone context, emotion) + Gemini watching each 60–90s window
  4. boundary snap to sentence ends and silences (word timestamps)
  5. 9:16 reframe: speaker/face tracking, or a per-streamer layout profile
     (facecam on top, gameplay below)
  6. word-level ASS captions + campaign overlays (watermark, title) via ffmpeg/libass
  7. RULE CHECK against campaign config → pass/fail list
                                           ▼
  REVIEW: FastAPI dashboard + Telegram message to the phone
          (clip file + caption + pinned-comment text + "submit by HH:MM")
                                           ▼
  POST (phone, manual for the first 10 posts) → SUBMIT on Content Rewards (manual)
                                           ▼
  TRACK views and retention → feedback loop retunes signal weights per niche
  DISCOVERY: hourly board scan → alert on new or high-paying campaigns and keywords (GTA…)
```

**Rule:** after a live stream ends, run the VOD pass on the recording and **dedupe** against the clips already made live.

## 6. Stack

| Part | Choice | Notes |
|---|---|---|
| Language | Python 3.12 via `uv` | Python 3.14 is installed but some libraries lag behind |
| Jobs and data | SQLite (Phase 1) → Postgres on the VM (Phase 2) | A job table with status states |
| Download | yt-dlp, streamlink | `brew install streamlink` |
| Video | **ffmpeg-full** (libass) | `brew install ffmpeg-full` |
| Transcription | AssemblyAI (about $0.15–0.21 per hour, words and speakers) | Groq Whisper or local WhisperX as a fallback |
| LLM recall | Claude Haiku 5.5 (`claude-haiku-5-5`) | Cheap pass over the whole transcript |
| LLM judge | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | Rubric scoring of the top candidates |
| Video judge | Gemini (AI Studio) | Watches short windows only |
| Reframe | MediaPipe face detection + OpenCV | Per-streamer layout profiles in YAML |
| Dashboard | FastAPI + HTMX | Runs locally on the Mac in Phase 1 |
| Phone delivery | Telegram bot | Send file, caption and comment text; 30-minute submit reminder |
| Auto-post (Phase 3) | YouTube Data API, Instagram API; Upload-Post as an alternative | Only after the first 10 manual posts per account |
| Secrets | `.env` (git-ignored) | Never paste keys into chat |

**Spending limits:** set a hard monthly cap on every API key.

## 7. GTA VI focus

- **Launch: 19 November 2026** on PS5 and Xbox Series X|S. It unlocks at midnight local time. No PC date yet. GTA Online is expected in 2027.
- **Content Rewards board on 16 Nov:** no GTA campaigns among the 100 listed. GTA content will mostly come through **streamer campaigns**, because almost every big streamer will play GTA VI for weeks.
- **Scanner alerts** on `GTA|Grand Theft|Rockstar|Vice City|Leonida|Lucia|Jason` in campaign titles and descriptions, plus streamer campaigns whose streamers are playing GTA VI.
- **Accounts:** **Hype Replay becomes the GTA VI page** now (rename it, for example "Hype Replay | GTA 6 Clips", and update the bio). That avoids starting from zero during the hype window. Add dedicated GTA pages per campaign later when a campaign requires them.
- **Content sources, safest first:**
  1. A streamer campaign's own streams (permission comes with the campaign).
  2. The user's own gameplay, if they buy the game.
  3. Random streamers without permission. Not eligible for payouts and a copyright risk, so **don't**.

  Before monetizing your own gameplay, check Rockstar's video policy.
- **Technical differences for gaming:**
  - Game audio and silence make the transcript signal weaker. Weight **chat spikes, audio loudness, and the Gemini video judge** more heavily.
  - Layout profiles: a facecam corner crop plus gameplay, or gameplay only (centre/action crop).
  - Add a **spoiler flag**. Story spoilers in launch week attract reports and hate.
- **Timing:** the hype window is the first 2–4 weeks after launch. Phase 1 (VOD) can clip GTA streamer VODs from Twitch, Kick and YouTube. That's the fastest route to GTA clips, so **ship Phase 1 fast**, then the live door.

## 8. Campaign rule config (example)

`campaigns/lhm.yaml`:
```yaml
id: 705f6e21-38af-408c-9e55-c77e4611bf09
name: Let Him Mansplain
accounts: {youtube: lhmtruthbombs, instagram: lhmtruthbombs}
platforms: [instagram, youtube]
sources: ["https://www.youtube.com/@lethimmansplain"]
payout_cpm: {instagram: 1.00, youtube: 0.50}
overlays:
  watermark: {image: assets/lhm_watermark.png, text: "@lethimmansplain", position: top_right, opacity: 0.8, full_duration: true}
caption:
  first_line_must_contain: "@lethimmansplain"
  hashtags: ["#lethimmansplain", "#podcast"]
  templates: ["Full episode on @lethimmansplain\n{hook}\n{hashtags}", "{hook} @lethimmansplain\nAgree or disagree? 👇\n{hashtags}"]
pinned_comment: {platforms: [instagram], replies: ["Full episode on YouTube @lethimmansplain 🔗", "Full episode linked in bio 🔗"]}
preferences: {multi_guest: true, styles: [humour, big_reaction, truth_bomb, supercut], max_similar_posts: 5}
forbid: [ad_like, stolen_clip]
submit_within_minutes: 30
min_live_days: 30
```

## 9. Build phases

**Phase 0: Setup (about half a day)**
- `brew install ffmpeg-full streamlink`
- `uv init`, a private GitHub repo, `.env.example`, the repo layout below, and a `make doctor` command that checks tools and keys.

**Phase 1: VOD to ready-to-post clips (about 1–2 weeks). Priority, because of GTA timing.**
1. `scanner`: scrape the board and campaign pages into the database. Hourly. Telegram alert for new campaigns or keyword matches.
2. `ingest_vod`: yt-dlp download, heatmap and comments.
3. `core`: transcript → recall → rerank → boundary snap → reframe → captions and overlays → render.
4. `rules`: load campaign YAML and validate the clip, caption and overlays.
5. `review`: FastAPI dashboard with approve, reject and edit-caption. On approve, send to Telegram with the file, caption, pinned-comment text and a "submit by" time.
6. **Done when:** given an LHM episode URL, it produces at least 5 rule-compliant clips in 15 minutes or less. The user rates at least 3 of them as postable.

**Phase 2: Live door (about 1–2 weeks)**
- Always-on Oracle VM (Ubuntu, Docker).
- Stream watcher, segmented recorder, Twitch/Kick/YouTube chat listeners, spike detector, clips while live, post-stream VOD pass and dedupe.
- Raw files kept for 48–72 hours (about 2.7 GB per hour at 1080p60).
- **Done when:** a GTA VI stream produces its first clip within 5 minutes of a chat spike.

**Phase 3: Scale (ongoing)**
- Auto-posting after the first 10 manual posts per account, using the best Eastern-time slots.
- Submission helpers and a 30-minute timer.
- View tracking and the feedback loop.
- Multi-account and multi-campaign scheduling.
- TikTok toggle.

**Repo layout**
```
viral-clipper/
  app/{scanner,ingest,core,rules,review,notify,track}/
  campaigns/*.yaml   profiles/*.yaml (streamer layouts)   assets/ (watermarks, fonts)
  brand/  data/ (git-ignored)  tests/  .env.example  PLAN.md  CLAUDE.md
```

## 10. Before building (user checklist)

- [ ] API keys, each with a spending limit: Anthropic, Google AI Studio (Gemini), AssemblyAI. Put them in `.env` only, never in chat.
- [ ] A Telegram bot from @BotFather. Put the token and your chat ID in `.env`.
- [ ] Payout method in Whop.
- [ ] Instagram @lhmtruthbombs: add the bio link `https://www.youtube.com/@lethimmansplain` from the phone app.
- [ ] Connect the @lhmtruthbombs YouTube and Instagram accounts to Content Rewards for LHM.
- [ ] Phone-verify YouTube for LHM Truth Bombs (and Hype Replay if not done).
- [ ] Warm up all accounts (2 days engagement only, test post on day 3 from the phone).
- [ ] Rebrand Hype Replay as the GTA VI page (name and bio). Keep its warm-up going with GTA VI content.
- [ ] Confirm which Content Rewards login is used: sanketpc5 or sanket.p7420.

## 11. Start the fresh chat with

> Read PLAN.md and CLAUDE.md in ~/viral-clipper. Start Phase 0, then Phase 1 in the order listed. Explain each new concept in one or two plain lines as we go.
