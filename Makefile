# Convenience targets for the Qdrant conformance suite. Everything else in
# this repo runs through plain `uv run ...` (see CONTRIBUTING.md).

QDRANT_URL ?= http://127.0.0.1:6333

.PHONY: test-qdrant test-qdrant-local qdrant-down

test-qdrant:  ## conformance against a real Qdrant via docker compose
	docker compose up -d qdrant
	@for i in $$(seq 1 60); do \
		curl -fsS $(QDRANT_URL)/readyz >/dev/null 2>&1 && break; \
		if [ $$i -eq 60 ]; then echo "qdrant not ready after 60s" && exit 1; fi; \
		sleep 1; \
	done
	QDRANT_URL=$(QDRANT_URL) uv run pytest -m qdrant tests/conformance

test-qdrant-local:  ## daemon-free smoke via qdrant-client's in-process mode
	QDRANT_URL=":memory:" uv run pytest -m qdrant tests/conformance

qdrant-down:  ## stop the compose qdrant (keeps its volume)
	docker compose stop qdrant
