"""Bring another library into this one (`assetforge library merge SOURCE`), so nothing made elsewhere is lost.

Everything that belongs to the work is copied: assets with their variants, tags, lineage, history, project usage
and collections; the stored files; generation jobs (with their `jobs/<id>/` folders, so cached text answers and
paid image jobs are reused); usage/cost records; art, sound and style profiles; and story folders with their
registry entries.

- The source library is only read (a backup-API copy is opened, migrated in memory if older).
- IDs are renumbered in this library; references inside JSON (metadata, params, job requests and results, profile
  reference lists, event data) are rewritten through the same map.
- An asset whose primary file already exists here is the same asset: it is linked, not duplicated, and only its
  missing variants, tags, usage and collection memberships are added.
- An asset key already taken here by a *different* asset is never overwritten: the incoming asset keeps no key
  (`metadata.merged_key` records it) and the report lists the conflict.
- Library-wide settings, provider slots, rate buckets and price snapshots stay as they are here.
- Stories are copied into `<library>/stories/<folder>` and registered; a story merged before is updated in place
  (new or changed files only, nothing deleted).
- Re-running is safe: a ledger in `<library>/merges/` remembers what each source already contributed.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assetforge.core import db
from assetforge.core.util import utcnow
from assetforge.errors import InvalidInputError, StorageError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

ID = re.compile(r"\b(?:ast|var|job|col|sty)_\d+\b")
LEFT_OUT = ("pruned", "withheld")


def _ledger_path(lib: Library, source: Path) -> Path:
    digest = hashlib.sha256(str(source).encode()).hexdigest()[:16]
    return lib.paths.home / "merges" / f"{digest}.json"


def _open_copy(source: Path, scratch: Path) -> sqlite3.Connection:
    """A private, migrated copy of the source catalog (the source itself is never written)."""
    copy = scratch / "source.sqlite3"
    src = sqlite3.connect(f"file:{source / 'catalog.sqlite3'}?mode=ro", uri=True)
    out = sqlite3.connect(copy)
    try:
        src.backup(out)
    finally:
        src.close()
        out.close()
    conn = db.connect(copy)
    db.migrate(conn)
    return conn


class _Merge:
    def __init__(self, lib: Library, src: sqlite3.Connection, source: Path, ledger: dict[str, Any], dry_run: bool) -> None:
        self.lib, self.src, self.source, self.dry_run = lib, src, source, dry_run
        self.conn = lib.conn
        self.ids: dict[str, str] = dict(ledger.get("ids", {}))
        self.done_jobs: set[str] = set(ledger.get("jobs", []))
        self.report: dict[str, Any] = {
            "source": str(source), "dry_run": dry_run,
            "assets": {"added": 0, "linked": 0}, "variants": 0, "files": {"copied": 0, "bytes": 0, "missing": []},
            "jobs": 0, "jobs_linked": 0, "usage_records": 0, "collections": 0, "profiles": 0, "events": 0,
            "key_conflicts": [], "stories": [],
        }  # fmt: skip

    # ----------------------------------------------------------------- helpers
    def remap(self, text: str | None) -> str | None:
        if not text:
            return text
        return ID.sub(lambda m: self.ids.get(m.group(0), m.group(0)), text)

    def new_id(self, prefix: str) -> str:
        return db.next_id(self.conn, prefix)

    def dest_exists(self, table: str, ident: str) -> bool:
        return self.conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (ident,)).fetchone() is not None  # noqa: S608

    # ----------------------------------------------------------------- files (outside the transaction)
    def copy_files(self) -> None:
        for row in self.src.execute("SELECT sha256, rel_path, ext, size_bytes, extra FROM asset_files").fetchall():
            extra = json.loads(row["extra"] or "{}")
            here = self.conn.execute("SELECT rel_path FROM asset_files WHERE sha256 = ?", (row["sha256"],)).fetchone()
            target = self.lib.store.object_path(row["sha256"], row["ext"])
            if here and self.lib.store.abs_path(here["rel_path"]).is_file():
                continue
            if target.is_file():
                continue
            origin = self.source / row["rel_path"]
            if not origin.is_file():
                if not any(extra.get(flag) for flag in LEFT_OUT):
                    self.report["files"]["missing"].append(row["rel_path"])
                continue
            if not self.dry_run:
                self.lib.store.put_file(origin, row["ext"], expected_sha256=row["sha256"])
            self.report["files"]["copied"] += 1
            self.report["files"]["bytes"] += row["size_bytes"]

    def copy_job_dirs(self, jobs: dict[str, str]) -> None:
        for old, new in jobs.items():
            origin = self.source / "jobs" / old
            target = self.lib.paths.jobs / new
            if origin.is_dir() and not target.exists() and not self.dry_run:
                shutil.copytree(origin, target)

    # ----------------------------------------------------------------- catalog rows (one transaction)
    def run(self) -> dict[str, str]:
        src = self.src
        self._plan()
        for row in src.execute("SELECT * FROM asset_files").fetchall():
            cols = row.keys()
            self.conn.execute(
                f"INSERT OR IGNORE INTO asset_files ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
                tuple(row),
            )
        assets = src.execute("SELECT * FROM assets ORDER BY created_at, id").fetchall()
        variants = src.execute("SELECT * FROM asset_variants ORDER BY created_at, id").fetchall()
        by_asset: dict[str, list[sqlite3.Row]] = {}
        for v in variants:
            by_asset.setdefault(v["asset_id"], []).append(v)
        primary_sha = {v["id"]: v["file_sha256"] for v in variants}
        linked: set[str] = set()
        fresh: list[tuple[sqlite3.Row, str]] = []

        for a in assets:
            known = self.ids.get(a["id"])
            if known and self.dest_exists("assets", known):
                linked.add(a["id"])
                continue
            sha = primary_sha.get(a["primary_variant_id"] or "")
            same = None
            if sha:
                same = self.conn.execute(
                    "SELECT v.asset_id FROM asset_variants v JOIN assets x ON x.id = v.asset_id WHERE v.file_sha256 = ? "
                    "ORDER BY (x.primary_variant_id = v.id) DESC, x.created_at LIMIT 1", (sha,)).fetchone()  # fmt: skip
            if same:
                self.ids[a["id"]] = same["asset_id"]
                linked.add(a["id"])
                self.report["assets"]["linked"] += 1
            else:
                self.ids[a["id"]] = self.new_id("ast")
                fresh.append((a, self.ids[a["id"]]))
                self.report["assets"]["added"] += 1

        # variants: ids for every variant first, so params/parents can be rewritten
        pending: list[tuple[sqlite3.Row, str, str]] = []
        for a in assets:
            target = self.ids[a["id"]]
            for v in by_asset.get(a["id"], []):
                known = self.ids.get(v["id"])
                if known and self.dest_exists("asset_variants", known):
                    continue
                here = self.conn.execute(
                    "SELECT id FROM asset_variants WHERE asset_id = ? AND file_sha256 = ? ORDER BY (label = ?) DESC LIMIT 1",
                    (target, v["file_sha256"], v["label"])).fetchone()  # fmt: skip
                if here:
                    self.ids[v["id"]] = here["id"]
                    continue
                self.ids[v["id"]] = self.new_id("var")
                pending.append((v, target, self.ids[v["id"]]))

        for a, new in fresh:
            key, metadata = a["key"], json.loads(self.remap(a["metadata"]) or "{}")
            if key and self.conn.execute(
                "SELECT 1 FROM assets WHERE ifnull(project, '') = ifnull(?, '') AND key = ?", (a["project"], key)
            ).fetchone():
                self.report["key_conflicts"].append({"asset_id": new, "project": a["project"], "key": key})
                metadata["merged_key"] = key
                key = None
            row = dict(a) | {"id": new, "key": key, "metadata": json.dumps(metadata), "primary_variant_id": None,
                             "parent_asset_id": None}  # fmt: skip
            cols = list(row)
            self.conn.execute(
                f"INSERT INTO assets ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
                tuple(row[c] for c in cols),
            )

        for v, asset_id, new in pending:
            label = v["label"]
            n = 2
            while self.conn.execute("SELECT 1 FROM asset_variants WHERE asset_id = ? AND label = ?", (asset_id, label)).fetchone():
                label, n = f"{v['label']}_m{n}", n + 1
            self.conn.execute(
                "INSERT INTO asset_variants (id, asset_id, file_sha256, label, parent_variant_id, params, "
                "original_filename, created_at) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (new, asset_id, v["file_sha256"], label, self.remap(v["params"]), v["original_filename"], v["created_at"]),
            )
            self.report["variants"] += 1
        for v, _, new in pending:
            if v["parent_variant_id"] and v["parent_variant_id"] in self.ids:
                self.conn.execute("UPDATE asset_variants SET parent_variant_id = ? WHERE id = ?",
                                  (self.ids[v["parent_variant_id"]], new))  # fmt: skip
        for a, new in fresh:
            self.conn.execute(
                "UPDATE assets SET primary_variant_id = ?, parent_asset_id = ? WHERE id = ?",
                (self.ids.get(a["primary_variant_id"] or ""), self.ids.get(a["parent_asset_id"] or ""), new),
            )

        self._tags()
        self._lineage()
        self._events({a["id"] for a, _ in fresh})
        self._usage()
        self._collections()
        jobs = self._jobs()
        self._usage_records()
        self._profiles()
        for _, new in fresh:
            self.lib._refresh_fts(new)
        return jobs

    def _tags(self) -> None:
        for row in self.src.execute("SELECT t.asset_id, g.name FROM asset_tags t JOIN tags g ON g.id = t.tag_id").fetchall():
            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (row["name"],))
            tag = self.conn.execute("SELECT id FROM tags WHERE name = ?", (row["name"],)).fetchone()["id"]
            self.conn.execute("INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                              (self.ids[row["asset_id"]], tag))  # fmt: skip

    def _lineage(self) -> None:
        for row in self.src.execute("SELECT * FROM asset_lineage").fetchall():
            a, b = self.ids[row["asset_id"]], self.ids[row["related_asset_id"]]
            if a == b:
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO asset_lineage (asset_id, related_asset_id, relation, variant_id, related_variant_id, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (a, b, row["relation"], self.ids.get(row["variant_id"] or "", row["variant_id"]),
                 self.ids.get(row["related_variant_id"] or "", row["related_variant_id"]), row["created_at"]),
            )  # fmt: skip

    def _events(self, fresh: set[str]) -> None:
        """History moves with assets that are new here; a linked asset already has its own history."""
        for row in self.src.execute("SELECT * FROM asset_events ORDER BY id").fetchall():
            if row["asset_id"] in fresh:
                self.conn.execute("INSERT INTO asset_events (asset_id, event, data, created_at) VALUES (?, ?, ?, ?)",
                                  (self.ids[row["asset_id"]], row["event"], self.remap(row["data"]), row["created_at"]))  # fmt: skip
                self.report["events"] += 1
        for old in sorted(fresh):
            self.conn.execute("INSERT INTO asset_events (asset_id, event, data, created_at) VALUES (?, 'merged', ?, ?)",
                              (self.ids[old], json.dumps({"from": str(self.source), "source_id": old}), utcnow()))  # fmt: skip

    def _usage(self) -> None:
        for row in self.src.execute("SELECT * FROM project_usage").fetchall():
            self.conn.execute(
                "INSERT OR IGNORE INTO project_usage (project, asset_id, variant_id, usage_key, created_at) VALUES (?, ?, ?, ?, ?)",
                (row["project"], self.ids[row["asset_id"]], self.ids[row["variant_id"]], row["usage_key"], row["created_at"]),
            )

    def _collections(self) -> None:
        for row in self.new_collections:
            self.conn.execute(
                "INSERT INTO collections (id, name, kind, description, project, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.ids[row["id"]], row["name"], row["kind"], row["description"], row["project"], row["created_at"],
                 row["updated_at"]),
            )  # fmt: skip
            self.report["collections"] += 1
        for row in self.src.execute("SELECT * FROM collection_assets").fetchall():
            self.conn.execute("INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) VALUES (?, ?, ?)",
                              (self.ids[row["collection_id"]], self.ids[row["asset_id"]], row["added_at"]))  # fmt: skip

    def _plan(self) -> None:
        """Every id is decided before any row is written, so references inside JSON always resolve.
        A job that is already here (same kind, fingerprint and start time: both libraries share its history)
        is linked, never copied twice, so its cost isn't counted twice either."""
        self.job_rows = []
        for r in self.src.execute("SELECT * FROM generation_jobs ORDER BY created_at, id").fetchall():
            if r["id"] in self.done_jobs:
                continue
            same = self.conn.execute(
                "SELECT id FROM generation_jobs WHERE kind = ? AND ifnull(fingerprint, '') = ifnull(?, '') AND created_at = ?",
                (r["kind"], r["fingerprint"], r["created_at"])).fetchone()  # fmt: skip
            if same:
                self.ids[r["id"]] = same["id"]
                self.done_jobs.add(r["id"])
                self.report["jobs_linked"] += 1
            else:
                self.ids[r["id"]] = self.new_id("job")
                self.job_rows.append(r)
        self.new_collections = []
        for row in self.src.execute("SELECT * FROM collections").fetchall():
            here = self.conn.execute("SELECT id FROM collections WHERE name = ?", (row["name"],)).fetchone()
            self.ids[row["id"]] = here["id"] if here else self.new_id("col")
            if not here:
                self.new_collections.append(row)
        self.new_styles = []
        for row in self.src.execute("SELECT * FROM style_profiles").fetchall():
            here = self.conn.execute("SELECT id FROM style_profiles WHERE name = ?", (row["name"],)).fetchone()
            self.ids[row["id"]] = here["id"] if here else self.new_id("sty")
            if not here:
                self.new_styles.append(row)

    def _jobs(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in self.job_rows:
            new = self.ids[r["id"]]
            row = dict(r) | {
                "id": new, "request": self.remap(r["request"]), "result": self.remap(r["result"]),
                "error": self.remap(r["error"]), "asset_id": self.ids.get(r["asset_id"] or "") if r["asset_id"] else None,
                "lease_owner": None, "lease_expires_at": None,
            }  # fmt: skip
            if r["status"] in ("queued", "running"):  # owned by a process of the other library: never re-run here
                row["status"] = "cancelled"
            cols = list(row)
            self.conn.execute(
                f"INSERT INTO generation_jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
                tuple(row[c] for c in cols),
            )
            self.done_jobs.add(r["id"])
            out[r["id"]] = new
        self.report["jobs"] = len(out)
        return out

    def _usage_records(self) -> None:
        for r in self.src.execute("SELECT * FROM usage_records WHERE job_id IS NOT NULL ORDER BY id").fetchall():
            job = self.ids.get(r["job_id"])
            if job is None or self.conn.execute("SELECT 1 FROM usage_records WHERE job_id = ?", (job,)).fetchone():
                continue
            row = {k: r[k] for k in r.keys() if k != "id"} | {"job_id": job}
            cols = list(row)
            self.conn.execute(
                f"INSERT INTO usage_records ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
                tuple(row[c] for c in cols),
            )
            self.report["usage_records"] += 1

    def _profiles(self) -> None:
        for r in self.src.execute("SELECT * FROM art_profiles").fetchall():
            if not self.conn.execute("SELECT 1 FROM art_profiles WHERE project = ?", (r["project"],)).fetchone():
                self.conn.execute(
                    "INSERT INTO art_profiles (project, direction, reference_asset_ids, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (r["project"], r["direction"], self.remap(r["reference_asset_ids"]), r["created_at"], r["updated_at"]),
                )
                self.report["profiles"] += 1
        for r in self.src.execute("SELECT * FROM sound_profiles").fetchall():
            if not self.conn.execute("SELECT 1 FROM sound_profiles WHERE project = ?", (r["project"],)).fetchone():
                self.conn.execute("INSERT INTO sound_profiles (project, direction, created_at, updated_at) VALUES (?, ?, ?, ?)",
                                  (r["project"], r["direction"], r["created_at"], r["updated_at"]))  # fmt: skip
                self.report["profiles"] += 1
        for r in self.new_styles:
            new = self.ids[r["id"]]
            self.conn.execute(
                "INSERT INTO style_profiles (id, name, description, palette, instructions, reference_asset_ids, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new, r["name"], r["description"], r["palette"], r["instructions"], self.remap(r["reference_asset_ids"]),
                 r["created_at"], r["updated_at"]),
            )  # fmt: skip
            self.report["profiles"] += 1


