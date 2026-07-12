"""Transcriber port: local speech-to-text over a downloaded audio file."""

from pathlib import Path
from typing import Protocol


class Transcriber(Protocol):
    """Transcribes an audio file to plain text."""

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> str:
        """Return the transcript text; raises on unreadable/undecodable audio."""
        ...
