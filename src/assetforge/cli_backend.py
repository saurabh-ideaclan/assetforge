"""`assetforge backend …`: the content backend in the R2 bucket (channels, signed index, deltas). Every write needs
--yes and shows its plan without it; nothing here runs automatically. See publish/backend.py and doc 27."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.cli_common import JsonOpt, session
from assetforge.errors import ExitCode

backend_app = typer.Typer(help="The published content in the R2 bucket: status, publish to staging, promote to production, "
                               "rollback, unlist, verify, repair, inventory, gc, and the app team's client kit.",
                          no_args_is_help=True)  # fmt: skip

Story = Annotated[str, typer.Argument(help="Story id (registered id, folder, or the storyId in the bucket).")]
Release = Annotated[str | None, typer.Option("--release", help="Release id (default: the latest / the current one).")]
Yes = Annotated[bool, typer.Option("--yes", help="Really write. Without it only the plan is shown; nothing is written.")]
ChannelOpt = Annotated[str, typer.Option("--channel", help="staging or production.")]


def _project(s: Any, story: str) -> Any:
    from assetforge.cli_story import _story_ref

    return _story_ref(s, story, None)


def _story_id(s: Any, story: str) -> str:
    """A registered id or folder → the contract storyId of its releases; anything else is taken as is."""
    from assetforge.errors import NotFoundError
    from assetforge.story import contract

    try:
        project = _project(s, story)
    except NotFoundError:
        return story
    return contract.load_index(contract.releases_root(project)).get("storyId") or story


def _plan_or_done(con: Console, result: dict[str, Any], done: str) -> None:
    if result.get("written") is False:
        con.print(f"[dim]{escape(str(result.get('message') or 'Nothing was written. Pass --yes to do it.'))}[/dim]")
    else:
        con.print(f"[green]{done}[/green]")


def _show_publish_plan(con: Console, result: dict[str, Any]) -> None:
    t = result["totals"]
    con.print(f"Plan for {result['story']} {result['release']} ({result['graph_id']}) → {result['channel']} · remote: {escape(result['remote'])}")
    con.print(f"upload {t['upload']['count']} ({t['upload']['bytes']:,} B) · skip {t['skip']['count']} · "
              f"unchecked {t['unknown']['count']} · conflicts {t['conflict']['count']}")  # fmt: skip
    if result.get("delta"):
        con.print(f"Delta: {result['delta']}")
    if result.get("catalog"):
        c = result["catalog"]
        con.print(f"Index {c['key']}: sequence {c['current_sequence']} → {c['next_sequence']} (current release: {c['current_release'] or 'none'})")
    if not result["valid"] or result["draft"]:
        con.print("[red]Not publishable:[/red] " + ("draft" if result["draft"] else f"{len(result['errors'])} validation error(s)"))
    con.print("[dim]Nothing was written. Pass --yes to publish.[/dim]")


@backend_app.command("status")
def status_(ctx: typer.Context, channel: Annotated[str | None, typer.Option("--channel", help="Only this channel.")] = None,
            json_: JsonOpt = False) -> None:  # fmt: skip
    """Configuration, reachability, each channel's sequence and every story: local latest, staging, production,
    state. Reads only."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        view = backend.status(home=s.lib.paths.home, channel=channel)

        def human(con: Console) -> None:
            cfg = view.get("config") or {}
            conn = view["connection"]
            con.print(f"Bucket: {cfg.get('bucket', '-')} · prefix: {cfg.get('prefix') or '(none)'} · "
                      + ("[green]reachable[/green]" if conn["ok"] else f"[red]{escape(conn['error']['code'])}[/red] {escape(conn['error']['message'])}"))  # fmt: skip
            if not conn["ok"] and conn.get("fix"):
                con.print(f"Fix: {escape(conn['fix'])}")
            for ch, info in view["channels"].items():
                url = f" · {info['index_url']}" if info.get("index_url") else ""
                con.print(f"{ch}: sequence {info['sequence'] or 0}{' (stale pointer)' if info.get('stale_pointer') else ''}{url}")
            if view["key"]:
                con.print(f"Public key {view['key']['key_id']}: {view['key']['public_key']}")
            table = Table(header_style="bold")
            for col in ("Story", "Local", "Staging", "Production", "State"):
                table.add_column(col)
            for r in view["stories"]:
                table.add_row(r["storyId"], r["local_latest"] or "-", r["staging"] or "-", r["production"] or "-", r["state"])
            con.print(table if view["stories"] else "No stories with releases.")

        s.out.emit(view, human, status="ok" if view["connection"]["ok"] else "partial")


