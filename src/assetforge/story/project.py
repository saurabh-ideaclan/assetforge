"""A story lives in a plain folder (default ./story/<slug>/) so it can sit in a game repo and be diffed:

    story.json      config (title, premise, scenes, endings, style, provider…)
    world.json      setting, history, rules, art and music direction
    cast.json       characters (look, voice, expressions) and locations (times of day, ambience)
    outline.json    branching plan: scenes, choices, variables, endings
    scenes/<id>.json  one written scene each
    script.json     the assembled playable story
    check.json      the last continuity report
    notes.md        everything above rendered for people to read

Writes are atomic. Overwriting a file keeps the previous version as `<name>.back` (storycraftr's habit).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from assetforge.errors import InvalidInputError, NotFoundError
from assetforge.story.models import Cast, ContinuityReport, Outline, Scene, Script, StoryConfig, World

M = TypeVar("M", bound=BaseModel)
FILES: dict[str, tuple[str, type[BaseModel]]] = {
    "config": ("story.json", StoryConfig),
    "world": ("world.json", World),
    "cast": ("cast.json", Cast),
    "outline": ("outline.json", Outline),
    "script": ("script.json", Script),
    "check": ("check.json", ContinuityReport),
}


def write_atomic(path: Path, data: str, *, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists() and path.read_text() != data:
        path.with_name(path.name + ".back").write_text(path.read_text())
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class StoryProject:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    # ------------------------------------------------------------------ generic
    def path(self, part: str) -> Path:
        return self.root / FILES[part][0]

    def has(self, part: str) -> bool:
        return self.path(part).exists()

    def load(self, part: str, model: type[M] | None = None) -> M:
        name, cls = FILES[part]
        path = self.root / name
        if not path.exists():
            step = {"config": "init", "world": "world", "cast": "cast", "outline": "outline", "script": "write",
                    "check": "check"}[part]  # fmt: skip
            raise NotFoundError(f"{name} not found in {self.root}", hint=f"Run `assetforge story {step}` first")
        try:
            return (model or cls).model_validate_json(path.read_text())  # type: ignore[return-value]
        except ValueError as exc:
            raise InvalidInputError(f"{path} is not a valid {cls.__name__}: {exc}") from exc

    def save(self, part: str, value: BaseModel) -> Path:
        path = self.path(part)
        write_atomic(path, value.model_dump_json(indent=2, exclude_none=True) + "\n")
        return path

    # ------------------------------------------------------------------ typed shortcuts
    @property
    def config(self) -> StoryConfig:
        return self.load("config")

    def scene_path(self, scene_id: str) -> Path:
        return self.root / "scenes" / f"{scene_id}.json"

    def load_scene(self, scene_id: str) -> Scene | None:
        path = self.scene_path(scene_id)
        return Scene.model_validate_json(path.read_text()) if path.exists() else None

    def save_scene(self, scene: Scene) -> Path:
        path = self.scene_path(scene.id)
        write_atomic(path, scene.model_dump_json(indent=2, exclude_none=True) + "\n")
        return path

    def scenes(self) -> dict[str, Scene]:
        directory = self.root / "scenes"
        if not directory.exists():
            return {}
        found = {}
        for path in sorted(directory.glob("*.json")):
            scene = Scene.model_validate_json(path.read_text())
            found[scene.id] = scene
        return found

    def status(self) -> dict[str, object]:
        steps = {part: self.has(part) for part in ("config", "world", "cast", "outline", "script")}
        planned = written = 0
        if self.has("outline"):
            outline: Outline = self.load("outline")
            planned = len(outline.scenes)
            written = sum(1 for s in outline.scenes if self.scene_path(s.id).exists())
        return {"dir": str(self.root), "steps": steps, "scenes_planned": planned, "scenes_written": written}

    def read_json(self, name: str) -> dict[str, object]:
        path = self.root / name
        return json.loads(path.read_text()) if path.exists() else {}


# ---------------------------------------------------------------------- registry (so the app can list stories)


def _registry(home: Path) -> Path:
    return Path(home) / "stories.json"


def registered(home: Path) -> dict[str, Path]:
    """Story id → folder for every story created or opened with this library (missing folders dropped)."""
    try:
        data = json.loads(_registry(home).read_text())
    except (OSError, ValueError):
        return {}
    return {sid: Path(p) for sid, p in data.get("stories", {}).items() if (Path(p) / "story.json").exists()}


def register(home: Path, project: StoryProject) -> str:
    """Remember a story folder; returns its stable id (the slug, suffixed if another folder has it)."""
    try:
        data = json.loads(_registry(home).read_text())
    except (OSError, ValueError):
        data = {}
    stories: dict[str, str] = data.get("stories", {})
    for sid, path in stories.items():
        if Path(path) == project.root:
            return sid
    base = project.config.slug
    sid, n = base, 2
    while sid in stories and (Path(stories[sid]) / "story.json").exists():
        sid, n = f"{base}-{n}", n + 1
    stories[sid] = str(project.root)
    write_atomic(_registry(home), json.dumps({"stories": stories}, indent=2) + "\n", backup=False)
    return sid
