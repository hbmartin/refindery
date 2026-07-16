"""Local Whisper transcribers: mlx-whisper (Apple Silicon) or faster-whisper.

Both are optional extras, lazily imported, and run blocking inference in a
worker thread. ffmpeg is a system dependency of the audio decode path.
"""

import asyncio
import logging
import platform
from collections.abc import Iterable
from importlib.util import find_spec
from pathlib import Path
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    ValidationError,
    model_validator,
)

from refindery.adapters.transcription.envelope import AUDIO_TRANSCRIPT_CONTENT_TYPE
from refindery.application.ports.transcriber import (
    Transcriber,
    TranscriptionResult,
    TranscriptionSegment,
)
from refindery.domain.errors import ExtractionUnavailableError

logger = logging.getLogger(__name__)


class _ProviderSegment(BaseModel):
    """Validated segment shape shared by MLX and faster-whisper results."""

    model_config = ConfigDict(extra="ignore", from_attributes=True, strict=True)

    text: str
    start: FiniteFloat = Field(ge=0)
    end: FiniteFloat = Field(ge=0)

    @model_validator(mode="after")
    def _ordered_times(self) -> Self:
        if self.end < self.start:
            msg = "transcription segment end must not precede its start"
            raise ValueError(msg)
        return self


class _MlxResult(BaseModel):
    """Validated subset of the untyped mlx-whisper result mapping."""

    model_config = ConfigDict(extra="ignore", strict=True)

    text: str = ""
    segments: list[_ProviderSegment] = Field(default_factory=list)


def _apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


class MlxWhisperTranscriber:
    """Whisper via MLX; Apple Silicon only (``transcribe-mlx`` extra)."""

    def __init__(self, *, model: str = "small") -> None:
        if not _apple_silicon() or find_spec("mlx_whisper") is None:
            raise ExtractionUnavailableError(
                content_type=AUDIO_TRANSCRIPT_CONTENT_TYPE, extra="transcribe-mlx"
            )
        self._repo = f"mlx-community/whisper-{model}-mlx"

    async def transcribe(
        self, audio_path: Path, *, language: str | None = None
    ) -> TranscriptionResult:
        """Transcribe the audio file; model weights download on first use."""
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language)

    def _transcribe_sync(
        self, audio_path: Path, language: str | None
    ) -> TranscriptionResult:
        import mlx_whisper  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        raw_result = mlx_whisper.transcribe(
            str(audio_path), path_or_hf_repo=self._repo, language=language
        )
        try:
            result = _MlxResult.model_validate(raw_result)
        except ValidationError as exc:
            msg = f"invalid mlx-whisper transcription result: {exc}"
            raise ValueError(msg) from exc
        return _normalize_result(fallback_text=result.text, segments=result.segments)


class FasterWhisperTranscriber:
    """Whisper via CTranslate2 (``transcribe`` extra); model cached per instance."""

    def __init__(self, *, model: str = "small") -> None:
        if find_spec("faster_whisper") is None:
            raise ExtractionUnavailableError(
                content_type=AUDIO_TRANSCRIPT_CONTENT_TYPE, extra="transcribe"
            )
        self._model_name = model
        self._model: object | None = None

    async def transcribe(
        self, audio_path: Path, *, language: str | None = None
    ) -> TranscriptionResult:
        """Transcribe the audio file; the model loads lazily and is reused."""
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language)

    def _transcribe_sync(
        self, audio_path: Path, language: str | None
    ) -> TranscriptionResult:
        import faster_whisper  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        if self._model is None:
            self._model = faster_whisper.WhisperModel(self._model_name)
        raw_segments, _info = self._model.transcribe(str(audio_path), language=language)  # ty: ignore[unresolved-attribute]  # pyrefly: ignore[missing-attribute]
        segments = _validate_provider_segments(
            raw_segments,
            provider="faster-whisper",
        )
        return _normalize_result(fallback_text="", segments=segments)


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


def _validate_provider_segments(
    raw_segments: Iterable[object],
    *,
    provider: str,
) -> tuple[_ProviderSegment, ...]:
    """Validate provider-owned segment objects before normalization."""
    try:
        return tuple(
            _ProviderSegment.model_validate(segment) for segment in raw_segments
        )
    except ValidationError as exc:
        msg = f"invalid {provider} transcription result: {exc}"
        raise ValueError(msg) from exc


def _normalize_result(
    *,
    fallback_text: str,
    segments: Iterable[_ProviderSegment],
) -> TranscriptionResult:
    """Sort, trim, and map validated provider segments into the port result."""
    normalized = tuple(
        TranscriptionSegment(
            text=segment.text.strip(),
            start_time_s=float(segment.start),
            end_time_s=float(segment.end),
        )
        for segment in sorted(segments, key=lambda item: (item.start, item.end))
        if segment.text.strip()
    )
    text = "\n".join(segment.text for segment in normalized)
    return TranscriptionResult(
        text=text or fallback_text.strip(),
        segments=normalized,
    )
