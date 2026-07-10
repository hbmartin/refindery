# Configuration API

The `refindery.config` module defines the pydantic-settings model that backs
every application setting. `Settings` is the root; each nested `BaseModel` group
maps to an environment-variable namespace (`REFINDERY_<GROUP>__<FIELD>`). See
the [Configuration overview](../../configuration/index.md) for how the
environment mapping works, and the
[Settings reference](../../configuration/reference.md) for a task-oriented tour.

::: refindery.config
    options:
      show_root_heading: false
      members_order: source
      show_if_no_docstring: true
      filters:
        - "!^_"
