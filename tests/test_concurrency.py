"""Several agents importing the same folder at once must not corrupt or duplicate the catalog."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from assetforge.core.catalog import Library

from conftest import PACK_UNIQUE


def test_concurrent_imports_of_the_same_folder(home, pack):
    env = {**os.environ, "ASSETFORGE_HOME": str(home)}
    cmd = [sys.executable, "-m", "assetforge", "import", str(pack), "--json"]
    procs = [subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
    outputs = [p.communicate(timeout=180) for p in procs]

    created = 0
    for proc, (stdout, stderr) in zip(procs, outputs, strict=True):
        assert proc.returncode == 0, stderr.decode()
        report = json.loads(stdout)
        assert report["summary"]["error"] == 0
        created += report["summary"]["created"]

    assert created == PACK_UNIQUE
    with Library(home) as lib:
        info = lib.info()
        assert info["counts"]["assets"] == PACK_UNIQUE
        assert info["counts"]["files"] == PACK_UNIQUE
        assert lib.verify(rehash=True)["ok"]
        assert not list(lib.paths.tmp.iterdir())


def test_concurrent_identical_audio_requests_produce_one_asset(home):
    env = {**os.environ, "ASSETFORGE_HOME": str(home)}
    cmd = [sys.executable, "-m", "assetforge", "audio", "generate", "-r", "coin", "-n", "3", "--seed", "5",
           "-f", "ogg", "--json"]  # fmt: skip
    procs = [subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
    results = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=180)
        assert proc.returncode == 0, stderr.decode()
        results.append(json.loads(stdout))

    assert len({r["asset_id"] for r in results}) == 1
    assert all(len(r["variants"]) == 3 for r in results)
    with Library(home) as lib:
        assert lib.info()["counts"]["assets"] == 1
        assert lib.info()["counts"]["variants"] == 6  # 3 WAV masters + 3 OGG
