"""Visual canon and the visual logic check: image input for the text providers, the judge's question, verdicts,
the review store, the remake loop and the app routes. Offline only (fake text and image providers)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from assetforge import llm
from assetforge.config import Settings
from assetforge.core import jobs
from assetforge.core.catalog import Library
from assetforge.errors import ProviderError
from assetforge.llm import runner
from assetforge.llm.base import TextCall, TextProvider, TextResponse
from assetforge.llm.claude_cli import ClaudeCLIProvider
from assetforge.llm.codex_cli import CodexCLIProvider
from assetforge.llm.structured import generate, strict_schema
from assetforge.request import by_key
from assetforge.story import steps, visual_canon, visual_check
from assetforge.story.assets import manifest, produce
from assetforge.story.visual_canon import LocationCanon, Prop, VisualCanon

from conftest import make_png


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def _written(lib: Library, root: Path):
    project = steps.init(root, title="Fog", premise="An apprentice uncovers smugglers.", scenes=3, endings=1, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)  # noqa: E702
    return project, ctx


def _canon(script: Any) -> VisualCanon:
    first = script.locations[0]
    return VisualCanon(world="One train on a dead dust plain.", outside="Flat pale dust, nothing built.",
                       always=["one single train on one track"], never=["a second train or a parallel track", "any city on the horizon"],
                       locations=[LocationCanon(id=first.id, description="The gap between two carriages of the same train.",
                                                geometry="two carriages joined end to end", never=["green fields"])],
                       props=[Prop(name="Charge", look="a cold blue glow")], state="approved")  # fmt: skip


# ---------------------------------------------------------------------- image input for the text providers


class Seeing(TextProvider):
    billing = "subscription"
    supports_images = True

    def __init__(self, name: str, answer: dict[str, Any]) -> None:
        super().__init__(Settings({}))
        self.name, self.answer, self.calls = name, answer, []

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, call: TextCall) -> TextResponse:
        self.calls.append(call)
        return TextResponse(text=json.dumps(self.answer), provider=self.name, model="m", billing="subscription")


class Blind(Seeing):
    supports_images = False


def test_images_change_the_fingerprint_reach_the_provider_and_blind_providers_are_skipped(lib: Library, tmp_path: Path) -> None:
    from pydantic import BaseModel

    class Count(BaseModel):
        n: int

    seeing, blind = Seeing("codex-cli", {"n": 3}), Blind("gemini-cli", {"n": 9})  # pinned under real names: the chain falls back
    llm.OVERRIDES.update({"codex-cli": seeing, "gemini-cli": blind})
    img = make_png(tmp_path / "a.png", color=(255, 0, 0, 255))
    other = make_png(tmp_path / "b.png", color=(0, 0, 255, 255))
    text_only = generate(lib, TextCall(prompt="count", task="t"), Count, provider="codex-cli", fallback=False)
    # a text-only call keeps the fingerprint it always had (no images key)
    old = jobs.fingerprint({"task": "t", "system": "", "prompt": "count", "schema": strict_schema(Count), "provider": "codex-cli", "model": None})
    assert jobs.get_job(lib, text_only.job_id).fingerprint == old and seeing.calls[-1].images == []
    with_img = generate(lib, TextCall(prompt="count", task="t", images=[img]), Count, provider="codex-cli", fallback=False)
    assert not with_img.reused and with_img.job_id != text_only.job_id and seeing.calls[-1].images == [img]
    again = generate(lib, TextCall(prompt="count", task="t", images=[img]), Count, provider="codex-cli", fallback=False)
    assert again.reused and again.job_id == with_img.job_id
    assert not generate(lib, TextCall(prompt="count", task="t", images=[other]), Count, provider="codex-cli", fallback=False).reused
    # a provider that cannot see is skipped for an image call (it is fine for text)
    n_blind = len(blind.calls)
    got = generate(lib, TextCall(prompt="count?", task="t2", images=[img]), Count, provider="gemini-cli")
    assert got.provider == "codex-cli" and len(blind.calls) == n_blind and got.choice.get("image_blind_skipped") == ["gemini-cli"]
    with pytest.raises(ProviderError) as info:
        generate(lib, TextCall(prompt="count??", task="t3", images=[img]), Count, provider="gemini-cli", fallback=False)
    assert info.value.kind == "unsupported" and "look at images" in info.value.message


class FakeSpawn:
    def __init__(self, stdout: str, files: dict[str, str] | None = None) -> None:
        self.stdout, self.files, self.calls = stdout, files or {}, []

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        cwd = Path(kw["cwd"])
        self.calls.append({"argv": argv, "input": kw["input"], "cwd": cwd, "files": sorted(p.name for p in cwd.iterdir())})
        for name, content in self.files.items():
            (cwd / name).write_text(content)
        return subprocess.CompletedProcess(argv, 0, self.stdout, "")


def test_cli_adapters_stage_images_in_their_temp_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "which", lambda name: f"/usr/local/bin/{name}")
    img = make_png(tmp_path / "pic.png")
    call = TextCall(prompt="judge it", system="Be strict.", schema={"type": "object"}, task="t", images=[img])
    codex = FakeSpawn('{"type":"turn.completed","usage":{}}', files={"answer.txt": '{"ok": true}'})
    monkeypatch.setattr(runner, "spawn", codex)
    CodexCLIProvider(Settings({"ASSETFORGE_CODEX_TEXT_MODEL": "m"})).run(call)
    c = codex.calls[0]
    staged = c["argv"][c["argv"].index("--image") + 1]
    assert c["argv"][:2] == ["/usr/local/bin/codex", "exec"] and Path(staged).parent == c["cwd"].resolve()
    assert "image_1.png" in c["files"] and c["argv"][-1] == "-" and "judge it" in c["input"]
    claude = FakeSpawn(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "",
                                   "structured_output": {"ok": True}, "usage": {}, "modelUsage": {}}))  # fmt: skip
    monkeypatch.setattr(runner, "spawn", claude)
    ClaudeCLIProvider(Settings({})).run(call)
    argv = claude.calls[0]["argv"]
    path = str(claude.calls[0]["cwd"].resolve() / "image_1.png")
    assert argv[argv.index("--tools") + 1] == "Read" and argv[argv.index("--allowedTools") + 1] == f"Read({path})"
    assert path in claude.calls[0]["input"] and claude.calls[0]["input"].endswith("judge it")
    # text-only calls are unchanged: no tools at all
    ClaudeCLIProvider(Settings({})).run(TextCall(prompt="x", schema={"type": "object"}, task="t"))
    argv = claude.calls[1]["argv"]
    assert argv[argv.index("--tools") + 1] == "" and "--allowedTools" not in argv and claude.calls[1]["input"] == "x"
    assert CodexCLIProvider.supports_images and ClaudeCLIProvider.supports_images
    from assetforge.llm.gemini_cli import GeminiCLIProvider

    assert GeminiCLIProvider.supports_images is False


# ---------------------------------------------------------------------- the visual canon


def test_visual_canon_draft_texts_and_manifest(lib: Library, tmp_path: Path, fake: None, monkeypatch: pytest.MonkeyPatch) -> None:
    project, ctx = _written(lib, tmp_path / "s")
    script = project.load("script")
    canon = visual_canon.draft(ctx)
    assert {lc.id for lc in canon.locations} == {loc.id for loc in script.locations} and canon.state == "proposed"
    assert visual_canon.load(project).world == canon.world and ctx.calls[-1]["task"] == "visual.canon"
    loc = script.locations[0]
    rich = _canon(script)
    text = visual_canon.location_text(rich, loc)
    assert text.startswith("The gap between two carriages") and "Layout, exactly: two carriages joined end to end." in text
    assert "Not in this place: green fields." in text
    assert visual_canon.location_text(None, loc) == loc.description
    block = visual_canon.world_block(rich)
    assert "Never paint: a second train or a parallel track; any city on the horizon." in block and "Charge: a cold blue glow" in block
    assert visual_canon.world_block(None) == ""
    world = project.load("world")
    plain = {i.key: i.request.description for i in manifest(script, project.config, world)}
    with_canon = {i.key: i for i in manifest(script, project.config, world, canon=rich)}
    groups = {i.group for i in with_canon.values() if "Never paint: a second train" in i.request.description}
    assert {"bg", "cg", "ui"} <= groups and ("bg_time" in groups or not any(i.group == "bg_time" for i in with_canon.values()))
    title = next(i for i in with_canon.values() if i.key.endswith("/ui/title"))
    assert "Never paint" in title.request.description
    assert not any("Never paint" in d for d in plain.values())  # without a canon prompts are as before
    textbox = next(k for k in plain if k.endswith("/ui/textbox"))
    assert with_canon[textbox].request.description == plain[textbox]  # UI frames carry no world rules
    sprite = next(k for k in plain if "/char/" in k)
    assert with_canon[sprite].request.description == plain[sprite]


# ---------------------------------------------------------------------- the check, review and fix


def _made(lib: Library, tmp_path: Path) -> tuple[Any, Any, Any]:
    project, ctx = _written(lib, tmp_path / "s")
    script = project.load("script")
    visual_canon.save(project, _canon(script))
    produce(lib, project, only=["bg", "ui"], image_provider="fake")
    return project, ctx, script


def test_check_asks_with_the_brief_and_the_canon(lib: Library, tmp_path: Path, fake: None, monkeypatch: pytest.MonkeyPatch) -> None:
    project, ctx, script = _made(lib, tmp_path)
    seen: list[TextCall] = []
    passing = visual_check.offline_judgement

    def judge(call: TextCall) -> dict[str, Any]:
        seen.append(call)
        return passing(call)

    monkeypatch.setattr(visual_check, "offline_judgement", judge)
    surveys: list[TextCall] = []
    monkeypatch.setattr(visual_check, "offline_survey", lambda call: (surveys.append(call), {
        "layout": "two rows of carriages side by side", "counts": ["trains: 2"], "beyond": "a castle city", "writing": "none"})[1])
    loc = script.locations[0]
    key = next(i.key for i in manifest(script, project.config, None) if i.group == "bg" and f"/bg/{loc.id}/" in i.key)
    verdict = visual_check.check(ctx, key)
    prompt = seen[0].prompt
    assert verdict.ok and verdict.asset_id and ctx.calls[-1]["task"] == f"visual.check.{key}"
    assert "THE BRIEF" in prompt and "Visual novel background" in prompt  # the item's own request description
    assert "- a second train or a parallel track" in prompt and "- green fields" in prompt  # canon and place never-lists
    assert "Layout, exactly: two carriages joined end to end" in prompt and "beautiful" in prompt and "`counts`" in prompt
    assert len(seen[0].images) == 1 and seen[0].images[0].exists()
    assert seen[0].system == visual_check.SYSTEM
    # a blind survey comes first: same image, no brief, no canon; the judge then gets it as evidence
    assert [c["task"] for c in ctx.calls[-2:]] == [f"visual.survey.{key}", f"visual.check.{key}"]
    assert surveys[0].images == seen[0].images[:1] and "THE BRIEF" not in surveys[0].prompt and "second train" not in surveys[0].prompt
    assert "A BLIND SURVEY OF IMAGE 1" in prompt and "two rows of carriages side by side" in prompt and "trains: 2" in prompt
    assert "trains: 2" in verdict.counts and "castle city" in verdict.survey
    # a bg_time is judged with its base image as the reference
    dep = next((i for i in manifest(script, project.config, None) if i.group == "bg_time"), None)
    if dep and by_key(lib, dep.request):
        visual_check.check(ctx, dep.key)
        assert len(seen[-1].images) == 2 and "Image 2 is the reference" in seen[-1].prompt


def failing(_: Any) -> dict[str, Any]:
    return {"seen": "two trains side by side", "counts": ["trains: 2"], "matches_brief": "partly",
            "violations": [{"rule": "one single train on one track", "what": "two trains on parallel tracks"}],
            "logic": [], "text_in_image": False, "note": "two trains"}  # fmt: skip


def test_verdict_rules() -> None:
    j = visual_check.Judgement.model_validate(failing(None))
    assert not visual_check.is_ok(j)
    clean = visual_check.Judgement.model_validate(visual_check.offline_judgement(None))
    assert visual_check.is_ok(clean)
    assert not visual_check.is_ok(clean.model_copy(update={"text_in_image": True}))
    assert not visual_check.is_ok(clean.model_copy(update={"logic": ["a bridge into nothing"]}))
    assert not visual_check.is_ok(clean.model_copy(update={"matches_brief": "partly"}))
    v = visual_check.Verdict(key="k", asset_id="a", matches_brief="partly", violations=j.violations)
    assert v.first_fault() == "one single train on one track: two trains on parallel tracks"
    text = visual_check.feedback([v])
    assert text.startswith("The previous attempt was rejected:") and "Paint it so that every one of these holds: one single train on one track." in text


def test_review_stores_skips_and_rechecks_a_remade_asset(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx, script = _made(lib, tmp_path)
    first = visual_check.review(ctx)
    made = [r for r in first["items"] if r["state"] == "checked"]
    assert made and first["ok"] == first["total"] == len(made) and not first["failed"]
    stored = json.loads((project.root / visual_check.FILE).read_text())["verdicts"]
    assert set(stored) == {r["key"] for r in made}
    again = visual_check.review(ctx)
    assert again["checked"] == 0 and all(r["state"] in ("checked_before", "not_made") for r in again["items"])
    # remake one background (archive + produce): its stored verdict judged another asset, so it is checked again
    from assetforge.core import review

    key = made[0]["key"]
    review.archive(lib, [made[0]["asset_id"]], note="test remake")
    item = next(i for i in manifest(script, project.config, None) if i.key == key)
    remade = visual_check._remake(ctx, item, "Brighter.", image_provider="fake", quality="auto")  # new bytes, same key
    assert remade != made[0]["asset_id"] and by_key(lib, item.request) == remade
    third = visual_check.review(ctx)
    rechecked = {r["key"] for r in third["items"] if r["state"] == "checked"}
    assert key in rechecked and third["checked"] == 1
    limited = visual_check.review(ctx, only_unchecked=False, limit=1)
    assert limited["checked"] == 1 and any(r["state"] == "skipped" for r in limited["items"])
    with pytest.raises(Exception):
        visual_check.review(ctx, groups=("music",))


def test_fix_archives_remakes_with_feedback_and_stops(lib: Library, tmp_path: Path, fake: None, monkeypatch: pytest.MonkeyPatch) -> None:
    project, ctx, script = _made(lib, tmp_path)
    monkeypatch.setattr(visual_check, "offline_judgement", failing)
    items = manifest(script, project.config, None)
    base = next(i for i in items if i.group == "bg" and any(d.group == "bg_time" and i.key in d.refs for d in items)) \
        if any(i.group == "bg_time" for i in items) else next(i for i in items if i.group == "bg")
    original = by_key(lib, base.request)
    visual_check.record(project, visual_check.check(ctx, base.key))
    out = visual_check.fix(ctx, base.key, max_rounds=2, image_provider="fake")
    assert out["state"] == "still_failing" and out["rounds"] == 2 and len(out["attempts"]) == 3
    assert lib.get_asset(original).status.value == "archived"
    kept = lib.get_asset(out["asset_id"])
    assert kept.status.value != "archived" and by_key(lib, base.request) == out["asset_id"]
    # the remake was requested with the violations as feedback
    remade = [lib.get_asset(a["asset_id"]) for a in out["attempts"][1:]]
    prompts = [json.dumps(jobs.get_job(lib, (a.metadata.get("generator") or {}).get("job_id")).request) for a in remade]
    assert "The previous attempt was rejected" in prompts[0] and "The previous 2 attempts were rejected" in prompts[1]
    assert all("one single train on one track" in p for p in prompts)
    assert len({a["asset_id"] for a in out["attempts"]}) == 3  # three different images, the best of them kept
    verdict = visual_check.stored(project, base.key)
    assert verdict.asset_id == out["asset_id"] and verdict.rounds == 2 and not verdict.ok
    if out["dependents"]:  # the other times of day were remade from the new base and checked
        assert all(d.get("asset_id") for d in out["dependents"])
    # a passing image is left alone
    monkeypatch.setattr(visual_check, "offline_judgement", lambda _: visual_check.Judgement(
        seen="x", matches_brief="yes", text_in_image=False).model_dump())  # fmt: skip
    other = next(i for i in items if i.key.endswith("/ui/textbox"))
    assert visual_check.fix(ctx, other.key, image_provider="fake")["state"] == "ok"


def test_app_routes(home: Path, fake: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from assetforge.app.api import router
    from assetforge.app.server import AppServer
    from assetforge.story.project import register

    from test_story_app import wait_task
    from test_ui_server import jcall

    with Library(home) as lib:
        project, _, _ = _made(lib, tmp_path)
        sid = register(home, project)
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        status, empty = jcall(srv, f"/api/stories/{sid}/visual-check")
        assert status == 200 and empty["verdicts"] == {} and empty["canon"]["state"] == "approved"
        _, run = jcall(srv, f"/api/stories/{sid}/run", method="POST", body={"action": "art_check"})
        task = wait_task(srv, run["task_id"])
        assert task["state"] == "done" and task["result"]["checked"] > 0, task
        _, data = jcall(srv, f"/api/stories/{sid}/visual-check")
        assert data["verdicts"] and all(v["current"] and v["ok"] for v in data["verdicts"].values())
        _, run = jcall(srv, f"/api/stories/{sid}/run", method="POST", body={"action": "visual_canon"})
        task = wait_task(srv, run["task_id"])
        assert task["state"] == "done" and task["result"]["canon"]["locations"], task
        _, run = jcall(srv, f"/api/stories/{sid}/run", method="POST", body={"action": "art_fix"})
        assert wait_task(srv, run["task_id"])["state"] == "failed"  # no key given
        key = next(iter(data["verdicts"]))
        _, run = jcall(srv, f"/api/stories/{sid}/run", method="POST", body={"action": "art_fix", "key": key, "image_provider": "fake"})
        task = wait_task(srv, run["task_id"])
        assert task["state"] == "done" and task["result"]["state"] == "ok", task
    finally:
        srv.shutdown()
        srv.server_close()
        srv.close_pool()
