"""The Character Studio's data: a structured profile per character (identity, personality, narrative, visual),
per-section approval and locks, proposals versus canon, versions, the master visual reference, and the
consistency review of generated expressions.

`characters.json` holds the profiles. `cast.json` stays the pipeline's compact input: `sync_cast` rewrites it
from the profiles, so approved canon is what every later step reads. Nothing a model proposes becomes canon
until a person applies it; locked sections come back unchanged from AI revisions.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.core.util import utcnow
from assetforge.errors import ConflictError, InvalidInputError, NotFoundError
from assetforge.story.models import Cast, Character, Id, StoryConfig, World
from assetforge.story.project import StoryProject, write_atomic

if TYPE_CHECKING:
    from assetforge.core.catalog import Library
    from assetforge.story.steps import Ctx

SECTIONS = ("identity", "personality", "narrative", "visual")
ApprovalState = Literal["proposed", "approved", "changes_requested", "superseded"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(Strict):
    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    age: str = ""
    role: str = ""
    origin: str = ""
    social_position: str = ""
    occupation: str = Field("", description="Occupation or faction")


class Personality(Strict):
    traits: list[str] = Field(default_factory=list, description="Core traits")
    desires: str = Field("", description="Desires and motivations")
    fears: str = ""
    flaws: str = ""
    contradictions: str = Field("", description="Internal contradictions")
    moral_boundaries: str = ""
    under_pressure: str = Field("", description="Typical behaviour under pressure")
    dialogue_style: str = ""


class Relationship(Strict):
    character: str
    relation: str


class Narrative(Strict):
    arc: str = ""
    relationships: list[Relationship] = Field(default_factory=list)
    secrets: str = ""
    conflicts: str = ""
    milestones: list[str] = Field(default_factory=list)
    route_variations: str = ""


class Visual(Strict):
    appearance: str = Field("", description="Overall physical appearance in one or two sentences")
    face: str = ""
    eyes: str = ""
    hair: str = ""
    build: str = Field("", description="Body proportions")
    features: str = Field("", description="Distinguishing features")
    expressions: list[str] = Field(default_factory=list, description="Typical expressions the story needs")
    costume: str = ""
    accessories: str = ""
    palette: list[str] = Field(default_factory=list)
    silhouette: str = ""
    art_notes: str = Field("", description="Art direction notes for this character")


class Approval(Strict):
    state: ApprovalState = "proposed"
    note: str = ""
    at: str = ""


class Reference(Strict):
    asset_id: str
    at: str
    spec_hash: str = Field(description="Hash of the visual spec the image was approved for")


class Proposal(Strict):
    id: str
    request: str
    at: str
    provider: str = ""
    model: str = ""
    notes: str = ""
    sections: dict[str, Any] = Field(default_factory=dict, description="Proposed section values")
    changed: list[str] = Field(default_factory=list)


class CharacterProfile(Strict):
    id: Id
    identity: Identity = Field(default_factory=Identity)
    personality: Personality = Field(default_factory=Personality)
    narrative: Narrative = Field(default_factory=Narrative)
    visual: Visual = Field(default_factory=Visual)
    approvals: dict[str, Approval] = Field(default_factory=dict)
    locked: list[str] = Field(default_factory=list)
    version: int = 1
    versions: list[dict[str, Any]] = Field(default_factory=list)
    reference: Reference | None = None
    proposals: list[Proposal] = Field(default_factory=list)
    notes: str = ""

    def section(self, name: str) -> dict[str, Any]:
        if name not in SECTIONS:
            raise InvalidInputError(f"Unknown section {name!r}", sections=list(SECTIONS))
        return getattr(self, name).model_dump()

    def status(self, name: str) -> str:
        return self.approvals.get(name, Approval()).state

    def all_approved(self) -> bool:
        return all(self.status(s) == "approved" for s in SECTIONS)


# ---------------------------------------------------------------------- storage + derivation


def _path(project: StoryProject):  # noqa: ANN202
    return project.root / "characters.json"


def profiles(project: StoryProject) -> dict[str, CharacterProfile]:
    """All profiles; derived from `cast.json` the first time (and new cast members are added later)."""
    data = project.read_json("characters.json")
    found = {cid: CharacterProfile(**p) for cid, p in (data.get("characters") or {}).items()}
    if project.has("cast"):
        cast: Cast = project.load("cast")
        added = False
        for c in cast.characters:
            if c.id not in found:
                found[c.id] = from_character(c)
                added = True
        if added or not data:
            save_profiles(project, found)
    return found


def save_profiles(project: StoryProject, items: dict[str, CharacterProfile]) -> None:
    write_atomic(_path(project), json.dumps({"characters": {k: v.model_dump(mode="json") for k, v in items.items()}}, indent=2))


def get(project: StoryProject, cid: str) -> CharacterProfile:
    items = profiles(project)
    if cid not in items:
        raise NotFoundError(f"No character {cid!r}")
    return items[cid]


def from_character(c: Character) -> CharacterProfile:
    return CharacterProfile(
        id=c.id,
        identity=Identity(name=c.name, age=c.age, role=c.role),
        personality=Personality(traits=[t.strip() for t in c.personality.replace(";", ",").split(",") if t.strip()][:6],
                                desires=c.personality, dialogue_style=c.voice),
        visual=Visual(appearance=c.look, expressions=list(c.expressions), palette=list(c.palette)),
        approvals={s: Approval(state="proposed") for s in SECTIONS},
    )  # fmt: skip


def visual_spec(p: CharacterProfile) -> str:
    """The written visual identity the image prompts use (the canon the sprites must keep)."""
    v = p.visual
    parts = [v.appearance, v.build and f"Build: {v.build}", v.face and f"Face: {v.face}", v.eyes and f"Eyes: {v.eyes}",
             v.hair and f"Hair: {v.hair}", v.features and f"Distinguishing features: {v.features}",
             v.costume and f"Costume: {v.costume}", v.accessories and f"Accessories: {v.accessories}",
             v.palette and "Colours: " + ", ".join(v.palette), v.silhouette and f"Silhouette: {v.silhouette}",
             v.art_notes]  # fmt: skip
    return " ".join(x.rstrip(".") + "." for x in parts if x).strip()


def spec_hash(p: CharacterProfile) -> str:
    return hashlib.sha256(visual_spec(p).encode()).hexdigest()[:16]


def to_character(p: CharacterProfile, existing: Character | None = None) -> Character:
    """The compact pipeline record from a profile (approved canon is what the writer and the art see)."""
    base = existing.model_dump() if existing else {"id": p.id, "expressions": ["neutral"], "look": "", "personality": "", "name": p.id}
    bits = [", ".join(p.personality.traits), p.personality.desires, p.personality.contradictions and f"Contradiction: {p.personality.contradictions}",
            p.personality.flaws and f"Flaw: {p.personality.flaws}"]  # fmt: skip
    personality = " ".join(b.strip().rstrip(".") + "." for b in bits if b and b.strip()) or base.get("personality", "")
    return Character(**{**base, "name": p.identity.name or base.get("name", p.id), "role": p.identity.role or base.get("role", ""),
                        "age": p.identity.age or base.get("age", ""), "personality": personality or "—",
                        "look": visual_spec(p) or base.get("look", "—"), "voice": p.personality.dialogue_style or base.get("voice", ""),
                        "palette": p.visual.palette or base.get("palette", []),
                        "expressions": p.visual.expressions or base.get("expressions", ["neutral"])})  # fmt: skip


def sync_cast(project: StoryProject) -> list[str]:
    """Write `cast.json` from the profiles so every later step uses the studio's canon. Returns what changed."""
    if not project.has("cast"):
        return []
    cast: Cast = project.load("cast")
    items = profiles(project)
    changed: list[str] = []
    out = []
    for c in cast.characters:
        p = items.get(c.id)
        if p is None:
            out.append(c)
            continue
        new = to_character(p, c)
        if new != c:
            changed.append(c.id)
        out.append(new)
    if changed:
        project.save("cast", cast.model_copy(update={"characters": out}))
    return changed


