"""Compile a story into an immutable, verifiable release in the visual-novel content contract v2.0.

A release is a set of documents (release manifest → chapter bundles → graph, presentation, asset registry,
locales, download plan) plus content-addressed media blobs, written under `<story>/releases/<releaseId>/…` in the
same layout as the bucket (`blobs/` is shared by every release). Nothing here touches the network: publishing is
`assetforge.publish.r2`, and only when explicitly asked.

Mapping (docs/narrative/26-content-contract-v2.md):
- chapter = a declared chapter (its scenes linked into one graph), else one scene per chapter;
- say → dialogue, narrate → narration, set → action, branch → condition (cases in order, fallback = goto),
  jump/goto into another chapter → chapterEnd, ending → ending; `when` on any node → a condition node that skips
  it; an in-chapter choice's options lead to their reaction lines, which merge back into the node after it;
- show/hide/place/music/sfx/cg/ambience are folded into presentation: each presenting node's `presentationRef`
  is a cue (named after the node) pointing at a complete scene state; sfx become `onNodeEnter` events;
- ids and text keys come from scene ids and node positions only, so unchanged text compiles to identical keys.

The graph id changes only when the gameplay part changes (node ids, types, targets, conditions, effects, state
schema); a recompile with nothing changed reports "identical" and mints no release.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from assetforge.core.util import utcnow
from assetforge.errors import AssetForgeError, ConflictError, NotFoundError
from assetforge.story import contract_models as cm
from assetforge.story.models import Condition, Issue, Line, Node, Scene, Script, Strict, Variable
from assetforge.story.project import StoryProject, write_atomic
from assetforge.story.validate import exits, validate_script

if TYPE_CHECKING:
    from assetforge.core.catalog import Library
    from assetforge.core.models import Asset, Variant

INDEX = "index.json"
DRAFT = "draft"
LANGS = {"english": "en", "spanish": "es", "french": "fr", "german": "de", "portuguese": "pt", "italian": "it",
         "japanese": "ja", "korean": "ko", "chinese": "zh", "hindi": "hi", "indonesian": "id", "russian": "ru",
         "turkish": "tr", "arabic": "ar", "vietnamese": "vi", "thai": "th", "polish": "pl", "dutch": "nl"}  # fmt: skip
OPS = {"==": "eq", "!=": "ne", ">=": "gte", "<=": "lte", ">": "gt", "<": "lt"}
OP_WORDS = {"==": "exactly", "!=": "other than", ">=": "at least", "<=": "at most", ">": "more than", "<": "less than"}
SLOTS = ("center", "left", "right")  # the player's free-slot order
SLOT_ORDER = {"left": 0, "center": 1, "right": 2}
MUSIC_FADE_MS, AMB_FADE_MS, AMB_NODE_FADE_MS = 2400, 1800, 1500
MAX_THREADS = 8  # presentation paths kept apart after gated staging nodes before they are merged


class ReleaseReport(Strict):
    story_id: str
    status: Literal["created", "identical", "draft", "blocked"]
    release_id: str | None = None
    graph_id: str | None = None
    previous: str | None = None
    graph_changed: bool | None = None
    path: str | None = None
    valid: bool = False
    message: str = ""
    missing_assets: list[str] = []
    findings: list[Issue] = []
    counts: dict[str, int] = {}
    migration: str | None = None


def finding(level: str, code: str, where: str, message: str) -> Issue:
    return Issue(level=level, code=code, where=where, message=message)  # type: ignore[arg-type]


def locale_code(language: str) -> str:
    text = (language or "").strip()
    if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})?", text):
        return text
    return LANGS.get(text.lower(), "en")


# ---------------------------------------------------------------------- inputs: script, variables, assets


@dataclass
class Media:
    """One library file a release ships."""

    variant_id: str
    path: Path
    ext: str
    sha256: str
    size: int
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None

    @property
    def key(self) -> str:
        return f"blobs/sha256/{self.sha256}{self.ext}"

    def descriptor(self) -> dict[str, Any]:
        return {"key": cm.check_key(self.key), "mediaType": cm.media_type(self.key), "bytes": self.size, "sha256": self.sha256}


@dataclass
class AssetFiles:
    lid: str
    key: str  # the story manifest key (vn/<slug>/…)
    kind: Literal["image", "audio"]
    alt: str = ""
    loop: bool = False
    standard: list[Media] = field(default_factory=list)
    high: list[Media] = field(default_factory=list)


@dataclass
class Inputs:
    script: Script
    story_id: str
    lang: str
    title: str
    summary: str
    variables: dict[str, tuple[str, str, Variable]]  # name → (path, type, variable)
    route_vars: dict[str, int]  # chapter id → number of entries (> 1 only)
    chapters: list[tuple[str, str, list[str]]]  # (chapter id, title, scene ids)
    chapter_of: dict[str, str]
    entries: dict[str, list[str]]  # chapter id → entry scene ids (index 0 is the default)
    lids: dict[str, str]  # logical asset id → manifest key
    assets: dict[str, AssetFiles]
    findings: list[Issue]
    items: dict[str, Any] = field(default_factory=dict)  # logical id → manifest Item
    reported: set[tuple[str, str, str]] = field(default_factory=set)  # findings already raised while compiling


def _load_script(project: StoryProject) -> tuple[Script, list[str]]:
    if project.has("script"):
        return project.load("script"), []
    from assetforge.story.progress import preview_script

    data = preview_script(project)
    if data is None:
        raise NotFoundError("Nothing written yet", hint="Write the story first (`assetforge story write`)")
    drafts = [s["id"] for s in data["scenes"] if s.get("draft")]
    for scene in data["scenes"]:
        scene.pop("draft", None)
        scene.pop("summary", None)
    data.pop("format", None)
    return Script(**data), drafts


def _state_path(v: Variable) -> tuple[str, str]:
    if v.kind == "flag":
        return f"flags.{v.name}", "bool"
    if v.kind == "relationship":
        return f"relationships.{v.name}", "int"
    return f"stats.{v.name}", "int"


def _chapters(script: Script, findings: list[Issue]) -> list[tuple[str, str, list[str]]]:
    if not script.chapters:
        return [(s.id, s.title, [s.id]) for s in script.scenes]
    out = [(c.id, c.title, [s.id for s in script.scenes if s.chapter == c.id]) for c in script.chapters]
    out = [c for c in out if c[2]]
    declared = {c.id for c in script.chapters}
    for s in script.scenes:
        if s.chapter not in declared:
            findings.append(finding("warning", "own_chapter", s.id, "Scene has no declared chapter; it becomes its own chapter"))
            out.append((s.id, s.title, [s.id]))
    return out


def _entries(script: Script, chapter_of: dict[str, str], chapters: list[tuple[str, str, list[str]]]) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {cid: [] for cid, _, _ in chapters}
    if script.start in chapter_of:
        entries[chapter_of[script.start]].append(script.start)
    for scene in script.scenes:
        for target in exits(scene)[0]:
            ch = chapter_of.get(target)
            if ch and ch != chapter_of.get(scene.id) and target not in entries[ch]:
                entries[ch].append(target)
    for cid, _, scene_ids in chapters:
        if not entries[cid]:
            entries[cid].append(scene_ids[0])
    return entries


def _alt_text(script: Script, lid: str) -> str:
    parts = lid.split(".")
    chars = {c.id: c for c in script.characters}
    locs = {loc.id: loc for loc in script.locations}
    if parts[0] == "bg" and len(parts) == 3 and parts[1] in locs:
        loc = locs[parts[1]]
        return f"{loc.name}, {parts[2]}. {loc.description}".strip()
    if parts[0] == "char" and len(parts) == 3 and parts[1] in chars:
        c = chars[parts[1]]
        return f"{c.name}, {parts[2].replace('_', ' ')} expression. {c.look}".strip()
    if parts[0] == "cg":
        text = next((n.text for s in script.scenes for n in s.nodes if n.kind == "cg" and n.cg == parts[1] and n.text), None)
        return text or f"Illustration: {parts[1].replace('_', ' ')}"
    if lid == "ui.title":
        return f"Title art for {script.title}"
    return lid.replace(".", " ").replace("_", " ")


def _media(variant: Variant) -> Media:
    f = variant.file
    return Media(variant.id, Path(f.path), f.ext.lower(), f.sha256, f.size_bytes, f.width, f.height, f.duration_ms)


def _image_media(lib: Library, asset: Asset, derive: bool) -> tuple[Variant, Variant | None]:
    """(the web delivery variant, the lossless source it was made from, if any)."""
    from assetforge.exporters.generic import _image_variant

    primary = asset.primary_variant
    assert primary is not None
    if derive:
        web = _image_variant(lib, asset, "webp", None)
        asset = lib.get_asset(asset.id)
    else:
        web = primary if primary.file.ext == ".webp" else next(
            (v for v in asset.variants if v.parent_variant_id == primary.id and v.file.ext == ".webp"), primary)
    by_id = {v.id: v for v in asset.variants}
    source = by_id.get(web.parent_variant_id or "") or (primary if primary.id != web.id else None)
    return web, source if source is not None and source.file.ext == ".png" else None


def _audio_existing(asset: Asset, fmt: str) -> Variant | None:
    primary = asset.primary_variant
    if primary is None:
        return None
    if primary.file.ext == f".{fmt}":
        return primary
    master = next((v for v in asset.variants if v.id == primary.params.get("encoded_from")), None) or primary
    return next((v for v in asset.variants if v.file.ext == f".{fmt}" and (
        v.params.get("encoded_from") == master.id or v.label in (f"{primary.label}_{fmt}", f"{fmt}_of_{master.label}")
        or v.parent_variant_id in (master.id, primary.id))), None)  # fmt: skip


def _audio_media(lib: Library, asset: Asset, derive: bool, findings: list[Issue], lid: str) -> list[Variant]:
    from assetforge.exporters.generic import _audio_variant

    out: list[Variant] = []
    for fmt in ("ogg", "m4a"):
        found = _audio_existing(asset, fmt)
        if found is None and derive:
            try:
                found = _audio_variant(lib, asset, fmt)
                asset = lib.get_asset(asset.id)
            except AssetForgeError as exc:
                findings.append(finding("warning", "no_audio_format", lid, f"No {fmt} copy: {exc.message}"))
        if found is None:
            findings.append(finding("warning" if out else "error", "no_audio_format", lid, f"No {fmt} delivery copy"))
            continue
        out.append(found)
    return out


def _resolve_assets(lib: Library, inputs: Inputs, items: dict[str, Any], wanted: set[str], derive: bool) -> None:
    from assetforge.request import by_key

    for lid in sorted(wanted):
        item = items.get(lid)
        if item is None:
            inputs.findings.append(finding("error", "unknown_asset", lid, "Referenced asset is not in the story's manifest"))
            continue
        kind: Literal["image", "audio"] = "audio" if item.group in ("music", "ambience", "sfx") else "image"
        files = AssetFiles(lid, item.key, kind, alt=_alt_text(inputs.script, lid), loop=item.group in ("music", "ambience"))
        inputs.assets[lid] = files
        asset_id = by_key(lib, item.request)
        if not asset_id:
            if lid != "ui.title":
                inputs.findings.append(finding("error", "missing_asset", item.key, "No library asset for this key yet"))
            continue
        asset = lib.get_asset(asset_id)
        try:
            if kind == "image":
                web, source = _image_media(lib, asset, derive)
                files.standard = [_media(web)]
                files.high = [_media(source)] if source is not None else []
            else:
                files.standard = [_media(v) for v in _audio_media(lib, asset, derive, inputs.findings, lid)]
        except AssetForgeError as exc:
            inputs.findings.append(finding("error", "asset_unusable", item.key, exc.message))
            continue
        for m in files.standard + files.high:
            if m.ext not in cm.MEDIA_TYPES or not m.path.is_file():
                inputs.findings.append(finding("error", "missing_file", item.key, f"{m.variant_id}: file {m.ext} is missing or unsupported"))
        files.standard = [m for m in files.standard if m.ext in cm.MEDIA_TYPES and m.path.is_file()]
        files.high = [m for m in files.high if m.ext in cm.MEDIA_TYPES and m.path.is_file()]


def gather(lib: Library, project: StoryProject) -> Inputs:
    from assetforge.story.assets import manifest

    script, drafts = _load_script(project)
    config = project.config
    world = project.load("world") if project.has("world") else None
    findings = [finding("error", "unwritten_chapter", sid, "Not written yet (a stand-in from the plan)") for sid in drafts]
    findings += [finding(i.level, f"script.{i.code}", i.where, i.message) for i in validate_script(script)]
    chapters = _chapters(script, findings)
    chapter_of = {sid: cid for cid, _, ids in chapters for sid in ids}
    entries = _entries(script, chapter_of, chapters)
    variables = {v.name: (*_state_path(v), v) for v in script.variables}
    prefix = f"vn/{config.slug}/"
    items = {i.key[len(prefix):].replace("/", "."): i for i in manifest(script, config, world)}
    inputs = Inputs(script=script, story_id=config.slug, lang=locale_code(script.language), title=script.title,
                    summary=(world.logline if world else "") or config.premise, variables=variables,
                    route_vars={c: len(e) for c, e in entries.items() if len(e) > 1}, chapters=chapters,
                    chapter_of=chapter_of, entries=entries, lids={lid: i.key for lid, i in items.items()}, assets={},
                    findings=findings, items=items)  # fmt: skip
    return inputs


# ---------------------------------------------------------------------- presentation state


@dataclass(frozen=True)
class Stage:
    location: str
    time: str
    shown: tuple[tuple[str, str, str], ...] = ()  # (character, expression, slot), sorted by character
    cg: str | None = None
    mood: str | None = None
    music_fade: int = MUSIC_FADE_MS
    amb: str | None = None
    amb_level: float | None = None
    amb_fade: int = AMB_FADE_MS
    loops: tuple[tuple[str, float], ...] = ()

    def with_char(self, cid: str, expression: str | None, position: str | None) -> Stage:
        current = {c: (e, s) for c, e, s in self.shown}
        prev = current.get(cid)
        expr = expression or (prev[0] if prev else "neutral")
        if position:
            slot = position
        elif prev:
            slot = prev[1]
        else:
            used = {s for _, s in current.values()}
            slot = next((p for p in SLOTS if p not in used), "center")
        current[cid] = (expr, slot)
        return replace(self, shown=tuple(sorted((c, e, s) for c, (e, s) in current.items())))


Event = tuple[str, str, float]  # (event id, cue, gain)


@dataclass
class Thread:
    stage: Stage
    events: tuple[Event, ...]
    inbound: list[tuple[dict[str, Any], str]]


def _ms(seconds: float | None, default: int) -> int:
    return default if seconds is None else max(0, int(round(seconds * 1000)))


def apply_staging(stage: Stage, node: Node, event_id: str) -> tuple[Stage, tuple[Event, ...]]:
    """What a non-gameplay node does to the stage, as the demo player does it."""
    kind = node.kind
    if kind == "show" and node.character:
        return replace(stage.with_char(node.character, node.expression, node.position), cg=None), ()
    if kind == "hide" and node.character:
        return replace(stage, shown=tuple(x for x in stage.shown if x[0] != node.character), cg=None), ()
    if kind == "cg" and node.cg:
        return replace(stage, cg=node.cg), ()
    if kind == "music" and node.music:
        return replace(stage, mood=node.music, music_fade=_ms(node.fade, MUSIC_FADE_MS)), ()
    if kind == "ambience" and node.ambience:
        return replace(stage, amb=node.ambience, amb_level=node.level, amb_fade=_ms(node.fade, AMB_NODE_FADE_MS)), ()
    if kind == "place" and node.location:
        return replace(stage, location=node.location, time=node.time or stage.time, shown=(), cg=None,
                       mood=node.music or stage.mood, music_fade=MUSIC_FADE_MS, amb=None, amb_level=None,
                       amb_fade=AMB_FADE_MS), ()  # fmt: skip
    if kind == "sfx":
        if node.stop:
            return replace(stage, loops=tuple(x for x in stage.loops if x[0] != node.stop)), ()
        if node.cue and node.loop:
            if any(c == node.cue for c, _ in stage.loops):
                return stage, ()
            return replace(stage, loops=tuple(sorted((*stage.loops, (node.cue, node.level or 1.0))))), ()
        if node.cue:
            return stage, ((event_id, node.cue, node.level if node.level is not None else 1.0),)
    return stage, ()


# ---------------------------------------------------------------------- one chapter → graph + presentation


class ChapterBuilder:
    def __init__(self, inputs: Inputs, chapter_id: str, title: str, scene_ids: list[str]) -> None:
        self.inp = inputs
        self.cid = chapter_id
        self.title = title
        self.scene_ids = scene_ids
        self.nodes: dict[str, dict[str, Any]] = {}
        self.states: dict[bytes, str] = {}
        self.scene_docs: dict[str, dict[str, Any]] = {}
        self.cues: dict[str, dict[str, Any]] = {}
        self.texts: dict[str, str] = {}
        self.speakers: set[str] = set()
        self.endings: set[str] = set()
        self.assets: set[str] = set()
        self.pending: list[tuple[dict[str, Any], str, str]] = []
        self.entry_of: dict[str, str | None] = {}
        self.entry = ""

    # ------------------------------------------------------------------ helpers
    def warn(self, level: str, code: str, where: str, message: str) -> None:
        if (code, where, message) not in self.inp.reported:  # staging is evaluated per node: report each problem once
            self.inp.reported.add((code, where, message))
            self.inp.findings.append(finding(level, code, where, message))

    def add(self, node: dict[str, Any]) -> dict[str, Any]:
        if node["id"] in self.nodes:
            self.warn("error", "duplicate_node", node["id"], "Two graph nodes got the same id")
        self.nodes[node["id"]] = node
        return node

    @staticmethod
    def link(inbound: list[tuple[dict[str, Any], str]], target: str) -> None:
        for container, key in inbound:
            container[key] = target

    def cond(self, c: Condition) -> dict[str, Any]:
        var = self.inp.variables.get(c.var)
        if var is None:
            self.warn("error", "unknown_variable", self.cid, f"Condition reads undeclared variable {c.var!r}")
            return {"any": []}
        path, type_, _ = var
        if type_ == "int":
            return {"path": path, "op": OPS[c.op], "value": c.value}
        truth = {v: _holds(c.op, v, c.value) for v in (0, 1)}
        if truth[0] and truth[1]:
            return {"all": []}
        if not truth[0] and not truth[1]:
            return {"any": []}
        return {"path": path, "op": "eq", "value": truth[1]}

    def conds(self, conditions: list[Condition]) -> dict[str, Any]:
        parts = [self.cond(c) for c in conditions]
        return parts[0] if len(parts) == 1 else {"all": parts}

    def effects(self, assigns: list[Any]) -> list[dict[str, Any]]:
        out = []
        for a in assigns:
            var = self.inp.variables.get(a.var)
            if var is None:
                self.warn("error", "unknown_variable", self.cid, f"Effect writes undeclared variable {a.var!r}")
                continue
            path, type_, _ = var
            if type_ == "bool":  # flags: set n → n != 0; add +n → true, add -n → false
                if a.op == "add" and a.value == 0:
                    continue
                out.append({"op": "set", "path": path, "value": a.value != 0 if a.op == "set" else a.value > 0})
            else:
                out.append({"op": a.op, "path": path, "value": a.value})
        return out

    def reason(self, c: Condition) -> str:
        var = self.inp.variables.get(c.var)
        name = c.var.replace("_", " ")
        if var and var[1] == "bool":
            meaning = var[2].meaning.strip().rstrip(".")
            return f"Requires: {meaning}" if meaning else f"Requires {name}"
        return f"Requires {name} {OP_WORDS[c.op]} {c.value}"

    def present(self, stage: Stage, events: tuple[Event, ...], node_id: str) -> str:
        state = self.scene_state(stage)
        key = cm.canonical(state)
        if key not in self.states:
            self.states[key] = f"s{len(self.states) + 1}"
            self.scene_docs[self.states[key]] = state
        cue_events = []
        for event_id, cue, gain in events:
            lid = self.asset(f"sfx.{cue}", event_id)
            if lid:
                cue_events.append({"id": event_id, "trigger": "onNodeEnter", "type": "sound", "asset": lid,
                                   "gain": round(min(1.0, max(0.0, gain)), 3), "resumePolicy": "suppress"})  # fmt: skip
        self.cues[node_id] = {"scene": self.states[key], "events": cue_events}
        return node_id

    def asset(self, lid: str, where: str, fallback: str | None = None) -> str | None:
        if lid not in self.inp.lids:
            if fallback and fallback in self.inp.lids:
                self.warn("warning", "asset_fallback", where, f"{lid} is not in the manifest; using {fallback}")
                lid = fallback
            else:
                self.warn("error", "unknown_asset", where, f"{lid} is not in the story's asset manifest")
                return None
        self.assets.add(lid)
        return lid

    def scene_state(self, st: Stage) -> dict[str, Any]:
        where = f"{self.cid}:{st.location}"
        chars = []
        for cid, expr, slot in sorted(st.shown, key=lambda x: (SLOT_ORDER.get(x[2], 1), x[0])):
            lid = self.asset(f"char.{cid}.{expr}", where, fallback=f"char.{cid}.neutral")
            if lid:
                chars.append({"characterId": cid, "asset": lid, "slot": slot})
        cg = self.asset(f"cg.{st.cg}", where) if st.cg else None
        music_lid = self.asset(f"music.{st.mood}", where) if st.mood and st.mood != "silence" else None
        amb_name = st.amb or st.location
        amb_lid = f"amb.{amb_name}" if amb_name != "none" and f"amb.{amb_name}" in self.inp.lids else None
        if amb_lid:
            self.assets.add(amb_lid)
        loops = []
        for cue, gain in st.loops:
            lid = self.asset(f"sfx.{cue}", where)
            if lid:
                loops.append({"asset": lid, "gain": round(min(1.0, gain), 3), "loop": True, "transitionMs": 200})
        times = [lid for lid in self.inp.lids if lid.startswith(f"bg.{st.location}.")]
        return {"background": self.asset(f"bg.{st.location}.{st.time}", where, fallback=times[0] if times else None),
                "characters": chars, "overlays": [{"asset": cg, "kind": "cg"}] if cg else [],
                "layoutProfile": "vn.cg" if cg else "vn.stage",
                "audio": {"music": {"asset": music_lid, "gain": 1.0, "loop": True, "transitionMs": st.music_fade} if music_lid else None,
                          "ambience": {"asset": amb_lid, "gain": round(min(1.0, st.amb_level or 1.0), 3), "loop": True,
                                       "transitionMs": st.amb_fade} if amb_lid else None,
                          "loops": loops}}  # fmt: skip

    @staticmethod
    def inst(base: str, k: int) -> str:
        return base if k == 0 else f"{base}.p{k}"

    def merge(self, threads: list[Thread], where: str) -> list[Thread]:
        groups: dict[tuple[Stage, tuple[Event, ...]], Thread] = {}
        for t in threads:
            key = (t.stage, t.events)
            if key in groups:
                groups[key].inbound += t.inbound
            else:
                groups[key] = Thread(t.stage, t.events, list(t.inbound))
        out = list(groups.values())
        if len(out) > MAX_THREADS:
            self.warn("warning", "staging_merged", where, "Too many staging paths after gated lines; later ones share the first one's stage")
            for extra in out[MAX_THREADS:]:
                out[0].inbound += extra.inbound
            out = out[:MAX_THREADS]
        return out

    # ------------------------------------------------------------------ scene walk
    def build(self) -> None:
        for sid in self.scene_ids:
            self.run_scene(next(s for s in self.inp.script.scenes if s.id == sid))
        for container, key, scene_id in self.pending:
            target = self.entry_of.get(scene_id)
            if target:
                container[key] = target
            else:
                self.warn("error", "bad_target", self.cid, f"No entry for scene {scene_id!r}")
        entries = self.inp.entries[self.cid]
        if len(entries) == 1:
            self.entry = self.entry_of.get(entries[0]) or ""
        else:
            path = route_path(self.cid)
            self.entry = f"{self.cid}.entry"
            self.add({"id": self.entry, "type": "condition", "fallback": self.entry_of.get(entries[0]),
                      "cases": [{"when": {"path": path, "op": "eq", "value": i}, "target": self.entry_of.get(s)}
                                for i, s in enumerate(entries) if i]})  # fmt: skip

    def run_scene(self, scene: Scene) -> None:
        holder: dict[str, Any] = {"target": None}
        stage = Stage(scene.location, scene.time, mood=scene.music)
        threads = [Thread(stage, (), [(holder, "target")])]
        for i, node in enumerate(scene.nodes):
            if not threads:
                self.warn("warning", "dead_node", f"{scene.id}#{i}", "Nothing can reach this node (after the scene's exit)")
                break
            threads = self.step(scene, i, node, threads)
        if threads:
            self.warn("error", "no_exit", scene.id, "A path through this scene runs past its last node")
        self.entry_of[scene.id] = holder["target"]

    def step(self, scene: Scene, i: int, node: Node, threads: list[Thread]) -> list[Thread]:
        base = f"{scene.id}.n{i}"
        skipped: list[Thread] = []
        if node.when:
            passed: list[Thread] = []
            for k, t in enumerate(threads):
                gate = self.add({"id": self.inst(f"{base}.w", k), "type": "condition", "fallback": None,
                                 "cases": [{"when": self.conds(node.when), "target": None}]})  # fmt: skip
                self.link(t.inbound, gate["id"])
                passed.append(Thread(t.stage, t.events, [(gate["cases"][0], "target")]))
                skipped.append(Thread(t.stage, t.events, [(gate, "fallback")]))
            threads = passed
        return self.merge(self.play(scene, i, node, base, threads) + skipped, base)

    def text_key(self, scene: Scene, i: int, suffix: str = "") -> str:
        return f"{self.cid}.{scene.id}.n{i}{suffix}"

    def play(self, scene: Scene, i: int, node: Node, base: str, threads: list[Thread]) -> list[Thread]:
        kind = node.kind
        if kind in ("say", "narrate"):
            return self.line(threads, base, self.text_key(scene, i), node.text or "",
                             node.speaker if kind == "say" else None, node.expression, node.position)  # fmt: skip
        if kind == "place":
            threads = [Thread(apply_staging(t.stage, node, base)[0], t.events, t.inbound) for t in threads]
            return self.line(threads, base, self.text_key(scene, i), node.text, None, None, None) if node.text else threads
        if kind in ("show", "hide", "sfx", "cg", "music", "ambience"):
            out = []
            for t in threads:
                stage, events = apply_staging(t.stage, node, base)
                out.append(Thread(stage, t.events + events, t.inbound))
            return out
        if kind == "set":
            out = []
            for k, t in enumerate(threads):
                action = self.add({"id": self.inst(base, k), "type": "action", "effects": self.effects(node.assign), "next": None})
                self.link(t.inbound, action["id"])
                out.append(Thread(t.stage, t.events, [(action, "next")]))
            return out
        if kind == "choice":
            return self.choice(scene, i, node, base, threads)
        if kind == "branch":
            for k, t in enumerate(threads):
                nid = self.inst(base, k)
                cond = self.add({"id": nid, "type": "condition", "fallback": None,
                                 "cases": [{"when": self.conds(b.when), "target": None} for b in node.branches]})  # fmt: skip
                self.link(t.inbound, nid)
                for c, (case, b) in enumerate(zip(cond["cases"], node.branches, strict=True)):
                    self.route([(case, "target")], f"{nid}.c{c}", b.goto, t.stage, t.events)
                self.route([(cond, "fallback")], f"{nid}.f", node.goto, t.stage, t.events)
            return []
        if kind == "jump":
            for k, t in enumerate(threads):
                self.route(t.inbound, self.inst(base, k), node.goto, t.stage, t.events)
            return []
        if kind == "ending":
            self.add({"id": base, "type": "ending", "endingId": node.ending})
            self.endings.add(node.ending or "")
            for t in threads:
                self.link(t.inbound, base)
            return []
        self.warn("warning", "unknown_kind", base, f"Node kind {kind!r} is not compiled")
        return threads

    def line(self, threads: list[Thread], base: str, key: str, text: str, speaker: str | None, expression: str | None,
             position: str | None) -> list[Thread]:  # fmt: skip
        self.texts[key] = text
        out = []
        for k, t in enumerate(threads):
            nid = self.inst(base, k)
            stage = t.stage.with_char(speaker, expression, position) if speaker else t.stage
            node: dict[str, Any] = {"id": nid, "type": "dialogue" if speaker else "narration", "textKey": key, "next": None}
            if speaker:
                node["speakerId"] = speaker
                self.speakers.add(speaker)
            node["presentationRef"] = self.present(stage, t.events, nid)
            self.add(node)
            self.link(t.inbound, nid)
            out.append(Thread(stage, (), [(node, "next")]))
        return out

    def choice(self, scene: Scene, i: int, node: Node, base: str, threads: list[Thread]) -> list[Thread]:
        key = self.text_key(scene, i)
        options = []
        for j, c in enumerate(node.choices):
            self.texts[f"{key}.o{j}"] = c.text
            opt: dict[str, Any] = {"id": f"o{j}", "textKey": f"{key}.o{j}", "effects": self.effects(c.set), "target": None}
            if c.require:
                opt["enabledIf"] = self.cond(c.require)
                opt["disabledReasonKey"] = f"{key}.o{j}.locked"
                self.texts[f"{key}.o{j}.locked"] = self.reason(c.require)
            options.append(opt)
        locked = bool(options) and all("enabledIf" in o for o in options)
        out: list[Thread] = []
        for k, t in enumerate(threads):
            nid = self.inst(base, k)
            copies = [(nid, options)]
            if locked:  # every option is conditional: when none holds, offer them all (as the demo player does)
                gate = self.add({"id": f"{nid}.gate", "type": "condition", "fallback": f"{nid}.open",
                                 "cases": [{"when": {"any": [o["enabledIf"] for o in options]}, "target": nid}]})  # fmt: skip
                self.link(t.inbound, gate["id"])
                copies.append((f"{nid}.open", [{k2: v for k2, v in o.items() if k2 not in ("enabledIf", "disabledReasonKey")} for o in options]))
            cue = self.present(t.stage, t.events, nid)
            merge_in: list[tuple[dict[str, Any], str]] = []
            for cid, opts in copies:
                opts = [dict(o) for o in opts]
                self.add({"id": cid, "type": "choice", "presentationRef": cue, "options": opts})
                if cid == nid and not locked:
                    self.link(t.inbound, nid)
                for j, (opt, c) in enumerate(zip(opts, node.choices, strict=True)):
                    if c.goto:
                        self.route([(opt, "target")], f"{cid}.o{j}", c.goto, t.stage, ())
                    else:
                        merge_in += self.reaction(c.reaction, f"{cid}.o{j}", f"{key}.o{j}", t.stage, [(opt, "target")])
            if any(c.goto is None for c in node.choices):
                out.append(Thread(t.stage, (), merge_in))  # the chapter carries on from the stage before the choice
        return out

    def reaction(self, lines: list[Line], base: str, key: str, stage: Stage,
                 inbound: list[tuple[dict[str, Any], str]]) -> list[tuple[dict[str, Any], str]]:  # fmt: skip
        threads = [Thread(stage, (), inbound)]
        for r, line in enumerate(lines):
            threads = self.line(threads, f"{base}.r{r}", f"{key}.r{r}", line.text, line.speaker, line.expression, None)
        return threads[0].inbound

    def route(self, inbound: list[tuple[dict[str, Any], str]], base: str, target: str | None, stage: Stage,
              events: tuple[Event, ...]) -> None:  # fmt: skip
        """Point `inbound` at scene `target`: inside this chapter directly, otherwise through a chapterEnd."""
        chapter = self.inp.chapter_of.get(target or "")
        if chapter is None:
            self.warn("error", "bad_target", base, f"Leads to unknown scene {target!r}")
            return
        if chapter == self.cid:
            self.pending += [(c, k, target) for c, k in inbound]  # type: ignore[misc]
            return
        end = f"{base}.end"
        self.add({"id": end, "type": "chapterEnd", "presentationRef": self.present(stage, events, end), "nextChapter": chapter})
        first = end
        entries = self.inp.entries[chapter]
        if len(entries) > 1:
            first = f"{base}.route"
            self.add({"id": first, "type": "action", "next": end,
                      "effects": [{"op": "set", "path": route_path(chapter), "value": entries.index(target)}]})  # type: ignore[arg-type]
        self.link(inbound, first)


def _holds(op: str, v: int, x: int) -> bool:
    return {"==": v == x, "!=": v != x, ">=": v >= x, "<=": v <= x, ">": v > x, "<": v < x}[op]


def route_path(chapter_id: str) -> str:
    return f"route.{chapter_id}"


# ---------------------------------------------------------------------- rendering documents


@dataclass
class Rendered:
    docs: dict[str, bytes]  # object key → exact bytes
    blobs: dict[str, Path]  # blob key → source file
    gameplay_hash: str
    graphs: dict[str, dict[str, Any]]
    state_schema: dict[str, Any]
    counts: dict[str, int]


def state_schema(inputs: Inputs) -> dict[str, Any]:
    variables: dict[str, Any] = {}
    for path, type_, v in inputs.variables.values():
        if type_ == "bool":
            variables[path] = {"type": "bool", "default": v.initial != 0, "overflow": "reject"}
        else:
            spec: dict[str, Any] = {"type": "int", "default": v.initial, "overflow": "clamp"}
            if v.min is not None:
                spec["min"] = v.min
            if v.max is not None:
                spec["max"] = v.max
            variables[path] = spec
    for chapter, n in inputs.route_vars.items():
        variables[route_path(chapter)] = {"type": "int", "default": 0, "min": 0, "max": n - 1, "overflow": "reject"}
    return {"version": 1, "variables": dict(sorted(variables.items()))}


def gameplay_hash(schema: dict[str, Any], entry_chapter: str, graphs: dict[str, dict[str, Any]]) -> str:
    """Identity of the progression graph: node ids, types, targets, conditions, effects and the state schema.
    Text keys, speakers, presentation and assets do not count."""
    drop = {"textKey", "presentationRef", "speakerId", "disabledReasonKey"}

    def view(node: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in node.items() if k not in drop}
        if "options" in out:
            out["options"] = [{k: v for k, v in o.items() if k not in drop} for o in out["options"]]
        return out

    chapters = {cid: {"entry": g["entry"], "nodes": sorted((view(n) for n in g["nodes"]), key=lambda n: n["id"])}
                for cid, g in graphs.items()}  # fmt: skip
    return cm.sha256(cm.canonical({"stateSchema": schema, "entryChapter": entry_chapter, "chapters": chapters}))


def _reachable(nodes: dict[str, dict[str, Any]], entry: str) -> list[str]:
    seen: list[str] = []
    stack = [entry]
    known = set()
    while stack:
        nid = stack.pop()
        if nid in known or nid not in nodes:
            continue
        known.add(nid)
        seen.append(nid)
        stack += reversed(cm.node_targets(nodes[nid]))
    return seen


def _startup(nodes: dict[str, dict[str, Any]], entry: str) -> list[str]:
    """The entry, the automatic steps after it, and the first nodes the reader sees."""
    out: list[str] = []
    frontier = [entry]
    for _ in range(cm.MAX_AUTO_STEPS):
        nxt = []
        for nid in frontier:
            if nid in out or nid not in nodes:
                continue
            out.append(nid)
            if nodes[nid]["type"] in cm.AUTOMATIC:
                nxt += cm.node_targets(nodes[nid])
        if not nxt:
            break
        frontier = nxt
    return out


def _cue_assets(presentation: dict[str, Any], cue_id: str) -> set[str]:
    cue = presentation["cues"].get(cue_id)
    if cue is None:
        return set()
    scene = presentation["scenes"][cue["scene"]]
    found = {scene["background"]} | {c["asset"] for c in scene["characters"]} | {o["asset"] for o in scene["overlays"]}
    audio = scene["audio"]
    found |= {x["asset"] for x in (audio["music"], audio["ambience"]) if x} | {x["asset"] for x in audio["loops"]}
    found |= {e["asset"] for e in cue["events"]}
    return {f for f in found if f}


def _downloads(inputs: Inputs, cid: str, graph: dict[str, Any], presentation: dict[str, Any],
               docs: dict[str, bytes]) -> dict[str, Any]:  # fmt: skip
    nodes = {n["id"]: n for n in graph["nodes"]}
    startup = _startup(nodes, graph["entry"])
    reach = _reachable(nodes, graph["entry"])
    first = set().union(*[_cue_assets(presentation, nodes[n].get("presentationRef", "")) for n in startup if "presentationRef" in nodes[n]])
    every = set().union(*[_cue_assets(presentation, nodes[n].get("presentationRef", "")) for n in reach if "presentationRef" in nodes[n]])

    def size(lids: set[str], qualities: tuple[str, ...]) -> int:
        total = 0
        for lid in lids:
            a = inputs.assets.get(lid)
            if a is None:
                continue
            total += max((m.size for m in a.standard), default=0)
            if "high" in qualities:
                total += max((m.size for m in a.high), default=0)
        return total

    doc_keys = sorted(docs)
    doc_bytes = sum(len(docs[k]) for k in doc_keys)
    images = {lid for lid in first if inputs.assets.get(lid) and inputs.assets[lid].kind == "image"}
    profile = lambda lids, q: {"documents": doc_keys, "assets": sorted(lids), "qualities": list(q),  # noqa: E731
                               "bytes": doc_bytes + size(lids, q)}  # fmt: skip
    return {"kind": "vn.downloads", "schemaVersion": cm.SCHEMA_VERSION, "storyId": inputs.story_id, "chapterId": cid,
            "startupNodes": startup, "profiles": {"core": profile(images, ("standard",)), "standard": profile(every, ("standard",)),
                                                  "enhanced": profile(every, ("standard", "high"))}}  # fmt: skip


def _registry(inputs: Inputs, cid: str, lids: set[str], blobs: dict[str, Path]) -> tuple[dict[str, Any], dict[str, str]]:
    assets: dict[str, Any] = {}
    alts: dict[str, str] = {}
    for lid in sorted(lids):
        a = inputs.assets.get(lid)
        if a is None:
            continue
        for m in a.standard + a.high:
            blobs[m.key] = m.path
        if a.kind == "image":
            alts[f"alt.{lid}"] = a.alt
            variants = [{"id": m.ext.lstrip("."), "layout": _layout(m), "quality": q, "width": m.width or 1,
                         "height": m.height or 1, "file": m.descriptor()}
                        for q, group in (("standard", a.standard), ("high", a.high)) for m in group]  # fmt: skip
            assets[lid] = {"type": "image", "altKey": f"alt.{lid}", "variants": variants}
        else:
            variants = [{"id": m.ext.lstrip("."), "codec": "vorbis" if m.ext == ".ogg" else "aac",
                         "container": "ogg" if m.ext == ".ogg" else "mp4", "quality": "standard",
                         "durationMs": int(m.duration_ms or 0), "file": m.descriptor()} for m in a.standard]  # fmt: skip
            assets[lid] = {"type": "audio", "loop": a.loop, "variants": variants}
    return {"kind": "vn.assets", "schemaVersion": cm.SCHEMA_VERSION, "storyId": inputs.story_id, "chapterId": cid,
            "assets": assets}, alts  # fmt: skip


def _layout(m: Media) -> str | None:
    if not m.width or not m.height:
        return None
    ratio = m.width / m.height
    return "landscape" if ratio > 1.15 else "portrait" if ratio < 0.87 else "square"


def build_chapters(inputs: Inputs) -> list[ChapterBuilder]:
    builders = []
    for cid, title, scene_ids in inputs.chapters:
        b = ChapterBuilder(inputs, cid, title, scene_ids)
        b.build()
        builders.append(b)
    return builders


def render(inputs: Inputs, builders: list[ChapterBuilder], rid: str, graph_id: str,
           migration: dict[str, Any] | None = None, *, draft: bool = False) -> Rendered:  # fmt: skip
    sid, lang = inputs.story_id, inputs.lang
    rel = f"stories/{sid}/releases/{rid}"
    docs: dict[str, bytes] = {}
    blobs: dict[str, Path] = {}
    chars = {c.id: c for c in inputs.script.characters}
    endings = {e.id: e for e in inputs.script.endings}
    schema = state_schema(inputs)
    graphs: dict[str, dict[str, Any]] = {}
    chapter_refs = []
    nodes_total = 0
    for b in builders:
        cid = b.cid
        graph = {"kind": "vn.chapter", "schemaVersion": cm.SCHEMA_VERSION, "storyId": sid, "graphId": graph_id,
                 "chapterId": cid, "entry": b.entry, "speakers": {s: {"nameKey": f"cast.{s}.name"} for s in sorted(b.speakers)},
                 "nodes": list(b.nodes.values())}  # fmt: skip
        graphs[cid] = graph
        nodes_total += len(b.nodes)
        presentation = {"kind": "vn.presentation", "schemaVersion": cm.SCHEMA_VERSION, "storyId": sid, "chapterId": cid,
                        "scenes": b.scene_docs, "cues": b.cues}  # fmt: skip
        registry, alts = _registry(inputs, cid, b.assets, blobs)
        strings = dict(b.texts) | alts | {f"cast.{s}.name": chars[s].name if s in chars else s for s in b.speakers}
        strings[f"chapter.{cid}.title"] = b.title
        for e in sorted(b.endings):
            if e in endings:
                strings[f"ending.{e}.title"], strings[f"ending.{e}.summary"] = endings[e].title, endings[e].summary
        locale = {"kind": "vn.locale", "schemaVersion": cm.SCHEMA_VERSION, "storyId": sid, "locale": lang, "strings": strings}
        part = {f"{rel}/{cid}/graph.json": graph, f"{rel}/{cid}/presentation.json": presentation,
                f"{rel}/{cid}/assets.json": registry, f"{rel}/{cid}/locales/{lang}.json": locale}  # fmt: skip
        chapter_docs = {k: cm.canonical(v) for k, v in part.items()}
        downloads = _downloads(inputs, cid, graph, presentation, chapter_docs)
        chapter_docs[f"{rel}/{cid}/downloads.json"] = cm.canonical(downloads)
        desc = {k: cm.descriptor(k, data) for k, data in chapter_docs.items()}
        bundle = {"kind": "vn.chapter.bundle", "schemaVersion": cm.SCHEMA_VERSION, "storyId": sid, "releaseId": rid,
                  "chapterId": cid, "graph": desc[f"{rel}/{cid}/graph.json"], "presentation": desc[f"{rel}/{cid}/presentation.json"],
                  "assets": desc[f"{rel}/{cid}/assets.json"], "locales": {lang: desc[f"{rel}/{cid}/locales/{lang}.json"]},
                  "downloads": desc[f"{rel}/{cid}/downloads.json"]}  # fmt: skip
        chapter_docs[f"{rel}/{cid}/bundle.json"] = cm.canonical(bundle)
        docs |= chapter_docs
        chapter_refs.append({"id": cid, "titleKey": f"chapter.{cid}.title", "accessPolicyId": "default",
                             "manifest": cm.descriptor(f"{rel}/{cid}/bundle.json", chapter_docs[f"{rel}/{cid}/bundle.json"])})  # fmt: skip
    release_strings = {"story.title": inputs.title, "story.summary": inputs.summary}
    release_strings |= {f"chapter.{cid}.title": title for cid, title, _ in inputs.chapters}
    for e in inputs.script.endings:
        release_strings[f"ending.{e.id}.title"], release_strings[f"ending.{e.id}.summary"] = e.title, e.summary
    cover = None
    title_art = inputs.assets.get("ui.title")
    if title_art and title_art.standard:
        m = title_art.standard[0]
        blobs[m.key] = m.path
        release_strings["alt.ui.title"] = title_art.alt
        cover = {"asset": "ui.title", "altKey": "alt.ui.title", "width": m.width or 1, "height": m.height or 1, "file": m.descriptor()}
    rlocale = {"kind": "vn.locale", "schemaVersion": cm.SCHEMA_VERSION, "storyId": sid, "locale": lang, "strings": release_strings}
    docs[f"{rel}/locales/{lang}.json"] = cm.canonical(rlocale)
    migrations = []
    if migration is not None:
        mkey = f"{rel}/migration-from-{migration['fromRelease']}.json"
        docs[mkey] = cm.canonical(migration)
        migrations.append(cm.descriptor(mkey, docs[mkey]))
    entry_chapter = inputs.chapter_of.get(inputs.script.start, inputs.chapters[0][0] if inputs.chapters else "")
    release: dict[str, Any] = {
        "kind": "vn.release", "schemaVersion": cm.SCHEMA_VERSION, "storyId": sid, "releaseId": rid, "graphId": graph_id,
        "requires": {"minEngineVersion": cm.ENGINE_VERSION, "capabilities": list(cm.CAPABILITIES)},
        "defaultLocale": lang, "titleKey": "story.title", "summaryKey": "story.summary", "entryChapter": entry_chapter,
        "stateSchema": schema, "chapters": chapter_refs,
        "endings": [{"id": e.id, "kind": e.kind, "titleKey": f"ending.{e.id}.title", "summaryKey": f"ending.{e.id}.summary"}
                    for e in inputs.script.endings],
        "locales": {lang: cm.descriptor(f"{rel}/locales/{lang}.json", docs[f"{rel}/locales/{lang}.json"])},
        "migrations": migrations,
    }  # fmt: skip
    if cover:
        release["cover"] = cover
    if draft:
        release["draft"] = True
    docs[f"{rel}/release.json"] = cm.canonical(release)
    counts = {"chapters": len(builders), "nodes": nodes_total, "assets": len({lid for b in builders for lid in b.assets}),
              "blobs": len(blobs), "documents": len(docs), "bytes": sum(len(d) for d in docs.values()) + sum(
                  p.stat().st_size for p in blobs.values() if p.is_file())}  # fmt: skip
    return Rendered(docs, blobs, gameplay_hash(schema, entry_chapter, graphs), graphs, schema, counts)


# ---------------------------------------------------------------------- local layout, index, previous releases


def releases_root(project: StoryProject, out_dir: str | Path | None = None) -> Path:
    return Path(out_dir).expanduser().resolve() if out_dir else project.root / "releases"


def local_path(root: Path, story_id: str, key: str) -> Path:
    """Where an object key lives in the local mirror (`root` = the story's releases folder)."""
    cm.check_key(key)
    if key.startswith("blobs/"):
        return root / key
    prefix = f"stories/{story_id}/releases/"
    if not key.startswith(prefix):
        raise cm.ContractError(f"{key} is not part of story {story_id}")
    return root / key[len(prefix):]


def load_index(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / INDEX).read_text())
    except (OSError, ValueError):
        return {"kind": "assetforge.vn.releases", "releases": [], "draft": None, "last_report": None}


