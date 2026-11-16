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


class RecallResult(BaseModel):
    candidates: list[Candidate]


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


def recall(transcript: str, campaign: Campaign, duration_s: float, want: int = 30) -> list[Candidate]:
    client = _client()
    if client is None:
        return []
    s = get_settings()
    lim = campaign.clip
    prompt = (
        f"<transcript>\n{transcript}\n</transcript>\n\n"
        f"Campaign: {campaign.name}. Preferred styles: {campaign.preferences.get('styles', 'any')}.\n"
        f"The video is {duration_s / 60:.0f} minutes long. Each clip should run "
        f"{lim.min_seconds:.0f}-{lim.max_seconds:.0f} seconds (timestamps are at the start of "
        f"each line). Return up to {want} candidates, spread across the whole video.")
    resp = client.messages.parse(
        model=s.recall_model, max_tokens=16000, system=RECALL_SYSTEM,
        messages=[{"role": "user", "content": prompt}], output_format=RecallResult)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        console.print(f"[yellow]recall pass returned nothing ({resp.stop_reason})[/]")
        return []
    return resp.parsed_output.candidates


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
    caption_hook: str = Field(description="One caption line for the post, under 60 characters")
    reason: str = Field(description="One sentence: why this works or does not")
    ad_like: bool = Field(description="True if the clip reads like an advert or sponsor read")
    spoiler: bool = Field(description="True if it reveals a story spoiler for a game, film or show")


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
the campaign's length limits. The caption hook should tease the moment in the creator's voice \
without giving away the payoff, and without hashtags or @mentions (those are added later)."""


def judge(blocks: list[dict], campaign: Campaign) -> dict[int, Verdict]:
    """blocks: [{id, lines (numbered text incl. a little context), seconds, signals}]."""
    client = _client()
    if client is None or not blocks:
        return {}
    s = get_settings()
    parts = []
    for b in blocks:
        sig = ", ".join(f"{k} {v:.2f}" for k, v in b["signals"].items())
        parts.append(f'<candidate id="{b["id"]}" seconds="{b["seconds"]:.0f}" '
                     f'audience_signals="{sig}">\n{b["lines"]}\n</candidate>')
    lim = campaign.clip
    prompt = (
        "\n\n".join(parts) + "\n\n"
        f"Campaign: {campaign.name}. Preferences: {campaign.preferences}. "
        f"Not allowed: {campaign.forbid}. Clip length: {lim.min_seconds:.0f}-{lim.max_seconds:.0f}s.\n"
        "Audience signals are 0-1: heatmap is YouTube's most-replayed graph, comments are "
        "viewer timestamp mentions, loudness is audio peaks such as laughter. Lines marked "
        "(context) are outside the candidate but may be pulled in.\n"
        "Return one verdict per candidate.")
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
