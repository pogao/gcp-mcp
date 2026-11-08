import logging

import pytest
import structlog

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
