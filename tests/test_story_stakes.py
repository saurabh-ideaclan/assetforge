"""Choice QA, the payoff ledger and the reader knowledge state."""

from __future__ import annotations

from pathlib import Path

import pytest

from assetforge.core.catalog import Library
from assetforge.story import clarity, stakes, steps
from assetforge.story.models import Outline, Script


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def _script(scenes: list[dict], variables: list[dict], endings: list[dict] | None = None) -> Script:
    return Script(title="T", slug="t", start=scenes[0]["id"], variables=variables, characters=[], locations=[],
                  endings=endings or [{"id": "end", "title": "End", "summary": "done"}], scenes=scenes)


def test_choice_report_classes_and_findings() -> None:
    variables = [{"name": "trust", "initial": 0, "meaning": "t", "kind": "relationship"}, {"name": "kept", "initial": 0, "meaning": "k", "kind": "flag"},
                 {"name": "lost", "initial": 0, "meaning": "l", "kind": "flag"}]
    s1 = {"id": "s1", "title": "One", "location": "yard", "nodes": [
        {"kind": "narrate", "text": "Start."},
        {"kind": "choice", "choices": [{"text": "warm", "reaction": [{"text": "ok"}]}, {"text": "cold", "reaction": [{"text": "ok"}]}]},  # A
        {"kind": "choice", "choices": [{"text": "keep", "set": [{"var": "kept", "op": "set", "value": 1}], "reaction": [{"text": "kept"}]},
                                       {"text": "give", "set": [{"var": "kept", "op": "set", "value": 0}], "reaction": [{"text": "gave"}]}]},  # B, read in s2
        {"kind": "choice", "choices": [{"text": "a", "set": [{"var": "lost", "op": "set", "value": 1}], "reaction": [{"text": "x"}]},
                                       {"text": "b", "set": [{"var": "lost", "op": "set", "value": 1}], "reaction": [{"text": "y"}]}]},  # same delta, never read
        {"kind": "jump", "goto": "s2"}]}
    s2 = {"id": "s2", "title": "Two", "location": "yard", "nodes": [
        {"kind": "narrate", "text": "You kept it.", "when": [{"var": "kept", "op": "==", "value": 1}]},
        {"kind": "choice", "choices": [{"text": "left", "goto": "s3", "set": [{"var": "trust", "op": "add", "value": 1}]}, {"text": "right", "goto": "s3"}]}]}  # C
    s3 = {"id": "s3", "title": "Three", "location": "yard", "nodes": [{"kind": "narrate", "text": "End."}, {"kind": "ending", "ending": "end"}]}
    r = stakes.choice_report(_script([s1, s2, s3], variables))
    assert r["counts"] == {"A": 1, "B": 2, "C": 1}
    codes = {(f["code"], f.get("var")) for f in r["findings"]}
    assert ("same_delta", "") in codes and ("no_acknowledgement", "lost") in codes and ("no_acknowledgement", "trust") in codes
    assert not any(f["code"] == "no_acknowledgement" and f.get("var") == "kept" for f in r["findings"])
    assert not r["ok"]
    # a chapter whose choices never touch state
    s_only_a = {"id": "s1", "title": "One", "location": "yard", "nodes": [
        {"kind": "choice", "choices": [{"text": "warm", "reaction": [{"text": "ok"}]}, {"text": "cold", "reaction": [{"text": "ok"}]}]}, {"kind": "jump", "goto": "s9"}]}
    s_end = {"id": "s9", "title": "End", "location": "yard", "nodes": [
        {"kind": "choice", "choices": [{"text": "stay", "reaction": [{"text": "ok"}]}, {"text": "go", "reaction": [{"text": "ok"}]}]}, {"kind": "ending", "ending": "end"}]}
    r2 = stakes.choice_report(_script([s_only_a, s_end], variables))
    assert [f["scene"] for f in r2["findings"] if f["code"] == "all_expression"] == ["s1"]  # an ending chapter's reflective choices are by design