def overview(lib: Library, project: StoryProject) -> list[dict[str, Any]]:
    """The cast dashboard rows."""
    from assetforge.request import AssetRequest, by_key
    from assetforge.story.assets import story_key

    items = profiles(project)
    rows = []
    slug = project.config.slug
    protagonist = next((p for p in items.values() if "protagonist" in p.identity.role.lower()), None)
    for cid, p in items.items():
        rel = next((r.relation for r in p.narrative.relationships if protagonist and r.character == protagonist.id), "")
        probe = AssetRequest(type="character", description="probe", project=project.config.asset_project, key=story_key(slug, "char", cid, "neutral"))  # type: ignore[arg-type]
        neutral = by_key(lib, probe)
        rows.append({"id": cid, "name": p.identity.name, "role": p.identity.role, "summary": ", ".join(p.personality.traits[:4]),
                     "motivation": p.personality.desires[:160], "conflict": p.personality.contradictions[:120],
                     "relationship": rel or ("protagonist" if protagonist and protagonist.id == cid else ""),
                     "development": "approved" if p.all_approved() else "draft ready for review",
                     "sections": {s: p.status(s) for s in SECTIONS}, "locked": p.locked, "version": p.version,
                     "visual_identity": "approved" if p.status("visual") == "approved" else "draft",
                     "reference": p.reference.model_dump() if p.reference else None, "neutral_asset": neutral,
                     "reference_stale": bool(p.reference and p.reference.spec_hash != spec_hash(p)),
                     "proposals": len(p.proposals)})  # fmt: skip
    return rows


