"""Agent-made assets: SVG (vector master + PNG render), procedural script output, `methods`, `request --method`."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from assetforge.add import AddRequest, add_file
from assetforge.core.models import AssetType, Origin
from assetforge.core.search import SearchQuery, search
from assetforge.errors import InvalidInputError, NotFoundError
from assetforge.request import AssetRequest, handle

from conftest import run_cli

HEART = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 21s-8-5.2-8-11a4.5 4.5 0 0 1 '
         '8-2.8A4.5 4.5 0 0 1 20 10c0 5.8-8 11-8 11z" fill="#e53935"/></svg>')  # fmt: skip


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_svg_is_kept_as_master_and_rendered_to_png(lib, tmp_path):
    result = add_file(lib, AddRequest(path=str(write(tmp_path / "heart.svg", HEART)), method="svg", type="icon",
                                      description="red heart health icon", size="128", project="castle"))  # fmt: skip
    asset = lib.get_asset(result.asset_id)
    labels = {v.label: v for v in asset.variants}
    assert set(labels) == {"svg", "png"} and asset.primary_variant.label == "png"
    assert labels["svg"].file.kind == "vector" and labels["png"].parent_variant_id == labels["svg"].id
    png = labels["png"].file
    assert (png.width, png.height) == (128, 128) and png.has_alpha and png.colors[0]["name"] == "red"
    assert asset.origin == Origin.PROCEDURAL and "svg" in asset.tags and result.qc["ok"]
    hits = search(lib, SearchQuery(text="red heart icon transparent", all_terms=True)).results
    assert [h.id for h in hits] == [asset.id]
    # adding the same file again returns the existing asset
    assert not add_file(lib, AddRequest(path=str(tmp_path / "heart.svg"), method="svg", type="icon")).created


def test_procedural_output_keeps_the_script(lib, tmp_path):
    script = write(tmp_path / "noise.py", "from PIL import Image\nImage.effect_noise((64, 64), 40).save('n.png')\n")
    Image.effect_noise((64, 64), 40).convert("RGB").save(tmp_path / "noise.png")
    result = add_file(lib, AddRequest(path=str(tmp_path / "noise.png"), method="procedural", type="texture",
                                      script=str(script), description="grey static noise"))  # fmt: skip
    gen = lib.get_asset(result.asset_id).metadata["generator"]
    assert gen["method"] == "procedural" and "effect_noise" in gen["script"] and gen["script_name"] == "noise.py"
    with pytest.raises(InvalidInputError):
        add_file(lib, AddRequest(path=str(tmp_path / "noise.png"), method="svg"))
    with pytest.raises(NotFoundError):
        add_file(lib, AddRequest(path=str(tmp_path / "missing.png")))


def test_request_registers_agent_made_files_after_the_reuse_check(lib, tmp_path):
    svg = write(tmp_path / "gem.svg", HEART.replace("#e53935", "#1e88e5"))
    with pytest.raises(InvalidInputError):
        handle(lib, AssetRequest(type="icon", description="blue gem icon", method="svg"))
    plan = handle(lib, AssetRequest(type="icon", description="blue gem icon", method="svg", file=str(svg), dry_run=True))
    assert plan.would == "register" and plan.plan["cost_usd"] == 0
    made = handle(lib, AssetRequest(type="icon", description="blue gem icon", method="svg", file=str(svg),
                                    project="castle"))  # fmt: skip
    assert made.generated["kind"] == "svg" and made.variants[0]["format"] == "png"
    again = handle(lib, AssetRequest(type="icon", description="blue gem icon", accept_candidates=True, method="svg",
                                     file=str(svg)))  # fmt: skip
    assert again.reused and again.asset_id == made.asset_id


def test_cli_add_and_methods(capsys, home, tmp_path):
    svg = write(tmp_path / "arrow.svg", HEART)
    code, out, _ = run_cli(capsys, "add", str(svg), "--method", "svg", "--type", "ui", "-d", "arrow", "--json")
    assert code == 0 and out["created"] and out["master_variant_id"]
    code, out, _ = run_cli(capsys, "methods", "--json")
    names = [m["name"] for m in out["methods"]]
    assert code == 0 and {"ai_image", "svg", "procedural_image", "sprite_slice", "audio_procedural"} <= set(names)
    assert all(m["limits"] for m in out["methods"])
