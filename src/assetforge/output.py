"""CLI output: one JSON document on stdout in --json mode, Rich text otherwise.

Diagnostics and errors always go to stderr, so stdout stays parseable.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.core.models import Asset, AssetSummary, Collection, ImportReport, SearchHit
from assetforge.errors import AssetForgeError

Renderer = Callable[[Console], None]


class Output:
    def __init__(self, json_mode: bool) -> None:
        self.json_mode = json_mode
        self.console = Console(highlight=False)
        self.err = Console(stderr=True, highlight=False)

    def emit(self, payload: dict[str, Any], human: Renderer | None = None, *, status: str = "ok") -> None:
        if self.json_mode:
            document = {"status": status, **payload}
            sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()
        elif human is not None:
            human(self.console)

    def error(self, exc: AssetForgeError) -> None:
        if self.json_mode:
            self.emit({"error": exc.to_dict()}, status="error")
        self.err.print(f"[red]error[/red] [{exc.code}] {escape(exc.message)}")

    def warn(self, message: str) -> None:
        self.err.print(f"[yellow]warning[/yellow] {escape(message)}")


def _file_facts(asset: AssetSummary) -> str:
    if not asset.primary_variant:
        return "-"
    f = asset.primary_variant.file
    parts = [f.ext.lstrip(".")]
    if f.width and f.height:
        parts.append(f"{f.width}x{f.height}")
    if f.duration_ms is not None:
        parts.append(f"{f.duration_ms / 1000:.2f}s")
    if f.has_alpha:
        parts.append("alpha")
    return " ".join(parts)


def render_assets(console: Console, assets: list[AssetSummary] | list[SearchHit], *, title: str | None = None) -> None:
    if not assets:
        console.print("[dim]No assets found.[/dim]")
        return
    scored = any(isinstance(a, SearchHit) and a.score is not None for a in assets)
    table = Table(title=title, show_lines=False, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", overflow="fold")
    table.add_column("Type")
    table.add_column("File")
    if scored:
        table.add_column("Score", justify="right")
        table.add_column("Cover", justify="right")
    table.add_column("Tags", style="dim")
    table.add_column("Status")
    for a in assets:
        row = [a.id, escape(a.name), a.type.value, _file_facts(a)]
        if scored:
            hit = a if isinstance(a, SearchHit) else None
            row += [f"{hit.score:.2f}" if hit and hit.score is not None else "-",
                    f"{hit.coverage:.0%}" if hit and hit.coverage is not None else "-"]  # fmt: skip
        row += [escape(", ".join(a.tags[:6]) + (" …" if len(a.tags) > 6 else "")), a.status.value]
        table.add_row(*row)
    console.print(table)


def render_asset(console: Console, asset: Asset) -> None:
    console.print(f"[bold cyan]{asset.id}[/bold cyan]  [bold]{escape(asset.name)}[/bold]  ({asset.type.value})")
    facts = [
        ("key", asset.key),
        ("description", asset.description),
        ("status", asset.status.value),
        ("scope", asset.scope.value + (f" ({asset.project or asset.style})" if asset.scope.value != "global" else "")),
        ("project", asset.project),
        ("style", asset.style),
        ("origin", asset.origin.value),
        ("license", asset.license),
        ("commercial use", asset.commercial_use.value),
        ("attribution", asset.attribution),
        ("tags", ", ".join(asset.tags)),
        ("collections", ", ".join(asset.collections)),
        ("source", asset.source_uri),
        ("path", asset.path),
    ]
    for label, value in facts:
        if value:
            console.print(f"  [dim]{label:>14}[/dim]  {escape(str(value))}")
    console.print("  [dim]      variants[/dim]")
    for v in asset.variants:
        primary = " [green]*[/green]" if asset.primary_variant and v.id == asset.primary_variant.id else ""
        console.print(f"    {v.id}{primary} {escape(v.label)}  {v.file.mime_type}  {v.file.size_bytes:,} B")
        console.print(f"      {escape(v.file.path)}")
    for link in asset.lineage:
        console.print(f"  [dim]       lineage[/dim]  {link.direction} {link.relation.value} {link.asset_id}")
    for usage in asset.usage:
        console.print(f"  [dim]      used by[/dim]  {usage.project} ({usage.variant_id})")


def render_import(console: Console, report: ImportReport) -> None:
    s = report.summary
    verb = "Would import" if report.dry_run else "Imported"
    created = s["would_create"] if report.dry_run else s["created"]
    console.print(
        f"{verb} [bold]{created}[/bold] new asset(s); "
        f"{s['duplicate']} duplicate(s), {s['skipped']} skipped, {s['error']} error(s), "
        f"{s['ignored']} unsupported file(s) ignored."
    )
    for item in report.items:
        if item.action == "duplicate":
            target = item.duplicate_of or item.reason
            console.print(f"  [yellow]duplicate[/yellow] {escape(item.path)} -> {escape(str(target))}")
        elif item.action in ("error", "skipped"):
            reason = item.error["message"] if item.error else item.reason
            console.print(f"  [red]{item.action}[/red] {escape(item.path)}: {escape(str(reason))}")


def render_collections(console: Console, collections: list[Collection]) -> None:
    if not collections:
        console.print("[dim]No collections.[/dim]")
        return
    table = Table(header_style="bold")
    for column in ("ID", "Name", "Kind", "Project", "Assets", "Description"):
        table.add_column(column)
    for c in collections:
        table.add_row(c.id, c.name, c.kind, c.project or "-", str(c.asset_count), escape(c.description))
    console.print(table)
