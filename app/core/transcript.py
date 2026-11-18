"""Transcript with word-level timestamps.

Primary: AssemblyAI (also labels speakers). Fallback when no key is set: a local Whisper
model, which is free but slower and has no speaker labels.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.core import media

SENTENCE_END = (".", "?", "!", "…")
MAX_SENTENCE_S = 14.0     # split run-on sentences so clip boundaries stay precise
PAUSE_S = 0.7


@dataclass
class Sentence:
    idx: int
    start: float
    end: float
    text: str
    speaker: str | None
    w0: int   # index of first word
    w1: int   # index after last word


def _assemblyai(audio: Path) -> list[dict]:
    import assemblyai as aai
    aai.settings.api_key = get_settings().assemblyai_api_key
    # language detection: streamers are not always English (the GTA board has French ones)
    config = aai.TranscriptionConfig(speaker_labels=True, language_detection=True)
    t = aai.Transcriber().transcribe(str(audio), config)
    if t.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI failed: {t.error}")
    return [{"text": w.text, "start": w.start / 1000, "end": w.end / 1000,
             "speaker": getattr(w, "speaker", None)} for w in t.words]


def _local_whisper(audio: Path, model_size: str = "small.en") -> list[dict]:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # Decode with our own ffmpeg: faster-whisper's built-in decoder breaks on newer PyAV releases.
    import subprocess

    import numpy as np
    raw = subprocess.run([get_settings().ffmpeg, "-v", "error", "-i", str(audio), "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(samples, word_timestamps=True, vad_filter=True)
    return [{"text": w.word.strip(), "start": float(w.start), "end": float(w.end), "speaker": None}
            for seg in segments for w in (seg.words or []) if w.word.strip()]


def get_words(video_path: str | Path, workdir: Path) -> list[dict]:
    cache = workdir / "words.json"
    if cache.exists():
        return json.loads(cache.read_text())
    audio = media.extract_audio(video_path, workdir / "audio.m4a")
    words = _assemblyai(audio) if get_settings().assemblyai_api_key else _local_whisper(audio)
    cache.write_text(json.dumps(words, ensure_ascii=False))
    return words


def to_sentences(words: list[dict]) -> list[Sentence]:
    """Groups words into sentences: split on punctuation, speaker change, long pause or length."""
    out: list[Sentence] = []
    start_i = 0
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = (
            nxt is None
            or w["text"].endswith(SENTENCE_END)
            or nxt["speaker"] != w["speaker"]
            or nxt["start"] - w["end"] > PAUSE_S
            or w["end"] - words[start_i]["start"] > MAX_SENTENCE_S
        )
        if ends:
            chunk = words[start_i: i + 1]
            out.append(Sentence(len(out), chunk[0]["start"], chunk[-1]["end"],
                                " ".join(x["text"] for x in chunk), chunk[0]["speaker"],
                                start_i, i + 1))
            start_i = i + 1
    return out


def fmt_time(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def render_for_llm(sentences: list[Sentence]) -> str:
    """One numbered line per sentence. The LLM answers with line numbers, never raw times."""
    return "\n".join(
        f"[{s.idx}] {fmt_time(s.start)} {('Speaker ' + s.speaker + ': ') if s.speaker else ''}{s.text}"
        for s in sentences)
