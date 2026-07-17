"""ASR sentence timeline helpers for highlight clipping.

The runtime ASR client returns word-level timestamps.  This module keeps the
pure logic for turning those words into sentence boundaries and remapping
episode-local sentences into a candidate span.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SENTENCE_ENDINGS = "。！？!?…."
DEFAULT_MAX_PAUSE_S = 0.8
DEFAULT_MAX_SENTENCE_S = 8.0
DEFAULT_MIN_TEXT_CHARS = 1


@dataclass(frozen=True)
class AsrSentence:
    text: str
    start_time: float
    end_time: float
    source: str = "asr"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start_time": round(self.start_time, 3),
            "end_time": round(self.end_time, 3),
            "source": self.source,
        }


@dataclass(frozen=True)
class AsrCorrection:
    original_start: float
    corrected_start: float
    sentence: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": round(self.original_start, 3),
            "to": round(self.corrected_start, 3),
            "sentence": self.sentence,
            "reason": self.reason,
        }


def normalize_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return sorted words with second-based start/end timestamps."""
    out: list[dict[str, Any]] = []
    for word in words:
        text = str(word.get("text") or word.get("word") or "").strip()
        if not text:
            continue
        try:
            start = float(word.get("start_time", word.get("start", 0.0)))
            end = float(word.get("end_time", word.get("end", start)))
        except (TypeError, ValueError):
            continue
        # ByteDance ASR words are millisecond-based.  Keep second-based inputs
        # untouched so tests and future providers can pass normalized data.
        if start > 1000 or end > 1000 or end - start > 20:
            start /= 1000.0
            end /= 1000.0
        if end < start:
            end = start
        out.append({"text": text, "start_time": start, "end_time": end})
    return sorted(out, key=lambda w: (w["start_time"], w["end_time"]))


def words_to_sentences(
    words: list[dict[str, Any]],
    *,
    max_pause_s: float = DEFAULT_MAX_PAUSE_S,
    max_sentence_s: float = DEFAULT_MAX_SENTENCE_S,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
) -> list[dict[str, Any]]:
    """Aggregate ASR words into sentence-level timeline entries."""
    normalized = normalize_words(words)
    if not normalized:
        return []

    sentences: list[AsrSentence] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = "".join(str(w["text"]) for w in current).strip()
        if len(text) >= min_text_chars:
            sentences.append(
                AsrSentence(
                    text=text,
                    start_time=float(current[0]["start_time"]),
                    end_time=float(current[-1]["end_time"]),
                )
            )
        current = []

    for word in normalized:
        if current:
            pause = float(word["start_time"]) - float(current[-1]["end_time"])
            duration = float(word["end_time"]) - float(current[0]["start_time"])
            if pause > max_pause_s or duration > max_sentence_s:
                flush()
        current.append(word)
        if str(word["text"]).strip().endswith(tuple(SENTENCE_ENDINGS)):
            flush()
    flush()
    return [sentence.to_dict() for sentence in sentences]


def find_sentence_containing(
    sentences: list[dict[str, Any]],
    time_s: float,
    *,
    tolerance_s: float = 0.05,
) -> dict[str, Any] | None:
    """Find the ASR sentence whose open interval contains ``time_s``."""
    for sentence in sentences:
        try:
            start = float(sentence.get("start_time", 0.0))
            end = float(sentence.get("end_time", start))
        except (TypeError, ValueError):
            continue
        if start + tolerance_s < time_s < end - tolerance_s:
            return sentence
    return None


def correct_start_to_sentence(
    *,
    candidate_start: float,
    current_start: float,
    content_start: float,
    sentences: list[dict[str, Any]],
    max_backtrack_s: float = 3.0,
) -> tuple[float, dict[str, Any] | None]:
    """Move a clip start back to the beginning of the containing ASR sentence."""
    sentence = find_sentence_containing(sentences, candidate_start)
    if sentence is None:
        return current_start, None
    sentence_start = float(sentence.get("start_time", current_start))
    lower_bound = max(0.0, content_start)
    corrected = max(lower_bound, sentence_start)
    if corrected >= current_start:
        return current_start, None
    if current_start - corrected > max_backtrack_s:
        corrected = current_start - max_backtrack_s
    correction = AsrCorrection(
        original_start=current_start,
        corrected_start=corrected,
        sentence=sentence,
        reason="candidate landed inside ASR sentence",
    )
    return round(corrected, 3), correction.to_dict()


def build_span_asr_timeline(
    *,
    start_episode_no: int,
    start_local: float,
    episode_refs: list[Any],
    sentences_by_episode: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Map episode-local ASR sentences into the candidate span timeline."""
    out: list[dict[str, Any]] = []
    real_offset = 0.0
    for index, ep in enumerate(episode_refs):
        ep_no = int(getattr(ep, "episode_no", 0))
        ep_duration = float(getattr(ep, "duration", 0.0))
        trim = start_local if index == 0 and ep_no == start_episode_no else 0.0
        for sentence in sentences_by_episode.get(ep_no, []):
            try:
                local_start = float(sentence.get("start_time", 0.0))
                local_end = float(sentence.get("end_time", local_start))
            except (TypeError, ValueError):
                continue
            if local_end <= trim:
                continue
            if local_start < trim:
                local_start = trim
            if ep_duration > 0:
                local_end = min(local_end, ep_duration)
            if local_end <= local_start:
                continue
            out.append({
                "episode_no": ep_no,
                "local_start": round(local_start, 3),
                "local_end": round(local_end, 3),
                "cum_start": round(real_offset + (local_start - trim), 3),
                "cum_end": round(real_offset + (local_end - trim), 3),
                "text": str(sentence.get("text") or ""),
                "source": str(sentence.get("source") or "asr"),
            })
        real_offset += max(0.0, ep_duration - trim)
    return out


def pick_asr_end_boundary(
    span_sentences: list[dict[str, Any]],
    *,
    window: tuple[float, float],
    ideal: float,
) -> dict[str, Any] | None:
    """Pick the ASR sentence end closest to ideal within the highlight window."""
    lo, hi = window
    candidates = [
        sentence
        for sentence in span_sentences
        if lo <= float(sentence.get("cum_end", 0.0)) <= hi
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda s: abs(float(s.get("cum_end", 0.0)) - ideal))
