"""Timestamp preservation and validation for local Whisper adapters."""

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

import pytest

from refindery.adapters.transcription import whisper


def _available_spec(name: str) -> ModuleSpec:
    return ModuleSpec(name=name, loader=None)


async def test_mlx_whisper_preserves_and_orders_segment_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("mlx_whisper")

    def transcribe(
        audio_path: str,
        *,
        path_or_hf_repo: str,
        language: str | None,
    ) -> dict[str, object]:
        assert audio_path == "audio.m4a"
        assert path_or_hf_repo == "mlx-community/whisper-small-mlx"
        assert language == "en"
        return {
            "text": "provider formatting is replaced by stable segments",
            "segments": [
                {"text": " second ", "start": 10.0, "end": 20.0},
                {"text": " first ", "start": 0.0, "end": 9.5},
            ],
        }

    monkeypatch.setattr(module, "transcribe", transcribe, raising=False)
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    monkeypatch.setattr(whisper, "_apple_silicon", lambda: True)
    monkeypatch.setattr(whisper, "find_spec", _available_spec)

    result = await whisper.MlxWhisperTranscriber().transcribe(
        Path("audio.m4a"), language="en"
    )

    assert result.text == "first\nsecond"
    assert result.timed_offsets == ((0, 0.0), (6, 10.0))
    assert result.segments[1].end_time_s == 20.0


async def test_mlx_whisper_rejects_non_finite_segment_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("mlx_whisper")
    monkeypatch.setattr(
        module,
        "transcribe",
        lambda *_args, **_kwargs: {
            "text": "bad",
            "segments": [{"text": "bad", "start": float("nan"), "end": 1.0}],
        },
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    monkeypatch.setattr(whisper, "_apple_silicon", lambda: True)
    monkeypatch.setattr(whisper, "find_spec", _available_spec)

    with pytest.raises(ValueError, match="invalid mlx-whisper transcription result"):
        await whisper.MlxWhisperTranscriber().transcribe(Path("audio.m4a"))


@dataclass(frozen=True, slots=True)
class _RawSegment:
    text: str
    start: float
    end: float


class _FakeWhisperModel:
    def __init__(self, model: str) -> None:
        assert model == "small"

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str | None,
    ) -> tuple[Iterator[_RawSegment], object]:
        assert audio_path == "audio.m4a"
        assert language == "en"
        segments = iter(
            [
                _RawSegment(text=" first ", start=0.0, end=4.0),
                _RawSegment(text=" second ", start=5.0, end=9.0),
            ]
        )
        return segments, object()


async def test_faster_whisper_preserves_segment_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("faster_whisper")
    monkeypatch.setattr(module, "WhisperModel", _FakeWhisperModel, raising=False)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(whisper, "find_spec", _available_spec)

    result = await whisper.FasterWhisperTranscriber().transcribe(
        Path("audio.m4a"), language="en"
    )

    assert result.text == "first\nsecond"
    assert result.timed_offsets == ((0, 0.0), (6, 5.0))


async def test_faster_whisper_rejects_reversed_segment_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadWhisperModel(_FakeWhisperModel):
        def transcribe(
            self,
            audio_path: str,  # noqa: ARG002 - malformed provider result fixture
            *,
            language: str | None,  # noqa: ARG002 - malformed provider result fixture
        ) -> tuple[Iterator[_RawSegment], object]:
            return iter([_RawSegment(text="bad", start=2.0, end=1.0)]), object()

    module = ModuleType("faster_whisper")
    monkeypatch.setattr(module, "WhisperModel", BadWhisperModel, raising=False)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(whisper, "find_spec", _available_spec)

    with pytest.raises(ValueError, match="invalid faster-whisper transcription result"):
        await whisper.FasterWhisperTranscriber().transcribe(
            Path("audio.m4a"), language="en"
        )
