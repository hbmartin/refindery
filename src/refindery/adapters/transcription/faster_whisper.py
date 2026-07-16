"""Whisper transcription via faster-whisper (cross-platform, CTranslate2).

Requires the ``transcribe`` extra. Used everywhere MLX is unavailable
(Linux, Intel Macs, CI). CTranslate2 releases the GIL during inference, so the
blocking call runs in a worker thread. The model is loaded once per instance on
first use.
"""

import asyncio
from importlib import util as importlib_util
from pathlib import Path

from refindery.domain.errors import ExtractionUnavailableError

_EXTRA = "transcribe"


def faster_whisper_available() -> bool:
    """Whether the faster-whisper extra is installed."""
    return importlib_util.find_spec("faster_whisper") is not None


class FasterWhisperTranscriber:
    """Transcriber backed by faster-whisper."""

    def __init__(self, *, model: str = "small") -> None:
        if not faster_whisper_available():
            raise ExtractionUnavailableError(content_type="audio", extra=_EXTRA)
        self._model_size = model
        self._model = None

    async def transcribe(self, audio: Path, *, language: str | None = None) -> str:
        """Transcribe ``audio`` to text off the event loop."""
        return await asyncio.to_thread(self._run, audio, language)

    def _run(self, audio: Path, language: str | None) -> str:
        model = self._model
        if model is None:
            from faster_whisper import (  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]
                WhisperModel,
            )

            model = WhisperModel(
                self._model_size, device="auto", compute_type="default"
            )
            self._model = model
        segments, _info = model.transcribe(str(audio), language=language)
        return " ".join(segment.text.strip() for segment in segments).strip()
