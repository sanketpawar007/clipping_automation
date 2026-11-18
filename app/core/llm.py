"""The AI passes: cheap recall over the whole transcript, strong judge on the shortlist,
and an optional Gemini pass that actually watches the top windows.

Every function returns None/[] when its API key is missing, so the pipeline still runs
on signals alone.
"""
from __future__ import annotations

from pathlib import Path

import anthropic
from pydantic import BaseModel, Field
from rich.console import Console

from app.config import get_settings
from app.core import media
from app.rules.campaign import Campaign

console = Console()


# ---------- recall ----------

class Candidate(BaseModel):
    start_line: int = Field(description="Line number where the clip should start")
    end_line: int = Field(description="Line number of the last line of the clip")
    hook: str = Field(description="What happens, in under 12 words")
    style: str = Field(description="humour | big_reaction | truth_bomb | story | debate | other")


class Part(BaseModel):
    start_line: int
    end_line: int


class SupercutCandidate(BaseModel):
    theme: str = Field(description="The question or topic every part answers, under 12 words")
    parts: list[Part] = Field(description="The answers to stitch together, in the best viewing order")
    hook: str = Field(description="What makes this compilation watchable, in under 12 words")


class RecallResult(BaseModel):
    candidates: list[Candidate]
    supercuts: list[SupercutCandidate] = Field(
        description="Compilations of several short answers on one theme. Empty if not asked for.")


RECALL_SYSTEM = """You find moments in long videos that would work as short vertical clips \
(YouTube Shorts, Instagram Reels).

You are the wide net: a stricter judge reviews your list afterwards, so include anything with \
real potential and skip only what is clearly flat. A good clip:
- opens on a line that makes a stranger stop scrolling within two seconds
- makes sense with no context from the rest of the video
- builds to a payoff: a punchline, a strong opinion, a reaction, a reveal
- carries emotion: laughter, shock, disagreement, vulnerability

The transcript has one numbered line per sentence. Answer with line numbers. Start each clip on \
the line where the setup begins and end it on the payoff line, not after it trails off. Clips \
must not overlap each other."""


def _client() -> anthropic.Anthropic | None:
    key = get_settings().anthropic_api_key
    return anthropic.Anthropic(api_key=key) if key else None


def _brief(campaign: Campaign) -> str:
    b = campaign.editing.brief.strip()
    return f"\n<campaign_cutting_brief>\n{b}\n</campaign_cutting_brief>\n" if b else ""


def recall(transcript: str, campaign: Campaign, duration_s: float,
           want: int = 30, context: str = "") -> RecallResult | None:
    client = _client()
    if client is None:
        return None
    s = get_settings()
    lim = campaign.clip
    prompt = (
        f"<transcript>\n{transcript}\n</transcript>\n\n"
        f"Campaign: {campaign.name}. Preferred styles: {campaign.preferences.get('styles', 'any')}.\n"
        f"The video is {duration_s / 60:.0f} minutes long. Each clip should run "
        f"{lim.min_seconds:.0f}-{lim.max_seconds:.0f} seconds (timestamps are at the start of "
        f"each line). Return up to {want} candidates, spread across the whole video."
        + _brief(campaign) + context)
    sc = campaign.editing.supercut
    if sc.enabled:
        prompt += (
            f"\nAlso propose up to 6 supercuts: {sc.min_parts}-{sc.max_parts} short parts from "
            "different places in the video that answer the same question or make the same point "
            "from different angles, ideally from different speakers. Each part should be the "
            "sharpest 5-20 seconds of that answer and make sense on its own. Open with the "
            f"strongest part. The whole supercut must fit in {lim.max_seconds:.0f} seconds. "
            "Only propose one where the video truly has several strong takes on one theme.")
    else:
        prompt += "\nLeave supercuts empty."
    resp = client.messages.parse(
        model=s.recall_model, max_tokens=16000, system=RECALL_SYSTEM,
        messages=[{"role": "user", "content": prompt}], output_format=RecallResult)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        console.print(f"[yellow]recall pass returned nothing ({resp.stop_reason})[/]")
        return None
    return resp.parsed_output


# ---------- judge ----------

