"""Plumbing shared by every CLI command: the --json flag and the library session."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from assetforge.core.catalog import Library
from assetforge.errors import AssetForgeError, InternalError, InvalidInputError, StorageError
from assetforge.output import Output

JsonOpt = Annotated[
    bool, typer.Option("--json", envvar="ASSETFORGE_JSON", help="Print one JSON document on stdout.")
]


@dataclass
class Session:
    lib: Library
    out: Output


# Long commands that register themselves as tasks, so a run started in a terminal shows in the app's Activity centre:
# (group, command) → (kind, title). Backend commands only when they really write (--yes).
TRACKED: dict[tuple[str, str], tuple[str, str]] = {
    ("story", "world"): ("story.world", "Build the world"), ("story", "names"): ("story.names", "Naming guide"),
    ("story", "cast"): ("story.cast", "Create the characters"), ("story", "outline"): ("story.outline", "Plan the chapters"),
    ("story", "write"): ("story.write", "Write chapters"), ("story", "refine"): ("story.refine", "Revise a chapter"),
    ("story", "improve"): ("story.improve", "Improve from feedback"), ("story", "check"): ("story.check", "Continuity check"),
    ("story", "assets"): ("story.assets", "Make art & sound"), ("story", "export"): ("story.export", "Export the demo"),
    ("story", "make"): ("story.make", "Make a story"), ("story", "contract"): ("story.contract", "Compile a release"),
    ("story", "publish"): ("story.publish", "Publish a release"), ("story", "art-check"): ("story.art_check", "Check art logic"),
    ("backend", "publish"): ("backend.publish", "Publish to staging"), ("backend", "promote"): ("backend.promote", "Promote to production"),
    ("backend", "rollback"): ("backend.rollback", "Roll back"), ("backend", "unlist"): ("backend.unlist", "Unlist a story"),
    ("backend", "relist"): ("backend.relist", "Relist a story"), ("backend", "repair"): ("backend.repair", "Repair the backend"),
    ("backend", "gc"): ("backend.gc", "Collect unused objects"),
}  # fmt: skip
WRITES_ONLY_WITH_YES = {"backend", ("story", "publish")}


def _story_of(ctx: typer.Context, lib: Library) -> str | None:
    """The registered story id a command works on (best effort; None when it can't be told)."""
    from pathlib import Path

    from assetforge.story.project import StoryProject, register, registered

    params = ctx.params or {}
    known = registered(lib.paths.home)
    ref = params.get("story")
    if ref and ref in known:
        return str(ref)
    try:
        if ref and (Path(str(ref)) / "story.json").exists():
            root = StoryProject(Path(str(ref))).root
        else:
            from assetforge.cli_story import find_dir

            root = find_dir(params.get("directory")).root
    except (AssetForgeError, OSError, ValueError):
        return None
    for sid, path in known.items():
        if Path(path).resolve() == root:
            return sid
    try:
        return register(lib.paths.home, StoryProject(root))
    except (AssetForgeError, OSError, ValueError):
        return None


def _start_task(ctx: typer.Context, lib: Library) -> Any:
    """Register this command as a task when it is one of the long ones (never fails the command)."""
    import os

    group = ctx.parent.info_name if ctx.parent is not None else ""
    spec = TRACKED.get((group or "", ctx.info_name or ""))
    if spec is None or os.environ.get("ASSETFORGE_NO_TASKS") == "1":
        return None
    params = ctx.params or {}
    if (group in WRITES_ONLY_WITH_YES or (group, ctx.info_name) in WRITES_ONLY_WITH_YES) and not params.get("yes"):
        return None
    if params.get("dry_run"):
        return None
    try:
        from assetforge.core import tasks

        kind, title = spec
        action = kind.split(".", 1)[1]
        story = _story_of(ctx, lib) if group == "story" else (str(params.get("story")) if params.get("story") else None)
        project = None
        if group == "story" and story:
            from assetforge.story.project import StoryProject, registered

            try:
                project = StoryProject(registered(lib.paths.home)[story]).config.asset_project
            except (AssetForgeError, KeyError, OSError, ValueError):
                project = None
        locks: tuple[str, ...] = ()
        cancellable = False
        if group == "story":
            from assetforge.app.api_story import CANCELLABLE, LOCKS

            locks = LOCKS.get(action, ("text", "cast") if action in ("world", "names", "cast", "outline", "make") else ())
            cancellable = action in CANCELLABLE or action in ("world", "names", "cast", "outline", "make")
        extra = params.get("scene") or ""
        if isinstance(extra, (list, tuple)):
            extra = ", ".join(str(x) for x in extra)
        return tasks.create(lib, kind, title=f"{title}{f' · {extra}' if extra else ''}{f' — {story}' if story else ''}",
                            story=story, project=project, started_by=os.environ.get("ASSETFORGE_STARTED_BY") or "cli", locks=locks,
                            cancellable=cancellable, command=_command_line(group or "", ctx.info_name or ""),
                            result_link=f"#/story/{story}" if story else ("#/backend" if group == "backend" else None))  # fmt: skip
    except Exception:  # noqa: BLE001 - tracking must never stop the work
        return None


def _command_line(group: str, name: str) -> str:
    """The command as typed (from argv when this process is the CLI), else just its name."""
    import shlex
    import sys

    argv = sys.argv[1:]
    if group in argv and name in argv:
        return "assetforge " + " ".join(shlex.quote(a) for a in argv)
    return f"assetforge {group} {name}"


def _end_task(task: Any, *, code: int | None = None, error: AssetForgeError | None = None) -> None:
    if task is None or task.ended:
        return
    from assetforge.core.tasks import TaskCancelled

    try:
        if isinstance(error, TaskCancelled):
            task.cancel_done()
        elif error is not None:
            task.fail(error.to_dict())
        elif code in (None, 0):
            task.succeed()
        elif code == 8:
            task.succeed(message="Finished with problems (partial)")
        else:
            task.fail({"code": "EXIT", "message": f"Exited with code {code}"})
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def session(ctx: typer.Context, json_mode: bool) -> Iterator[Session]:
    """Open the library for one command and turn AssetForge errors into the documented output + exit code.
    Long commands (TRACKED) are recorded as durable tasks for the app's Activity centre."""
    from assetforge.core import tasks as tasks_mod

    out = Output(json_mode)
    lib: Library | None = None
    task = None
    token = None
    try:
        lib = Library((ctx.find_root().obj or {}).get("home"))
        task = _start_task(ctx, lib)
        token = tasks_mod.CURRENT.set(task)
        yield Session(lib, out)
        _end_task(task)
    except (typer.Exit, typer.Abort) as exc:
        _end_task(task, code=getattr(exc, "exit_code", 1))
        raise
    except AssetForgeError as exc:
        _end_task(task, error=exc)
        out.error(exc)
        raise typer.Exit(int(exc.exit_code)) from None
    except ValidationError as exc:
        error = invalid_input(exc)
        _end_task(task, error=error)
        out.error(error)
        raise typer.Exit(int(error.exit_code)) from None
    except sqlite3.Error as exc:
        error = StorageError(f"Catalog database error: {exc}")
        _end_task(task, error=error)
        out.error(error)
        raise typer.Exit(int(error.exit_code)) from None
    except Exception as exc:  # a bug: still answer with the JSON contract, keep the traceback for a report
        log_path = _log_internal_error(lib, exc)
        error = InternalError(f"{type(exc).__name__}: {exc}", hint=f"Traceback saved to {log_path}" if log_path else None)
        _end_task(task, error=error)
        out.error(error)
        raise typer.Exit(int(error.exit_code)) from None
    finally:
        if task is not None:
            if not task.ended:
                task.interrupt("Stopped before it finished (Ctrl-C or a crash)")
            task.close()
        if token is not None:
            tasks_mod.CURRENT.reset(token)
        if lib is not None:
            lib.close()


def _log_internal_error(lib: Library | None, exc: Exception) -> str | None:
    import sys
    import traceback

    from assetforge.core.util import utcnow

    text = "".join(traceback.format_exception(exc))
    sys.stderr.write(text)
    if lib is None:
        return None
    try:
        log_dir = lib.paths.home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "errors.log"
        with path.open("a") as handle:
            handle.write(f"--- {utcnow()}\n{text}\n")
        return str(path)
    except OSError:
        return None


def invalid_input(exc: ValidationError) -> InvalidInputError:
    problems = [f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()]
    return InvalidInputError("; ".join(problems), errors=problems)


def dump(model: Any) -> Any:
    if isinstance(model, list):
        return [dump(m) for m in model]
    return model.model_dump(mode="json")


def parse_pairs(pairs: list[str], option: str) -> dict[str, Any]:
    """['a=1', 'b=x'] -> {'a': 1, 'b': 'x'}; values are parsed as JSON when possible."""
    import json

    result: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise InvalidInputError(f"{option} expects key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        try:
            result[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            result[key.strip()] = raw
    return result