# ---------------------------------------------------------------------- stories


def _sync_tree(origin: Path, target: Path) -> int:
    """Copy new or changed files from origin into target; never deletes. Returns files written."""
    written = 0
    for path in origin.rglob("*"):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        dest = target / path.relative_to(origin)
        if dest.is_file() and filecmp.cmp(path, dest, shallow=False):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        written += 1
    return written


def _merge_stories(lib: Library, source: Path, ledger: dict[str, Any], dry_run: bool, extra: list[Path]) -> list[dict[str, Any]]:
    from assetforge.story.project import StoryProject, register, registered

    placed: dict[str, str] = dict(ledger.get("stories", {}))  # source folder → folder here
    folders = {p.resolve() for p in registered(source).values()}
    if (source / "stories").is_dir():
        folders |= {p.parent.resolve() for p in (source / "stories").glob("*/story.json")}
    folders |= {p.resolve() for p in extra}
    out = []
    home = lib.paths.home.resolve()
    for folder in sorted(folders):
        if not (folder / "story.json").is_file():
            continue
        if home in folder.parents:  # already lives in this library
            if not dry_run:
                register(lib.paths.home, StoryProject(folder))
            out.append({"from": str(folder), "to": str(folder), "files": 0, "status": "already here"})
            continue
        target = Path(placed[str(folder)]) if str(folder) in placed else None
        status = "updated"
        if target is None:
            base = lib.paths.home / "stories" / folder.name
            target, n = base, 2
            while target.exists():
                target, n = base.with_name(f"{folder.name}-{n}"), n + 1
            status = "copied"
        written = 0 if dry_run else _sync_tree(folder, target)
        if not dry_run:
            register(lib.paths.home, StoryProject(target))
            placed[str(folder)] = str(target)
        out.append({"from": str(folder), "to": str(target), "files": written, "status": status})
    ledger["stories"] = placed
    return out


