import subprocess
import sys
import time
from collections.abc import Mapping
from typing import Any

import pytest

from gcp_mcp.discovery import (
    CLOUD_PLATFORM_SCOPE,
    BundledCorpus,
    ReadMethod,
    Registry,
    ServiceRef,
)
from gcp_mcp.errors import UnknownMethod, UnknownService, UnknownVersion

THING = {"type": "string", "location": "path", "required": True, "pattern": "^things/[^/]+$"}

METHODS = {
    "get": {
        "id": "example.things.get",
        "httpMethod": "GET",
        "path": "v1/{+name}",
        "parameters": {"name": THING},
        "response": {"$ref": "Thing"},
        "scopes": [CLOUD_PLATFORM_SCOPE],
        "description": "Gets a thing.",
    },
    "list": {
        "id": "example.things.list",
        "httpMethod": "GET",
        "path": "v1/things",
        "parameters": {
            "pageSize": {"type": "integer", "location": "query", "maximum": "50"},
            "pageToken": {"type": "string", "location": "query"},
            "kind": {"type": "string", "location": "query", "enum": ["BIG", "SMALL"]},
        },
        "response": {"$ref": "ListThingsResponse"},
        "scopes": [CLOUD_PLATFORM_SCOPE],
    },
    "delete": {
        "id": "example.things.delete",
        "httpMethod": "DELETE",
        "path": "v1/{+name}",
        "parameters": {"name": THING},
        "response": {"$ref": "Operation"},
        "scopes": [CLOUD_PLATFORM_SCOPE],
    },
    "search": {
        "id": "example.things.search",
        "httpMethod": "POST",
        "path": "v1/things:search",
        "parameters": {},
        "request": {"$ref": "SearchThingsRequest"},
        "response": {"$ref": "ListThingsResponse"},
        "scopes": [CLOUD_PLATFORM_SCOPE],
    },
}

SCHEMAS = {
    "Thing": {"id": "Thing", "type": "object", "properties": {"name": {"type": "string"}}},
    "ListThingsResponse": {
        "id": "ListThingsResponse",
        "type": "object",
        "properties": {
            "things": {"type": "array", "items": {"$ref": "Thing"}},
            "nextPageToken": {"type": "string"},
        },
    },
    "SearchThingsRequest": {
        "id": "SearchThingsRequest",
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "pageSize": {"type": "integer"},
            "pageToken": {"type": "string"},
        },
    },
    "Operation": {"id": "Operation", "type": "object", "properties": {"done": {"type": "boolean"}}},
}


def document(
    name: str,
    version: str,
    methods: Mapping[str, Any],
    *,
    title: str = "Example API",
    scopes: tuple[str, ...] = (CLOUD_PLATFORM_SCOPE,),
    docs: str = "https://cloud.google.com/example/docs",
) -> dict[str, Any]:
    return {
        "name": name,
        "version": version,
        "title": title,
        "description": f"{title} manages {name} things.",
        "documentationLink": docs,
        "rootUrl": f"https://{name}.googleapis.com/",
        "servicePath": "",
        "auth": {"oauth2": {"scopes": {scope: {"description": ""} for scope in scopes}}},
        "parameters": {"fields": {"type": "string", "location": "query"}},
        "schemas": SCHEMAS,
        "resources": {"things": {"methods": dict(methods)}},
    }


def pick(*names: str) -> dict[str, Any]:
    return {name: METHODS[name] for name in names}


class FakeCorpus:
    def __init__(self, *documents: Mapping[str, Any]) -> None:
        self._documents = {(doc["name"], doc["version"]): doc for doc in documents}
        self.loads: list[tuple[str, str]] = []

    def names(self) -> list[tuple[str, str]]:
        return sorted(self._documents)

    def load(self, name: str, version: str) -> Mapping[str, Any]:
        self.loads.append((name, version))
        return self._documents[(name, version)]


