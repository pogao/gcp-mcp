import re

import pytest

from gcp_mcp.discovery import (
    CLOUD_PLATFORM_SCOPE,
    NOT_GCP,
    BundledCorpus,
    Pagination,
    ReadMethod,
    ServiceSpec,
    is_gcp,
    parse_service,
    version_rank,
    walk_methods,
)

CORPUS = BundledCorpus()


@pytest.fixture(scope="module")
def compute() -> ServiceSpec:
    return parse_service(CORPUS.load("compute", "v1"))


@pytest.fixture(scope="module")
def logging() -> ServiceSpec:
    return parse_service(CORPUS.load("logging", "v2"))


def test_bundled_corpus_lists_every_document_but_the_stale_index() -> None:
    names = list(CORPUS.names())
    assert len(names) == 604
    assert len(set(names)) == len(names)
    assert ("compute", "v1") in names
    assert ("youtube", "v3") in names
    assert all(version for _, version in names)
    assert not any(name == "index" for name, _ in names)


def test_bundled_corpus_loads_a_document_by_name_and_version() -> None:
    document = CORPUS.load("compute", "v1")
    assert (document["name"], document["version"]) == ("compute", "v1")
    assert "instances" in document["resources"]


@pytest.mark.parametrize(
    ("name", "version", "expected"),
    [
        ("compute", "v1", True),
        ("storage", "v1", True),
        ("logging", "v2", True),
        ("cloudchannel", "v1", True),
        ("youtube", "v3", False),
        ("drive", "v3", False),
        ("sheets", "v4", False),
        ("admin", "directory_v1", False),
        ("places", "v1", False),
        ("firebase", "v1beta1", False),
        ("fcm", "v1", False),
    ],
)
def test_is_gcp_on_bundled_documents(name: str, version: str, expected: bool) -> None:
    assert is_gcp(CORPUS.load(name, version)) is expected


def test_is_gcp_accepts_the_scope_or_the_docs_host_and_refuses_the_not_gcp_names() -> None:
    by_scope = {
        "name": "example",
        "documentationLink": "https://developers.google.com/example",
        "auth": {"oauth2": {"scopes": {CLOUD_PLATFORM_SCOPE: {}}}},
    }
    by_host = {"name": "example", "documentationLink": "https://cloud.google.com/example/docs"}
    neither = {"name": "example", "documentationLink": "https://developers.google.com/example"}
    assert is_gcp(by_scope)
    assert is_gcp(by_host)
    assert not is_gcp(neither)
    assert not is_gcp({**by_scope, "name": "admin"})
    assert "admin" in NOT_GCP


@pytest.mark.parametrize(
    ("higher", "lower"),
    [
        ("v1", "v1beta1"),
        ("v1", "v1beta"),
        ("v1", "beta"),
        ("v1beta1", "v1p1beta1"),
        ("v1p1beta1", "v1alpha1"),
        ("v1beta1", "v1alpha1"),
        ("v1alpha1", "alpha"),
        ("alpha", "preview"),
        ("preview", "v1beta1a"),
        ("v2", "v1"),
        ("v1beta2", "v1beta1"),
        ("v1beta1", "v1beta"),
        ("v2beta", "v1beta1"),
        ("v1", "v3beta1"),
        ("v1b3", "v1alpha2"),
    ],
)
def test_version_rank_orders_ga_over_beta_over_alpha_over_preview(higher: str, lower: str) -> None:
    assert version_rank(higher) > version_rank(lower)


def test_version_rank_sorts_compute_as_v1_beta_alpha_preview() -> None:
    versions = ["preview", "alpha", "v1", "beta"]
    assert sorted(versions, key=version_rank, reverse=True) == ["v1", "beta", "alpha", "preview"]


def test_parse_service_keeps_admitted_methods_and_the_reason_for_the_rest(
    compute: ServiceSpec,
) -> None:
    assert (compute.name, compute.version, compute.title) == ("compute", "v1", "Compute Engine API")
    assert compute.base_url == "https://compute.googleapis.com/compute/v1/"
    assert compute.docs_url.startswith("https://cloud.google.com/compute/")
    assert "instances.aggregatedList" in compute.methods
    assert compute.excluded["instances.delete"] == "mutating HTTP method DELETE"
    assert compute.excluded["instances.setMetadata"] == "starts a long-running operation"
    assert set(compute.methods).isdisjoint(compute.excluded)
    every_name = {raw["id"].partition(".")[2] for raw in walk_methods(CORPUS.load("compute", "v1"))}
    assert set(compute.methods) | set(compute.excluded) == every_name
    assert list(compute.methods) == sorted(compute.methods)
    assert all(isinstance(method, ReadMethod) for method in compute.methods.values())
    assert "Instance" in compute.schemas


