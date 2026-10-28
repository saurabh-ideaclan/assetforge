from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile

from assetforge.audio import generate as gen_mod
from assetforge.audio.generate import AudioRequest, generate_audio, reproduce_audio, vary_audio
from assetforge.core import jobs
from assetforge.core.models import ImportOptions, JobStatus, Origin, Status
from assetforge.core.search import SearchQuery, search
from assetforge.errors import InvalidInputError

from conftest import make_wav


def test_generate_catalogs_a_reproducible_candidate(lib):
    result = generate_audio(lib, AudioRequest(recipe="cartoon_hit", preset="soft", variants=4, seed=42,
                                              duration=0.35, key="enemy/beetle/hit", project="panda-bamboo",
                                              style="cute-jungle", tags=["beetle"]))  # fmt: skip
    assert not result.reused and result.job_id
    assert [v.label for v in result.variants] == ["v1", "v2", "v3", "v4"]
    assert len({v.variant_id for v in result.variants}) == 4

    asset = lib.get_asset(result.asset_id)
    assert asset.origin == Origin.PROCEDURAL and asset.status == Status.CANDIDATE
    assert asset.commercial_use == "allowed"
    assert {"hit", "cartoon", "soft", "procedural", "beetle"} <= set(asset.tags)
    assert asset.key == "enemy/beetle/hit" and asset.style == "cute-jungle"
    generator = asset.metadata["generator"]
    assert generator["recipe"] == "cartoon_hit" and generator["seed"] == 42 and generator["preset"] == "soft"
    assert generator["base_params"]["duration"] == 0.35

    for v in result.variants:
        data, sr = soundfile.read(v.path, dtype="int16")
        assert sr == 44100 and data.ndim == 1
        assert v.qc["ok"]
        assert abs(len(data) / sr * 1000 - v.duration_ms) <= 1
    assert reproduce_audio(lib, result.asset_id)["ok"]
    assert jobs.get_job(lib, result.job_id).status == JobStatus.SUCCEEDED


def test_ogg_output_keeps_wav_masters(lib):
    result = generate_audio(lib, AudioRequest(recipe="coin", variants=2, seed=3, format="ogg"))
    asset = lib.get_asset(result.asset_id)
    assert sorted(v.label for v in asset.variants) == ["v1", "v1_m4a", "v1_ogg", "v2", "v2_m4a", "v2_ogg"]
    assert asset.primary_variant.label == "v1_ogg" and asset.path.endswith(".ogg")
    for v in result.variants:
        assert v.format == "ogg" and Path(v.path).suffix == ".ogg"
        info = soundfile.info(v.path)
        assert info.format == "OGG" and info.frames > 0
    masters = {v.id: v for v in asset.variants if v.label in ("v1", "v2")}
    assert {v.master_variant_id for v in result.variants} == set(masters)
    assert all(m.file.ext == ".wav" for m in masters.values())


def test_same_seeded_request_is_reused_not_regenerated(lib):
    req = AudioRequest(recipe="jump", variants=2, seed=11)
    first = generate_audio(lib, req)
    again = generate_audio(lib, req)
    assert again.reused and again.reuse_reason == "fingerprint"
    assert again.asset_id == first.asset_id and again.job_id == first.job_id
    assert lib.info()["counts"]["assets"] == 1

    # A noise-free recipe's default sound is the same whatever the seed, so it is reused...
    default = generate_audio(lib, AudioRequest(recipe="jump"))
    assert default.reused and default.reuse_reason == "identical_content"
    # ...unless the caller explicitly asks for a fresh take.
    fresh = [generate_audio(lib, AudioRequest(recipe="jump", new_variant=True)) for _ in range(2)]
    assert len({first.asset_id, fresh[0].asset_id, fresh[1].asset_id}) == 3
    assert fresh[0].variants[0].params != first.variants[0].params
    # Noisy recipes differ per seed even without --new-variant.
    booms = [generate_audio(lib, AudioRequest(recipe="explosion")) for _ in range(2)]
    assert booms[0].asset_id != booms[1].asset_id
    with pytest.raises(InvalidInputError):
        generate_audio(lib, AudioRequest(recipe="jump", new_variant=True, variation=0))


