import json

import pytest

from gcp_mcp.discovery import CLOUD_PLATFORM_SCOPE, ReadMethod, Registry, parse_service
from gcp_mcp.errors import BadArguments
from gcp_mcp.rest import Limits, PreparedRequest, prepare

REGISTRY = Registry()
LIMITS = Limits(default_page_size=100, max_response_bytes=100_000, request_timeout_s=30.0)
PLAIN = (("alt", "json"), ("prettyPrint", "false"))
VM = {"project": "example-project", "zone": "us-central1-a", "instance": "vm-1"}
SECRET_VERSION = "projects/example-project/secrets/example-secret/versions"
PACKAGE = (
    "projects/example-project/locations/us-central1/repositories/example-repo"
    "/packages/example-package/versions"
)


def resolve(service: str, method: str, version: str) -> tuple[ReadMethod, dict]:
    read_method = REGISTRY.method(service, method, version)
    return read_method, REGISTRY.service(service, version).schemas


def build(
    service: str,
    method: str,
    version: str,
    args: dict | None = None,
    body: dict | None = None,
    limits: Limits = LIMITS,
) -> PreparedRequest:
    read_method, schemas = resolve(service, method, version)
    return prepare(read_method, args or {}, body, limits, schemas)


def problems(service: str, method: str, version: str, args=None, body=None) -> str:
    with pytest.raises(BadArguments) as caught:
        build(service, method, version, args, body)
    return "\n".join(caught.value.problems)


def test_instances_get_builds_the_exact_request() -> None:
    assert build("compute", "instances.get", "v1", VM) == PreparedRequest(
        method="GET",
        url=(
            "https://compute.googleapis.com/compute/v1/projects/example-project"
            "/zones/us-central1-a/instances/vm-1"
        ),
        query=PLAIN,
        body=None,
    )


def test_aggregated_list_gets_the_default_page_size() -> None:
    request = build("compute", "instances.aggregatedList", "v1", {"project": "example-project"})
    assert request.url == (
        "https://compute.googleapis.com/compute/v1/projects/example-project/aggregated/instances"
    )
    assert request.query == (*PLAIN, ("maxResults", "100"))


def test_the_page_size_default_comes_from_the_limits() -> None:
    limits = Limits(default_page_size=7, max_response_bytes=100_000, request_timeout_s=30.0)
    request = build("compute", "zones.list", "v1", {"project": "example-project"}, limits=limits)
    assert request.query == (*PLAIN, ("maxResults", "7"))


def test_a_caller_page_size_wins_over_the_default() -> None:
    args = {"project": "example-project", "maxResults": 5}
    request = build("compute", "instances.aggregatedList", "v1", args)
    assert request.query == (*PLAIN, ("maxResults", "5"))


def test_the_page_size_default_is_clamped_to_the_documented_maximum() -> None:
    spec = parse_service(
        {
            "name": "example",
            "version": "v1",
            "title": "Example API",
            "rootUrl": "https://example.googleapis.com/",
            "servicePath": "",
            "resources": {
                "things": {
                    "methods": {
                        "list": {
                            "id": "example.things.list",
                            "httpMethod": "GET",
                            "path": "v1/things",
                            "parameters": {
                                "pageSize": {
                                    "type": "integer",
                                    "location": "query",
                                    "maximum": "50",
                                },
                                "pageToken": {"type": "string", "location": "query"},
                            },
                            "response": {"$ref": "ListThingsResponse"},
                            "scopes": [CLOUD_PLATFORM_SCOPE],
                        }
                    }
                }
            },
            "schemas": {
                "ListThingsResponse": {
                    "properties": {"things": {"type": "array"}, "nextPageToken": {}}
                }
            },
        }
    )
    request = prepare(spec.methods["things.list"], {}, None, LIMITS, spec.schemas)
    assert request.url == "https://example.googleapis.com/v1/things"
    assert request.query == (*PLAIN, ("pageSize", "50"))


def test_bucket_iam_policy_is_a_plain_get() -> None:
    args = {"bucket": "example-bucket", "optionsRequestedPolicyVersion": 3}
    request = build("storage", "buckets.getIamPolicy", "v1", args)
    assert request == PreparedRequest(
        method="GET",
        url="https://storage.googleapis.com/storage/v1/b/example-bucket/iam",
        query=(*PLAIN, ("optionsRequestedPolicyVersion", "3")),
        body=None,
    )


def test_logging_entries_list_posts_the_body_with_the_page_size_in_it() -> None:
    body = {"resourceNames": ["projects/example-project"], "filter": "severity>=ERROR"}
    request = build("logging", "entries.list", "v2", body=body)
    assert (request.method, request.url, request.query) == (
        "POST",
        "https://logging.googleapis.com/v2/entries:list",
        PLAIN,
    )
    assert json.loads(request.body) == {**body, "pageSize": 100}


def test_a_body_page_size_wins_over_the_default() -> None:
    body = {"resourceNames": ["projects/example-project"], "pageSize": 5}
    request = build("logging", "entries.list", "v2", body=body)
    assert json.loads(request.body) == body


