import pytest

from gcp_mcp.errors import (
    BadArguments,
    GcpApiError,
    GcpMcpError,
    UnknownMethod,
    UnknownService,
    UnknownVersion,
    UnsupportedResponse,
)

EXAMPLES = [
    UnknownService("computer", ("compute",)),
    UnknownVersion("compute", "v9", ("v1", "beta")),
    UnknownMethod("compute", "v1", "instances.gett", ("instances.get",), None),
    UnknownMethod("compute", "v1", "instances.delete", (), "mutating HTTP method DELETE"),
    BadArguments("compute", "instances.get", ["missing required 'zone'", "unknown 'zone_id'"]),
    GcpApiError(403, "PERMISSION_DENIED", "The caller does not have permission", ()),
    UnsupportedResponse("the response is image/png, not JSON"),
]


@pytest.mark.parametrize("error", EXAMPLES, ids=lambda error: type(error).__name__)
def test_every_error_speaks_to_the_model(error: GcpMcpError) -> None:
    assert isinstance(error, GcpMcpError)
    assert isinstance(error, Exception)
    text = error.for_llm()
    assert text == text.strip()
    assert text.endswith(".")
    assert str(error)


def test_unknown_service_names_the_closest_services() -> None:
    text = UnknownService("computer", ("compute", "composer")).for_llm()
    assert "computer" in text
    assert "compute" in text
    assert "composer" in text


def test_unknown_service_without_suggestions_still_points_somewhere() -> None:
    text = UnknownService("zzz", ()).for_llm()
    assert "zzz" in text
    assert "gcp_services" in text


def test_unknown_version_lists_the_bundled_versions() -> None:
    error = UnknownVersion("compute", "v9", ("v1", "beta", "alpha"))
    text = error.for_llm()
    assert "v9" in text
    assert "v1, beta, alpha" in text
    assert error.versions == ("v1", "beta", "alpha")


def test_unknown_method_quotes_the_policy_reason() -> None:
    error = UnknownMethod("compute", "v1", "instances.delete", (), "mutating HTTP method DELETE")
    text = error.for_llm()
    assert "read-only" in text
    assert "mutating HTTP method DELETE" in text
    assert "instances.delete" in text
    assert error.excluded_reason == "mutating HTTP method DELETE"


def test_unknown_method_without_a_reason_suggests_admitted_names() -> None:
    suggestions = ("instances.get", "instances.list")
    error = UnknownMethod("compute", "v1", "instances.gett", suggestions, None)
    text = error.for_llm()
    assert "instances.gett" in text
    assert "instances.get" in text
    assert "instances.list" in text
    assert "read-only" not in text
    assert error.excluded_reason is None


def test_bad_arguments_reports_every_problem() -> None:
    problems = ["missing required 'zone'", "unknown argument 'zone_id'"]
    error = BadArguments("compute", "instances.get", problems)
    text = error.for_llm()
    assert all(problem in text for problem in problems)
    assert "instances.get" in text
    assert error.problems == problems


def test_gcp_api_error_carries_status_reason_and_message() -> None:
    details = ({"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_DENIED"},)
    error = GcpApiError(403, "PERMISSION_DENIED", "The caller does not have permission", details)
    text = error.for_llm()
    assert "403" in text
    assert "PERMISSION_DENIED" in text
    assert "The caller does not have permission" in text
    assert (error.status, error.reason, error.message, error.details) == (
        403,
        "PERMISSION_DENIED",
        "The caller does not have permission",
        details,
    )


def test_gcp_api_error_without_a_reason_reads_cleanly() -> None:
    text = GcpApiError(500, None, "Internal error", ()).for_llm()
    assert "500" in text
    assert "None" not in text


def test_unsupported_response_relays_its_message() -> None:
    error = UnsupportedResponse("the response is image/png, not JSON")
    assert "image/png" in error.for_llm()
    assert error.message == "the response is image/png, not JSON"


def test_for_llm_never_doubles_the_period() -> None:
    assert ".." not in GcpApiError(403, None, "Denied.", ()).for_llm()
    assert ".." not in UnsupportedResponse("the body is not JSON.").for_llm()
    assert ".." not in GcpMcpError("plain message.").for_llm()


@pytest.mark.parametrize(
    ("status", "hints"),
    [
        (400, ("gcp_describe_method",)),
        (401, ("identity", "enabled", "not your arguments")),
        (403, ("identity", "enabled", "not your arguments")),
        (404, ("names",)),
        (429, ("Back off",)),
        (500, ("Retry once",)),
        (503, ("Retry once",)),
    ],
)
def test_gcp_api_error_names_the_next_move(status: int, hints: tuple[str, ...]) -> None:
    text = GcpApiError(status, None, "message", ()).for_llm()
    assert all(hint in text for hint in hints)
    assert text.endswith(".")


def test_gcp_api_error_without_a_known_next_move_reports_the_answer_alone() -> None:
    text = GcpApiError(409, "ABORTED", "Concurrent change", ()).for_llm()
    assert "Concurrent change" in text
    for hint in ("gcp_describe_method", "identity", "names", "Back off", "Retry once"):
        assert hint not in text
