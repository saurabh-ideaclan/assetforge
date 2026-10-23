"""The desktop UI: browse/facets, and the local HTTP server's security, media and generation endpoints."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from assetforge.app.api import router
from assetforge.app.server import AppServer
from assetforge.config import Settings
from assetforge.core.browse import BrowseFilters, browse, facets
from assetforge.core.models import ImportOptions, Status
from assetforge.errors import InvalidInputError
from assetforge.image import engine, providers
from assetforge.image.providers.fake import FakeProvider

from conftest import make_png, make_wav


@pytest.fixture
def stocked(lib, tmp_path):
    ids = {}
    ids["hero"] = lib.import_paths([make_png(tmp_path / "hero.png", (64, 64))], ImportOptions(
        type="character", project="panda-bamboo", style="cute-jungle", tags=["player"])).items[0].asset_id
    ids["tree"] = lib.import_paths([make_png(tmp_path / "tree.png", (32, 48), (10, 90, 20, 255))], ImportOptions(
        type="sprite", status=Status.CANDIDATE, collection="forest")).items[0].asset_id
    ids["coin"] = lib.import_paths([make_wav(tmp_path / "coin.wav", 300)], ImportOptions(type="sfx")).items[0].asset_id
    lib.set_status([ids["tree"]], Status.REJECTED)
    return ids


# ---------------------------------------------------------------------- browse


def test_browse_filters_sorting_and_pagination(lib, stocked):
    total, items = browse(lib, BrowseFilters())
    assert total == 3 and [a.id for a in items] == [stocked["coin"], stocked["tree"], stocked["hero"]]
    assert browse(lib, BrowseFilters(statuses=["rejected"]))[0] == 1  # browsing shows rejected too
    assert [a.id for a in browse(lib, BrowseFilters(kind="audio"))[1]] == [stocked["coin"]]
    assert [a.id for a in browse(lib, BrowseFilters(project="Panda Bamboo"))[1]] == [stocked["hero"]]
    assert [a.id for a in browse(lib, BrowseFilters(collection="forest"))[1]] == [stocked["tree"]]
    assert [a.id for a in browse(lib, BrowseFilters(tag="player"))[1]] == [stocked["hero"]]
    assert [a.name for a in browse(lib, BrowseFilters(), sort="name")[1]] == ["coin", "hero", "tree"]
    total, page = browse(lib, BrowseFilters(), sort="name", offset=1, limit=1)
    assert total == 3 and [a.name for a in page] == ["hero"]
    total, hits = browse(lib, BrowseFilters(q="tree"))
    assert total == 1 and hits[0].id == stocked["tree"]  # text search includes rejected when browsing
    assert browse(lib, BrowseFilters(q="tree", types=["character"]))[0] == 0
    with pytest.raises(InvalidInputError):
        browse(lib, BrowseFilters(), sort="random")


def test_facets(lib, stocked):
    f = facets(lib)
    assert {x["value"] for x in f["types"]} == {"character", "sprite", "sfx"}
    assert {x["value"]: x["count"] for x in f["kinds"]} == {"image": 2, "audio": 1}
    assert f["collections"] == [{"value": "forest", "count": 1}]
    assert {"value": "player", "count": 1} in f["tags"]


# ---------------------------------------------------------------------- server


@pytest.fixture
def server(home, stocked):
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def call(srv, path, *, method="GET", body=None, headers=None, token=True):
    h = {"X-AssetForge-Token": "secret"} if token else {}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jcall(srv, path, **kw):
    status, _, raw = call(srv, path, **kw)
    return status, json.loads(raw)


def test_auth_host_and_static(server):
    assert call(server, "/api/overview", token=False)[0] == 403
    assert call(server, "/api/overview", headers={"Host": "attacker.example"})[0] == 403
    status, headers, body = call(server, "/?token=secret", token=False)
    assert status == 200 and b"AssetForge" in body and headers["Set-Cookie"].startswith("af_token=secret")
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    cookie = {"Cookie": "af_token=secret"}
    assert call(server, "/static/app.js", token=False, headers=cookie)[1]["Content-Type"].startswith("text/javascript")
    assert call(server, "/static/../server.py", token=False, headers=cookie)[0] in (400, 403, 404)
    assert jcall(server, "/api/nope")[0] == 404


def test_overview_assets_detail_and_facets(server, stocked, monkeypatch, tmp_path):
    status, d = jcall(server, "/api/overview?tz=Asia/Kolkata")
    assert status == 200 and d["counts"]["assets"] == 3 and d["by_status"] == {"approved": 2, "rejected": 1}
    assert len(d["newest"]) == 3 and d["spend"]["totals"]["calls"] == 0 and "monthly" in d["budget"]
    assert d["library"]["is_default"] is True  # the library agents get from $ASSETFORGE_HOME
    monkeypatch.setenv("ASSETFORGE_HOME", str(tmp_path / "agents-library"))
    lib_info = jcall(server, "/api/overview")[1]["library"]
    assert lib_info["is_default"] is False and lib_info["default_home"] == str((tmp_path / "agents-library").resolve())
    status, d = jcall(server, "/api/assets?type=character,sprite&sort=name&limit=1")
    assert d["total"] == 2 and [a["name"] for a in d["items"]] == ["hero"]
    status, d = jcall(server, f"/api/assets/{stocked['hero']}")
    assert d["asset"]["project"] == "panda-bamboo" and d["lineage"]["asset_id"] == stocked["hero"] and d["job"] is None
    assert jcall(server, "/api/assets/ast_99999")[1]["error"]["code"] == "NOT_FOUND"
    assert jcall(server, "/api/assets?sort=random")[0] == 400
    assert "tags" in jcall(server, "/api/facets")[1]["facets"]
    assert jcall(server, "/api/report?from=2026-10-01&to=2026-10-02")[1]["report"]["totals"]["calls"] == 0
    assert jcall(server, "/api/report?range=fortnight")[0] == 400


def test_media_ranges_thumbs_and_reveal(server, stocked, lib, monkeypatch):
    coin = lib.get_asset(stocked["coin"]).primary_variant
    status, headers, body = call(server, f"/media/variant/{coin.id}", headers={"Range": "bytes=10-19"})
    assert status == 206 and headers["Content-Range"] == f"bytes 10-19/{coin.file.size_bytes}" and len(body) == 10
    assert headers["Content-Type"].startswith("audio/")
    status, headers, body = call(server, f"/media/variant/{coin.id}", headers={"Range": "bytes=-5"})
    assert status == 206 and len(body) == 5
    assert call(server, f"/media/variant/{coin.id}", headers={"Range": "bytes=999999-"})[0] == 416
    status, headers, body = call(server, f"/media/thumb/{coin.id}?size=128")
    assert status == 200 and headers["Content-Type"] == "image/png" and body[:4] == b"\x89PNG"
    assert call(server, f"/media/thumb/{coin.id}?size=77")[0] == 400

    opened = []
    monkeypatch.setattr("assetforge.app.api.subprocess.run", lambda args, **kw: opened.append(args))
    status, d = jcall(server, "/api/reveal", method="POST", body={"variant_id": coin.id})
    assert status == 200 and d["revealed"] == coin.file.path
    assert jcall(server, "/api/reveal", method="POST", body={"variant_id": "var_99999"})[0] == 404
    assert jcall(server, "/api/reveal", method="POST", body={"path": "/etc/passwd"})[0] == 400


def test_generate_audio_and_options(server):
    status, d = jcall(server, "/api/audio/recipes")
    assert status == 200 and len(d["recipes"]) >= 20 and d["presets"] and d["effects"]
    status, d = jcall(server, "/api/generate/audio", method="POST",
                      body={"recipe": "coin", "variants": 2, "seed": 4, "format": "ogg"})  # fmt: skip
    assert status == 200 and len(d["result"]["variants"]) == 2 and d["result"]["format"] == "ogg"
    status, d = jcall(server, "/api/generate/audio", method="POST", body={"recipe": "coin", "bogus": 1})
    assert status == 400 and d["error"]["code"] == "INVALID_INPUT"
    status, d = jcall(server, "/api/image/options")
    assert {p["name"] for p in d["providers"]} >= {"openai", "gemini"} and "character" in d["types"]


def test_generate_image_estimate_then_background_job(server, monkeypatch):
    providers.OVERRIDES["openai"] = FakeProvider(Settings({}), name="openai", cost_per_image=0.02)
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    body = {"prompt": "wooden shield", "type": "sprite", "provider": "openai"}
    status, d = jcall(server, "/api/generate/image/estimate", method="POST", body=body)
    assert status == 200 and d["estimate"]["dry_run"] and d["estimate"]["estimated_cost_usd"] == pytest.approx(0.02)
    status, d = jcall(server, "/api/generate/image", method="POST", body=body)
    assert status == 200 and d["state"] in ("running", "done")
    job_id = d.get("job_id")
    if job_id:
        for _ in range(100):
            status, j = jcall(server, f"/api/jobs/{job_id}")
            if j["job"]["status"] != "running":
                break
            time.sleep(0.05)
        assert j["job"]["status"] == "succeeded" and j["result"]["assets"][0]["asset_id"].startswith("ast_")
    status, again = jcall(server, "/api/generate/image", method="POST", body=body)
    assert again["state"] == "done" and again["result"]["reused"]  # identical request: no new job
    assert jcall(server, "/api/jobs?kind=image")[1]["jobs"][0]["status"] == "succeeded"
    status, d = jcall(server, "/api/generate/image", method="POST", body={"prompt": "x"})
    assert status == 400


def test_ui_command_prints_contract_and_exits_with_parent(home, tmp_path):
    """`assetforge ui --json` prints one JSON line with the URL, then exits when its parent dies."""
    import os
    import subprocess
    import sys

    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    env = {**os.environ, "ASSETFORGE_HOME": str(home)}
    proc = subprocess.Popen([sys.executable, "-m", "assetforge", "ui", "--json", "--port", "0", "--parent-pid",
                             str(parent.pid)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # fmt: skip
    try:
        info = json.loads(proc.stdout.readline())
        assert info["url"].startswith(f"http://127.0.0.1:{info['port']}/?token=") and info["home"] == str(home.resolve())
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/overview",
                                     headers={"X-AssetForge-Token": info["token"]})  # fmt: skip
        with urllib.request.urlopen(req, timeout=20) as r:
            assert json.loads(r.read())["status"] == "ok"
        parent.kill()
        parent.wait()
        assert proc.wait(timeout=15) is not None
    finally:
        parent.kill()
        proc.kill()
