"""Search before generate: rank cataloged assets against a free-text request.

Every query term (plus a few synonyms) is matched as a prefix against name, key, tags,
description, type and style via FTS5. Results are ranked mainly by *coverage*, the share
of query terms an asset matches, so agents can judge whether a hit is close enough to
reuse. BM25 relevance and style/project affinity break ties.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from assetforge.core.catalog import Library
from assetforge.core.classify import tokens
from assetforge.core.collections import get_collection
from assetforge.core.models import AssetType, FileKind, Scope, SearchHit, Status
from assetforge.core.util import normalize_optional_slug, normalize_slug, qmarks

STOPWORDS = {
    "a", "an", "and", "the", "of", "for", "with", "to", "in", "on", "at", "by", "or", "is", "it",
    "that", "this", "some", "very", "like", "asset", "assets", "file",
}  # fmt: skip

SYNONYMS: dict[str, list[str]] = {
    "sfx": ["sound"],
    "sound": ["sfx"],
    "sounds": ["sfx"],
    "audio": ["sfx", "music"],
    "bg": ["background"],
    "background": ["bg", "backdrop"],
    "backdrop": ["background"],
    "music": ["bgm", "song"],
    "bgm": ["music"],
    "song": ["music"],
    "btn": ["button"],
    "button": ["btn"],
    "pickup": ["collect"],
    "collect": ["pickup"],
    "char": ["character"],
    "character": ["char", "hero", "player"],
    "explosion": ["explode", "boom"],
    "boom": ["explosion"],
    "fx": ["effect", "vfx"],
    "vfx": ["effect", "fx"],
    "effect": ["fx", "vfx"],
    "img": ["image"],
    "pic": ["image"],
}

# bm25 weights follow the assets_fts column order: asset_id, name, key, tags, description, type, style.
BM25 = "bm25(assets_fts, 0.0, 10.0, 4.0, 5.0, 2.0, 1.5, 1.5)"
CANDIDATE_POOL = 2000
DEFAULT_STATUSES = (Status.APPROVED, Status.CANDIDATE)

W_COVERAGE, W_RELEVANCE, W_STYLE, W_PROJECT = 0.65, 0.20, 0.10, 0.05


class SearchQuery(BaseModel):
    text: str = ""
    types: list[AssetType] = Field(default_factory=list)
    kind: FileKind | None = None
    tags: list[str] = Field(default_factory=list)
    style: str | None = None
    strict_style: bool = False
    project: str | None = None
    scopes: list[Scope] = Field(default_factory=list)
    statuses: list[Status] = Field(default_factory=list)
    collection: str | None = None
    include_restricted: bool = False
    transparent: bool | None = None
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    min_width: int | None = None
    min_height: int | None = None
    all_terms: bool = False
    min_coverage: float = Field(0.0, ge=0.0, le=1.0)
    limit: int = Field(20, ge=1, le=500)


class SearchResult(BaseModel):
    query: dict[str, Any]
    terms: list[str]
    count: int
    results: list[SearchHit]


def query_terms(text: str) -> list[str]:
    return list(dict.fromkeys(t for t in tokens(text) if t not in STOPWORDS))


def _group_expr(group: list[str]) -> str:
    return "(" + " OR ".join(f'"{term}"*' for term in group) + ")"


def _filters(lib: Library, q: SearchQuery) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    statuses = q.statuses or list(DEFAULT_STATUSES)
    where.append(f"a.status IN ({qmarks(len(statuses))})")
    params += [s.value for s in statuses]
    if q.types:
        where.append(f"a.type IN ({qmarks(len(q.types))})")
        params += [t.value for t in q.types]
    if q.scopes:
        where.append(f"a.scope IN ({qmarks(len(q.scopes))})")
        params += [s.value for s in q.scopes]
    if not q.include_restricted:
        where.append("a.commercial_use != 'restricted'")
    for tag in q.tags:
        where.append(
            "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            "WHERE at.asset_id = a.id AND t.name = ?)"
        )
        params.append(normalize_slug(tag, "tag"))
    style = normalize_optional_slug(q.style, "style")
    if q.strict_style and style:
        where.append("a.style = ?")
        params.append(style)
    project = normalize_optional_slug(q.project, "project")
    if project:
        # Reuse scopes: global assets, this project's own assets, and style-scoped assets of a matching style.
        where.append(
            "(a.scope = 'global' OR (a.scope = 'project' AND a.project = ?) OR (a.scope = 'style' AND a.style = ?))"
        )
        params += [project, style]
    if q.collection:
        where.append("a.id IN (SELECT asset_id FROM collection_assets WHERE collection_id = ?)")
        params.append(get_collection(lib, q.collection).id)
    if q.kind:
        where.append("pf.kind = ?")
        params.append(q.kind.value)
    if q.transparent is not None:
        where.append("pf.has_alpha = ?")
        params.append(int(q.transparent))
    for column, op, value in (
        ("pf.duration_ms", ">=", q.min_duration_ms),
        ("pf.duration_ms", "<=", q.max_duration_ms),
        ("pf.width", ">=", q.min_width),
        ("pf.height", ">=", q.min_height),
    ):
        if value is not None:
            where.append(f"{column} {op} ?")
            params.append(value)
    return where, params


_JOINS = (
    "LEFT JOIN asset_variants pv ON pv.id = a.primary_variant_id "
    "LEFT JOIN asset_files pf ON pf.sha256 = pv.file_sha256"
)


def search(lib: Library, q: SearchQuery) -> SearchResult:
    where, params = _filters(lib, q)
    terms = query_terms(q.text)
    style = normalize_optional_slug(q.style, "style")
    project = normalize_optional_slug(q.project, "project")
    query_echo = q.model_dump(mode="json", exclude_defaults=True)

    if not terms:
        rows = lib.conn.execute(
            f"SELECT a.id FROM assets a {_JOINS} WHERE {' AND '.join(where)} "
            "ORDER BY a.updated_at DESC, a.id DESC LIMIT ?",
            [*params, q.limit],
        ).fetchall()
        hits = [SearchHit(**s.model_dump()) for s in lib.summaries([r[0] for r in rows])]
        return SearchResult(query=query_echo, terms=[], count=len(hits), results=hits)

    groups = [[term, *SYNONYMS.get(term, [])] for term in terms]
    match = (" AND " if q.all_terms else " OR ").join(_group_expr(g) for g in groups)
    candidates = lib.conn.execute(
        f"SELECT a.id, a.style, a.project, {BM25} AS rank FROM assets_fts "
        f"JOIN assets a ON a.id = assets_fts.asset_id {_JOINS} "
        f"WHERE assets_fts MATCH ? AND {' AND '.join(where)} ORDER BY rank LIMIT ?",
        [match, *params, CANDIDATE_POOL],
    ).fetchall()
    if not candidates:
        return SearchResult(query=query_echo, terms=terms, count=0, results=[])

    ids = [row["id"] for row in candidates]
    matched: dict[str, list[str]] = {asset_id: [] for asset_id in ids}
    for term, group in zip(terms, groups, strict=True):
        for row in lib.conn.execute(
            f"SELECT asset_id FROM assets_fts WHERE assets_fts MATCH ? AND asset_id IN ({qmarks(len(ids))})",
            [_group_expr(group), *ids],
        ):
            matched[row[0]].append(term)

    best_rank = min(row["rank"] for row in candidates)  # bm25 is negative; lower is better
    scored: list[tuple[float, float, str]] = []
    for row in candidates:
        coverage = len(matched[row["id"]]) / len(terms)
        if coverage < q.min_coverage:
            continue
        relevance = row["rank"] / best_rank if best_rank else 0.0
        score = (
            W_COVERAGE * coverage
            + W_RELEVANCE * relevance
            + (W_STYLE if style and row["style"] == style else 0.0)
            + (W_PROJECT if project and row["project"] == project else 0.0)
        )
        scored.append((round(score, 4), round(coverage, 4), row["id"]))
    scored.sort(key=lambda item: (-item[0], item[2]))
    scored = scored[: q.limit]

    summaries = {s.id: s for s in lib.summaries([asset_id for _, _, asset_id in scored])}
    hits = [
        SearchHit(**summaries[asset_id].model_dump(), score=score, coverage=coverage, matched_terms=matched[asset_id])
        for score, coverage, asset_id in scored
    ]
    return SearchResult(query=query_echo, terms=terms, count=len(hits), results=hits)
