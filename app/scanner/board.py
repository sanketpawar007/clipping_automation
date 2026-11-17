"""Reads the public Content Rewards board and campaign pages.

The site is a Next.js app: campaign data is embedded in the HTML as a stream of
`self.__next_f.push([1, "..."])` script calls. We join those strings and pull the
JSON out. Only public pages are read; `/api/` is never called (robots.txt disallows it).
"""
from __future__ import annotations

import json
import re

import httpx

BOARD_URL = "https://contentrewards.com/c/discover/clipping"
CAMPAIGN_URL = "https://contentrewards.com/c/campaigns/{id}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_DECODER = json.JSONDecoder()


def fetch(url: str) -> str:
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.text


def flight_text(html: str) -> str:
    """Joins the embedded Next.js data chunks into one string."""
    return "".join(json.loads(m.group(1)) for m in _PUSH.finditer(html))


def _decode_after(text: str, marker: str) -> list:
    """Decodes every JSON value that directly follows `marker` in `text`."""
    found, pos = [], 0
    while (i := text.find(marker, pos)) != -1:
        pos = i + len(marker)
        try:
            value, _ = _DECODER.raw_decode(text[pos:])
            found.append(value)
        except json.JSONDecodeError:
            continue
    return found


def parse_board(html: str) -> list[dict]:
    """All campaigns listed on the board, de-duplicated by id."""
    by_id: dict[str, dict] = {}
    for arr in _decode_after(flight_text(html), '"campaigns":'):
        if isinstance(arr, list):
            for c in arr:
                if isinstance(c, dict) and c.get("id") and c.get("title"):
                    by_id[c["id"]] = c
    return list(by_id.values())


def parse_campaign(html: str, campaign_id: str) -> dict | None:
    """Detail for one campaign: payout per platform (in cents per 1,000 views), limits, status."""
    text = flight_text(html)
    for data in _decode_after(text, '"data":'):
        if (isinstance(data, dict) and data.get("id") == campaign_id
                and "configuration" in data):
            cfg = data.get("configuration") or {}
            return {
                "name": data.get("name"),
                "status": data.get("status"),
                "description": data.get("description"),
                "requires_application": data.get("requiresApplication"),
                "budget_cents": cfg.get("budget"),
                "payout_model": cfg.get("payoutModel") or [],
                "metrics": data.get("metrics") or {},
                "brief_counts": data.get("briefCounts") or {},
                "top_clips": top_clips(text),
            }
    return None


def top_clips(text: str) -> list[dict]:
    """The campaign's best-performing submissions: platform, title and views."""
    for arr in _decode_after(text, '"topClips":'):
        if isinstance(arr, list) and arr:
            return [{"platform": c.get("platform"), "title": c.get("title"),
                     "views": c.get("totalViews"), "url": c.get("postUrl"), "rank": c.get("rank")}
                    for c in arr if isinstance(c, dict)]
    return []


def fetch_board() -> list[dict]:
    return parse_board(fetch(BOARD_URL))


def fetch_campaign(campaign_id: str) -> dict | None:
    return parse_campaign(fetch(CAMPAIGN_URL.format(id=campaign_id)), campaign_id)
