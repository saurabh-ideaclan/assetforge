"""`assetforge library …`: whole-library operations (a shareable snapshot for a git repo)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from assetforge.cli_common import JsonOpt, session

library_app = typer.Typer(help="Whole-library operations: a shareable snapshot for teammates, merging another library in.", no_args_is_help=True)


@library_app.command("snapshot")
def snapshot_command(
    ctx: typer.Context,
    dest: Annotated[Path, typer.Argument(help="Folder to write (or update) the shareable copy in, e.g. a git checkout.")],
    json_: JsonOpt = False,
) -> None:
    """Write a shareable copy of the library into DEST, for committing to a (private) git repo.

    Copies the catalog and every stored file, without WAVs: synthesized masters are pruned (they re-render bit
    for bit on demand) and other WAV originals are left out (their OGG copy becomes the primary file). Jobs,
    previews, logs, backups, SoundFonts and creature packs are not copied. Refuses if the catalog contains
    something that looks like an API key. The library itself is only read. Re-run it to update DEST.
    """
    from assetforge.core.snapshot import snapshot

    with session(ctx, json_) as s:
        report = snapshot(s.lib, dest)

        def human(c: Console) -> None:
            wav = report["wav"]
            c.print(f"Snapshot at [bold]{escape(report['dest'])}[/bold]: {report['assets']} assets, "
                    f"{report['files']} files, {report['bytes'] / 1e6:.1f} MB")  # fmt: skip
            c.print(f"  copied {report['copied']} file(s) ({report['copied_bytes'] / 1e6:.1f} MB), "
                    f"removed {report['removed']}")  # fmt: skip
            c.print(f"  WAV masters pruned: {wav['pruned']} ({wav['pruned_bytes'] / 1e6:.1f} MB), "
                    f"left out as before: {wav['carried']}")  # fmt: skip
            for item in wav["withheld"]:
                now = item["primary"] or "[red]no OGG/M4A copy[/red]"
                c.print(f"  [dim]withheld {item['asset_id']} {escape(item['name'])} {item['label']}.wav → primary {now}[/dim]")

        s.out.emit({"snapshot": report}, human)


@library_app.command("merge")
def merge_command(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(help="Another AssetForge library folder (the one holding catalog.sqlite3).")],
    story: Annotated[list[Path] | None, typer.Option("--story", help="Also bring this story folder (repeatable).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report what would be merged; change nothing.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Bring everything from the library at SOURCE into this one: assets, variants, files, jobs, costs, profiles
    and stories. SOURCE is only read. Assets already here (same file) are linked, not duplicated; a key taken here
    by a different asset is never overwritten. Safe to re-run: only what's new is added.
    """
    from assetforge.core.merge import merge_library

    with session(ctx, json_) as s:
        report = merge_library(s.lib, source, dry_run=dry_run, stories=story)

        def human(c: Console) -> None:
            a, f = report["assets"], report["files"]
            head = "Would merge" if report["dry_run"] else "Merged"
            c.print(f"{head} [bold]{escape(report['source'])}[/bold]: {a['added']} new asset(s), {a['linked']} already here, "
                    f"{report['variants']} variant(s), {f['copied']} file(s) ({f['bytes'] / 1e6:.1f} MB), "
                    f"{report['jobs']} job(s)")  # fmt: skip
            for item in report["stories"]:
                c.print(f"  story {escape(Path(item['to']).name)}: {item['status']} ({item['files']} file(s))")
            for k in report["key_conflicts"]:
                c.print(f"  [yellow]key taken here[/yellow] {escape(k['project'] or '-')}:{escape(k['key'])} → {k['asset_id']} kept without key")
            for m in f["missing"]:
                c.print(f"  [red]missing in source[/red] {escape(m)}")
            c.print(f"  library now has {report['totals']['assets']} assets")

        s.out.emit({"merge": report}, human)
