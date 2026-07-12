"""Zensical macros that generate Refindery's API reference pages."""

from __future__ import annotations

import json
import re
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import TypeAdapter

from refindery.api.app import create_openapi_app
from refindery.api.mcp import MUTATING_OPERATIONS, READ_OPERATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]
type Operation = tuple[str, str, JsonObject]

_DOCUMENT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")
_PACKAGE_NAME = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


class MacroEnvironment(Protocol):
    """Subset of the Zensical macro environment used by this module."""

    def macro[**P](self, function: Callable[P, str]) -> Callable[P, str]:
        """Register a macro function."""


def _object(value: JsonValue | None, *, context: str) -> JsonObject:
    """Return a JSON object or fail with a useful schema location."""
    if not isinstance(value, dict):
        msg = f"OpenAPI {context} must be an object"
        raise TypeError(msg)
    return value


def _list(value: JsonValue | None, *, context: str) -> list[JsonValue]:
    """Return a JSON list or fail with a useful schema location."""
    if not isinstance(value, list):
        msg = f"OpenAPI {context} must be a list"
        raise TypeError(msg)
    return value


def _text(value: JsonValue | None, *, default: str = "") -> str:
    """Return a string value or a supplied default."""
    return value if isinstance(value, str) else default


def _string_list(value: JsonValue | None, *, context: str) -> list[str]:
    """Validate a JSON array containing only strings."""
    if value is None:
        return []
    values = _list(value, context=context)
    if not all(isinstance(item, str) for item in values):
        msg = f"OpenAPI {context} must contain only strings"
        raise TypeError(msg)
    return [item for item in values if isinstance(item, str)]


def _openapi_document() -> JsonObject:
    """Build and validate the canonical OpenAPI document."""
    return _DOCUMENT_ADAPTER.validate_python(create_openapi_app().openapi())


def _operations(document: JsonObject) -> list[Operation]:
    """Return documented operations in FastAPI registration order."""
    paths = _object(document.get("paths"), context="paths")
    operations: list[Operation] = []
    for path, raw_path_item in paths.items():
        path_item = _object(raw_path_item, context=f"path {path}")
        for method in _HTTP_METHODS:
            if (raw_operation := path_item.get(method)) is None:
                continue
            operation = _object(
                raw_operation,
                context=f"operation {method.upper()} {path}",
            )
            operations.append((method.upper(), path, operation))
    return operations


def _anchor(name: str) -> str:
    """Return a stable explicit Markdown anchor for a schema name."""
    return f"schema-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"


def _schema_type(schema: JsonObject) -> str:  # noqa: PLR0911
    """Render an OpenAPI schema as a compact linked type expression."""
    if ref := _text(schema.get("$ref")):
        name = ref.rsplit("/", maxsplit=1)[-1]
        return f"[`{name}`](#{_anchor(name)})"

    for keyword, separator in (
        ("oneOf", " &#124; "),
        ("anyOf", " &#124; "),
        ("allOf", " & "),
    ):
        if raw_variants := schema.get(keyword):
            variants = _list(raw_variants, context=keyword)
            return separator.join(
                _schema_type(_object(item, context=f"{keyword} item"))
                for item in variants
            )

    if enum := schema.get("enum"):
        values = _list(enum, context="enum")
        return " &#124; ".join(f"`{_json_literal(value)}`" for value in values)
    if "const" in schema:
        return f"`{_json_literal(schema['const'])}`"

    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        return " &#124; ".join(str(item) for item in raw_type)
    schema_type = _text(raw_type, default="object")
    if schema_type == "array":
        item_schema = _object(schema.get("items", {}), context="array items")
        return f"array&lt;{_schema_type(item_schema)}&gt;"
    if schema_type == "object" and isinstance(schema.get("additionalProperties"), dict):
        value_schema = _object(
            schema.get("additionalProperties"),
            context="additionalProperties",
        )
        return f"object[string, {_schema_type(value_schema)}]"
    if schema_format := _text(schema.get("format")):
        return f"{schema_type} (`{schema_format}`)"
    return schema_type


