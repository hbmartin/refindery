"""Transcriber port: local speech-to-text over a downloaded audio file."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    """One timed, non-empty span returned by a speech-to-text provider."""

    text: str
    start_time_s: float
    end_time_s: float


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Normalized transcript plus provider-derived timed segments."""

    text: str
    segments: tuple[TranscriptionSegment, ...] = ()

    @property
    def timed_offsets(self) -> tuple[tuple[int, float], ...]:
        """Return segment character offsets when they align with ``text``."""
        offsets: list[tuple[int, float]] = []
        cursor = 0
        for segment in self.segments:
            if (char_start := self.text.find(segment.text, cursor)) < 0:
                return ()
            offsets.append((char_start, segment.start_time_s))
            cursor = char_start + len(segment.text)
        return tuple(offsets)


class Transcriber(Protocol):
    """Transcribes an audio file to normalized text and timed segments."""

    async def transcribe(
        self, audio_path: Path, *, language: str | None = None
    ) -> TranscriptionResult:
        """Return normalized transcription; raise on unreadable audio."""
        ...
