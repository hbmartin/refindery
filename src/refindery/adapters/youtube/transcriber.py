"""Local Whisper transcribers: mlx-whisper (Apple Silicon) or faster-whisper.

Both are optional extras, lazily imported, and run blocking inference in a
worker thread. ffmpeg is a system dependency of the audio decode path.
"""

import asyncio
import logging
import platform
from importlib.util import find_spec
from pathlib import Path

from refindery.adapters.youtube.envelope import YOUTUBE_TRANSCRIPT_CONTENT_TYPE
from refindery.application.ports.transcriber import Transcriber
from refindery.domain.errors import ExtractionUnavailableError

logger = logging.getLogger(__name__)


def _apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


class MlxWhisperTranscriber:
    """Whisper via MLX; Apple Silicon only (``transcribe-mlx`` extra)."""

    def __init__(self, *, model: str = "small") -> None:
        if not _apple_silicon() or find_spec("mlx_whisper") is None:
            raise ExtractionUnavailableError(
                content_type=YOUTUBE_TRANSCRIPT_CONTENT_TYPE, extra="transcribe-mlx"
            )
        self._repo = f"mlx-community/whisper-{model}-mlx"

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> str:
        """Transcribe the audio file; model weights download on first use."""
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language)

    def _transcribe_sync(self, audio_path: Path, language: str | None) -> str:
        import mlx_whisper  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        result = mlx_whisper.transcribe(
            str(audio_path), path_or_hf_repo=self._repo, language=language
        )
        return str(result.get("text", "")).strip()


class FasterWhisperTranscriber:
    """Whisper via CTranslate2 (``transcribe`` extra); model cached per instance."""

    def __init__(self, *, model: str = "small") -> None:
        if find_spec("faster_whisper") is None:
            raise ExtractionUnavailableError(
                content_type=YOUTUBE_TRANSCRIPT_CONTENT_TYPE, extra="transcribe"
            )
        self._model_name = model
        self._model: object | None = None

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> str:
        """Transcribe the audio file; the model loads lazily and is reused."""
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language)

    def _transcribe_sync(self, audio_path: Path, language: str | None) -> str:
        import faster_whisper  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        if self._model is None:
            self._model = faster_whisper.WhisperModel(self._model_name)
        segments, _info = self._model.transcribe(str(audio_path), language=language)  # ty: ignore[unresolved-attribute]  # pyrefly: ignore[missing-attribute]
        return "\n".join(segment.text.strip() for segment in segments).strip()


def build_transcriber(*, model: str) -> Transcriber | None:
    """Best available local transcriber, or None when no extra is installed."""
    for factory in (MlxWhisperTranscriber, FasterWhisperTranscriber):
        try:
            transcriber = factory(model=model)
        except ExtractionUnavailableError:
            continue
        logger.info("whisper transcriber: %s(%r)", factory.__name__, model)
        return transcriber
    return None
