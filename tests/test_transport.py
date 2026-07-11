from unittest.mock import MagicMock

import pytest

from ai_router.mcp.server import run_server
from ai_router.mcp.transport import Transport


def test_transport_values():
    assert Transport.STDIO.value == "stdio"
    assert Transport.HTTP.value == "http"


def test_transport_is_str_enum():
    assert isinstance(Transport.STDIO, str)
    assert Transport("stdio") is Transport.STDIO


@pytest.fixture
def fake_mcp(monkeypatch):
    instances: list[MagicMock] = []

    def factory(host: str, port: int):
        mcp = MagicMock()
        instances.append(mcp)
        return mcp

    monkeypatch.setattr("ai_router.mcp.server.create_mcp_app", factory)
    return instances


def test_run_server_stdio_calls_stdio_transport(fake_mcp):
    run_server(transport=Transport.STDIO)
    assert len(fake_mcp) == 1
    fake_mcp[0].run.assert_called_once_with(transport="stdio")


def test_run_server_http_calls_streamable_http(fake_mcp):
    run_server(host="127.0.0.1", port=9090, transport=Transport.HTTP)
    assert len(fake_mcp) == 1
    fake_mcp[0].run.assert_called_once_with(transport="streamable-http")


def test_run_server_stdio_no_stderr_banner(fake_mcp, capsys):
    run_server(transport=Transport.STDIO)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
