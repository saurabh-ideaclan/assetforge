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


@contextmanager
def session(ctx: typer.Context, json_mode: bool) -> Iterator[Session]:
    """Open the library for one command and turn AssetForge errors into the documented output + exit code."""
    out = Output(json_mode)
    lib: Library | None = None
    try:
        lib = Library((ctx.find_root().obj or {}).get("home"))
        yield Session(lib, out)
    except (typer.Exit, typer.Abort):
        raise
    except AssetForgeError as exc:
        out.error(exc)
        raise typer.Exit(int(exc.exit_code)) from None
    except ValidationError as exc:
        error = invalid_input(exc)
        out.error(error)
        raise typer.Exit(int(error.exit_code)) from None
    except sqlite3.Error as exc:
        error = StorageError(f"Catalog database error: {exc}")
        out.error(error)
        raise typer.Exit(int(error.exit_code)) from None
    except Exception as exc:  # a bug: still answer with the JSON contract, keep the traceback for a report
        log_path = _log_internal_error(lib, exc)
        error = InternalError(f"{type(exc).__name__}: {exc}", hint=f"Traceback saved to {log_path}" if log_path else None)
        out.error(error)
        raise typer.Exit(int(error.exit_code)) from None
    finally:
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
