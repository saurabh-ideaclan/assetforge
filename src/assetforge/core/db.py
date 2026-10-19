"""SQLite connection handling, explicit migrations and ID sequences.

The catalog stores metadata only; binaries live in the content-addressed object store.
Connections run in autocommit mode and writes use short `BEGIN IMMEDIATE` transactions,
so several agents can share one library: WAL gives concurrent readers and one writer.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from assetforge.core.util import utcnow
from assetforge.errors import StorageError

BUSY_TIMEOUT_MS = 30_000

SCHEMA_V1 = [
    "CREATE TABLE id_sequences (prefix TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    """
    CREATE TABLE asset_files (
        sha256      TEXT PRIMARY KEY,
        rel_path    TEXT NOT NULL,
        ext         TEXT NOT NULL,
        mime_type   TEXT NOT NULL,
        kind        TEXT NOT NULL,
        size_bytes  INTEGER NOT NULL,
        width       INTEGER,
        height      INTEGER,
        has_alpha   INTEGER,
        frames      INTEGER,
        duration_ms INTEGER,
        sample_rate INTEGER,
        channels    INTEGER,
        extra       TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE assets (
        id                 TEXT PRIMARY KEY,
        key                TEXT,
        name               TEXT NOT NULL,
        type               TEXT NOT NULL,
        description        TEXT NOT NULL DEFAULT '',
        scope              TEXT NOT NULL DEFAULT 'global'
                           CHECK (scope IN ('global', 'style', 'project')),
        project            TEXT,
        style              TEXT,
        status             TEXT NOT NULL DEFAULT 'approved'
                           CHECK (status IN ('candidate', 'approved', 'rejected', 'archived')),
        origin             TEXT NOT NULL DEFAULT 'import'
                           CHECK (origin IN ('import', 'procedural', 'ai', 'derived')),
        license            TEXT,
        commercial_use     TEXT NOT NULL DEFAULT 'unknown'
                           CHECK (commercial_use IN ('allowed', 'restricted', 'unknown')),
        attribution        TEXT,
        source_uri         TEXT,
        primary_variant_id TEXT,
        metadata           TEXT NOT NULL DEFAULT '{}',
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX idx_assets_project_key ON assets (ifnull(project, ''), key) WHERE key IS NOT NULL",
    "CREATE INDEX idx_assets_type ON assets (type)",
    "CREATE INDEX idx_assets_style ON assets (style)",
    "CREATE INDEX idx_assets_project ON assets (project)",
    """
    CREATE TABLE asset_variants (
        id                TEXT PRIMARY KEY,
        asset_id          TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
        file_sha256       TEXT NOT NULL REFERENCES asset_files (sha256),
        label             TEXT NOT NULL,
        parent_variant_id TEXT REFERENCES asset_variants (id),
        params            TEXT NOT NULL DEFAULT '{}',
        original_filename TEXT,
        created_at        TEXT NOT NULL,
        UNIQUE (asset_id, label)
    )
    """,
    "CREATE INDEX idx_variants_file ON asset_variants (file_sha256)",
    "CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)",
    """
    CREATE TABLE asset_tags (
        asset_id TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
        tag_id   INTEGER NOT NULL REFERENCES tags (id),
        PRIMARY KEY (asset_id, tag_id)
    )
    """,
    "CREATE INDEX idx_asset_tags_tag ON asset_tags (tag_id)",
    """
    CREATE TABLE collections (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        kind        TEXT NOT NULL DEFAULT 'custom',
        description TEXT NOT NULL DEFAULT '',
        project     TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE collection_assets (
        collection_id TEXT NOT NULL REFERENCES collections (id) ON DELETE CASCADE,
        asset_id      TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
        added_at      TEXT NOT NULL,
        PRIMARY KEY (collection_id, asset_id)
    )
    """,
    "CREATE INDEX idx_collection_assets_asset ON collection_assets (asset_id)",
    """
    CREATE TABLE generation_jobs (
        id          TEXT PRIMARY KEY,
        kind        TEXT NOT NULL,
        status      TEXT NOT NULL
                    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
        fingerprint TEXT,
        request     TEXT NOT NULL DEFAULT '{}',
        provider    TEXT,
        model       TEXT,
        asset_id    TEXT REFERENCES assets (id),
        result      TEXT NOT NULL DEFAULT '{}',
        error       TEXT,
        cost_usd    REAL,
        attempts    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_jobs_fingerprint ON generation_jobs (fingerprint)",
    "CREATE INDEX idx_jobs_status ON generation_jobs (status)",
    """
    CREATE TABLE asset_lineage (
        id                 INTEGER PRIMARY KEY,
        asset_id           TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
        related_asset_id   TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
        relation           TEXT NOT NULL
                           CHECK (relation IN ('derived_from', 'edited_from', 'variant_of', 'source_of')),
        variant_id         TEXT,
        related_variant_id TEXT,
        created_at         TEXT NOT NULL,
        UNIQUE (asset_id, related_asset_id, relation)
    )
    """,
    "CREATE INDEX idx_lineage_related ON asset_lineage (related_asset_id)",
    """
    CREATE TABLE project_usage (
        project    TEXT NOT NULL,
        asset_id   TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
        variant_id TEXT NOT NULL REFERENCES asset_variants (id) ON DELETE CASCADE,
        usage_key  TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (project, asset_id, variant_id)
    )
    """,
    "CREATE INDEX idx_usage_asset ON project_usage (asset_id)",
    """
    CREATE TABLE style_profiles (
        id                  TEXT PRIMARY KEY,
        name                TEXT NOT NULL UNIQUE,
        description         TEXT NOT NULL DEFAULT '',
        palette             TEXT NOT NULL DEFAULT '[]',
        instructions        TEXT NOT NULL DEFAULT '',
        reference_asset_ids TEXT NOT NULL DEFAULT '[]',
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE asset_events (
        id         INTEGER PRIMARY KEY,
        asset_id   TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
        event      TEXT NOT NULL,
        data       TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_events_asset ON asset_events (asset_id)",
    """
    CREATE VIRTUAL TABLE assets_fts USING fts5 (
        asset_id UNINDEXED, name, key, tags, description, type, style,
        tokenize = 'porter unicode61 remove_diacritics 2'
    )
    """,
]

# Phase 06: job queue leases, budget reservations, shared settings, cross-process rate limiting.
SCHEMA_V2 = [
    "ALTER TABLE generation_jobs ADD COLUMN lease_owner TEXT",
    "ALTER TABLE generation_jobs ADD COLUMN lease_expires_at REAL",
    "ALTER TABLE generation_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE generation_jobs ADD COLUMN run_after REAL",
    "ALTER TABLE generation_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE generation_jobs ADD COLUMN estimated_cost_usd REAL",
    "CREATE INDEX idx_jobs_queue ON generation_jobs (status, kind, priority, created_at)",
    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)",
    """
    CREATE TABLE provider_slots (
        provider   TEXT NOT NULL,
        slot       INTEGER NOT NULL,
        owner      TEXT NOT NULL,
        expires_at REAL NOT NULL,
        PRIMARY KEY (provider, slot)
    )
    """,
    "CREATE TABLE rate_buckets (provider TEXT PRIMARY KEY, tokens REAL NOT NULL, updated_at REAL NOT NULL)",
]

# Token usage metering and internet-refreshed model pricing.
SCHEMA_V3 = [
    """
    CREATE TABLE usage_records (
        id                  INTEGER PRIMARY KEY,
        created_at          TEXT NOT NULL,
        provider            TEXT NOT NULL,
        model               TEXT NOT NULL,
        operation           TEXT NOT NULL,
        job_id              TEXT,
        project             TEXT,
        quality             TEXT,
        size                TEXT,
        images              INTEGER NOT NULL DEFAULT 0,
        input_text_tokens   INTEGER NOT NULL DEFAULT 0,
        input_image_tokens  INTEGER NOT NULL DEFAULT 0,
        output_image_tokens INTEGER NOT NULL DEFAULT 0,
        output_text_tokens  INTEGER NOT NULL DEFAULT 0,
        total_tokens        INTEGER NOT NULL DEFAULT 0,
        cost_usd            REAL,
        estimated_cost_usd  REAL,
        price_source        TEXT,
        rates               TEXT NOT NULL DEFAULT '{}',
        raw_usage           TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX idx_usage_records_time ON usage_records (created_at)",
    "CREATE INDEX idx_usage_records_model ON usage_records (provider, model, quality, size)",
    "CREATE UNIQUE INDEX idx_usage_records_job ON usage_records (job_id) WHERE job_id IS NOT NULL",
    """
    CREATE TABLE pricing_snapshots (
        id         INTEGER PRIMARY KEY,
        source     TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        models     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE price_overrides (
        model      TEXT NOT NULL,
        field      TEXT NOT NULL,
        value      REAL NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (model, field)
    )
    """,
]

# Append (version, statements) pairs; never edit a migration that has shipped.
MIGRATIONS: list[tuple[int, list[str]]] = [(1, SCHEMA_V1), (2, SCHEMA_V2), (3, SCHEMA_V3)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def connect(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        _enable_wal(conn)
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as exc:
        raise StorageError(f"Cannot open catalog {path}: {exc}", catalog=str(path)) from exc
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    """WAL is persistent, so only the very first opener switches it on. That switch needs an
    exclusive lock and SQLite does not retry it through the busy handler, so several agents
    creating a fresh library at once would otherwise see 'database is locked'."""
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    delay = 0.01
    while True:
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc) or time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A short write transaction. Never hold one across slow work such as API calls."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> int:
    with transaction(conn):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        current = conn.execute("SELECT ifnull(max(version), 0) FROM schema_migrations").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise StorageError(
                f"Catalog schema v{current} is newer than this AssetForge supports (v{SCHEMA_VERSION}); upgrade AssetForge",
                schema_version=current,
            )
        for version, statements in MIGRATIONS:
            if version <= current:
                continue
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, utcnow())
            )
    return SCHEMA_VERSION


def next_id(conn: sqlite3.Connection, prefix: str, width: int = 5) -> str:
    """Stable, human-friendly sequential IDs like ast_00241. Call inside a transaction."""
    value = conn.execute(
        "INSERT INTO id_sequences (prefix, value) VALUES (?, 1) "
        "ON CONFLICT (prefix) DO UPDATE SET value = value + 1 RETURNING value",
        (prefix,),
    ).fetchone()[0]
    return f"{prefix}_{value:0{width}d}"
