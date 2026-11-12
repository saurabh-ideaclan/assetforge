"""Claude through the `claude` CLI (Claude Code) you are logged in to: bills your Claude plan, no API key.

    claude -p --output-format json --json-schema SCHEMA --tools "" --no-session-persistence
           --strict-mcp-config --system-prompt SYSTEM [--model M]      (prompt on stdin)

Not `--bare`: bare mode skips keychain reads, which is where the subscription login lives.

Image calls (`TextCall.images`): `-p` has no image flag, so the images are copied into the empty temp folder and the
only tool switched on is Read, allowed on exactly those files (`--tools Read --allowedTools "Read(<abs path>)"…`).
Anything else it tries to read is denied (headless runs cannot ask), and no other tool exists in the session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assetforge.errors import ProviderError
from assetforge.llm import runner
from assetforge.llm.base import TextCall, TextProvider, TextResponse

DEFAULT_SYSTEM = "You are a careful writing assistant. Answer with exactly what is asked, nothing else."


class ClaudeCLIProvider(TextProvider):
    name = "claude-cli"
    billing = "subscription"
    binary = "claude"
    login_hint = "Run `claude` in a terminal and sign in with /login (uses your Claude plan)."
    supports_images = True

    def available(self) -> tuple[bool, str]:
        return (True, "") if runner.which(self.binary) else (False, "`claude` CLI not installed (npm i -g @anthropic-ai/claude-code)")

    def default_model(self) -> str | None:
        return self.settings.get("ASSETFORGE_CLAUDE_TEXT_MODEL")

    def signed_in(self) -> tuple[bool | None, str]:
        exe = runner.which(self.binary)
        if not exe:
            return None, "not installed"
        code, out = runner.quick([exe, "auth", "status", "--json"])
        try:
            info = json.loads(out[out.find("{"):])
        except (json.JSONDecodeError, ValueError):
            return None, out.strip()[:200] or f"exit {code}"
        if not info.get("loggedIn"):
            return False, "not signed in"
        plan = info.get("subscriptionType") or info.get("authMethod") or ""
        who = info.get("email") or info.get("account") or ""
        return True, " · ".join(str(x) for x in (plan, who) if x) or "signed in"

    def models(self) -> list[dict[str, Any]]:
        # Claude Code resolves these aliases to the newest model of each family your plan includes.
        return [
            {"id": "", "label": "Default", "description": "Your Claude Code default model"},
            {"id": "opus", "label": "Opus", "description": "Most capable; best prose and structure, slower"},
            {"id": "sonnet", "label": "Sonnet", "description": "Strong writing at good speed (recommended)"},
            {"id": "haiku", "label": "Haiku", "description": "Fastest and lightest on your plan's limits"},
        ]

    def argv(self, call: TextCall, images: list[Path] | None = None) -> list[str]:
        exe = runner.which(self.binary) or self.binary
        tools = ["--tools", "Read", "--allowedTools", *(f"Read({p})" for p in images)] if images else ["--tools", ""]
        args = [exe, "-p", "--output-format", "json", *tools, "--no-session-persistence", "--strict-mcp-config",
                "--system-prompt", call.system or DEFAULT_SYSTEM]  # fmt: skip
        if call.schema:
            args += ["--json-schema", json.dumps(call.loose_schema or call.schema, separators=(",", ":"))]
        if model := call.model or self.default_model():
            args += ["--model", model]
        return args

    def run(self, call: TextCall) -> TextResponse:
        if call.images:
            staged: list[Path] = []

            def prepare(workdir: Path) -> None:
                staged.extend(runner.stage_images(workdir, call.images))

            run, _ = runner.run_cli(self.name, lambda _: self.argv(call, staged), lambda _: image_prompt(call.prompt, staged),
                                    timeout=call.timeout, prepare=prepare)  # fmt: skip
        else:
            run, _ = runner.run_cli(self.name, self.argv(call), call.prompt, timeout=call.timeout)
        return self.parse(run.returncode, run.stdout, run.stderr, call)

    def parse(self, returncode: int, stdout: str, stderr: str, call: TextCall) -> TextResponse:
        try:
            doc: dict[str, Any] = json.loads(stdout)
        except json.JSONDecodeError:
            raise runner.failure(self.name, stderr or stdout, login_hint=self.login_hint) from None
        if doc.get("is_error") or returncode != 0 or doc.get("subtype") not in (None, "success"):
            raise runner.failure(self.name, str(doc.get("result") or stderr or doc.get("subtype")),
                                 login_hint=self.login_hint, model_hint="Set ASSETFORGE_CLAUDE_TEXT_MODEL "
                                 "(e.g. sonnet, opus, haiku) or pass --model")  # fmt: skip
        text = str(doc.get("result") or "")
        data = doc.get("structured_output")
        if call.schema and data is None and not text.strip():
            raise ProviderError("claude-cli returned an empty answer", provider=self.name, kind="bad_response")
        usage = doc.get("usage") or {}
        # Claude Code also makes small Haiku side calls; the answer came from the model with the most output.
        usage_by_model = doc.get("modelUsage") or {}
        main = max(usage_by_model, key=lambda m: (usage_by_model[m] or {}).get("outputTokens", 0), default=None)
        return TextResponse(
            text=text, data=data, provider=self.name, model=main or call.model or self.default_model() or "default",
            billing=self.billing,
            usage={"input_tokens": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                   + usage.get("cache_creation_input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                   "notional_cost_usd": doc.get("total_cost_usd"), "duration_ms": doc.get("duration_ms")},
            raw={k: doc.get(k) for k in ("session_id", "num_turns", "stop_reason", "terminal_reason")},
        )  # fmt: skip


def image_prompt(prompt: str, images: list[Path]) -> str:
    """The prompt with the image files named first: Claude opens each with Read before answering."""
    names = "\n".join(f"- Image {i}: {p}" for i, p in enumerate(images, 1))
    return (f"Before answering, open every image below with the Read tool and look at it carefully; the question is about "
            f"what they show.\n{names}\n\n---\n\n{prompt}")
