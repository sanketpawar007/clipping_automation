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
    "gaming": {"judge": 0.20, "heatmap": 0.05, "comments": 0.05, "loudness": 0.10, "video": 0.45,
               "chat": 0.15},
}


class Watermark(BaseModel):
    image: str | None = None
    text: str | None = None
    position: str = "top_right"
    width: int = 460                # pixels, on the 1080-wide output
    opacity: float = 0.8
    full_duration: bool = True


class Overlays(BaseModel):
    watermark: Watermark | None = None


class Caption(BaseModel):
    first_line_must_contain: str | None = None
    must_contain: list[str] = []          # each must appear somewhere in the caption
    hashtag_pool: list[str] = []          # extra tags rotated between posts ("mix them up")
    pool_per_post: int = 3
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
    reserve: int = 2                # places in each run kept for the best supercuts


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
    # streamer clips: Gemini watches each candidate and plans the edit (hook teaser, cut, zooms)
    video_editor: bool = False
    min_virality: int = 6            # editor's 0-10 rating a clip needs to be kept
    zoom: float = 0.12               # punch-in amount on peak reactions (0.12 = 12%)
    cta: str = ""                    # end card text, shown for the last few seconds
    credit: str = ""                 # small on-screen credit for the whole clip
    disclosure: str = ""             # small on-screen paid-content label for the whole clip
    spoiler_warning: bool = False    # show a spoiler warning at the start instead of dropping the clip


class Music(BaseModel):
    """Music-edit campaigns: the song is added in the phone app, never burned into the video."""
    title: str
    artist: str = ""
    file: str | None = None          # local copy used ONLY to find the beats; never exported
    bpm: float | None = None         # used when there is no file
    start: float | None = None       # where in the song the edit starts; found automatically
    official_sound: dict[str, str] = {}  # platform -> link to the official in-app sound
    credit: dict[str, str] = {}      # platform -> required caption credit line
    disclosure: dict[str, str] = {}  # platform -> how to switch on the paid-content label


class MusicEdit(BaseModel):
    library: str = ""                # data/library/<name>: official footage to cut from
    themes: list[str] = ["hype", "jason_lucia", "scenic", "meme"]
    beats_per_shot: int = 2          # cut on every 2nd beat; the first shot holds longer
    framing: str = "crop"            # crop (full-screen 9:16) or blur (whole frame, blurred sides)
    brief: str = ""                  # plain-language guidance for the on-screen text writer


class Live(BaseModel):
    """Clipping while the stream is live."""
    quality: str = "1080p60,1080p,720p60,720p,best"
    chat: bool = True
    chat_z: float = 4.0              # how unusual chat activity must be (vs. this stream's normal)
    min_messages: int = 8            # and at least this many messages in the last 15 seconds
    audio_db: float = 14.0           # or a loudness jump this far above the recent normal
    before_s: float = 55             # footage kept before the spike (the setup)
    after_s: float = 20              # and after it (the reaction and payoff)
    cooldown_s: float = 75
    sweep_minutes: int = 15          # also review every N minutes for quotes chat did not react to
    sweep_clips: int = 1
    max_clips_per_hour: int = 10     # spending cap
    min_score: float = 0.55          # live clips below this are dropped, not sent
    not_before: str | None = None    # ISO time; streams before this are not eligible footage
    keep_minutes: int = 90           # raw footage kept on disk
    send_to_phone: bool = True       # send each live clip to Telegram straight away


class Brand(BaseModel):
    """A page's look (brands.yaml), so each page is recognisable and they never look alike."""
    name: str = ""
    title_text: str = "#101010"
    title_box: str = "#FFFFFF"
    highlight: list[str] = ["#FFE600"]
    end_text: str = "#FFFFFF"
    end_box: str = "#2B3AE8"
    follow: str = ""


def load_brand(page: str | None) -> Brand:
    path = ROOT / "brands.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return Brand(**(data or {}).get(page or "", {}))


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
    posting_notes: list[str] = []             # rules to follow when posting, shown in the post guide
    streamer: str = ""                        # who the clips must feature (gaming campaigns)
    page: str = ""                            # which of our pages posts these clips (brands.yaml)
    mode: str = "clip"                        # clip (cut long videos) | music_edit (beat-synced edits)
    posting: str = "phone"                    # phone | api: music edits must be posted by hand
    live: Live = Field(default_factory=Live)
    music: Music | None = None
    music_edit: MusicEdit = Field(default_factory=MusicEdit)

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
