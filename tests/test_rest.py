import json
import threading

import google.auth
import google.auth.credentials
import google.oauth2.credentials
import httpx2
import pytest

from gcp_mcp.discovery import CLOUD_PLATFORM_SCOPE, ReadMethod, Registry, parse_service
from gcp_mcp.errors import BadArguments, GcpApiError, UnknownMethod, UnsupportedResponse
from gcp_mcp.rest import (
    AdcCredentials,
    CallResult,
    Gcp,
    Limits,
    PreparedRequest,
    StaticCredentials,
    bound,
    parse_error,
    prepare,
    redact,
)

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


@pytest.mark.parametrize(
    "method", ["projects.runQuery", "projects.lookup", "projects.runAggregationQuery"]
)
def test_a_snake_case_new_transaction_is_refused(method: str) -> None:
    body = {"readOptions": {"new_transaction": {"read_write": {}}}}
    assert "'readOptions.new_transaction'" in problems("datastore", method, "v1", DATASTORE, body)


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"read_options": {"NewTransaction": {}}}, "read_options.NewTransaction"),
        (
            {"query": {"filter": {"compositeFilter": {"filters": [{"transaction_options": {}}]}}}},
            "query.filter.compositeFilter.filters[0].transaction_options",
        ),
    ],
)
def test_a_transaction_key_is_refused_in_any_case(body: dict, path: str) -> None:
    assert repr(path) in problems("datastore", "projects.runQuery", "v1", DATASTORE, body)


def test_snake_case_body_keys_are_accepted_and_sent_as_given() -> None:
    body = {"read_options": {"read_consistency": "STRONG"}, "query": {"kind": [{"name": "Task"}]}}
    request = build("datastore", "projects.runQuery", "v1", DATASTORE, body)
    assert json.loads(request.body) == body


def test_a_snake_case_page_size_is_not_sent_twice() -> None:
    body = {"resource_names": ["projects/example-project"], "page_size": 5}
    request = build("logging", "entries.list", "v2", body=body)
    assert json.loads(request.body) == body


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"readOptions": {"readConsistancy": "STRONG"}}, "readOptions.readConsistancy"),
        ({"query": {"kind": [{"name": "Task"}, {"nmae": "Task"}]}}, "query.kind[1].nmae"),
        (
            {"gqlQuery": {"queryString": "SELECT 1", "namedBindings": {"done": {"valeu": {}}}}},
            "gqlQuery.namedBindings.done.valeu",
        ),
    ],
)
def test_an_unknown_nested_body_key_is_refused_with_its_path(body: dict, path: str) -> None:
    assert repr(path) in problems("datastore", "projects.runQuery", "v1", DATASTORE, body)


def test_any_key_of_a_map_field_is_accepted() -> None:
    bindings = {
        "done": {"value": {"booleanValue": False}},
        "owner": {"value": {"stringValue": "a"}},
    }
    body = {"gqlQuery": {"queryString": "SELECT 1", "namedBindings": bindings}}
    request = build("datastore", "projects.runQuery", "v1", DATASTORE, body)
    assert json.loads(request.body) == body


SERVICE_CHECK = {"serviceName": "example.googleapis.com"}


def claims(value: dict) -> dict:
    return {"attributes": {"request": {"auth": {"claims": value}}}}


def test_a_free_form_struct_field_accepts_any_keys() -> None:
    body = claims({"iss": "issuer", "ext": {"any": {"shape": [1, {"goes": True}]}}})
    request = build("servicecontrol", "services.check", "v2", SERVICE_CHECK, body)
    assert json.loads(request.body) == body


@pytest.mark.parametrize("key", ["newTransaction", "new_transaction", "transaction_options"])
def test_a_transaction_key_inside_a_free_form_struct_field_is_refused(key: str) -> None:
    body = claims({"iss": "issuer", "ext": [{key: {}}]})
    text = problems("servicecontrol", "services.check", "v2", SERVICE_CHECK, body)
    assert repr(f"attributes.request.auth.claims.ext[0].{key}") in text


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


