"""Job queue, workers, crash recovery and multi-agent concurrency."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

from assetforge.config import Settings
from assetforge.core import jobs, queue, settings_store
from assetforge.core.catalog import Library
from assetforge.core.models import JobStatus
from assetforge.errors import ConflictError
from assetforge.image import engine, providers
from assetforge.image.providers.fake import FakeProvider
from assetforge.request import AssetRequest, handle, result_from_job, run_job
from assetforge.worker import run_worker

from conftest import run_cli


@pytest.fixture
def fake_openai(monkeypatch):
    provider = FakeProvider(Settings({}), name="openai")
    providers.OVERRIDES["openai"] = provider
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    return provider


def test_async_requests_run_on_workers_exactly_once(lib, home):
    queued = [handle(lib, AssetRequest(type="sfx", description=d), enqueue=True)
              for d in ("coin pickup", "laser zap", "jump", "explosion", "menu click", "power up")]  # fmt: skip
    assert {r.state for r in queued} == {"queued"} and queue.pending(lib)["queued"] == 6
    twin = handle(lib, AssetRequest(type="sfx", description="coin pickup"), enqueue=True)
    assert twin.reused and twin.reuse_reason == "in_flight" and twin.job_id == queued[0].job_id

    stats = run_worker(home, concurrency=3, drain=True, poll=0.05)
    assert sorted(stats.succeeded) == sorted(r.job_id for r in queued) and not stats.failed
    for r in queued:
        job = jobs.get_job(lib, r.job_id)
        assert job.status == JobStatus.SUCCEEDED and job.attempts == 1 and job.lease_owner is None
        assert result_from_job(lib, job).state == "ready"
    assert lib.info()["counts"]["assets"] == 6


def test_expired_leases_are_recovered_and_finished(lib, home, monkeypatch):
    job_id, _ = queue.create_request_job(lib, {"request": AssetRequest(type="sfx", description="hurt").model_dump(mode="json")},
                                         "fp-crash", owner="dead-host:1:1", queued=False)  # fmt: skip
    assert jobs.get_job(lib, job_id).status == JobStatus.RUNNING
    lib.conn.execute("UPDATE generation_jobs SET lease_expires_at = 1 WHERE id = ?", (job_id,))
    assert queue.recover(lib)["requeued"] == [job_id]
    assert not queue.finish(lib, job_id, "dead-host:1:1", status=JobStatus.SUCCEEDED)  # the zombie lost its lease

    stats = run_worker(home, concurrency=1, drain=True, poll=0.05)
    assert stats.succeeded == [job_id]
    assert jobs.get_job(lib, job_id).attempts == 2

    doomed, _ = queue.create_request_job(lib, {"request": {}}, "fp-doomed", owner="x", queued=False)
    lib.conn.execute("UPDATE generation_jobs SET lease_expires_at = 1, attempts = 3 WHERE id = ?", (doomed,))
    assert queue.recover(lib)["failed"] == [doomed]
    assert jobs.get_job(lib, doomed).error["code"] == "LEASE_EXPIRED"

    stale = lib.paths.tmp / "audio-left-by-a-crash"
    stale.mkdir()
    fresh = lib.paths.tmp / "audio-in-progress"
    fresh.mkdir()
    os.utime(stale, (1, 1))
    assert queue.recover(lib)["tmp_removed"] == 1 and not stale.exists() and fresh.exists()


def test_retryable_failures_are_requeued_with_backoff(lib, home, fake_openai):
    fake_openai.fail_plan = ["unavailable"] * 3
    queued = handle(lib, AssetRequest(type="sprite", description="crate", provider="openai"), enqueue=True)
    settings_store.set_value(lib, "queue.max_attempts", 2)
    lib.conn.execute("UPDATE generation_jobs SET max_attempts = 2 WHERE id = ?", (queued.job_id,))
    stats = run_worker(home, concurrency=1, max_jobs=1, poll=0.05)
    job = jobs.get_job(lib, queued.job_id)
    assert stats.requeued == [queued.job_id] and job.status == JobStatus.QUEUED and job.run_after
    assert job.error["code"] == "PROVIDER_UNAVAILABLE" and job.error["retryable"] is True

    lib.conn.execute("UPDATE generation_jobs SET run_after = NULL WHERE id = ?", (queued.job_id,))
    stats = run_worker(home, concurrency=1, drain=True, poll=0.05)
    assert stats.succeeded == [queued.job_id]


def test_request_retry_resumes_the_paid_image_job_instead_of_paying_again(lib, home, fake_openai, monkeypatch):
    real = engine.finalize
    monkeypatch.setattr(engine, "finalize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    queued = handle(lib, AssetRequest(type="sprite", description="magic wand", provider="openai"), enqueue=True)
    stats = run_worker(home, concurrency=1, drain=True, poll=0.05)
    assert stats.failed == [queued.job_id]
    job = jobs.get_job(lib, queued.job_id)
    assert job.error["code"] == "INTERNAL_ERROR" and job.result["inner_job_id"]

    monkeypatch.setattr(engine, "finalize", real)
    queue.retry(lib, queued.job_id)
    stats = run_worker(home, concurrency=1, drain=True, poll=0.05)
    assert stats.succeeded == [queued.job_id] and len(fake_openai.calls) == 1
    assert result_from_job(lib, jobs.get_job(lib, queued.job_id)).state == "ready"


def test_sync_callers_join_identical_in_flight_requests(lib, home, fake_openai):
    results = []
    spec = {"type": "sprite", "description": "wooden shield", "provider": "openai"}

    def agent() -> None:
        with Library(home) as own:
            results.append(handle(own, AssetRequest(**spec)))

    threads = [threading.Thread(target=agent) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.asset_id for r in results}) == 1 and len(fake_openai.calls) == 1
    assert sorted(r.reuse_reason or "generated" for r in results)[0:1] == ["generated"]
    assert len([j for j in jobs.list_jobs(lib) if j.kind == "request"]) == 1


def test_cancel_and_retry_rules(lib):
    queued = handle(lib, AssetRequest(type="sfx", description="ui error buzz"), enqueue=True)
    assert queue.cancel(lib, queued.job_id).status == JobStatus.CANCELLED
    with pytest.raises(ConflictError):
        queue.cancel(lib, queued.job_id)
    assert queue.retry(lib, queued.job_id).status == JobStatus.QUEUED
    owner = "me:1:1"
    assert queue.claim(lib, queued.job_id, owner) is not None
    result = run_job(lib, queued.job_id, owner)
    assert result.state == "ready"


def test_multiple_agent_processes_share_one_generation(home, tmp_path):
    """Several separate `assetforge request` processes ask for the same thing at the same moment."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"type": "sprite", "description": "glowing mushroom", "provider": "fake",
                                "project": "panda-bamboo", "key": "props/mushroom"}))  # fmt: skip
    env = {**os.environ, "ASSETFORGE_HOME": str(home), "ASSETFORGE_ENABLE_FAKE_PROVIDER": "1"}
    cmd = [sys.executable, "-m", "assetforge", "request", "--file", str(spec), "--json"]
    procs = [subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(5)]
    outputs = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err.decode()
        outputs.append(json.loads(out))
    assert len({o["asset_id"] for o in outputs}) == 1
    assert {o["state"] for o in outputs} == {"ready"}
    with Library(home) as lib:
        assert lib.info()["counts"]["assets"] == 1
        image_jobs = [j for j in jobs.list_jobs(lib) if j.kind == "image"]
        assert len(image_jobs) == 1 and image_jobs[0].status == JobStatus.SUCCEEDED
        assert lib.verify(rehash=True)["ok"]


