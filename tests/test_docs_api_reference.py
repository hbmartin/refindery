"""Regression tests for generated documentation reference coverage."""

from pathlib import Path

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
