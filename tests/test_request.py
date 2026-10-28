"""`request`: reuse ladder, generation, budgets and provider limits."""

from __future__ import annotations

import threading

import pytest

from assetforge.config import Settings
from assetforge.core import budget, jobs, limits, settings_store
from assetforge.core.models import JobStatus, Status
from assetforge.errors import BudgetExceededError, InvalidInputError, NotFoundError
from assetforge.image import engine, providers
from assetforge.image.providers.fake import FakeProvider
from assetforge.request import AssetRequest, handle, infer_recipe


@pytest.fixture
def fake_openai(monkeypatch):
    provider = FakeProvider(Settings({}), name="openai", cost_per_image=0.05)
    providers.OVERRIDES["openai"] = provider
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    return provider


SPEC = {
    "project": "panda-bamboo", "key": "enemy/beetle/hit", "type": "sfx",
    "description": "Short soft crunchy cartoon insect hit", "style": "cute-jungle", "duration_ms": 350,
    "variants": 4, "reuse_policy": "prefer_existing", "format": "ogg",
}  # fmt: skip


def test_recipe_inference():
    assert infer_recipe("Short soft crunchy cartoon insect hit").name == "cartoon_hit"
    assert infer_recipe("collect a gold coin").name == "coin"
    assert infer_recipe("big bomb explosion").name == "explosion"
    assert infer_recipe("menu button click").name == "ui_click"
    with pytest.raises(InvalidInputError):
        infer_recipe("zzz qqq")


def test_spec_example_generates_then_reuses_by_key(lib):
    first = handle(lib, AssetRequest(**SPEC))
    assert first.state == "ready" and not first.reused and len(first.variants) == 4
    assert all(v["format"] == "ogg" and v["path"].endswith(".ogg") for v in first.variants)
    asset = lib.get_asset(first.asset_id)
    gen = asset.metadata["generator"]
    assert (gen["recipe"], gen["preset"]) == ("cartoon_hit", "soft")
    assert asset.key == "enemy/beetle/hit" and asset.style == "cute-jungle" and asset.status == Status.CANDIDATE
    assert [(u.project, u.usage_key) for u in asset.usage] == [("panda-bamboo", "enemy/beetle/hit")]
    assert jobs.get_job(lib, first.job_id).status == JobStatus.SUCCEEDED

    again = handle(lib, AssetRequest(**SPEC))
    assert again.reused and again.reuse_reason == "key" and again.asset_id == first.asset_id
    assert lib.info()["counts"]["assets"] == 1


def test_identical_request_and_search_reuse(lib):
    spec = {**SPEC, "key": None, "project": None}
    first = handle(lib, AssetRequest(**spec))
    again = handle(lib, AssetRequest(**spec))
    assert again.reused and again.reuse_reason == "request" and again.asset_id == first.asset_id

    other = {"type": "sfx", "description": "soft cartoon insect hit", "style": "cute-jungle"}
    fresh = handle(lib, AssetRequest(**other))  # the first sound is only a candidate: not reusable yet
    assert not fresh.reused
    lib.set_status([first.asset_id], Status.APPROVED)
    reused = handle(lib, AssetRequest(**{**other, "project": "space-cats", "key": "sfx/hit"}))
    assert reused.reused and reused.reuse_reason == "search" and reused.asset_id == first.asset_id
    assert reused.match["coverage"] >= 0.7
    assert ("space-cats", "sfx/hit") in [(u.project, u.usage_key) for u in lib.get_asset(first.asset_id).usage]


def test_policies(lib):
    with pytest.raises(NotFoundError) as exc:
        handle(lib, AssetRequest(type="sfx", description="laser zap", reuse_policy="require_existing"))
    assert exc.value.details["closest"] == [] and exc.value.hint
    a = handle(lib, AssetRequest(type="sfx", description="laser zap", seed=1))
    lib.set_status([a.asset_id], Status.APPROVED)
    b = handle(lib, AssetRequest(type="sfx", description="laser zap", reuse_policy="always_generate", new_variant=True))
    assert not b.reused and b.asset_id != a.asset_id
    with pytest.raises(InvalidInputError) as exc:  # music and ambience are generated now; speech is not
        handle(lib, AssetRequest(type="voice", description="narrator says hello"))
    assert "music" in exc.value.details["generatable"]


def test_audio_post_processing(lib):
    result = handle(lib, AssetRequest(type="sfx", description="coin pickup", process="web-sfx", format="wav"))
    asset = lib.get_asset(result.asset_id)
    assert asset.path.endswith(".ogg") and result.variants[0]["format"] == "ogg"
    assert asset.primary_variant.params["ops"][-1] == {"op": "format", "format": "ogg", "quality": 0.6}


