"""`audio inspect | diagnose | correct | profile | models`: the Audio Analysis Engine from the command line.

    assetforge audio inspect FILE_OR_REF [--kind music] [--visuals DIR] [--json]
    assetforge audio diagnose FILE_OR_REF --kind ambience --brief "…" [--loop/--no-loop] [--json]
    assetforge audio correct REF [--out FILE] [--prefer precise|regenerate] [--json]
    assetforge audio profile PROJECT --kind music [REF…] [--json]      # from approved assets by default
    assetforge audio models status | install silero-vad --yes [--from FILE]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

from assetforge.cli_audio import audio_app
from assetforge.cli_common import JsonOpt, session
from assetforge.errors import InvalidInputError, NotFoundError

models_app = typer.Typer(help="Optional analysis models (speech detection, sound-event tagging).", no_args_is_help=True)
audio_app.add_typer(models_app, name="models")


def _resolve(s, target: str) -> tuple[Path, str | None, Any]:
    """A file path, or an asset / variant / key → (path, kind, asset)."""
    candidate = Path(target).expanduser()
    if candidate.is_file():
        return candidate, None, None
    asset = s.lib.get_asset(target)
    variant = next((v for v in asset.variants if v.id == target), asset.primary_variant)
    if variant is None or variant.file.kind != "audio":
        raise InvalidInputError(f"{target} is not an audio asset")
    from assetforge.audio.masters import ensure_file

    kind = asset.type.value if asset.type.value in ("music", "ambience", "sfx") else None
    return ensure_file(s.lib, variant), kind, asset


def _loop_of(asset) -> bool | None:
    if asset is None:
        return None
    meta = asset.metadata or {}
    audio = meta.get("audio") or {}
    if "loop" in audio:
        return bool(audio["loop"])
    spec = (meta.get("generator") or {}).get("spec") or {}
    comp = spec.get("compose") or {}
    if comp.get("form"):
        return comp["form"] == "loop"
    return True if asset.type.value == "ambience" else None


def _print_report(c: Console, r: dict[str, Any], path: Path) -> None:
    c.print(f"[bold]{escape(str(path))}[/bold]")
    for section, lines in r["text"].split("\n\n") and [(b.split("\n")[0], b.split("\n")[1:]) for b in r["text"].split("\n\n")]:
        c.print(f"[cyan]{section}[/cyan]")
        for line in lines:
            colour = "red" if line.startswith("- Unusable") else "yellow" if line.startswith("- Problem") else "dim" if line.startswith("- Note") else ""
            c.print(f"  [{colour}]{escape(line)}[/{colour}]" if colour else f"  {escape(line)}")


@audio_app.command("inspect")
def inspect_cmd(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Audio file path, asset ID, variant ID or key.")],
    kind: Annotated[str | None, typer.Option(help="music | ambience | sfx (an asset's type by default).")] = None,
    visuals: Annotated[Path | None, typer.Option(help="Write waveform/spectrogram/mel/chroma/similarity PNGs and timelines.json here.")] = None,
    no_vad: Annotated[bool, typer.Option("--no-vad", help="Skip the Silero VAD even when installed.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Measure a sound at every level (technical, spectral, musical, speech, events) and read it back in words."""
    from assetforge.audio.analysis import analyze_file, to_json
    from assetforge.audio.analysis.diagnose import diagnose, summarize
    from assetforge.audio.analysis.interpret import text
    from assetforge.audio.analysis.visuals import render

    with session(ctx, json_) as s:
        path, akind, asset = _resolve(s, target)
        kind = kind or akind
        report = analyze_file(path, kind=kind, with_vad=not no_vad)
        findings = diagnose(report, kind=kind, loop=_loop_of(asset))
        out = to_json(report) | {"findings": findings, "summary": summarize(findings), "text": text(report, findings)}
        if visuals is not None:
            out["visuals"] = render(report, visuals, path=path)
        s.out.emit(out, lambda c: _print_report(c, out, path))