@pytest.fixture
def corpus() -> FakeCorpus:
    return FakeCorpus(
        document("example", "v1", pick("get", "list", "delete")),
        document("example", "v1beta1", pick("get", "list", "search", "delete")),
        document("example", "v2", pick("get", "delete")),
        document("other", "v1", pick("list"), title="Other API"),
        document("other", "v1alpha", pick("list", "search"), title="Other API"),
        document("empty", "v1", pick("delete"), title="Empty API"),
        document(
            "tube",
            "v3",
            pick("get", "list"),
            title="Tube API",
            scopes=("https://www.googleapis.com/auth/tube",),
            docs="https://developers.google.com/tube",
        ),
    )


@pytest.fixture
def registry(corpus: FakeCorpus) -> Registry:
    return Registry(corpus)


def test_catalog_groups_gcp_documents_by_service(registry: Registry) -> None:
    refs = registry.services()
    assert [ref.name for ref in refs] == ["empty", "example", "other"]
    example = refs[1]
    assert example == ServiceRef(
        name="example",
        title="Example API",
        description="Example API manages example things.",
        docs_url="https://cloud.google.com/example/docs",
        default_version="v1",
        versions=("v2", "v1", "v1beta1"),
        read_methods=2,
    )


def test_default_version_prefers_the_ga_band_then_the_most_reads(registry: Registry) -> None:
    by_name = {ref.name: ref for ref in registry.services()}
    assert by_name["example"].default_version == "v1"
    assert by_name["other"].default_version == "v1"
    assert by_name["other"].read_methods == 1


def test_a_service_with_no_reads_stays_listed_with_zero(registry: Registry) -> None:
    by_name = {ref.name: ref for ref in registry.services()}
    assert by_name["empty"].read_methods == 0
    assert registry.service("empty").methods == {}
    assert "things.delete" in registry.service("empty").excluded


def test_services_filters_by_name_or_title_case_insensitively(registry: Registry) -> None:
    assert [ref.name for ref in registry.services("OTHER")] == ["other"]
    assert [ref.name for ref in registry.services("Example API")] == ["example"]
    assert [ref.name for ref in registry.services("api")] == ["empty", "example", "other"]
    assert registry.services("manages") == ()


def test_catalog_is_scanned_once(registry: Registry, corpus: FakeCorpus) -> None:
    registry.services()
    scanned = list(corpus.loads)
    registry.services("ex")
    registry.services()
    assert corpus.loads == scanned
    assert sorted(scanned) == sorted(corpus.names())


def test_method_resolves_a_read_method_in_the_default_version(registry: Registry) -> None:
    method = registry.method("example", "things.get")
    assert isinstance(method, ReadMethod)
    assert (method.service, method.version, method.name) == ("example", "v1", "things.get")
    assert method.params["name"].reserved is True


def test_method_accepts_an_explicit_version(registry: Registry) -> None:
    method = registry.method("example", "things.search", version="v1beta1")
    assert method.version == "v1beta1"
    assert method.accepts_body is True
    assert method.pagination is not None
    assert method.pagination.in_body is True


def test_unknown_service_suggests_the_closest_catalog_name(registry: Registry) -> None:
    with pytest.raises(UnknownService) as caught:
        registry.method("exampel", "things.get")
    assert caught.value.name == "exampel"
    assert "example" in caught.value.suggestions
    with pytest.raises(UnknownService):
        registry.service("tube")


def test_unknown_version_lists_the_bundled_versions(registry: Registry) -> None:
    with pytest.raises(UnknownVersion) as caught:
        registry.method("example", "things.get", version="v9")
    assert (caught.value.service, caught.value.version) == ("example", "v9")
    assert caught.value.versions == ("v2", "v1", "v1beta1")