def test_image_requests(lib, fake_openai):
    one = handle(lib, AssetRequest(type="character", description="cute beetle enemy", provider="openai",
                                   project="panda-bamboo", key="enemy/beetle", process="web-sprite"))  # fmt: skip
    assert one.state == "ready" and one.variants[0]["path"].endswith(".webp")
    assert one.generated["provider"] == "openai" and one.generated["cost_usd"] == pytest.approx(0.05)
    assert lib.get_asset(one.asset_id).key == "enemy/beetle"

    many = handle(lib, AssetRequest(type="icon", description="golden coin icon", provider="openai", n=3))
    assert many.state == "needs_review" and len(many.candidates) == 3

    lib.set_status([one.asset_id], Status.APPROVED)
    reused = handle(lib, AssetRequest(type="sprite", description="beetle enemy", provider="openai"))
    assert reused.reused and reused.reuse_reason == "search" and reused.asset_id == one.asset_id
    assert len(fake_openai.calls) == 2


def test_budgets_block_spending_atomically(lib, fake_openai):
    settings_store.set_value(lib, "budget.daily_usd", 0.12)
    handle(lib, AssetRequest(type="sprite", description="red gem", provider="openai"))
    handle(lib, AssetRequest(type="sprite", description="blue gem", provider="openai"))
    with pytest.raises(BudgetExceededError) as exc:
        handle(lib, AssetRequest(type="sprite", description="green gem", provider="openai"))
    assert exc.value.code == "BUDGET_EXCEEDED" and exc.value.details["period"] == "daily"
    assert len(fake_openai.calls) == 2
    summary = budget.summary(lib)
    assert summary["daily"]["spent_usd"] == pytest.approx(0.10) and summary["daily"]["remaining_usd"] == pytest.approx(0.02)
    failed = [j for j in jobs.list_jobs(lib) if j.status == JobStatus.FAILED]
    assert {j.kind for j in failed} == {"image", "request"}

    with pytest.raises(BudgetExceededError):
        handle(lib, AssetRequest(type="sprite", description="tiny gem", provider="openai", max_cost=0.01))


def test_concurrent_reservations_cannot_overshoot(lib, home):
    from assetforge.core.catalog import Library

    settings_store.set_value(lib, "budget.daily_usd", 1.0)
    ids = [jobs.create_job(lib, "image", {}, provider="openai") for _ in range(6)]
    outcomes: list[str] = []

    def reserve(job_id: str) -> None:
        with Library(home) as own:
            try:
                budget.reserve(own, job_id, 0.3)
                outcomes.append("ok")
            except BudgetExceededError:
                outcomes.append("blocked")

    threads = [threading.Thread(target=reserve, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 3 and outcomes.count("blocked") == 3


def test_provider_slots_and_rate_tokens(lib, home, monkeypatch):
    from assetforge.core.catalog import Library

    settings_store.set_value(lib, "limits.openai.max_concurrent", 1)
    order: list[str] = []
    holding = threading.Event()

    def holder() -> None:
        with Library(home) as own, limits.provider_slot(own, "openai"):
            order.append("a-start")
            holding.set()
            threading.Event().wait(0.4)
            order.append("a-end")

    t = threading.Thread(target=holder)
    t.start()
    holding.wait()
    with limits.provider_slot(lib, "openai"):
        order.append("b-start")
    t.join()
    assert order == ["a-start", "a-end", "b-start"]

    now = [1000.0]
    waits: list[float] = []
    monkeypatch.setattr(limits, "clock", lambda: now[0])
    monkeypatch.setattr(limits, "sleep", lambda s: (waits.append(s), now.__setitem__(0, now[0] + s)))
    settings_store.set_value(lib, "limits.gemini.rpm", 6)
    for _ in range(6):
        assert limits.take_token(lib, "gemini") == 0
    waited = limits.take_token(lib, "gemini")
    assert waited == pytest.approx(10.0, abs=0.3)  # 6/min = one token every 10 s


def test_settings_validation(lib):
    assert settings_store.get(lib, "limits.openai.rpm") == 30
    settings_store.set_value(lib, "limits.default.rpm", 12)
    assert settings_store.get(lib, "limits.openai.rpm") == 12
    for key, value in (("budget.daily_usd", "-1"), ("nope.key", "1"), ("limits.openai.rpm", "fast")):
        with pytest.raises(InvalidInputError):
            settings_store.set_value(lib, key, value)