def visual_summary(project: StoryProject) -> dict[str, int]:
    items = profiles(project)
    return {"total": len(items), "approved": sum(1 for p in items.values() if p.status("visual") == "approved")}


# ---------------------------------------------------------------------- edits, approvals, locks


def _changed_sections(old: CharacterProfile, new: CharacterProfile) -> list[str]:
    return [s for s in SECTIONS if getattr(old, s) != getattr(new, s)]


def update(project: StoryProject, cid: str, patch: dict[str, Any], *, note: str = "", unlock: bool = False) -> CharacterProfile:
    """Merge section values into the profile. Changed sections go back to `proposed` (approval is per version);
    locked sections refuse changes unless `unlock`."""
    items = profiles(project)
    old = items.get(cid)
    if old is None:
        raise NotFoundError(f"No character {cid!r}")
    data = old.model_dump()
    for section, values in patch.items():
        if section not in SECTIONS:
            raise InvalidInputError(f"Unknown section {section!r}", sections=list(SECTIONS))
        if section in old.locked and not unlock:
            raise ConflictError(f"{section} is locked for {old.identity.name or cid}", hint="Unlock it first, or pass unlock")
        if not isinstance(values, dict):
            raise InvalidInputError(f"{section} must be an object")
        data[section] = {**data[section], **values}
    new = CharacterProfile(**data)
    changed = _changed_sections(old, new)
    if not changed:
        return old
    for s in changed:
        new.approvals[s] = Approval(state="proposed", note="", at=utcnow())
    new.version = old.version + 1
    new.versions = [*old.versions, {"version": new.version, "at": utcnow(), "note": note or "edited", "changed": changed,
                                    "previous": {s: data_before for s, data_before in ((s, getattr(old, s).model_dump()) for s in changed)}}][-30:]  # fmt: skip
    items[cid] = new
    save_profiles(project, items)
    sync_cast(project)
    return new


def set_approval(project: StoryProject, cid: str, section: str, state: str, note: str = "") -> CharacterProfile:
    if section not in SECTIONS and section != "all":
        raise InvalidInputError(f"Unknown section {section!r}", sections=[*SECTIONS, "all"])
    if state not in ("proposed", "approved", "changes_requested"):
        raise InvalidInputError("state must be proposed, approved or changes_requested")
    items = profiles(project)
    p = items.get(cid)
    if p is None:
        raise NotFoundError(f"No character {cid!r}")
    for s in (SECTIONS if section == "all" else (section,)):
        p.approvals[s] = Approval(state=state, note=note, at=utcnow())  # type: ignore[arg-type]
        if state == "approved" and s not in p.locked:
            p.locked.append(s)  # approving locks: later generation never casually changes it
        if state != "approved" and s in p.locked:
            p.locked.remove(s)
    items[cid] = p
    save_profiles(project, items)
    sync_cast(project)
    return p


def set_lock(project: StoryProject, cid: str, section: str, locked: bool) -> CharacterProfile:
    items = profiles(project)
    p = items.get(cid)
    if p is None:
        raise NotFoundError(f"No character {cid!r}")
    if section not in SECTIONS:
        raise InvalidInputError(f"Unknown section {section!r}", sections=list(SECTIONS))
    if locked and section not in p.locked:
        p.locked.append(section)
    if not locked and section in p.locked:
        p.locked.remove(section)
    items[cid] = p
    save_profiles(project, items)
    return p


# ---------------------------------------------------------------------- AI revision (proposals)