@backend_app.command("plan")
def plan_(ctx: typer.Context, story: Story, release: Release = None, json_: JsonOpt = False) -> None:
    """What a publish would upload, skip and refuse, with byte totals. Reads only."""
    from assetforge.publish import backend
    from assetforge.story import contract

    with session(ctx, json_) as s:
        path = contract.release_dir(_project(s, story), release)
        result = backend.plan(path, home=s.lib.paths.home)
        s.out.emit({"plan": result, "published": False}, lambda c: _show_publish_plan(c, result))


def publish_command(s: Any, story: str | None, directory: Path | None, release: str | None, out: Path | None, yes: bool) -> None:
    """Shared by `backend publish` and its alias `story publish`."""
    from assetforge.cli_story import _story_ref
    from assetforge.publish import backend
    from assetforge.story import contract

    path = contract.release_dir(_story_ref(s, story, directory), release, out)
    home = s.lib.paths.home
    if not yes:
        result = backend.plan(path, home=home)
        s.out.emit({"plan": result, "published": False}, lambda c: _show_publish_plan(c, result))
        return
    receipt = backend.publish(path, yes=True, home=home)
    message = (receipt.get("message") if receipt.get("unchanged") else
               f"published {receipt['release']} to {receipt['channel']} · index sequence {receipt['sequence']} · "
               f"{receipt['uploaded']} uploaded, {receipt['skipped']} already there")  # fmt: skip
    s.out.emit({"receipt": receipt, "published": not receipt.get("unchanged")}, lambda c: c.print(f"[green]{escape(str(message))}[/green]"))


@backend_app.command("publish")
def publish_(ctx: typer.Context, story: Story, release: Release = None, yes: Yes = False, json_: JsonOpt = False) -> None:
    """Upload a validated release (and its delta) and publish the signed staging index last. Without --yes: the plan."""
    with session(ctx, json_) as s:
        publish_command(s, story, None, release, None, yes)


@backend_app.command("promote")
def promote_(ctx: typer.Context, story: Story, release: Release = None, yes: Yes = False, json_: JsonOpt = False) -> None:
    """Make a release already on staging current on production (every object checked first; only the index is written)."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        result = backend.promote(_story_id(s, story), release=release, yes=yes, home=s.lib.paths.home)

        def human(con: Console) -> None:
            con.print(f"Promote {result['story']} {result['release']} to production (from {result['from_release'] or 'nothing'}): "
                      f"{result['objects']} objects checked, {len(result['missing'])} missing")  # fmt: skip
            _plan_or_done(con, result, f"production index sequence {result.get('sequence')}")

        s.out.emit(result, human)


@backend_app.command("rollback")
def rollback_(ctx: typer.Context, story: Story, release: Annotated[str, typer.Argument(help="The older release to make current.")],
              channel: ChannelOpt = "staging", yes: Yes = False, json_: JsonOpt = False) -> None:  # fmt: skip
    """A new index sequence pointing the story at an older release already published on the channel."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        result = backend.rollback(_story_id(s, story), release, channel=channel, yes=yes, home=s.lib.paths.home)

        def human(con: Console) -> None:
            con.print(f"Roll back {result['story']} on {channel}: {result['from_release']} → {result['release']}")
            _plan_or_done(con, result, f"{channel} index sequence {result.get('sequence')}")

        s.out.emit(result, human)


def _listing(ctx: typer.Context, story: str, channel: str, yes: bool, json_: bool, listed: bool) -> None:
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        result = backend.set_listed(_story_id(s, story), listed=listed, channel=channel, yes=yes, home=s.lib.paths.home)

        def human(con: Console) -> None:
            con.print(f"{result['story']} on {channel}: {result['status_from']} → {result['status_to']}")
            _plan_or_done(con, result, f"{channel} index sequence {result.get('sequence')}")

        s.out.emit(result, human)


@backend_app.command("unlist")
def unlist_(ctx: typer.Context, story: Story, channel: ChannelOpt = "staging", yes: Yes = False, json_: JsonOpt = False) -> None:
    """Hide a story from the app's shelf (the entry stays, saves keep working)."""
    _listing(ctx, story, channel, yes, json_, listed=False)


@backend_app.command("relist")
def relist_(ctx: typer.Context, story: Story, channel: ChannelOpt = "staging", yes: Yes = False, json_: JsonOpt = False) -> None:
    """Show an unlisted story again."""
    _listing(ctx, story, channel, yes, json_, listed=True)