def test_a_paged_post_without_a_body_still_sends_the_page_size() -> None:
    request = build("logging", "entries.list", "v2")
    assert json.loads(request.body) == {"pageSize": 100}


def test_a_post_without_a_body_or_paging_sends_no_body() -> None:
    args = {"resource": "projects/example-project"}
    request = build("cloudresourcemanager", "projects.getIamPolicy", "v3", args)
    assert (request.method, request.url, request.body) == (
        "POST",
        "https://cloudresourcemanager.googleapis.com/v3/projects/example-project:getIamPolicy",
        None,
    )


def test_fields_passes_through() -> None:
    request = build("compute", "instances.get", "v1", {**VM, "fields": "name,status"})
    assert request.query == (*PLAIN, ("fields", "name,status"))


def test_simple_path_values_are_percent_encoded_with_no_safe_characters() -> None:
    args = {"bucket": "example-bucket/o?x=1#y:z%"}
    request = build("storage", "buckets.getIamPolicy", "v1", args)
    assert request.url == (
        "https://storage.googleapis.com/storage/v1/b/example-bucket%2Fo%3Fx%3D1%23y%3Az%25/iam"
    )


@pytest.mark.parametrize("value", ["", ".", ".."])
def test_simple_path_values_cannot_be_empty_or_a_dot_segment(value: str) -> None:
    text = problems("compute", "instances.get", "v1", {**VM, "instance": value})
    assert "'instance'" in text


def test_a_reserved_path_value_expands_unescaped() -> None:
    request = build(
        "secretmanager", "projects.secrets.versions.get", "v1", {"name": f"{SECRET_VERSION}/1"}
    )
    assert request.url == f"https://secretmanager.googleapis.com/v1/{SECRET_VERSION}/1"


def test_a_digest_with_a_colon_is_a_valid_reserved_value() -> None:
    method = "projects.locations.repositories.packages.versions.get"
    request = build("artifactregistry", method, "v1", {"name": f"{PACKAGE}/sha256:0a1b2c3d"})
    assert request.url == f"https://artifactregistry.googleapis.com/v1/{PACKAGE}/sha256:0a1b2c3d"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (f"{SECRET_VERSION}/1:access", ":access"),
        (f"{SECRET_VERSION}/1:ACCESS", ":ACCESS"),
        ("projects/example-project/secrets/example-secret:x/versions/1", ":x"),
        (f"{SECRET_VERSION}/1?alt=media", "'?'"),
        (f"{SECRET_VERSION}/1#fragment", "'#'"),
        (f"{SECRET_VERSION}/1%3Aaccess", "'%'"),
        (f"{SECRET_VERSION}/1\\x", "'\\\\'"),
        (f"{SECRET_VERSION}/..", "'..'"),
        (f"{SECRET_VERSION}/.", "'.'"),
        (f"/{SECRET_VERSION}/1", "'/'"),
        ("", "empty"),
        ("projects/example-project/secrets/example-secret", "pattern"),
    ],
)
def test_reserved_path_values_that_could_reach_another_endpoint_are_refused(
    value: str, reason: str
) -> None:
    text = problems("secretmanager", "projects.secrets.versions.get", "v1", {"name": value})
    assert "'name'" in text
    assert reason in text


def test_a_reserved_value_must_match_the_whole_pattern() -> None:
    parent = "reservations/r1/reservationBlocks/b1/reservationSubBlocks/s1"
    args = {"project": "example-project", "zone": "us-central1-a", "reservationSlot": "slot-1"}
    request = build("compute", "reservationSlots.get", "v1", {**args, "parentName": parent})
    assert request.url.endswith(f"/zones/us-central1-a/{parent}/reservationSlots/slot-1")
    text = problems("compute", "reservationSlots.get", "v1", {**args, "parentName": f"x/{parent}"})
    assert "'parentName'" in text
    assert "pattern" in text


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("projects/example-project:setIamPolicy?x=", "'?'"),
        ("projects/example-project:setIamPolicy", ":setIamPolicy"),
    ],
)
def test_get_iam_policy_cannot_be_turned_into_set_iam_policy(value: str, reason: str) -> None:
    args = {"resource": value}
    text = problems("cloudresourcemanager", "projects.getIamPolicy", "v3", args)
    assert "'resource'" in text
    assert reason in text


def test_a_body_key_outside_the_request_schema_is_refused() -> None:
    args = {"resource": "projects/example-project"}
    body = {"options": {"requestedPolicyVersion": 3}, "policy": {"bindings": []}}
    text = problems("cloudresourcemanager", "projects.getIamPolicy", "v3", args, body)
    assert "'policy'" in text
    assert "options" in text


def test_a_body_within_the_request_schema_is_sent() -> None:
    args = {"resource": "projects/example-project"}
    body = {"options": {"requestedPolicyVersion": 3}}
    request = build("cloudresourcemanager", "projects.getIamPolicy", "v3", args, body)
    assert json.loads(request.body) == body


