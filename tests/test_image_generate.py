"""Image engine end to end with offline fake providers standing in for OpenAI and Gemini."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from assetforge.config import Settings
from assetforge.core import jobs, styles
from assetforge.core.models import ImportOptions, JobStatus, Origin, Status
from assetforge.core.search import SearchQuery, search
from assetforge.errors import InvalidInputError, ProviderError
from assetforge.image import engine, providers
from assetforge.image.edit import EditRequest, edit_image
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.providers.fake import FakeProvider
from assetforge.image.resume import resume_image_job

from conftest import make_png

SETTINGS = Settings({})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(engine, "sleep", waits.append)
    return waits


@pytest.fixture
def openai_fake():
    provider = FakeProvider(SETTINGS, name="openai", native_transparency=True)
    providers.OVERRIDES["openai"] = provider
    return provider


@pytest.fixture
def gemini_fake():
    provider = FakeProvider(SETTINGS, name="gemini", native_transparency=False, max_references=3)
    providers.OVERRIDES["gemini"] = provider
    return provider


def gen(lib, **kwargs):
    kwargs.setdefault("provider", "openai")
    return generate_image(lib, ImageRequest(**kwargs), SETTINGS)


def test_character_sprite_with_native_transparency(lib, openai_fake, gemini_fake):
    result = gen(lib, prompt="Cute cartoon beetle, front view", type="character", style="cute-jungle",
                 project="panda-bamboo", key="enemy/beetle")  # fmt: skip
    assert not result.reused and result.provider == "openai" and result.transparency == "native"
    [candidate] = result.assets
    assert candidate.has_alpha and candidate.qc["ok"] and candidate.width == 160 and candidate.height == 256
    assert "Art style: cute jungle." in result.prompt

    asset = lib.get_asset(candidate.asset_id)
    assert asset.type == "character" and asset.origin == Origin.AI and asset.status == Status.CANDIDATE
    assert asset.style == "cute-jungle" and asset.key == "enemy/beetle" and asset.project == "panda-bamboo"
    assert asset.name == "cute_cartoon_beetle_front_view"
    assert {"character", "cute-jungle", "ai-generated", "openai"} <= set(asset.tags)
    gen_meta = asset.metadata["generator"]
    assert gen_meta["prompt"] == "Cute cartoon beetle, front view" and gen_meta["final_prompt"] == result.prompt
    assert gen_meta["provider"] == "openai" and gen_meta["job_id"] == result.job_id
    assert openai_fake.calls[0].transparent is True and openai_fake.calls[0].aspect == "2:3"
    assert gemini_fake.calls == []

    job = jobs.get_job(lib, result.job_id)
    assert job.status == JobStatus.SUCCEEDED and job.provider == "openai" and job.result["asset_ids"] == [asset.id]
    saved = engine.job_dir(lib, result.job_id)
    assert (saved / "output_1.png").is_file() and json.loads((saved / "response.json").read_text())["prompt"]


def test_keyed_transparency_keeps_the_original(lib, gemini_fake):
    result = gen(lib, prompt="bamboo leaf", type="sprite", provider="gemini")
    assert result.transparency == "keyed" and "solid pure magenta (#FF00FF)" in result.prompt
    asset = lib.get_asset(result.assets[0].asset_id)
    assert [v.label for v in asset.variants] == ["original", "transparent"]
    assert asset.primary_variant.label == "transparent"
    original = Image.open(asset.variants[0].file.path).convert("RGBA")
    keyed = Image.open(asset.path)
    assert np.asarray(original)[0, 0, 3] == 255 and np.asarray(keyed)[0, 0, 3] == 0
    assert asset.variants[1].parent_variant_id == asset.variants[0].id
    assert result.assets[0].qc["ok"] and result.assets[0].original_variant_id == asset.variants[0].id


def test_backgrounds_are_opaque_and_wide(lib, openai_fake):
    result = gen(lib, prompt="Misty jungle clearing at dawn", type="background")
    assert result.transparency == "none" and openai_fake.calls[0].aspect == "16:9"
    assert result.assets[0].width > result.assets[0].height and not result.assets[0].has_alpha


def test_candidates_and_key_rules(lib, openai_fake):
    result = gen(lib, prompt="golden coin icon", type="icon", n=3, collection="ui-icons")
    names = [lib.get_asset(a.asset_id).name for a in result.assets]
    assert names == ["golden_coin_icon_1", "golden_coin_icon_2", "golden_coin_icon_3"]
    assert lib.get_asset(result.assets[0].asset_id).collections == ["ui-icons"]
    with pytest.raises(InvalidInputError):
        gen(lib, prompt="golden coin icon", type="icon", n=2, key="ui/coin")


def test_same_request_is_reused_unless_new_variant(lib, openai_fake):
    first = gen(lib, prompt="red potion bottle", type="sprite")
    again = gen(lib, prompt="red potion bottle", type="sprite")
    assert again.reused and again.job_id == first.job_id and again.assets[0].asset_id == first.assets[0].asset_id
    assert len(openai_fake.calls) == 1
    fresh = gen(lib, prompt="red potion bottle", type="sprite", new_variant=True, seed=2)
    assert not fresh.reused and fresh.assets[0].asset_id != first.assets[0].asset_id


def test_style_profile_adds_palette_instructions_and_references(lib, openai_fake, tmp_path):
    ref = lib.import_paths([make_png(tmp_path / "style_ref.png", (32, 32))], ImportOptions()).items[0].asset_id
    styles.create_style(lib, "cute-jungle", description="chunky cartoon, thick dark outlines",
                        palette=["#4caf50", "FFEB3B"], instructions="Soft cel shading", references=[ref])  # fmt: skip
    user_ref = make_png(tmp_path / "beetle_sketch.png", (32, 32), (1, 2, 3, 255))
    result = gen(lib, prompt="beetle enemy", type="character", style="cute-jungle", references=[str(user_ref)])
    call = openai_fake.calls[0]
    assert "chunky cartoon, thick dark outlines" in call.prompt and "#4CAF50, #FFEB3B" in call.prompt
    assert [p.name for p in call.images] == ["beetle_sketch.png", Path(lib.get_asset(ref).path).name]
    asset = lib.get_asset(result.assets[0].asset_id)
    assert [(link.relation, link.asset_id) for link in asset.lineage] == [("derived_from", ref)]
    roles = [r["role"] for r in asset.metadata["generator"]["references"]]
    assert roles == ["user", "style"]


def test_retries_then_success(lib, openai_fake, no_sleep):
    openai_fake.fail_plan = ["rate_limited", "unavailable"]
    result = gen(lib, prompt="treasure chest", type="sprite", fallback=False)
    assert len(result.assets) == 1 and len(no_sleep) == 2
    assert [a["code"] for a in result.attempts] == ["PROVIDER_RATE_LIMITED", "PROVIDER_UNAVAILABLE"]
    assert jobs.get_job(lib, result.job_id).attempts == 3


def test_fallback_to_the_next_provider(lib, openai_fake, gemini_fake):
    openai_fake.fail_plan = ["unavailable"] * 3
    result = gen(lib, prompt="wooden crate", type="sprite", retries=0)
    assert result.provider == "gemini" and result.transparency == "keyed"
    assert result.attempts[0]["provider"] == "openai"
    assert jobs.get_job(lib, result.job_id).provider == "gemini"


def test_content_refusals_do_not_fall_back_and_fail_the_job(lib, openai_fake, gemini_fake):
    openai_fake.fail_plan = ["content"]
    with pytest.raises(ProviderError) as exc:
        gen(lib, prompt="something disallowed", type="sprite")
    assert exc.value.code == "CONTENT_REJECTED" and gemini_fake.calls == []
    [job] = jobs.list_jobs(lib)
    assert job.status == JobStatus.FAILED and job.error["code"] == "CONTENT_REJECTED"
    assert lib.info()["counts"]["assets"] == 0


def test_too_many_references_for_a_provider_falls_back(lib, gemini_fake, openai_fake, tmp_path):
    refs = [str(make_png(tmp_path / f"r{i}.png", color=(i, i, i, 255))) for i in range(4)]
    result = gen(lib, prompt="hero", type="character", provider="gemini", references=refs)
    assert result.provider == "openai" and result.attempts[0]["code"] == "PROVIDER_UNSUPPORTED"


def test_webp_output_adds_a_primary_webp_variant(lib, openai_fake):
    result = gen(lib, prompt="star icon", type="icon", format="webp")
    asset = lib.get_asset(result.assets[0].asset_id)
    assert [v.label for v in asset.variants] == ["original", "webp"]
    assert asset.path.endswith(".webp") and result.assets[0].path == asset.path


def test_edit_creates_a_linked_asset_and_leaves_the_original(lib, openai_fake, gemini_fake):
    source = gen(lib, prompt="blue slime monster", type="character").assets[0]
    before = Path(source.path).read_bytes()
    edited = edit_image(lib, EditRequest(source=source.asset_id, prompt="make it red", provider="gemini"), SETTINGS)
    assert edited.transparency == "keyed"  # source has alpha, so the edit keeps it
    call = gemini_fake.calls[0]
    assert call.operation == "edit" and call.images[0].name.startswith("flattened-")
    new = lib.get_asset(edited.assets[0].asset_id)
    assert new.id != source.asset_id and new.name == "blue_slime_monster_edit"
    assert "edited" in new.tags and new.type == "character"
    assert [(link.relation, link.asset_id) for link in new.lineage] == [("edited_from", source.asset_id)]
    assert Path(source.path).read_bytes() == before

    as_variant = edit_image(lib, EditRequest(source=source.asset_id, prompt="add a hat", as_variant=True,
                                             provider="openai"), SETTINGS)  # fmt: skip
    assert as_variant.assets[0].asset_id == source.asset_id
    labels = [v.label for v in lib.get_asset(source.asset_id).variants]
    assert labels == ["original", "edit_1"]
    assert lib.get_asset(source.asset_id).primary_variant.label == "original"


def test_mask_requires_a_capable_provider(lib, gemini_fake, openai_fake, tmp_path):
    source = make_png(tmp_path / "src.png", (64, 64))
    mask = make_png(tmp_path / "mask.png", (64, 64), (0, 0, 0, 0))
    gemini_fake.capabilities = lambda model: openai_fake.capabilities(model).__class__(  # type: ignore[method-assign]
        native_transparency=False, max_references=3, supports_mask=False, supports_seed=True, max_n_per_call=1,
        aspects=("1:1",),
    )  # fmt: skip
    result = edit_image(lib, EditRequest(source=str(source), prompt="fix the hand", mask=str(mask),
                                         provider="gemini"), SETTINGS)  # fmt: skip
    assert result.provider == "openai" and openai_fake.calls[0].mask is not None


def test_resume_after_crash_catalogs_saved_outputs_without_paying_again(lib, openai_fake, monkeypatch):
    real_finalize = engine.finalize
    monkeypatch.setattr(engine, "finalize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk on fire")))
    with pytest.raises(RuntimeError):
        gen(lib, prompt="magic wand", type="sprite")
    [job] = jobs.list_jobs(lib)
    assert job.status == JobStatus.FAILED and job.error["outputs_saved"] is True
    assert lib.info()["counts"]["assets"] == 0

    monkeypatch.setattr(engine, "finalize", real_finalize)
    result = resume_image_job(lib, job.id, SETTINGS)
    assert len(result.assets) == 1 and len(openai_fake.calls) == 1
    assert jobs.get_job(lib, job.id).status == JobStatus.SUCCEEDED
    assert resume_image_job(lib, job.id, SETTINGS).reused  # finished jobs just report their result


def test_resume_replays_a_request_that_never_got_outputs(lib, openai_fake):
    openai_fake.fail_plan = ["unavailable"] * 3
    with pytest.raises(ProviderError):
        gen(lib, prompt="spiky shell", type="sprite", fallback=False)
    [job] = jobs.list_jobs(lib)
    result = resume_image_job(lib, job.id, SETTINGS)
    assert result.job_id == job.id and len(result.assets) == 1


def test_dry_run_calls_nothing(lib, openai_fake):
    result = gen(lib, prompt="lava tile", type="tile", dry_run=True)
    assert result.dry_run and "seamless tileable" in result.prompt and result.job_id is None
    assert openai_fake.calls == [] and jobs.list_jobs(lib) == []


def test_generated_images_are_searchable(lib, openai_fake):
    sprite = gen(lib, prompt="Cute cartoon beetle, front view", type="character", style="cute-jungle")
    gen(lib, prompt="jungle background", type="background")
    hits = search(lib, SearchQuery(text="cartoon beetle", style="cute-jungle")).results
    assert hits[0].id == sprite.assets[0].asset_id


def test_out_of_credits_falls_back_without_retrying(lib, openai_fake, gemini_fake, no_sleep):
    openai_fake.fail_plan = ["quota"]
    result = gen(lib, prompt="beetle", type="character")
    assert result.provider == "gemini" and no_sleep == [] and [a["code"] for a in result.attempts] == ["PROVIDER_QUOTA"]