@backend_app.command("verify")
def verify_(ctx: typer.Context, channel: Annotated[str | None, typer.Option("--channel", help="Only this channel.")] = None,
            deep: Annotated[bool, typer.Option("--deep", help="Download and hash every object.")] = False,
            public: Annotated[bool, typer.Option("--public", help="Also read through ASSETFORGE_R2_PUBLIC_BASE anonymously.")] = False,
            json_: JsonOpt = False) -> None:  # fmt: skip
    """Check every object any listed release needs. Reads only."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        report = backend.verify(home=s.lib.paths.home, channel=channel, deep=deep, public=public)

        def human(con: Console) -> None:
            con.print(f"{report['objects']} objects, {report['ok']} ok, {len(report['missing'])} missing, "
                      f"{len(report['corrupted'])} corrupted, {len(report['unreadable'])} unreadable")  # fmt: skip
            for r in report["missing"] + report["corrupted"]:
                con.print(f"  [red]{r['state']}[/red] {r['key']}")
            if public and "public" in report:
                con.print("Public read: " + ("[green]ok[/green]" if report["public"].get("ok") else f"[red]failed[/red] {escape(json.dumps(report['public'])[:400])}"))

        s.out.emit(report, human, status="ok" if report["healthy"] else "partial")
        if not report["healthy"]:
            raise typer.Exit(int(ExitCode.PARTIAL))


@backend_app.command("repair")
def repair_(ctx: typer.Context, yes: Yes = False, deep: Annotated[bool, typer.Option("--deep", help="Verify by hashing.")] = False,
            json_: JsonOpt = False) -> None:  # fmt: skip
    """Re-upload objects verify found missing or corrupted, only from a local copy with the declared digest."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        result = backend.repair(home=s.lib.paths.home, yes=yes, deep=deep)

        def human(con: Console) -> None:
            con.print(f"Repairable: {len(result['fixable'])} · not repairable: {len(result['unfixable'])}")
            for r in result["unfixable"]:
                con.print(f"  [red]{r['state']}[/red] {r['key']} ({r['reason']})")
            _plan_or_done(con, result, f"repaired {result.get('repaired', 0)} object(s)")

        s.out.emit(result, human)


@backend_app.command("ls")
def ls_(ctx: typer.Context, orphans: Annotated[bool, typer.Option("--orphans", help="Only objects no index references.")] = False,
        json_: JsonOpt = False) -> None:  # fmt: skip
    """Inventory of the objects under the prefix: referenced by some index in some channel's history, or orphaned."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        inv = backend.ls(home=s.lib.paths.home, orphans=orphans)

        def human(con: Console) -> None:
            for r in inv["objects"]:
                con.print(f"{'ref   ' if r['referenced'] else '[yellow]orphan[/yellow]'} {r['bytes']:>12,}  {r['key']}")
            con.print(f"{inv['count']} object(s), {inv['bytes']:,} B, {len(inv['orphans'])} orphan(s) under prefix {inv['prefix'] or '(none)'}")

        s.out.emit(inv, human)


@backend_app.command("gc")
def gc_(ctx: typer.Context, yes: Yes = False,
        delete_orphans: Annotated[bool, typer.Option("--delete-orphans", help="Really delete the orphans (with --yes).")] = False,
        whole_bucket: Annotated[bool, typer.Option("--whole-bucket", help="Allow deleting with no prefix: the entire "
                                                                          "bucket belongs to AssetForge.")] = False,
        json_: JsonOpt = False) -> None:  # fmt: skip
    """Delete orphans only (never anything an index in any history references). A dry run by default."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        result = backend.gc(home=s.lib.paths.home, yes=yes, delete_orphans=delete_orphans, whole_bucket=whole_bucket)

        def human(con: Console) -> None:
            for r in result["orphans"]:
                con.print(f"orphan {r['bytes']:>12,}  {r['key']}")
            con.print(f"{len(result['orphans'])} orphan(s), {result['bytes']:,} B")
            _plan_or_done(con, result, f"deleted {result.get('deleted', 0)} object(s)")

        s.out.emit(result, human)


@backend_app.command("pull")
def pull_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Rebuild the local published-state records from the bucket (local writes only)."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        result = backend.pull(home=s.lib.paths.home)
        s.out.emit(result, lambda c: c.print(f"Channels: {result['channels']} · stories updated: {', '.join(result['stories_updated']) or 'none'}"))


@backend_app.command("client-kit")
def client_kit_(ctx: typer.Context, out: Annotated[Path, typer.Option("--out", help="Folder to write the kit into.")] = Path("vn-client-kit"),
                json_: JsonOpt = False) -> None:  # fmt: skip
    """The public key, JSON Schemas, a sample index and delta, the reference client and INTEGRATION.md (local only)."""
    from assetforge.publish import backend

    with session(ctx, json_) as s:
        result = backend.client_kit(home=s.lib.paths.home, out=out)

        def human(con: Console) -> None:
            con.print(f"Client kit in {escape(result['out'])}: {len(result['files'])} files")
            for w in result["warnings"]:
                con.print(f"[yellow]{escape(w)}[/yellow]")

        s.out.emit(result, human, status="partial" if result["warnings"] else "ok")
