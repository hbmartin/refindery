# Services

The application services orchestrate the use cases over the ports. They are
framework-free — no FastAPI, no SQL — so the same objects drive the HTTP routes,
the MCP tools, and the eval CLI. The [composition root](../../architecture/index.md)
constructs them in `build_container`.

The module inventory below is discovered from
`refindery/application/services` at build time. The conceptual guides explain
the main workflows: [search](../../guides/search.md),
[ingest](../../guides/ingest.md), [models](../../guides/models.md),
[clustering](../../guides/clustering.md), [deletion](../../guides/deletion.md),
and [evaluation](../../guides/eval.md).

{{ python_api_reference("refindery.application.services") }}
