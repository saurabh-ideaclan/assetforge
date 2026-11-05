"""Claude through the `claude` CLI (Claude Code) you are logged in to: bills your Claude plan, no API key.

    claude -p --output-format json --json-schema SCHEMA --tools "" --no-session-persistence
           --strict-mcp-config --system-prompt SYSTEM [--model M]      (prompt on stdin)

Not `--bare`: bare mode skips keychain reads, which is where the subscription login lives.
"""

from __future__ import annotations

import json
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

    def argv(self, call: TextCall) -> list[str]:
        exe = runner.which(self.binary) or self.binary
        args = [exe, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--strict-mcp-config",
                "--system-prompt", call.system or DEFAULT_SYSTEM]  # fmt: skip
        if call.schema:
            args += ["--json-schema", json.dumps(call.schema, separators=(",", ":"))]
        if model := call.model or self.default_model():
            args += ["--model", model]
        return args

    def run(self, call: TextCall) -> TextResponse:
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
        models = list((doc.get("modelUsage") or {}).keys())
        return TextResponse(
            text=text, data=data, provider=self.name, model=models[0] if models else (call.model or self.default_model() or "default"),
            billing=self.billing,
            usage={"input_tokens": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                   + usage.get("cache_creation_input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                   "notional_cost_usd": doc.get("total_cost_usd"), "duration_ms": doc.get("duration_ms")},
            raw={k: doc.get(k) for k in ("session_id", "num_turns", "stop_reason", "terminal_reason")},
        )  # fmt: skip