def _json_literal(value: JsonValue) -> str:
    """Serialize one JSON value for compact documentation."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _table_text(value: str) -> str:
    """Escape arbitrary prose for a Markdown table cell."""
    return value.replace("|", "\\|").replace("\n", "<br>") or "—"


def _schema_notes(schema: JsonObject) -> str:
    """Render defaults, examples, and JSON Schema constraints."""
    notes: list[str] = []
    if "default" in schema:
        notes.append(f"Default: `{_json_literal(schema['default'])}`")
    if "example" in schema:
        notes.append(f"Example: `{_json_literal(schema['example'])}`")
    if "examples" in schema:
        examples = _list(schema["examples"], context="schema examples")
        if examples:
            rendered = ", ".join(f"`{_json_literal(item)}`" for item in examples)
            notes.append(f"Examples: {rendered}")
    if schema.get("deprecated") is True:
        notes.append("Deprecated")
    labels = {
        "minimum": "minimum",
        "exclusiveMinimum": "exclusive minimum",
        "maximum": "maximum",
        "exclusiveMaximum": "exclusive maximum",
        "minLength": "minimum length",
        "maxLength": "maximum length",
        "minItems": "minimum items",
        "maxItems": "maximum items",
        "pattern": "pattern",
    }
    for keyword, label in labels.items():
        if keyword in schema:
            notes.append(f"{label.title()}: `{_json_literal(schema[keyword])}`")
    return "; ".join(notes)


def _parameter_table(operation: JsonObject) -> str:
    """Render an operation's path, query, header, and cookie parameters."""
    raw_parameters = operation.get("parameters")
    if raw_parameters is None:
        return ""
    parameters = _list(raw_parameters, context="operation parameters")
    rows: list[str] = []
    for raw_parameter in parameters:
        parameter = _object(raw_parameter, context="parameter")
        schema = _object(parameter.get("schema", {}), context="parameter schema")
        description = _text(parameter.get("description"))
        notes = "; ".join(filter(None, (description, _schema_notes(schema))))
        rows.append(
            "| "
            f"`{_text(parameter.get('name'), default='?')}` | "
            f"{_text(parameter.get('in'), default='?')} | "
            f"{_schema_type(schema)} | "
            f"{'yes' if parameter.get('required') is True else 'no'} | "
            f"{_table_text(notes)} |"
        )
    if not rows:
        return ""
    return "\n".join(
        (
            "##### Parameters",
            "",
            "| Name | In | Type | Required | Description and constraints |",
            "| --- | --- | --- | --- | --- |",
            *rows,
            "",
        )
    )


def _request_body(operation: JsonObject) -> str:
    """Render every declared request-body media type and schema."""
    raw_body = operation.get("requestBody")
    if raw_body is None:
        return ""
    body = _object(raw_body, context="requestBody")
    content = _object(body.get("content", {}), context="requestBody content")
    rows: list[str] = []
    for media_type, raw_media in content.items():
        media = _object(raw_media, context=f"request media type {media_type}")
        schema = _object(media.get("schema", {}), context="request schema")
        rows.append(f"| `{media_type}` | {_schema_type(schema)} |")
    required = "Required." if body.get("required") is True else "Optional."
    description = _text(body.get("description"))
    prose = " ".join(filter(None, (required, description)))
    return "\n".join(
        (
            "##### Request body",
            "",
            prose,
            "",
            "| Media type | Schema |",
            "| --- | --- |",
            *rows,
            "",
        )
    )


def _response_schema(response: JsonObject) -> str:
    """Return all media-type schemas for one response."""
    raw_content = response.get("content")
    if raw_content is None:
        return "—"
    content = _object(raw_content, context="response content")
    labels: list[str] = []
    for media_type, raw_media in content.items():
        media = _object(raw_media, context=f"response media type {media_type}")
        schema = _object(media.get("schema", {}), context="response schema")
        labels.append(f"`{media_type}`: {_schema_type(schema)}")
    return "<br>".join(labels) or "—"


def _response_table(operation: JsonObject) -> str:
    """Render all documented status codes and their response schemas."""
    responses = _object(operation.get("responses", {}), context="responses")
    rows: list[str] = []
    for status_code, raw_response in responses.items():
        response = _object(raw_response, context=f"response {status_code}")
        rows.append(
            f"| `{status_code}` | "
            f"{_table_text(_text(response.get('description')))} | "
            f"{_response_schema(response)} |"
        )
    return "\n".join(
        (
            "##### Responses",
            "",
            "| Status | Description | Body |",
            "| --- | --- | --- |",
            *rows,
            "",
        )
    )


def _endpoint_section(method: str, path: str, operation: JsonObject) -> str:
    """Render one complete OpenAPI operation."""
    summary = _text(operation.get("summary"), default="Untitled operation")
    description = _text(operation.get("description"))
    operation_id = _text(operation.get("operationId"), default="unknown")
    scope = _text(operation.get("x-required-scope"), default="public")
    lock = " 🔒" if scope == "write" else ""
    metadata = f"**Operation ID:** `{operation_id}` · **Required scope:** `{scope}`"
    deprecated = '\n\n!!! warning "Deprecated"\n    This operation is deprecated.'
    if operation.get("deprecated") is not True:
        deprecated = ""
    return "\n".join(
        filter(
            None,
            (
                f"#### `{method} {path}` — {summary}{lock}",
                "",
                description,
                "",
                metadata,
                deprecated,
                "",
                _parameter_table(operation),
                _request_body(operation),
                _response_table(operation),
            ),
        )
    )


def _endpoint_map(operations: list[Operation]) -> str:
    """Render a compact, exhaustive endpoint map."""
    path_width = max(len(path) for _, path, _ in operations)
    lines = []
    for method, path, operation in operations:
        summary = _text(operation.get("summary"), default="Untitled operation")
        scope = _text(operation.get("x-required-scope"), default="public")
        lock = "  🔒" if scope == "write" else ""
        lines.append(f"{method:<6} {path:<{path_width}}  {summary}{lock}".rstrip())
    return "\n".join(("## Endpoint map", "", "```text", *lines, "```", ""))