@audio_app.command("diagnose")
def diagnose_cmd(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Audio file path, asset ID, variant ID or key.")],
    kind: Annotated[str | None, typer.Option(help="music | ambience | sfx.")] = None,
    brief: Annotated[str | None, typer.Option(help="What the sound was meant to be (voices allowed? rain? energy?).")] = None,
    direction: Annotated[str | None, typer.Option(help="A production direction as JSON (energy, brightness, activity…).")] = None,
    loop: Annotated[bool | None, typer.Option("--loop/--no-loop", help="Whether the sound must loop seamlessly.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Name the problems against a brief: evidence, likely cause, and the correction that addresses it."""
    from assetforge.audio.analysis import analyze_file, to_json
    from assetforge.audio.analysis.corrections import plan_corrections
    from assetforge.audio.analysis.diagnose import diagnose, summarize
    from assetforge.audio.analysis.interpret import text

    with session(ctx, json_) as s:
        path, akind, asset = _resolve(s, target)
        kind = kind or akind or "music"
        d = json.loads(direction) if direction else None
        report = analyze_file(path, kind=kind)
        findings = diagnose(report, kind=kind, brief=brief, direction=d, loop=loop if loop is not None else _loop_of(asset))
        plan = plan_corrections(findings, kind=kind)
        out = {"path": str(path), "kind": kind, "findings": findings, "summary": summarize(findings), "plan": plan,
               "text": text(report, findings), "measured": to_json(report)}  # fmt: skip

        def human(c: Console) -> None:
            _print_report(c, out, path)
            if plan["reasons"]:
                c.print("[cyan]Plan[/cyan]")
                for r in plan["reasons"]:
                    c.print(f"  {escape(r)}")

        s.out.emit(out, human)


@audio_app.command("correct")
def correct_cmd(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Asset ID / key (a new variant is made) or a file path (needs --out).")],
    out: Annotated[Path | None, typer.Option(help="For a file: where to write the corrected WAV.")] = None,
    kind: Annotated[str | None, typer.Option(help="music | ambience | sfx.")] = None,
    brief: Annotated[str | None, typer.Option(help="The brief the diagnosis is judged against.")] = None,
    prefer: Annotated[str, typer.Option(help="precise (DSP / layer first) or regenerate.")] = "precise",
    json_: JsonOpt = False,
) -> None:
    """Diagnose, plan the targeted corrections, apply the DSP ones and re-measure. Composition, layer and
    regeneration steps are reported for the production loop, not applied here."""
    from assetforge.audio.analysis import analyze_file
    from assetforge.audio.analysis.corrections import compare, correct_asset, fill_donors, plan_corrections
    from assetforge.audio.analysis.diagnose import diagnose, summarize

    with session(ctx, json_) as s:
        path, akind, asset = _resolve(s, target)
        kind = kind or akind or "music"
        report = analyze_file(path, kind=kind)
        findings = diagnose(report, kind=kind, brief=brief, loop=_loop_of(asset))
        plan = fill_donors(plan_corrections(findings, kind=kind, prefer=prefer), report)
        result: dict[str, Any] = {"path": str(path), "kind": kind, "findings": findings, "plan": plan, "applied": None}
        if asset is not None:
            d = correct_asset(s.lib, asset.id, plan, label="corrected")
            if d:
                after = analyze_file(Path(d["path"]) if d.get("path") else path, kind=kind) if d.get("path") else None
                result["applied"] = d
                if after is not None:
                    after_f = diagnose(after, kind=kind, brief=brief, loop=_loop_of(asset))
                    result["comparison"] = compare({"findings": findings}, {"findings": after_f}, [f["code"] for f in findings if f["severity"] != "note"])
        else:
            if out is None:
                raise InvalidInputError("--out is required when correcting a file")
            from assetforge.audio.analysis.corrections import correct_file

            result |= correct_file(path, out, findings, report, kind=kind, brief=brief)
        result["summary"] = summarize(findings)

        def human(c: Console) -> None:
            for r in plan["reasons"]:
                c.print(f"  {escape(r)}")
            if result.get("comparison"):
                cmp_ = result["comparison"]
                c.print(f"[bold]{cmp_['verdict']}[/bold]: fixed {cmp_['fixed']}, remaining {cmp_['remaining']}, regressions {cmp_['regressions']}")
            elif not plan["dsp"]:
                c.print("[dim]no DSP correction applies; the plan needs the production loop (composition / layers / regeneration)[/dim]")

        s.out.emit(result, human, status="ok" if result.get("applied") or result.get("comparison") else "partial")


@audio_app.command("profile")
def profile_cmd(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Project whose sound identity gets the measured profile.")],
    refs: Annotated[list[str] | None, typer.Argument(help="Assets or files to profile (default: the project's approved assets of --kind).")] = None,
    kind: Annotated[str, typer.Option(help="music | ambience | sfx.")] = "music",
    json_: JsonOpt = False,
) -> None:
    """Measure what the project's approved sound is like and store it as a reusable profile."""
    from assetforge.audio.analysis.reference import describe, profile_files, set_profile

    with session(ctx, json_) as s:
        paths: list[Path] = []
        if refs:
            for r in refs:
                paths.append(_resolve(s, r)[0])
        else:
            rows = s.lib.conn.execute("SELECT id FROM assets WHERE project = ? AND type = ? AND status = 'approved'", (project, kind)).fetchall()
            for row in rows:
                try:
                    paths.append(_resolve(s, row["id"])[0])
                except (NotFoundError, InvalidInputError):
                    continue
        if not paths:
            raise NotFoundError(f"No approved {kind} assets in project {project!r} and no refs given")
        prof = profile_files(paths, kind=kind, name=f"{project}/{kind}", note=f"from {len(paths)} approved {kind} assets")
        set_profile(s.lib.paths.home, project, kind, prof)
        s.out.emit({"project": project, "kind": kind, "files": len(paths), "profile": prof, "description": describe(prof)},
                   lambda c: c.print("\n".join(f"  {escape(line)}" for line in describe(prof))))


@models_app.command("status")
def models_status(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Which optional analysis models are installed."""
    from assetforge.audio.analysis import silero, taggers

    with session(ctx, json_) as s:
        home = s.lib.paths.home
        out = {"silero_vad": {"installed": silero.model_path(home=home) is not None, "usable": silero.available(home=home),
                              "path": str(silero.model_path(home=home) or ""), **{k: v for k, v in silero.MODELS["silero-vad"].items() if k != "sha256"}},
               "tagger": taggers.spec(home) or {"installed": False, "note": "drop a verified ONNX tagger as models/tagger.onnx + tagger.json"}}  # fmt: skip
        s.out.emit(out, lambda c: c.print(escape(json.dumps(out, indent=1))))


@models_app.command("install")
def models_install(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="silero-vad")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the download (2.3 MB, MIT).")] = False,
    from_: Annotated[Path | None, typer.Option("--from", help="Install from a local copy instead of downloading.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Download (or copy) an optional model with a pinned SHA-256 into the library's models folder."""
    from assetforge.audio.analysis import silero

    with session(ctx, json_) as s:
        if name not in silero.MODELS:
            raise NotFoundError(f"Unknown model {name!r}", allowed=list(silero.MODELS))
        if not yes and from_ is None:
            raise InvalidInputError("Pass --yes to download, or --from FILE", hint=silero.MODELS[name]["url"])
        info = silero.install(name, home=s.lib.paths.home, source=from_)
        s.out.emit({"model": name, **info}, lambda c: c.print(f"installed {name}: {escape(info['sha256'][:12])}… ({escape(info['licence'])})"))
