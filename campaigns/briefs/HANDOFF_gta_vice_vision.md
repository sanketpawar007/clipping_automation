# Handoff: GTA VI clipping, recent activity (2026-11-18)

## 1. New GTA account: Vice Vision (replaces Hype Replay for GTA)
- Do **not** use Hype Replay (`hypereplayhq`) for GTA any more. Vice Vision is the GTA page.
- It's used for GTA 6 edits/news/hype **and** GTA streamer stream clipping.
- All three platforms were created on a US (New York) VPN.

| Platform | Handle | Notes |
|---|---|---|
| YouTube | @vicevisionclips (channel UCFVUnBUodu_GF27Pv3vb72Q) | Name "Vice Vision"; own banner `brand/vicevision_banner.png`; description = daily GTA 6 moments, edits, news, stream clips |
| Instagram | @vicevisionclips | Creator account, category "Gaming video creator"; bio starts "GTA 6 fan page" |
| TikTok | @vicevisionclips | Name "Vice Vision \| GTA 6" (locked 7 days); same bio |

- **Gmail:** vicevisionclipshq@gmail.com. The credentials stay with the user; never put them in code or chat.
- **Profile picture (all three):** Rockstar's official "Jason and Lucia 01" square artwork, without the logo, from rockstargames.com/VI/media. Saved as `brand/vicevision_profile_rockstar.jpg`. Our own logo is kept as `brand/vicevision_profile.png`.
- **Fan-page rule:** bios say "fan page". Never use the Rockstar/GTA logo in a way that looks official.

## 2. Campaign: Tokyo Girl x GTA 6 (Content Rewards, music category)
- The full brief is in `campaigns/briefs/tokyo_girl_gta6.md`.
- **Pay:** Instagram and TikTok only. $0.70 per 1K views, $2 minimum and $35 maximum per post, $2K budget.
- **Audio:**
  - The song "Tokyo Girl" must be the main audio, attached via the **official in-app sound**.
  - No talking. SFX sit under the music. Trailer audio is muted.
- **Caption credit:** Instagram "Song: Tokyo Girl by @PassiveBand"; TikTok "Song: Tokyo Girl by @Passive". Paid-content disclosure is required.
- **Themes:** GTA 6 news/leaks with on-screen text; memes/hype; Jason & Lucia; edits, gameplay and Vice City views.
- **Other requirements:**
  - Original edits only.
  - 60%+ Tier-1 views and at least 1% engagement.
  - Join the Placements Whop and send analytics through its support chat.
- **The API can't do this:** Instagram/TikTok APIs can't attach official sounds (an uploaded file's audio becomes "Original audio"). These posts must be made **manually on the phone**.

## 3. What to build next
1. **Music-edit mode** (new pipeline mode):
   - footage library (official trailers/screenshots + gameplay)
   - beat-synced cuts to the Tokyo Girl tempo
   - on-screen text hooks (news/hype lines)
   - export **without** the song, plus a sidecar note giving the song offset to use in-app
   - caption text with the song credit and a paid-partnership reminder
2. **`campaigns/tokyo_girl_gta6.yaml`** from the brief:
   - accounts: vicevisionclips on Instagram and TikTok
   - `audio: official_in_app`, `posting: manual_phone`
   - forbid talking, spoilers and ad-like content
3. **GTA stream-clip campaign YAML:**
   - Copy `campaigns/_gta_template.yaml.example`.
   - Change its accounts from `hypereplayhq` to `vicevisionclips` on YouTube, Instagram and TikTok.
4. **Update PLAN.md:** the GTA page is now Vice Vision, not Hype Replay. Add TikTok to the account table, and turn the TikTok toggle on for Vice Vision.
5. **Timing:** GTA VI launches **19 Nov 2026**. Prioritise getting streamer VODs → clips working for launch day.

## 4. The user's manual to-dos (not code)
- Connect Vice Vision (Instagram + TikTok) to Tokyo Girl on Content Rewards, and join the Placements Whop.
- Save the official Tokyo Girl sound in both apps.
- Warm up the accounts for a few days on the US VPN before the first posts.
