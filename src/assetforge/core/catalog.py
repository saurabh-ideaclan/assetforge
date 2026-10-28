"""The asset catalog: the Python API behind every CLI command.

An *asset* is a logical item (a panda idle sprite). A *file* is content on disk keyed by
its SHA-256. A *variant* binds a file to an asset under a label ("original", "webp_512").
Identical bytes are stored once and resolve to the asset that first registered them.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assetforge.config import LibraryPaths, resolve_home
from assetforge.core import collections as collections_mod
from assetforge.core import db, visual
from assetforge.core.classify import infer_type, path_tags
from assetforge.core.metadata import FileMeta, extract_metadata, is_importable
from assetforge.core.models import (
    Asset,
    AssetPatch,
    AssetSummary,
    Event,
    FileInfo,
    ImportItem,
    ImportOptions,
    ImportReport,
    LineageLink,
    LineageRelation,
    NewAsset,
    Origin,
    Scope,
    Status,
    Usage,
    Variant,
)
from assetforge.core.storage import ObjectStore, hash_file
from assetforge.core.util import (
    chunks,
    dumps,
    loads,
    normalize_key,
    normalize_name,
    normalize_optional_slug,
    normalize_slug,
    qmarks,
    utcnow,
)
from assetforge.errors import AssetForgeError, ConflictError, InvalidInputError, NotFoundError, StorageError

HISTORY_LIMIT = 50
DISCOVERY_FIELDS = ("category", "subcategory", "theme", "genre")  # kebab-case slugs, all searchable

_VARIANT_COLUMNS = """
    v.id, v.asset_id, v.label, v.parent_variant_id, v.params, v.original_filename, v.created_at,
    f.sha256, f.rel_path, f.ext, f.mime_type, f.kind, f.size_bytes, f.width, f.height,
    f.has_alpha, f.frames, f.duration_ms, f.sample_rate, f.channels,
    f.aspect, f.alpha_fraction, f.mean_luma, f.colors, f.dhash
