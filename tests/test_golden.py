import json
from pathlib import Path

from gcp_mcp.discovery import Registry
from scripts import golden

GOLDEN = Path(__file__).parent / "golden"


def test_catalog_golden_pins_every_service_and_its_default_version() -> None:
    assert golden.main(["--check", "catalog.json"]) == 0
    catalog = json.loads((GOLDEN / "catalog.json").read_text())
    by_name = {entry["name"]: entry for entry in catalog}
    assert [entry["name"] for entry in catalog] == sorted(by_name)
    assert set(by_name["compute"]) == {"name", "default_version", "versions", "read_methods"}
    assert by_name["compute"]["default_version"] == "v1"
    assert by_name["iam"]["default_version"] == "v1"
    assert len(catalog) == len(Registry().services())


def test_read_methods_golden_pins_every_admitted_method_in_default_versions() -> None:
    assert golden.main(["--check", "read_methods.txt"]) == 0
    lines = (GOLDEN / "read_methods.txt").read_text().splitlines()
    assert lines == sorted(lines)
    assert len(lines) == len(set(lines))
    assert "compute.instances.aggregatedList" in lines
    assert "iam.projects.serviceAccounts.list" in lines
    assert "logging.entries.list" in lines
    assert "compute.instances.delete" not in lines
    assert "sqladmin.instances.list" in lines
    assert not any(line.startswith("youtube.") for line in lines)
