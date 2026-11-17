"""Campaign config (campaigns/<slug>.yaml). Rules differ per campaign, so nothing is hard-coded."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.config import ROOT

# How much each signal counts toward the final score. Gaming leans on audio and video because
# game sound and silence make the transcript weak (PLAN.md section 7).
WEIGHTS = {
    "podcast": {"judge": 0.60, "heatmap": 0.14, "comments": 0.08, "loudness": 0.06, "video": 0.12},
    "gaming": {"judge": 0.35, "heatmap": 0.15, "comments": 0.05, "loudness": 0.20, "video": 0.25},
}


class Watermark(BaseModel):
    image: str | None = None
    text: str | None = None
    position: str = "top_right"
    opacity: float = 0.8
    full_duration: bool = True


class Overlays(BaseModel):
    watermark: Watermark | None = None


class Caption(BaseModel):
    first_line_must_contain: str | None = None
    hashtags: list[str] = []
    templates: list[str] = ["{hook}\n{hashtags}"]
    max_length: int = 2200


class PinnedComment(BaseModel):
    platforms: list[str] = []
    replies: list[str] = []


class ClipLimits(BaseModel):
    min_seconds: float = 20
    max_seconds: float = 75


class Supercut(BaseModel):
    enabled: bool = False
    min_parts: int = 2
    max_parts: int = 4


class Tighten(BaseModel):
    enabled: bool = True
    max_pause: float = 0.45         # silences longer than this are cut out
    keep_pause: float = 0.18        # how much of each silence stays, so speech sounds natural
    remove_fillers: bool = True     # drop stand-alone "um", "uh"


class Headline(BaseModel):
    enabled: bool = True
    style: str = ""                 # e.g. 'Asked men: <question>?'. Guidance for the AI writer.


class Editing(BaseModel):
    """How clips are cut for this campaign. The engine is shared; these switches are not."""
    brief: str = ""                 # plain-language cutting instructions, given to the AI passes
    candidates: int = 10            # clips offered for review per video
    supercut: Supercut = Field(default_factory=Supercut)
    tighten: Tighten = Field(default_factory=Tighten)
    headline: Headline = Field(default_factory=Headline)
    cold_open: bool = True          # open on the strongest line, then play the moment
    end_on_payoff: bool = True      # never pad a clip past its punchline
    learn_from_top_clips: bool = True


class Campaign(BaseModel):
    slug: str = ""
    id: str = ""
    name: str
    niche: str = "podcast"                    # podcast | gaming: picks the signal weights
    accounts: dict[str, str] = {}
    platforms: list[str] = []
    sources: list[str] = []
    payout_cpm: dict[str, float] = {}
    overlays: Overlays = Field(default_factory=Overlays)
    caption: Caption = Field(default_factory=Caption)
    pinned_comment: PinnedComment = Field(default_factory=PinnedComment)
    preferences: dict = {}
    clip: ClipLimits = Field(default_factory=ClipLimits)
    editing: Editing = Field(default_factory=Editing)
    forbid: list[str] = []                    # e.g. ad_like, stolen_clip, watermark, spoilers
    banned_words: list[str] = []
    profile: str | None = None                # profiles/<name>.yaml layout for streamers
    weights: dict[str, float] = {}
    submit_within_minutes: int = 30
    min_live_days: int = 0

    def signal_weights(self) -> dict[str, float]:
        return {**WEIGHTS.get(self.niche, WEIGHTS["podcast"]), **self.weights}


def load(slug: str) -> Campaign:
    path = ROOT / "campaigns" / f"{slug}.yaml"
    if not path.exists():
        names = ", ".join(p.stem for p in (ROOT / "campaigns").glob("*.yaml"))
        raise FileNotFoundError(f"No campaign '{slug}'. Available: {names}")
    c = Campaign(**yaml.safe_load(path.read_text()))
    c.slug = slug
    return c


def load_profile(name: str | None) -> dict:
    if not name:
        return {}
    path = Path(ROOT / "profiles" / f"{name}.yaml")
    return yaml.safe_load(path.read_text()) or {}