def save_index(root: Path, index: dict[str, Any]) -> None:
    write_atomic(root / INDEX, json.dumps(index, indent=2, ensure_ascii=False) + "\n", backup=False)


def _release_ids(root: Path, index: dict[str, Any]) -> list[str]:
    found = {p.name for p in root.iterdir() if p.is_dir() and cm.RELEASE_ID.match(p.name)} if root.exists() else set()
    found |= {r["releaseId"] for r in index.get("releases", [])}
    return sorted(found)


@dataclass
class Loaded:
    release: dict[str, Any]
    graphs: dict[str, dict[str, Any]]
    strings: dict[str, str]


def load_release(directory: Path) -> Loaded:
    release = json.loads((directory / "release.json").read_bytes())
    root, sid = directory.parent, release["storyId"]
    graphs: dict[str, dict[str, Any]] = {}
    strings: dict[str, str] = {}
    for ref in release["chapters"]:
        bundle = json.loads(local_path(root, sid, ref["manifest"]["key"]).read_bytes())
        graphs[ref["id"]] = json.loads(local_path(root, sid, bundle["graph"]["key"]).read_bytes())
        for desc in bundle["locales"].values():
            strings |= json.loads(local_path(root, sid, desc["key"]).read_bytes())["strings"]
    return Loaded(release, graphs, strings)


