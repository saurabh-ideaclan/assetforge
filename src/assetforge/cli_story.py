"""`assetforge story …` (branching visual-novel writing → assets → playable demo) and `assetforge llm …`
(the text providers: signed-in CLIs first, so no API key is needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.cli_common import JsonOpt, parse_pairs, session
from assetforge.config import load_settings
from assetforge.errors import ConflictError, ExitCode, InvalidInputError, NotFoundError
from assetforge.story import direction as creative
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
    scenes: Annotated[int, typer.Option(help="Target number of chapters (2-60).")] = 8,
    endings: Annotated[int, typer.Option(help="Number of endings (1-6).")] = 2,
    chapter_words: Annotated[int, typer.Option(help="Words per chapter (benchmark ~1,500).")] = 1500,
    chapter_choices: Annotated[int, typer.Option(help="Choices per chapter (benchmark 6-8).")] = 6,
    style: Annotated[str | None, typer.Option(help="Art preset (`art presets`) or free style text.")] = None,
    project: Annotated[str | None, typer.Option(help="AssetForge project for the assets (default vn-<slug>).")] = None,
    preset: Annotated[str | None, typer.Option(help="Creative-direction preset (`story presets`).")] = None,
    direction: Annotated[str | None, typer.Option(help="Creative direction as JSON, or @file.json/@file.yaml; "
                                                       "fields override the preset.")] = None,  # fmt: skip
    provider: Provider = None,
    model: Model = None,
    force: Annotated[bool, typer.Option(help="Replace an existing story.json.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Start a story folder (story.json). Next: `story world`, or `story make` to run every step."""
    with session(ctx, json_) as s:
        if behaviour and behaviour.startswith("@"):
            behaviour = Path(behaviour[1:]).expanduser().read_text().strip()
        resolved = _direction(s.lib.paths.home, preset, direction)
        proj = steps.init(directory, title=title, premise=premise, force=force, genre=genre, audience=audience,
                          language=language, tone=tone, behaviour=behaviour, scenes=scenes, endings=endings,
                          chapter_words=chapter_words, chapter_choices=chapter_choices, art_style=style, project=project, provider=provider, model=model,
                          direction=resolved)  # fmt: skip
        config = proj.config
        register(s.lib.paths.home, proj)
        warnings = creative.warnings(resolved) if resolved else []

        def human(c: Console) -> None:
            c.print(f"Story [bold]{escape(config.title)}[/bold] in {proj.root}")
            if resolved:
                c.print(f"Direction: {escape(creative.summary(resolved))}")
            for w in warnings:
                c.print(f"[yellow]warning[/yellow] {escape(w['message'])}")
            c.print(f"Next: assetforge story make --dir {proj.root}  (or world → names → cast → outline → write)")

        s.out.emit({"dir": str(proj.root), "config": config.model_dump(mode="json"), "warnings": warnings}, human)


def _direction(home: Path, preset: str | None, text: str | None) -> creative.CreativeDirection | None:
    """`--preset` and/or `--direction` (JSON, or @file in JSON/YAML) → a resolved direction; neither → None."""
    if not preset and not text:
        return None
    data: dict[str, Any] = {}
    if text:
        raw = Path(text[1:]).expanduser().read_text() if text.startswith("@") else text
        import yaml

        loaded = yaml.safe_load(raw) or {}
        if not isinstance(loaded, dict):
            raise InvalidInputError("--direction must be a JSON/YAML mapping")
        data = loaded
    return creative.resolve(data, preset=preset or data.pop("preset", None), home=home)


