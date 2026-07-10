# Validate the install

A few checks confirm that configuration, the metadata store, the embedding
provider, the queue, and the retrieval path all work.

## 1. Print the resolved configuration

Validate settings and print them. Pydantic masks secret values in this output,
so it is safe to share when reporting a problem:

```bash
uv run --env-file .env python - <<'PY'
from refindery.config import load_settings

print(load_settings().model_dump_json(indent=2))
PY
```

If this raises, a setting is invalid — the error names the offending field. See
the [Settings reference](../configuration/reference.md).

## 2. Liveness and readiness

After starting the server, check the two health endpoints. They are
unauthenticated. **Liveness** (`/healthz`) says the process is up; **readiness**
(`/readyz`) additionally confirms the metadata store is reachable and an
embedding model is active (`503` until then).

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

## 3. End-to-end check

Run the [ingest and search](quickstart.md) requests. A page that reaches
`indexed` and a search that returns it exercises the whole path: queue →
embedding provider → vector store → retrieval.

!!! tip "If readiness never turns green"
    The most common cause is entity extraction having no healthy extractor at
    startup. Install the `ner` extra or configure another extractor chain — see
    [Entities](../guides/entities.md) and [Operations](../operations/index.md).