def release_dir(project: StoryProject, release_id: str | None = None, out_dir: str | Path | None = None) -> Path:
    root = releases_root(project, out_dir)
    index = load_index(root)
    if release_id is None:
        ids = [r["releaseId"] for r in index.get("releases", [])]
        if not ids:
            raise NotFoundError("No release compiled yet", hint="Run `assetforge story contract` first")
        release_id = ids[-1]
    path = root / release_id
    if not (path / "release.json").is_file():
        raise NotFoundError(f"Release {release_id} not found in {root}")
    return path


# ---------------------------------------------------------------------- migration


_NODE = re.compile(r"^(?P<scene>[a-z][a-z0-9_]*)\.n(?P<i>\d+)")


def migration(story_id: str, old: Loaded, to_release: str, to_graph: str, graphs: dict[str, dict[str, Any]],
              strings: dict[str, str], schema: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    new_nodes = {n["id"]: (cid, n) for cid, g in graphs.items() for n in g["nodes"]}
    by_text: dict[tuple[str, str, str], list[str]] = {}
    for nid, (cid, n) in new_nodes.items():
        if n.get("textKey") in strings:
            by_text.setdefault((cid, n["type"], strings[n["textKey"]]), []).append(nid)
    entry = graphs.get(next(iter(graphs), ""), {}).get("entry", "")
    aliases: dict[str, str] = {}
    for cid, g in old.graphs.items():
        for n in g["nodes"]:
            if n["id"] in new_nodes:
                continue
            text = old.strings.get(n.get("textKey", ""))
            same = by_text.get((cid, n["type"], text or ""), []) if text else []
            if len(same) == 1:
                aliases[n["id"]] = same[0]
                continue
            aliases[n["id"]] = _nearest(n["id"], cid, new_nodes, graphs) or entry
    old_vars, new_vars = old.release["stateSchema"]["variables"], schema["variables"]
    ops: list[dict[str, Any]] = []
    removed = {p.split(".", 1)[1]: p for p in old_vars if p not in new_vars}
    for path, spec in new_vars.items():
        if path in old_vars:
            continue
        if path.split(".", 1)[1] in removed:
            ops.append({"op": "rename", "from": removed[path.split(".", 1)[1]], "to": path})
        else:
            ops.append({"op": "default", "path": path, "value": spec["default"]})
    return {"kind": "vn.migration", "schemaVersion": cm.SCHEMA_VERSION, "storyId": story_id,
            "fromRelease": old.release["releaseId"], "toRelease": to_release, "fromGraphId": old.release["graphId"],
            "toGraphId": to_graph, "nodeAliases": dict(sorted(aliases.items())), "stateOperations": ops}  # fmt: skip


def _nearest(old_id: str, cid: str, new_nodes: dict[str, tuple[str, dict[str, Any]]], graphs: dict[str, dict[str, Any]]) -> str | None:
    """The closest surviving presenting node of the same scene at or before the old position, else the chapter entry."""
    m = _NODE.match(old_id)
    if m:
        scene, index = m["scene"], int(m["i"])
        best = None
        for nid, (_, n) in new_nodes.items():
            mm = _NODE.fullmatch(nid)
            if mm and mm["scene"] == scene and int(mm["i"]) <= index and n["type"] in cm.PRESENTING:
                if best is None or int(mm["i"]) > best[0]:
                    best = (int(mm["i"]), nid)
        if best:
            return best[1]
    return graphs[cid]["entry"] if cid in graphs else None


# ---------------------------------------------------------------------- compile


def compile_release(lib: Library, project: StoryProject, out_dir: str | Path | None = None, *, allow_missing: bool = False,
                    derive: bool = True) -> ReleaseReport:  # fmt: skip
    """Compile the story into a release (or report why it can't be one). `allow_missing` writes a draft (never
    publishable) when assets are missing or the story is unfinished; `derive=False` never adds web-format
    variants to the library (read-only use)."""
    root = releases_root(project, out_dir)
    inputs = gather(lib, project)
    builders = build_chapters(inputs)
    wanted = set().union(*(b.assets for b in builders)) | ({"ui.title"} if "ui.title" in inputs.lids else set())
    _resolve_assets(lib, inputs, inputs.items, wanted, derive)
    index = load_index(root)
    index["storyId"] = inputs.story_id
    latest = index["releases"][-1] if index.get("releases") else None
    blocking = [f for f in inputs.findings if f.level == "error"]
    missing = sorted({f.where for f in inputs.findings if f.code == "missing_asset"})
    report = ReleaseReport(story_id=inputs.story_id, status="blocked", previous=latest["releaseId"] if latest else None,
                           findings=inputs.findings, missing_assets=missing)  # fmt: skip
    probe = render(inputs, builders, "r0000", "__graph__")  # identity of the content, whatever its release id
    report.counts = probe.counts
    if blocking and not allow_missing:
        report.message = f"{len(blocking)} blocking finding(s); fix them, or pass --allow-missing for a draft"
        return _finish(root, index, report)
    old = load_release(root / latest["releaseId"]) if latest and (root / latest["releaseId"] / "release.json").is_file() else None
    content = cm.sha256(b"".join(k.encode() + cm.sha256(v).encode() for k, v in sorted(probe.docs.items())))
    if old is not None:
        old_hash = gameplay_hash(old.release["stateSchema"], old.release["entryChapter"], old.graphs)
        changed = old_hash != probe.gameplay_hash
        n = int(old.release["graphId"].rsplit(".", 1)[-1]) + (1 if changed else 0)
    else:
        changed, n = True, 1
    graph_id = f"{inputs.story_id}.graph.{n}"
    report.graph_id, report.graph_changed = graph_id, changed if old is not None else None
    if not blocking and latest and latest.get("contentHash") == content and old is not None:
        report.status, report.release_id, report.valid = "identical", latest["releaseId"], bool(latest.get("valid"))
        report.path, report.message = str(root / latest["releaseId"]), f"identical to {latest['releaseId']}, no new release"
        return _finish(root, index, report)
    draft = bool(blocking)
    ids = _release_ids(root, index)
    rid = DRAFT if draft else f"r{(int(ids[-1][1:]) + 1) if ids else 1:04d}"
    strings = {}
    for b in builders:
        strings |= b.texts
    mig = migration(inputs.story_id, old, rid, graph_id, probe.graphs, strings, probe.state_schema) if old else None
    rendered = render(inputs, builders, rid, graph_id, mig, draft=draft)
    if old is not None:  # what changed since the previous release, written next to it (docs/narrative/27)
        dkey, ddoc = delta_for(root, inputs.story_id, old.release["releaseId"], rendered.docs, rid)
        rendered.docs[dkey] = cm.canonical(ddoc)
    path = _write(root, inputs.story_id, rid, rendered)
    issues = validate_release(path)
    report.findings = inputs.findings + [i for i in issues if i not in inputs.findings]
    report.valid = not draft and not any(i.level == "error" for i in issues)
    report.status, report.release_id, report.path = ("draft" if draft else "created"), rid, str(path)
    report.counts = rendered.counts
    if mig:
        report.migration = f"stories/{inputs.story_id}/releases/{rid}/migration-from-{mig['fromRelease']}.json"
    entry = {"releaseId": rid, "graphId": graph_id, "gameplayHash": rendered.gameplay_hash, "contentHash": content,
             "created": utcnow(), "valid": report.valid, "errors": sum(i.level == "error" for i in report.findings),
             "warnings": sum(i.level == "warning" for i in report.findings), "published": None,
             "previous": latest["releaseId"] if latest else None}  # fmt: skip
    if draft:
        index["draft"] = entry
        report.message = f"draft written ({len(blocking)} blocking finding(s)); a draft can never be published"
    else:
        index["releases"].append(entry)
        report.message = f"{rid} created ({'new graph ' + graph_id if changed else 'same graph ' + graph_id})"
    return _finish(root, index, report)


def delta_for(root: Path, story_id: str, old_rid: str, new_docs: dict[str, bytes], rid: str) -> tuple[str, dict[str, Any]]:
    """The `vn.delta` from the local release `old_rid` to the documents of release `rid` (deterministic)."""
    from assetforge.story import delta

    old = delta.read_tree(lambda k: local_path(root, story_id, k).read_bytes(), story_id, old_rid)
    new = delta.read_tree(lambda k: new_docs[k], story_id, rid)
    return delta.delta_key(story_id, rid, old_rid), delta.delta(old, new)


def delta_paths(directory: Path) -> list[Path]:
    return sorted(directory.glob("delta-from-r*.json"))


def _finish(root: Path, index: dict[str, Any], report: ReleaseReport) -> ReleaseReport:
    index["last_report"] = {**report.model_dump(mode="json"), "at": utcnow()}
    save_index(root, index)
    return report


def _write(root: Path, story_id: str, rid: str, rendered: Rendered) -> Path:
    """Blobs first (content-addressed, shared), then the release folder, staged and moved into place whole."""
    final = root / rid
    if rid != DRAFT and final.exists():
        raise ConflictError(f"Release folder {final} already exists; releases are never modified")
    for key, src in sorted(rendered.blobs.items()):
        target = root / key
        if target.is_file() and target.stat().st_size == src.stat().st_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
    staging = root / f".staging-{rid}"
    shutil.rmtree(staging, ignore_errors=True)
    prefix = f"stories/{story_id}/releases/{rid}/"
    for key, data in rendered.docs.items():
        path = staging / key[len(prefix):]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if rid == DRAFT:
        shutil.rmtree(final, ignore_errors=True)
    os.replace(staging, final)
    return final


# ---------------------------------------------------------------------- validation of a written release


@dataclass
class Obj:
    key: str
    path: Path
    media_type: str
    bytes: int
    sha256: str
    role: Literal["blob", "document", "release"]


def release_objects(directory: Path) -> tuple[dict[str, Any], list[Obj]]:
    """Every object of a release in upload order: blobs, chapter documents, release documents, release.json last."""
    release = json.loads((directory / "release.json").read_bytes())
    root, sid = directory.parent, release["storyId"]
    blobs: dict[str, Obj] = {}
    documents: dict[str, Obj] = {}

    def obj(desc: dict[str, Any], role: Literal["blob", "document", "release"]) -> Obj:
        return Obj(desc["key"], local_path(root, sid, desc["key"]), desc["mediaType"], desc["bytes"], desc["sha256"], role)

    for ref in release["chapters"]:
        bundle_path = local_path(root, sid, ref["manifest"]["key"])
        bundle = json.loads(bundle_path.read_bytes())
        for name in ("graph", "presentation", "assets", "downloads"):
            documents[bundle[name]["key"]] = obj(bundle[name], "document")
        for desc in bundle["locales"].values():
            documents[desc["key"]] = obj(desc, "document")
        registry = json.loads(local_path(root, sid, bundle["assets"]["key"]).read_bytes())
        for asset in registry["assets"].values():
            for v in asset["variants"]:
                blobs[v["file"]["key"]] = obj(v["file"], "blob")
        documents[ref["manifest"]["key"]] = obj(ref["manifest"], "document")
    for desc in [*release["locales"].values(), *release.get("migrations", [])]:
        documents[desc["key"]] = obj(desc, "document")
    if release.get("cover"):
        blobs[release["cover"]["file"]["key"]] = obj(release["cover"]["file"], "blob")
    for path in delta_paths(directory):  # not named by release.json; the catalog carries its descriptor
        key = f"stories/{sid}/releases/{release['releaseId']}/{path.name}"
        raw = path.read_bytes()
        documents[key] = Obj(key, path, "application/json", len(raw), cm.sha256(raw), "document")
    data = (directory / "release.json").read_bytes()
    top = Obj(f"stories/{sid}/releases/{release['releaseId']}/release.json", directory / "release.json", "application/json",
              len(data), cm.sha256(data), "release")  # fmt: skip
    return release, [*sorted(blobs.values(), key=lambda o: o.key), *documents.values(), top]


def _model(issues: list[Issue], model: type[Any], data: dict[str, Any], where: str) -> bool:
    try:
        model.model_validate(data)
        return True
    except ValidationError as exc:
        for err in exc.errors()[:8]:
            issues.append(finding("error", "schema", where, f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"))
        return False


def _read(issues: list[Issue], root: Path, sid: str, desc: dict[str, Any], where: str) -> dict[str, Any] | None:
    """Check a descriptor against the file it names and parse it when it is JSON."""
    try:
        path = local_path(root, sid, desc["key"])
    except (cm.ContractError, KeyError) as exc:
        issues.append(finding("error", "bad_key", where, str(exc)))
        return None
    if not path.is_file():
        issues.append(finding("error", "missing_object", where, f"{desc['key']} is missing"))
        return None
    data = path.read_bytes()
    if len(data) != desc.get("bytes") or cm.sha256(data) != desc.get("sha256"):
        issues.append(finding("error", "digest_mismatch", where, f"{desc['key']} does not match its descriptor"))
    if desc.get("mediaType") != cm.media_type(desc["key"]):
        issues.append(finding("error", "media_type", where, f"{desc['key']}: media type {desc.get('mediaType')!r}"))
    if desc.get("mediaType") != "application/json":
        return None
    if len(data) > cm.MAX_DOC_BYTES:
        issues.append(finding("error", "too_large", where, f"{desc['key']} is over {cm.MAX_DOC_BYTES} bytes"))
    try:
        return json.loads(data)
    except ValueError:
        issues.append(finding("error", "bad_json", where, f"{desc['key']} is not JSON"))
        return None


def validate_release(directory: str | Path) -> list[Issue]:
    """Everything a consumer relies on: descriptors match files, documents match the schema, every reference
    resolves (targets, cues, scenes, assets, text keys, speakers, chapters, endings), every reachable choice has an
    available option, no automatic-only cycle, guardrails hold."""
    directory = Path(directory)
    issues: list[Issue] = []
    try:
        release = json.loads((directory / "release.json").read_bytes())
    except (OSError, ValueError) as exc:
        return [finding("error", "no_release", str(directory), f"release.json unreadable: {exc}")]
    if not _model(issues, cm.Release, release, "release.json"):
        return issues
    root, sid = directory.parent, release["storyId"]
    if not release.get("draft") and not cm.RELEASE_ID.match(release["releaseId"]):
        issues.append(finding("error", "release_id", "release.json", f"bad release id {release['releaseId']!r}"))
    if release.get("draft"):
        issues.append(finding("error", "draft", "release.json", "This is a draft; it cannot be published"))
    variables = release["stateSchema"]["variables"]
    chapter_ids = {c["id"] for c in release["chapters"]}
    ending_ids = {e["id"] for e in release["endings"]}
    if release["entryChapter"] not in chapter_ids:
        issues.append(finding("error", "entry_chapter", "release.json", f"Unknown entry chapter {release['entryChapter']!r}"))
    lang = release["defaultLocale"]
    rstrings = {}
    for code, desc in release["locales"].items():
        doc = _read(issues, root, sid, desc, f"locales/{code}")
        if doc and _model(issues, cm.Locale, doc, f"locales/{code}"):
            rstrings |= doc["strings"] if code == lang else {}
    needed = {"story.title", "story.summary"} | {c["titleKey"] for c in release["chapters"]}
    needed |= {e[k] for e in release["endings"] for k in ("titleKey", "summaryKey")}
    if release.get("cover"):
        needed.add(release["cover"]["altKey"])
        _read(issues, root, sid, release["cover"]["file"], "cover")
    issues += [finding("error", "missing_text", "release", f"No {lang} text for {k}") for k in sorted(needed - set(rstrings))]
    for desc in release.get("migrations", []):
        doc = _read(issues, root, sid, desc, "migration")
        if doc and _model(issues, cm.Migration, doc, desc["key"]):
            issues += _check_migration(doc, directory, root, sid, variables)
    for ref in release["chapters"]:
        issues += _check_chapter(root, sid, ref, release, variables, chapter_ids, ending_ids)
    issues += _check_deltas(directory, root, release)
    return issues


def _check_deltas(directory: Path, root: Path, release: dict[str, Any]) -> list[Issue]:
    """Each delta matches the schema, names this release, and (when the older release is here) equals a recompute."""
    issues: list[Issue] = []
    sid, rid = release["storyId"], release["releaseId"]
    for path in delta_paths(directory):
        try:
            doc = json.loads(path.read_bytes())
        except (OSError, ValueError):
            issues.append(finding("error", "bad_json", path.name, "the delta is not JSON"))
            continue
        if not _model(issues, cm.Delta, doc, path.name):
            continue
        if doc["toRelease"] != rid or doc["storyId"] != sid or path.name != f"delta-from-{doc['fromRelease']}.json":
            issues.append(finding("error", "delta", path.name, "the delta does not describe this release"))
            continue
        if (root / doc["fromRelease"] / "release.json").is_file():
            try:
                docs = {o.key: o.path.read_bytes() for o in release_objects(directory)[1] if o.role != "blob"}
                _, expected = delta_for(root, sid, doc["fromRelease"], docs, rid)
            except (OSError, ValueError, KeyError) as exc:
                issues.append(finding("error", "delta", path.name, f"cannot recompute the delta: {exc}"))
                continue
            if cm.canonical(expected) != cm.canonical(doc):
                issues.append(finding("error", "delta_mismatch", path.name, "the delta differs from the two releases it compares"))
    return issues


def _check_migration(doc: dict[str, Any], directory: Path, root: Path, sid: str, variables: dict[str, Any]) -> list[Issue]:
    issues = []
    try:
        loaded = load_release(directory)
    except (OSError, ValueError, KeyError) as exc:
        return [finding("error", "migration", "migration", f"cannot load the release graphs: {exc}")]
    ids = {n["id"] for g in loaded.graphs.values() for n in g["nodes"]}
    issues += [finding("error", "migration_target", "migration", f"alias {old} → {new}: no such node")
               for old, new in doc["nodeAliases"].items() if new not in ids]  # fmt: skip
    for op in doc["stateOperations"]:
        target = op.get("to") or op.get("path")
        if target not in variables:
            issues.append(finding("error", "migration_target", "migration", f"state operation names unknown {target!r}"))
    return issues


def _check_chapter(root: Path, sid: str, ref: dict[str, Any], release: dict[str, Any], variables: dict[str, Any],
                   chapter_ids: set[str], ending_ids: set[str]) -> list[Issue]:  # fmt: skip
    cid = ref["id"]
    issues: list[Issue] = []
    bundle = _read(issues, root, sid, ref["manifest"], f"{cid}/bundle")
    if not bundle or not _model(issues, cm.Bundle, bundle, f"{cid}/bundle"):
        return issues
    docs = {name: _read(issues, root, sid, bundle[name], f"{cid}/{name}") for name in ("graph", "presentation", "assets", "downloads")}
    lang = release["defaultLocale"]
    if lang not in bundle["locales"]:
        issues.append(finding("error", "missing_locale", cid, f"No {lang} locale"))
        return issues
    locale = _read(issues, root, sid, bundle["locales"][lang], f"{cid}/locale")
    models = {"graph": cm.Graph, "presentation": cm.Presentation, "assets": cm.AssetRegistry, "downloads": cm.Downloads}
    if any(docs[n] is None or not _model(issues, models[n], docs[n], f"{cid}/{n}") for n in models) or not locale \
            or not _model(issues, cm.Locale, locale, f"{cid}/locale"):  # fmt: skip
        return issues
    graph, presentation, registry, downloads = docs["graph"], docs["presentation"], docs["assets"], docs["downloads"]
    strings = locale["strings"]
    nodes = {n["id"]: n for n in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        issues.append(finding("error", "duplicate_node", cid, "Node ids are not unique"))
    if graph["graphId"] != release["graphId"] or graph["chapterId"] != cid:
        issues.append(finding("error", "graph_identity", cid, "graphId/chapterId differ from the release"))
    if graph["entry"] not in nodes:
        issues.append(finding("error", "bad_entry", cid, f"Entry {graph['entry']!r} is not a node"))
    texts: set[str] = {f"chapter.{cid}.title"} | {s["nameKey"] for s in graph["speakers"].values()}
    back: dict[str, list[dict[str, Any]]] = {}
    for n in graph["nodes"]:
        where = f"{cid}:{n['id']}"
        for target in cm.node_targets(n):
            if target not in nodes:
                issues.append(finding("error", "bad_target", where, f"Leads to {target!r}, not a node of this chapter"))
        for target in set(cm.node_targets(n)):
            back.setdefault(target, []).append(n)
        for key in ("textKey", "disabledReasonKey"):
            if n.get(key):
                texts.add(n[key])
        if n["type"] == "dialogue" and n["speakerId"] not in graph["speakers"]:
            issues.append(finding("error", "unknown_speaker", where, f"Speaker {n['speakerId']!r} is not declared"))
        if "presentationRef" in n and n["presentationRef"] not in presentation["cues"]:
            issues.append(finding("error", "bad_cue", where, f"No cue {n['presentationRef']!r}"))
        if n["type"] == "chapterEnd" and n["nextChapter"] not in chapter_ids:
            issues.append(finding("error", "bad_chapter", where, f"Unknown chapter {n['nextChapter']!r}"))
        if n["type"] == "ending" and n["endingId"] not in ending_ids:
            issues.append(finding("error", "bad_ending", where, f"Unknown ending {n['endingId']!r}"))
            continue
        if n["type"] == "ending":
            texts |= {f"ending.{n['endingId']}.title", f"ending.{n['endingId']}.summary"}
        conds = [c["when"] for c in n.get("cases", [])] + [o["enabledIf"] for o in n.get("options", []) if o.get("enabledIf")]
        effects = n.get("effects", []) + [e for o in n.get("options", []) for e in o["effects"]]
        for o in n.get("options", []):
            texts |= {o["textKey"]} | ({o["disabledReasonKey"]} if o.get("disabledReasonKey") else set())
        issues += _check_state(conds, effects, variables, where)
    reachable = set(_reachable(nodes, graph["entry"]))
    for n in graph["nodes"]:
        if n["type"] == "choice" and n["id"] in reachable and all(o.get("enabledIf") for o in n["options"]):
            # only reached through a gate that checks one of its options holds
            gate = {"any": [o["enabledIf"] for o in n["options"]]}
            preds = back.get(n["id"], [])
            covered = bool(preds) and all(m["type"] == "condition" and m["fallback"] != n["id"] and all(
                c["when"] == gate for c in m["cases"] if c["target"] == n["id"]) for m in preds)
            if not covered:
                issues.append(finding("error", "choice_may_block", f"{cid}:{n['id']}", "Every option is conditional and nothing "
                                      "guarantees one is available"))  # fmt: skip
    issues += _check_flow(nodes, graph["entry"], cid)
    issues += _check_presentation(presentation, registry, cid)
    for lid, asset in registry["assets"].items():
        if not asset["variants"] and not release.get("draft"):
            issues.append(finding("error", "missing_asset", f"{cid}:{lid}", "Asset has no files"))
        if asset["type"] == "image":
            texts.add(asset["altKey"])
        for v in asset["variants"]:
            _read(issues, root, sid, v["file"], f"{cid}:{lid}")
    missing = sorted(t for t in texts if not strings.get(t, "").strip())
    issues += [finding("error", "missing_text", cid, f"No {lang} text for {k}") for k in missing]
    for nid in downloads["startupNodes"]:
        if nid not in nodes:
            issues.append(finding("error", "bad_startup", cid, f"Startup node {nid!r} is not a node"))
    for name, profile in downloads["profiles"].items():
        issues += [finding("error", "bad_download", f"{cid}:{name}", f"Unknown asset {a!r}") for a in profile["assets"] if a not in registry["assets"]]
    reach = _reachable(nodes, graph["entry"])
    used = set().union(*[_cue_assets(presentation, nodes[n]["presentationRef"]) for n in reach if "presentationRef" in nodes[n]])
    if not used <= set(downloads["profiles"]["standard"]["assets"]):
        issues.append(finding("error", "download_closure", cid, "The standard profile misses assets of reachable nodes"))
    return issues


def _check_state(conds: list[dict[str, Any]], effects: list[dict[str, Any]], variables: dict[str, Any], where: str) -> list[Issue]:
    issues = []
    for cond in conds:
        if cm.condition_depth(cond) > cm.MAX_CONDITION_DEPTH:
            issues.append(finding("error", "condition_depth", where, f"Condition deeper than {cm.MAX_CONDITION_DEPTH}"))
        for c in cm.condition_paths(cond):
            spec = variables.get(c["path"])
            if spec is None:
                issues.append(finding("error", "unknown_state", where, f"Condition reads unknown {c['path']!r}"))
            elif (spec["type"] == "bool") != isinstance(c["value"], bool) or (spec["type"] == "bool" and c["op"] not in ("eq", "ne")):
                issues.append(finding("error", "state_type", where, f"{c['path']} is {spec['type']}; bad comparison"))
    for e in effects:
        spec = variables.get(e["path"])
        if spec is None:
            issues.append(finding("error", "unknown_state", where, f"Effect writes unknown {e['path']!r}"))
        elif (spec["type"] == "bool") != isinstance(e["value"], bool) or (spec["type"] == "bool" and e["op"] == "add"):
            issues.append(finding("error", "state_type", where, f"{e['path']} is {spec['type']}; bad effect"))
    return issues


def _check_flow(nodes: dict[str, dict[str, Any]], entry: str, cid: str) -> list[Issue]:
    issues = []
    auto = {nid for nid, n in nodes.items() if n["type"] in cm.AUTOMATIC}
    depth: dict[str, int] = {}
    on_path: set[str] = set()

    def longest(nid: str) -> int:  # automatic steps from nid until a presenting/terminal node
        if nid not in auto:
            return 0
        if nid in depth:
            return depth[nid]
        if nid in on_path:
            raise RecursionError(nid)
        on_path.add(nid)
        value = 1 + max((longest(t) for t in cm.node_targets(nodes[nid]) if t in nodes), default=0)
        on_path.discard(nid)
        depth[nid] = value
        return value

    for nid in sorted(auto):
        try:
            if longest(nid) > cm.MAX_AUTO_STEPS:
                issues.append(finding("error", "auto_steps", f"{cid}:{nid}", f"More than {cm.MAX_AUTO_STEPS} automatic steps"))
                break
        except RecursionError:
            issues.append(finding("error", "auto_cycle", f"{cid}:{nid}", "A cycle of automatic transitions"))
            break
    reach = set(_reachable(nodes, entry))
    for nid in sorted(set(nodes) - reach):
        issues.append(finding("warning", "unreachable_node", f"{cid}:{nid}", "No path from the chapter entry reaches it"))
    terminal = {nid for nid, n in nodes.items() if n["type"] in ("chapterEnd", "ending")}
    back: dict[str, set[str]] = {}
    for nid, n in nodes.items():
        for t in cm.node_targets(n):
            back.setdefault(t, set()).add(nid)
    can_end, stack = set(terminal), list(terminal)
    while stack:
        for prev in back.get(stack.pop(), ()):
            if prev not in can_end:
                can_end.add(prev)
                stack.append(prev)
    issues += [finding("error", "no_way_out", f"{cid}:{nid}", "From here no chapter end or ending can be reached")
               for nid in sorted(reach - can_end)]  # fmt: skip
    return issues


def _check_presentation(presentation: dict[str, Any], registry: dict[str, Any], cid: str) -> list[Issue]:
    issues = []
    assets = registry["assets"]

    def need(lid: str | None, kind: str, where: str) -> None:
        if lid is None:
            return
        if lid not in assets:
            issues.append(finding("error", "unknown_asset", where, f"{lid} is not in the asset registry"))
        elif assets[lid]["type"] != kind:
            issues.append(finding("error", "asset_type", where, f"{lid} is not an {kind}"))

    for key, scene in presentation["scenes"].items():
        where = f"{cid}:scene {key}"
        need(scene["background"], "image", where)
        for c in scene["characters"]:
            need(c["asset"], "image", where)
        for o in scene["overlays"]:
            need(o["asset"], "image", where)
        audio = scene["audio"]
        for x in [audio["music"], audio["ambience"], *audio["loops"]]:
            if x:
                need(x["asset"], "audio", where)
    for cue_id, cue in presentation["cues"].items():
        if cue["scene"] not in presentation["scenes"]:
            issues.append(finding("error", "bad_scene", f"{cid}:cue {cue_id}", f"No scene {cue['scene']!r}"))
        for e in cue["events"]:
            need(e["asset"], "audio", f"{cid}:cue {cue_id}")
    return issues


# ---------------------------------------------------------------------- views


def releases_view(project: StoryProject, out_dir: str | Path | None = None) -> dict[str, Any]:
    root = releases_root(project, out_dir)
    index = load_index(root)
    try:
        receipts = json.loads((root / "publish.json").read_text())
    except (OSError, ValueError):
        receipts = {"receipts": []}
    return {"root": str(root), "releases": index.get("releases", []), "draft": index.get("draft"),
            "last_report": index.get("last_report"), "receipts": receipts.get("receipts", [])}  # fmt: skip
