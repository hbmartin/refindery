"""Whisper transcription via mlx-whisper (Apple Silicon only).

Requires the ``transcribe-mlx`` extra and an Apple-Silicon host; otherwise the
constructor reports itself unavailable and the container falls back to
faster-whisper (or to no transcription). The model is downloaded and cached by
mlx-whisper on first use, so construction is cheap.
"""

import asyncio
import platform
from importlib import util as importlib_util
from pathlib import Path

from refindery.domain.errors import ExtractionUnavailableError

_EXTRA = "transcribe-mlx"


def _is_apple_silicon() -> bool:
    """Whether the host is an Apple-Silicon Mac (where MLX runs)."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def mlx_whisper_available() -> bool:
    """Whether MLX Whisper can run on this host."""
    return _is_apple_silicon() and importlib_util.find_spec("mlx_whisper") is not None


class MlxWhisperTranscriber:
    """Transcriber backed by mlx-whisper."""

    def __init__(self, *, model: str = "small") -> None:
        if not mlx_whisper_available():
            raise ExtractionUnavailableError(content_type="audio", extra=_EXTRA)
        # A bare size ("small") maps to the mlx-community repo; a value with a
        # slash is treated as a full Hugging Face repo id.
        self._repo = model if "/" in model else f"mlx-community/whisper-{model}-mlx"

    async def transcribe(self, audio: Path, *, language: str | None = None) -> str:
        """Transcribe ``audio`` to text off the event loop."""
        return await asyncio.to_thread(self._run, audio, language)

    def _run(self, audio: Path, language: str | None) -> str:
        import mlx_whisper  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        result = mlx_whisper.transcribe(
            str(audio), path_or_hf_repo=self._repo, language=language
        )
        text = result["text"] if isinstance(result, dict) else result
        return str(text).strip()