def compact(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def test_bound_leaves_a_payload_that_fits_alone() -> None:
    payload = {"items": ["xxxxxxxxxx"] * 3, "nextPageToken": "cursor"}
    assert bound(payload, 100) == (payload, 0)


def test_bound_pops_trailing_items_until_the_compact_json_fits() -> None:
    items = [f"item-{number:04}" for number in range(100)]
    kept = max(
        count
        for count in range(len(items) + 1)
        if len(compact({"items": items[:count], "nextPageToken": "cursor"})) <= 500
    )
    trimmed, omitted = bound({"items": items, "nextPageToken": "cursor"}, 500)
    assert trimmed == {"items": items[:kept], "nextPageToken": "cursor"}
    assert omitted == len(items) - kept


def test_bound_keeps_the_largest_count_that_fits() -> None:
    trimmed, omitted = bound({"items": ["xxxxxxxxxx"] * 100}, 500)
    assert (len(trimmed["items"]), omitted) == (37, 63)
    assert len(compact(trimmed)) == 492


def test_bound_does_not_change_its_input() -> None:
    payload = {"items": ["xxxxxxxxxx"] * 100}
    bound(payload, 500)
    assert len(payload["items"]) == 100


def test_bound_trims_the_largest_nested_list_first() -> None:
    payload = {
        "items": {
            "zones/us-central1-a": {"instances": [{"name": f"vm-{n}"} for n in range(20)]},
            "zones/us-central1-b": {"instances": [{"name": "vm-b"}]},
        },
        "warnings": ["w"],
    }
    trimmed, omitted = bound(payload, len(compact(payload)) - 1)
    zones = trimmed["items"]
    assert [vm["name"] for vm in zones["zones/us-central1-a"]["instances"]] == [
        f"vm-{n}" for n in range(19)
    ]
    assert zones["zones/us-central1-b"]["instances"] == [{"name": "vm-b"}]
    assert trimmed["warnings"] == ["w"]
    assert omitted == 1


def test_bound_spreads_trimming_across_lists_of_equal_length() -> None:
    payload = {"a": ["xxxxxxxxxx"] * 10, "b": ["yyyyyyyyyy"] * 10}
    trimmed, omitted = bound(payload, len(compact(payload)) - 100)
    assert abs(len(trimmed["a"]) - len(trimmed["b"])) <= 1
    assert omitted == 20 - len(trimmed["a"]) - len(trimmed["b"])
    assert len(compact(trimmed)) <= len(compact(payload)) - 100


def test_bound_returns_a_payload_without_lists_unchanged() -> None:
    payload = {"description": "x" * 1000}
    assert bound(payload, 100) == (payload, 0)


def test_redact_replaces_secrets_and_keeps_pagination_cursors() -> None:
    payload = {
        "nextPageToken": "cursor-1",
        "nextSyncToken": "cursor-2",
        "pageToken": "cursor-3",
        "items": [{"config": {"database": {"password": "example-password", "user": "admin"}}}],
    }
    redacted, count = redact(payload)
    assert redacted == {
        "nextPageToken": "cursor-1",
        "nextSyncToken": "cursor-2",
        "pageToken": "cursor-3",
        "items": [{"config": {"database": {"password": "[redacted]", "user": "admin"}}}],
    }
    assert count == 1


def test_redact_replaces_objects_and_lists_whole_and_keeps_flags() -> None:
    payload = {
        "pairingKey": {"key": "example-key", "expireTime": "2026-01-01T00:00:00Z"},
        "apiKeys": ["example-key-1", "example-key-2"],
        "masterAuth": {"clientKey": "example-client-key", "clusterCaCertificate": "PEM"},
        "allowCredentials": True,
        "secret": 3,
        "password": None,
    }
    redacted, count = redact(payload)
    assert redacted == {
        "pairingKey": "[redacted]",
        "apiKeys": "[redacted]",
        "masterAuth": {"clientKey": "[redacted]", "clusterCaCertificate": "PEM"},
        "allowCredentials": True,
        "secret": 3,
        "password": None,
    }
    assert count == 3


def test_redact_counts_every_secret_in_a_list() -> None:
    payload = {"items": [{"privateKey": "example-1"}, {"privateKey": "example-2"}, {"name": "x"}]}
    redacted, count = redact(payload)
    assert redacted == {
        "items": [{"privateKey": "[redacted]"}, {"privateKey": "[redacted]"}, {"name": "x"}]
    }
    assert count == 2


def test_parse_error_reads_the_google_error_envelope() -> None:
    info = {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "SERVICE_DISABLED",
        "domain": "googleapis.com",
        "metadata": {"service": "compute.googleapis.com"},
    }
    envelope = {
        "error": {
            "code": 403,
            "message": "Compute Engine API has not been used in project example-project.",
            "status": "PERMISSION_DENIED",
            "details": [info],
        }
    }
    error = parse_error(403, json.dumps(envelope).encode())
    assert isinstance(error, GcpApiError)
    assert (error.status, error.reason, error.message, error.details) == (
        403,
        "PERMISSION_DENIED",
        "Compute Engine API has not been used in project example-project.",
        (info,),
    )


def test_parse_error_falls_back_to_the_legacy_reason() -> None:
    envelope = {
        "error": {
            "code": 404,
            "message": "The resource 'projects/example-project/zones/us-central1-a/instances/vm-1' "
            "was not found",
            "errors": [{"message": "not found", "domain": "global", "reason": "notFound"}],
        }
    }
    error = parse_error(404, json.dumps(envelope).encode())
    assert (error.status, error.reason) == (404, "notFound")
    assert "vm-1" in error.message
    assert error.details == ()


@pytest.mark.parametrize(
    ("status", "body", "reason", "message"),
    [
        (
            502,
            b"<html>upstream connect error</html>",
            "Bad Gateway",
            "<html>upstream connect error</html>",
        ),
        (503, b"", "Service Unavailable", "Service Unavailable"),
        (500, b'["not", "an", "envelope"]', "Internal Server Error", '["not", "an", "envelope"]'),
        (500, b"\xff\xfe broken", "Internal Server Error", "\ufffd\ufffd broken"),
    ],
)
def test_parse_error_falls_back_to_the_status_phrase_and_the_text(
    status: int, body: bytes, reason: str, message: str
) -> None:
    error = parse_error(status, body)
    assert (error.status, error.reason, error.message) == (status, reason, message)


def test_parse_error_keeps_a_long_plain_body_short() -> None:
    error = parse_error(500, b"x" * 5000)
    assert error.message == "x" * 200


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Google:
    def __init__(self, response: httpx2.Response) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.response

    def gcp(self, credentials=None, limits: Limits = LIMITS) -> Gcp:
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self))
        return Gcp(REGISTRY, limits, credentials or StaticCredentials("example-token"), http)


