"""Editorial evaluation: the fifteen questions with evidence, stored and turned into feedback."""

from __future__ import annotations

from pathlib import Path

import pytest

from assetforge.core.catalog import Library
from assetforge.story import evaluate, steps


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def test_evaluate_answers_every_question_with_evidence(lib: Library, tmp_path: Path, fake: None) -> None:
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=3, endings=1, provider="fake")
    ctx = steps.Ctx(lib, project)
    with pytest.raises(Exception):
        evaluate.evaluate(ctx)  # nothing written yet
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    data = evaluate.evaluate(ctx)
    assert [a.id for a in data.answers] == [q for q, _ in evaluate.QUESTIONS] and data.scenes and data.evaluator["provider"]
    assert ctx.calls[-1]["task"] == "evaluation"
    loaded = evaluate.load(project)
    assert loaded and loaded.evaluated_at == data.evaluated_at
    s = evaluate.summary(data)
    assert sum(s["counts"].values()) == len(evaluate.QUESTIONS) and s["changes"]
    fb = evaluate.as_feedback(data)
    assert fb.startswith("Editorial evaluation") and all(c["id"] in fb for c in s["changes"])
    # a subset of scenes, and the prompt carries the chapters' lines with choices marked
    first = project.load("outline").scenes[0].id
    sub = evaluate.evaluate(ctx, [first], fresh=True)
    assert sub.scenes == [first]
    text = evaluate.prompt(project, [project.load_scene(first)], project.load("outline"))
    assert "QUESTIONS" in text and "=== " in text
