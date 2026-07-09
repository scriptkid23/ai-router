import typer

from ai_router.cli.browser import browser_app
from ai_router.cli.serve import serve_cmd

app = typer.Typer(name="ai", help="ai-router — web AI provider automation")
app.command("serve")(serve_cmd)
app.add_typer(browser_app, name="browser")

if __name__ == "__main__":
    app()
