import pytest

from gcp_mcp.discovery import (
    CLOUD_PLATFORM_SCOPE,
    NOT_GCP,
    BundledCorpus,
    is_gcp,
    version_rank,
)

CORPUS = BundledCorpus()


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
