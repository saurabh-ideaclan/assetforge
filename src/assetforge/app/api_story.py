"""Story endpoints for the desktop app: list/create stories, pick the writer (any signed-in subscription and any
model it offers), run the long steps in the background, improve from feedback, and play the exported demo.

Long work (writing, improving, assets, export) runs as an in-process task: POST returns `task_id`, the UI polls
`GET /api/story-tasks/{id}`. One task per story at a time. Every model call is still a cached `text` job, so a
task that dies part-way loses nothing (re-running continues).
"""

from __future__ import annotations

import json
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
from assetforge.llm import plans
from assetforge.story import audio_direction, characters, workflow
from assetforge.story import direction as creative
from assetforge.story import steps
from assetforge.story.models import StoryConfig
from assetforge.story.project import StoryProject, register, registered

ACTIONS = ("write", "improve", "check", "assets_plan", "assets", "export", "refine", "character", "alternatives", "concepts",
           "audio_plan", "audio_make", "dynamics_plan", "dynamics_estimate", "evaluate", "acting_profile", "acting_analyze",
           "acting_generate", "payoffs_draft", "rules_draft", "contract", "publish", "visual_canon", "art_check",
           "art_fix")  # fmt: skip
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
        """Every story the library knows (app, CLI or agent), with live progress and a cover."""
        from assetforge.story.progress import library_assets, progress

        items = []
        for sid, root in sorted(registered(lib.paths.home).items()):
            project = StoryProject(root)
            try:
                config = project.config
                prog = progress(lib, project)
                cover = (library_assets(lib, project).get(f"vn/{config.slug}/ui/title") or {}).get("image")
                world = project.read_json("world.json")
            except (AssetForgeError, ValueError, OSError):
                continue
            items.append({"id": sid, "title": config.title, "premise": config.premise, "genre": config.genre,
                          "logline": world.get("logline") or config.premise, "dir": str(root),
                          "provider": config.provider, "model": config.model, "status": project.status(),
                          "progress": {k: prog[k] for k in ("stage", "steps", "active", "running", "eta_seconds",
                                                            "playable_chapters")},
                          "busy": sid in _busy or prog["active"], "cover": cover,
                          "playable": prog["playable_chapters"] > 0,
                          "updated": max((p.stat().st_mtime for p in [*root.glob("*.json"), *root.glob("scenes/*.json")]),
                                         default=0)})  # fmt: skip
        items.sort(key=lambda s: (not s["busy"], -s["updated"]))
        return {"stories": items, "default_dir": str(lib.paths.home / "stories")}

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/assets")
    def story_assets_route(req: Request, lib: Library) -> dict[str, Any]:
        """Everything the story uses (characters × expressions, places × times, CGs, UI, sound) and where."""
        from assetforge.story.usage import story_assets

        _, project = _story(lib, req.match["sid"])
        data = story_assets(lib, project)
        if data is None:
            raise NotFoundError("Nothing planned yet: write the story first")
        return data

    @router.route("GET", r"/api/assets/(?P<ref>[^/]+)/stories")
    def asset_stories(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.story.usage import stories_using

        return {"stories": stories_using(lib, lib.resolve_asset_id(req.match["ref"]))}

    @router.route("GET", "/api/activity")
    def activity(req: Request, lib: Library) -> dict[str, Any]:
        """What is being written right now, across every story (for the sidebar)."""
        from assetforge.core.util import loads
        from assetforge.story.progress import STALE_SECONDS, _ts

        projects: dict[str, tuple[str, str]] = {}
        for sid, root in registered(lib.paths.home).items():
            try:
                config = StoryProject(root).config
            except AssetForgeError:
                continue
            projects[config.asset_project] = (sid, config.title)
        now = time.time()
        busy: dict[str, dict[str, Any]] = {}
        for r in lib.conn.execute("SELECT request, created_at FROM generation_jobs WHERE kind = 'text' AND status = 'running'"):
            req_data = loads(r["request"], {}) or {}
            if now - _ts(r["created_at"]) > STALE_SECONDS or req_data.get("project") not in projects:
                continue
            sid, title = projects[req_data["project"]]
            busy.setdefault(sid, {"id": sid, "title": title, "running": 0})["running"] += 1
        for sid, task_id in list(_busy.items()):
            entry = busy.setdefault(sid, {"id": sid, "title": sid, "running": 0})
            entry["task"] = _tasks.get(task_id, {}).get("action")
        return {"stories": list(busy.values()), "running": sum(b["running"] for b in busy.values()) or len(busy)}

    @router.route("GET", r"/preview/(?P<sid>[a-z0-9-]+)/(?P<path>.*)")
    def preview(req: Request, lib: Library) -> Any:
        """The player on the story exactly as it is now: written chapters, stand-ins for the rest, library art."""
        from assetforge.app.server import PLAYER_CSP, RawResponse
        from assetforge.exporters.vn import player_html
        from assetforge.story.progress import preview_data

        _, project = _story(lib, req.match["sid"])
        path = req.match["path"] or "index.html"
        if path == "index.html":
            return RawResponse(player_html().encode(), "text/html; charset=utf-8", headers={"Content-Security-Policy": PLAYER_CSP})
        if path == "story.js":
            data = preview_data(lib, project)
            if data is None:
                raise NotFoundError("Nothing to play yet: the chapters haven't been planned")
            return RawResponse(f"window.STORY = {json.dumps(data, ensure_ascii=False)};\n".encode(),
                               "text/javascript; charset=utf-8")  # fmt: skip
        raise NotFoundError("Not part of the preview")

    @router.route("GET", "/api/story/direction")
    def direction_options(req: Request, lib: Library) -> dict[str, Any]:
        """Everything the creative-direction form needs: option lists, help, the taxonomy and the presets."""
        return {"options": creative.options(),
                "presets": [p.model_dump(mode="json") for p in creative.presets(lib.paths.home)]}  # fmt: skip

    @router.route("POST", "/api/story/direction/check")
    def direction_check(req: Request, lib: Library) -> dict[str, Any]:
        """Resolve a (partial) direction against its preset and report contradictions and a readable summary."""
        body = dict(req.body or {})
        resolved = creative.resolve(body.get("direction") or {}, preset=body.get("preset") or None, home=lib.paths.home)
        return {"direction": resolved.model_dump(mode="json"), "warnings": creative.warnings(resolved),
                "summary": creative.summary(resolved)}  # fmt: skip

    @router.route("POST", "/api/story/presets")
    def preset_save(req: Request, lib: Library) -> dict[str, Any]:
        body = dict(req.body or {})
        resolved = creative.resolve(body.get("direction") or {}, preset=body.get("from_preset") or None, home=lib.paths.home)
        path = creative.save_preset(lib.paths.home, str(body.get("name", "")), resolved, title=str(body.get("title", "")),
                                    summary=str(body.get("summary", "")))  # fmt: skip
        return {"preset": creative.get_preset(path.stem, lib.paths.home).model_dump(mode="json"), "path": str(path)}

    @router.route("POST", r"/api/story/presets/(?P<name>[a-z0-9-]+)/delete")
    def preset_delete(req: Request, lib: Library) -> dict[str, Any]:
        creative.delete_preset(lib.paths.home, req.match["name"])
        return {"deleted": req.match["name"]}

    # ------------------------------------------------------------------ AI execution plan (library-wide)

    @router.route("GET", "/api/ai/plan")
    def ai_plan(req: Request, lib: Library) -> dict[str, Any]:
        return {"plan": plans.load_plan(lib.paths.home).model_dump(mode="json"), "task_kinds": plans.TASK_KINDS,
                "providers": providers(req, lib)["providers"]}  # fmt: skip

    @router.route("POST", "/api/ai/plan")
    def ai_plan_save(req: Request, lib: Library) -> dict[str, Any]:
        plan = plans.ExecutionPlan(**dict(req.body or {}))
        plans.save_plan(lib.paths.home, plan)
        return {"plan": plan.model_dump(mode="json")}

    # ------------------------------------------------------------------ workflow, approvals, characters, alternatives

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/workflow")
    def story_workflow(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.story.progress import progress

        sid, project = _story(lib, req.match["sid"])
        return workflow.status(lib, project, progress(lib, project))

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/workflow")
    def story_workflow_change(req: Request, lib: Library) -> dict[str, Any]:
        """action: mode (mode) | approve (stage, note) | changes (stage, note) | reopen (stage, note)."""
        from assetforge.story.progress import progress

        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        action, stage, note = str(body.get("action", "")), str(body.get("stage", "")), str(body.get("note", ""))
        if action == "mode":
            workflow.set_mode(project, str(body.get("mode", "")))
        elif action == "approve":
            workflow.approve(project, stage, note=note)
        elif action == "changes":
            workflow.request_changes(project, stage, note)
        elif action == "reopen":
            workflow.unapprove(project, stage, note)
        else:
            raise InvalidInputError("action must be mode, approve, changes or reopen")
        return workflow.status(lib, project, progress(lib, project))

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/evaluation")
    def story_evaluation(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.story import evaluate

        sid, project = _story(lib, req.match["sid"])
        data = evaluate.load(project)
        return {"evaluation": data.model_dump(mode="json") if data else None, "summary": evaluate.summary(data) if data else None,
                "questions": [{"id": q, "text": t} for q, t in evaluate.QUESTIONS], "feedback": evaluate.as_feedback(data) if data else ""}  # fmt: skip

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/releases")
    def story_releases(req: Request, lib: Library) -> dict[str, Any]:
        """Content-contract v2 releases: the index, the last compile report and publish receipts."""
        from assetforge.publish import r2
        from assetforge.story import contract

        sid, project = _story(lib, req.match["sid"])
        view = contract.releases_view(project)
        try:
            view["r2"] = r2.resolve_config(lib.paths.home).describe()
        except AssetForgeError as exc:
            view["r2"] = {"configured": False, "message": exc.message}
        return view

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/dynamics")
    def story_dynamics(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.story import dynamics

        sid, project = _story(lib, req.match["sid"])
        return dynamics.view(project)

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters")
    def story_characters(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        if not project.has("cast"):
            return {"cast": [], "profiles": {}, "sections": list(characters.SECTIONS)}
        return {"cast": characters.overview(lib, project), "sections": list(characters.SECTIONS),
                "profiles": {k: v.model_dump(mode="json") for k, v in characters.profiles(project).items()}}  # fmt: skip

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters/(?P<cid>[a-z0-9_]+)")
    def story_character_edit(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        profile = characters.update(project, req.match["cid"], dict(body.get("patch") or {}), note=str(body.get("note", "")),
                                    unlock=bool(body.get("unlock")))  # fmt: skip
        return {"profile": profile.model_dump(mode="json"), "impact": workflow.character_impact(project, req.match["cid"])}

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters/(?P<cid>[a-z0-9_]+)/approval")
    def story_character_approval(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        profile = characters.set_approval(project, req.match["cid"], str(body.get("section", "all")), str(body.get("state", "approved")),
                                          str(body.get("note", "")))  # fmt: skip
        return {"profile": profile.model_dump(mode="json")}

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters/(?P<cid>[a-z0-9_]+)/lock")
    def story_character_lock(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        profile = characters.set_lock(project, req.match["cid"], str(body.get("section", "")), bool(body.get("locked", True)))
        return {"profile": profile.model_dump(mode="json")}

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters/(?P<cid>[a-z0-9_]+)/proposals/(?P<pid>[a-z0-9]+)")
    def story_character_proposal(req: Request, lib: Library) -> dict[str, Any]:
        """apply (optional `sections`) or discard a proposal."""
        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        if body.get("discard"):
            profile = characters.discard_proposal(project, req.match["cid"], req.match["pid"])
        else:
            profile = characters.apply_proposal(project, req.match["cid"], req.match["pid"], sections=body.get("sections") or None)
        return {"profile": profile.model_dump(mode="json")}

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters/(?P<cid>[a-z0-9_]+)/master")
    def story_character_master(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        profile = characters.set_master(lib, project, req.match["cid"], str((req.body or {}).get("asset_id", "")))
        return {"profile": profile.model_dump(mode="json")}

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/stakes")
    def story_stakes(req: Request, lib: Library) -> dict[str, Any]:
        """Choice QA over the current script (written scenes), the payoff ledger, the rule registry and the reader
        knowledge state per chapter."""
        from assetforge.story import stakes
        from assetforge.story.progress import preview_script

        sid, project = _story(lib, req.match["sid"])
        choices = None
        if project.has("outline") and project.has("cast"):
            from assetforge.story.models import Script

            data = preview_script(project)
            if data:
                written = [sc for sc in data["scenes"] if not sc.get("draft")]
                if written:
                    script = Script(**{**data, "scenes": written})
                    choices = stakes.choice_report(script, unwritten_after=len(data["scenes"]) - len(written))
        return {"choices": choices, "payoffs": stakes.ledger_view(project), "rules": stakes.load_rules(project).model_dump(mode="json"),
                "knowledge": stakes.knowledge_state(project)}

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/visual-check")
    def story_visual_check(req: Request, lib: Library) -> dict[str, Any]:
        """The visual logic verdicts by asset key (`current` = it judged the image the key holds now) and the canon."""
        from assetforge.story import visual_canon, visual_check

        _, project = _story(lib, req.match["sid"])
        canon = visual_canon.load(project)
        return {**visual_check.view(lib, project), "canon": canon.model_dump(mode="json") if canon else None}

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/acting")
    def story_acting(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.story import acting

        sid, project = _story(lib, req.match["sid"])
        return acting.plan(lib, project, (req.query.get("character") or [None])[0])

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/acting/(?P<cid>[a-z0-9_]+)/profile")
    def story_acting_profile(req: Request, lib: Library) -> dict[str, Any]:
        """patch fields, or set state approved|changes_requested|proposed with a note."""
        from assetforge.story import acting

        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        data = acting.set_profile(project, req.match["cid"], patch=dict(body.get("patch") or {}) or None, state=body.get("state") or None,
                                  note=str(body.get("note", "")))  # fmt: skip
        return {"profile": data.profiles[req.match["cid"]].model_dump(mode="json")}

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/acting/performances/(?P<pid>[a-z0-9]+)")
    def story_acting_decide(req: Request, lib: Library) -> dict[str, Any]:
        """action approve | modify | reuse | dismiss (direction patch, reuse_sprite, note)."""
        from assetforge.story import acting

        sid, project = _story(lib, req.match["sid"])
        body = dict(req.body or {})
        data = acting.decide(project, req.match["pid"], str(body.get("action", "")), direction=dict(body.get("direction") or {}) or None,
                             reuse_sprite=str(body.get("reuse_sprite", "")), note=str(body.get("note", "")))  # fmt: skip
        perf = next(p for p in data.performances if p.id == req.match["pid"])
        return {"performance": perf.model_dump(mode="json")}

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters/(?P<cid>[a-z0-9_]+)/consistency")
    def story_character_consistency(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        return characters.consistency(lib, project, req.match["cid"])

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/characters/(?P<cid>[a-z0-9_]+)/impact")
    def story_character_impact(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        return workflow.character_impact(project, req.match["cid"])

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/audio/(?P<scene>[a-z0-9_]+)")
    def story_audio(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        return _audio_view(lib, project, req.match["scene"])

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/audio/(?P<scene>[a-z0-9_]+)")
    def story_audio_change(req: Request, lib: Library) -> dict[str, Any]:
        """action: apply (approved_only) | update (cue, patch) | remove (cue) | add (cue) | regenerate (cue) |
        approve_asset (cue) | reject_asset (cue)."""
        from assetforge.core import review
        from assetforge.core.models import Status
        from assetforge.exporters.vn import export_story

        sid, project = _story(lib, req.match["sid"])
        scene_id = req.match["scene"]
        body = dict(req.body or {})
        action = str(body.get("action", ""))
        cue = str(body.get("cue", ""))
        if action == "apply":
            result = audio_direction.apply(project, scene_id, approved_only=bool(body.get("approved_only")))
            result["export"] = export_story(lib, project, project.root / "build")
            view = _audio_view(lib, project, scene_id)
            view["applied"] = result
            return view
        if action == "update":
            audio_direction.update_cue(project, scene_id, cue, dict(body.get("patch") or {}))
        elif action == "remove":
            audio_direction.remove_cue(project, scene_id, cue)
        elif action == "add":
            audio_direction.add_cue(project, scene_id, dict(body.get("cue_data") or {}))
        elif action in ("approve_asset", "reject_asset", "regenerate"):
            view = _audio_view(lib, project, scene_id)
            row = next((r for r in view["cues"] if r["id"] == cue), None)
            if row is None or not row.get("asset"):
                raise NotFoundError(f"No generated sound for cue {cue!r}")
            if action == "approve_asset":
                review.approve(lib, [row["asset"]["id"]], note="approved in the scene audio panel", force=True)
                audio_direction.update_cue(project, scene_id, cue, {"status": "approved"})
            elif action == "reject_asset":
                lib.set_status([row["asset"]["id"]], Status.REJECTED, note=str(body.get("note", "rejected in the scene audio panel")))
                audio_direction.update_cue(project, scene_id, cue, {"status": "revision"})
            else:  # regenerate: archive the sound and let audio_make remake it
                review.archive(lib, [row["asset"]["id"]], note="regenerate requested")
                audio_direction.update_cue(project, scene_id, cue, {"status": "needs_generation", "note": str(body.get("note", ""))})
        else:
            raise InvalidInputError("action must be apply, update, remove, add, regenerate, approve_asset or reject_asset")
        return _audio_view(lib, project, scene_id)

    @router.route("GET", r"/api/stories/(?P<sid>[a-z0-9-]+)/alternatives/(?P<scene>[a-z0-9_]+)")
    def story_alternatives(req: Request, lib: Library) -> dict[str, Any]:
        sid, project = _story(lib, req.match["sid"])
        return project.read_json(f"alternatives/{req.match['scene']}.json") or {"scene": req.match["scene"], "items": []}

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/alternatives/(?P<scene>[a-z0-9_]+)/(?P<alt>[a-z0-9]+)")
    def story_alternative_choose(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.exporters.vn import export_story

        sid, project = _story(lib, req.match["sid"])
        result = steps.choose_alternative(project, req.match["scene"], req.match["alt"])
        result["export"] = export_story(lib, project, project.root / "build")
        return result

    @router.route("POST", "/api/stories")
    def create_story(req: Request, lib: Library) -> dict[str, Any]:
        body = dict(req.body or {})
        title = str(body.pop("title", "")).strip()
        premise = str(body.pop("premise", "")).strip()
        if not title or len(premise) < 3:
            raise InvalidInputError("A story needs a title and a premise")
        # an untitled story is keyed by its idea (the writer names it later; slug and asset keys never change)
        slug = slugify(title) if not body.get("title_from_writer") else slugify(" ".join(premise.split()[:6]))[:40].strip("-")
        directory = body.pop("dir", None) or lib.paths.home / "stories" / slug
        preset, direction = body.pop("preset", None), body.pop("direction", None)
        allowed = set(StoryConfig.model_fields) - {"title", "slug", "premise", "direction"}
        options = {k: v for k, v in body.items() if k in allowed and v not in ("", None)}
        if preset or direction is not None:  # every story made in the app carries an explicit direction
            options["direction"] = creative.resolve(direction or {}, preset=preset or None, home=lib.paths.home)
        project = steps.init(directory, title=title, premise=premise, slug=slug, **options)
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
        from assetforge.story.progress import library_assets, progress

        if project.has("names"):
            data["names"] = project.load("names").model_dump(mode="json")
        if project.config.direction is not None:
            data["direction"] = {"summary": creative.summary(project.config.direction),
                                 "warnings": creative.warnings(project.config.direction)}  # fmt: skip
        data["cover"] = (library_assets(lib, project).get(f"vn/{project.config.slug}/ui/title") or {}).get("image")
        data["written"] = {sid_: sc.model_dump(mode="json", exclude_none=True) for sid_, sc in project.scenes().items()}
        data["progress"] = progress(lib, project, with_assets=project.has("script"))
        data["workflow"] = workflow.status(lib, project, data["progress"])
        data["feedback"] = project.read_json("feedback.json").get("history", [])[-10:]
        data["playable"] = data["progress"]["playable_chapters"] > 0
        data["exported"] = (project.root / "build" / "index.html").exists()
        return data

    @router.route("POST", r"/api/stories/(?P<sid>[a-z0-9-]+)/settings")
    def story_settings(req: Request, lib: Library) -> dict[str, Any]:
        """Change the writer (provider/model), the creative direction or other config fields; the next steps use
        them. A direction change on a story with written parts returns an impact review first (`needs_confirm`)
        unless `confirm` is set, so established canon is never altered by accident."""
        sid, project = _story(lib, req.match["sid"])
        config = project.config
        body = dict(req.body or {})
        confirm = bool(body.pop("confirm", False))
        changes = {k: (v or None) if k in ("provider", "model", "art_style", "project") else v
                   for k, v in body.items() if k in StoryConfig.model_fields and k not in ("slug", "direction")}  # fmt: skip
        result: dict[str, Any] = {}
        if "direction" in body:
            new = None if body["direction"] is None else creative.resolve(body["direction"], preset=body.get("preset") or None,
                                                                           home=lib.paths.home)  # fmt: skip
            if new is not None:
                written = {"world": project.has("world"), "cast": project.has("cast"), "outline": project.has("outline"),
                           "chapters": bool(project.scenes()), "assets": project.has("script")}  # fmt: skip
                impact = creative.impact(config.direction, new, written)
                if impact and not confirm:
                    return {"config": config.model_dump(mode="json"), "needs_confirm": True, "impact": impact,
                            "warnings": creative.warnings(new), "summary": creative.summary(new)}  # fmt: skip
                result.update(warnings=creative.warnings(new), summary=creative.summary(new), impact=impact)
            changes["direction"] = new
        updated = StoryConfig(**{**config.model_dump(), **changes})
        project.save("config", updated)
        result["config"] = updated.model_dump(mode="json")
        return result

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
        from assetforge.app.server import PLAYER_CSP

        return FileResponse(target, mime, "no-cache", headers={"Content-Security-Policy": PLAYER_CSP})


def _audio_view(lib: Library, project: StoryProject, scene_id: str) -> dict[str, Any]:
    """The scene audio panel's data: the plan with library statuses, the chapter's lines, the review and the
    intensity strip."""
    scene = project.load_scene(scene_id)
    if scene is None:
        raise NotFoundError(f"Chapter {scene_id!r} is not written yet")
    plan = audio_direction.load(project, scene_id)
    lines = audio_direction.lines(scene.model_copy(update={"nodes": [n for n in scene.nodes if not n.why]}))
    if plan is None:
        return {"scene": scene_id, "plan": None, "cues": [], "lines": lines, "intensity": [], "review": None,
                "applied_nodes": sum(1 for n in scene.nodes if n.why)}  # fmt: skip
    bare = scene.model_copy(update={"nodes": [n for n in scene.nodes if not n.why]})
    return {"scene": scene_id, "plan": {k: v for k, v in plan.model_dump(mode="json").items() if k != "cues"},
            "project": project.config.asset_project, "cues": audio_direction.statuses(lib, project, plan), "lines": lines,
            "intensity": audio_direction.intensity(plan, bare), "review": audio_direction.review(plan, bare, project.config),
            "applied_nodes": sum(1 for n in scene.nodes if n.why)}  # fmt: skip


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
                            model=body.get("model") or None, fresh=bool(body.get("fresh")),
                            allow_paid=bool(body.get("allow_paid")))  # fmt: skip
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

    def paused(stage: str) -> dict[str, Any]:
        workflow.pause(project, stage)
        title = next(s["title"] for s in workflow.STAGES if s["id"] == stage)
        return {"paused_at": stage, "needs": f"Review and approve: {title}", "written": [], "kept": []}

    if action == "write":  # everything not done yet: world → cast → outline → scenes, then a playable export;
        # in guided/manual mode it stops at the next stage that waits for the operator's approval
        for step, fn in (("world", steps.world), ("cast", steps.cast), ("outline", steps.outline)):
            if project.has(step):
                continue
            if stage := workflow.blocked_by(project, step):
                return paused(stage)
            fn(ctx)
            if step == "cast":
                characters.profiles(project)  # draft profiles for the studio as soon as the cast exists
        if stage := workflow.blocked_by(project, "write"):
            return paused(stage)
        result = steps.write(ctx, workers=int(body.get("workers") or 2))
        if result.get("assembled"):
            result["export"] = export_story(lib, project, project.root / "build")
        return result
    if action == "contract":  # compile a content-contract v2 release (local only; never uploads)
        from assetforge.story import contract

        report = contract.compile_release(lib, project, allow_missing=bool(body.get("allow_missing")))
        return {"report": report.model_dump(mode="json"), "releases": contract.releases_view(project)}
    if action == "publish":  # the plan, unless the operator confirmed and the release can be published
        from assetforge.publish import r2
        from assetforge.story import contract

        path = contract.release_dir(project, str(body.get("release") or "") or None)
        plan = r2.plan(path, home=lib.paths.home)
        if body.get("confirm") is True and plan["publishable"]:
            return {"published": True, "plan": plan, "receipt": r2.publish(path, yes=True, home=lib.paths.home)}
        return {"published": False, "plan": plan}
    if action in ("payoffs_draft", "rules_draft"):
        from assetforge.story import stakes

        if action == "payoffs_draft":
            stakes.draft_ledger(ctx, fresh=bool(body.get("fresh"))); stakes.advance_ledger(project, ctx)
            return {"payoffs": stakes.ledger_view(project)}
        return {"rules": stakes.draft_rules(ctx, fresh=bool(body.get("fresh"))).model_dump(mode="json")}
    if action in ("acting_profile", "acting_analyze"):
        from assetforge.story import acting

        cid = str(body.get("character", ""))
        if not cid:
            raise InvalidInputError("Which character? Pass `character`")
        if action == "acting_profile":
            data = acting.profile(ctx, cid, fresh=bool(body.get("fresh")))
        else:
            scenes = [str(x) for x in (body.get("scenes") or [])]
            data = acting.analyze(ctx, cid, scenes or None, fresh=bool(body.get("fresh")))
        return {"acting": acting.plan(lib, project, cid)}
    if action in ("visual_canon", "art_check", "art_fix"):
        from assetforge.story import visual_canon, visual_check

        if action == "visual_canon":
            return {"canon": visual_canon.draft(ctx, fresh=bool(body.get("fresh"))).model_dump(mode="json")}
        if action == "art_check":
            groups = tuple(str(g) for g in (body.get("groups") or visual_check.DEFAULT_GROUPS))
            limit = int(body["limit"]) if body.get("limit") is not None else None
            return visual_check.review(ctx, groups=groups, only_unchecked=not body.get("all"), limit=limit)
        key = str(body.get("key", ""))
        if not key:
            raise InvalidInputError("Which image? Pass `key`")
        return visual_check.fix(ctx, key, max_rounds=int(body.get("max_rounds") or 2),
                                image_provider=str(body.get("image_provider") or "auto"), quality=str(body.get("quality") or "auto"))  # fmt: skip
    if action == "acting_generate":
        from assetforge.story import acting

        pid = str(body.get("performance", ""))
        if not pid:
            raise InvalidInputError("Which performance? Pass `performance`")
        return acting.generate(lib, project, pid, quality=str(body.get("quality", "auto")), image_provider=str(body.get("image_provider", "auto")))
    if action == "evaluate":
        from assetforge.story import evaluate

        scenes = [str(x) for x in (body.get("scenes") or [])]
        return {"evaluation": evaluate.evaluate(ctx, scenes or None, fresh=bool(body.get("fresh"))).model_dump(mode="json")}
    if action == "dynamics_plan":
        from assetforge.story import dynamics

        return {"dynamics": dynamics.plan(ctx, fresh=bool(body.get("fresh"))).model_dump(mode="json")}
    if action == "dynamics_estimate":
        from assetforge.story import dynamics

        chapter = str(body.get("chapter", ""))
        if not chapter:
            raise InvalidInputError("Which chapter? Pass `chapter`")
        return {"dynamics": dynamics.estimate(ctx, chapter, fresh=bool(body.get("fresh"))).model_dump(mode="json")}
    if action == "character":
        proposal = characters.revise(ctx, str(body.get("character", "")), str(body.get("request", "")))
        return {"character": body.get("character"), "proposal": proposal.model_dump(mode="json")}
    if action == "audio_plan":
        plan = audio_direction.plan(ctx, str(body.get("scene", "")), note=str(body.get("note", "")))
        return {"scene": plan.scene, "plan": plan.model_dump(mode="json")}
    if action == "audio_make":  # the sounds the applied cues need (free, local synthesis; SFX reuse by search first)
        result = produce(lib, project, only=["audio"], image_provider="auto", redo=False)
        if body.get("scene"):
            result["audio"] = _audio_view(lib, project, str(body["scene"]))
        result["export"] = export_story(lib, project, project.root / "build")
        return result
    if action == "alternatives":
        return steps.alternatives(ctx, str(body.get("scene", "")), n=int(body.get("n") or 3), note=str(body.get("note", "")))
    if action == "concepts":
        if stage := workflow.blocked_by(project, "assets"):
            if stage != "visual":  # concepts are how the visual identity gets approved; only other gates block them
                return paused(stage)
        return characters.concepts(lib, project, str(body.get("character", "")), n=int(body.get("n") or 3),
                                   image_provider=body.get("image_provider") or "auto", quality=body.get("quality") or "auto",
                                   dry_run=bool(body.get("dry_run")))  # fmt: skip
    if action in ("assets", "assets_plan") and action == "assets" and (stage := workflow.blocked_by(project, "assets")):
        return paused(stage)
    if action == "export" and (stage := workflow.blocked_by(project, "export")):
        return paused(stage)
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
                         quality=body.get("quality") or "auto", redo=bool(body.get("redo")),
                         replan=bool(body.get("replan")))  # fmt: skip
        if action == "assets":
            result["export"] = export_story(lib, project, project.root / "build")
        return result
    return export_story(lib, project, project.root / "build")