@pytest.mark.anyio
async def test_call_sends_exactly_one_authenticated_get() -> None:
    google = Google(httpx2.Response(200, json={"name": "vm-1", "status": "RUNNING"}))
    result = await google.gcp().call("compute", "instances.get", VM)
    [request] = google.requests
    assert request.method == "GET"
    assert str(request.url) == (
        "https://compute.googleapis.com/compute/v1/projects/example-project"
        "/zones/us-central1-a/instances/vm-1?alt=json&prettyPrint=false"
    )
    assert request.headers["authorization"] == "Bearer example-token"
    assert "x-goog-user-project" not in request.headers
    assert request.content == b""
    assert request.extensions["timeout"]["read"] == LIMITS.request_timeout_s
    assert result == CallResult(
        service="compute",
        version="v1",
        method="instances.get",
        response={"name": "vm-1", "status": "RUNNING"},
        next_page_token=None,
        truncated=False,
        omitted_items=0,
        redacted_fields=0,
    )


@pytest.mark.anyio
async def test_call_posts_the_body_and_surfaces_the_page_token() -> None:
    answer = {"entries": [{"logName": "projects/example-project/logs/a"}], "nextPageToken": "next"}
    google = Google(httpx2.Response(200, json=answer))
    body = {"resourceNames": ["projects/example-project"]}
    result = await google.gcp().call("logging", "entries.list", body=body)
    [request] = google.requests
    assert (request.method, str(request.url)) == (
        "POST",
        "https://logging.googleapis.com/v2/entries:list?alt=json&prettyPrint=false",
    )
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {**body, "pageSize": 100}
    assert (result.response, result.next_page_token) == (answer, "next")


@pytest.mark.anyio
async def test_a_query_value_cannot_add_parameters() -> None:
    google = Google(httpx2.Response(200, json={}))
    await google.gcp().call("compute", "instances.get", {**VM, "fields": "name&alt=media#x"})
    url = google.requests[0].url
    assert url.params.get_list("alt") == ["json"]
    assert url.params.get_list("fields") == ["name&alt=media#x"]
    assert url.fragment == ""


@pytest.mark.anyio
async def test_call_honors_an_explicit_version() -> None:
    google = Google(httpx2.Response(200, json={}))
    result = await google.gcp().call("compute", "instances.get", VM, version="beta")
    assert google.requests[0].url.path.startswith("/compute/beta/projects/example-project/")
    assert result.version == "beta"


