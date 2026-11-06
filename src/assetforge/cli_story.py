"""`assetforge story …` (branching visual-novel writing → assets → playable demo) and `assetforge llm …`
(the text providers: signed-in CLIs first, so no API key is needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.cli_common import JsonOpt, session
from assetforge.config import load_settings
from assetforge.errors import ExitCode, InvalidInputError, NotFoundError
from assetforge.story import steps
from assetforge.story.project import StoryProject, register

story_app = typer.Typer(help="Write branching visual-novel stories with your signed-in LLM (claude, codex, gemini), "
                             "make every asset they need, and export a playable demo.", no_args_is_help=True)  # fmt: skip
llm_app = typer.Typer(help="Text providers for story writing (signed-in CLIs; API keys only as opt-in).",
                      no_args_is_help=True)  # fmt: skip

Dir = Annotated[Path | None, typer.Option("--dir", "-d", help="Story folder (default: ./story.json here, or the only "
                                                              "story under ./story/).")]  # fmt: skip
Provider = Annotated[str | None, typer.Option(help="claude-cli | codex-cli | gemini-cli | openai-api | gemini-api | "
                                                   "fake | auto (default: the story's, then $ASSETFORGE_TEXT_PROVIDER).")]  # fmt: skip
Model = Annotated[str | None, typer.Option(help="Model for that provider (e.g. sonnet, opus for claude).")]
Fresh = Annotated[bool, typer.Option("--fresh", help="Ask again instead of reusing the stored answer to the same prompt.")]


def find_dir(directory: Path | None) -> StoryProject:
    if directory is not None:
        return StoryProject(directory)
    here = Path.cwd()
    if (here / "story.json").exists():
        return StoryProject(here)
    found = sorted(p.parent for p in (here / "story").glob("*/story.json"))
    if len(found) == 1:
        return StoryProject(found[0])
    raise InvalidInputError("Which story? Pass --dir", candidates=[str(p) for p in found])


def _ctx(s: Any, directory: Path | None, provider: str | None, model: str | None, fresh: bool = False) -> steps.Ctx:
    project = find_dir(directory)
    register(s.lib.paths.home, project)  # the Mac app lists every story the library has worked on
    return steps.Ctx(lib=s.lib, project=project, provider=provider, model=model, fresh=fresh)


def _calls(c: Console, ctx: steps.Ctx) -> None:
    for call in ctx.calls:
        how = "reused" if call["reused"] else f"{call['billing']}"
        c.print(f"[dim]{call['task']}: {call['provider']} {call['model']} ({how}, {call['job_id']})[/dim]")


def _issues(c: Console, issues: list[dict[str, Any]]) -> None:
    for i in issues:
        colour = "red" if i["level"] == "error" else "yellow"
        c.print(f"[{colour}]{i['level']}[/{colour}] {escape(i['where'])}: {escape(i['message'])}")


# ---------------------------------------------------------------------- writing steps


@story_app.command("init")
def init_(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Story title.")],
    premise: Annotated[str, typer.Option("--premise", "-p", help="What the story is about (one or two sentences).")],
    directory: Dir = None,
    genre: Annotated[str, typer.Option(help="mystery, romance, horror, sci-fi, slice-of-life…")] = "drama",
    audience: Annotated[str, typer.Option(help="Rating/age group, e.g. teen, all ages, mature.")] = "teen",
    language: Annotated[str, typer.Option(help="Language of the text.")] = "English",
    tone: Annotated[str, typer.Option(help="e.g. 'cozy with a bittersweet edge'.")] = "",
    behaviour: Annotated[str | None, typer.Option(help="Author style rules, or @file to read them from a file.")] = None,
    scenes: Annotated[int, typer.Option(help="Target number of scenes (2-60).")] = 8,
    endings: Annotated[int, typer.Option(help="Number of endings (1-6).")] = 2,
    style: Annotated[str | None, typer.Option(help="Art preset (`art presets`) or free style text.")] = None,
    project: Annotated[str | None, typer.Option(help="AssetForge project for the assets (default vn-<slug>).")] = None,
    provider: Provider = None,
    model: Model = None,
    force: Annotated[bool, typer.Option(help="Replace an existing story.json.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Start a story folder (story.json). Next: `story world`, or `story make` to run every step."""
    with session(ctx, json_) as s:
        if behaviour and behaviour.startswith("@"):
            behaviour = Path(behaviour[1:]).expanduser().read_text().strip()
        proj = steps.init(directory, title=title, premise=premise, force=force, genre=genre, audience=audience,
                          language=language, tone=tone, behaviour=behaviour, scenes=scenes, endings=endings,
                          art_style=style, project=project, provider=provider, model=model)  # fmt: skip
        config = proj.config
        register(s.lib.paths.home, proj)
        s.out.emit({"dir": str(proj.root), "config": config.model_dump(mode="json")},
                   lambda c: c.print(f"Story [bold]{escape(config.title)}[/bold] in {proj.root}\nNext: assetforge story make --dir {proj.root}  (or world → cast → outline → write)"))  # fmt: skip


