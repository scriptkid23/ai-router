from typer.testing import CliRunner

from ai_router.cli.main import app
from ai_router.mcp.transport import Transport

runner = CliRunner(mix_stderr=False)


def test_serve_defaults_to_stdio(monkeypatch):
    captured: dict = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai_router.cli.serve.run_server", fake_run_server)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert captured["transport"] is Transport.STDIO
    assert result.stdout == ""


def test_serve_http_transport(monkeypatch):
    captured: dict = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai_router.cli.serve.run_server", fake_run_server)
    result = runner.invoke(app, ["serve", "--transport", "http", "--port", "9090"])
    assert result.exit_code == 0
    assert captured["transport"] is Transport.HTTP
    assert captured["port"] == 9090
    assert "9090" in result.stderr
    assert result.stdout == ""


def test_serve_invalid_transport_rejected():
    result = runner.invoke(app, ["serve", "--transport", "websocket"])
    assert result.exit_code != 0
    combined = (result.stdout + result.stderr).lower()
    assert "websocket" in combined or "invalid" in combined


def test_serve_stdio_ignores_host_port(monkeypatch):
    captured: dict = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai_router.cli.serve.run_server", fake_run_server)
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9999"])
    assert result.exit_code == 0
    assert captured["transport"] is Transport.STDIO
    assert result.stdout == ""


def test_import_mcp_server_writes_nothing_to_stdout(capsys):
    import importlib

    import ai_router.mcp.server as server_mod

    importlib.reload(server_mod)
    captured = capsys.readouterr()
    assert captured.out == ""
