"""The documents of the visual-novel content contract v2.0 (see `contract.py` and docs/narrative/26).

Every document is `{"kind": "vn.<x>", "schemaVersion": "2.0", …}`. The compiler builds plain dicts and validates
them against these models, so what is written is exactly what is checked. Field names follow the contract
(camelCase). File descriptors carry relative object keys only (`check_key` refuses anything else).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SCHEMA_VERSION = "2.0"
ENGINE_VERSION = "2.0.0"
CAPABILITIES = ["graph.branching@1", "scene.layers@1", "audio.mix@1"]
MEDIA_TYPES = {".json": "application/json", ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg",
               ".ogg": "audio/ogg", ".m4a": "audio/mp4"}  # fmt: skip

# guardrails enforced at compile and validation time
MAX_DOC_BYTES = 4_000_000
MAX_KEY_LENGTH = 512
MAX_NODES = 6000  # per chapter graph
MAX_CONDITION_DEPTH = 6
MAX_AUTO_STEPS = 64  # automatic transitions (condition/action) between two presenting nodes
MAX_TEXT = 8000

_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RELEASE_ID = re.compile(r"^r\d{4}$")

NodeId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_][a-z0-9_.]*$", max_length=200)]
TextKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_][a-z0-9_.-]*$", max_length=240)]
StatePath = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$", max_length=120)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ContractError(ValueError):
    """A key or document breaks the contract."""


def check_key(key: str) -> str:
    """A relative object key: ASCII segments joined by '/', no traversal, no absolute path, no query or
    fragment, no percent-encoding, a known media extension. Returns the key or raises ContractError."""
    if not isinstance(key, str) or not key or len(key) > MAX_KEY_LENGTH:
        raise ContractError(f"bad key {key!r}: empty or too long")
    if key.startswith("/") or "\\" in key or "://" in key or ":" in key:
        raise ContractError(f"bad key {key!r}: absolute paths and URLs are not keys")
    if any(ch in key for ch in "?#%&=@~ ") or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise ContractError(f"bad key {key!r}: queries, fragments, encodings and spaces are not allowed")
    for segment in key.split("/"):
        if segment in ("", ".", "..") or not _KEY_SEGMENT.match(segment):
            raise ContractError(f"bad key {key!r}: segment {segment!r}")
    ext = "." + key.rsplit(".", 1)[-1].lower() if "." in key.rsplit("/", 1)[-1] else ""
    if ext not in MEDIA_TYPES:
        raise ContractError(f"bad key {key!r}: unsupported media type {ext or '(none)'}")
    return key


def media_type(key: str) -> str:
    return MEDIA_TYPES["." + key.rsplit(".", 1)[-1].lower()]


def canonical(doc: Any) -> bytes:
    """The exact bytes of a document: sorted keys, no whitespace, UTF-8."""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def descriptor(key: str, data: bytes) -> dict[str, Any]:
    return {"key": check_key(key), "mediaType": media_type(key), "bytes": len(data), "sha256": sha256(data)}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Doc(Strict):
    schemaVersion: Literal["2.0"]


class FileDescriptor(Strict):
    key: str
    mediaType: str
    bytes: int = Field(ge=0)
    sha256: Sha256

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        try:
            return check_key(value)
        except ContractError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("mediaType")
    @classmethod
    def _media(cls, value: str) -> str:
        if value not in MEDIA_TYPES.values():
            raise ValueError(f"unsupported media type {value!r}")
        return value


# ---------------------------------------------------------------------- conditions and effects


class Compare(Strict):
    path: StatePath
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: bool | int


class AllOf(Strict):
    all: list[Condition]


class AnyOf(Strict):
    any: list[Condition]


class NotOf(Strict):
    not_: Condition = Field(alias="not")


Condition = Compare | AllOf | AnyOf | NotOf
AllOf.model_rebuild()
AnyOf.model_rebuild()
NotOf.model_rebuild()


class Effect(Strict):
    op: Literal["set", "add"]
    path: StatePath
    value: bool | int


def condition_depth(cond: dict[str, Any]) -> int:
    if "all" in cond or "any" in cond:
        items = cond.get("all", cond.get("any")) or []
        return 1 + max((condition_depth(c) for c in items), default=0)
    if "not" in cond:
        return 1 + condition_depth(cond["not"])
    return 1


def condition_paths(cond: dict[str, Any]) -> list[dict[str, Any]]:
    """The comparisons inside a condition."""
    if "all" in cond or "any" in cond:
        return [c for item in cond.get("all", cond.get("any")) or [] for c in condition_paths(item)]
    if "not" in cond:
        return condition_paths(cond["not"])
    return [cond]


# ---------------------------------------------------------------------- the chapter graph


class Option(Strict):
    id: Annotated[str, StringConstraints(pattern=r"^o\d+$")]
    textKey: TextKey
    enabledIf: Condition | None = None
    disabledReasonKey: TextKey | None = None
    effects: list[Effect] = Field(default_factory=list)
    target: NodeId


class Case(Strict):
    when: Condition
    target: NodeId


class Narration(Strict):
    id: NodeId
    type: Literal["narration"]
    textKey: TextKey
    presentationRef: str
    next: NodeId


class Dialogue(Strict):
    id: NodeId
    type: Literal["dialogue"]
    speakerId: str
    textKey: TextKey
    presentationRef: str
    next: NodeId


class ChoiceNode(Strict):
    id: NodeId
    type: Literal["choice"]
    textKey: TextKey | None = None
    presentationRef: str
    options: list[Option] = Field(min_length=1)


class ConditionNode(Strict):
    id: NodeId
    type: Literal["condition"]
    cases: list[Case] = Field(min_length=1)
    fallback: NodeId


class Action(Strict):
    id: NodeId
    type: Literal["action"]
    effects: list[Effect]
    next: NodeId


class ChapterEnd(Strict):
    id: NodeId
    type: Literal["chapterEnd"]
    textKey: TextKey | None = None
    presentationRef: str
    nextChapter: str


class EndingNode(Strict):
    id: NodeId
    type: Literal["ending"]
    endingId: str


GraphNode = Annotated[Narration | Dialogue | ChoiceNode | ConditionNode | Action | ChapterEnd | EndingNode,
                      Field(discriminator="type")]  # fmt: skip
PRESENTING = frozenset({"narration", "dialogue", "choice", "chapterEnd"})
AUTOMATIC = frozenset({"condition", "action"})


class Speaker(Strict):
    nameKey: TextKey


class Graph(Doc):
    kind: Literal["vn.chapter"]
    storyId: str
    graphId: str
    chapterId: str
    entry: NodeId
    speakers: dict[str, Speaker] = Field(default_factory=dict)
    nodes: list[GraphNode] = Field(max_length=MAX_NODES)


def node_targets(node: dict[str, Any]) -> list[str]:
    """Every node id a graph node can lead to (within its chapter)."""
    kind = node["type"]
    if kind in ("narration", "dialogue", "action"):
        return [node["next"]]
    if kind == "choice":
        return [o["target"] for o in node["options"]]
    if kind == "condition":
        return [c["target"] for c in node["cases"]] + [node["fallback"]]
    return []


# ---------------------------------------------------------------------- presentation


class Character(Strict):
    characterId: str
    asset: str
    slot: Literal["left", "center", "right"]


class Overlay(Strict):
    asset: str
    kind: Literal["cg"]


class Loop(Strict):
    asset: str
    gain: float = Field(ge=0, le=1)
    loop: Literal[True] = True
    transitionMs: int = Field(ge=0)


class Audio(Strict):
    music: Loop | None
    ambience: Loop | None
    loops: list[Loop] = Field(default_factory=list)


class SceneState(Strict):
    background: str | None
    characters: list[Character]
    overlays: list[Overlay]
    layoutProfile: str
    audio: Audio


class CueEvent(Strict):
    id: str
    trigger: Literal["onNodeEnter", "onTextRevealed", "onChoiceCommitted"]
    type: Literal["sound"]
    asset: str
    gain: float = Field(1.0, ge=0, le=1)
    resumePolicy: Literal["suppress"] = "suppress"


class Cue(Strict):
    scene: str
    events: list[CueEvent] = Field(default_factory=list)


class Presentation(Doc):
    kind: Literal["vn.presentation"]
    storyId: str
    chapterId: str
    scenes: dict[str, SceneState]
    cues: dict[str, Cue]


# ---------------------------------------------------------------------- assets, locale, downloads


class ImageVariant(Strict):
    id: str
    layout: Literal["landscape", "portrait", "square"] | None = None
    quality: Literal["standard", "high"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    focalPoint: list[float] | None = None
    subjectSafeRect: list[float] | None = None
    file: FileDescriptor


class AudioVariant(Strict):
    id: str
    codec: Literal["vorbis", "aac", "opus"]
    container: Literal["ogg", "mp4"]
    quality: Literal["standard", "high"] = "standard"
    durationMs: int = Field(ge=0)
    file: FileDescriptor


class ImageAsset(Strict):
    type: Literal["image"]
    altKey: TextKey
    variants: list[ImageVariant]


class AudioAsset(Strict):
    type: Literal["audio"]
    loop: bool = False
    variants: list[AudioVariant]


class AssetRegistry(Doc):
    kind: Literal["vn.assets"]
    storyId: str
    chapterId: str
    assets: dict[str, Annotated[ImageAsset | AudioAsset, Field(discriminator="type")]]


class Locale(Doc):
    kind: Literal["vn.locale"]
    storyId: str
    locale: str
    strings: dict[TextKey, Annotated[str, StringConstraints(max_length=MAX_TEXT)]]


class Profile(Strict):
    documents: list[str]
    assets: list[str]
    qualities: list[Literal["standard", "high"]]
    bytes: int = Field(ge=0)


class Downloads(Doc):
    kind: Literal["vn.downloads"]
    storyId: str
    chapterId: str
    startupNodes: list[NodeId]
    profiles: dict[Literal["core", "standard", "enhanced"], Profile]


class Bundle(Doc):
    kind: Literal["vn.chapter.bundle"]
    storyId: str
    releaseId: str
    chapterId: str
    graph: FileDescriptor
    presentation: FileDescriptor
    assets: FileDescriptor
    locales: dict[str, FileDescriptor]
    downloads: FileDescriptor


# ---------------------------------------------------------------------- release, migration, catalog


class Variable(Strict):
    type: Literal["bool", "int"]
    default: bool | int
    min: int | None = None
    max: int | None = None
    overflow: Literal["clamp", "reject"]


class StateSchema(Strict):
    version: int = Field(ge=1)
    variables: dict[StatePath, Variable]


class Requires(Strict):
    minEngineVersion: str
    capabilities: list[str]


class ChapterRef(Strict):
    id: str
    titleKey: TextKey
    accessPolicyId: str
    manifest: FileDescriptor


class EndingRef(Strict):
    id: str
    kind: str
    titleKey: TextKey
    summaryKey: TextKey


class Cover(Strict):
    asset: str
    altKey: TextKey
    width: int
    height: int
    file: FileDescriptor


class Release(Doc):
    kind: Literal["vn.release"]
    storyId: str
    releaseId: str
    draft: bool | None = None
    graphId: str
    requires: Requires
    defaultLocale: str
    titleKey: TextKey
    summaryKey: TextKey
    entryChapter: str
    stateSchema: StateSchema
    chapters: list[ChapterRef] = Field(min_length=1)
    endings: list[EndingRef] = Field(default_factory=list)
    locales: dict[str, FileDescriptor]
    migrations: list[FileDescriptor] = Field(default_factory=list)
    cover: Cover | None = None


class StateOperation(Strict):
    op: Literal["rename", "default"]
    path: StatePath | None = None
    value: bool | int | None = None
    from_: StatePath | None = Field(None, alias="from")
    to: StatePath | None = None


class Migration(Doc):
    kind: Literal["vn.migration"]
    storyId: str
    fromRelease: str
    toRelease: str
    fromGraphId: str
    toGraphId: str
    nodeAliases: dict[str, str]
    stateOperations: list[StateOperation]


class CatalogRelease(Strict):
    releaseId: str
    graphId: str
    minEngineVersion: str
    manifest: FileDescriptor


class CatalogStory(Strict):
    storyId: str
    title: str
    summary: str
    defaultLocale: str
    thumbnail: Cover | None = None
    current: str
    releases: list[CatalogRelease] = Field(min_length=1)


class Catalog(Doc):
    kind: Literal["vn.catalog"]
    catalogSequence: int = Field(ge=1)
    stories: list[CatalogStory]


class Envelope(Strict):
    alg: Literal["Ed25519"]
    keyId: str
    payload: str
    signature: str