def _step(name: str, doc: str):  # noqa: ANN202 - small command factory for world/cast/outline
    def command(ctx: typer.Context, directory: Dir = None, provider: Provider = None, model: Model = None,
                fresh: Fresh = False, json_: JsonOpt = False) -> None:  # fmt: skip
        with session(ctx, json_) as s:
            c = _ctx(s, directory, provider, model, fresh)
            payload: dict[str, Any] = {"dir": str(c.project.root)}
            if name == "outline":
                data, fixes = steps.outline(c)
                payload["fixes"] = fixes
            else:
                data = getattr(steps, name)(c)
            payload[name] = data.model_dump(mode="json")
            payload["calls"] = c.calls

            def human(con: Console) -> None:
                _calls(con, c)
                con.print(f"Saved {c.project.path(name)}")
                for fix in payload.get("fixes", []):
                    con.print(f"[yellow]fixed[/yellow] {escape(fix)}")

            s.out.emit(payload, human)

    command.__doc__ = doc
    story_app.command(name)(command)


_step("world", "Worldbuilding: setting, history, culture, rules, art and music direction (world.json).")
_step("cast", "Characters (look, voice, expressions) and locations (times of day, ambience) (cast.json).")
_step("outline", "Branching plan: scenes, choices, variables and endings (outline.json), fixed to be playable.")


@story_app.command("write")
def write_(
    ctx: typer.Context, directory: Dir = None, provider: Provider = None, model: Model = None, fresh: Fresh = False,
    scene: Annotated[list[str] | None, typer.Option("--scene", "-s", help="Only these scene ids (rewrites them).")] = None,
    rewrite: Annotated[bool, typer.Option(help="Rewrite scenes that are already written.")] = False,
    workers: Annotated[int, typer.Option(min=1, max=8, help="Scenes written at once (also capped per provider by "
                                                            "limits.<provider>.max_concurrent).")] = 2,  # fmt: skip
    json_: JsonOpt = False,
) -> None:
    """Write the scenes (resumable: written scenes are kept), then assemble and validate script.json."""
    with session(ctx, json_) as s:
        c = _ctx(s, directory, provider, model, fresh)
        result = steps.write(c, only=scene, rewrite=rewrite, workers=workers)
        result["calls"] = c.calls
        status = "ok" if result.get("assembled") and not result.get("errors") else "partial"

        def human(con: Console) -> None:
            _calls(con, c)
            con.print(f"Wrote {len(result['written'])} scene(s), kept {len(result['kept'])}")
            for sid, fixes in result["fixes"].items():
                con.print(f"[dim]{sid}: {escape('; '.join(fixes))}[/dim]")
            if result.get("missing_scenes"):
                con.print(f"[yellow]Missing scenes:[/yellow] {', '.join(result['missing_scenes'])}")
            _issues(con, result.get("issues", []))
            if result.get("assembled"):
                con.print(f"Script: {result['script']}  (read it in notes.md)")

        s.out.emit(result, human, status=status)
        if status == "partial":
            raise typer.Exit(int(ExitCode.PARTIAL))


