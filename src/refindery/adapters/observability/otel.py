"""OpenTelemetry setup and the span helper.

Off by default: with no exporter configured the no-op tracer costs nothing.
``observability.traces`` selects console or OTLP-HTTP export (the standard
``OTEL_EXPORTER_OTLP_ENDPOINT`` is honored when no endpoint is set).

Span vocabulary: ingest, chunk, embed, vector.upsert, entity.extract,
entity.canonicalize, search, search.dense, search.sparse, search.fuse,
search.rerank, search.rollup, cluster.umap, cluster.hdbscan, cluster.match,
cluster.label. Process-pool stages report timings back to the parent, which
emits retroactive spans (contexts cannot cross the boundary).
"""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

from refindery.config import ObservabilitySettings, TraceExporter

_tracer = trace.get_tracer("refindery")


def configure_tracing(settings: ObservabilitySettings) -> None:
    """Install an SDK tracer provider when exporting is enabled."""
    global _tracer  # noqa: PLW0603 — process-wide tracing setup
    if settings.traces is TraceExporter.OFF:
        return
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import (  # noqa: PLC0415
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )

    provider = TracerProvider(resource=Resource.create({"service.name": "refindery"}))
    if settings.traces is TraceExporter.CONSOLE:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )

        exporter = (
            OTLPSpanExporter(endpoint=settings.otlp_endpoint)
            if settings.otlp_endpoint
            else OTLPSpanExporter()
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("refindery")


@contextmanager
def span(name: str, **attributes: str | float | bool) -> Iterator[None]:
    """Open a span (no-op when tracing is off)."""
    with _tracer.start_as_current_span(name, attributes=attributes):
        yield
