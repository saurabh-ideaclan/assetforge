"""Music & Ambience Studio routes: direction from a brief, production runs (background tasks with readable
status), candidates with measured reviews, approval, and the per-project sound identity (references)."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from assetforge.app.server import Request, Router
from assetforge.audio import production, quality
from assetforge.core.catalog import Library
from assetforge.core.models import AssetPatch, Status
from assetforge.errors import AssetForgeError, InternalError, InvalidInputError, NotFoundError

_tasks: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
STEPS = ["Brief understood", "Direction prepared", "Existing approved assets checked", "Candidates rendered",
         "Quality review", "Refinement", "Candidates ready for listening"]  # fmt: skip


def register_routes(router: Router) -> None:
    @router.route("POST", "/api/audio/direct")
    def audio_direct(req: Request, lib: Library) -> dict[str, Any]:
        body = dict(req.body or {})
        kind = str(body.get("kind", "music"))
        if kind not in ("music", "ambience"):
            raise InvalidInputError("kind must be music or ambience")
        direction, chosen = production.direct(lib, str(body.get("brief", "")), kind, project=body.get("project") or None,
                                              context=body.get("context") or None, provider=body.get("provider") or None,
                                              model=body.get("model") or None, feedback=body.get("feedback") or None,
                                              fresh=bool(body.get("fresh")))  # fmt: skip
        return {"kind": kind, "direction": direction.model_dump(mode="json"), "directed_by": chosen,
                "expected": quality.expectations(kind, direction.model_dump())}  # fmt: skip

    @router.route("GET", "/api/audio/catalog")
    def audio_catalog(req: Request, lib: Library) -> dict[str, Any]:
        import json

        return json.loads(production.catalog_text(lib))

    @router.route("POST", "/api/audio/produce")
    def audio_produce(req: Request, lib: Library) -> dict[str, Any]:
        """Start a production run in the background; poll /api/audio/tasks/{id}."""
        body = dict(req.body or {})
        kind = str(body.get("kind", "music"))
        if kind not in ("music", "ambience"):
            raise InvalidInputError("kind must be music or ambience")
        if not str(body.get("brief", "")).strip() and not body.get("direction"):
            raise InvalidInputError("Give a brief or a direction")
        task_id = uuid.uuid4().hex[:10]
        task = {"id": task_id, "kind": kind, "state": "running", "started": time.time(), "step": 0, "steps": STEPS,
                "label": "Brief understood", "result": None, "error": None, "name": body.get("name") or f"{kind}_{task_id[:4]}"}  # fmt: skip
        with _lock:
            _tasks[task_id] = task
        threading.Thread(target=_work, args=(lib.paths.home, task_id, body), daemon=True).start()
        return {"task_id": task_id, "task": _public(task)}

    @router.route("GET", r"/api/audio/tasks/(?P<tid>[0-9a-f]+)")
    def audio_task(req: Request, lib: Library) -> dict[str, Any]:
        task = _tasks.get(req.match["tid"])
        if task is None:
            raise NotFoundError("No such production run")
        return {"task": _public(task)}

    @router.route("GET", "/api/audio/tasks")
    def audio_tasks(req: Request, lib: Library) -> dict[str, Any]:
        return {"tasks": [_public(t) for t in sorted(_tasks.values(), key=lambda t: -t["started"])[:20]]}

    @router.route("POST", "/api/audio/approve")
    def audio_approve(req: Request, lib: Library) -> dict[str, Any]:
        """Approve a candidate (optionally under a story key), reject its kept sibling, and optionally make it a
        reference of the project's sound identity."""
        from assetforge.core import review

        body = dict(req.body or {})
        asset_id = lib.resolve_asset_id(str(body.get("asset_id", "")))
        review.approve(lib, [asset_id], note=str(body.get("note", "approved in the Music & Ambience Studio")), force=True)
        patch: dict[str, Any] = {}
        if body.get("key"):
            from assetforge.request import AssetRequest, by_key

            probe = AssetRequest(type="music", description="probe", project=body.get("project") or None, key=str(body["key"]))  # type: ignore[arg-type]
            old = by_key(lib, probe)
            if old and old != asset_id:
                lib.update_asset(old, AssetPatch(key=""))
            patch["key"] = str(body["key"])
        if body.get("project"):
            patch["project"] = str(body["project"])
        if patch:
            lib.update_asset(asset_id, AssetPatch(**patch))
        for other in body.get("reject") or []:
            lib.set_status([lib.resolve_asset_id(str(other))], Status.REJECTED, note="not chosen in the studio")
        guide = None
        if body.get("as_reference") and body.get("project"):
            guide = production.add_reference(lib, str(body["project"]), asset_id, note=str(body.get("note", "")))
        return {"asset_id": asset_id, "guide": guide}

    @router.route("GET", r"/api/audio/style/(?P<project>[a-z0-9-]+)")
    def audio_style(req: Request, lib: Library) -> dict[str, Any]:
        return production.style_guide(lib, req.match["project"])

    @router.route("POST", r"/api/audio/style/(?P<project>[a-z0-9-]+)")
    def audio_style_change(req: Request, lib: Library) -> dict[str, Any]:
        body = dict(req.body or {})
        action = str(body.get("action", ""))
        project = req.match["project"]
        if action == "add_reference":
            return production.add_reference(lib, project, str(body.get("asset_id", "")), note=str(body.get("note", "")))
        if action == "remove_reference":
            return production.remove_reference(lib, project, str(body.get("asset_id", "")))
        if action == "preferences":
            return production.set_preferences(lib, project, dict(body.get("preferences") or {}))
        raise InvalidInputError("action must be add_reference, remove_reference or preferences")

    @router.route("GET", r"/api/audio/measure/(?P<ref>[^/]+)")
    def audio_measure(req: Request, lib: Library) -> dict[str, Any]:
        asset = lib.get_asset(req.match["ref"])
        m = quality.measure(Path(asset.path))
        meta = asset.metadata or {}
        prod = meta.get("production") or {}
        rev = quality.review(prod.get("kind") or ("music" if asset.type.value == "music" else "ambience"), prod.get("direction") or {}, m) if prod else None
        return {"asset_id": asset.id, "measured": m, "review": rev}


    # ------------------------------------------------------------ the analysis workbench

    @router.route("GET", r"/api/audio/analysis/(?P<ref>[^/]+)")
    def audio_analysis(req: Request, lib: Library) -> dict[str, Any]:
        """Deep analysis of an asset (or one of its variants): measurements, findings, plan, text, visuals."""
        from assetforge.app.server import FileResponse  # noqa: F401  (route registration order)
        from assetforge.audio.analysis import analyze_file, to_json
        from assetforge.audio.analysis.corrections import fill_donors, plan_corrections
        from assetforge.audio.analysis.diagnose import diagnose, summarize
        from assetforge.audio.analysis.interpret import describe
        from assetforge.audio.analysis.reference import load_style, place
        from assetforge.audio.analysis.visuals import render
        from assetforge.audio.masters import ensure_file

        asset = lib.get_asset(req.match["ref"])
        variant = next((v for v in asset.variants if v.id == req.match["ref"]), asset.primary_variant)
        if variant is None or variant.file.kind != "audio":
            raise InvalidInputError(f"{asset.id} is not audio")
        path = ensure_file(lib, variant)
        kind = asset.type.value if asset.type.value in ("music", "ambience", "sfx") else "music"
        meta = asset.metadata or {}
        prod = meta.get("production") or {}
        brief = req.arg("brief") or prod.get("brief")
        direction = prod.get("direction")
        loop = (meta.get("audio") or {}).get("loop")
        if loop is None:
            comp = ((meta.get("generator") or {}).get("spec") or {}).get("compose") or {}
            loop = comp["form"] == "loop" if comp.get("form") else (True if kind == "ambience" else None)
        report = analyze_file(path, kind=kind)
        findings = diagnose(report, kind=kind, brief=brief, direction=direction, loop=loop)
        plan = fill_donors(plan_corrections(findings, kind=kind), report)
        out_dir = lib.paths.home / "previews" / "analysis" / variant.id
        files = render(report, out_dir, path=path)
        visuals = {k: f"/media/analysis/{variant.id}/{Path(v).name}" for k, v in files.items() if k != "timelines"}
        import json as _json

        timelines = _json.loads(Path(files["timelines"]).read_text())
        placement = None
        if asset.project:
            prof = (load_style(lib.paths.home, asset.project).get("profiles") or {}).get(kind)
            placement = place(report, prof) if prof else None
        return {"asset_id": asset.id, "variant_id": variant.id, "kind": kind, "loop": loop, "brief": brief,
                "report": to_json(report), "findings": findings, "summary": summarize(findings), "plan": plan,
                "sections": describe(report, findings), "visuals": visuals, "timelines": timelines, "placement": placement}  # fmt: skip

    @router.route("GET", r"/media/analysis/(?P<variant>var_\d+)/(?P<name>[a-z]+\.png)")
    def media_analysis(req: Request, lib: Library):
        from assetforge.app.server import FileResponse

        path = lib.paths.home / "previews" / "analysis" / req.match["variant"] / req.match["name"]
        if not path.exists():
            raise NotFoundError("no such diagnostic image; load the analysis first")
        return FileResponse(path, "image/png", "no-cache")

    @router.route("POST", "/api/audio/correct")
    def audio_correct(req: Request, lib: Library) -> dict[str, Any]:
        """Apply the plan's DSP steps to an asset as a new variant and re-measure; or run one named action."""
        from assetforge.audio.analysis import analyze_file
        from assetforge.audio.analysis.corrections import compare, correct_asset, fill_donors, plan_corrections
        from assetforge.audio.analysis.diagnose import diagnose, summarize
        from assetforge.audio.masters import ensure_file

        body = dict(req.body or {})
        asset = lib.get_asset(str(body.get("asset_id", "")))
        variant = next((v for v in asset.variants if v.id == body.get("variant_id")), asset.primary_variant)
        kind = asset.type.value if asset.type.value in ("music", "ambience", "sfx") else "music"
        path = ensure_file(lib, variant)
        report = analyze_file(path, kind=kind)
        loop = body.get("loop")
        findings = diagnose(report, kind=kind, brief=body.get("brief"), loop=loop)
        if body.get("ops"):  # one human-friendly action already turned into ops by the UI
            plan = {"dsp": list(body["ops"]), "reasons": [str(body.get("reason", "requested"))], "composition": {}, "layer": {}, "regenerate": None, "method": []}
            if not any(o.get("op") == "normalize" for o in plan["dsp"]):
                from assetforge.audio.master import LOUDNESS_TARGETS

                plan["dsp"].append({"op": "normalize", "mode": "lufs", "target": LOUDNESS_TARGETS.get(kind, -16.0), "ceiling_db": -1.5})
        else:
            plan = fill_donors(plan_corrections(findings, kind=kind, prefer=str(body.get("prefer", "precise"))), report)
        made = correct_asset(lib, asset.id, plan, label=str(body.get("label") or "corrected"), variant=variant.id)
        result: dict[str, Any] = {"asset_id": asset.id, "plan": plan, "applied": made, "before": summarize(findings)}
        if made:
            new_variant = next(v for v in lib.get_asset(asset.id).variants if v.id == made["variant_id"])
            after = analyze_file(ensure_file(lib, new_variant), kind=kind)
            after_f = diagnose(after, kind=kind, brief=body.get("brief"), loop=loop)
            result["comparison"] = compare({"findings": findings}, {"findings": after_f}, [f["code"] for f in findings if f["severity"] != "note"])
            result["after"] = summarize(after_f)
        return result

    @router.route("POST", "/api/audio/preference")
    def audio_preference(req: Request, lib: Library) -> dict[str, Any]:
        """Record an approve / reject with the brief, the measured findings and the owner's words."""
        from assetforge.audio.analysis import analyze_file
        from assetforge.audio.analysis.diagnose import diagnose
        from assetforge.audio.analysis.reference import features_of, record_preference
        from assetforge.audio.masters import ensure_file

        body = dict(req.body or {})
        asset = lib.get_asset(str(body.get("asset_id", "")))
        project = str(body.get("project") or asset.project or "")
        if not project:
            raise InvalidInputError("a project is needed to record a preference")
        kind = asset.type.value if asset.type.value in ("music", "ambience", "sfx") else "music"
        report = analyze_file(ensure_file(lib, asset.primary_variant), kind=kind, with_vad=False)
        findings = diagnose(report, kind=kind, brief=body.get("brief"))
        row = record_preference(lib.paths.home, project, asset_id=asset.id, decision=str(body.get("decision", "approved")), kind=kind,
                                brief=str(body.get("brief", "")), findings=findings, feedback=str(body.get("feedback", "")),
                                features=features_of(report), changes=list(body.get("changes") or []))  # fmt: skip
        return {"recorded": row}

    @router.route("GET", r"/api/audio/profile/(?P<project>[^/]+)")
    def audio_profile_get(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.audio.analysis.reference import describe, load_style

        data = load_style(lib.paths.home, req.match["project"])
        profiles = data.get("profiles") or {}
        return {"project": req.match["project"], "profiles": profiles, "described": {k: describe(v) for k, v in profiles.items()},
                "history": (data.get("history") or [])[-50:]}  # fmt: skip

    @router.route("POST", r"/api/audio/profile/(?P<project>[^/]+)")
    def audio_profile_make(req: Request, lib: Library) -> dict[str, Any]:
        """Build (or rebuild) the measured profile of a project's approved assets of one kind."""
        from assetforge.audio.analysis.reference import describe, profile_files, set_profile
        from assetforge.audio.masters import ensure_file

        body = dict(req.body or {})
        kind = str(body.get("kind") or "music")
        project = req.match["project"]
        ids = list(body.get("asset_ids") or [])
        if not ids:
            ids = [r["id"] for r in lib.conn.execute("SELECT id FROM assets WHERE project = ? AND type = ? AND status = 'approved'", (project, kind)).fetchall()]
        paths = []
        for aid in ids:
            a = lib.get_asset(aid)
            if a.primary_variant is not None and a.primary_variant.file.kind == "audio":
                paths.append(ensure_file(lib, a.primary_variant))
        if not paths:
            raise NotFoundError(f"no approved {kind} assets in {project}")
        prof = profile_files(paths, kind=kind, name=f"{project}/{kind}", note=f"from {len(paths)} approved {kind} assets")
        set_profile(lib.paths.home, project, kind, prof)
        return {"project": project, "kind": kind, "files": len(paths), "profile": prof, "described": describe(prof)}


def _public(task: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in task.items() if k != "result"} | {"result": task.get("result"), "elapsed": round(time.time() - task["started"])}


def _work(home: Path, task_id: str, body: dict[str, Any]) -> None:
    task = _tasks[task_id]

    def step(i: int) -> None:
        task["step"], task["label"] = i, STEPS[min(i, len(STEPS) - 1)]

    try:
        with Library(home) as lib:
            step(1)
            direction = body.get("direction") or None
            step(2)
            result = production.produce_for_brief(
                lib, str(body.get("brief", "")), kind=body["kind"], name=str(task["name"]), seed=int(body.get("seed") or 0),
                candidates=max(1, min(4, int(body.get("candidates") or 3))), project=body.get("project") or None,
                context=body.get("context") or None, reference=body.get("reference") or None, direction=direction,
                provider=body.get("provider") or None, model=body.get("model") or None, feedback=body.get("feedback") or None,
                key=body.get("key") or None, max_rounds=int(body.get("max_rounds", production.MAX_ROUNDS)))  # fmt: skip
            for c in result["candidates"]:
                a = lib.get_asset(c["asset_id"])
                c["variant_id"] = a.primary_variant.id if a.primary_variant else None
                c["name"] = a.name
            step(6)
            task["result"] = result
            task["state"] = "done"
    except AssetForgeError as exc:
        task.update(state="failed", error=exc.to_dict())
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        task.update(state="failed", error=InternalError(f"{type(exc).__name__}: {exc}").to_dict())