def test_unknown_method_carries_the_policy_reason_for_a_refused_method(
    registry: Registry,
) -> None:
    with pytest.raises(UnknownMethod) as caught:
        registry.method("example", "things.delete")
    assert caught.value.excluded_reason == "mutating HTTP method DELETE"
    assert caught.value.suggestions == ()
    assert "read-only" in caught.value.for_llm()


def test_unknown_method_suggests_admitted_names_for_a_typo(registry: Registry) -> None:
    with pytest.raises(UnknownMethod) as caught:
        registry.method("example", "things.gte")
    assert caught.value.excluded_reason is None
    assert caught.value.suggestions[0] == "things.get"
    assert (caught.value.service, caught.value.version) == ("example", "v1")


def test_service_parses_each_document_once_while_cached(
    registry: Registry, corpus: FakeCorpus
) -> None:
    registry.services()
    scanned = len(corpus.loads)
    first = registry.service("example")
    assert registry.service("example") is first
    assert registry.service("example", "v1") is first
    assert len(corpus.loads) == scanned + 1
    registry.service("example", "v1beta1")
    assert len(corpus.loads) == scanned + 2


def test_service_cache_is_bounded(corpus: FakeCorpus) -> None:
    registry = Registry(corpus, cache_size=1)
    registry.services()
    scanned = len(corpus.loads)
    registry.service("example")
    registry.service("other")
    registry.service("example")
    assert len(corpus.loads) == scanned + 3


@pytest.fixture(scope="module")
def bundled() -> Registry:
    return Registry()


def test_bundled_catalog_picks_ga_defaults_with_the_most_reads(bundled: Registry) -> None:
    by_name = {ref.name: ref for ref in bundled.services()}
    assert by_name["compute"].default_version == "v1"
    assert by_name["compute"].versions == ("v1", "beta", "alpha", "preview")
    assert by_name["iam"].default_version == "v1"
    assert by_name["iam"].versions == ("v2", "v1", "v2beta")
    assert by_name["storage"].default_version == "v1"
    assert by_name["storage"].read_methods > 20
    assert "youtube" not in by_name
    assert "admin" not in by_name


def test_bundled_catalog_filters_by_name_and_title(bundled: Registry) -> None:
    assert "compute" in {ref.name for ref in bundled.services("compute")}
    assert [ref.name for ref in bundled.services("Kubernetes Engine")] == ["container"]


def test_bundled_registry_resolves_and_refuses_compute_methods(bundled: Registry) -> None:
    get = bundled.method("compute", "instances.get")
    assert (get.service, get.version, get.http_method) == ("compute", "v1", "GET")
    with pytest.raises(UnknownService) as unknown_service:
        bundled.method("computer", "instances.get")
    assert "compute" in unknown_service.value.suggestions
    with pytest.raises(UnknownMethod) as unknown_method:
        bundled.method("compute", "instances.delete")
    assert "DELETE" in unknown_method.value.excluded_reason
    with pytest.raises(UnknownVersion) as unknown_version:
        bundled.method("compute", "instances.get", version="v9")
    assert unknown_version.value.versions == ("v1", "beta", "alpha", "preview")


def test_bundled_catalog_keys_match_the_documents(bundled: Registry) -> None:
    corpus = BundledCorpus()
    for ref in bundled.services():
        for version in ref.versions:
            doc = corpus.load(ref.name, version)
            assert (doc["name"], doc["version"]) == (ref.name, version)


def test_first_scan_fits_the_budget() -> None:
    started = time.perf_counter()
    assert len(Registry().services()) > 100
    assert time.perf_counter() - started < 5


def test_importing_discovery_opens_no_document() -> None:
    probe = """
import sys

def refuse(event, args):
    if event == "open" and str(args[0]).endswith(".json") and "discovery_cache" in str(args[0]):
        raise SystemExit(f"opened {args[0]}")

sys.addaudithook(refuse)
import gcp_mcp.discovery
gcp_mcp.discovery.Registry()
"""
    subprocess.run([sys.executable, "-c", probe], check=True)