def revise(ctx: Ctx, cid: str, request: str) -> Proposal:
    """Ask the writer for a revised profile. The answer is stored as a proposal; nothing changes until applied."""
    from assetforge.story import offline, prompts
    from assetforge.story.steps import _ask

    request = request.strip()
    if not request:
        raise InvalidInputError("Say what should change about the character")
    project = ctx.project
    items = profiles(project)
    p = items.get(cid)
    if p is None:
        raise NotFoundError(f"No character {cid!r}")
    world: World = project.load("world") if project.has("world") else World(logline="", setting="", history="", culture="",
                                                                              rules="", themes=[], art_direction="", music_direction="")  # fmt: skip
    current = {s: p.section(s) for s in SECTIONS}
    others = [{"id": o.id, "name": o.identity.name, "role": o.identity.role} for o in items.values() if o.id != cid]
    prompt = prompts.character_prompt(ctx.config, world, current, request, list(p.locked), others)
    n = len(p.proposals) + 1
    answer: ProfileAnswer = _ask(ctx, f"character.{cid}.r{n}", prompt, ProfileAnswer, offline.character(current, request), fresh=True)
    sections = {s: getattr(answer, s).model_dump() for s in SECTIONS}
    for s in p.locked:  # a locked section never comes back changed, whatever the model did
        sections[s] = current[s]
    changed = [s for s in SECTIONS if sections[s] != current[s]]
    last = ctx.calls[-1] if ctx.calls else {}
    proposal = Proposal(id=f"p{n}", request=request, at=utcnow(), provider=str(last.get("provider", "")),
                        model=str(last.get("model", "")), notes=answer.notes, sections=sections, changed=changed)  # fmt: skip
    p.proposals.append(proposal)
    items[cid] = p
    save_profiles(project, items)
    return proposal


class ProfileAnswer(Strict):
    identity: Identity
    personality: Personality
    narrative: Narrative
    visual: Visual
    notes: str = Field("", description="What changed and why, and what it affects")


def apply_proposal(project: StoryProject, cid: str, pid: str, *, sections: list[str] | None = None) -> CharacterProfile:
    p = get(project, cid)
    prop = next((x for x in p.proposals if x.id == pid), None)
    if prop is None:
        raise NotFoundError(f"No proposal {pid!r} for {cid}")
    wanted = [s for s in (sections or prop.changed) if s in prop.changed]
    patch = {s: prop.sections[s] for s in wanted}
    new = update(project, cid, patch, note=f"applied proposal {pid}: {prop.request[:80]}", unlock=False) if patch else p
    items = profiles(project)
    items[cid].proposals = [x for x in items[cid].proposals if x.id != pid]
    save_profiles(project, items)
    return items[cid]


def discard_proposal(project: StoryProject, cid: str, pid: str) -> CharacterProfile:
    items = profiles(project)
    p = items.get(cid)
    if p is None:
        raise NotFoundError(f"No character {cid!r}")
    p.proposals = [x for x in p.proposals if x.id != pid]
    items[cid] = p
    save_profiles(project, items)
    return p


# ---------------------------------------------------------------------- visual reference pipeline


def _neutral_request(lib: Library, project: StoryProject, p: CharacterProfile, *, n: int, image_provider: str,
                     quality: str, keyed: bool) -> Any:  # fmt: skip
    from assetforge.core.models import AssetType
    from assetforge.request import AssetRequest
    from assetforge.story.assets import story_key

    config: StoryConfig = project.config
    slug = config.slug
    name = p.identity.name or p.id
    return AssetRequest(type=AssetType.CHARACTER, project=config.asset_project, provider=image_provider, quality=quality,
                        key=story_key(slug, "char", p.id, "neutral") if keyed else None, n=n,
                        description=f"{name}, visual novel character sprite. {visual_spec(p)} Full body, standing, facing the "
                                    "viewer, arms relaxed, calm neutral expression, centred, nothing else in frame.",
                        aspect="2:3", transparent=True, name=f"{slug}_{p.id}_concept", tags=["visual-novel", slug, "concept"],
                        process="web-sprite", collection=f"vn-{slug}", reuse_policy="always_generate", genre=config.genre,
                        purpose="visual novel character concept")  # type: ignore[arg-type]  # fmt: skip