def test_parse_service_builds_a_get_with_required_path_params(compute: ServiceSpec) -> None:
    get = compute.methods["instances.get"]
    assert (get.service, get.version, get.name) == ("compute", "v1", "instances.get")
    assert get.http_method == "GET"
    assert get.base_url == compute.base_url
    assert get.path_template == "projects/{project}/zones/{zone}/instances/{instance}"
    assert set(get.params) == {"project", "zone", "instance"}
    for param in get.params.values():
        assert (param.location, param.required, param.reserved) == ("path", True, False)
        assert param.type == "string"
        assert isinstance(param.pattern, re.Pattern)
    assert get.params["zone"].pattern.fullmatch("us-central1-a")
    assert not get.params["zone"].pattern.fullmatch("us central1")
    assert (get.accepts_body, get.body_schema, get.response_schema) == (False, None, "Instance")
    assert get.pagination is None
    assert CLOUD_PLATFORM_SCOPE in get.scopes
    assert get.deprecated is False
    assert get.description.startswith("Returns the specified Instance resource")


def test_parse_service_keeps_global_params_apart_from_method_params(
    compute: ServiceSpec,
) -> None:
    fields = compute.global_params["fields"]
    assert (fields.location, fields.required, fields.type) == ("query", False, "string")
    assert "fields" not in compute.methods["instances.get"].params


def test_parse_service_detects_query_pagination(compute: ServiceSpec) -> None:
    paged = Pagination(
        token_param="pageToken", size_param="maxResults", size_max=None, in_body=False
    )
    assert compute.methods["instances.list"].pagination == paged
    assert compute.methods["instances.aggregatedList"].pagination == paged


def test_parse_service_reads_size_max_from_the_declared_maximum() -> None:
    doc = CORPUS.load("compute", "v1")
    doc["resources"]["instances"]["methods"]["list"]["parameters"]["maxResults"]["maximum"] = "500"
    assert parse_service(doc).methods["instances.list"].pagination.size_max == 500


def test_parse_service_needs_next_page_token_in_the_response_to_call_it_paged(
    compute: ServiceSpec,
) -> None:
    features = compute.methods["sslPolicies.listAvailableFeatures"]
    assert "pageToken" in features.params
    assert features.pagination is None


def test_parse_service_records_enum_repeated_and_reserved_params(compute: ServiceSpec) -> None:
    views = compute.methods["subnetworks.aggregatedList"].params["views"]
    assert views.repeated is True
    assert views.enum is not None
    assert "WITH_UTILIZATION" in views.enum
    assert compute.methods["instances.list"].params["filter"].enum is None
    parent = compute.methods["reservationSubBlocks.get"].params["parentName"]
    assert (parent.location, parent.required, parent.reserved) == ("path", True, True)
    assert parent.pattern.fullmatch("reservations/r1/reservationBlocks/b1")


def test_parse_service_builds_a_post_read_that_pages_in_the_body(logging: ServiceSpec) -> None:
    entries = logging.methods["entries.list"]
    assert entries.http_method == "POST"
    assert entries.base_url == "https://logging.googleapis.com/"
    assert entries.path_template == "v2/entries:list"
    assert entries.accepts_body is True
    assert entries.body_schema == "ListLogEntriesRequest"
    assert entries.response_schema == "ListLogEntriesResponse"
    assert entries.pagination == Pagination(
        token_param="pageToken", size_param="pageSize", size_max=None, in_body=True
    )
    assert logging.methods["projects.logs.list"].params["resourceNames"].repeated is True
    assert logging.methods["getCmekSettings"].params["name"].reserved is True


def test_parse_service_strips_the_id_prefix_even_when_it_is_not_the_service_name() -> None:
    sqladmin = parse_service(CORPUS.load("sqladmin", "v1"))
    assert sqladmin.name == "sqladmin"
    assert "instances.list" in sqladmin.methods
    assert sqladmin.methods["flags.list"].params["flagScope"].enum is not None
    assert not any(name.startswith("sql.") for name in sqladmin.methods)
