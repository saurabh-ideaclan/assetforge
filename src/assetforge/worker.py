"""`assetforge worker`: run queued request jobs with bounded concurrency.

Each worker thread opens its own catalog connection, recovers expired leases, claims the next
job, and runs it under a heartbeat lease. Retryable failures (rate limits, outages) are
requeued with backoff until `max_attempts`. Run as many workers, on as many machines sharing
the library, as you like: claiming is atomic, and provider limits and budgets are enforced globally.
"""

from __future__ import annotations

import logging
import threading

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from assetforge.config import load_settings
from assetforge.core import limits, queue
from assetforge.core.catalog import Library
from assetforge.errors import AssetForgeError

log = logging.getLogger("assetforge.worker")


@dataclass
class WorkerStats:
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    requeued: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, bucket: str, job_id: str) -> None:
        with self.lock:
            getattr(self, bucket).append(job_id)

    def total(self) -> int:
        return len(self.succeeded) + len(self.failed) + len(self.requeued)

    def as_dict(self) -> dict[str, list[str]]:
        return {"succeeded": self.succeeded, "failed": self.failed, "requeued": self.requeued}


def run_worker(
    home: str | Path | None,
    *,
    concurrency: int = 2,
    drain: bool = False,
    poll: float = 1.0,
    max_jobs: int | None = None,
    stop: threading.Event | None = None,
    on_job: Callable[[str, str], None] | None = None,
) -> WorkerStats:
    """Process request jobs until stopped; with `drain`, exit once nothing is queued or running."""
    stop = stop or threading.Event()
    stats = WorkerStats()

    def loop() -> None:
        lib = Library(home)
        settings = load_settings(lib.paths.home)
        owner = limits.owner_id()
        try:
            while not stop.is_set():
                if max_jobs is not None and stats.total() >= max_jobs:
                    return
                queue.recover(lib)
                job = queue.claim_next(lib, owner)
                if job is None:
                    counts = queue.pending(lib)
                    if drain and counts["queued"] == 0 and counts["running"] == 0:
                        return
                    stop.wait(poll)
                    continue
                if on_job:
                    on_job("start", job.id)
                try:
                    from assetforge.request import run_job

                    run_job(lib, job.id, owner, settings, requeue_on_retryable=True)
                    stats.add("succeeded", job.id)
                except AssetForgeError as exc:
                    current = queue.jobs.get_job(lib, job.id)
                    stats.add("requeued" if current.status.value == "queued" else "failed", job.id)
                    log.warning("job %s: %s", job.id, exc.message)
                except Exception:  # keep the worker alive; the job is already marked failed
                    stats.add("failed", job.id)
                    log.exception("job %s crashed", job.id)
                if on_job:
                    on_job("end", job.id)
        finally:
            lib.close()

    threads = [threading.Thread(target=loop, name=f"assetforge-worker-{i}", daemon=True) for i in range(max(1, concurrency))]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.2)
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join()
    return stats


__all__ = ["WorkerStats", "run_worker"]
