"""OpenAI models through the `codex` CLI signed in with ChatGPT: bills your ChatGPT plan, no API key.

    codex exec --sandbox read-only --skip-git-repo-check --ephemeral --json
               --output-schema schema.json -o answer.txt [-m MODEL] [-c model_reasoning_effort=…] -

The prompt (with the system instructions in front, since `exec` has no system flag) goes on stdin.
`--json` prints JSONL events (errors, token usage); the final answer is read from `answer.txt`.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from assetforge.errors import ProviderError
from assetforge.llm import runner
from assetforge.llm.base import TextCall, TextProvider, TextResponse

MODEL_REJECTIONS = ("not supported", "model_not_found", "newer version")
_WORKING: str | None = None  # the model this process found the CLI accepts (when none is chosen)
MODEL_HINT = ("The Codex CLI may be older than your account's models: update it (`npm i -g @openai/codex@latest` or "
              "`brew upgrade codex`), or set ASSETFORGE_CODEX_TEXT_MODEL to a model your plan offers.")  # fmt: skip


class CodexCLIProvider(TextProvider):
    name = "codex-cli"
    billing = "subscription"
    binary = "codex"
    login_hint = "Run `codex login` and choose 'Sign in with ChatGPT' (uses your ChatGPT plan)."

    def available(self) -> tuple[bool, str]:
        return (True, "") if runner.which(self.binary) else (False, "`codex` CLI not installed (npm i -g @openai/codex)")

    def default_model(self) -> str | None:
        """ASSETFORGE_CODEX_TEXT_MODEL, else the first model your account lists: codex's own configured
        default can be one your plan rejects (seen: 'not supported when using Codex with a ChatGPT account')."""
        if configured := self.settings.get("ASSETFORGE_CODEX_TEXT_MODEL"):
            return configured
        listed = [m["id"] for m in self.models() if m["id"]]
        return _WORKING or (listed[0] if listed else None)

    def signed_in(self) -> tuple[bool | None, str]:
        exe = runner.which(self.binary)
        if not exe:
            return None, "not installed"
        code, out = runner.quick([exe, "login", "status"])
        text = " ".join(out.split())
        if code == 0 and "logged in" in text.lower():
            return True, text.replace("Logged in using ", "") or "signed in"
        return (False, "not signed in") if "not logged in" in text.lower() else (None, text[:200] or f"exit {code}")

    def _cache(self) -> dict[str, Any]:
        path = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "models_cache.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def models(self) -> list[dict[str, Any]]:
        """The models your ChatGPT plan offers (`visibility: list` in the list the codex CLI keeps), best first."""
        entries = self._cache().get("models") or []
        found = [{"id": m["slug"], "label": m.get("display_name") or m["slug"], "description": m.get("description") or ""}
                 for m in sorted(entries, key=lambda m: m.get("priority") or 99)
                 if isinstance(m, dict) and m.get("visibility") == "list" and m.get("slug")]  # fmt: skip
        return [{"id": "", "label": "Default", "description": "First model your plan offers that this CLI accepts"},
                *found]  # fmt: skip

    def version_note(self) -> str | None:
        """Warn when the CLI is older than the model list your account (or the Codex app) fetched."""
        exe = runner.which(self.binary)
        listed = str(self._cache().get("client_version") or "")
        if not exe or not listed:
            return None
        _, out = runner.quick([exe, "--version"])
        mine = (out.split() or [""])[-1]
        try:
            older = tuple(int(x) for x in mine.split(".")[:3]) < tuple(int(x) for x in listed.split(".")[:3])
        except ValueError:
            return None
        return (f"codex CLI {mine} is older than your account's model list ({listed}): newer models may be rejected. "
                "Update: npm i -g @openai/codex@latest") if older else None  # fmt: skip

    def argv(self, call: TextCall) -> list[str]:
        exe = runner.which(self.binary) or self.binary
        args = [exe, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "--json",
                "-o", "answer.txt"]  # fmt: skip
        if call.schema:
            args += ["--output-schema", "schema.json"]
        if model := call.model or self.default_model():
            args += ["-m", model]
        effort = self.settings.get("ASSETFORGE_CODEX_REASONING") or "medium"
        args += ["-c", f'model_reasoning_effort="{effort}"', "-"]
        return args

    @staticmethod
    def stdin(call: TextCall) -> str:
        return f"{call.system.strip()}\n\n---\n\n{call.prompt}" if call.system.strip() else call.prompt

    def run(self, call: TextCall) -> TextResponse:
        """With no model chosen, try the plan's models in order and remember the first this CLI accepts."""
        if call.model or self.settings.get("ASSETFORGE_CODEX_TEXT_MODEL"):
            return self._run(call)
        global _WORKING
        candidates = list(dict.fromkeys([m for m in (_WORKING, *(m["id"] for m in self.models() if m["id"])) if m]))
        last: ProviderError | None = None
        for model in candidates or [None]:
            try:
                response = self._run(replace(call, model=model))
            except ProviderError as exc:
                if exc.kind == "rejected" and any(w in exc.message for w in MODEL_REJECTIONS):
                    last = exc
                    continue
                raise
            _WORKING = model
            return response
        assert last is not None
        raise last

    def _run(self, call: TextCall) -> TextResponse:
        def prepare(workdir: Path) -> None:
            if call.schema:
                (workdir / "schema.json").write_text(json.dumps(call.schema))

        def collect(workdir: Path) -> str | None:
            path = workdir / "answer.txt"
            return path.read_text() if path.exists() else None

        run, answer = runner.run_cli(self.name, self.argv(call), self.stdin(call), timeout=call.timeout,
                                     prepare=prepare, collect=collect)  # fmt: skip
        return self.parse(run.returncode, run.stdout, run.stderr, answer, call)

    def parse(self, returncode: int, stdout: str, stderr: str, answer: str | None, call: TextCall) -> TextResponse:
        errors: list[str] = []
        usage: dict[str, Any] = {}
        model = call.model or self.default_model() or "default"
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "error":
                errors.append(_message(event.get("message")))
            elif kind == "turn.failed":
                errors.append(_message((event.get("error") or {}).get("message")))
            elif kind == "turn.completed":
                usage = event.get("usage") or {}
            elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message" and answer is None:
                answer = event["item"].get("text")
        if errors or returncode != 0 or answer is None:
            raise runner.failure(self.name, errors[-1] if errors else (stderr[-600:] or "no answer"),
                                 login_hint=self.login_hint, model_hint=MODEL_HINT)  # fmt: skip
        if not answer.strip():
            raise ProviderError("codex-cli returned an empty answer", provider=self.name, kind="bad_response")
        return TextResponse(text=answer, provider=self.name, model=model, billing=self.billing,
                            usage={"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                                   "cached_input_tokens": usage.get("cached_input_tokens", 0)})  # fmt: skip


def _message(raw: Any) -> str:
    """Codex wraps API errors as a JSON string inside `message`; pull out the readable part."""
    text = str(raw or "")
    try:
        inner = json.loads(text)
        error = inner.get("error") or {}
        return " ".join(str(x) for x in (error.get("message"), error.get("code")) if x) or text
    except (json.JSONDecodeError, AttributeError):
        return text