@pytest.mark.anyio
async def test_an_excluded_method_sends_nothing() -> None:
    google = Google(httpx2.Response(200, json={}))
    with pytest.raises(UnknownMethod) as caught:
        await google.gcp().call("compute", "instances.delete", VM)
    assert caught.value.excluded_reason is not None
    assert google.requests == []


@pytest.mark.anyio
async def test_refused_arguments_send_nothing() -> None:
    google = Google(httpx2.Response(200, json={}))
    args = {"name": f"{SECRET_VERSION}/1:access"}
    with pytest.raises(BadArguments):
        await google.gcp().call("secretmanager", "projects.secrets.versions.get", args)
    assert google.requests == []


@pytest.mark.anyio
async def test_a_403_envelope_becomes_a_gcp_api_error_about_the_server_identity() -> None:
    envelope = {
        "error": {"code": 403, "message": "Permission denied", "status": "PERMISSION_DENIED"}
    }
    google = Google(httpx2.Response(403, json=envelope))
    with pytest.raises(GcpApiError) as caught:
        await google.gcp().call("compute", "instances.get", VM)
    assert (caught.value.status, caught.value.reason) == (403, "PERMISSION_DENIED")
    assert "identity" in caught.value.for_llm()


@pytest.mark.anyio
async def test_a_redirect_is_an_error_and_is_not_followed() -> None:
    google = Google(httpx2.Response(302, headers={"location": "https://elsewhere.example/"}))
    with pytest.raises(GcpApiError) as caught:
        await google.gcp().call("compute", "instances.get", VM)
    assert caught.value.status == 302
    assert len(google.requests) == 1


@pytest.mark.anyio
async def test_a_non_json_answer_is_unsupported() -> None:
    png = httpx2.Response(200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"})
    google = Google(png)
    with pytest.raises(UnsupportedResponse) as caught:
        await google.gcp().call("compute", "instances.get", VM)
    assert "image/png" in caught.value.message


@pytest.mark.anyio
async def test_call_redacts_then_bounds_the_answer() -> None:
    answer = {
        "items": [{"name": f"vm-{n}", "metadata": {"password": "example"}} for n in range(50)],
        "nextPageToken": "next",
    }
    limits = Limits(default_page_size=100, max_response_bytes=1000, request_timeout_s=30.0)
    google = Google(httpx2.Response(200, json=answer))
    result = await google.gcp(limits=limits).call(
        "compute", "instances.aggregatedList", {"project": "example-project"}
    )
    kept = len(result.response["items"])
    assert 0 < kept < 50
    assert (result.truncated, result.omitted_items, result.redacted_fields) == (True, 50 - kept, 50)
    assert result.response["items"][0] == {"name": "vm-0", "metadata": {"password": "[redacted]"}}
    assert result.next_page_token == "next"
    assert len(json.dumps(result.response, separators=(",", ":"))) <= 1000


@pytest.mark.anyio
async def test_adc_credentials_send_the_quota_project(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def default(scopes):
        calls.append(scopes)
        adc = google.oauth2.credentials.Credentials(
            token="example-token", quota_project_id="example-project"
        )
        return adc, "example-project"

    monkeypatch.setattr(google.auth, "default", default)
    credentials = AdcCredentials()
    assert calls == []
    server = Google(httpx2.Response(200, json={}))
    await server.gcp(credentials).call("compute", "instances.get", VM)
    headers = server.requests[0].headers
    assert headers["authorization"] == "Bearer example-token"
    assert headers["x-goog-user-project"] == "example-project"
    assert calls == [["https://www.googleapis.com/auth/cloud-platform"]]


class Expired(google.auth.credentials.Credentials):
    def __init__(self) -> None:
        super().__init__()
        self.refreshed_on: list[int] = []

    def refresh(self, request: object) -> None:
        self.refreshed_on.append(threading.get_ident())
        self.token = f"fresh-token-{len(self.refreshed_on)}"


@pytest.mark.anyio
async def test_adc_credentials_refresh_off_the_event_loop_and_only_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adc = Expired()
    monkeypatch.setattr(google.auth, "default", lambda scopes: (adc, None))
    credentials = AdcCredentials()
    first = await credentials.headers()
    second = await credentials.headers()
    assert first["authorization"] == second["authorization"] == "Bearer fresh-token-1"
    assert len(adc.refreshed_on) == 1
    assert adc.refreshed_on[0] != threading.get_ident()
