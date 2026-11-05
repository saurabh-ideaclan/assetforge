"""Story endpoints for the desktop app: list/create stories, pick the writer (any signed-in subscription and any
model it offers), run the long steps in the background, improve from feedback, and play the exported demo.

Long work (writing, improving, assets, export) runs as an in-process task: POST returns `task_id`, the UI polls
`GET /api/story-tasks/{id}`. One task per story at a time. Every model call is still a cached `text` job, so a
task that dies part-way loses nothing (re-running continues).
"""

from __future__ import annotations

import mimetypes
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from assetforge.app.server import FileResponse, Request, Router
from assetforge.config import load_settings
from assetforge.core.catalog import Library
from assetforge.core.util import slugify
from assetforge.errors import AssetForgeError, ConflictError, InternalError, InvalidInputError, NotFoundError
from assetforge.story import steps
from assetforge.story.models import StoryConfig
from assetforge.story.project import StoryProject, register, registered

ACTIONS = ("write", "improve", "check", "assets_plan", "assets", "export", "refine")
_tasks: dict[str, dict[str, Any]] = {}
_busy: dict[str, str] = {}  # story id → running task id
_lock = threading.Lock()
_providers_cache: dict[str, Any] = {}


def register_routes(router: Router) -> None:  # noqa: C901 - one place for all story routes
    @router.route("GET", "/api/llm/providers")
    def providers(req: Request, lib: Library) -> dict[str, Any]:
        """Signed-in subscriptions and their models (login checks spawn the CLIs: cached for a minute)."""
        from assetforge.llm.catalog import describe

        cached = _providers_cache.get("value")
        if cached and time.time() - _providers_cache["at"] < 60 and req.arg("refresh") != "1":
            return cached
        value = describe(lib, load_settings(lib.paths.home))
        _providers_cache.update(value=value, at=time.time())
        return value

    @router.route("POST", "/api/llm/login")
    def login_start(req: Request, lib: Library) -> dict[str, Any]:
        """Start the provider's own sign-in; the UI shows the link and sends back the code the user pastes."""
        from assetforge.llm import login

        session = login.start(str((req.body or {}).get("provider", "")), load_settings(lib.paths.home))
        _providers_cache.clear()
        return {"login": session.public()}

    @router.route("GET", r"/api/llm/login/(?P<lid>[0-9a-f]+)")
    def login_status(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.llm import login

        session = login.get(req.match["lid"])
        if session.state == "done":
            _providers_cache.clear()
        return {"login": session.public()}

    @router.route("POST", r"/api/llm/login/(?P<lid>[0-9a-f]+)/code")
    def login_code(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.llm import login

        return {"login": login.submit(req.match["lid"], str((req.body or {}).get("code", ""))).public()}

    @router.route("POST", r"/api/llm/login/(?P<lid>[0-9a-f]+)/open")
    def login_open(req: Request, lib: Library) -> dict[str, Any]:
        """Open this sign-in's own link in the default browser (the app window can't open new windows)."""
        import subprocess
        import sys

        from assetforge.llm import login

        session = login.get(req.match["lid"])
        if not session.url:
            raise InvalidInputError("The sign-in link isn't ready yet")
        if sys.platform == "darwin":
            subprocess.run(["open", session.url], check=False, timeout=10)
        return {"opened": True}

    @router.route("POST", r"/api/llm/login/(?P<lid>[0-9a-f]+)/cancel")
    def login_cancel(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.llm import login

        return {"login": login.cancel(req.match["lid"]).public()}

    @router.route("GET", "/api/stories")
    def list_stories(req: Request, lib: Library) -> dict[str, Any]:
        items = []
        for sid, root in sorted(registered(lib.paths.home).items()):
            project = StoryProject(root)
            try:
                config = project.config
            except AssetForgeError:
                continue
            items.append({"id": sid, "title": config.title, "premise": config.premise, "genre": config.genre,
                          "dir": str(root), "provider": config.provider, "model": config.model,
                          "status": project.status(), "busy": sid in _busy,
                          "playable": (root / "build" / "index.html").exists(),
                          "updated": max((p.stat().st_mtime for p in root.glob("*.json")), default=0)})  # fmt: skip
        items.sort(key=lambda s: -s["updated"])
        return {"stories": items, "default_dir": str(lib.paths.home / "stories")}

    @router.route("POST", "/api/stories")
    def create_story(req: Request, lib: Library) -> dict[str, Any]:
        body = dict(req.body or {})
        title = str(body.pop("title", "")).strip()
        premise = str(body.pop("premise", "")).strip()
        if not title or len(premise) < 3:
            raise InvalidInputError("A story needs a title and a premise")
        directory = body.pop("dir", None) or lib.paths.home / "stories" / slugify(title)
        allowed = set(StoryConfig.model_fields) - {"title", "slug", "premise"}
        options = {k: v for k, v in body.items() if k in allowed and v not in ("", None)}
        project = steps.init(directory, title=title, premise=premise, **options)
        return {"id": register(lib.paths.home, project), "dir": str(project.root)}

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)")
    def story_detail(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        data: dict[str, Any] = {"id": sid, "dir": str(project.root), "config": project.config.model_dump(mode="json"),
                                "status": project.status(), "task": _task_for(sid)}  # fmt: skip
        for part in ("world", "cast", "outline", "check"):
            if project.has(part):
                data[part] = project.load(part).model_dump(mode="json", exclude_none=True)
        if project.has("script"):
            from assetforge.story.assets import coverage

            script = project.load("script")
            data["scenes"] = [s.model_dump(mode="json", exclude_none=True) for s in script.scenes]
            data["issues"] = [i.model_dump() for i in steps.validate(project)]
            data["assets"] = coverage(lib, project)
        data["feedback"] = project.read_json("feedback.json").get("history", [])[-10:]
        data["playable"] = (project.root / "build" / "index.html").exists()
        return data

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/settings")
    def story_settings(req: Request, lib: Library) -> dict[str, Any]:
        """Change the writer (provider/model) or other config fields; the next steps use them."""
        sid, project = _story(lib, req.match["sid"])
        config = project.config
        changes = {k: (v or None) if k in ("provider", "model", "art_style", "project") else v
                   for k, v in (req.body or {}).items() if k in StoryConfig.model_fields and k not in ("slug",)}  # fmt: skip
        updated = StoryConfig(**{**config.model_dump(), **changes})
        project.save("config", updated)
        return {"config": updated.model_dump(mode="json")}

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/run")
    def run_story(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        action = body.get("action")
        if action not in ACTIONS:
            raise InvalidInputError(f"action must be one of {ACTIONS}")
        if action == "improve" and not str(body.get("feedback", "")).strip() and not body.get("use_check"):
            raise InvalidInputError("Write some feedback first")
        with _lock:
            if sid in _busy:
                raise ConflictError("This story is already working on something", task_id=_busy[sid])
            task_id = uuid.uuid4().hex[:12]
            _tasks[task_id] = {"id": task_id, "story": sid, "action": action, "state": "running",
                               "started": time.time(), "result": None, "error": None}  # fmt: skip
            _busy[sid] = task_id
        threading.Thread(target=_work, args=(req.server.home, sid, project.root, task_id, body), daemon=True,
                         name=f"story-{action}").start()  # fmt: skip
        return {"task_id": task_id}

    @router.route("GET", r"/api/story-tasks/(?P<task>[0-9a-f]+)")
    def task_detail(req: Request, lib: Library) -> dict[str, Any]:
        task = _tasks.get(req.match["task"])
        if task is None:
            raise NotFoundError("No such task (the app may have restarted)")
        sid = task["story"]
        root = registered(lib.paths.home).get(sid)
        return {"task": {**task, "elapsed": round(time.time() - task["started"])},
                "status": StoryProject(root).status() if root else None}  # fmt: skip

    @router.route("GET", r"/play/(?P<sid>[a-z0-9-]+)/(?P<path>.*)")
    def play(req: Request, lib: Library) -> FileResponse:
        """The exported demo, served from the story's build folder (only files inside it)."""
        _, project = _story(lib, req.match["sid"])
        build = (project.root / "build").resolve()
        target = (build / (req.match["path"] or "index.html")).resolve()
        if build not in target.parents or not target.is_file():
            raise NotFoundError("Not in the exported demo; export it first")
        mime = mimetypes.guess_type(target.name)[0]
        if target.suffix == ".js":
            mime = "text/javascript"
        return FileResponse(target, mime, "no-cache")


def _story(lib: Library, sid: str) -> tuple[str, StoryProject]:
    root = registered(lib.paths.home).get(sid)
    if root is None:
        raise NotFoundError(f"No story {sid!r}")
    return sid, StoryProject(root)


def _task_for(sid: str) -> dict[str, Any] | None:
    task_id = _busy.get(sid)
    if task_id:
        return _tasks[task_id]
    done = [t for t in _tasks.values() if t["story"] == sid]
    return max(done, key=lambda t: t["started"]) if done else None


def _work(home: Path, sid: str, root: Path, task_id: str, body: dict[str, Any]) -> None:
    task = _tasks[task_id]
    try:
        with Library(home) as lib:
            project = StoryProject(root)
            ctx = steps.Ctx(lib=lib, project=project, provider=body.get("provider") or None,
                            model=body.get("model") or None, fresh=bool(body.get("fresh")))  # fmt: skip
            task["result"] = _run_action(lib, project, ctx, body)
            task["calls"] = ctx.calls
            task["state"] = "done"
    except AssetForgeError as exc:
        task.update(state="failed", error=exc.to_dict())
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        task.update(state="failed", error=InternalError(f"{type(exc).__name__}: {exc}").to_dict())
    finally:
        task["finished"] = time.time()
        with _lock:
            _busy.pop(sid, None)


def _run_action(lib: Library, project: StoryProject, ctx: steps.Ctx, body: dict[str, Any]) -> dict[str, Any]:
    from assetforge.exporters.vn import export_story
    from assetforge.story.assets import produce

    action = body["action"]
    if action == "write":  # everything not done yet: world → cast → outline → scenes, then a playable export
        if not project.has("world"):
            steps.world(ctx)
        if not project.has("cast"):
            steps.cast(ctx)
        if not project.has("outline"):
            steps.outline(ctx)
        result = steps.write(ctx, workers=int(body.get("workers") or 2))
        if result.get("assembled"):
            result["export"] = export_story(lib, project, project.root / "build")
        return result
    if action == "improve":
        result = steps.improve(ctx, str(body.get("feedback", "")), include_check=bool(body.get("use_check")),
                               scenes=body.get("scenes") or None)  # fmt: skip
        result["export"] = export_story(lib, project, project.root / "build")
        return result
    if action == "refine":
        result = steps.refine(ctx, str(body["scene"]), str(body.get("note", "")))
        result["export"] = export_story(lib, project, project.root / "build")
        return result
    if action == "check":
        return steps.check(ctx).model_dump(mode="json")
    if action in ("assets_plan", "assets"):
        result = produce(lib, project, only=body.get("only") or None, limit=body.get("limit"),
                         dry_run=action == "assets_plan", image_provider=body.get("image_provider") or "auto",
                         quality=body.get("quality") or "auto")  # fmt: skip
        if action == "assets":
            result["export"] = export_story(lib, project, project.root / "build")
        return result
    return export_story(lib, project, project.root / "build")
