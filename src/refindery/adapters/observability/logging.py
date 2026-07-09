"""Structured JSON logging over stdlib logging.

stdlib (not structlog): uvicorn, huey, and spacy all log through stdlib, so
one root-handler formatter yields uniform JSON with no bridging. Trace ids
are injected when an OpenTelemetry span is active.
"""

import json
import logging
import sys
from datetime import UTC, datetime

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message",
}  # fmt: skip


class JsonFormatter(logging.Formatter):
    """One JSON object per line, trace-correlated when a span is active."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as a JSON line."""
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED and not key.startswith("_")
            }
        )
        self._inject_trace(payload)
        return json.dumps(payload, default=str)

    @staticmethod
    def _inject_trace(payload: dict[str, object]) -> None:
        try:
            from opentelemetry import trace  # noqa: PLC0415 — optional at runtime

            context = trace.get_current_span().get_span_context()
            if context.is_valid:
                payload["trace_id"] = format(context.trace_id, "032x")
                payload["span_id"] = format(context.span_id, "016x")
        except ImportError:  # pragma: no cover
            return


def configure_logging(*, json_logs: bool = True, level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
