# Python API

These pages are generated directly from the source docstrings and type
annotations with [mkdocstrings](https://mkdocstrings.github.io/), so they track
the code. They document the **stable internal seams** of Refindery — the
configuration model, the domain types, the port protocols, and the headline
application services — for contributors and for anyone embedding Refindery as a
library.

!!! note "Generated statically"
    The Python handler analyzes the source with
    [griffe](https://mkdocstrings.github.io/griffe/) — it does not import the
    modules — so rendering the reference never triggers the optional heavy
    dependencies (`torch`, `gliner`, …) that Refindery imports lazily.

## Layout

| Page | Module(s) | What it covers |
| --- | --- | --- |
| [Configuration](config.md) | `refindery.config` | `Settings` and every nested settings group. |
| [Domain models](domain.md) | `refindery.domain.models` | Pages, chunks, jobs, clusters, entities, enums. |
| [Ports](ports.md) | `refindery.application.ports.*` | The `Protocol` contracts every adapter implements. |
| [Services](services.md) | `refindery.application.services.*` | The use-case orchestration layer. |

For the architectural picture these types live inside — the hexagonal layering,
the composition root, and the data flow — start with the
[Architecture overview](../../architecture/index.md).
