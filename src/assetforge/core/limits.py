"""Cross-process provider limits stored in SQLite, shared by every agent using the library.

* Concurrency: a provider has N numbered slots. A call holds one for its duration. Slots expire,
  so a crashed process can't hold one forever.
* Rate: a token bucket per provider refilled at `rpm / 60` tokens per second, capacity `rpm`.

Both wait (with short sleeps) instead of failing immediately, up to a timeout.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from assetforge.core import db, settings_store
from assetforge.errors import ProviderError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

SLOT_TTL_SECONDS = 900.0
POLL_SECONDS = 0.25
sleep: Callable[[float], None] = time.sleep  # replaced in tests
clock: Callable[[], float] = time.time


def owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


@contextmanager
def provider_slot(lib: Library, provider: str, *, timeout: float = 600.0) -> Iterator[int]:
    limit = settings_store.get(lib, f"limits.{provider}.max_concurrent")
    owner = owner_id()
    deadline = clock() + timeout
    slot = None
    while slot is None:
        now = clock()
        with db.transaction(lib.conn):
            lib.conn.execute("DELETE FROM provider_slots WHERE expires_at < ?", (now,))
            taken = {r[0] for r in lib.conn.execute("SELECT slot FROM provider_slots WHERE provider = ?", (provider,))}
            free = next((i for i in range(limit) if i not in taken), None)
            if free is not None:
                lib.conn.execute(
                    "INSERT INTO provider_slots (provider, slot, owner, expires_at) VALUES (?, ?, ?, ?)",
                    (provider, free, owner, now + SLOT_TTL_SECONDS),
                )
                slot = free
        if slot is None:
            if clock() > deadline:
                raise ProviderError(f"No free {provider} slot after {timeout:.0f}s ({limit} concurrent allowed)",
                                    provider=provider, kind="rate_limited", hint="Raise limits.<provider>.max_concurrent")  # fmt: skip
            sleep(POLL_SECONDS)
    try:
        yield slot
    finally:
        with db.transaction(lib.conn):
            lib.conn.execute("DELETE FROM provider_slots WHERE provider = ? AND slot = ? AND owner = ?",
                             (provider, slot, owner))  # fmt: skip


def take_token(lib: Library, provider: str, *, timeout: float = 600.0) -> float:
    """Consume one request token; returns the seconds spent waiting."""
    rpm = settings_store.get(lib, f"limits.{provider}.rpm")
    rate = rpm / 60.0
    started = clock()
    while True:
        now = clock()
        with db.transaction(lib.conn):
            row = lib.conn.execute("SELECT tokens, updated_at FROM rate_buckets WHERE provider = ?", (provider,)).fetchone()
            tokens = float(rpm) if row is None else min(float(rpm), row["tokens"] + (now - row["updated_at"]) * rate)
            granted = tokens >= 1.0
            if granted:
                tokens -= 1.0
            lib.conn.execute(
                "INSERT INTO rate_buckets (provider, tokens, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (provider) DO UPDATE SET tokens = excluded.tokens, updated_at = excluded.updated_at",
                (provider, tokens, now),
            )
        if granted:
            return clock() - started
        if clock() - started > timeout:
            raise ProviderError(f"{provider} request rate limit ({rpm}/min) not available within {timeout:.0f}s",
                                provider=provider, kind="rate_limited")  # fmt: skip
        sleep(max(POLL_SECONDS, (1.0 - tokens) / rate))


def status(lib: Library) -> list[dict[str, object]]:
    now = clock()
    rows = lib.conn.execute(
        "SELECT provider, count(*) AS busy FROM provider_slots WHERE expires_at >= ? GROUP BY provider", (now,)
    ).fetchall()
    return [{"provider": r["provider"], "busy_slots": r["busy"]} for r in rows]