@story_app.command("presets")
def presets_(
    ctx: typer.Context,
    save: Annotated[str | None, typer.Option(help="Save a story's direction as a user preset with this name.")] = None,
    directory: Dir = None,
    delete: Annotated[str | None, typer.Option(help="Delete a user preset.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Creative-direction presets: built-in ones, and yours in $ASSETFORGE_HOME/story_presets/."""
    with session(ctx, json_) as s:
        home = s.lib.paths.home
        if delete:
            creative.delete_preset(home, delete)
        if save:
            project = StoryProject(directory or Path.cwd())
            if project.config.direction is None:
                raise InvalidInputError("This story has no creative direction to save")
            path = creative.save_preset(home, save, project.config.direction, title=save)
            s.out.warn(f"Saved {path}")
        items = [p.model_dump(mode="json") for p in creative.presets(home)]

        def human(c: Console) -> None:
            for p in items:
                c.print(f"[bold]{p['name']}[/bold] ({p['source']}): {escape(p['summary'] or p['title'])}")

        s.out.emit({"presets": items}, human)


@story_app.command("direction")
def direction_(
    ctx: typer.Context,
    directory: Dir = None,
    preset: Annotated[str | None, typer.Option(help="Apply this preset to the story (keeps explicit fields).")] = None,
    set_: Annotated[list[str] | None, typer.Option("--set", help="field=value (lists comma-separated), e.g. romance=medium")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Apply even when written parts would be left stale.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Show or change a story's creative direction, with an impact review before anything written is affected."""
    with session(ctx, json_) as s:
        project = StoryProject(directory or Path.cwd())
        config = project.config
        current = config.direction
        payload: dict[str, Any] = {"dir": str(project.root)}
        if preset or set_:
            data = dict(parse_pairs(set_ or [], "--set"))
            for key in ("subgenres", "themes", "tone", "reserved_names"):
                if key in data:
                    data[key] = [v.strip() for v in str(data[key]).split(",") if v.strip()]
            base = current.model_dump(exclude_defaults=True) if current else {}
            new = creative.resolve({**base, **data}, preset=preset or (current.preset if current else None), home=s.lib.paths.home)
            written = {"world": project.has("world"), "cast": project.has("cast"), "outline": project.has("outline"),
                       "chapters": bool(project.scenes()), "assets": project.has("script")}  # fmt: skip
            impact = creative.impact(current, new, written)
            payload["impact"] = impact
            if impact and not yes:
                raise ConflictError("Changing the direction would leave written parts stale: " + " ".join(impact),
                                    hint="Re-run with --yes to apply anyway")  # fmt: skip
            project.save("config", config.model_copy(update={"direction": new}))
            current = new
        payload["direction"] = current.model_dump(mode="json") if current else None
        payload["summary"] = creative.summary(current) if current else ""
        payload["warnings"] = creative.warnings(current) if current else []

        def human(c: Console) -> None:
            if not current:
                c.print("No creative direction (pre-direction story). Set one with --preset or --set.")
                return
            c.print(escape(payload["summary"]))
            for w in payload["warnings"]:
                c.print(f"[yellow]warning[/yellow] {escape(w['message'])}")
            for note in payload.get("impact", []):
                c.print(f"[yellow]impact[/yellow] {escape(note)}")

        s.out.emit(payload, human)


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
_step("names", "Naming guide for people and places, from the creative direction (names.json); the cast follows it.")
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
    scenes: Annotated[int, typer.Option(help="Chapters.")] = 8,
    endings: Annotated[int, typer.Option()] = 2,
    chapter_words: Annotated[int, typer.Option()] = 1500,
    chapter_choices: Annotated[int, typer.Option()] = 6,
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
                       endings=endings, chapter_words=chapter_words, chapter_choices=chapter_choices, art_style=style, provider=provider, model=model)  # fmt: skip
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


@story_app.command("lint")
def lint_(ctx: typer.Context, directory: Dir = None,
          fix: Annotated[bool, typer.Option("--fix", help="Rewrite failing chapters from the findings (kept only "
                                                          "when better).")] = False,
          scene: Annotated[list[str] | None, typer.Option("--scene", "-s", help="With --fix: only these.")] = None,
          rounds: Annotated[int, typer.Option(min=1, max=4, help="With --fix: rewrites per chapter.")] = 2,
          provider: Provider = None, model: Model = None,
          json_: JsonOpt = False) -> None:  # fmt: skip
    """Measure every chapter against the benchmark: words, choices, first choice, longest stretch without a choice,
    callbacks. Errors are the hard rules a freshly written chapter is repaired for. --fix rewrites the ones failing."""
    from assetforge.story.lint import lint_story
    from assetforge.story.usage import current_script

    with session(ctx, json_) as s:
        proj = find_dir(directory)
        if fix:
            steps.fix_lint(steps.Ctx(s.lib, proj, provider=provider, model=model), scenes=scene, rounds=rounds)
        script = current_script(proj)
        if script is None:
            raise NotFoundError("Nothing written yet", hint="Run `assetforge story outline` and `story write` first")
        report = lint_story(script, proj.config)

        def human(con: Console) -> None:
            sm = report["summary"]
            con.print(f"{sm['passing']}/{sm['chapters']} chapters pass · avg {sm['avg_words']} words, "
                      f"{sm['avg_choices']} choices · longest stretch {sm['worst_stretch']} words · {sm['minutes']} min")
            for c in report["chapters"]:
                mark = "[green]✓[/green]" if c["ok"] else "[red]✗[/red]"
                con.print(f" {mark} {c['chapter']:>2} {escape(c['title'])[:34]:<34} {c['words']:>5}w {c['choices']:>2}ch "
                          f"first@{c['first_choice_box'] or '-':<3} stretch {c['max_stretch']['words']:>3} cb {c['callbacks']}")
                for i in c["issues"]:
                    con.print(f"     [{'red' if i['level'] == 'error' else 'yellow'}]{i['code']}[/]: {escape(i['message'])}")

        s.out.emit({"lint": report}, human)


# ---------------------------------------------------------------------- content contract v2 releases + R2 publishing

StoryRef = Annotated[str | None, typer.Argument(help="Story id (as the app lists it) or folder; default: --dir or ./")]


def _story_ref(s: Any, story: str | None, directory: Path | None) -> StoryProject:
    if story:
        from assetforge.story.project import registered

        known = registered(s.lib.paths.home)
        if story in known:
            return StoryProject(known[story])
        if (Path(story) / "story.json").exists():
            return StoryProject(Path(story))
        raise NotFoundError(f"No story {story!r}", hint="Pass a registered story id or a story folder", known=sorted(known))
    return find_dir(directory)


@story_app.command("contract")
def contract_(
    ctx: typer.Context, story: StoryRef = None, directory: Dir = None,
    allow_missing: Annotated[bool, typer.Option("--allow-missing", help="Write a draft (never publishable) even when "
                                                                        "assets are missing or chapters unwritten.")] = False,  # fmt: skip
    out: Annotated[Path | None, typer.Option("--out", help="Releases folder (default <story>/releases).")] = None,
    no_derive: Annotated[bool, typer.Option("--no-derive", help="Never add web-format variants to the library; ship "
                                                                "the files that exist.")] = False,  # fmt: skip
    json_: JsonOpt = False,
) -> None:
    """Compile the story into a content-contract v2.0 release (graph, presentation, assets, locales, download plan,
    blobs), validate it and report. Unchanged content mints no new release. Never uploads anything."""
    from assetforge.story import contract

    with session(ctx, json_) as s:
        proj = _story_ref(s, story, directory)
        report = contract.compile_release(s.lib, proj, out, allow_missing=allow_missing, derive=not no_derive)
        payload = report.model_dump(mode="json")
        payload["release_status"] = payload.pop("status")  # `status` is the JSON contract's ok|partial|error
        status = {"created": "ok", "identical": "ok", "draft": "partial", "blocked": "error"}[report.status]

        def human(con: Console) -> None:
            colour = {"ok": "green", "partial": "yellow", "error": "red"}[status]
            con.print(f"[{colour}]{report.status}[/{colour}] {escape(report.message)}")
            if report.graph_id:
                con.print(f"Graph: {report.graph_id}  ·  valid: {report.valid}  ·  {report.path or ''}")
            con.print(", ".join(f"{k}: {v}" for k, v in report.counts.items()))
            if report.missing_assets:
                con.print(f"[yellow]Missing assets ({len(report.missing_assets)}):[/yellow] " + ", ".join(report.missing_assets[:12])
                          + (" …" if len(report.missing_assets) > 12 else ""))  # fmt: skip
            _issues(con, [f.model_dump() for f in report.findings if f.code != "missing_asset"][:30])

        s.out.emit(payload, human, status=status)
        if status == "error":
            raise typer.Exit(int(ExitCode.INVALID_INPUT))


@story_app.command("releases")
def releases_(ctx: typer.Context, story: StoryRef = None, directory: Dir = None,
              out: Annotated[Path | None, typer.Option("--out", help="Releases folder (default <story>/releases).")] = None,
              json_: JsonOpt = False) -> None:  # fmt: skip
    """List the story's compiled releases: graph ids, validation and published state."""
    from assetforge.story import contract

    with session(ctx, json_) as s:
        view = contract.releases_view(_story_ref(s, story, directory), out)

        def human(con: Console) -> None:
            table = Table(header_style="bold")
            for col in ("Release", "Graph", "Valid", "Published", "Created"):
                table.add_column(col)
            for r in view["releases"]:
                pub = f"seq {r['published']['sequence']} · {r['published']['at']}" if r.get("published") else "-"
                table.add_row(r["releaseId"], r["graphId"], "yes" if r["valid"] else "[red]no[/red]", pub, r["created"])
            con.print(table if view["releases"] else "No releases yet (`assetforge story contract`).")
            if view["draft"]:
                con.print(f"[yellow]Draft[/yellow] ({view['draft']['errors']} blocking finding(s)) at {view['root']}/draft")

        s.out.emit(view, human)


@story_app.command("publish")
def publish_(ctx: typer.Context, story: StoryRef = None, directory: Dir = None,
             release: Annotated[str | None, typer.Option("--release", help="Release id (default: the latest). An older "
                                                                           "one publishes a rollback.")] = None,  # fmt: skip
             out: Annotated[Path | None, typer.Option("--out", help="Releases folder (default <story>/releases).")] = None,
             yes: Annotated[bool, typer.Option("--yes", help="Really upload. Without it only the plan is shown.")] = False,
             json_: JsonOpt = False) -> None:  # fmt: skip
    """Alias of `backend publish`: upload a validated release (and its delta) to R2 and publish the signed staging
    index last. Without --yes: the plan only (what would be uploaded or skipped); nothing is written."""
    from assetforge.cli_backend import publish_command

    with session(ctx, json_) as s:
        publish_command(s, story, directory, release, out, yes)


@story_app.command("art-check")
def art_check_(
    ctx: typer.Context, story: StoryRef = None, directory: Dir = None,
    groups: Annotated[list[str] | None, typer.Option("--groups", help="Image groups to judge: bg, bg_time, cg, ui "
                                                                      "(default), char, expr (repeatable).")] = None,  # fmt: skip
    fix: Annotated[bool, typer.Option("--fix", help="Remake every failed image with its faults as feedback (PAID: one "
                                                    "image generation per round) and check again.")] = False,  # fmt: skip
    limit: Annotated[int | None, typer.Option(help="Judge (or fix) at most N images this run.")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Judge again images that already have a verdict.")] = False,
    max_rounds: Annotated[int, typer.Option(help="With --fix: remakes per image before keeping the best.")] = 2,
    image_provider: Annotated[str, typer.Option(help="With --fix: auto, openai, gemini (or fake).")] = "auto",
    provider: Provider = None, model: Model = None, json_: JsonOpt = False,
) -> None:
    """Visual logic check: a vision-capable signed-in CLI (codex or claude) looks at each made story image and judges
    it against its brief and the visual canon (visual_canon.json). Verdicts go to <story>/visual_check.json."""
    from assetforge.story import visual_check

    with session(ctx, json_) as s:
        proj = _story_ref(s, story, directory)
        register(s.lib.paths.home, proj)
        c = steps.Ctx(lib=s.lib, project=proj, provider=provider, model=model)
        chosen = tuple(groups or visual_check.DEFAULT_GROUPS)
        result = visual_check.review(c, groups=chosen, only_unchecked=not all_, limit=limit)
        fixes: list[dict[str, Any]] = []
        if fix:
            in_scope = {r["key"] for r in result["items"]}
            for key in [k for k in result["failed"] if k in in_scope][:limit]:
                fixes.append(visual_check.fix(c, key, max_rounds=max_rounds, image_provider=image_provider))
            result = {**result, **visual_check.summary(proj), "fixes": fixes}
        verdicts = visual_check.load(proj)["verdicts"]

        def human(con: Console) -> None:
            _calls(con, c)
            table = Table(header_style="bold")
            for col in ("Key", "State", "Verdict", "First fault"):
                table.add_column(col, overflow="fold")
            for r in result["items"]:
                v = verdicts.get(r["key"]) or {}
                mark = "" if r["state"] == "not_made" else ("[green]ok[/green]" if v.get("ok") else f"[red]{v.get('matches_brief', '?')}[/red]" if v else "")
                fault = r.get("fault") or (visual_check.Verdict.model_validate(v).first_fault() if v else (r.get("error") or {}).get("message", ""))
                table.add_row(r["key"], r["state"], mark, escape(str(fault or "")))
            con.print(table)
            con.print(f"{result['ok']} of {result['total']} judged images pass; {len(result['failed'])} fail.")
            for f in fixes:
                con.print(f"fix {f['key']}: {f['state']} after {f['rounds']} round(s) → {f['asset_id']}")

        failing = bool(result["failed"])
        s.out.emit(result, human, status="partial" if failing else "ok")
        if failing:
            raise typer.Exit(int(ExitCode.PARTIAL))