def _schema_section(name: str, schema: JsonObject) -> str:
    """Render one reusable component schema and all of its fields."""
    description = _text(schema.get("description"))
    properties = _object(schema.get("properties", {}), context=f"schema {name}")
    required = set(
        _string_list(schema.get("required"), context=f"schema {name} required")
    )
    lines = [f"### `{name}` {{ #{_anchor(name)} }}", ""]
    if description:
        lines.extend((description, ""))
    if not properties:
        lines.extend((f"Type: {_schema_type(schema)}", ""))
        return "\n".join(lines)
    lines.extend(
        (
            "| Field | Type | Required | Description and constraints |",
            "| --- | --- | --- | --- |",
        )
    )
    for field_name, raw_field in properties.items():
        field = _object(raw_field, context=f"schema {name}.{field_name}")
        field_description = _text(field.get("description"))
        notes = "; ".join(filter(None, (field_description, _schema_notes(field))))
        lines.append(
            f"| `{field_name}` | {_schema_type(field)} | "
            f"{'yes' if field_name in required else 'no'} | "
            f"{_table_text(notes)} |"
        )
    lines.append("")
    return "\n".join(lines)


def http_api_reference() -> str:
    """Generate the complete HTTP reference from FastAPI's OpenAPI document."""
    document = _openapi_document()
    operations = _operations(document)
    by_tag: dict[str, list[Operation]] = {}
    for operation in operations:
        tags = _string_list(
            operation[2].get("tags"),
            context=f"tags for {operation[0]} {operation[1]}",
        )
        by_tag.setdefault(tags[0] if tags else "other", []).append(operation)

    sections = [_endpoint_map(operations), "## Endpoint details\n"]
    for tag, tagged_operations in by_tag.items():
        sections.append(f"### {tag.replace('_', ' ').title()}\n")
        sections.extend(
            _endpoint_section(method, path, operation)
            for method, path, operation in tagged_operations
        )

    components = _object(document.get("components", {}), context="components")
    schemas = _object(components.get("schemas", {}), context="components.schemas")
    sections.append("## Schemas\n")
    sections.extend(
        _schema_section(name, _object(schema, context=f"schema {name}"))
        for name, schema in sorted(schemas.items())
    )
    return "\n".join(sections)


def _module_names(package_name: str, *, recursive: bool) -> list[str]:
    """Discover public source modules without importing their dependencies."""
    if not _PACKAGE_NAME.fullmatch(package_name):
        msg = f"invalid Python package name: {package_name!r}"
        raise ValueError(msg)
    spec = find_spec(package_name)
    if spec is None:
        msg = f"cannot find Python package: {package_name}"
        raise ModuleNotFoundError(msg)
    locations = spec.submodule_search_locations
    if not recursive or locations is None:
        return [package_name]

    modules: set[str] = set()
    for raw_location in locations:
        location = Path(raw_location)
        for source in location.rglob("*.py"):
            relative = source.relative_to(location)
            if source.name == "__init__.py" or any(
                part.startswith("_") for part in relative.parts
            ):
                continue
            suffix = ".".join(relative.with_suffix("").parts)
            modules.add(f"{package_name}.{suffix}")
    return sorted(modules)


def python_api_reference(package_name: str, *, recursive: bool = True) -> str:
    """Generate mkdocstrings directives for every public source module."""
    directives = [
        "\n".join(
            (
                f"::: {module_name}",
                "    options:",
                "      show_root_heading: true",
                "      members_order: source",
                "      show_if_no_docstring: true",
                "      filters:",
                '        - "!^_"',
                "",
            )
        )
        for module_name in _module_names(package_name, recursive=recursive)
    ]
    return "\n".join(directives)


def mcp_tools_reference(kind: str) -> str:
    """Generate an MCP tool table from configured OpenAPI operation IDs."""
    match kind:
        case "read":
            operation_ids = READ_OPERATIONS
        case "mutating":
            operation_ids = MUTATING_OPERATIONS
        case _:
            msg = f"unknown MCP tool kind: {kind!r}"
            raise ValueError(msg)

    index = {
        _text(operation.get("operationId")): (method, path, operation)
        for method, path, operation in _operations(_openapi_document())
    }
    rows = []
    for operation_id in operation_ids:
        if operation_id not in index:
            msg = f"MCP tool {operation_id!r} has no matching OpenAPI operation"
            raise KeyError(msg)
        method, path, operation = index[operation_id]
        purpose = _text(
            operation.get("description"),
            default=_text(operation.get("summary")),
        )
        rows.append(
            f"| `{operation_id}` | `{method} {path}` | {_table_text(purpose)} |"
        )
    return "\n".join(
        (
            "| Tool | Backing route | Purpose |",
            "| --- | --- | --- |",
            *rows,
        )
    )


def define_env(env: MacroEnvironment) -> None:
    """Register documentation macros with Zensical."""
    env.macro(http_api_reference)
    env.macro(mcp_tools_reference)
    env.macro(python_api_reference)
