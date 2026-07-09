"""HTML -> markdown extraction via the pulpie model (transformers).

Requires the ``html`` extra (torch, ~2 GB): without it this extractor
reports itself unavailable and ingest of ``body_html`` / fetched HTML fails
with a clear install hint. Inference runs in a ProcessPoolExecutor with the
model loaded once per worker (spawn context; the model object never crosses
the process boundary).
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from importlib import util as importlib_util
from multiprocessing import get_context

from refindery.domain.errors import ExtractionUnavailableError
from refindery.domain.models import ExtractedContent

MODEL_NAME = "feyninc/pulpie-orange-small"
_MAX_HTML_CHARS = 500_000

_pipeline: Callable[[str], object] | None = None


def _load_pipeline() -> Callable[[str], object]:
    """Load the pulpie pipeline once per worker process."""
    global _pipeline  # noqa: PLW0603 — per-process model cache
    if _pipeline is None:
        from transformers import pipeline  # noqa: PLC0415 — worker-only import

        _pipeline = pipeline(model=MODEL_NAME)  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # pyrefly: ignore
    return _pipeline


def _extract_markdown(html: str) -> str:
    """Worker-side: run the model over raw HTML, return markdown."""
    pipe = _load_pipeline()
    result = pipe(html[:_MAX_HTML_CHARS])
    if isinstance(result, str):
        return result
    if isinstance(result, list) and result and isinstance(result[0], dict):
        first = dict(result[0])
        for key in ("generated_text", "summary_text"):
            if isinstance(value := first.get(key), str):
                return value
    msg = f"unexpected pulpie pipeline output shape: {type(result)!r}"
    raise RuntimeError(msg)


def torch_available() -> bool:
    """Whether the html extra (torch) is installed."""
    return importlib_util.find_spec("torch") is not None


class PulpieHtmlExtractor:
    """ContentExtractor for text/html via pulpie."""

    def __init__(self, *, max_workers: int = 1) -> None:
        if not torch_available():
            raise ExtractionUnavailableError(content_type="text/html", extra="html")
        self._executor = ProcessPoolExecutor(
            max_workers=max_workers, mp_context=get_context("spawn")
        )

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({"text/html", "application/xhtml+xml"})

    async def extract(self, *, raw: bytes, charset: str | None) -> ExtractedContent:
        """Decode and extract markdown body text from HTML."""
        try:
            html = raw.decode(charset or "utf-8", errors="replace")
        except LookupError:
            html = raw.decode("utf-8", errors="replace")
        loop = asyncio.get_running_loop()
        markdown = await loop.run_in_executor(self._executor, _extract_markdown, html)
        return ExtractedContent(body_text=markdown)

    def close(self) -> None:
        """Shut the worker pool down."""
        self._executor.shutdown(wait=False, cancel_futures=True)
