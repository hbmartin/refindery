"""Regression tests for generated documentation reference coverage."""

from pathlib import Path

import pytest

import docs_api_reference
from refindery.api.app import create_openapi_app
from refindery.api.mcp import MUTATING_OPERATIONS, READ_OPERATIONS


def test_python_reference_discovers_every_public_port_module() -> None:
    source = Path("src/refindery/application/ports")
    expected = {
        f"refindery.application.ports.{path.stem}"
        for path in source.glob("*.py")
        if path.name != "__init__.py" and not path.name.startswith("_")
    }

    generated = docs_api_reference.python_api_reference("refindery.application.ports")

    assert {
        line.removeprefix("::: ")
        for line in generated.splitlines()
        if line.startswith("::: ")
    } == expected


def test_http_reference_covers_every_operation_and_schema() -> None:
    spec = create_openapi_app().openapi()
    generated = docs_api_reference.http_api_reference()

    operations = [
        operation
        for path_item in spec["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete", "options", "head"}
    ]
    for operation in operations:
        assert f"**Operation ID:** `{operation['operationId']}`" in generated
    for schema_name in spec["components"]["schemas"]:
        assert f"### `{schema_name}`" in generated


def test_http_reference_preserves_markdown_table_columns() -> None:
    generated = docs_api_reference.http_api_reference()
    tables = [
        block
        for block in generated.split("\n\n")
        if block.startswith("|") and "\n| ---" in block
    ]

    assert tables
    for table in tables:
        rows = table.splitlines()
        expected_delimiters = rows[0].replace("\\|", "").count("|")
        assert all(
            row.replace("\\|", "").count("|") == expected_delimiters for row in rows
        )


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"oneOf": [{"type": "string"}, {"type": "null"}]}, "string &#124; null"),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, "integer &#124; null"),
        ({"enum": ["ready", "done"]}, '`"ready"` &#124; `"done"`'),
        ({"type": ["number", "null"]}, "number &#124; null"),
    ],
)
def test_schema_type_escapes_markdown_table_separators(
    schema: docs_api_reference.JsonObject,
    expected: str,
) -> None:
    assert docs_api_reference._schema_type(schema) == expected  # noqa: SLF001


def test_schema_notes_render_singular_and_plural_examples() -> None:
    notes = docs_api_reference._schema_notes(  # noqa: SLF001
        {"example": "one", "examples": ["two", {"nested": True}]}
    )

    assert notes == ('Example: `"one"`; Examples: `"two"`, `{"nested": true}`')


def test_schema_notes_reject_invalid_plural_examples() -> None:
    with pytest.raises(TypeError, match="OpenAPI schema examples must be a list"):
        docs_api_reference._schema_notes(  # noqa: SLF001
            {"examples": "not-a-list"}
        )


def test_openapi_documents_health_and_effective_auth_scopes() -> None:
    paths = create_openapi_app().openapi()["paths"]

    assert paths["/healthz"]["get"]["x-required-scope"] == "public"
    assert paths["/metrics"]["get"]["x-required-scope"] == "read"
    assert paths["/v1/search"]["post"]["x-required-scope"] == "read"
    assert paths["/v1/forget"]["post"]["x-required-scope"] == "write"


def test_mcp_reference_covers_configured_operations() -> None:
    read_reference = docs_api_reference.mcp_tools_reference("read")
    mutating_reference = docs_api_reference.mcp_tools_reference("mutating")

    assert all(
        f"`{operation_id}`" in read_reference for operation_id in READ_OPERATIONS
    )
    assert all(
        f"`{operation_id}`" in mutating_reference
        for operation_id in MUTATING_OPERATIONS
    )
