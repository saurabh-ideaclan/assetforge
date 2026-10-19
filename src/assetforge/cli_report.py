"""`assetforge report` (usage & cost overview) and `assetforge pricing …` (model prices)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.cli_common import JsonOpt, session
from assetforge.core import pricing, reporting
from assetforge.core.report_html import render

pricing_app = typer.Typer(help="Model prices used for cost tracking (fetched from the internet).", no_args_is_help=True)


def _money(v: float | None) -> str:
    return "-" if v is None else (f"${v:,.4f}" if 0 < abs(v) < 1 else f"${v:,.2f}")


def render_report(c: Console, r: dict) -> None:
    t = r["totals"]
    rng = r["range"]
    c.print(f"[bold]Usage & cost[/bold]  {rng['label']}  [dim]{rng['from'] or 'beginning'} → {rng['to']} ({rng['tz']})[/dim]")
    err = "" if t["estimate_error_pct"] is None else f", estimates {t['estimate_error_pct']:+.1f}% vs actual"
    c.print(f"  {_money(t['cost_usd'])} for {t['images']} image(s) in {t['calls']} call(s); "
            f"{t['total_tokens']:,} tokens (in: {t['input_text_tokens']:,} text + {t['input_image_tokens']:,} image; "
            f"out: {t['output_image_tokens']:,} image + {t['output_text_tokens']:,} text){err}")  # fmt: skip
    for name, label in (("by_provider", "Provider"), ("by_model", "Model"), ("by_project", "Project")):
        rows = r[name]
        if not rows:
            continue
        table = Table(header_style="bold", title=label, title_justify="left")
        for column in (label, "Calls", "Images", "Tokens", "Cost", "Share", "$/image"):
            table.add_column(column, justify="left" if column == label else "right")
        for row in rows:
            table.add_row(escape(str(row["key"])), str(row["calls"]), str(row["images"]), f"{row['total_tokens']:,}",
                          _money(row["cost_usd"]), f"{row['share'] * 100:.1f}%", _money(row["avg_cost_per_image"]))  # fmt: skip
        c.print(table)
    active = [p for p in r["series"] if p["calls"]]
    if active:
        table = Table(header_style="bold", title=f"Per {rng['interval']}", title_justify="left")
        for column in ("Period", "Calls", "Images", "Tokens", "Cost"):
            table.add_column(column, justify="left" if column == "Period" else "right")
        for p in active:
            table.add_row(p["period"], str(p["calls"]), str(p["images"]), f"{p['total_tokens']:,}", _money(p["cost_usd"]))
        c.print(table)
    p = r["pricing"]
    c.print(f"[dim]prices: {('fetched ' + p['fetched_at']) if p['fetched_at'] else 'built-in defaults'} from "
            f"{p['source_url']}[/dim]")  # fmt: skip


def register(app: typer.Typer) -> None:
    app.add_typer(pricing_app, name="pricing")

    @app.command("report")
    def report_command(
        ctx: typer.Context,
        range_: Annotated[str | None, typer.Option("--range", help=f"Preset: {', '.join(reporting.PRESETS)} (default last-30-days).")] = None,
        start: Annotated[str | None, typer.Option("--from", help="Start date (YYYY-MM-DD, inclusive) or ISO datetime.")] = None,
        end: Annotated[str | None, typer.Option("--to", help="End date (YYYY-MM-DD, inclusive) or ISO datetime.")] = None,
        tz: Annotated[str, typer.Option(help="Timezone for days/weeks: UTC, local, or e.g. Asia/Kolkata.")] = "UTC",
        interval: Annotated[str, typer.Option(help="day, week or month buckets for the time series.")] = "day",
        provider: Annotated[str | None, typer.Option(help="Only this provider.")] = None,
        model: Annotated[str | None, typer.Option(help="Only this model.")] = None,
        project: Annotated[str | None, typer.Option(help="Only this project.")] = None,
        top: Annotated[int, typer.Option(help="How many of the most expensive calls to list.")] = 10,
        html: Annotated[Path | None, typer.Option("--html", help="Also write a standalone HTML report here.")] = None,
        csv: Annotated[Path | None, typer.Option("--csv", help="Also write the raw usage records as CSV here.")] = None,
        json_: JsonOpt = False,
    ) -> None:
        """Token usage and cost overview for OpenAI/Gemini over a preset or custom date range."""
        with session(ctx, json_) as s:
            rng = reporting.resolve_range(range_, start, end, tz_name=tz)
            data = reporting.build(s.lib, rng, interval=interval, provider=provider, model=model, project=project, top=top)
            outputs = {}
            if html:
                html.parent.mkdir(parents=True, exist_ok=True)
                html.write_text(render(data))
                outputs["html"] = str(html)
            if csv:
                csv.parent.mkdir(parents=True, exist_ok=True)
                csv.write_text(reporting.to_csv(reporting.records(s.lib, rng, provider=provider, model=model,
                                                                  project=project)))  # fmt: skip
                outputs["csv"] = str(csv)
            data["outputs"] = outputs

            def human(c: Console) -> None:
                render_report(c, data)
                for kind, path in outputs.items():
                    c.print(f"[dim]{kind}:[/dim] {path}")

            s.out.emit({"report": data}, human)


@pricing_app.command("show")
def pricing_show(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Current prices per model, with where each came from."""
    with session(ctx, json_) as s:
        data = pricing.table(s.lib)

        def human(c: Console) -> None:
            c.print(f"[dim]{data['units']}[/dim]")
            age = "" if data["age_days"] is None else f" ({data['age_days']} days ago)"
            c.print(f"[dim]source: {data['source_url']} · fetched: {data['fetched_at'] or 'never (built-in defaults)'}"
                    f"{age}[/dim]")  # fmt: skip
            table = Table(header_style="bold")
            for column in ("Model", "Text in", "Image in", "Image out", "Text out", "Per image", "Source"):
                table.add_column(column, justify="left" if column in ("Model", "Source") else "right")
            for key, info in data["models"].items():
                r = info["rates"]
                table.add_row(key, *(str(r.get(f, "-")) for f in ("input_text", "input_image", "output_image",
                                                                  "output_text", "per_image")), info["source"])  # fmt: skip
            c.print(table)

        s.out.emit({"pricing": data}, human)