DATASTORE = {"projectId": "example-project"}
FIRESTORE = {"parent": "projects/example-project/databases/(default)/documents"}


@pytest.mark.parametrize(
    ("service", "method", "args", "body", "key"),
    [
        (
            "datastore",
            "projects.runQuery",
            DATASTORE,
            {"readOptions": {"newTransaction": {"readWrite": {}}}},
            "readOptions.newTransaction",
        ),
        (
            "datastore",
            "projects.runQuery",
            DATASTORE,
            {"readOptions": {"transaction": "dHJhbnNhY3Rpb24="}},
            "readOptions.transaction",
        ),
        (
            "datastore",
            "projects.runQuery",
            DATASTORE,
            {"query": {"filter": {"compositeFilter": {"filters": [{"transactionOptions": {}}]}}}},
            "transactionOptions",
        ),
        (
            "firestore",
            "projects.databases.documents.runQuery",
            FIRESTORE,
            {"newTransaction": {"readWrite": {}}},
            "newTransaction",
        ),
    ],
)
def test_a_body_that_opens_a_transaction_is_refused(
    service: str, method: str, args: dict, body: dict, key: str
) -> None:
    assert key in problems(service, method, "v1", args, body)


def test_a_read_only_query_body_is_sent() -> None:
    body = {"readOptions": {"readConsistency": "STRONG"}, "query": {"kind": [{"name": "Task"}]}}
    request = build("datastore", "projects.runQuery", "v1", DATASTORE, body)
    assert request.url == "https://datastore.googleapis.com/v1/projects/example-project:runQuery"
    assert json.loads(request.body) == body


def test_a_body_on_a_get_is_refused() -> None:
    text = problems("compute", "instances.get", "v1", VM, {"name": "vm-1"})
    assert "body" in text


@pytest.mark.parametrize(
    "name",
    [
        "alt",
        "key",
        "oauth_token",
        "access_token",
        "prettyPrint",
        "quotaUser",
        "callback",
        "$.xgafv",
    ],
)
def test_global_parameters_other_than_fields_are_refused(name: str) -> None:
    text = problems("compute", "instances.get", "v1", {**VM, name: "media"})
    assert repr(name) in text


def test_an_unknown_parameter_is_refused_with_the_accepted_names() -> None:
    text = problems("compute", "instances.get", "v1", {**VM, "zone_id": "us-central1-a"})
    assert "'zone_id'" in text
    for accepted in ("fields", "instance", "project", "zone"):
        assert accepted in text


def test_values_are_coerced_by_type_and_none_is_dropped() -> None:
    args = {
        "project": "example-project",
        "includeAllScopes": True,
        "maxResults": "25",
        "filter": None,
    }
    request = build("compute", "instances.aggregatedList", "v1", args)
    assert request.query == (*PLAIN, ("includeAllScopes", "true"), ("maxResults", "25"))


def test_enum_values_are_checked() -> None:
    method = "projects.locations.repositories.packages.versions.get"
    request = build("artifactregistry", method, "v1", {"name": f"{PACKAGE}/1", "view": "FULL"})
    assert request.query == (*PLAIN, ("view", "FULL"))
    text = problems(
        "artifactregistry", method, "v1", {"name": f"{PACKAGE}/1", "view": "EVERYTHING"}
    )
    assert "'view'" in text
    assert "BASIC" in text


def test_lists_are_accepted_only_for_repeated_parameters() -> None:
    args = {
        "bucket": "example-bucket",
        "permissions": ["storage.buckets.get", "storage.buckets.list"],
    }
    request = build("storage", "buckets.testIamPermissions", "v1", args)
    assert request.query == (
        *PLAIN,
        ("permissions", "storage.buckets.get"),
        ("permissions", "storage.buckets.list"),
    )
    text = problems(
        "compute",
        "instances.aggregatedList",
        "v1",
        {"project": "example-project", "filter": ["a", "b"]},
    )
    assert "'filter'" in text


def test_every_problem_is_reported_at_once() -> None:
    args = {
        "project": "example-project",
        "instance": "vm-1",
        "alt": "media",
        "zone_id": "us-central1-a",
    }
    with pytest.raises(BadArguments) as caught:
        build("compute", "instances.get", "v1", args, {"name": "vm-1"})
    text = "\n".join(caught.value.problems)
    assert len(caught.value.problems) == 4
    for fragment in ("'zone'", "'alt'", "'zone_id'", "body"):
        assert fragment in text


@pytest.mark.parametrize(
    ("args", "name"),
    [
        ({"project": "example-project", "maxResults": "five"}, "'maxResults'"),
        ({"project": "example-project", "maxResults": True}, "'maxResults'"),
        ({"project": "example-project", "includeAllScopes": "yes"}, "'includeAllScopes'"),
    ],
)
def test_values_of_the_wrong_type_are_refused(args: dict, name: str) -> None:
    assert name in problems("compute", "instances.aggregatedList", "v1", args)
