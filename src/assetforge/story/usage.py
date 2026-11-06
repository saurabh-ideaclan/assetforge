"""Which assets a story uses, and where: every character expression, location × time of day, CG, UI piece, music
mood, ambience and sound cue, with the library asset behind it (if made) and the chapters that use it. Works while a
story is still being written (written chapters plus the plan). The reverse lookup lists the stories an asset is in.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from assetforge.request import by_key
from assetforge.story.models import Script
from assetforge.story.project import StoryProject, registered

if TYPE_CHECKING:
    from assetforge.core.catalog import Library


def current_script(project: StoryProject) -> Script | None:
    """script.json when complete, else the written chapters plus planned stand-ins."""
    if project.has("script"):
        return project.load("script")
    from assetforge.story.progress import preview_script

    data = preview_script(project)
    if data is None:
        return None
    for scene in data["scenes"]:
        scene.pop("draft", None)
        scene.pop("summary", None)
    data.pop("format", None)
    return Script(**data)


def _chapters(script: Script) -> dict[str, dict[str, list[str]]]:
    """key fragment → chapter ids, per asset group."""
    out: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    def add(group: str, ref: str, sid: str) -> None:
        if sid not in out[group][ref]:
            out[group][ref].append(sid)

    for scene in script.scenes:
        add("bg", f"{scene.location}/{scene.time}", scene.id)
        add("amb", scene.location, scene.id)
        add("music", scene.music, scene.id)
        for node in scene.nodes:
            who = node.speaker if node.kind == "say" else node.character if node.kind == "show" else None
            if who:
                add("char", f"{who}/{node.expression or 'neutral'}", scene.id)
                add("char", f"{who}/neutral", scene.id) if node.kind == "show" and not node.expression else None
            if node.kind == "cg" and node.cg:
                add("cg", node.cg, scene.id)
            if node.kind == "sfx" and node.cue:
                add("sfx", node.cue, scene.id)
    return out


def story_assets(lib: Library, project: StoryProject) -> dict[str, Any] | None:
    from assetforge.story.assets import manifest

    script = current_script(project)
    if script is None:
        return None
    config = project.config
    world = project.load("world") if project.has("world") else None
    sound_plan = project.load("sound") if project.has("sound") else None
    items = manifest(script, config, world, sound_plan=sound_plan)
    found = {i.key: by_key(lib, i.request) for i in items}
    summaries = {s.id: s.model_dump(mode="json") for s in lib.summaries([a for a in found.values() if a])}
    uses = _chapters(script)
    titles = {s.id: s.title for s in script.scenes}
    prefix = f"vn/{config.slug}/"

    def entry(item: Any, group: str, ref: str) -> dict[str, Any]:
        asset_id = found.get(item.key)
        chapters = uses.get(group, {}).get(ref, [])
        return {"key": item.key, "label": item.label, "group": item.group, "asset": summaries.get(asset_id) if asset_id else None,
                "chapters": [{"id": c, "title": titles.get(c, c)} for c in chapters], "ref": ref}  # fmt: skip

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        rest = item.key.removeprefix(prefix)
        kind, _, ref = rest.partition("/")
        group = {"char": "char", "bg": "bg", "cg": "cg", "ui": "ui", "music": "music", "amb": "amb", "sfx": "sfx"}[kind]
        by_group[group].append(entry(item, group, ref))

    characters = []
    for c in script.characters:
        exprs = [e for e in by_group["char"] if e["ref"].split("/")[0] == c.id]
        characters.append({"id": c.id, "name": c.name, "role": c.role, "look": c.look,
                           "expressions": [{**e, "expression": e["ref"].split("/")[1]} for e in exprs],
                           "lines": sum(1 for s in script.scenes for n in s.nodes if n.kind == "say" and n.speaker == c.id)})  # fmt: skip
    locations = []
    for loc in script.locations:
        times = [{**e, "time": e["ref"].split("/")[1]} for e in by_group["bg"] if e["ref"].split("/")[0] == loc.id]
        amb = next((e for e in by_group["amb"] if e["ref"] == loc.id), None)
        if times or amb:
            locations.append({"id": loc.id, "name": loc.name, "description": loc.description, "times": times, "ambience": amb})
    cg_text = {n.cg: n.text for s in script.scenes for n in s.nodes if n.kind == "cg" and n.cg}
    sfx_text = {n.cue: n.text for s in script.scenes for n in s.nodes if n.kind == "sfx" and n.cue}
    for e in by_group["cg"]:
        e["text"] = cg_text.get(e["ref"], "")
    for e in by_group["sfx"]:
        e["text"] = sfx_text.get(e["ref"], "")
    every = [e for g in by_group.values() for e in g]
    return {
        "characters": characters, "locations": locations, "cgs": by_group["cg"], "ui": by_group["ui"],
        "music": by_group["music"], "sfx": by_group["sfx"],
        "totals": {"total": len(every), "made": sum(1 for e in every if e["asset"]),
                   "approved": sum(1 for e in every if e["asset"] and e["asset"]["status"] == "approved")},
    }  # fmt: skip


def stories_using(lib: Library, asset_id: str) -> list[dict[str, Any]]:
    """The stories (and keys) an asset is used under, for the asset detail panel."""
    rows = lib.conn.execute(
        "SELECT DISTINCT project, usage_key FROM project_usage WHERE asset_id = ? AND usage_key LIKE 'vn/%' "
        "UNION SELECT project, key FROM assets WHERE id = ? AND key LIKE 'vn/%'", (asset_id, asset_id)).fetchall()  # fmt: skip
    if not rows:
        return []
    stories = {}
    for sid, root in registered(lib.paths.home).items():
        try:
            config = StoryProject(root).config
        except Exception:  # noqa: BLE001 - a broken story folder shouldn't break the asset panel
            continue
        stories[config.asset_project] = (sid, config.title)
    out = []
    for r in rows:
        if r[0] in stories:
            sid, title = stories[r[0]]
            out.append({"story": sid, "title": title, "key": r[1], "role": r[1].split("/")[2] if r[1].count("/") >= 2 else ""})
    return out
