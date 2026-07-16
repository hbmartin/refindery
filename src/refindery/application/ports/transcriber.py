"""Audio transcription port.

Used only as a fallback when a YouTube video has no captions: the fetcher
downloads the audio and hands the file to a ``Transcriber`` (Whisper).
"""

from pathlib import Path
from typing import Protocol


class Transcriber(Protocol):
    """Transcribes a local audio file to plain text."""

    async def transcribe(self, audio: Path, *, language: str | None = None) -> str:
        """Transcribe ``audio`` to text; ``language`` hints the spoken language."""
        ...
