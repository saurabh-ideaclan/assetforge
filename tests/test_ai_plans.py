"""AI execution plans: precedence (override > story > library > managed), paid-fallback policy, the transparency
record on every job, and the Anthropic API provider (never a real call)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from assetforge.config import Settings
from assetforge.core.catalog import Library
from assetforge.errors import ProviderError
from assetforge.llm import plans
from assetforge.llm.anthropic_api import AnthropicAPIProvider
from assetforge.llm.base import TextCall, TextProvider, TextResponse
from assetforge.llm.structured import generate


class Answer(BaseModel):
    title: str


class Stub(TextProvider):
    """A provider that answers, or fails with a given error kind."""

    def __init__(self, name: str, billing: str = "subscription", fail: str | None = None) -> None:
        self.name, self.billing, self.fail = name, billing, fail  # type: ignore[assignment]
        self.calls: list[TextCall] = []

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, call: TextCall) -> TextResponse:
        self.calls.append(call)
        if self.fail:
            raise ProviderError(f"{self.name} failed", provider=self.name, kind=self.fail)
        return TextResponse(text=json.dumps({"title": f"from {self.name} {call.model or ''}".strip()}), provider=self.name,
                            model=call.model or "default", billing=self.billing)  # type: ignore[arg-type]


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Stub]:
    from assetforge import llm  # the autouse fixture replaces llm.OVERRIDES: register on the live dict

    made = {"sub-a": Stub("sub-a"), "sub-b": Stub("sub-b"), "paid": Stub("paid", billing="api")}
    for name, stub in made.items():
        monkeypatch.setitem(llm.OVERRIDES, name, stub)
    return made


def _settings(**values: str) -> Settings:
    return Settings(values=values)


def test_task_kinds_and_managed_choice() -> None:
    assert plans.task_kind("story.scene.s01.p2") == "scene" and plans.task_kind("story.check") == "evaluation"
    assert plans.task_kind("story.outline") == "outline" and plans.task_kind("story.improve") == "rewrite"
    assert "outline" in plans.HEAVY_KINDS and "scene" not in plans.HEAVY_KINDS


def test_resolve_precedence_and_paid_policy(home: Path, stubs: dict[str, Stub]) -> None:
    s = _settings()
    library = plans.ExecutionPlan(managed=False, steps=[plans.Step(provider="sub-a"), plans.Step(provider="paid", model="m")],
                                  paid_fallback="confirm")  # fmt: skip
    plans.save_plan(home, library)
    assert plans.load_plan(home).steps[1].model == "m"
    # library plan: the paid step is held back until confirmed
    r = plans.resolve("story.world", s, home=home)
    assert r.chosen_by == "library" and [x.provider for x in r.steps] == ["sub-a"]
    assert r.needs_confirmation and r.needs_confirmation.provider == "paid" and r.skipped[0]["reason"].startswith("paid fallback needs")
    # allowed for this run: the paid step is kept
    r = plans.resolve("story.world", s, home=home, allow_paid=True)
    assert [x.provider for x in r.steps] == ["sub-a", "paid"] and r.needs_confirmation is None
    # never: dropped, no confirmation asked
    plans.save_plan(home, library.model_copy(update={"paid_fallback": "never"}))
    r = plans.resolve("story.world", s, home=home)
    assert [x.provider for x in r.steps] == ["sub-a"] and r.needs_confirmation is None and r.skipped[0]["reason"] == "paid fallback is off"
    # allow: kept automatically
    plans.save_plan(home, library.model_copy(update={"paid_fallback": "allow"}))
    assert [x.provider for x in plans.resolve("story.world", s, home=home).steps] == ["sub-a", "paid"]
    # story plan beats the library plan; per-task steps beat the plan's default steps
    story = plans.ExecutionPlan(managed=False, steps=[plans.Step(provider="sub-b")], per_task={"outline": [plans.Step(provider="sub-a", model="big")]})
    r = plans.resolve("story.outline", s, home=home, story_plan=story)
    assert r.chosen_by == "story" and r.steps[0].key() == "sub-a:big"
    assert plans.resolve("story.scene.x", s, home=home, story_plan=story).steps[0].provider == "sub-b"
    # a run override comes first and keeps the rest as fallbacks
    r = plans.resolve("story.scene.x", s, home=home, story_plan=story, override_provider="sub-a", override_model="fast")
    assert r.chosen_by == "override" and r.steps[0].key() == "sub-a:fast" and r.steps[1].provider == "sub-b"


def test_generate_records_the_choice_and_falls_back(lib: Library, stubs: dict[str, Stub]) -> None:
    stubs["sub-a"].fail = "unavailable"
    s = _settings()
    resolved = plans.Resolved(kind="world", chosen_by="library", steps=[plans.Step(provider="sub-a", model="x"), plans.Step(provider="sub-b", model="y")])
    out = generate(lib, TextCall(prompt="p", task="story.world"), Answer, settings=s, resolved=resolved)
    assert out.provider == "sub-b" and out.model == "y" and out.data.title == "from sub-b y"
    assert out.choice["requested"] == {"provider": "sub-a", "model": "x"} and out.choice["used"]["provider"] == "sub-b"
    assert out.choice["fallbacks"][0]["provider"] == "sub-a" and out.choice["chosen_by"] == "library"
    assert stubs["sub-b"].calls[0].model == "y"  # each step uses its own model
    row = lib.conn.execute("SELECT request, result FROM generation_jobs WHERE id = ?", (out.job_id,)).fetchone()
    assert json.loads(row["request"])["choice"]["chosen_by"] == "library" and json.loads(row["result"])["choice"]["used"]["model"] == "y"


def test_generate_asks_before_a_paid_fallback(lib: Library, stubs: dict[str, Stub]) -> None:
    stubs["sub-a"].fail = "unavailable"
    s = _settings()
    resolved = plans.Resolved(kind="world", chosen_by="library", steps=[plans.Step(provider="sub-a")],
                              needs_confirmation=plans.Step(provider="paid", model="m"),
                              skipped=[{"provider": "paid", "reason": "paid fallback needs your confirmation"}])  # fmt: skip
    with pytest.raises(ProviderError) as info:
        generate(lib, TextCall(prompt="p", task="story.world"), Answer, settings=s, resolved=resolved)
    assert info.value.kind == "needs_confirmation" and "paid" in info.value.message
    assert stubs["paid"].calls == []


def test_anthropic_provider_is_opt_in_and_parses_structured_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    off = AnthropicAPIProvider(_settings(ANTHROPIC_API_KEY="k"))
    assert off.available() == (False, "opt-in: set ASSETFORGE_ANTHROPIC_TEXT_MODEL to use the API (billed per token)")
    prov = AnthropicAPIProvider(_settings(ANTHROPIC_API_KEY="k", ASSETFORGE_ANTHROPIC_TEXT_MODEL="claude-opus-5-5"))
    assert prov.available()[0] and prov.billing == "api" and prov.models()[0]["id"] == "claude-opus-5-5"
    seen: dict[str, Any] = {}

    class FakeMessages:
        def create(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return SimpleNamespace(stop_reason="end_turn", model=kwargs["model"], stop_details=None,
                                   content=[SimpleNamespace(type="text", text='{"title": "Ash"}')],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                         cache_creation_input_tokens=0))  # fmt: skip

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: SimpleNamespace(messages=FakeMessages()))
    call = TextCall(prompt="write", system="sys", schema={"type": "object", "properties": {"title": {"type": "string"}},
                                                        "required": ["title"], "additionalProperties": False}, task="story.world")  # fmt: skip
    out = prov.run(call)
    assert out.data == {"title": "Ash"} and out.model == "claude-opus-5-5" and out.usage["input_tokens"] == 10
    assert seen["output_config"]["format"]["type"] == "json_schema" and seen["system"] == "sys"

    class Refusing(FakeMessages):
        def create(self, **kwargs: Any) -> Any:
            return SimpleNamespace(stop_reason="refusal", model="m", content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=0),
                                   stop_details=SimpleNamespace(category="general_harms", explanation="declined"))  # fmt: skip

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: SimpleNamespace(messages=Refusing()))
    with pytest.raises(ProviderError) as info:
        prov.run(call)
    assert info.value.kind == "content" and not info.value.allows_fallback
