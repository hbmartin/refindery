# HTTP API

This reference is generated at build time from FastAPI's OpenAPI document. Its
endpoint inventory, parameters, request and response types, validation
constraints, status codes, and component schemas come directly from route
metadata, docstrings, and type hints.

Bearer authentication is required unless an operation is marked `public`.
Write-scoped routes are also marked 🔒. See
[Authentication](../configuration/auth.md) for token configuration and scope
semantics. The server binds to `127.0.0.1:8000` by default.

The **ingest** workflow also has a task-oriented contract in the
[Upstream ingest API](upstream-ingest-api.md); that guide adds retry,
idempotency, and integration advice around the generated wire reference below.

{{ http_api_reference() }}

## Related

- [Upstream ingest API](upstream-ingest-api.md) — ingest semantics, revisit behavior, and integration guidance.
- [Python API → Services](python-api/services.md) — the application objects behind these routes.
- [Observability](../configuration/observability.md) — metrics, tracing, and query-log operations.