@story_app.command("refine")
def refine_(
    ctx: typer.Context,
    scene: Annotated[str, typer.Option("--scene", "-s", help="Scene id to revise.")],
    note: Annotated[str, typer.Option("--note", "-n", help="What to change.")],
    directory: Dir = None, provider: Provider = None, model: Model = None, json_: JsonOpt = False,
) -> None:
    """Revise one scene with a note (the previous version is kept as <scene>.json.back)."""
    with session(ctx, json_) as s:
        c = _ctx(s, directory, provider, model)
        result = steps.refine(c, scene, note)
        result["calls"] = c.calls

        def human(con: Console) -> None:
            _calls(con, c)
            con.print(f"Revised {scene}; previous version: {result['backup']}")
            _issues(con, result.get("issues", []))

        s.out.emit(result, human)


@story_app.command("improve")
def improve_(
    ctx: typer.Context,
    feedback: Annotated[str, typer.Argument(help="What to change, in your own words (or @file).")] = "",
    directory: Dir = None, provider: Provider = None, model: Model = None,
    use_check: Annotated[bool, typer.Option("--use-check", help="Also fix the issues in the last `story check`.")] = False,
    scene: Annotated[list[str] | None, typer.Option("--scene", "-s", help="Only these scenes may change.")] = None,
    workers: Annotated[int, typer.Option(min=1, max=8)] = 2,
    json_: JsonOpt = False,
) -> None:
    """Improve the story from feedback: an editor pass turns it into per-scene notes and the scenes are rewritten
    (each keeps a .back; the round is logged in feedback.json)."""
    with session(ctx, json_) as s:
        if feedback.startswith("@"):
            feedback = Path(feedback[1:]).expanduser().read_text()
        c = _ctx(s, directory, provider, model)
        result = steps.improve(c, feedback, include_check=use_check, scenes=scene, workers=workers)
        result["calls"] = c.calls
        status = "ok" if not result["failed"] and not result.get("errors") else "partial"

        def human(con: Console) -> None:
            _calls(con, c)
            con.print(escape(result["summary"]))
            for e in result["edits"]:
                mark = "[green]✓[/green]" if e["scene"] in result["revised"] else "[red]✗[/red]"
                con.print(f"{mark} {e['scene']}: [dim]{escape(e['note'])}[/dim]")
            _issues(con, result.get("issues", []))

        s.out.emit(result, human, status=status)
        if status == "partial":
            raise typer.Exit(int(ExitCode.PARTIAL))


