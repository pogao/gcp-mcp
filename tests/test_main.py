import logging

import pytest
import structlog
from joserfc.jwk import RSAKey

from gcp_mcp.__main__ import main


@pytest.fixture
def root_logger():
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield root
    root.handlers[:] = handlers
    root.setLevel(level)


def test_invalid_settings_exit_with_code_2(capsys):
    assert main(["--transport", "carrier-pigeon"]) == 2
    assert "transport" in capsys.readouterr().err


def test_help_lists_the_flags(capsys):
    assert main(["--help"]) == 0
    assert "--transport" in capsys.readouterr().out


def test_debug_logs_reach_stderr_as_json_when_serving(monkeypatch, root_logger, capsys):
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
    argv = ["--transport", "http", "--require-auth", "--base-url", "https://mcp.example.com"]
    argv += ["--auth-issuer", "https://issuer.example.com", "--auth-public-key", pem]

    assert main(argv) == 2
    assert "GCP_MCP_AUTH_PUBLIC_KEY" in capsys.readouterr().err
