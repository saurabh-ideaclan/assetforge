"""Export a story as a playable demo: the story's assets (via the generic exporter), `story.json` (the script
plus a key → file map), `story.js` (the same data as a script, so `index.html` also works from file://) and a
no-build `index.html` player. Assets that don't exist yet are listed in `missing` and drawn as placeholders,
so a story is playable as soon as it is written.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assetforge.errors import ConflictError
from assetforge.exporters.generic import ExportRequest, export
from assetforge.request import by_key
from assetforge.story.assets import manifest
from assetforge.story.project import StoryProject, write_atomic

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

PLAYER_MARK = "assetforge-vn-player"
OWN_FILES = ("index.html", "story.js", "story.json")


def player_html() -> str:
    return resources.files("assetforge.exporters").joinpath("vn_player/index.html").read_text()


def export_story(lib: Library, project: StoryProject, out: str | Path, *, include_candidates: bool = True,
                 overwrite: bool = False, dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    root = Path(out).expanduser().resolve()
    config = project.config
    script = project.load("script")
    world = project.load("world") if project.has("world") else None
    _check_ownership(root, overwrite)

    items = manifest(script, config, world)
    by_asset: dict[str, str] = {}
    missing: list[str] = []
    for item in items:
        asset_id = by_key(lib, item.request)
        if asset_id:
            by_asset.setdefault(asset_id, item.key)
        else:
            missing.append(item.key)

    files: dict[str, dict[str, Any]] = {}
    exported: dict[str, Any] | None = None
    skipped: list[dict[str, str]] = []
    if by_asset:
        name = f"vn-{config.slug}"
        result = export(lib, ExportRequest(out=str(root), refs=list(by_asset), project=config.asset_project,
                                           include_candidates=include_candidates, image_formats=["webp"],
                                           audio_formats=["ogg", "m4a"], audio_extras=False, pixi=False,
                                           bundle=name, overwrite=overwrite, dry_run=dry_run))  # fmt: skip
        skipped = result.skipped
        for f in result.files:
            key = by_asset.get(f.asset_id)
            if key is None:
                continue
            entry = files.setdefault(key, {})
            if f.kind == "texture":
                entry.setdefault("image", f.path)
            elif f.kind == "sound":
                entry.setdefault("audio", []).append(f.path)
        exported = {"files": len(result.files), "counts": result.counts, "warnings": result.warnings}
        missing += [by_asset[s["asset_id"]] for s in skipped if s.get("asset_id") in by_asset]

    data = {"format": "assetforge-vn-export/1", "logline": world.logline if world else "",
            "script": script.model_dump(mode="json", exclude_none=True), "assets": files, "missing": sorted(set(missing))}  # fmt: skip
    if not dry_run:
        text = json.dumps(data, ensure_ascii=False, indent=1)
        write_atomic(root / "story.json", text + "\n", backup=False)
        write_atomic(root / "story.js", f"window.STORY = {text};\n", backup=False)
        write_atomic(root / "index.html", player_html(), backup=False)
    return {"out": str(root), "player": str(root / "index.html"), "dry_run": dry_run, "assets": len(files),
            "missing": sorted(set(missing)), "skipped": skipped, "export": exported,
            "serve": f"cd {root} && python3 -m http.server 8000  # then open http://localhost:8000"}  # fmt: skip


def _check_ownership(root: Path, overwrite: bool) -> None:
    """Never replace someone else's index.html / story.js / story.json."""
    for name in OWN_FILES:
        path = root / name
        if not path.exists() or overwrite:
            continue
        head = path.read_text(errors="replace")[:4000]
        ours = PLAYER_MARK in head if name == "index.html" else ('"assetforge-vn-export/1"' in head or "assetforge-vn-export/1" in head)
        if not ours:
            raise ConflictError(f"{path} exists and was not written by AssetForge", hint="Pick another --out or pass --overwrite")
