import os

import pytest


@pytest.fixture(autouse=True)
def without_gcp_mcp_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("GCP_MCP_"):
            monkeypatch.delenv(name)