def test_ledger_rules_and_knowledge_state(lib: Library, tmp_path: Path, fake: None) -> None:
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=3, endings=1, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx)
    outline = project.load("outline", Outline)
    ledger = stakes.draft_ledger(ctx)
    assert ledger.promises and ledger.promises[0].introduced in {s.id for s in outline.scenes} and ctx.calls[-1]["task"] == "payoffs.ledger"
    clarity.terms_plan(ctx)
    rules = stakes.draft_rules(ctx)
    assert rules.rules and rules.rules[0].term and ctx.calls[-1]["task"] == "rules.registry"
    # the cold reads carry payoff fields; the ledger advances from them and flags chapters without payoff
    steps.write(ctx)
    for s in outline.scenes:
        clarity.audit(ctx, s.id)
    ledger = stakes.advance_ledger(project)
    assert set(ledger.chapters) == {s.id for s in outline.scenes}
    view = stakes.ledger_view(project)
    assert "no_payoff" in view and view["promises"]
    # the reader knowledge state exists per term per chapter, in reading order
    ks = stakes.knowledge_state(project)
    assert ks["order"][0] == outline.start and ks["terms"]
    first_term = next(iter(ks["terms"]))
    assert set(ks["terms"][first_term]) == set(ks["order"])
    assert all(v in ("unknown", "observed", "partially_explained", "understood", "intentionally_misunderstood", "revealed") for v in ks["terms"][first_term].values())
    # lint carries the choice report
    from assetforge.story.lint import lint_story

    steps.assemble(project)
    assert "choices" in lint_story(project.load("script"), project.config)


def test_absolute_set_and_name_drift() -> None:
    variables = [{"name": "nerve", "initial": 0, "meaning": "n", "kind": "personality"}, {"name": "kept", "initial": 0, "meaning": "k", "kind": "flag"}]
    chars = [{"id": "rafe", "name": "Rafe Oduya", "role": "protagonist", "personality": "p", "look": "l", "expressions": ["neutral"]}]
    s1 = {"id": "s1", "title": "One", "location": "yard", "nodes": [
        {"kind": "narrate", "text": "Ask Oduya later. They wrote Evara Oduya on the brass."},
        {"kind": "choice", "choices": [{"text": "bold", "set": [{"var": "nerve", "op": "set", "value": 2}], "reaction": [{"text": "x"}]},
                                       {"text": "keep", "set": [{"var": "kept", "op": "set", "value": 1}], "reaction": [{"text": "y"}]}]},
        {"kind": "narrate", "text": "You ask nothing.", "when": [{"var": "nerve", "op": ">=", "value": 1}]},
        {"kind": "narrate", "text": "Kept.", "when": [{"var": "kept", "op": "==", "value": 1}]},
        {"kind": "jump", "goto": "s2"}]}
    s2 = {"id": "s2", "title": "Two", "location": "yard", "nodes": [
        {"kind": "say", "speaker": "rafe", "text": "Dema Oduya. My mother."}, {"kind": "ending", "ending": "end"}]}
    script = Script(title="T", slug="t", start="s1", variables=variables, characters=chars, locations=[],
                    endings=[{"id": "end", "title": "End", "summary": "done"}], scenes=[s1, s2])
    r = stakes.choice_report(script)
    assert [(f["code"], f["var"]) for f in r["findings"] if f["code"] == "absolute_set"] == [("absolute_set", "nerve")]  # the flag may be set
    c = stakes.continuity_report(script)
    assert not c["ok"] and c["findings"][0]["code"] == "name_drift" and c["findings"][0]["names"] == ["Dema", "Evara"]
    assert "Ask" not in c["mentioned"]["Oduya"]  # an ordinary word before a family name is not a person
    s2["nodes"][0]["text"] = "Evara Oduya. My mother."
    again = Script(title="T", slug="t", start="s1", variables=variables, characters=chars, locations=[],
                   endings=[{"id": "end", "title": "End", "summary": "done"}], scenes=[s1, s2])
    assert stakes.continuity_report(again)["ok"] and stakes.continuity_report(again)["mentioned"] == {"Oduya": {"Evara": ["s1", "s2"]}}
