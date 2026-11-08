import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "gcp_mcp"
CORE = ("policy", "errors", "discovery")
SERVER = ("server", "tools", "settings", "__main__")


def parse(module: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{module}.py").read_text())


def top_level_imports(module: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(parse(module)):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.partition(".")[0])
    return names


@pytest.mark.parametrize("module", CORE)
def test_core_modules_import_no_server_or_http_library(module: str) -> None:
    assert top_level_imports(module).isdisjoint({"fastmcp", "httpx", "httpx2", "mcp"})


@pytest.mark.parametrize("module", ("policy", "errors"))
def test_policy_and_errors_import_only_the_standard_library(module: str) -> None:
    assert top_level_imports(module) <= sys.stdlib_module_names


def test_discovery_imports_only_stdlib_policy_errors_and_the_document_path() -> None:
    allowed = sys.stdlib_module_names | {"gcp_mcp", "googleapiclient"}
    assert top_level_imports("discovery") <= allowed


@pytest.mark.parametrize("module", SERVER)
def test_server_modules_import_no_http_client_or_google_client(module: str) -> None:
    assert top_level_imports(module).isdisjoint({"httpx", "googleapiclient"})


def test_only_parse_service_constructs_read_methods() -> None:
    constructors = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if name == "ReadMethod":
                constructors.append(path.name)
    assert constructors == ["discovery.py"]
