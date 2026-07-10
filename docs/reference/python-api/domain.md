# Domain models

`refindery.domain.models` holds the pure domain types — plain dataclasses and
enums with no I/O — that every layer speaks in: pages and chunks, embedding
models, jobs, clusters, entities, and the ingest-outcome results. For the SQL
shape these map onto, see the [Data model](../../architecture/data-model.md).

::: refindery.domain.models
    options:
      show_root_heading: false
      members_order: source
      show_if_no_docstring: true
      filters:
        - "!^_"
