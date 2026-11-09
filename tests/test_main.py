import json
import logging

import pytest
import structlog
from joserfc.jwk import RSAKey

from gcp_mcp.__main__ import main

HTTP_AUTH_ARGV = [
    *("--transport", "http", "--require-auth", "--base-url", "https://mcp.example.com"),
    *("--auth-issuer", "https://issuer.example.com"),
]


@pytest.fixture
def restored_loggers():
    saved = [
        (logger, logger.level, list(logger.handlers), logger.propagate)
        for logger in map(logging.getLogger, (None, "fastmcp", "httpx2"))
    ]
    yield
    for logger, level, handlers, propagate in saved:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def test_invalid_settings_exit_with_code_2(capsys):
    assert main(["--transport", "carrier-pigeon"]) == 2
    assert "transport" in capsys.readouterr().err


def test_help_lists_the_flags(capsys):
    assert main(["--help"]) == 0
    assert "--transport" in capsys.readouterr().out


def test_debug_logs_reach_stderr_as_json_when_serving(monkeypatch, restored_loggers, capsys):
    def fake_serve(server, config):
        structlog.get_logger("test").debug("serving")

    monkeypatch.setattr("gcp_mcp.__main__.serve", fake_serve)

    assert main(["--log-level", "DEBUG"]) == 0
    assert '"event": "serving"' in capsys.readouterr().err


def test_require_auth_over_stdio_exits_with_code_2(capsys):
    assert main(["--require-auth"]) == 2
    assert "invalid settings: require_auth needs transport=http" in capsys.readouterr().err


def test_a_public_key_flag_points_to_the_environment_variable(monkeypatch, capsys):
    monkeypatch.setattr("gcp_mcp.__main__.serve", lambda server, config: None)
    pem = RSAKey.generate_key(2048).as_pem(private=False).decode()

    assert main([*HTTP_AUTH_ARGV, "--auth-public-key", pem]) == 2
    assert "GCP_MCP_AUTH_PUBLIC_KEY" in capsys.readouterr().err


def test_scopes_in_the_environment_are_a_comma_separated_list(monkeypatch, restored_loggers):
    served = []
    monkeypatch.setattr("gcp_mcp.__main__.serve", lambda server, config: served.append(config))
    monkeypatch.setenv("GCP_MCP_AUTH_REQUIRED_SCOPES", "gcp:read, gcp:admin")

    assert main([*HTTP_AUTH_ARGV, "--auth-jwks-uri", "https://issuer.example.com/jwks"]) == 0
    assert served[0].auth.required_scopes == ("gcp:read", "gcp:admin")


def test_a_json_list_of_scopes_in_the_environment_exits_with_code_2(monkeypatch, capsys):
    monkeypatch.setattr("gcp_mcp.__main__.serve", lambda server, config: None)
    monkeypatch.setenv("GCP_MCP_AUTH_REQUIRED_SCOPES", '["gcp:read"]')

    assert main([*HTTP_AUTH_ARGV, "--auth-jwks-uri", "https://issuer.example.com/jwks"]) == 2
    assert "auth_required_scopes" in (err := capsys.readouterr().err)
    assert "comma-separated list" in err


def test_empty_allowed_subject_entries_in_the_environment_exit_with_code_2(monkeypatch, capsys):
    monkeypatch.setattr("gcp_mcp.__main__.serve", lambda server, config: None)
    monkeypatch.setenv("GCP_MCP_AUTH_ALLOWED_SUBJECTS", ",,")

    assert main([*HTTP_AUTH_ARGV, "--auth-jwks-uri", "https://issuer.example.com/jwks"]) == 2
    assert "auth_allowed_subjects" in capsys.readouterr().err


def test_fastmcp_log_records_reach_stderr_as_json_when_serving(
    monkeypatch, restored_loggers, capsys
):
    def fake_serve(server, config):
        logging.getLogger("fastmcp.server.auth.providers.jwt").warning("Bearer token rejected")

    monkeypatch.setattr("gcp_mcp.__main__.serve", fake_serve)

    assert main([]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert {"event": "Bearer token rejected", "level": "warning"}.items() <= lines[-1].items()


def test_http_request_lines_stay_out_of_the_info_log(monkeypatch, restored_loggers, capsys):
    def fake_serve(server, config):
        client_log = logging.getLogger("httpx2")
        client_log.info("HTTP Request: GET https://compute.googleapis.com/v1/projects/example-project")
        client_log.warning("connection pool is full")

    monkeypatch.setattr("gcp_mcp.__main__.serve", fake_serve)

    assert main([]) == 0
    err = capsys.readouterr().err
    assert "example-project" not in err
    assert "connection pool is full" in err