def test_identical_sound_from_a_different_request_tops_up_variants(lib):
    one = generate_audio(lib, AudioRequest(recipe="laser", seed=8, variants=1))
    three = generate_audio(lib, AudioRequest(recipe="laser", seed=8, variants=3))
    assert three.reused and three.reuse_reason == "identical_content"
    assert three.asset_id == one.asset_id
    assert [v.label for v in three.variants] == ["v1", "v2", "v3"]


def test_vary_adds_new_variants_to_the_same_asset(lib):
    base = generate_audio(lib, AudioRequest(recipe="pickup", variants=2, seed=1, format="ogg"))
    more = vary_audio(lib, base.asset_id, variants=3, seed=99, variation=0.5)
    assert more.asset_id == base.asset_id
    assert [v.index for v in more.variants] == [3, 4, 5]
    assert all(v.format == "ogg" for v in more.variants)
    hashes = {v.params["pcm_sha256"] for v in lib.get_asset(base.asset_id).variants if "pcm_sha256" in v.params}
    assert len(hashes) == 5
    assert reproduce_audio(lib, base.asset_id)["ok"]
    assert reproduce_audio(lib, more.variants[0].variant_id)["variants"][0]["label"] == "v3"


def test_generate_from_existing_sound_inherits_and_links(lib):
    source = generate_audio(lib, AudioRequest(recipe="explosion", seed=4, params={"size": 0.9, "debris": 0.8}))
    derived = generate_audio(lib, AudioRequest(params={"size": 0.3}, seed=5), from_ref=source.asset_id)
    gen = lib.get_asset(derived.asset_id).metadata["generator"]
    assert gen["recipe"] == "explosion"
    assert gen["base_params"]["size"] == 0.3 and gen["base_params"]["debris"] == 0.8
    assert gen["derived_from"] == source.asset_id
    lineage = lib.get_asset(derived.asset_id).lineage
    assert (lineage[0].relation, lineage[0].asset_id) == ("derived_from", source.asset_id)


def test_reproduce_detects_drift(lib):
    result = generate_audio(lib, AudioRequest(recipe="ui_click", seed=2))
    lib.conn.execute(
        "UPDATE asset_variants SET params = json_set(params, '$.pcm_sha256', 'bogus') WHERE id = ?",
        (result.variants[0].master_variant_id,),
    )
    report = reproduce_audio(lib, result.asset_id)
    assert not report["ok"] and not report["variants"][0]["match"]


def test_failures_are_recorded_on_the_job(lib, monkeypatch):
    monkeypatch.setattr(gen_mod, "render_recipe", lambda recipe, params, sr, seed: np.zeros(1000))
    with pytest.raises(InvalidInputError, match="failed QC"):
        generate_audio(lib, AudioRequest(recipe="coin", seed=1))
    [job] = jobs.list_jobs(lib)
    assert job.status == JobStatus.FAILED and "silent" in job.error["message"]
    assert lib.info()["counts"]["assets"] == 0


def test_request_validation(lib):
    with pytest.raises(InvalidInputError):
        generate_audio(lib, AudioRequest())
    with pytest.raises(InvalidInputError):
        generate_audio(lib, AudioRequest(recipe="coin", params={"bogus": 1}))
    with pytest.raises(ValueError):
        AudioRequest(recipe="coin", sample_rate=12345)
    with pytest.raises(ValueError):
        AudioRequest(recipe="coin", variants=100)


def test_vary_and_from_reject_non_procedural_assets(lib, tmp_path):
    imported = lib.import_paths([make_wav(tmp_path / "x.wav")], ImportOptions()).items[0].asset_id
    with pytest.raises(InvalidInputError):
        vary_audio(lib, imported)
    with pytest.raises(InvalidInputError):
        generate_audio(lib, AudioRequest(seed=1), from_ref=imported)


def test_generated_sounds_are_found_by_search(lib):
    coin = generate_audio(lib, AudioRequest(recipe="coin", preset="soft", seed=1))
    generate_audio(lib, AudioRequest(recipe="explosion", seed=1))
    hits = search(lib, SearchQuery(text="soft cartoon coin collection sfx")).results
    assert hits[0].id == coin.asset_id
