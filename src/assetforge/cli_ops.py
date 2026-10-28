"""Phase 06 commands: `request`, `worker`, `config`, `budget`, `errors`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.cli_common import JsonOpt, session
from assetforge.core import budget as budget_mod
from assetforge.core import limits, settings_store
from assetforge.core.models import AssetType
from assetforge.errors import CATALOG, InvalidInputError, NotFoundError
from assetforge.request import AssetRequest, RequestResult, handle
from assetforge.worker import run_worker

config_app = typer.Typer(help="Library-wide settings shared by all agents (budgets, provider limits, queue).",
                         no_args_is_help=True)  # fmt: skip


def render_request(c: Console, r: RequestResult) -> None:
    if r.state == "dry_run":
        if r.would == "reuse":
            extra = f" (coverage {r.match['coverage']:.0%})" if r.match and r.match.get("coverage") is not None else ""
            c.print(f"[green]Would reuse[/green] {r.asset_id} ({r.reuse_reason}){extra}: {escape(r.asset.path or '') if r.asset else ''}")
            if r.plan:
                c.print(f"  adapted with: {r.plan['adapt_ops']}")
        else:
            p = r.plan or {}
            cost = p.get("estimated_cost_usd", p.get("cost_usd"))
            c.print(f"[yellow]Would {r.would}[/yellow] via {p.get('method')}"
                    + (f" ({p.get('provider')}/{p.get('model')})" if p.get("provider") else "")
                    + (f", ~${cost:.3f}" if cost else ", free"))  # fmt: skip
            if p.get("prompt"):
                c.print(f"  [dim]prompt:[/dim] {escape(p['prompt'])}")
        for alt in r.alternatives:
            c.print(f"  [dim]near miss {alt['asset_id']} {alt['name']} (coverage {alt['coverage'] or 0:.0%})[/dim]")
        return
    if r.reused:
        how = {"key": "already used under this key", "request": "identical request done before",
               "in_flight": "joined an identical in-flight request", "search": "matching asset in the library"}  # fmt: skip
        extra = f" (coverage {r.match['coverage']:.0%})" if r.match else ""
        c.print(f"[green]Reused[/green] {r.asset_id}: {how[r.reuse_reason or 'search']}{extra}")
    elif r.state in ("queued", "running"):
        c.print(f"Request is [yellow]{r.state}[/yellow] as {r.job_id}; `assetforge jobs wait {r.job_id}` to block on it")
    elif r.state == "needs_review":
        c.print(f"Generated {len(r.candidates)} candidates ({r.job_id}); review with "
                f"`assetforge preview --job {(r.generated or {}).get('job_id')}` then `assetforge review pick`")  # fmt: skip
    else:
        g = r.generated or {}
        source = g.get("recipe") and f"recipe {g['recipe']}" or f"{g.get('provider')} {g.get('model')}"
        c.print(f"[bold cyan]Generated[/bold cyan] {r.asset_id} with {source} ({r.job_id})")
    if r.adapted:
        c.print(f"  adapted from {r.adapted['from_asset']} with {r.adapted['ops']}")
    for v in r.variants:
        c.print(f"  {v['label']:>10} {v['variant_id']}  {escape(v['path'])}")
    for cand in r.candidates:
        c.print(f"  candidate {cand['asset_id']}  {escape(cand['path'])}")
    for dup in r.near_duplicates:
        c.print(f"  [yellow]looks like existing {dup['asset_id']}[/yellow] (distance {dup['distance']})")


def register(app: typer.Typer) -> None:
    app.add_typer(config_app, name="config")

    @app.command("request")
    def request_command(
        ctx: typer.Context,
        file: Annotated[Path | None, typer.Option("--file", "-f", help="JSON request ('-' reads stdin).")] = None,
        type_: Annotated[AssetType | None, typer.Option("--type", help="sfx, ambience, music, character, sprite, background, icon…")] = None,
        description: Annotated[str | None, typer.Option("--description", "-d", help="What you need.")] = None,
        project: Annotated[str | None, typer.Option(help="Your game/project (records usage).")] = None,
        key: Annotated[str | None, typer.Option(help="How your project refers to it, e.g. enemy/beetle/hit.")] = None,
        style: Annotated[str | None, typer.Option(help="Preset (`art presets`) or any style label.")] = None,
        art_file: Annotated[Path | None, typer.Option("--art-file", help="YAML/JSON art direction for this asset.")] = None,
        prompt: Annotated[str | None, typer.Option("--prompt", "-p", help="Detailed image prompt (default: the description).")] = None,
        purpose: Annotated[str | None, typer.Option(help="What the asset is for in the game.")] = None,
        size: Annotated[str | None, typer.Option(help="Wanted size WxH; reused images are resized to it as a variant.")] = None,
        category: Annotated[str | None, typer.Option(help="Catalog category (also favoured when reusing).")] = None,
        genre: Annotated[str | None, typer.Option(help="Game genre (also favoured when reusing).")] = None,
        method: Annotated[str | None, typer.Option(help="auto, ai, svg, procedural or file (with --path).")] = None,
        path: Annotated[Path | None, typer.Option("--path", help="Your own file to register (method svg/procedural/file).")] = None,
        max_cost: Annotated[float | None, typer.Option(help="Refuse image calls estimated above this many USD.")] = None,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Say whether it would reuse or generate, and the cost.")] = False,
        reuse_policy: Annotated[str | None, typer.Option(help="prefer_existing, require_existing, always_generate.")] = None,
        format_: Annotated[str | None, typer.Option("--format", help="Output format (ogg/mp3/wav, png/webp).")] = None,
        process: Annotated[str | None, typer.Option(help="Post-processing preset, e.g. web-sprite or web-sfx.")] = None,
        provider: Annotated[str | None, typer.Option(help="Image provider (openai, gemini, auto).")] = None,
        sound: Annotated[list[str] | None, typer.Option("--sound", help="Sound engine option name=value (repeatable; JSON values "
                                                                        "allowed), e.g. scene=cave, tempo=140, stems=true.")] = None,
        async_: Annotated[bool, typer.Option("--async", help="Queue it for `assetforge worker` and return at once.")] = False,
        wait_seconds: Annotated[float | None, typer.Option(help="Max wait for an identical in-flight request.")] = None,
        json_: JsonOpt = False,
    ) -> None:
        """Get an asset: reuse a matching one if possible, otherwise generate it. Returns ready-to-use paths.

        Reused images are adapted to --size/--format/--process as a new variant (originals never change).
        Start with --dry-run to see whether it would reuse (and which asset) or generate (and the cost).
        """
        with session(ctx, json_) as s:
            data: dict[str, Any] = {}
            if file is not None:
                try:
                    text = sys.stdin.read() if str(file) == "-" else file.read_text()
                    data = json.loads(text)
                except FileNotFoundError as exc:
                    raise NotFoundError(f"Request file not found: {file}") from exc
                except json.JSONDecodeError as exc:
                    raise InvalidInputError(f"Request file is not valid JSON: {exc}") from exc
                if not isinstance(data, dict):
                    raise InvalidInputError("The request must be a JSON object")
            flags = {"type": type_, "description": description, "project": project, "key": key, "style": style,
                     "reuse_policy": reuse_policy, "format": format_, "process": process, "provider": provider,
                     "prompt": prompt, "purpose": purpose, "size": size, "category": category, "genre": genre,
                     "method": method, "file": str(path) if path else None, "max_cost": max_cost,
                     "dry_run": dry_run or None}  # fmt: skip
            data |= {k: v for k, v in flags.items() if v is not None}
            if art_file is not None:
                from assetforge.art import read_direction_file

                data["art"] = {**data.get("art", {}), **read_direction_file(art_file).model_dump(exclude_defaults=True)}
            if sound:
                options = dict(data.get("sound", {}))
                for pair in sound:
                    name, sep, value = pair.partition("=")
                    if not sep:
                        raise InvalidInputError(f"--sound {pair!r} must look like name=value")
                    try:
                        options[name.strip()] = json.loads(value)
                    except json.JSONDecodeError:
                        options[name.strip()] = value
                data["sound"] = options
            for required in ("type", "description"):
                if required not in data:
                    raise InvalidInputError(f"A request needs `{required}`")
            result = handle(s.lib, AssetRequest(**data), enqueue=async_, wait_seconds=wait_seconds)
            s.out.emit(result.model_dump(mode="json"), lambda c: render_request(c, result))

    @app.command("worker")
    def worker_command(
        ctx: typer.Context,
        concurrency: Annotated[int, typer.Option(help="Jobs run in parallel by this worker.")] = 2,
        drain: Annotated[bool, typer.Option("--drain", help="Exit when the queue is empty.")] = False,
        poll: Annotated[float, typer.Option(help="Seconds between queue checks when idle.")] = 1.0,
        max_jobs: Annotated[int | None, typer.Option(help="Exit after this many jobs.")] = None,
        json_: JsonOpt = False,
    ) -> None:
        """Process queued `request --async` jobs. Safe to run several workers at once."""
        with session(ctx, json_) as s:
            home = s.lib.paths.home
            if not json_:
                s.out.err.print(f"worker started on {home} (concurrency {concurrency}{', drain' if drain else ''})")
            stats = run_worker(home, concurrency=concurrency, drain=drain, poll=poll, max_jobs=max_jobs)
            s.out.emit({"worker": stats.as_dict()}, lambda c: c.print(
                f"done: {len(stats.succeeded)} succeeded, {len(stats.failed)} failed, {len(stats.requeued)} requeued"))

    @app.command("budget")
    def budget_command(
        ctx: typer.Context,
        daily: Annotated[float | None, typer.Option(help="Set the daily limit in USD.")] = None,
        monthly: Annotated[float | None, typer.Option(help="Set the monthly limit in USD.")] = None,
        per_request: Annotated[float | None, typer.Option(help="Set the per-call limit in USD.")] = None,
        json_: JsonOpt = False,
    ) -> None:
        """Show provider spend against limits (and optionally set limits)."""
        with session(ctx, json_) as s:
            for key, value in (("budget.daily_usd", daily), ("budget.monthly_usd", monthly),
                               ("budget.per_request_usd", per_request)):  # fmt: skip
                if value is not None:
                    settings_store.set_value(s.lib, key, value)
            data = budget_mod.summary(s.lib)
            data["providers_busy"] = limits.status(s.lib)

            def human(c: Console) -> None:
                for period in ("daily", "monthly"):
                    p = data[period]
                    limit = "no limit" if p["limit_usd"] is None else f"limit ${p['limit_usd']:.2f}, ${p['remaining_usd']:.2f} left"
                    c.print(f"{period:>8}: ${p['spent_usd']:.3f} spent ({limit})")
                if data["per_request_usd"] is not None:
                    c.print(f"per call: ${data['per_request_usd']:.2f} max")
                for provider, total in data["by_provider_monthly"].items():
                    c.print(f"  {provider}: ${total:.3f} this month")

            s.out.emit({"budget": data}, human)

    @app.command("errors")
    def errors_command(ctx: typer.Context, json_: JsonOpt = False) -> None:
        """List every error code, its exit code, meaning and what to do."""
        with session(ctx, json_) as s:
            items = [{"code": code, "exit_code": e, "meaning": m, "action": a} for code, (e, m, a) in CATALOG.items()]

            def human(c: Console) -> None:
                table = Table(header_style="bold")
                for column in ("Code", "Exit", "Meaning", "What to do"):
                    table.add_column(column)
                for i in items:
                    table.add_row(i["code"], str(i["exit_code"]), i["meaning"], i["action"])
                c.print(table)

            s.out.emit({"errors": items}, human)


@config_app.command("list")
def config_list(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Show every setting with its value and default."""
    with session(ctx, json_) as s:
        data = settings_store.all_settings(s.lib)

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Key", "Value", "Default", "Description"):
                table.add_column(column)
            for key, item in data.items():
                value = str(item["value"]) + (" *" if item["set"] else "")
                table.add_row(key, value, str(item["default"]), item["description"])
            c.print(table)

        s.out.emit({"settings": data}, human)


@config_app.command("get")
def config_get(ctx: typer.Context, key: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Read one setting."""
    with session(ctx, json_) as s:
        value = settings_store.get(s.lib, key)
        s.out.emit({"key": key, "value": value}, lambda c: c.print(str(value)))


@config_app.command("set")
def config_set(ctx: typer.Context, key: Annotated[str, typer.Argument()], value: Annotated[str, typer.Argument()],
               json_: JsonOpt = False) -> None:  # fmt: skip
    """Set a setting for every agent using this library (e.g. limits.openai.max_concurrent 2)."""
    with session(ctx, json_) as s:
        stored = settings_store.set_value(s.lib, key, value)
        s.out.emit({"key": key, "value": stored}, lambda c: c.print(f"{key} = {stored}"))


@config_app.command("unset")
def config_unset(ctx: typer.Context, key: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Return a setting to its default."""
    with session(ctx, json_) as s:
        settings_store.unset(s.lib, key)
        value = settings_store.get(s.lib, key)
        s.out.emit({"key": key, "value": value}, lambda c: c.print(f"{key} = {value} (default)"))
