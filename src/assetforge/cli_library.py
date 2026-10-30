"""`assetforge library …`: whole-library operations (a shareable snapshot for a git repo)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from assetforge.cli_common import JsonOpt, session

library_app = typer.Typer(help="Whole-library operations: a shareable snapshot for teammates.", no_args_is_help=True)


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
