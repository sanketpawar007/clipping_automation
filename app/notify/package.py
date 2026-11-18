"""The posting package sent to the phone for each clip: the video, then a guide that says
exactly which text goes where on each platform."""
from __future__ import annotations

import html
from pathlib import Path

from app import db
from app.notify import telegram
from app.rules import campaign as campaigns

PLATFORM_NAMES = {"youtube": "YouTube Shorts", "instagram": "Instagram Reels", "tiktok": "TikTok",
                  "x": "X"}
YT_TITLE_MAX = 100


def _code(text: str) -> str:
    return f"<code>{html.escape(text)}</code>"


def youtube_title(clip: dict, camp: campaigns.Campaign) -> str:
    """Shorts titles are capped at 100 characters: the hook plus the hashtags that fit, keeping
    every hashtag the campaign requires."""
    first = (clip["caption"] or "").split("\n", 1)[0].strip()
    tags = [h for h in camp.caption.hashtags]
    required = [x for x in camp.caption.must_contain if x.startswith("#")]
    title = first
    for t in required + [t for t in tags if t not in required]:
        if t.lower() not in title.lower() and len(title) + 1 + len(t) <= YT_TITLE_MAX:
            title = f"{title} {t}"
    return title[:YT_TITLE_MAX]


def platform_caption(clip: dict, camp: campaigns.Campaign, platform: str) -> str:
    cap = clip["caption"] or ""
    if camp.music and camp.music.credit:          # music campaigns credit the song per platform
        for other, line in camp.music.credit.items():
            if other != platform and line in cap and platform in camp.music.credit:
                cap = cap.replace(line, camp.music.credit[platform])
    return cap


def build_messages(clip: dict, camp: campaigns.Campaign) -> list[str]:
    n = clip["id"]
    acct = ", ".join(f"{PLATFORM_NAMES.get(p, p)}: @{h}" for p, h in camp.accounts.items()
                     if p in camp.platforms)
    lines = [
        f"<b>📋 POST GUIDE · clip #{n} · {html.escape(camp.name)}</b>",
        f"Title on video: {html.escape(clip.get('headline') or clip.get('hook') or '')}",
        "",
        f"<b>Post from:</b> {html.escape(acct)}",
        "Post from the phone apps, US VPN server on, same video on each platform.",
    ]
    if camp.id:
        lines += ["", f"<b>Submit within {camp.submit_within_minutes} min of posting</b> (each platform's "
                  "post link separately):", f"https://contentrewards.com/c/campaigns/{camp.id}"]
    notes = list(camp.posting_notes)
    if camp.music and camp.music.disclosure:
        notes += list(camp.music.disclosure.values())
    if notes:
        lines += ["", "<b>⚠️ Before posting:</b>"] + [f"• {html.escape(x)}" for x in notes]
    lines += ["", "The next messages are the texts. Tap the grey text to copy it."]
    msgs = ["\n".join(lines)]

    for p in camp.platforms:
        name = PLATFORM_NAMES.get(p, p)
        if p == "youtube":
            msgs.append(f"<b>▶️ {name} · TITLE</b> (paste into the title field)\n"
                        + _code(youtube_title(clip, camp)))
            msgs.append(f"<b>▶️ {name} · DESCRIPTION</b> (paste into the description)\n"
                        + _code(platform_caption(clip, camp, p)))
        else:
            icon = {"instagram": "📸", "tiktok": "🎵"}.get(p, "✖️")
            msgs.append(f"<b>{icon} {name} · CAPTION</b> (paste into the caption box)\n"
                        + _code(platform_caption(clip, camp, p)))
    pin = clip.get("pinned_comment")
    pin_on = [PLATFORM_NAMES.get(p, p) for p in camp.pinned_comment.platforms if p in camp.platforms]
    if pin and pin_on:
        msgs.append(f"<b>📌 PINNED COMMENT · {', '.join(pin_on)}</b> (after posting: comment this from the "
                    "same account, then pin it)\n" + _code(pin))
    msgs.append(f"✅ When posted: submit the links, then tap 'Mark posted' on clip #{n} in the dashboard "
                f"to start the {camp.submit_within_minutes}-minute reminder.")
    return msgs


def send(clip_id: int, live: bool = False) -> bool:
    """Sends the clip and its post guide. Returns False if Telegram is not set up."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if row is None:
        raise ValueError(f"clip #{clip_id} not found")
    clip = dict(row)
    camp = campaigns.load(clip["campaign"])
    head = f"{'🔴 LIVE ' if live else ''}🎬 Clip #{clip_id} · {camp.name} · score {clip['score']:.2f}"
    if not telegram.send_video(Path(clip["path"]), head):
        return False
    for m in build_messages(clip, camp):
        telegram.send_html(m)
    return True
