"""Vocabulary and the typed shapes returned by the Python API and the CLI's JSON output."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetType(StrEnum):
    CHARACTER = "character"
    ENEMY = "enemy"
    SPRITE = "sprite"
    OBJECT = "object"
    SPRITESHEET = "spritesheet"
    ANIMATION = "animation"
    BACKGROUND = "background"
    TILE = "tile"
    TEXTURE = "texture"
    ICON = "icon"
    UI = "ui"
    EFFECT = "effect"
    IMAGE = "image"
    SFX = "sfx"
    MUSIC = "music"
    AMBIENCE = "ambience"
    VOICE = "voice"
    AUDIO = "audio"
    FONT = "font"
    SOURCE = "source"
    DATA = "data"
    OTHER = "other"


# Visual types, and the subset that is a single isolated subject (transparent by default, packable in atlases).
IMAGE_TYPES = frozenset({
    AssetType.CHARACTER, AssetType.ENEMY, AssetType.SPRITE, AssetType.OBJECT, AssetType.SPRITESHEET,
    AssetType.ANIMATION, AssetType.BACKGROUND, AssetType.TILE, AssetType.TEXTURE, AssetType.ICON, AssetType.UI,
    AssetType.EFFECT, AssetType.IMAGE,
})  # fmt: skip
ISOLATED_TYPES = frozenset({
    AssetType.CHARACTER, AssetType.ENEMY, AssetType.SPRITE, AssetType.OBJECT, AssetType.ICON, AssetType.UI,
    AssetType.EFFECT,
})  # fmt: skip


class Scope(StrEnum):
    GLOBAL = "global"
    STYLE = "style"
    PROJECT = "project"


class Status(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class Origin(StrEnum):
    IMPORT = "import"
    PROCEDURAL = "procedural"
    AI = "ai"
    DERIVED = "derived"


class CommercialUse(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class FileKind(StrEnum):
    IMAGE = "image"
    VECTOR = "vector"
    AUDIO = "audio"
    FONT = "font"
    SOURCE = "source"
    OTHER = "other"


class LineageRelation(StrEnum):
    DERIVED_FROM = "derived_from"
    EDITED_FROM = "edited_from"
    VARIANT_OF = "variant_of"
    SOURCE_OF = "source_of"


class FileInfo(BaseModel):
    sha256: str
    path: str
    rel_path: str
    ext: str
    mime_type: str
    kind: FileKind
    size_bytes: int
    width: int | None = None
    height: int | None = None
    has_alpha: bool | None = None
    frames: int | None = None
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    aspect: float | None = None
    alpha_fraction: float | None = None
    mean_luma: float | None = None
    colors: list[dict[str, Any]] | None = None
    dhash: str | None = None
    pruned: bool = False  # a synthesized master deleted to save space; re-rendered from its spec on demand
    withheld: bool = False  # left out of a shared snapshot (`library snapshot`); only the publisher has it


class Variant(BaseModel):
    id: str
    asset_id: str
    label: str
    parent_variant_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    original_filename: str | None = None
    created_at: str
    file: FileInfo


class LineageLink(BaseModel):
    relation: LineageRelation
    direction: Literal["outgoing", "incoming"]
    asset_id: str
    variant_id: str | None = None
    created_at: str


class Usage(BaseModel):
    project: str
    asset_id: str
    variant_id: str
    usage_key: str | None = None
    created_at: str


class Event(BaseModel):
    event: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AssetSummary(BaseModel):
    id: str
    key: str | None = None
    name: str
    type: AssetType
    description: str = ""
    scope: Scope
    project: str | None = None
    style: str | None = None
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    parent_asset_id: str | None = None  # the sheet this frame was cut from
    children: int = 0  # frames cut from this asset
    status: Status
    origin: Origin
    license: str | None = None
    commercial_use: CommercialUse
    tags: list[str] = Field(default_factory=list)
    path: str | None = None
    primary_variant: Variant | None = None
    created_at: str
    updated_at: str


class Asset(AssetSummary):
    attribution: str | None = None
    source_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    variants: list[Variant] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    lineage: list[LineageLink] = Field(default_factory=list)
    usage: list[Usage] = Field(default_factory=list)
    history: list[Event] = Field(default_factory=list)


class SearchHit(AssetSummary):
    score: float | None = None
    coverage: float | None = None
    matched_terms: list[str] = Field(default_factory=list)


class Collection(BaseModel):
    id: str
    name: str
    kind: str
    description: str = ""
    project: str | None = None
    asset_count: int = 0
    created_at: str
    updated_at: str


class NewAsset(BaseModel):
    """Everything needed to register a file as a new logical asset."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: AssetType
    key: str | None = None
    description: str = ""
    scope: Scope = Scope.GLOBAL
    project: str | None = None
    style: str | None = None
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    parent_asset_id: str | None = None
    status: Status = Status.APPROVED
    origin: Origin = Origin.IMPORT
    license: str | None = None
    commercial_use: CommercialUse = CommercialUse.UNKNOWN
    attribution: str | None = None
    source_uri: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetPatch(BaseModel):
    """Fields to change on an existing asset; unset fields stay as they are.

    For nullable text fields an empty string clears the value.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    key: str | None = None
    type: AssetType | None = None
    description: str | None = None
    scope: Scope | None = None
    project: str | None = None
    style: str | None = None
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    license: str | None = None
    commercial_use: CommercialUse | None = None
    attribution: str | None = None
    add_tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AssetType | None = None
    name: str | None = None
    key: str | None = None
    description: str = ""
    scope: Scope = Scope.GLOBAL
    project: str | None = None
    style: str | None = None
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    status: Status = Status.APPROVED
    license: str | None = None
    commercial_use: CommercialUse = CommercialUse.UNKNOWN
    attribution: str | None = None
    tags: list[str] = Field(default_factory=list)
    auto_tags: bool = True
    collection: str | None = None
    recursive: bool = True
    all_files: bool = False
    dry_run: bool = False


ImportAction = Literal["created", "duplicate", "would_create", "skipped", "error"]


class ImportItem(BaseModel):
    path: str
    action: ImportAction
    asset_id: str | None = None
    variant_id: str | None = None
    sha256: str | None = None
    name: str | None = None
    type: AssetType | None = None
    tags: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    reason: str | None = None
    error: dict[str, Any] | None = None


class ImportReport(BaseModel):
    status: Literal["ok", "partial", "error"]
    dry_run: bool
    collection: str | None = None
    summary: dict[str, int]
    items: list[ImportItem]

    @classmethod
    def build(
        cls, items: list[ImportItem], *, ignored: int, dry_run: bool, collection: str | None
    ) -> ImportReport:
        summary = {action: 0 for action in ("created", "would_create", "duplicate", "skipped", "error")}
        for item in items:
            summary[item.action] += 1
        summary["ignored"] = ignored
        summary["total"] = len(items)
        errors = summary["error"]
        if errors == 0:
            status = "ok"
        elif errors == len(items):
            status = "error"
        else:
            status = "partial"
        return cls(status=status, dry_run=dry_run, collection=collection, summary=summary, items=items)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    id: str
    kind: str
    status: JobStatus
    fingerprint: str | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    asset_id: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    cost_usd: float | None = None
    estimated_cost_usd: float | None = None
    attempts: int = 0
    max_attempts: int | None = None
    priority: int = 0
    lease_owner: str | None = None
    lease_expires_at: float | None = None
    run_after: float | None = None
    created_at: str
    updated_at: str


class StyleProfile(BaseModel):
    id: str
    name: str
    description: str = ""
    palette: list[str] = Field(default_factory=list)
    instructions: str = ""
    reference_asset_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