@llm_app.command("models")
def models_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Every model you can write with: per signed-in provider, the models your subscription offers."""
    from assetforge.llm.catalog import describe

    with session(ctx, json_) as s:
        info = describe(s.lib, load_settings(s.lib.paths.home))

        def human(c: Console) -> None:
            for p in info["providers"]:
                state = ("[green]signed in[/green]" if p["signed_in"] else "[red]not signed in[/red]" if p["signed_in"] is False
                         else "[dim]not installed[/dim]" if not p["installed"] else "[yellow]unknown[/yellow]")  # fmt: skip
                c.print(f"[bold]{p['name']}[/bold] {state} {escape(p['account'] or '')}  [dim]({p['billing']})[/dim]")
                if p.get("note"):
                    c.print(f"  [yellow]{escape(p['note'])}[/yellow]")
                if p["installed"]:
                    for m in p["models"]:
                        tag = " [green]✓ worked[/green]" if m["worked"] else ""
                        c.print(f"  {m['id'] or '(default)':<16} {escape(m['label'])}{tag} [dim]{escape(m['description'])}[/dim]")
            c.print(f"auto: {' → '.join(info['auto']) or '(none signed in)'}")

        s.out.emit(info, human)


@story_app.command("check")
def check_(ctx: typer.Context, directory: Dir = None, provider: Provider = None, model: Model = None,
           fresh: Fresh = False, json_: JsonOpt = False) -> None:  # fmt: skip
    """Continuity pass by the model: contradictions, out-of-character lines, dropped threads (check.json)."""
    with session(ctx, json_) as s:
        c = _ctx(s, directory, provider, model, fresh)
        report = steps.check(c)

        def human(con: Console) -> None:
            _calls(con, c)
            for i in report.issues:
                con.print(f"[{'red' if i.severity == 'high' else 'yellow'}]{i.severity}[/] {i.scene}: {escape(i.problem)}"
                          + (f" [dim]→ {escape(i.suggestion)}[/dim]" if i.suggestion else ""))  # fmt: skip
            con.print(escape(report.summary))

        s.out.emit({"report": report.model_dump(mode="json"), "calls": c.calls}, human)


@story_app.command("validate")
def validate_(ctx: typer.Context, directory: Dir = None, json_: JsonOpt = False) -> None:
    """Check links, reachability, endings, cast and variables of script.json (no model call)."""
    with session(ctx, json_) as s:
        proj = find_dir(directory)
        issues = [i.model_dump() for i in steps.validate(proj)]
        errors = sum(i["level"] == "error" for i in issues)
        s.out.emit({"issues": issues, "errors": errors, "warnings": len(issues) - errors},
                   lambda c: (_issues(c, issues), c.print("[green]valid[/green]" if not errors else f"{errors} error(s)")),
                   status="ok" if not errors else "error")  # fmt: skip
        if errors:
            raise typer.Exit(int(ExitCode.INVALID_INPUT))


# ---------------------------------------------------------------------- assets, export, status


@story_app.command("assets")
def assets_(
    ctx: typer.Context, directory: Dir = None,
    only: Annotated[list[str] | None, typer.Option("--only", help="chars, bg, cg, ui, audio, music, ambience, sfx "
                                                                  "(repeatable).")] = None,  # fmt: skip
    limit: Annotated[int | None, typer.Option(help="Make at most N new assets this run.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be made and the estimated cost.")] = False,
    image_provider: Annotated[str, typer.Option(help="Image provider: auto, openai, gemini (or fake).")] = "auto",
    quality: Annotated[str, typer.Option(help="auto, low, medium, high, max.")] = "auto",
    redo: Annotated[bool, typer.Option("--redo", help="Remake the selected groups (old versions are archived, not "
                                                      "deleted).")] = False,
    replan: Annotated[bool, typer.Option("--replan", help="Ask the writer for a new sound plan (sound.json) first.")] = False,  # fmt: skip
    json_: JsonOpt = False,
) -> None:
    """Make every asset the script needs (sprites + expressions, backgrounds per time of day, CGs, UI, music,
    ambience, SFX) through `request`. Re-runs only make what is missing. Images need an image API key."""
    from assetforge.story.assets import produce

    with session(ctx, json_) as s:
        proj = find_dir(directory)
        result = produce(s.lib, proj, only=only, limit=limit, dry_run=dry_run, image_provider=image_provider,
                         quality=quality, redo=redo, replan=replan)  # fmt: skip
        failed = result["counts"].get("failed", 0) + result["counts"].get("blocked", 0)
        status = "ok" if not failed else "partial" if failed < result["total"] else "error"

        def human(con: Console) -> None:
            table = Table(header_style="bold")
            for col in ("Key", "State", "Asset", "Note"):
                table.add_column(col, overflow="fold")
            for r in result["items"]:
                note = (r.get("error") or {}).get("message") or r.get("reason") or (
                    f"${r['estimated_cost_usd']:.3f} via {r.get('provider')}" if r.get("estimated_cost_usd") is not None and dry_run else "")
                colour = {"failed": "red", "blocked": "yellow", "skipped": "dim"}.get(r["state"], "")
                table.add_row(r["key"], f"[{colour}]{r['state']}[/{colour}]" if colour else r["state"], r.get("asset_id") or "",
                              escape(str(note)))  # fmt: skip
            con.print(table)
            con.print(", ".join(f"{k}: {v}" for k, v in result["counts"].items()))
            if dry_run:
                con.print(f"Estimated image cost: ${result['estimated_cost_usd']:.2f}")

        s.out.emit(result, human, status=status)
        if status != "ok":
            raise typer.Exit(int(ExitCode.PARTIAL if status == "partial" else ExitCode.ERROR))


@story_app.command("export")
def export_(
    ctx: typer.Context, directory: Dir = None,
    out: Annotated[Path | None, typer.Option(help="Output folder (default <story>/build).")] = None,
    approved_only: Annotated[bool, typer.Option("--approved-only", help="Leave out unreviewed candidates.")] = False,
    overwrite: Annotated[bool, typer.Option(help="Replace files AssetForge did not write.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_: JsonOpt = False,
) -> None:
    """Write a playable demo: index.html + story.js/json + the assets (missing ones play as placeholders)."""
    from assetforge.exporters.vn import export_story

    with session(ctx, json_) as s:
        proj = find_dir(directory)
        result = export_story(s.lib, proj, out or proj.root / "build", include_candidates=not approved_only,
                              overwrite=overwrite, dry_run=dry_run)  # fmt: skip

        def human(con: Console) -> None:
            con.print(f"Player: {result['player']}  ({result['assets']} assets, {len(result['missing'])} placeholders)")
            con.print(f"[dim]Open it directly, or serve it: {escape(result['serve'])}[/dim]")

        s.out.emit(result, human)


@story_app.command("status")
def status_(ctx: typer.Context, directory: Dir = None, json_: JsonOpt = False) -> None:
    """Which steps are done, validation state and asset coverage."""
    from assetforge.story.assets import coverage

    with session(ctx, json_) as s:
        proj = find_dir(directory)
        payload: dict[str, Any] = proj.status()
        if proj.has("script"):
            issues = steps.validate(proj)
            payload["errors"] = sum(i.level == "error" for i in issues)
            payload["warnings"] = len(issues) - payload["errors"]
            payload["assets"] = coverage(s.lib, proj)

        def human(con: Console) -> None:
            con.print(f"[bold]{escape(proj.config.title)}[/bold] {proj.root}")
            con.print("  ".join(f"{k}: {'[green]✓[/green]' if v else '[dim]–[/dim]'}" for k, v in payload["steps"].items()))
            con.print(f"Scenes written: {payload['scenes_written']}/{payload['scenes_planned']}")
            if "assets" in payload:
                con.print(f"Validation: {payload['errors']} error(s), {payload['warnings']} warning(s)")
                con.print(f"Assets: {payload['assets']['have']}/{payload['assets']['total']}")

        s.out.emit(payload, human)


@story_app.command("make")
def make(
    ctx: typer.Context,
    premise: Annotated[str, typer.Argument(help="What the story is about.")],
    title: Annotated[str | None, typer.Option(help="Title (default: derived from the premise).")] = None,
    directory: Dir = None,
    genre: Annotated[str, typer.Option()] = "drama",
    audience: Annotated[str, typer.Option()] = "teen",
    tone: Annotated[str, typer.Option()] = "",
    scenes: Annotated[int, typer.Option()] = 8,
    endings: Annotated[int, typer.Option()] = 2,
    style: Annotated[str | None, typer.Option(help="Art preset or style text.")] = None,
    provider: Provider = None, model: Model = None,
    assets: Annotated[bool, typer.Option("--assets", help="Also make the assets (images cost money: try "
                                                          "`story assets --dry-run` first).")] = False,  # fmt: skip
    image_provider: Annotated[str, typer.Option()] = "auto",
    out: Annotated[Path | None, typer.Option(help="Export folder (default <story>/build).")] = None,
    json_: JsonOpt = False,
) -> None:
    """One shot: init (if needed) → world → cast → outline → write → export a playable demo (placeholders until
    `story assets` has run). Every step reuses stored answers, so re-running continues where it stopped."""
    from assetforge.exporters.vn import export_story

    with session(ctx, json_) as s:
        name = title or " ".join(premise.split()[:6]).rstrip(".,;:")
        proj = StoryProject(directory or steps.default_dir(name))
        if not proj.has("config"):
            steps.init(proj.root, title=name, premise=premise, genre=genre, audience=audience, tone=tone, scenes=scenes,
                       endings=endings, art_style=style, provider=provider, model=model)  # fmt: skip
        register(s.lib.paths.home, proj)
        c = steps.Ctx(lib=s.lib, project=proj, provider=provider, model=model)
        done: dict[str, Any] = {"dir": str(proj.root)}
        if not proj.has("world"):
            steps.world(c)
        if not proj.has("cast"):
            steps.cast(c)
        if not proj.has("outline"):
            done["outline_fixes"] = steps.outline(c)[1]
        written = steps.write(c)
        done["write"] = {k: written[k] for k in ("written", "kept", "assembled", "errors") if k in written}
        done["issues"] = written.get("issues", [])
        if assets:
            from assetforge.story.assets import produce

            produced = produce(s.lib, proj, image_provider=image_provider)
            done["assets"] = {"counts": produced["counts"], "total": produced["total"]}
        if written.get("assembled"):
            done["export"] = export_story(s.lib, proj, out or proj.root / "build")
        done["calls"] = c.calls
        status = "ok" if written.get("assembled") and not written.get("errors") else "partial"

        def human(con: Console) -> None:
            _calls(con, c)
            con.print(f"Story: {proj.root} — {len(written['written'])} scene(s) written, {len(written['kept'])} kept")
            _issues(con, done["issues"])
            if "assets" in done:
                con.print("Assets: " + ", ".join(f"{k}: {v}" for k, v in done["assets"]["counts"].items()))
            if "export" in done:
                con.print(f"Play: {done['export']['player']}  ({len(done['export']['missing'])} placeholders)")

        s.out.emit(done, human, status=status)
        if status == "partial":
            raise typer.Exit(int(ExitCode.PARTIAL))


# ---------------------------------------------------------------------- llm


@llm_app.command("providers")
def providers(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Text providers, whether each is usable, and which one `auto` picks first."""
    from assetforge import llm

    with session(ctx, json_) as s:
        settings = load_settings(s.lib.paths.home)
        items = [p.describe() for p in llm.all_providers(settings)]
        chain = [p.name for p in llm.provider_chain(None, settings)]

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for col in ("Provider", "Usable", "Billing", "Model", "Note"):
                table.add_column(col, overflow="fold")
            for p in items:
                table.add_row(p["name"], "[green]yes[/green]" if p["available"] else "[dim]no[/dim]", p["billing"], p["model"],
                              escape(p["reason"] or p["login_hint"] or ""))  # fmt: skip
            c.print(table)
            c.print(f"auto order: {' → '.join(chain) or '(none available)'}")
            c.print("[dim]Installed is not the same as signed in: `assetforge llm test --provider NAME` checks with one tiny call.[/dim]")

        s.out.emit({"providers": items, "auto": chain}, human)


