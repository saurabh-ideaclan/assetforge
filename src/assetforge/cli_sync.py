"""`assetforge sync …`: per-device spend shared between devices, and code updates from GitHub `main`."""

from __future__ import annotations

import time
from typing import Annotated, Any

import typer
from rich.console import Console

from assetforge import updater
from assetforge.cli_common import JsonOpt, session
from assetforge.core import budget, devices
from assetforge.core.util import utcnow

sync_app = typer.Typer(help="Share spend between devices (one API key, many Macs) and keep every device on GitHub main.",
                       no_args_is_help=True)  # fmt: skip


def _print_update(c: Console, u: dict[str, Any]) -> None:
    line = f"update: {u.get('status')}"
    if u.get("reason") or u.get("reasons"):
        line += f" ({u.get('reason') or '; '.join(u['reasons'])})"
    if u.get("status") == "updated":
        line += f" {u['from']} → {u['to']} ({u['commits']} commits; {', '.join(u['steps']) or 'no extra steps'})"
    if u.get("error"):
        line += f": {u['error']}"
    c.print(line)


@sync_app.command("status")
def sync_status(
    ctx: typer.Context,
    fetch: Annotated[bool, typer.Option(help="Ask GitHub whether main has moved.")] = True,
    json_: JsonOpt = False,
) -> None:
    """This device, every device's spend, and whether this checkout is on GitHub's latest main."""
    with session(ctx, json_) as s:
        home = s.lib.paths.home
        try:
            code: dict[str, Any] = updater.status(fetch=fetch)
            code["unmerged_branches"] = updater.unmerged_branches(updater.repo_root())
        except Exception as exc:  # noqa: BLE001
            code = {"error": str(exc)}
        data = {"device": devices.device(home), "budget": budget.summary(s.lib), "code": code,
                "state": updater.load_state(home), "agent_installed": updater.agent_path().exists()}  # fmt: skip

        def human(c: Console) -> None:
            b = data["budget"]
            c.print(f"device: {data['device']['name']} ({data['device']['id']})")
            for period in ("daily", "monthly"):
                p = b[period]
                c.print(f"{period:>8}: this device ${p['this_device_usd']:.3f} + others ${p['other_devices_usd']:.3f}"
                        f" = ${p['this_device_usd'] + p['other_devices_usd']:.3f}")  # fmt: skip
            for o in b["devices"]["others"]:
                c.print(f"  {o['name']}: ${o['daily_usd']:.3f} today, ${o['monthly_usd']:.3f} this month "
                        f"(synced {o['age_hours']} h ago)")  # fmt: skip
            st = data["state"]
            c.print(f"spend last synced: {st.get('spend_synced_at', 'never')}"
                    + (f" (last error: {st['spend_error']})" if st.get("spend_error") else ""))  # fmt: skip
            if "error" in code:
                c.print(f"code: {code['error']}")
            else:
                c.print(f"code: {code['branch']} @ {code['head']}, GitHub main @ {code['origin_main']} "
                        f"(behind {code['behind']}, ahead {code['ahead']}{', uncommitted changes' if code['dirty'] else ''})")  # fmt: skip
                for name, n in code["unmerged_branches"].items():
                    c.print(f"  [yellow]branch {name} has {n} commit(s) not on main[/yellow]")
            c.print(f"auto-sync agent: {'installed' if data['agent_installed'] else 'not installed (assetforge sync install)'}")

        s.out.emit({"sync": data}, human)


@sync_app.command("spend")
def sync_spend(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Publish this device's spend and pull the other devices' now (normally hourly via the agent)."""
    with session(ctx, json_) as s:
        result = devices.sync(s.lib, force=True)
        if result["status"] == "ok":
            updater.save_state(s.lib.paths.home, spend_synced_epoch=time.time(), spend_synced_at=utcnow(), spend_error=None)

        def human(c: Console) -> None:
            if result["status"] != "ok":
                c.print(f"skipped: {result.get('reason')}")
                return
            c.print(f"published {result['device']['name']} → {result['branch']} ({'pushed' if result['pushed'] else 'unchanged'})")
            for o in result["others"]:
                c.print(f"  {o['name']}: ${o['daily_usd']:.3f} today, ${o['monthly_usd']:.3f} this month")

        s.out.emit({"spend_sync": result}, human)


@sync_app.command("update")
def sync_update(
    ctx: typer.Context,
    now: Annotated[bool, typer.Option("--now", help="Don't wait for an idle moment.")] = False,
    restart: Annotated[bool, typer.Option(help="Relaunch AssetForge.app after updating if it is open.")] = True,
    json_: JsonOpt = False,
) -> None:
    """Fast-forward this checkout to GitHub main (only when idle unless --now) and restart the app."""
    with session(ctx, json_) as s:
        result = updater.apply(s.lib, now=now, restart=restart)
        updater.save_state(s.lib.paths.home, update_checked_at=utcnow(), update=result)
        s.out.emit({"update": result}, lambda c: _print_update(c, result))


@sync_app.command("publish")
def sync_publish(
    ctx: typer.Context,
    allow_unmerged: Annotated[bool, typer.Option(help="Push even if other branches hold commits main lacks.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Push main to GitHub so the other devices pick it up (refuses if work sits on another branch)."""
    with session(ctx, json_) as s:
        result = updater.publish(allow_unmerged=allow_unmerged)

        def human(c: Console) -> None:
            if result["status"] == "pushed":
                c.print(f"pushed {result['commits']} commit(s); main is {result['head']} on GitHub")
            else:
                c.print(f"nothing to push; GitHub main is {result['origin_main']}")
            for name, n in result["unmerged"].items():
                c.print(f"  [yellow]branch {name} still has {n} commit(s) not on main[/yellow]")

        s.out.emit({"publish": result}, human)


@sync_app.command("tick")
def sync_tick(
    ctx: typer.Context,
    force_spend: Annotated[bool, typer.Option(help="Sync spend even if the last sync is recent.")] = False,
    json_: JsonOpt = False,
) -> None:
    """One round of the background agent: spend sync when due, then the update check."""
    with session(ctx, json_) as s:
        updater.trim_log(s.lib.paths.home)
        result = updater.tick(s.lib, force_spend=force_spend)

        def human(c: Console) -> None:
            sp = result["spend"]
            c.print(f"spend: {sp.get('status')}" + (f" ({sp.get('error') or sp.get('reason')})" if sp.get("error") or sp.get("reason") else ""))
            _print_update(c, result["update"])

        s.out.emit({"tick": result}, human)


@sync_app.command("install")
def sync_install(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Install the background agent (every 10 min: spend sync when due, update when idle; starts at login)."""
    with session(ctx, json_) as s:
        result = updater.install_agent(s.lib.paths.home)
        s.out.emit({"agent": result}, lambda c: c.print(
            f"installed {result['plist']} (every {result['every_seconds'] // 60} min); log: {result['log']}"))


@sync_app.command("uninstall")
def sync_uninstall(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Remove the background agent."""
    with session(ctx, json_) as s:
        result = updater.uninstall_agent()
        s.out.emit({"agent": result}, lambda c: c.print("removed" if result["removed"] else "was not installed"))
