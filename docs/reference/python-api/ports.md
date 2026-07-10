# Ports

Ports are the `Protocol` contracts that isolate the domain and application
layers from infrastructure. Every adapter under `refindery.adapters.*`
implements one of these; the [composition root](../../architecture/index.md)
wires the selected adapter behind its port. Because they are `Protocol`s, no
adapter type ever leaks into `domain/` or `application/`.

## VectorStore

::: refindery.application.ports.vector_store
    options:
      show_root_heading: false
      filters: ["!^_"]

## MetadataStore

::: refindery.application.ports.metadata_store
    options:
      show_root_heading: false
      filters: ["!^_"]

## Embedder

::: refindery.application.ports.embedder
    options:
      show_root_heading: false
      filters: ["!^_"]

## Reranker

::: refindery.application.ports.reranker
    options:
      show_root_heading: false
      filters: ["!^_"]

## EntityExtractor

::: refindery.application.ports.entity_extractor
    options:
      show_root_heading: false
      filters: ["!^_"]

## ClusterEngine

::: refindery.application.ports.cluster_engine
    options:
      show_root_heading: false
      filters: ["!^_"]

## Chunker

::: refindery.application.ports.chunker
    options:
      show_root_heading: false
      filters: ["!^_"]

## JobQueue

::: refindery.application.ports.job_queue
    options:
      show_root_heading: false
      filters: ["!^_"]

## Query log

::: refindery.application.ports.query_log
    options:
      show_root_heading: false
      filters: ["!^_"]

## Clock

::: refindery.application.ports.clock
    options:
      show_root_heading: false
      filters: ["!^_"]
