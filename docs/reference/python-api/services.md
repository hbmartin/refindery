# Services

The application services orchestrate the use cases over the ports. They are
framework-free — no FastAPI, no SQL — so the same objects drive the HTTP routes,
the MCP tools, and the eval CLI. The [composition root](../../architecture/index.md)
constructs them in `build_container`.

## SearchService

The hybrid retrieval pipeline. See the [Searching guide](../../guides/search.md)
for the conceptual walkthrough.

::: refindery.application.services.search_service.SearchService
    options:
      show_root_heading: true
      filters: ["!^_"]

## IngestService

The single entry point for new pages. See the
[Ingesting pages guide](../../guides/ingest.md).

::: refindery.application.services.ingest.IngestService
    options:
      show_root_heading: true
      filters: ["!^_"]

## IndexingService

Executes the durable index/fetch-and-index jobs. See the
[Ingesting pages guide](../../guides/ingest.md).

::: refindery.application.services.indexing.IndexingService
    options:
      show_root_heading: true
      filters: ["!^_"]

## SimilarityService

Backs `similar_to` and the `suggestions` block. See the
[Searching guide](../../guides/search.md).

::: refindery.application.services.similarity_service.SimilarityService
    options:
      show_root_heading: true
      filters: ["!^_"]

## CompareService

A/B comparison across embedding models. See the
[Embedding models guide](../../guides/models.md).

::: refindery.application.services.compare_service.CompareService
    options:
      show_root_heading: true
      filters: ["!^_"]

## ClusterRunService

Orchestrates a clustering run. See the [Clustering guide](../../guides/clustering.md).

::: refindery.application.services.clustering_run.ClusterRunService
    options:
      show_root_heading: true
      filters: ["!^_"]

## ForgetService

Atomic purge + blacklist. See the [Deletion guide](../../guides/deletion.md).

::: refindery.application.services.forget_service.ForgetService
    options:
      show_root_heading: true
      filters: ["!^_"]

## ModelRegistry

Embedding-model registration rules and embedder lookup. See the
[Embedding models guide](../../guides/models.md).

::: refindery.application.services.model_registry.ModelRegistry
    options:
      show_root_heading: true
      filters: ["!^_"]

## EvalService

Offline scoring and replay over the query log. See the
[Evaluation guide](../../guides/eval.md).

::: refindery.application.services.eval_service.EvalService
    options:
      show_root_heading: true
      filters: ["!^_"]
