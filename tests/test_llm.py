"""Text providers: CLI argv/stdin/env, parsing of real CLI output shapes, error mapping, selection, and the
structured-output layer (validation, repair round, retries, fallback, caching). Nothing real is ever run."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from assetforge import llm
from assetforge.config import Settings
from assetforge.core.catalog import Library
from assetforge.errors import ProviderError
from assetforge.llm import provider_chain, runner
from assetforge.llm.base import TextCall, TextProvider, TextResponse
from assetforge.llm.claude_cli import ClaudeCLIProvider
from assetforge.llm.codex_cli import CodexCLIProvider
from assetforge.llm.gemini_cli import GeminiCLIProvider
from assetforge.llm.structured import extract_json, generate, strict_schema

# Captured verbatim from the real CLIs (claude 2.1.216, codex-cli 0.145.0).
CLAUDE_AUTH_ERROR = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": None, "duration_ms": 29,
                     "num_turns": 1, "result": "Failed to authenticate: OAuth session expired and could not be refreshed",
                     "stop_reason": "stop_sequence", "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0},
                     "modelUsage": {}, "terminal_reason": "api_error"}  # fmt: skip
CODEX_OK = "\n".join([
    '{"type":"thread.started","thread_id":"01a1ab49-ddef-7072-a884-3b95cf94ce4b"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Model metadata for `x` not found. Defaulting to fallback metadata"}}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"{\\"greeting\\":\\"hello\\",\\"n\\":7}"}}',
    '{"type":"turn.completed","usage":{"input_tokens":16190,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":20,"reasoning_output_tokens":0}}',
])  # fmt: skip
CODEX_MODEL_ERROR = "\n".join([
    '{"type":"thread.started","thread_id":"01a1ab3a-fbd7-7351-8223-51319baa547a"}',
    '{"type":"turn.started"}',
    '{"type":"error","message":"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The \'gpt-6.1-sol\' model is not supported when using Codex with a ChatGPT account.\\",\\"code\\":\\"model_not_found\\"}}"}',
    '{"type":"turn.failed","error":{"message":"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The \'gpt-6.1-sol\' model is not supported when using Codex with a ChatGPT account.\\",\\"code\\":\\"model_not_found\\"}}"}}',
])  # fmt: skip


class Greeting(BaseModel):
    greeting: str
    n: int = Field(ge=0)
    note: str | None = None


SETTINGS = Settings({})


class FakeSpawn:
    """Stands in for subprocess.run; records the call and can write output files into the temp cwd."""

    def __init__(self, stdout: str = "", returncode: int = 0, files: dict[str, str] | None = None, raise_: Exception | None = None):
        self.stdout, self.returncode, self.files, self.raise_ = stdout, returncode, files or {}, raise_
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cwd = Path(kwargs["cwd"])
        self.calls.append({"argv": argv, "input": kwargs["input"], "cwd": cwd, "env": kwargs["env"],
                           "inputs": {p.name: p.read_text() for p in cwd.iterdir()}})  # fmt: skip
        if self.raise_:
            raise self.raise_
        for name, content in self.files.items():
            (cwd / name).write_text(content)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "which", lambda name: f"/usr/local/bin/{name}" if name in ("claude", "codex", "gemini") else None)


def schema_call(prompt: str = "say hello") -> TextCall:
    return TextCall(prompt=prompt, system="Be brief.", schema=strict_schema(Greeting), task="t")


# ---------------------------------------------------------------------- CLI adapters


def test_claude_cli_argv_stdin_env_and_parse(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    ok = {"type": "result", "subtype": "success", "is_error": False, "result": "", "structured_output": {"greeting": "hi", "n": 3},
          "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 4}, "modelUsage": {"claude-x": {}},
          "total_cost_usd": 0.01}  # fmt: skip
    spawn = FakeSpawn(stdout=json.dumps(ok))
    monkeypatch.setattr(runner, "spawn", spawn)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    response = ClaudeCLIProvider(Settings({"ASSETFORGE_CLAUDE_TEXT_MODEL": "sonnet"})).run(schema_call("secret prompt"))
    call = spawn.calls[0]
    argv = call["argv"]
    assert argv[:2] == ["/usr/local/bin/claude", "-p"] and "--bare" not in argv
    assert argv[argv.index("--tools") + 1] == "" and "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--json-schema") + 1])["additionalProperties"] is False
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "secret prompt" not in " ".join(argv) and call["input"] == "secret prompt"  # prompt only on stdin
    assert call["inputs"] == {} and call["cwd"].name.startswith("assetforge-llm-")  # empty temp dir
    assert not {"CLAUDECODE", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_SESSION_ID"} & set(call["env"])
    assert response.data == {"greeting": "hi", "n": 3} and response.billing == "subscription"
    assert response.model == "claude-x" and response.usage["input_tokens"] == 15


def test_claude_cli_real_auth_error_maps_to_login_hint(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "spawn", FakeSpawn(stdout=json.dumps(CLAUDE_AUTH_ERROR), returncode=1))
    with pytest.raises(ProviderError) as info:
        ClaudeCLIProvider(SETTINGS).run(schema_call())
    assert info.value.kind == "auth" and info.value.code == "PROVIDER_AUTH"
    assert "/login" in (info.value.hint or "") and info.value.allows_fallback and not info.value.retryable


def test_codex_cli_real_success_transcript(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = FakeSpawn(stdout=CODEX_OK, files={"answer.txt": '{"greeting":"hello","n":7}'})
    monkeypatch.setattr(runner, "spawn", spawn)
    response = CodexCLIProvider(Settings({"ASSETFORGE_CODEX_TEXT_MODEL": "gpt-5.6-terra"})).run(schema_call("task"))
    argv = spawn.calls[0]["argv"]
    assert argv[:2] == ["/usr/local/bin/codex", "exec"] and argv[-1] == "-"
    assert {"--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--json"} <= set(argv)
    assert argv[argv.index("-m") + 1] == "gpt-5.6-terra"
    assert json.loads(spawn.calls[0]["inputs"]["schema.json"])["required"] == ["greeting", "n", "note"]
    assert spawn.calls[0]["input"].startswith("Be brief.") and spawn.calls[0]["input"].endswith("task")
    assert json.loads(response.text) == {"greeting": "hello", "n": 7}
    assert response.usage["input_tokens"] == 16190 and response.usage["output_tokens"] == 20


def test_codex_cli_real_model_error_is_rejected_with_update_hint(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "spawn", FakeSpawn(stdout=CODEX_MODEL_ERROR, returncode=1))
    with pytest.raises(ProviderError) as info:
        CodexCLIProvider(SETTINGS).run(schema_call())
    assert info.value.kind == "rejected" and "not supported" in info.value.message
    assert "update" in (info.value.hint or "").lower() and info.value.allows_fallback


def test_gemini_cli_schema_in_prompt_and_errors(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    out = {"response": '```json\n{"greeting": "yo", "n": 1}\n```', "stats": {"models": {"gemini-x": {"tokens": {"prompt": 9, "candidates": 3}}}}}
    spawn = FakeSpawn(stdout="Loaded cached credentials.\n" + json.dumps(out))
    monkeypatch.setattr(runner, "spawn", spawn)
    response = GeminiCLIProvider(SETTINGS).run(schema_call())
    assert "JSON schema" in spawn.calls[0]["input"] and extract_json(response.text) == {"greeting": "yo", "n": 1}
    assert response.model == "gemini-x" and response.usage == {"input_tokens": 9, "output_tokens": 3}
    monkeypatch.setattr(runner, "spawn", FakeSpawn(stdout=json.dumps({"error": {"message": "429 Too Many Requests"}}), returncode=1))
    with pytest.raises(ProviderError) as info:
        GeminiCLIProvider(SETTINGS).run(schema_call())
    assert info.value.kind == "rate_limited" and info.value.retryable


def test_timeout_and_missing_binary(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "spawn", FakeSpawn(raise_=subprocess.TimeoutExpired("claude", 5)))
    with pytest.raises(ProviderError) as info:
        ClaudeCLIProvider(SETTINGS).run(schema_call())
    assert info.value.kind == "unavailable"
    monkeypatch.setattr(runner, "spawn", FakeSpawn(raise_=FileNotFoundError()))
    with pytest.raises(ProviderError) as info:
        ClaudeCLIProvider(SETTINGS).run(schema_call())
    assert info.value.kind == "not_configured"


@pytest.mark.parametrize(("text", "kind"), [
    ("Claude AI usage limit reached|1730000000", "quota"),
    ("You've hit your usage limit. Upgrade to Pro", "quota"),
    ("Invalid API key · Please run /login", "auth"),
    ("API Error: 529 Overloaded", "unavailable"),
    ("rate_limit_error: too many requests", "rate_limited"),
    ("error: unexpected argument '--output-schema' found", "rejected"),
])  # fmt: skip
def test_classify(text: str, kind: str) -> None:
    assert runner.classify(text) == kind


# ---------------------------------------------------------------------- selection


def test_provider_chain(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert [p.name for p in provider_chain(None, SETTINGS)] == ["claude-cli", "codex-cli", "gemini-cli"]
    assert [p.name for p in provider_chain("codex", SETTINGS)][0] == "codex-cli"
    preferred = Settings({"ASSETFORGE_TEXT_PROVIDER": "gemini-cli"})
    assert [p.name for p in provider_chain(None, preferred)][:2] == ["gemini-cli", "claude-cli"]
    # API providers are opt-in: a key alone is not enough
    keyed = Settings({"OPENAI_API_KEY": "sk-test"})
    assert "openai-api" not in [p.name for p in provider_chain(None, keyed)]
    opted = Settings({"OPENAI_API_KEY": "sk-test", "ASSETFORGE_OPENAI_TEXT_MODEL": "gpt-x"})
    assert [p.name for p in provider_chain(None, opted)][-1] == "openai-api"
    fake = Settings({"ASSETFORGE_ENABLE_FAKE_PROVIDER": "1"})
    assert [p.name for p in provider_chain("fake", fake)] == ["fake"]  # never falls back to a real model
    assert "fake" not in [p.name for p in provider_chain(None, fake)]  # and is never picked by auto


def test_nothing_installed_is_a_clear_error(lib: Library) -> None:
    with pytest.raises(ProviderError) as info:
        generate(lib, TextCall(prompt="x"), Greeting)
    assert info.value.kind == "not_configured" and "llm providers" in (info.value.hint or "")


# ---------------------------------------------------------------------- structured layer


class Scripted(TextProvider):
    """Returns queued answers (strings, dicts or ProviderErrors) and records the calls."""

    billing = "subscription"

    def __init__(self, name: str, answers: list[Any]) -> None:
        super().__init__(SETTINGS)
        self.name, self.answers, self.calls = name, list(answers), []

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, call: TextCall) -> TextResponse:
        self.calls.append(call)
        answer = self.answers.pop(0)
        if isinstance(answer, ProviderError):
            raise answer
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return TextResponse(text=text, provider=self.name, model="m1", billing="subscription", usage={"output_tokens": 1})


def pin(*providers: Scripted) -> None:
    for p in providers:
        llm.OVERRIDES[p.name] = p


def test_strict_schema_closes_every_object() -> None:
    schema = strict_schema(Greeting)
    assert schema["additionalProperties"] is False and schema["required"] == ["greeting", "n", "note"]
    assert "minimum" not in json.dumps(schema) and "default" not in json.dumps(schema)


def test_strict_schema_keeps_fields_named_like_keywords() -> None:
    """Regression: a field called `title` (or `format`, `pattern`…) is data, not the schema keyword."""

    class Inner(BaseModel):
        title: str
        format: str = "x"

    class Outer(BaseModel):
        title: str
        items: list[Inner]

    schema = strict_schema(Outer)
    assert schema["required"] == ["title", "items"] and "title" in schema["properties"]
    inner = schema["$defs"]["Inner"]
    assert inner["required"] == ["title", "format"] and "title" not in inner["properties"]["title"]


def test_repair_round_fixes_an_invalid_answer(lib: Library) -> None:
    first = Scripted("claude-cli", ['Sure! {"greeting": "hi", "n": -1}', '{"greeting": "hi", "n": 2, "note": null}'])
    pin(first)
    result = generate(lib, TextCall(prompt="p", task="t"), Greeting, provider="claude-cli", fallback=False)
    assert result.data == Greeting(greeting="hi", n=2) and len(first.calls) == 2
    assert "did not match" in first.calls[1].prompt and "n" in first.calls[1].prompt
    job_dir = lib.paths.jobs / result.job_id
    assert (job_dir / "prompt.md").exists() and (job_dir / "answer_1.txt").exists()


def test_retry_then_fallback_then_cache(lib: Library) -> None:
    busy = ProviderError("busy", provider="claude-cli", kind="rate_limited")
    auth = ProviderError("login", provider="claude-cli", kind="auth", hint="sign in")
    claude = Scripted("claude-cli", [busy, auth])
    codex = Scripted("codex-cli", [{"greeting": "from codex", "n": 1}])
    pin(claude, codex)
    result = generate(lib, TextCall(prompt="p2", task="t"), Greeting, provider="claude-cli")
    assert result.provider == "codex-cli" and len(claude.calls) == 2  # retried once, then auth → fallback
    again = generate(lib, TextCall(prompt="p2", task="t"), Greeting, provider="claude-cli")
    assert again.reused and again.data.greeting == "from codex" and len(codex.calls) == 1
    codex.answers.append({"greeting": "fresh", "n": 2})
    assert generate(lib, TextCall(prompt="p2", task="t"), Greeting, provider="codex-cli", fresh=True).data.greeting == "fresh"


def test_all_providers_fail_reports_the_requested_providers_error(lib: Library) -> None:
    codex = Scripted("codex-cli", ['{"greeting": "no number"}', '{"still": "wrong"}'])
    claude = Scripted("claude-cli", [ProviderError("expired", provider="claude-cli", kind="auth", hint="/login")])
    pin(claude, codex)
    with pytest.raises(ProviderError) as info:
        generate(lib, TextCall(prompt="p3", task="t"), Greeting, provider="codex-cli")
    # codex answered in the wrong shape: that is what gets reported, not claude's login problem
    assert info.value.kind == "bad_response" and info.value.provider == "codex-cli" and "n" in info.value.message
    assert info.value.details["tried"] == ["codex-cli", "claude-cli"]
    assert [e["kind"] for e in info.value.details["errors"]] == ["bad_response", "auth"]
    job = lib.conn.execute("SELECT status FROM generation_jobs WHERE id = ?", (info.value.details["job_id"],)).fetchone()
    assert job["status"] == "failed"


def test_content_refusal_does_not_fall_back(lib: Library) -> None:
    claude = Scripted("claude-cli", [ProviderError("refused", provider="claude-cli", kind="content")])
    codex = Scripted("codex-cli", [{"greeting": "x", "n": 1}])
    pin(claude, codex)
    with pytest.raises(ProviderError):
        generate(lib, TextCall(prompt="p4", task="t"), Greeting, provider="claude-cli")
    assert codex.calls == []