@llm_app.command("test")
def test(ctx: typer.Context, provider: Annotated[str, typer.Option(help="Provider to test.")] = "auto",
         model: Model = None, json_: JsonOpt = False) -> None:  # fmt: skip
    """One tiny structured call (never cached) to check sign-in, model and JSON output."""
    from pydantic import BaseModel

    from assetforge.llm.base import TextCall
    from assetforge.llm.structured import generate

    class Ping(BaseModel):
        reply: str
        number: int

    with session(ctx, json_) as s:
        call = TextCall(prompt="Reply with reply='pong' and number=7.", task="llm.test", model=model,
                        offline=lambda _: {"reply": "pong", "number": 7}, timeout=180)  # fmt: skip
        result = generate(s.lib, call, Ping, provider=None if provider == "auto" else provider, fallback=False, fresh=True)
        ok = result.data.number == 7
        payload = {"provider": result.provider, "model": result.model, "billing": result.billing, "answer": result.data.model_dump(),
                   "usage": result.usage, "job_id": result.job_id, "ok": ok}  # fmt: skip
        s.out.emit(payload, lambda c: c.print(f"[green]ok[/green] {result.provider} ({result.model}, {result.billing}): "
                                               f"{result.data.model_dump()}"))  # fmt: skip
        if not ok:
            raise NotFoundError("The provider answered but not as asked")