class Verdict(BaseModel):
    candidate_id: int
    hook_score: int = Field(description="0-10: do the first two seconds stop the scroll")
    payoff_score: int = Field(description="0-10: is there a clear punchline, reveal or reaction")
    standalone_score: int = Field(description="0-10: understandable with zero context")
    emotion_score: int = Field(description="0-10: emotional charge")
    campaign_fit_score: int = Field(description="0-10: fit with the campaign's preferred styles")
    start_line: int = Field(description="Best first line, tightened if the setup drags")
    end_line: int = Field(description="Best last line, ending on the payoff")
    cold_open_line: int = Field(
        description="Line number of the single most arresting line inside the clip, to replay as "
                    "a 1-4 second teaser before the clip starts. -1 if the clip already opens on "
                    "its strongest line or no line works out of context.")
    headline: str = Field(
        description="On-screen title shown for the whole clip, under 40 characters. It tells a "
                    "viewer with the sound off why to keep watching, without giving away the payoff.")
    caption_hook: str = Field(description="One caption line for the post, under 60 characters")
    caption_question: str = Field(
        description="A short question to end the caption that makes viewers want to reply, "
                    "under 50 characters")
    reason: str = Field(description="One sentence: why this works or does not")
    ad_like: bool = Field(description="True if the clip reads like an advert or sponsor read")
    spoiler: bool = Field(description="True if it reveals a story spoiler for a game, film or show")
    explicit: bool = Field(description="True if it relies on sexual content, slurs or heavy profanity "
                                       "that would get the post limited or removed")


class JudgeResult(BaseModel):
    verdicts: list[Verdict]


JUDGE_SYSTEM = """You are the final judge for short-form clips cut from long videos. Clippers \
are paid per view, so the only question is whether strangers on a Shorts or Reels feed will \
watch this to the end and react.

Score each candidate honestly against the rubric and use the full 0-10 range: most candidates \
are average (4-6), and only a few per video deserve 8 or more. A clip with a weak first two \
seconds fails no matter how good the ending is, because viewers have already swiped.

You may tighten a candidate by moving its start or end line within the lines you are shown. \
Move the start forward to open on the strongest line; end on the payoff. Keep each clip inside \
the campaign's length limits. End every clip on its payoff: the punchline, the reaction or \
the strongest statement, never the trailing chatter after it. The caption hook should tease \
the moment in the creator's voice without giving away the payoff. Only name a game, person or \
event in the headline or caption if the transcript makes it certain, and without hashtags or \
@mentions (those are added later)."""


def _winning(top_clips: list[dict]) -> str:
    if not top_clips:
        return ""
    rows = "\n".join(f"- {c['views']:,} views ({c['platform']}): {(c['title'] or '').strip()[:220]}"
                     for c in top_clips[:10] if c.get("views"))
    return ("\n<top_clips_on_this_campaign>\nThese are the best-performing clips other clippers "
            "have posted for this same campaign, with their captions. Use them as evidence of which "
            "topics, angles and caption styles this audience rewards. Do not copy them.\n"
            f"{rows}\n</top_clips_on_this_campaign>\n")


def judge(blocks: list[dict], campaign: Campaign, top_clips: list[dict] | None = None,
          context: str = "") -> dict[int, Verdict]:
    """blocks: [{id, kind, lines (numbered text incl. a little context), seconds, signals}]."""
    client = _client()
    if client is None or not blocks:
        return {}
    s = get_settings()
    parts = []
    for b in blocks:
        sig = ", ".join(f"{k} {v:.2f}" for k, v in b["signals"].items())
        parts.append(f'<candidate id="{b["id"]}" kind="{b["kind"]}" seconds="{b["seconds"]:.0f}" '
                     f'audience_signals="{sig}">\n{b["lines"]}\n</candidate>')
    lim = campaign.clip
    prompt = (
        "\n\n".join(parts) + "\n\n"
        f"Campaign: {campaign.name}. Preferences: {campaign.preferences}. "
        f"Not allowed: {campaign.forbid}. Clip length: {lim.min_seconds:.0f}-{lim.max_seconds:.0f}s.\n"
        "Audience signals are 0-1: heatmap is YouTube's most-replayed graph, comments are "
        "viewer timestamp mentions, loudness is audio peaks such as laughter. Lines marked "
        "(context) are outside the candidate but may be pulled in.\n"
        "A supercut is several parts stitched in order; score it as one clip and leave its "
        "start_line, end_line and cold_open_line as given (-1 for cold_open_line)."
        + (f" Headline style for this campaign: {campaign.editing.headline.style}."
           if campaign.editing.headline.style else "")
        + _brief(campaign)
        + (_winning(top_clips or []) if campaign.editing.learn_from_top_clips else "")
        + context
        + "\nReturn one verdict per candidate.")
    resp = client.beta.messages.parse(
        model=s.judge_model, max_tokens=16000, system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}], output_format=JudgeResult,
        # If the safety system declines the request, the API retries on a fallback model.
        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        console.print(f"[yellow]judge pass returned nothing ({resp.stop_reason})[/]")
        return {}
    return {v.candidate_id: v for v in resp.parsed_output.verdicts}