@pricing_app.command("update")
def pricing_update(
    ctx: typer.Context,
    url: Annotated[str | None, typer.Option(help="Catalog URL (default: pricing.source_url).")] = None,
    json_: JsonOpt = False,
) -> None:
    """Fetch the latest prices from the internet now."""
    with session(ctx, json_) as s:
        result = pricing.update(s.lib, url)

        def human(c: Console) -> None:
            c.print(f"Fetched prices for {result['models']} image models from {result['source']}")
            for change in result["changes"]:
                c.print(f"  {change['model']} {change['field']}: {change['before']} → {change['after']}")
            if result["first"]:
                c.print("  first price snapshot stored")
            elif not result["changes"]:
                c.print("  no price changes since the last fetch")

        s.out.emit({"update": result}, human)


@pricing_app.command("set")
def pricing_set(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="e.g. gpt-image-2.5-flare or gemini-3.1-flash-image")],
    field: Annotated[str, typer.Argument(help=f"One of {', '.join(pricing.FIELDS)}")],
    value: Annotated[float, typer.Argument(help="USD per 1M tokens (per image for per_* fields)")],
    provider: Annotated[str | None, typer.Option(help="openai or gemini (default: from the model name).")] = None,
    json_: JsonOpt = False,
) -> None:
    """Pin a price (e.g. a negotiated rate); it overrides fetched prices."""
    with session(ctx, json_) as s:
        provider = provider or ("gemini" if model.startswith(("gemini", "models/")) else "openai")
        pricing.set_override(s.lib, provider, model, field, value)
        rates, source = pricing.rates_for(s.lib, provider, model)
        s.out.emit({"model": f"{provider}/{model}", "rates": rates, "source": source},
                   lambda c: c.print(f"{provider}/{model} {field} = {value} (override)"))  # fmt: skip


@pricing_app.command("unset")
def pricing_unset(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument()],
    field: Annotated[str | None, typer.Argument(help="Omit to clear all overrides for the model.")] = None,
    provider: Annotated[str | None, typer.Option()] = None,
    json_: JsonOpt = False,
) -> None:
    """Remove price overrides so fetched prices apply again."""
    with session(ctx, json_) as s:
        provider = provider or ("gemini" if model.startswith(("gemini", "models/")) else "openai")
        removed = pricing.clear_override(s.lib, provider, model, field)
        s.out.emit({"removed": removed}, lambda c: c.print(f"removed {removed} override(s)"))
