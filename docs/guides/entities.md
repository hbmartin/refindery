# Entities

Refindery extracts named entities from every indexed page and canonicalizes them
**within your corpus** — no Wikidata, no external knowledge base. Entities power
the `entity` search filter, the `entity` similarity mediation, and
`GET /v1/entities/{id_or_form}`.

!!! important "Required at startup"
    Entity extraction is required for the server to start. If startup reports no
    healthy extractor, install the `ner` extra, configure a different chain,
    provide a gazetteer, or configure an LLM extractor (below).

## The extractor chain

The chain is an ordered **fallback**, not an ensemble: the first *healthy*
extractor handles each page; the next is tried only if it fails to load. The
default is GLiNER → spaCy → gazetteer.

```dotenv
REFINDERY_ENTITY__EXTRACTOR_CHAIN='["gliner", "spacy"]'
```

Install the extractors you reference:

```bash
uv sync --extra gliner --extra ner   # GLiNER, then spaCy fallback
uv sync --extra ner                  # spaCy only (default quickstart)
```

### Gazetteer extractor

A gazetteer needs no model dependency. Its file is JSONL, one validated entity
per line:

```jsonl
{"label":"technology","pattern":"Kubernetes"}
{"label":"product","pattern":"Refindery"}
```

```dotenv
REFINDERY_ENTITY__EXTRACTOR_CHAIN='["gazetteer"]'
REFINDERY_ENTITY__GAZETTEER_PATTERNS_PATH=/absolute/path/entities.jsonl
```

### LLM extractor

For an OpenAI-compatible entity endpoint, include `llm` in the chain and
configure `REFINDERY_LLM__BASE_URL`, `REFINDERY_LLM__MODEL`, and (when required)
`REFINDERY_LLM__API_KEY`. The endpoint must accept
`POST <base-url>/chat/completions`.

## The taxonomy

Every extractor emits one of a fixed set of labels, with character offsets:

`person` · `org` · `product` · `technology` · `concept` · `place` · `work`

## Canonicalization

Mentions are canonicalized incrementally as pages are indexed:

1. Normalize the surface form (casefold, strip punctuation/diacritics, singularize).
2. Exact-match against known aliases → link.
3. Otherwise block on first-token + type and match block members by embedding
   cosine **or** normalized edit distance.
4. Match → add an alias; no match → create a new entity.

Surface-form embeddings power the cosine step. They are **optional**: if they
fail to load, canonicalization still uses exact and edit-distance matching, and
cosine matching resumes once the embedder loads (see
[Operations](../operations/index.md)).

Periodically — with each [clustering run](clustering.md) — Refindery runs a full
re-canonicalization within blocks, merging entities and refreshing counts and
IDF. Merges change an entity's ID, so callers holding an `entity_id` should
prefer canonical-form lookups or re-resolve.

## Related

- [Searching](search.md) — the `entity` filter and `entity` similarity mediation.
- [Clustering](clustering.md) — triggers the periodic re-canonicalization.
- [Configuration overview](../configuration/index.md) — `REFINDERY_ENTITY__*` and `REFINDERY_LLM__*`.