# ---------- video judge ----------

class VideoVerdict(BaseModel):
    visual_score: int = Field(description="0-10: how visually engaging the footage is")
    note: str


def video_judge(source: str | Path, start: float, end: float, workdir: Path) -> VideoVerdict | None:
    """Gemini watches a small low-res copy of the window and scores what is on screen."""
    s = get_settings()
    if not s.gemini_api_key:
        return None
    from google import genai
    from google.genai import types

    tmp = workdir / f"judge_{int(start)}.mp4"
    media.run([s.ffmpeg, "-y", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
               "-i", str(source), "-vf", "scale=-2:360,fps=10", "-c:v", "libx264", "-crf", "30",
               "-preset", "veryfast", "-c:a", "aac", "-b:a", "32k", "-ac", "1", str(tmp)])
    try:
        client = genai.Client(api_key=s.gemini_api_key)
        resp = client.models.generate_content(
            model=s.gemini_model,
            contents=[
                types.Part.from_bytes(data=tmp.read_bytes(), mime_type="video/mp4"),
                "This is a candidate short-form clip. Score 0-10 how visually engaging it is for "
                "a Shorts/Reels viewer: visible reactions, laughter, movement, on-screen action. "
                "A static talking head with no reaction is about 4. Add a one-sentence note.",
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_schema=VideoVerdict))
        return resp.parsed
    except Exception as exc:   # the video judge is a bonus signal; never fail the run on it
        console.print(f"[yellow]video judge skipped: {exc}[/]")
        return None
    finally:
        tmp.unlink(missing_ok=True)


# ---------- video editor (gaming / streams) ----------

class Cut(BaseModel):
    start: float = Field(description="seconds from the start of THIS video")
    end: float


class EditPlan(BaseModel):
    format: str = Field(description="reaction | story | reveal | none. reaction: one moment of the "
                                    "streamer reacting. story: a few cuts from this footage joined into "
                                    "setup -> escalation -> payoff. reveal: a first look at a place, car, "
                                    "feature or detail people have never seen, where the GAME is the star. "
                                    "none: nothing worth a standalone post")
    postable: bool = Field(description="True only if this footage alone makes a clip strangers will "
                                       "watch to the end")
    virality: int = Field(description="0-10 chance a standalone clip goes viral. Strict: 5 is average, "
                                      "8+ only for something people rewatch and share")
    cuts: list[Cut] = Field(description="The pieces to play, in order. One cut for a reaction or reveal; "
                                        "2-4 cuts for a story, skipping the boring parts between them. "
                                        "Start at the setup, end right after the payoff")
    hook_start: float = Field(description="Start of a 1.5-3 second teaser shown FIRST (the most arresting "
                                          "instant). -1 if the clip already opens strong "
                                          "or if it is a reveal")
    hook_end: float = Field(description="End of that teaser, -1 if none")
    title: str = Field(description="On-screen title, max 45 characters: the streamer's name and the "
                                   "concrete situation as a story, true to the footage, no punchline "
                                   "giveaway. For a reveal: 'First look at <thing> in GTA 6' style")
    title_style: str = Field(description="box (bold title box, for reactions and stories that need "
                                          "context), plain (light text, when the picture explains itself), "
                                          "or none (no title: the footage is self-explanatory)")
    captions: bool = Field(description="True if the spoken words carry the moment and need subtitles; "
                                       "False for visual moments, reveals or game noise")
    zoom: str = Field(description="punch (quick punch-ins on peak reactions), push (slow push-in across a "
                                  "reveal), or none")
    zooms: list[float] = Field(description="For punch: 1-3 moments (seconds from the start of THIS "
                                           "video) of peak reaction or impact. Empty otherwise")
    collect_theme: str = Field(description="If this footage contains a short moment (3-12 s) that is good "
                                           "but would be stronger combined with similar moments later, a "
                                           "short theme label for it, e.g. 'first look: cars', 'police "
                                           "chaos', 'deaths', 'Vice City views'. Empty if not")
    moment: Cut = Field(description="That collectable moment's start and end (seconds from the start of "
                                    "THIS video); 0,0 if collect_theme is empty")
    what_happens: str = Field(description="One sentence: what actually happens")
    spoiler: bool = Field(description="True if it shows a story ending, character death or plot twist")
    explicit: bool = Field(description="True if it relies on sexual content, slurs or heavy profanity")
    unsafe_people: bool = Field(description="True if the clip shows or involves anyone who is or may be "
                                            "under 18, or a member of the public who did not choose to be "
                                            "on stream (strangers on random video-chat sites such as "
                                            "Omegle/Umingle, passers-by being filmed), or sexual or "
                                            "romantic talk with a stranger")
    reason: str = Field(description="One sentence: why it will or will not get views")


