"""Run a signed-in LLM command-line tool safely and turn its failures into `ProviderError`s.

* The prompt goes through stdin, never argv (no length limits, nothing visible in `ps`).
* The working directory is a fresh empty temp folder, so no project CLAUDE.md/AGENTS.md/settings leak in.
* The environment is scrubbed of variables that would redirect the tool to another account or to API-key
  billing (e.g. a parent Claude Code session's `CLAUDE_CODE_*`/`ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assetforge.errors import ProviderError

# Where the CLIs and node usually live. A Mac app started from Finder gets a bare PATH (/usr/bin:/bin…),
# so these are searched too and passed on to the child (codex and gemini are node programs).
EXTRA_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin", "~/.bun/bin",
              "~/.volta/bin", "~/.claude/local", "~/.codex/packages/standalone/current/bin")  # fmt: skip


def search_path() -> str:
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    extra = [str(Path(d).expanduser()) for d in EXTRA_DIRS]
    extra += sorted((str(p) for p in Path("~/.nvm/versions/node").expanduser().glob("*/bin")), reverse=True)
    return os.pathsep.join(dict.fromkeys([*dirs, *extra]))


def _which(name: str) -> str | None:
    return shutil.which(name, path=search_path())


# Replaced in tests: nothing may spawn a real model CLI there.
spawn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen  # long-running sign-ins (llm/login.py)
which: Callable[[str], str | None] = _which

_SCRUB = re.compile(r"^(CLAUDECODE|CLAUDE_CODE_.*|CLAUDE_AGENT_SDK_.*|ANTHROPIC_.*|OPENAI_.*|CODEX_API_KEY|GEMINI_API_KEY|"
                    r"GOOGLE_API_KEY|GOOGLE_GENAI_.*|ASSETFORGE_.*)$")  # fmt: skip


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SCRUB.match(k)}
    env["PATH"] = search_path()
    return env


@dataclass
class Run:
    returncode: int
    stdout: str
    stderr: str
    workdir: Path


def run_cli(provider: str, argv: list[str], stdin: str, *, timeout: float,
            prepare: Callable[[Path], None] | None = None, collect: Callable[[Path], Any] | None = None) -> tuple[Run, Any]:  # fmt: skip
    """Run `argv` in a temp dir. `prepare(dir)` can write input files first, `collect(dir)` reads outputs."""
    with tempfile.TemporaryDirectory(prefix="assetforge-llm-") as tmp:
        workdir = Path(tmp)
        if prepare:
            prepare(workdir)
        try:
            done = spawn(argv, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=workdir, env=clean_env())
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"{provider} did not answer within {timeout:.0f}s", provider=provider, kind="unavailable",
                                hint="Raise ASSETFORGE_TEXT_TIMEOUT or ask for less per call") from exc  # fmt: skip
        except FileNotFoundError as exc:
            raise ProviderError(f"{argv[0]} is not installed", provider=provider, kind="not_configured") from exc
        run = Run(done.returncode, done.stdout or "", done.stderr or "", workdir)
        return run, collect(workdir) if collect else None


# Ordered: the first pattern that matches decides the kind.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("quota", re.compile(r"usage limit|limit reached|hit your .*limit|quota|out of credits|insufficient_quota|"
                         r"credit balance", re.I)),  # fmt: skip
    ("auth", re.compile(r"not logged in|log ?in|authenticat|oauth|unauthori[sz]ed|\b401\b|invalid api key|"
                        r"sign in|credentials", re.I)),  # fmt: skip
    ("rate_limited", re.compile(r"rate.?limit|too many requests|\b429\b", re.I)),
    ("content", re.compile(r"content policy|safety|refus", re.I)),
    ("rejected", re.compile(r"model_not_found|not supported|invalid_request|\b400\b|unknown model|invalid model|"
                            r"unexpected argument|unrecognized|unknown option|newer version", re.I)),  # fmt: skip
    ("unavailable", re.compile(r"overloaded|\b5\d\d\b|timed? ?out|connection|network|unavailable|ECONN", re.I)),
]


def classify(text: str) -> str:
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return "unavailable"


def failure(provider: str, message: str, *, login_hint: str = "", model_hint: str = "") -> ProviderError:
    message = " ".join(message.split())[:600] or "no output"
    kind = classify(message)
    hint = {"auth": login_hint, "rejected": model_hint, "quota": "Your plan's usage limit is used up: wait for it to "
            "reset or use another provider (--provider)."}.get(kind) or None  # fmt: skip
    return ProviderError(f"{provider}: {message}", provider=provider, kind=kind, hint=hint)


def quick(argv: list[str], *, timeout: float = 15.0) -> tuple[int, str]:
    """Run a fast, model-free status command (e.g. `codex login status`); (-1, reason) when it can't run."""
    try:
        done = spawn(argv, input="", capture_output=True, text=True, timeout=timeout, env=clean_env())
    except (subprocess.TimeoutExpired, OSError) as exc:
        return -1, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")
