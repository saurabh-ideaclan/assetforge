"""Gemini through the `gemini` CLI signed in with a Google account: no API key.

    gemini --output-format json [-m MODEL] -p "Answer now."      (instructions + task on stdin)

The CLI has no schema flag, so the schema is written into the prompt and the answer is validated (and
repaired once) by `structured.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assetforge.llm import runner
from assetforge.llm.base import TextCall, TextProvider, TextResponse
from assetforge.errors import ProviderError


class GeminiCLIProvider(TextProvider):
    name = "gemini-cli"
    billing = "subscription"
    binary = "gemini"
    login_hint = "Run `gemini` in a terminal and choose 'Login with Google'."

    def available(self) -> tuple[bool, str]:
        return (True, "") if runner.which(self.binary) else (False, "`gemini` CLI not installed (npm i -g @google/gemini-cli)")

    def default_model(self) -> str | None:
        return self.settings.get("ASSETFORGE_GEMINI_TEXT_MODEL")

    def signed_in(self) -> tuple[bool | None, str]:
        if not runner.which(self.binary):
            return None, "not installed"
        creds = Path("~/.gemini/oauth_creds.json").expanduser()
        return (True, "Google account") if creds.exists() else (False, "not signed in")

    def models(self) -> list[dict[str, Any]]:
        models = [{"id": "", "label": "Default", "description": "Your gemini CLI default"}]
        if configured := self.default_model():
            models.append({"id": configured, "label": configured, "description": "ASSETFORGE_GEMINI_TEXT_MODEL"})
        return models

    def argv(self, call: TextCall) -> list[str]:
        # --skip-trust: headless runs in our empty temp folder are refused as "untrusted" otherwise
        args = [runner.which(self.binary) or self.binary, "--skip-trust", "--output-format", "json"]
        if model := call.model or self.default_model():
            args += ["-m", model]
        return [*args, "-p", "Answer now, following the instructions above."]

    @staticmethod
    def stdin(call: TextCall) -> str:
        parts = [call.system.strip(), call.prompt]
        if call.schema:
            parts.append("Reply with one JSON object (no prose, no code fence) matching this JSON schema:\n"
                         + json.dumps(call.schema, separators=(",", ":")))  # fmt: skip
        return "\n\n---\n\n".join(p for p in parts if p)

    def run(self, call: TextCall) -> TextResponse:
        run, _ = runner.run_cli(self.name, self.argv(call), self.stdin(call), timeout=call.timeout)
        return self.parse(run.returncode, run.stdout, run.stderr, call)

    def parse(self, returncode: int, stdout: str, stderr: str, call: TextCall) -> TextResponse:
        start = stdout.find("{")
        try:
            doc = json.loads(stdout[start:]) if start >= 0 else {}
        except json.JSONDecodeError:
            doc = {}
        if doc.get("error") or returncode != 0 or "response" not in doc:
            error = doc.get("error") or {}
            message = str(error.get("message") or stderr[-600:] or stdout[-600:])
            if "no longer supported" in message:  # Google retired the free individual tier for this CLI
                raise ProviderError(
                    "gemini-cli: Google no longer serves the free 'Gemini Code Assist for individuals' tier to the "
                    "Gemini CLI", provider=self.name, kind="unsupported",
                    hint="Use a Google AI Pro/Ultra or Workspace (Code Assist) account, or a free AI Studio API key "
                         "(gemini-api provider); Claude and ChatGPT keep working meanwhile.")  # fmt: skip
            raise runner.failure(self.name, message, login_hint=self.login_hint,
                                 model_hint="Set ASSETFORGE_GEMINI_TEXT_MODEL")  # fmt: skip
        text = str(doc.get("response") or "")
        if not text.strip():
            raise ProviderError("gemini-cli returned an empty answer", provider=self.name, kind="bad_response")
        tokens = {"input_tokens": 0, "output_tokens": 0}
        model = call.model or self.default_model() or "default"
        for name, stats in ((doc.get("stats") or {}).get("models") or {}).items():
            model = name
            t = stats.get("tokens") or {}
            tokens["input_tokens"] += int(t.get("prompt") or 0)
            tokens["output_tokens"] += int(t.get("candidates") or 0)
        return TextResponse(text=text, provider=self.name, model=model, billing=self.billing, usage=tokens)
