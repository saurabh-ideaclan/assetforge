"""Narrative clarity: the reader's view, the knowledge model, the cold read, the revision note, the leak check,
and the gate inside `write`."""

from __future__ import annotations

from pathlib import Path

import pytest

from assetforge.core.catalog import Library
from assetforge.story import clarity, steps
from assetforge.story.models import Outline, Scene


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def _planned(lib: Library, tmp_path: Path):
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=3, endings=1, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx)
    return project, ctx


def test_reader_view_shows_only_what_the_player_sees() -> None:
    scene = Scene(id="s1", title="One", location="yard", nodes=[
        {"kind": "show", "character": "eli_hale", "expression": "worried"},
        {"kind": "narrate", "text": "The clerk keeps the fee."},
        {"kind": "say", "speaker": "noor_adeyemi", "text": "Let him try.", "expression": "determined"},
        {"kind": "narrate", "text": "Only on one path.", "when": [{"var": "bold", "op": ">=", "value": 1}]},
        {"kind": "choice", "choices": [{"text": "Keep it.", "set": [{"var": "kept", "op": "set", "value": 1}],
                                        "reaction": [{"speaker": "eli_hale", "text": "I'll keep what you wrote."}, {"text": "He frowns."}]},
                                       {"text": "Give it back.", "reaction": [{"text": "You hand it over."}]}]},
        {"kind": "place", "location": "hale_repair", "time": "evening", "text": "Your mother has a pump open."},
        {"kind": "jump", "goto": "s2"},
    ])
    view = clarity.reader_view(scene, {"eli_hale": "Eli", "noor_adeyemi": "Noor"})
    assert "Noor: Let him try." in view and "[on some routes]" in view and "(1) Keep it." in view and "Eli: I'll keep what you wrote." in view
    assert "— hale repair, evening —" in view and "eli_hale" not in view and "expression" not in view and "bold" not in view and "goto" not in view


def test_terms_plan_knowledge_block_audit_and_revision(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _planned(lib, tmp_path)
    outline = project.load("outline", Outline)
    data = clarity.terms_plan(ctx)
    assert data.terms and data.terms.terms[0].first_needed == outline.scenes[0].id and ctx.calls[-1]["task"] == "clarity.terms"
    block = clarity.knowledge_block(project, outline, outline.scenes[0].id)
    assert "READER KNOWLEDGE" in block and "Introduce in THIS chapter" in block
    later = clarity.knowledge_block(project, outline, outline.scenes[-1].id)
    assert "Already met" in later
    # the gate runs inside write: a cold read per chapter, and a revision when it says revise
    out = steps.write(ctx)
    for sid, fixes in out["fixes"].items():
        assert any(f.startswith("clarity:") for f in fixes), (sid, fixes)
    first = outline.scenes[0].id
    report = clarity.load_report(project, first)
    assert report is not None and report.scene == first and report.reviewer.get("provider") == "fake"
    rows = clarity.problems(report)
    note = clarity.revision_note(report, "keep the ending")
    assert note.startswith("CLARITY REVISION") and note.endswith("keep the ending") and (len(rows) == 0 or rows[0] in note)
    # the path to a later chapter lists the chapters a reader passed through
    assert clarity.path_before(outline, outline.scenes[-1].id)[0] == outline.start
    # an audit on an unwritten chapter is refused
    with pytest.raises(Exception):
        clarity.audit(ctx, "nope")


def test_leaks_find_ids_and_markup_in_player_text() -> None:
    scene = Scene(id="s1", title="One", location="yard", nodes=[
        {"kind": "narrate", "text": "IF trust_mara >= 3: she smiles."},
        {"kind": "say", "speaker": "eli_hale", "text": "Fine.", "expression": "neutral"},
        {"kind": "narrate", "text": "eli_hale walks in."},
        {"kind": "narrate", "text": "A clean line."},
    ])
    found = clarity.leaks(scene, {"eli_hale": "Eli"})
    assert {f["line"] for f in found} == {0, 2}


def test_a_fake_provider_run_never_picks_a_signed_in_cli(lib: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The clarity gate prefers Claude (Sonnet audit, Opus revision) when it is signed in, but a run that asked for the
    fake provider must stay offline: a manual `story write --provider fake` once reached the real CLI through it."""
    import assetforge.llm as llm

    class Signed:
        name = "claude-cli"

    monkeypatch.setattr(llm, "provider_chain", lambda *a, **k: [Signed()])
    project = steps.init(tmp_path / "s", title="T", premise="A lighthouse keeper finds a letter.", scenes=3, endings=2)
    real = steps.Ctx(lib=lib, project=project)
    assert steps._clarity_models(real) == ({"provider": "claude-cli", "model": "sonnet"}, {"provider": "claude-cli", "model": "opus"})
    offline = steps.Ctx(lib=lib, project=project, provider="fake")
    assert steps._clarity_models(offline) == ({}, {})
    configured = steps.init(tmp_path / "f", title="T", premise="A lighthouse keeper finds a letter.", scenes=3, endings=2, provider="fake")
    assert steps._clarity_models(steps.Ctx(lib=lib, project=configured)) == ({}, {})