"""


@dataclass(frozen=True)
class Registration:
    asset_id: str
    variant_id: str
    created: bool


def validate_scope(scope: Scope, project: str | None, style: str | None) -> None:
    if scope is Scope.PROJECT and not project:
        raise InvalidInputError("Project-scoped assets need a project", scope=scope.value)
    if scope is Scope.STYLE and not style:
        raise InvalidInputError("Style-scoped assets need a style", scope=scope.value)


def normalize_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    for tag in tags:
        value = normalize_slug(tag, "tag")
        if value not in result:
            result.append(value)
    return result


class Library:
    """Opens (and if needed creates) the library at `home`, or $ASSETFORGE_HOME, or ~/.assetforge."""

    def __init__(self, home: str | os.PathLike[str] | None = None, *, pooled: bool = False) -> None:
        self.paths = LibraryPaths(resolve_home(home))
        try:
            self.paths.ensure()
        except OSError as exc:
            raise StorageError(f"Cannot create library at {self.paths.home}: {exc}", home=str(self.paths.home)) from exc
        self.store = ObjectStore(self.paths)
        self.conn = db.connect(self.paths.catalog, check_same_thread=not pooled)
        self.schema_version = db.migrate(self.conn)
        if self._fts_out_of_step():
            self.rebuild_fts()

    def _fts_out_of_step(self) -> bool:
        assets = self.conn.execute("SELECT count(*) FROM assets").fetchone()[0]
        return assets != self.conn.execute("SELECT count(*) FROM assets_fts").fetchone()[0]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Library:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ info

    def info(self) -> dict[str, Any]:
        def scalar(sql: str) -> int:
            return self.conn.execute(sql).fetchone()[0]

        def grouped(column: str) -> dict[str, int]:
            rows = self.conn.execute(
                f"SELECT {column}, count(*) FROM assets GROUP BY {column} ORDER BY count(*) DESC, {column}"
            )
            return {row[0]: row[1] for row in rows}

        return {
            "home": str(self.paths.home),
            "catalog": str(self.paths.catalog),
            "objects": str(self.paths.objects),
            "schema_version": self.schema_version,
            "counts": {
                "assets": scalar("SELECT count(*) FROM assets"),
                "variants": scalar("SELECT count(*) FROM asset_variants"),
                "files": scalar("SELECT count(*) FROM asset_files"),
                "collections": scalar("SELECT count(*) FROM collections"),
                "tags": scalar("SELECT count(*) FROM tags"),
            },
            "stored_bytes": scalar("SELECT ifnull(sum(size_bytes), 0) FROM asset_files"),
            "by_type": grouped("type"),
            "by_status": grouped("status"),
        }

    # ------------------------------------------------------------------ import

    def import_paths(self, paths: list[str | os.PathLike[str]], options: ImportOptions | None = None) -> ImportReport:
        opts = options or ImportOptions()
        project = normalize_optional_slug(opts.project, "project")
        style = normalize_optional_slug(opts.style, "style")
        validate_scope(opts.scope, project, style)
        opts = opts.model_copy(update={"project": project, "style": style, "tags": normalize_tags(opts.tags)})

        files, ignored = self._collect_import_files([Path(p) for p in paths], opts)
        if (opts.name or opts.key) and len(files) != 1:
            raise InvalidInputError(
                "--name and --key can only be used when importing exactly one file", files=len(files)
            )
        collection_id = None
        if opts.collection and not opts.dry_run:
            collection_id = collections_mod.ensure_collection(self, opts.collection, project=project).id

        items: list[ImportItem] = []
        planned: dict[str, str] = {}  # sha256 -> path, so dry runs also report in-batch duplicates
        for path, root in files:
            try:
                items.append(self._import_one(path, root, opts, collection_id, planned))
            except AssetForgeError as exc:
                items.append(ImportItem(path=str(path), action="error", error=exc.to_dict()))
        return ImportReport.build(items, ignored=ignored, dry_run=opts.dry_run, collection=opts.collection)

    def _collect_import_files(self, paths: list[Path], opts: ImportOptions) -> tuple[list[tuple[Path, Path]], int]:
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise NotFoundError(f"Path not found: {', '.join(missing)}", paths=missing)
        files: list[tuple[Path, Path]] = []
        seen: set[Path] = set()
        ignored = 0
        for raw in paths:
            path = raw.expanduser().resolve()
            if path.is_file():
                if path not in seen:
                    seen.add(path)
                    files.append((path, path.parent))
                continue
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")) if opts.recursive else []
                for filename in sorted(filenames):
                    file = Path(dirpath) / filename
                    if filename.startswith(".") or file.is_symlink() or file in seen:
                        continue
                    if not opts.all_files and not is_importable(file.suffix):
                        ignored += 1
                        continue
                    seen.add(file)
                    files.append((file, path))
        return files, ignored

    def _import_one(
        self, path: Path, root: Path, opts: ImportOptions, collection_id: str | None, planned: dict[str, str]
    ) -> ImportItem:
        ext = path.suffix.lower()
        if not opts.all_files and not is_importable(ext):
            return ImportItem(path=str(path), action="skipped", reason=f"unsupported file type '{ext or path.name}'")
        sha256 = hash_file(path)
        existing = self._variant_for_file(sha256)
        if existing:
            if collection_id:
                collections_mod.add_asset_ids(self, collection_id, [existing[0]])
            return ImportItem(
                path=str(path), action="duplicate", asset_id=existing[0], variant_id=existing[1],
                sha256=sha256, duplicate_of=existing[0],
            )  # fmt: skip
        if sha256 in planned:
            return ImportItem(path=str(path), action="duplicate", sha256=sha256, reason=f"same content as {planned[sha256]}")

        meta = extract_metadata(path, ext)
        dir_parts = path.relative_to(root).parent.parts
        asset_type = opts.type or infer_type(path.stem, dir_parts, meta)
        name = normalize_name(opts.name or path.stem)
        tags = normalize_tags(opts.tags + (path_tags(dir_parts) if opts.auto_tags else []))
        if opts.dry_run:
            planned[sha256] = str(path)
            return ImportItem(path=str(path), action="would_create", sha256=sha256, name=name, type=asset_type, tags=tags)

        spec = NewAsset(
            name=name,
            type=asset_type,
            key=opts.key,
            description=opts.description,
            scope=opts.scope,
            project=opts.project,
            style=opts.style,
            category=opts.category,
            subcategory=opts.subcategory,
            theme=opts.theme,
            genre=opts.genre,
            status=opts.status,
            origin=Origin.IMPORT,
            license=opts.license,
            commercial_use=opts.commercial_use,
            attribution=opts.attribution,
            source_uri=path.as_uri(),
            tags=tags,
        )
        reg = self.register_file(
            path, spec, meta=meta, sha256=sha256, original_filename=path.name, collection_id=collection_id
        )
        if not reg.created:  # another process registered the same bytes a moment ago
            return ImportItem(
                path=str(path), action="duplicate", asset_id=reg.asset_id, variant_id=reg.variant_id,
                sha256=sha256, duplicate_of=reg.asset_id,
            )  # fmt: skip
        return ImportItem(
            path=str(path), action="created", asset_id=reg.asset_id, variant_id=reg.variant_id,
            sha256=sha256, name=name, type=asset_type, tags=tags,
        )  # fmt: skip

    # ------------------------------------------------------------------ registration

    def register_file(
        self,
        src: Path,
        spec: NewAsset,
        *,
        meta: FileMeta | None = None,
        sha256: str | None = None,
        original_filename: str | None = None,
        label: str = "original",
        params: dict[str, Any] | None = None,
        collection_id: str | None = None,
        dedupe: bool = True,
    ) -> Registration:
        """Store `src` and register it as a new asset (the entry point for importers and generators).

        With `dedupe`, content that is already cataloged returns the existing asset instead.
        """
        ext = src.suffix.lower()
        meta = meta or extract_metadata(src, ext)
        name = normalize_name(spec.name)
        key = normalize_key(spec.key)
        project = normalize_optional_slug(spec.project, "project")
        style = normalize_optional_slug(spec.style, "style")
        validate_scope(spec.scope, project, style)
        tags = normalize_tags(spec.tags)

        rel_path, sha256, size = self._store(src, ext, sha256)
        now = utcnow()
        with db.transaction(self.conn):
            if dedupe and (existing := self._variant_for_file(sha256)):
                if collection_id:
                    collections_mod.add_asset_ids(self, collection_id, [existing[0]], in_transaction=True)
                return Registration(existing[0], existing[1], created=False)
            if key:
                self._check_key_free(key, project)
            self._upsert_file(sha256, rel_path, ext, size, meta, now)
            asset_id = db.next_id(self.conn, "ast")
            variant_id = db.next_id(self.conn, "var")
            self.conn.execute(
                """
                INSERT INTO assets (id, key, name, type, description, scope, project, style, status, origin,
                                    license, commercial_use, attribution, source_uri, primary_variant_id,
                                    metadata, created_at, updated_at, category, subcategory, theme, genre,
                                    parent_asset_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id, key, name, spec.type.value, spec.description, spec.scope.value, project, style,
                    spec.status.value, spec.origin.value, spec.license, spec.commercial_use.value,
                    spec.attribution, spec.source_uri, variant_id, dumps(spec.metadata), now, now,
                    *(normalize_optional_slug(getattr(spec, f), f) for f in DISCOVERY_FIELDS),
                    spec.parent_asset_id,
                ),
            )  # fmt: skip
            self.conn.execute(
                """
                INSERT INTO asset_variants (id, asset_id, file_sha256, label, params, original_filename, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (variant_id, asset_id, sha256, normalize_name(label), dumps(params or {}), original_filename, now),
            )
            self._add_tags(asset_id, tags)
            self._refresh_fts(asset_id)
            self._event(asset_id, "created", {"origin": spec.origin.value, "variant_id": variant_id})
            if collection_id:
                collections_mod.add_asset_ids(self, collection_id, [asset_id], in_transaction=True)
        return Registration(asset_id, variant_id, created=True)

    def add_variant(
        self,
        ref: str,
        src: Path,
        *,
        label: str,
        parent_variant_id: str | None = None,
        params: dict[str, Any] | None = None,
        make_primary: bool = False,
    ) -> Variant:
        """Attach new content (a processed or regenerated rendering) to an existing asset."""
        asset_id = self.resolve_asset_id(ref)
        label = normalize_name(label)
        ext = src.suffix.lower()
        meta = extract_metadata(src, ext)
        rel_path, sha256, size = self._store(src, ext, None)
        now = utcnow()
        with db.transaction(self.conn):
            if self.conn.execute(
                "SELECT 1 FROM asset_variants WHERE asset_id = ? AND label = ?", (asset_id, label)
            ).fetchone():
                raise ConflictError(f"Asset {asset_id} already has a variant labelled {label!r}", label=label)
            if parent_variant_id and not self.conn.execute(
                "SELECT 1 FROM asset_variants WHERE id = ? AND asset_id = ?", (parent_variant_id, asset_id)
            ).fetchone():
                raise NotFoundError(f"Variant {parent_variant_id} does not belong to {asset_id}")
            self._upsert_file(sha256, rel_path, ext, size, meta, now)
            variant_id = db.next_id(self.conn, "var")
            self.conn.execute(
                """
                INSERT INTO asset_variants (id, asset_id, file_sha256, label, parent_variant_id, params,
                                            original_filename, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (variant_id, asset_id, sha256, label, parent_variant_id, dumps(params or {}), src.name, now),
            )
            if make_primary:
                self.conn.execute("UPDATE assets SET primary_variant_id = ? WHERE id = ?", (variant_id, asset_id))
                self._refresh_fts(asset_id)
            self.conn.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (now, asset_id))
            self._event(asset_id, "variant_added", {"variant_id": variant_id, "label": label})
        return self._variants("v.id IN (?)", [variant_id])[0]

    def _store(self, src: Path, ext: str, sha256: str | None) -> tuple[str, str, int]:
        if sha256:
            row = self.conn.execute("SELECT rel_path, size_bytes FROM asset_files WHERE sha256 = ?", (sha256,)).fetchone()
            if row and self.store.abs_path(row["rel_path"]).is_file():
                return row["rel_path"], sha256, row["size_bytes"]
        stored = self.store.put_file(src, ext, expected_sha256=sha256)
        return self.store.rel_path(stored.path), stored.sha256, stored.size_bytes

    def _upsert_file(self, sha256: str, rel_path: str, ext: str, size: int, meta: FileMeta, now: str) -> None:
        vis = meta.visual or {}
        self.conn.execute(
            """
            INSERT INTO asset_files (sha256, rel_path, ext, mime_type, kind, size_bytes, width, height, has_alpha,
                                     frames, duration_ms, sample_rate, channels, extra, created_at,
                                     aspect, alpha_fraction, mean_luma, colors, dhash, audio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (sha256) DO UPDATE SET rel_path = excluded.rel_path,
                aspect = ifnull(asset_files.aspect, excluded.aspect),
                alpha_fraction = ifnull(asset_files.alpha_fraction, excluded.alpha_fraction),
                mean_luma = ifnull(asset_files.mean_luma, excluded.mean_luma),
                colors = ifnull(asset_files.colors, excluded.colors),
                dhash = ifnull(asset_files.dhash, excluded.dhash),
                audio = ifnull(asset_files.audio, excluded.audio)
            """,
            (
                sha256, rel_path, ext, meta.mime_type, meta.kind.value, size, meta.width, meta.height,
                None if meta.has_alpha is None else int(meta.has_alpha), meta.frames, meta.duration_ms,
                meta.sample_rate, meta.channels, dumps(meta.extra), now,
                vis.get("aspect"), vis.get("alpha_fraction"), vis.get("mean_luma"),
                dumps(vis["colors"]) if "colors" in vis else None, vis.get("dhash"),
                dumps(meta.audio) if meta.audio else None,
            ),
        )  # fmt: skip

    def _variant_for_file(self, sha256: str) -> tuple[str, str] | None:
        row = self.conn.execute(
            """
            SELECT asset_id, id FROM asset_variants WHERE file_sha256 = ?
            ORDER BY label = 'original' DESC, created_at, id LIMIT 1
            """,
            (sha256,),
        ).fetchone()
        return (row["asset_id"], row["id"]) if row else None

    def _check_key_free(self, key: str, project: str | None, *, except_id: str | None = None) -> None:
        row = self.conn.execute(
            "SELECT id FROM assets WHERE key = ? AND ifnull(project, '') = ? AND id != ifnull(?, '')",
            (key, project or "", except_id),
        ).fetchone()
        if row:
            raise ConflictError(
                f"Key {key!r} is already used by {row['id']}" + (f" in project {project}" if project else ""),
                key=key,
                asset_id=row["id"],
            )

    # ------------------------------------------------------------------ lookup

    def resolve_asset_id(self, ref: str, *, project: str | None = None) -> str:
        """Accepts an asset ID (ast_…), a variant ID (var_…), a key, or 'project:key'."""
        ref = ref.strip()
        if ref.startswith("ast_"):
            row = self.conn.execute("SELECT id FROM assets WHERE id = ?", (ref,)).fetchone()
        elif ref.startswith("var_"):
            row = self.conn.execute("SELECT asset_id AS id FROM asset_variants WHERE id = ?", (ref,)).fetchone()
        else:
            key = ref
            if project is None and ":" in ref:
                project, key = ref.split(":", 1)
            sql, params = "SELECT id, project FROM assets WHERE key = ?", [normalize_key(key) or ""]
            if project is not None:
                sql += " AND ifnull(project, '') = ?"
                params.append(normalize_optional_slug(project, "project") or "")
            rows = self.conn.execute(sql, params).fetchall()
            if len(rows) > 1:
                raise ConflictError(
                    f"Key {key!r} exists in several projects; pass a project",
                    candidates=[{"asset_id": r["id"], "project": r["project"]} for r in rows],
                )
            row = rows[0] if rows else None
        if row is None:
            raise NotFoundError(f"No asset matches {ref!r}", ref=ref)
        return row["id"]

    def get_asset(self, ref: str, *, project: str | None = None) -> Asset:
        asset_id = self.resolve_asset_id(ref, project=project)
        row = self.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        base = self._summary_from_row(row, self._tags_for([asset_id]).get(asset_id, []), None,
                                      self._child_counts([asset_id]).get(asset_id, 0))
        variants = self._variants("v.asset_id = ?", [asset_id])
        primary = next((v for v in variants if v.id == row["primary_variant_id"]), None)
        collections = [
            r["name"]
            for r in self.conn.execute(
                "SELECT c.name FROM collection_assets ca JOIN collections c ON c.id = ca.collection_id "
                "WHERE ca.asset_id = ? ORDER BY c.name",
                (asset_id,),
            )
        ]
        return Asset(
            **base.model_dump(exclude={"primary_variant", "path"}),
            primary_variant=primary,
            path=primary.file.path if primary else None,
            attribution=row["attribution"],
            source_uri=row["source_uri"],
            metadata=loads(row["metadata"], {}),
            variants=variants,
            collections=collections,
            lineage=self._lineage(asset_id),
            usage=[Usage(**dict(r)) for r in self.conn.execute(
                "SELECT * FROM project_usage WHERE asset_id = ? ORDER BY project", (asset_id,)
            )],  # fmt: skip
            history=[
                Event(event=r["event"], data=loads(r["data"], {}), created_at=r["created_at"])
                for r in self.conn.execute(
                    "SELECT * FROM asset_events WHERE asset_id = ? ORDER BY id DESC LIMIT ?",
                    (asset_id, HISTORY_LIMIT),
                )
            ],
        )

    def summaries(self, asset_ids: list[str]) -> list[AssetSummary]:
        """Compact records for many assets, in the given order (used by search and listings)."""
        if not asset_ids:
            return []
        rows: dict[str, sqlite3.Row] = {}
        for batch in chunks(asset_ids):
            for row in self.conn.execute(f"SELECT * FROM assets WHERE id IN ({qmarks(len(batch))})", batch):
                rows[row["id"]] = row
        tags = self._tags_for(asset_ids)
        children = self._child_counts(asset_ids)
        primary_ids = [r["primary_variant_id"] for r in rows.values() if r["primary_variant_id"]]
        primaries = {v.id: v for v in self._variants_by_ids(primary_ids)}
        return [
            self._summary_from_row(rows[i], tags.get(i, []), primaries.get(rows[i]["primary_variant_id"]), children.get(i, 0))
            for i in asset_ids
            if i in rows
        ]

    def _child_counts(self, asset_ids: list[str]) -> dict[str, int]:
        """How many non-archived parts (sliced frames) each asset has."""
        counts: dict[str, int] = {}
        for batch in chunks(asset_ids):
            for row in self.conn.execute(
                f"SELECT parent_asset_id, count(*) FROM assets WHERE parent_asset_id IN ({qmarks(len(batch))}) "
                "AND status != 'archived' GROUP BY parent_asset_id",
                batch,
            ):
                counts[row[0]] = row[1]
        return counts

    def children(self, ref: str, *, include_archived: bool = False) -> list[str]:
        asset_id = self.resolve_asset_id(ref)
        sql = "SELECT id FROM assets WHERE parent_asset_id = ?" + ("" if include_archived else " AND status != 'archived'")
        return [r[0] for r in self.conn.execute(sql + " ORDER BY id", (asset_id,))]

    def set_parent(self, ref: str, parent_ref: str | None) -> None:
        asset_id = self.resolve_asset_id(ref)
        parent = self.resolve_asset_id(parent_ref) if parent_ref else None
        if parent == asset_id:
            raise InvalidInputError("An asset cannot be its own parent")
        with db.transaction(self.conn):
            self.conn.execute("UPDATE assets SET parent_asset_id = ? WHERE id = ?", (parent, asset_id))

    def _summary_from_row(self, row: sqlite3.Row, tags: list[str], primary: Variant | None,
                          children: int = 0) -> AssetSummary:  # fmt: skip
        return AssetSummary(
            id=row["id"],
            key=row["key"],
            name=row["name"],
            type=row["type"],
            description=row["description"],
            scope=row["scope"],
            project=row["project"],
            style=row["style"],
            category=row["category"],
            subcategory=row["subcategory"],
            theme=row["theme"],
            genre=row["genre"],
            parent_asset_id=row["parent_asset_id"],
            children=children,
            status=row["status"],
            origin=row["origin"],
            license=row["license"],
            commercial_use=row["commercial_use"],
            tags=tags,
            path=primary.file.path if primary else None,
            primary_variant=primary,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _tags_for(self, asset_ids: list[str]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for batch in chunks(asset_ids):
            for row in self.conn.execute(
                f"SELECT at.asset_id, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                f"WHERE at.asset_id IN ({qmarks(len(batch))}) ORDER BY t.name",
                batch,
            ):
                result.setdefault(row["asset_id"], []).append(row["name"])
        return result

    def _variants_by_ids(self, variant_ids: list[str]) -> list[Variant]:
        variants: list[Variant] = []
        for batch in chunks(variant_ids):
            variants += self._variants(f"v.id IN ({qmarks(len(batch))})", batch)
        return variants

    def _variants(self, where: str, params: list[Any]) -> list[Variant]:
        rows = self.conn.execute(
            f"SELECT {_VARIANT_COLUMNS} FROM asset_variants v JOIN asset_files f ON f.sha256 = v.file_sha256 "
            f"WHERE {where} ORDER BY v.created_at, v.id",
            params,
        )
        return [self._variant_from_row(row) for row in rows]

    def _variant_from_row(self, row: sqlite3.Row) -> Variant:
        file = FileInfo(
            sha256=row["sha256"],
            path=str(self.store.abs_path(row["rel_path"])),
            rel_path=row["rel_path"],
            ext=row["ext"],
            mime_type=row["mime_type"],
            kind=row["kind"],
            size_bytes=row["size_bytes"],
            width=row["width"],
            height=row["height"],
            has_alpha=None if row["has_alpha"] is None else bool(row["has_alpha"]),
            frames=row["frames"],
            duration_ms=row["duration_ms"],
            sample_rate=row["sample_rate"],
            channels=row["channels"],
            aspect=row["aspect"],
            alpha_fraction=row["alpha_fraction"],
            mean_luma=row["mean_luma"],
            colors=loads(row["colors"], None) if row["colors"] else None,
            dhash=row["dhash"],
        )
        return Variant(
            id=row["id"],
            asset_id=row["asset_id"],
            label=row["label"],
            parent_variant_id=row["parent_variant_id"],
            params=loads(row["params"], {}),
            original_filename=row["original_filename"],
            created_at=row["created_at"],
            file=file,
        )

    def _lineage(self, asset_id: str) -> list[LineageLink]:
        links = [
            LineageLink(
                relation=r["relation"], direction="outgoing", asset_id=r["related_asset_id"],
                variant_id=r["related_variant_id"], created_at=r["created_at"],
            )  # fmt: skip
            for r in self.conn.execute("SELECT * FROM asset_lineage WHERE asset_id = ? ORDER BY id", (asset_id,))
        ]
        links += [
            LineageLink(
                relation=r["relation"], direction="incoming", asset_id=r["asset_id"],
                variant_id=r["variant_id"], created_at=r["created_at"],
            )  # fmt: skip
            for r in self.conn.execute(
                "SELECT * FROM asset_lineage WHERE related_asset_id = ? ORDER BY id", (asset_id,)
            )
        ]
        return links

    # ------------------------------------------------------------------ edits

    def update_asset(self, ref: str, patch: AssetPatch) -> Asset:
        asset_id = self.resolve_asset_id(ref)
        changes = patch.model_dump(exclude_unset=True, exclude={"add_tags", "remove_tags", "metadata"})
        if "name" in changes:
            changes["name"] = normalize_name(changes["name"] or "")
        if "key" in changes:
            changes["key"] = normalize_key(changes["key"])
        for field in ("project", "style", *DISCOVERY_FIELDS):
            if field in changes:
                changes[field] = normalize_optional_slug(changes[field], field)
        for field in ("license", "attribution"):
            if field in changes and not (changes[field] or "").strip():
                changes[field] = None
        for field in ("type", "scope", "commercial_use"):
            if field in changes:
                if changes[field] is None:
                    raise InvalidInputError(f"{field} cannot be cleared")
                changes[field] = changes[field].value
        add_tags = normalize_tags(patch.add_tags)
        remove_tags = normalize_tags(patch.remove_tags)

        with db.transaction(self.conn):
            row = self.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            merged = {**dict(row), **changes}
            validate_scope(Scope(merged["scope"]), merged["project"], merged["style"])
            if merged["key"] and ("key" in changes or "project" in changes):
                self._check_key_free(merged["key"], merged["project"], except_id=asset_id)
            if patch.metadata:
                changes["metadata"] = dumps({**loads(row["metadata"], {}), **patch.metadata})
            changes = {k: v for k, v in changes.items() if row[k] != v}
            if changes or add_tags or remove_tags:
                changes["updated_at"] = utcnow()
                assignments = ", ".join(f"{column} = ?" for column in changes)
                self.conn.execute(f"UPDATE assets SET {assignments} WHERE id = ?", [*changes.values(), asset_id])
                self._add_tags(asset_id, add_tags)
                self._remove_tags(asset_id, remove_tags)
                self._refresh_fts(asset_id)
                event = {k: v for k, v in changes.items() if k not in ("updated_at", "metadata")}
                if add_tags:
                    event["add_tags"] = add_tags
                if remove_tags:
                    event["remove_tags"] = remove_tags
                if "metadata" in changes:
                    event["metadata_keys"] = sorted(patch.metadata)
                self._event(asset_id, "updated", event)
        return self.get_asset(asset_id)

    def set_primary_variant(self, ref: str, variant_id: str) -> None:
        asset_id = self.resolve_asset_id(ref)
        with db.transaction(self.conn):
            if not self.conn.execute(
                "SELECT 1 FROM asset_variants WHERE id = ? AND asset_id = ?", (variant_id, asset_id)
            ).fetchone():
                raise NotFoundError(f"Variant {variant_id} does not belong to {asset_id}")
            self.conn.execute(
                "UPDATE assets SET primary_variant_id = ?, updated_at = ? WHERE id = ?", (variant_id, utcnow(), asset_id)
            )
            self._refresh_fts(asset_id)
            self._event(asset_id, "primary_changed", {"variant_id": variant_id})

    def merge_metadata(self, ref: str, values: dict[str, Any], *, event: str | None = None) -> None:
        """Shallow-merge keys into an asset's metadata (used for QC results and similar annotations)."""
        asset_id = self.resolve_asset_id(ref)
        with db.transaction(self.conn):
            row = self.conn.execute("SELECT metadata FROM assets WHERE id = ?", (asset_id,)).fetchone()
            merged = {**loads(row["metadata"], {}), **values}
            self.conn.execute("UPDATE assets SET metadata = ? WHERE id = ?", (dumps(merged), asset_id))
            if event:
                self._event(asset_id, event, {k: v for k, v in values.items() if not isinstance(v, dict)})

    def set_status(self, refs: list[str], status: Status, *, note: str | None = None) -> list[str]:
        asset_ids = [self.resolve_asset_id(ref) for ref in refs]
        now = utcnow()
        with db.transaction(self.conn):
            for asset_id in asset_ids:
                previous = self.conn.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()[0]
                self.conn.execute(
                    "UPDATE assets SET status = ?, updated_at = ? WHERE id = ?", (status.value, now, asset_id)
                )
                data = {"from": previous, "to": status.value}
                if note:
                    data["note"] = note
                self._event(asset_id, "reviewed", data)
        return asset_ids

    def record_usage(
        self, ref: str, project: str, *, variant_id: str | None = None, usage_key: str | None = None
    ) -> Usage:
        asset_id = self.resolve_asset_id(ref)
        project = normalize_slug(project, "project")
        if ref.startswith("var_") and variant_id is None:
            variant_id = ref
        row = self.conn.execute("SELECT primary_variant_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        variant_id = variant_id or row["primary_variant_id"]
        if not self.conn.execute(
            "SELECT 1 FROM asset_variants WHERE id = ? AND asset_id = ?", (variant_id, asset_id)
        ).fetchone():
            raise NotFoundError(f"Variant {variant_id} does not belong to {asset_id}")
        now = utcnow()
        with db.transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO project_usage (project, asset_id, variant_id, usage_key, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (project, asset_id, variant_id) DO UPDATE SET usage_key = excluded.usage_key
                """,
                (project, asset_id, variant_id, usage_key, now),
            )
            self._event(asset_id, "used", {"project": project, "variant_id": variant_id, "usage_key": usage_key})
        row = self.conn.execute(
            "SELECT * FROM project_usage WHERE project = ? AND asset_id = ? AND variant_id = ?",
            (project, asset_id, variant_id),
        ).fetchone()
        return Usage(**dict(row))

    def project_usage(self, project: str) -> list[tuple[Usage, AssetSummary]]:
        project = normalize_slug(project, "project")
        usages = [
            Usage(**dict(r))
            for r in self.conn.execute(
                "SELECT * FROM project_usage WHERE project = ? ORDER BY created_at, asset_id", (project,)
            )
        ]
        summaries = {s.id: s for s in self.summaries(list(dict.fromkeys(u.asset_id for u in usages)))}
        return [(u, summaries[u.asset_id]) for u in usages if u.asset_id in summaries]

    def add_lineage(
        self,
        ref: str,
        related_ref: str,
        relation: LineageRelation,
        *,
        variant_id: str | None = None,
        related_variant_id: str | None = None,
    ) -> None:
        """Record that `ref` relates to `related_ref`, e.g. ast_2 derived_from ast_1."""
        asset_id = self.resolve_asset_id(ref)
        related_id = self.resolve_asset_id(related_ref)
        if asset_id == related_id:
            raise InvalidInputError("An asset cannot be related to itself")
        with db.transaction(self.conn):
            self.conn.execute(
                """
                INSERT OR IGNORE INTO asset_lineage
                    (asset_id, related_asset_id, relation, variant_id, related_variant_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (asset_id, related_id, relation.value, variant_id, related_variant_id, utcnow()),
            )

    # ------------------------------------------------------------------ integrity

    def verify(self, *, rehash: bool = False) -> dict[str, Any]:
        """Check every cataloged file is present (and, with `rehash`, unmodified)."""
        issues: list[dict[str, Any]] = []
        known: set[str] = set()
        rows = self.conn.execute("SELECT sha256, rel_path, size_bytes FROM asset_files ORDER BY sha256").fetchall()
        for row in rows:
            path = self.store.abs_path(row["rel_path"])
            known.add(row["rel_path"])
            if not path.is_file():
                issues.append({"sha256": row["sha256"], "path": str(path), "problem": "missing"})
            elif path.stat().st_size != row["size_bytes"]:
                issues.append({"sha256": row["sha256"], "path": str(path), "problem": "size_mismatch"})
            elif rehash and hash_file(path) != row["sha256"]:
                issues.append({"sha256": row["sha256"], "path": str(path), "problem": "hash_mismatch"})
        orphans = [str(p) for p in self.store.iter_objects() if self.store.rel_path(p) not in known]
        return {
            "ok": not issues,
            "checked": len(rows),
            "rehashed": rehash,
            "issues": issues,
            "orphan_objects": orphans,
        }

    # ------------------------------------------------------------------ internals

    def _add_tags(self, asset_id: str, tags: list[str]) -> None:
        for tag in tags:
            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            self.conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) SELECT ?, id FROM tags WHERE name = ?",
                (asset_id, tag),
            )

    def _remove_tags(self, asset_id: str, tags: list[str]) -> None:
        for tag in tags:
            self.conn.execute(
                "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)",
                (asset_id, tag),
            )

    def _refresh_fts(self, asset_id: str) -> None:
        row = self.conn.execute(
            "SELECT a.name, a.key, a.description, a.type, a.style, a.category, a.subcategory, a.theme, a.genre, "
            "f.kind, f.ext, f.has_alpha, f.aspect, f.mean_luma, f.colors, f.audio, "
            "json_extract(a.metadata, '$.audio.loop') AS loop FROM assets a "
            "LEFT JOIN asset_variants v ON v.id = a.primary_variant_id "
            "LEFT JOIN asset_files f ON f.sha256 = v.file_sha256 WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        tags = self._tags_for([asset_id]).get(asset_id, [])

        def words(value: str | None) -> str:
            return (value or "").replace("-", " ").replace("_", " ")

        self.conn.execute("DELETE FROM assets_fts WHERE asset_id = ?", (asset_id,))
        self.conn.execute(
            "INSERT INTO assets_fts (asset_id, name, key, tags, description, type, style, category, theme, genre, "
            "colors, tech) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                row["name"].replace("_", " "),
                (row["key"] or "").replace("/", " ").replace("_", " "),
                " ".join(t.replace("-", " ") for t in tags),
                row["description"],
                row["type"],
                words(row["style"]),
                f"{words(row['category'])} {words(row['subcategory'])}".strip(),
                words(row["theme"]),
                words(row["genre"]),
                visual.color_terms(row["colors"]),
                visual.tech_terms(row),
            ),
        )

    def rebuild_fts(self) -> int:
        """Re-index every asset's text (after a schema change or `reindex`)."""
        with db.transaction(self.conn):
            self.conn.execute("DELETE FROM assets_fts")
            ids = [r[0] for r in self.conn.execute("SELECT id FROM assets ORDER BY id")]
            for asset_id in ids:
                self._refresh_fts(asset_id)
        return len(ids)

    def _event(self, asset_id: str, event: str, data: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO asset_events (asset_id, event, data, created_at) VALUES (?, ?, ?, ?)",
            (asset_id, event, dumps(data), utcnow()),
        )
