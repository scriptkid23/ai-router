from typer.testing import CliRunner

from ai_router.cli.main import app

runner = CliRunner(mix_stderr=False)


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.1"
    assert result.stdout.endswith("\n") or result.stdout == "0.1.1"
