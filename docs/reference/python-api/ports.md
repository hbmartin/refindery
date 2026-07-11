# Ports

Ports are the `Protocol` contracts that isolate the domain and application
layers from infrastructure. Every adapter under `refindery.adapters.*`
implements one of these; the [composition root](../../architecture/index.md)
wires the selected adapter behind its port. Because they are `Protocol`s, no
adapter type ever leaks into `domain/` or `application/`.

The module inventory below is discovered from `refindery/application/ports` at
build time, so adding a port automatically adds it to this page.

{{ python_api_reference("refindery.application.ports") }}