def test_cli_request_worker_and_jobs(capsys, home, tmp_path, monkeypatch):
    spec = tmp_path / "beetle-hit.json"
    spec.write_text(json.dumps({"project": "panda-bamboo", "key": "enemy/beetle/hit", "type": "sfx",
                                "description": "Short soft crunchy cartoon insect hit", "duration_ms": 350,
                                "variants": 2, "format": "ogg"}))  # fmt: skip
    code, out, err = run_cli(capsys, "request", "--file", str(spec), "--async", "--json")
    assert code == 0, err
    assert out["state"] == "queued" and out["job_id"].startswith("job_")
    code, stats, _ = run_cli(capsys, "worker", "--drain", "--poll", "0.05", "--json")
    assert code == 0 and stats["worker"]["succeeded"] == [out["job_id"]]
    code, waited, _ = run_cli(capsys, "jobs", "wait", out["job_id"], "--json")
    assert waited["result"]["state"] == "ready" and len(waited["result"]["variants"]) == 2

    code, again, _ = run_cli(capsys, "request", "--file", str(spec), "--json")
    assert again["reused"] and again["reuse_reason"] == "key"
    code, missing, _ = run_cli(capsys, "request", "--type", "sfx", "-d", "thunder", "--reuse-policy",
                               "require_existing", "--json")  # fmt: skip
    assert code == 3 and missing["error"]["code"] == "NOT_FOUND" and missing["error"]["hint"]
    code, bad, _ = run_cli(capsys, "request", "--type", "sfx", "--json")
    assert code == 4

    assert run_cli(capsys, "config", "set", "budget.daily_usd", "2.5", "--json")[1]["value"] == 2.5
    code, cfg, _ = run_cli(capsys, "config", "list", "--json")
    assert cfg["settings"]["budget.daily_usd"]["set"] and cfg["settings"]["limits.default.rpm"]["value"] == 30
    code, bud, _ = run_cli(capsys, "budget", "--monthly", "40", "--json")
    assert bud["budget"]["daily"]["limit_usd"] == 2.5 and bud["budget"]["monthly"]["limit_usd"] == 40
    code, err_out, _ = run_cli(capsys, "config", "set", "budget.daily", "1", "--json")
    assert code == 4
    code, catalog, _ = run_cli(capsys, "errors", "--json")
    assert {"BUDGET_EXCEEDED", "PROVIDER_QUOTA", "INTERNAL_ERROR"} <= {e["code"] for e in catalog["errors"]}
    code, rec, _ = run_cli(capsys, "jobs", "recover", "--json")
    assert code == 0 and rec["recovered"]["requeued"] == [] and rec["recovered"]["tmp_removed"] == 0

    monkeypatch.setenv("COLUMNS", "200")
    code, text, _ = run_cli(capsys, "request", "--file", str(spec))
    assert code == 0 and "Reused" in text
    assert run_cli(capsys, "budget")[0] == 0 and run_cli(capsys, "errors")[0] == 0