# ---------------------------------------------------------------------- entry point


def merge_library(lib: Library, source: str | Path, *, dry_run: bool = False,
                  stories: list[str | Path] | None = None) -> dict[str, Any]:  # fmt: skip
    """Merge the library at `source` into `lib`. `stories` adds story folders that live outside both libraries."""
    root = Path(source).expanduser().resolve()
    if not (root / "catalog.sqlite3").is_file():
        raise InvalidInputError(f"{root} is not an AssetForge library (no catalog.sqlite3)", source=str(root))
    if root == lib.paths.home.resolve():
        raise InvalidInputError("A library can't be merged into itself", source=str(root))
    ledger_file = _ledger_path(lib, root)
    try:
        ledger = json.loads(ledger_file.read_text())
    except (OSError, ValueError):
        ledger = {}
    with tempfile.TemporaryDirectory(prefix="af-merge-") as scratch:
        try:
            src = _open_copy(root, Path(scratch))
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot read {root}: {exc}", source=str(root)) from exc
        try:
            work = _Merge(lib, src, root, ledger, dry_run)
            work.copy_files()
            if dry_run:
                lib.conn.execute("BEGIN IMMEDIATE")
                try:
                    work.run()
                finally:
                    lib.conn.execute("ROLLBACK")
                jobs = {}
            else:
                with db.transaction(lib.conn):
                    jobs = work.run()
                work.copy_job_dirs(jobs)
        finally:
            src.close()
    report = work.report
    report["stories"] = _merge_stories(lib, root, ledger, dry_run, [Path(p).expanduser() for p in stories or []])
    if not dry_run:
        ledger.update({"source": str(root), "merged_at": utcnow(), "ids": work.ids, "jobs": sorted(work.done_jobs)})
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        ledger_file.write_text(json.dumps(ledger, indent=1) + "\n")
    report["totals"] = {
        "assets": lib.conn.execute("SELECT count(*) FROM assets").fetchone()[0],
        "jobs": lib.conn.execute("SELECT count(*) FROM generation_jobs").fetchone()[0],
    }
    return report
