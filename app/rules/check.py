"""Builds the caption for a clip and checks the clip against its campaign's rules."""
from __future__ import annotations

import re

from app.rules.campaign import Campaign


def build_caption(campaign: Campaign, hook: str, index: int = 0, question: str = "",
                  platform: str | None = None) -> str:
    """Rotates through the campaign's templates so posts do not all look identical.
    Templates may use {hook}, {question} (an engagement question) and {hashtags}."""
    templates = campaign.caption.templates or ["{hook}\n{hashtags}"]
    template = templates[index % len(templates)]
    credits = campaign.music.credit if campaign.music else {}
    credit = credits.get(platform or "", "") or next(iter(credits.values()), "")
    text = template.format(hook=hook.strip(), question=(question or "Agree or disagree?").strip(),
                           hashtags=" ".join(campaign.caption.hashtags), credit=credit)
    return "\n".join(line for line in text.split("\n") if line.strip()).strip()


def pinned_comment(campaign: Campaign, index: int = 0) -> str:
    replies = campaign.pinned_comment.replies
    return replies[index % len(replies)] if replies else ""


def check(campaign: Campaign, *, caption: str, duration: float, render: dict,
          clip_text: str = "", ad_like: bool = False, spoiler: bool = False) -> list[dict]:
    """Returns [{rule, ok, detail}]. A clip is compliant when every `ok` is True."""
    out: list[dict] = []

    def add(rule: str, ok: bool, detail: str = "") -> None:
        out.append({"rule": rule, "ok": bool(ok), "detail": detail})

    lim = campaign.clip
    add("length", lim.min_seconds - 0.5 <= duration <= lim.max_seconds + 0.5,
        f"{duration:.0f}s (allowed {lim.min_seconds:.0f}-{lim.max_seconds:.0f}s)")

    must = campaign.caption.first_line_must_contain
    if must:
        first = caption.split("\n", 1)[0]
        add("caption first line tag", must.lower() in first.lower(), f"needs {must} in line 1")

    for need in campaign.caption.must_contain:
        add(f"mentions {need}", need.lower() in caption.lower(), f"caption must contain {need}")

    missing = [h for h in campaign.caption.hashtags if h.lower() not in caption.lower()]
    if campaign.caption.hashtags:
        add("hashtags", not missing, "missing " + " ".join(missing) if missing else "all present")

    add("caption length", len(caption) <= campaign.caption.max_length, f"{len(caption)} characters")

    wm_required = bool(campaign.overlays.watermark)
    if "watermark" in campaign.forbid:
        add("no watermark", not render.get("watermark"), "campaign forbids watermarks")
    elif wm_required:
        add("watermark", bool(render.get("watermark")), "must be visible for the whole clip")

    if campaign.banned_words:
        text = f"{caption}\n{clip_text}".lower()
        hits = [w for w in campaign.banned_words if re.search(rf"\b{re.escape(w.lower())}\b", text)]
        add("banned words", not hits, "found: " + ", ".join(hits) if hits else "none")

    if "ad_like" in campaign.forbid:
        add("not ad-like", not ad_like, "judge flagged it as an advert" if ad_like else "")
    if "spoilers" in campaign.forbid:
        add("no spoilers", not spoiler, "judge flagged a story spoiler" if spoiler else "")
    return out


def compliant(report: list[dict]) -> bool:
    return all(r["ok"] for r in report)
