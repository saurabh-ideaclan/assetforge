"""Browsing the whole library: filtered, sorted, paginated listings and filter facets.

Unlike `search` (built for agents: best matches only, visibility rules, approved/candidate by
default), browsing shows everything a person might look for, including rejected and archived
assets, with exact-match filters and a total count for pagination.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from assetforge.core.catalog import Library
from assetforge.core.models import AssetSummary, AssetType, FileKind, Origin, Status
from assetforge.core.search import SearchQuery, search
from assetforge.core.search import _filters as search_filters
from assetforge.core.util import normalize_optional_slug, normalize_slug, qmarks
from assetforge.errors import InvalidInputError

SORTS = {
    "newest": "a.created_at DESC, a.id DESC",
    "oldest": "a.created_at, a.id",
    "updated": "a.updated_at DESC, a.id DESC",
    "name": "a.name, a.id",
    "type": "a.type, a.name, a.id",
}
SEARCH_POOL = 500


class BrowseFilters(BaseModel):
    q: str = ""
    types: list[AssetType] = Field(default_factory=list)
    statuses: list[Status] = Field(default_factory=list)
    kind: FileKind | None = None
    origins: list[Origin] = Field(default_factory=list)
    project: str | None = None
    style: str | None = None
    collection: str | None = None
    tag: str | None = None
    category: str | None = None
    genre: str | None = None
    theme: str | None = None
    color: str | None = None
    orientation: str | None = None
    tone: str | None = None
    transparent: bool | None = None
    similar: str | None = None  # rank by similarity (look or sound) to this asset
    bpm: float | None = None  # ±8
    key: str | None = None  # "A minor" or "minor"
    loopable: bool | None = None
    channels: int | None = None
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    group: bool = False  # show parts (sliced frames) as their parent sheet instead of separately
    parent: str | None = None  # only the parts of this asset


def _where(lib: Library, f: BrowseFilters) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    for column, values in (("a.type", f.types), ("a.status", f.statuses), ("a.origin", f.origins)):
        if values:
            where.append(f"{column} IN ({qmarks(len(values))})")
            params += [v.value for v in values]
    if not f.statuses:
        where.append("a.status != 'archived'")  # the archive is its own view (status=archived)
    if f.kind:
        where.append("pf.kind = ?")
        params.append(f.kind.value)
    if project := normalize_optional_slug(f.project, "project"):
        where.append("a.project = ?")
        params.append(project)
    for field in ("style", "category", "genre", "theme"):
        if value := normalize_optional_slug(getattr(f, field), field):
            where.append(f"a.{field} = ?")
            params.append(value)
    if f.collection:
        where.append("a.id IN (SELECT ca.asset_id FROM collection_assets ca JOIN collections c ON c.id = ca.collection_id "
                     "WHERE c.name = ? OR c.id = ?)")  # fmt: skip
        params += [normalize_slug(f.collection, "collection"), f.collection]
    if f.tag:
        where.append("EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                     "WHERE at.asset_id = a.id AND t.name = ?)")  # fmt: skip
        params.append(normalize_slug(f.tag, "tag"))
    if f.parent:
        where.append("a.parent_asset_id = ?")
        params.append(lib.resolve_asset_id(f.parent))
    if f.transparent is not None:
        where.append("pf.has_alpha = ?")
        params.append(int(f.transparent))
    from assetforge.core.search import audio_filters

    audio_where, audio_params = audio_filters(f.bpm, 8.0, f.key, f.loopable, f.channels)
    where += audio_where
    params += audio_params
    for clause, value in (("pf.duration_ms >= ?", f.min_duration_ms), ("pf.duration_ms <= ?", f.max_duration_ms)):
        if value is not None:
            where.append(clause)
            params.append(value)
    # colour/orientation/tone use the same SQL as search
    visual_q = SearchQuery(colors=[f.color] if f.color else [], orientation=f.orientation or None, tone=f.tone or None,
                           statuses=list(Status), include_restricted=True)  # fmt: skip
    extra_where, extra_params = search_filters(lib, visual_q)
    keep = [i for i, w in enumerate(extra_where) if "pf.colors" in w or "pf.aspect" in w or "pf.mean_luma" in w]
    where += [extra_where[i] for i in keep]
    params += [p for p in _params_for(extra_where, extra_params, keep)]
    return where, params


def _params_for(where: list[str], params: list[Any], keep: list[int]) -> list[Any]:
    """The parameters belonging to the kept WHERE clauses (each clause owns as many params as it has `?`)."""
    out, cursor = [], 0
    for i, clause in enumerate(where):
        n = clause.count("?")
        if i in keep:
            out += params[cursor : cursor + n]
        cursor += n
    return out


_FROM = ("FROM assets a LEFT JOIN asset_variants pv ON pv.id = a.primary_variant_id "
         "LEFT JOIN asset_files pf ON pf.sha256 = pv.file_sha256")  # fmt: skip


def browse(lib: Library, f: BrowseFilters, *, sort: str = "newest", offset: int = 0,
           limit: int = 60) -> tuple[int, list[AssetSummary]]:  # fmt: skip
    if sort not in SORTS:
        raise InvalidInputError(f"Unknown sort {sort!r}", allowed=list(SORTS))
    if not 1 <= limit <= 500 or offset < 0:
        raise InvalidInputError("limit must be 1-500 and offset >= 0")
    ids = _matching_ids(lib, f, sort, None if (f.group and not f.parent) else offset + limit)
    if f.group and not f.parent:
        ids = _group_under_parents(lib, ids, archived_view=Status.ARCHIVED in f.statuses)
    total = len(ids) if (f.group and not f.parent) or f.similar or f.q.strip() else _count(lib, f)
    return total, lib.summaries(ids[offset : offset + limit])


def _count(lib: Library, f: BrowseFilters) -> int:
    where, params = _where(lib, f)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return lib.conn.execute(f"SELECT count(*) {_FROM} {clause}", params).fetchone()[0]


def _matching_ids(lib: Library, f: BrowseFilters, sort: str, upto: int | None) -> list[str]:
    """Ids of every match in display order (`upto` caps the plain SQL listing when nothing is regrouped)."""
    where, params = _where(lib, f)
    if f.similar:
        from assetforge.core.search import _similar

        ranked = _similar(lib, f.similar, limit=SEARCH_POOL, where=" AND ".join(where) or "1", params=params)
        return [asset_id for asset_id, _, _ in ranked]
    if f.q.strip():
        # Text: rank with the search engine, then apply browse's exact filters to the ranked ids.
        hits = search(lib, SearchQuery(text=f.q, statuses=list(Status), include_restricted=True,
                                       limit=SEARCH_POOL)).results  # fmt: skip
        ids = [h.id for h in hits]
        if ids and where:
            allowed = {r[0] for r in lib.conn.execute(
                f"SELECT a.id {_FROM} WHERE a.id IN ({qmarks(len(ids))}) AND {' AND '.join(where)}", [*ids, *params])}  # fmt: skip
            ids = [i for i in ids if i in allowed]
        return ids
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = "" if upto is None else f" LIMIT {int(upto)}"
    return [r[0] for r in lib.conn.execute(f"SELECT a.id {_FROM} {clause} ORDER BY {SORTS[sort]}{limit}", params)]


def _group_under_parents(lib: Library, ids: list[str], *, archived_view: bool) -> list[str]:
    """Replace each part by its parent (when the parent is visible in this view), keeping first-seen order.

    A search that only matches a frame still surfaces it, as its sheet; the frame itself is one click away.
    """
    parents: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        for row in lib.conn.execute(
            f"SELECT a.id, a.parent_asset_id, p.status FROM assets a JOIN assets p ON p.id = a.parent_asset_id "
            f"WHERE a.id IN ({qmarks(len(batch))})", batch,
        ):  # fmt: skip
            if archived_view or row[2] != Status.ARCHIVED.value:
                parents[row[0]] = row[1]
    seen: set[str] = set()
    out: list[str] = []
    for asset_id in ids:
        shown = parents.get(asset_id, asset_id)
        if shown not in seen:
            seen.add(shown)
            out.append(shown)
    return out


def facets(lib: Library) -> dict[str, list[dict[str, Any]]]:
    """Distinct filter values with counts, for building filter controls."""

    def grouped(expr: str, extra: str = "") -> list[dict[str, Any]]:
        rows = lib.conn.execute(
            f"SELECT {expr} AS value, count(*) AS n {_FROM} {extra} GROUP BY value HAVING value IS NOT NULL "
            "ORDER BY n DESC, value"
        )
        return [{"value": r["value"], "count": r["n"]} for r in rows]

    collections = [
        {"value": r["name"], "count": r["n"]}
        for r in lib.conn.execute(
            "SELECT c.name, count(ca.asset_id) AS n FROM collections c LEFT JOIN collection_assets ca "
            "ON ca.collection_id = c.id GROUP BY c.id ORDER BY c.name"
        )
    ]
    tags = [
        {"value": r["name"], "count": r["n"]}
        for r in lib.conn.execute(
            "SELECT t.name, count(*) AS n FROM asset_tags at JOIN tags t ON t.id = at.tag_id GROUP BY t.id "
            "ORDER BY n DESC, t.name LIMIT 40"
        )
    ]
    return {
        "types": grouped("a.type"),
        "statuses": grouped("a.status"),
        "origins": grouped("a.origin"),
        "kinds": grouped("pf.kind"),
        "projects": grouped("a.project"),
        "styles": grouped("a.style"),
        "categories": grouped("a.category"),
        "genres": grouped("a.genre"),
        "themes": grouped("a.theme"),
        "collections": collections,
        "tags": tags,
    }