def concepts(lib: Library, project: StoryProject, cid: str, *, n: int = 3, image_provider: str = "auto",
             quality: str = "auto", dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    """Generate `n` concept candidates for the character's approved written visual identity. They are candidates
    (unkeyed) until one is chosen as the master reference."""
    from assetforge.request import handle

    p = get(project, cid)
    if not visual_spec(p):
        raise InvalidInputError("Write the visual identity first (appearance, hair, eyes, costume…)")
    req = _neutral_request(lib, project, p, n=max(1, min(6, n)), image_provider=image_provider, quality=quality, keyed=False)
    if dry_run:
        req = req.model_copy(update={"dry_run": True})
    result = handle(lib, req)
    data = result.model_dump(mode="json")
    data["candidates"] = data.get("candidates") or ([{"asset_id": result.asset_id}] if result.asset_id else [])
    data["spec"] = visual_spec(p)
    return data


def set_master(lib: Library, project: StoryProject, cid: str, asset_id: str) -> CharacterProfile:
    """Approve one candidate as the canonical reference: it gets the neutral key (the old holder loses it), its
    siblings are rejected, and the profile records which visual spec it stands for."""
    from assetforge.core import review
    from assetforge.core.models import AssetPatch
    from assetforge.request import by_key
    from assetforge.story.assets import story_key

    items = profiles(project)
    p = items.get(cid)
    if p is None:
        raise NotFoundError(f"No character {cid!r}")
    key = story_key(project.config.slug, "char", cid, "neutral")
    asset_id = lib.resolve_asset_id(asset_id)
    probe = _neutral_request(lib, project, p, n=1, image_provider="auto", quality="auto", keyed=True)
    old = by_key(lib, probe)
    if old and old != asset_id:
        lib.update_asset(old, AssetPatch(key=""))
    review.pick(lib, asset_id, note=f"master reference for {p.identity.name or cid}", force=True)
    lib.update_asset(asset_id, AssetPatch(key=key, name=f"{project.config.slug}_{cid}_neutral"))
    p.reference = Reference(asset_id=asset_id, at=utcnow(), spec_hash=spec_hash(p))
    if p.status("visual") != "approved":
        p.approvals["visual"] = Approval(state="approved", note="approved with the master reference", at=utcnow())
        if "visual" not in p.locked:
            p.locked.append("visual")
    items[cid] = p
    save_profiles(project, items)
    sync_cast(project)
    return p


def consistency(lib: Library, project: StoryProject, cid: str) -> dict[str, Any]:
    """Compare every generated expression with the master reference (dHash shape distance and palette overlap).
    A heuristic: it catches a different person, outfit or palette, not a subtly wrong face. Flagged assets need
    a human look."""
    from assetforge.core import visual
    from assetforge.request import AssetRequest, by_key
    from assetforge.story.assets import story_key

    p = get(project, cid)
    if p.reference is None:
        return {"character": cid, "reference": None, "items": [], "note": "No master reference yet"}
    master = p.reference.asset_id
    base = visual._features_row(lib, master)
    if base is None or not base["dhash"]:
        return {"character": cid, "reference": master, "items": [], "note": "The reference image has no features yet"}
    visual.ensure_features(lib, base["sha256"])
    base = visual._features_row(lib, master)
    base_colors = visual.color_shares(json.loads(base["colors"] or "[]"))
    items = []
    slug = project.config.slug
    for expr in p.visual.expressions:
        if expr == "neutral":
            continue
        probe = AssetRequest(type="character", description="probe", project=project.config.asset_project, key=story_key(slug, "char", cid, expr))  # type: ignore[arg-type]
        aid = by_key(lib, probe)
        if not aid:
            continue
        row = visual._features_row(lib, aid)
        if row is None or not row["dhash"]:
            items.append({"expression": expr, "asset_id": aid, "verdict": "unknown"})
            continue
        distance = visual.hamming(base["dhash"], row["dhash"])
        palette = round(visual.palette_overlap(base_colors, visual.color_shares(json.loads(row["colors"] or "[]"))), 3)
        verdict = "consistent" if distance <= 16 and palette >= 0.45 else "review"
        items.append({"expression": expr, "asset_id": aid, "distance": distance, "palette": palette, "verdict": verdict,
                      "made_for_current_spec": True})  # fmt: skip
    stale = p.reference.spec_hash != spec_hash(p)
    return {"character": cid, "reference": master, "items": items, "reference_stale": stale,
            "note": "Shape distance ≤ 16 and palette overlap ≥ 0.45 count as consistent; this cannot judge a face. "
                    + ("The visual spec changed after the reference was approved." if stale else "")}  # fmt: skip
