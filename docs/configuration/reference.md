# Settings reference

This is the complete settings model, generated from `refindery.config`. Each
group below is a nested settings object; its fields map to environment variables
as `REFINDERY_<GROUP>__<FIELD>` (see the [Configuration overview](index.md) for
the mapping rules). The `Settings` root also holds the top-level fields
(`auth_token`, `bind_host`, `bind_port`, `vector_store`, …).

::: refindery.config.Settings
    options:
      show_root_heading: true
      show_root_full_path: false
      members_order: source
      show_if_no_docstring: true
      filters:
        - "!^_"