EDITOR_PROMPT = """You are the lead editor for a top streamer-clips page on TikTok, YouTube Shorts and \
Instagram Reels. Watch AND listen to this footage from {streamer}'s live stream and decide what, if \
anything, to make from it. Do not apply one template to everything: choose the treatment this \
footage deserves.

What makes these clips go viral:
- HOOK in the first 2 seconds: the viewer must instantly see or hear that something is about to \
happen. For reactions and stories that usually means a 1.5-3 second teaser of the peak first.
- ONE clear idea: a funny, crazy, surprising or emotional moment with a setup, the real reaction \
and a payoff. Cut everything that needs more than a few seconds of context. End right after the \
payoff. 15-45 seconds, shorter if complete.
- Stories: if the good parts are spread out, use several cuts and skip the dead time between them.
- Reveals: GTA 6 is brand new, so the first time people see a building, beach, car, weapon, \
feature or detail is valuable on its own. Show it cleanly and let it breathe; little text.
- Less is more on screen: only add a title box when the viewer needs context; only add subtitles \
when the words matter. Visual moments often work best with no text at all.
- {streamer} must be part of it (face or voice), except in reveals where the game can lead.
- Small good moments that are not enough alone can be collected (collect_theme) and combined \
later with similar ones into a montage.
- Titles that perform on this kind of page are short stories with the streamer's name, for \
example: "Lacy Got Hit With A $12,000 Restaurant Bill", "Lacy got SCAMMED".
{winning}
Campaign rules and preferences:
{brief}

Transcript of this footage (seconds from the start of this video):
{transcript}

Never pick a moment that involves a minor, a stranger from a random video-chat site, or sexual \
talk with a stranger, however funny: set unsafe_people=true, postable=false, no collect_theme.
Be strict: one great clip beats ten average ones."""


def edit_plan(source: str | Path, start: float, end: float, campaign: Campaign, transcript_lines: str,
              workdir: Path, winning: list[dict] | None = None) -> EditPlan | None:
    """Gemini watches and hears [start, end] of the source and plans the edit. Times in the plan
    are relative to `start`."""
    s = get_settings()
    if not s.gemini_api_key:
        return None
    from google import genai
    from google.genai import types

    tmp = workdir / f"edit_{int(start)}.mp4"
    media.run([s.ffmpeg, "-y", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
               "-i", str(source), "-vf", "scale=-2:480,fps=12", "-c:v", "libx264", "-crf", "28",
               "-preset", "veryfast", "-c:a", "aac", "-b:a", "64k", "-ac", "1", str(tmp)])
    win = ""
    if winning:
        win = "Top clips other clippers have posted for this campaign:\n" + "\n".join(
            f"- {c.get('views') or 0:,} views: {(c.get('title') or '').strip()[:120]}" for c in winning[:8])
    prompt = EDITOR_PROMPT.format(streamer=campaign.streamer or "the streamer", winning=win,
                                  brief=campaign.editing.brief.strip(), transcript=transcript_lines)
    try:
        client = genai.Client(api_key=s.gemini_api_key)
        resp = client.models.generate_content(
            model=s.gemini_model,
            contents=[types.Part.from_bytes(data=tmp.read_bytes(), mime_type="video/mp4"), prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_schema=EditPlan))
        return resp.parsed
    except Exception as exc:   # noqa: BLE001 - fall back to the transcript-based edit
        console.print(f"[yellow]video editor skipped: {exc}[/]")
        return None
    finally:
        tmp.unlink(missing_ok=True)
