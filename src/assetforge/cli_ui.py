"""`assetforge ui` (local web UI server for the desktop app) and `assetforge app install` (build AssetForge.app)."""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from assetforge.cli_common import JsonOpt, session

app_app = typer.Typer(help="The AssetForge Mac app.", no_args_is_help=True)


def register(app: typer.Typer) -> None:
    app.add_typer(app_app, name="app")

    @app.command("ui")
    def ui_command(
        ctx: typer.Context,
        port: Annotated[int, typer.Option(help="Port on 127.0.0.1 (0 = pick a free one).")] = 0,
        browser: Annotated[bool, typer.Option("--browser", help="Open the UI in the default web browser.")] = False,
        parent_pid: Annotated[int | None, typer.Option(help="Exit when this process exits (used by the Mac app).")] = None,
        token: Annotated[str | None, typer.Option(help="Fixed session token (default: random).", hidden=True)] = None,
        verbose: Annotated[bool, typer.Option("--verbose", help="Log every request to stderr.")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Serve the AssetForge desktop UI (overview, asset browser, generation) on 127.0.0.1."""
        from assetforge.app.api import router
        from assetforge.app.server import AppServer, watch_parent

        with session(ctx, json_) as s:
            home = s.lib.paths.home
        server = AppServer(home, router, port=port, token=token, verbose=verbose)
        info = {"url": server.url, "port": server.port, "token": server.token, "home": str(server.home)}
        if json_:
            sys.stdout.write(json.dumps(info) + "\n")
        else:
            sys.stdout.write(f"AssetForge UI for {server.home}\n  {server.url}\n  (Ctrl-C to stop)\n")
        sys.stdout.flush()
        if parent_pid:
            watch_parent(parent_pid, server)
        if browser:
            webbrowser.open(server.url)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            server.close_pool()


@app_app.command("install")
def app_install(
    ctx: typer.Context,
    dest: Annotated[Path, typer.Option(help="Folder to put AssetForge.app in.")] = Path("~/Applications"),
    name: Annotated[str, typer.Option(help="App name.")] = "AssetForge",
    json_: JsonOpt = False,
) -> None:
    """Build (or update) AssetForge.app: a native window around the UI, launchable from the Dock or Spotlight."""
    with session(ctx, json_) as s:
        from assetforge.app.installer import install

        result = install(dest.expanduser(), name=name)
        s.out.emit({"install": {k: str(v) if isinstance(v, Path) else v for k, v in result.items()}},
                   lambda c: c.print(f"Installed {result['app']}\nOpen it from Finder, Spotlight or: open \"{result['app']}\""))  # fmt: skip
