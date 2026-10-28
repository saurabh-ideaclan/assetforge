"""JSON API for the desktop app. Thin wrappers over the same core modules the CLI uses."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from assetforge import art
from assetforge.app.server import FileResponse, Request, Router
from assetforge.audio.effects import EFFECTS
from assetforge.audio.generate import AudioRequest, generate_audio
from assetforge.audio.recipes import PRESETS, RECIPES
from assetforge.config import resolve_home
from assetforge.core import budget, jobs, lineage, pricing, previews, queue, reporting, review, styles, visual
from assetforge.core import collections as coll
from assetforge.core.browse import SORTS, BrowseFilters, browse, facets
from assetforge.core.catalog import Library
from assetforge.core.models import IMAGE_TYPES as VISUAL_TYPES
from assetforge.core.models import AssetPatch, AssetType, FileKind, JobStatus, Status, Variant
from assetforge.errors import AssetForgeError, InvalidInputError, NotFoundError
from assetforge.image.engine import result_from_job as image_result_from_job
from assetforge.image.engine import settings_for
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.prompts import TRANSPARENT_BY_DEFAULT
from assetforge.image.providers import FACTORIES, get_provider
from assetforge.image.providers.base import ASPECTS

router = Router()
IMAGE_TYPES = [t.value for t in AssetType if t in VISUAL_TYPES]
THUMB_SIZES = (128, 256, 512)
IMMUTABLE = "private, max-age=31536000, immutable"
JOB_START_WAIT = 15.0


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json")


def _job_brief(job: Any) -> dict[str, Any]:
    request = job.request.get("request", job.request) if isinstance(job.request, dict) else {}
    return {
        "id": job.id, "kind": job.kind, "status": job.status.value, "provider": job.provider, "model": job.model,
        "asset_id": job.asset_id, "cost_usd": job.cost_usd, "attempts": job.attempts, "created_at": job.created_at,
        "updated_at": job.updated_at, "error": job.error,
        "summary": request.get("prompt") or request.get("description") or request.get("recipe"),
    }  # fmt: skip


# ---------------------------------------------------------------------- overview


@router.route("GET", "/api/overview")
def overview(req: Request, lib: Library) -> dict[str, Any]:
    info = lib.info()
    tz = req.arg("tz")
    try:
        report = reporting.build(lib, reporting.resolve_range("last-30-days", tz_name=tz))
    except InvalidInputError:
        report = reporting.build(lib, reporting.resolve_range("last-30-days"))
    origins = {r[0]: r[1] for r in lib.conn.execute("SELECT origin, count(*) FROM assets GROUP BY origin")}
    kinds = {r[0] or "none": r[1] for r in lib.conn.execute(
        "SELECT pf.kind, count(*) FROM assets a LEFT JOIN asset_variants pv ON pv.id = a.primary_variant_id "
        "LEFT JOIN asset_files pf ON pf.sha256 = pv.file_sha256 GROUP BY pf.kind")}  # fmt: skip
    job_counts = {r[0]: r[1] for r in lib.conn.execute("SELECT status, count(*) FROM generation_jobs GROUP BY status")}
    total, newest = browse(lib, BrowseFilters(), sort="newest", limit=12)
    price = pricing.table(lib)
    default_home = resolve_home(None)  # where agents write unless they pass --home or set ASSETFORGE_HOME
    return {
        "library": {"home": info["home"], "schema_version": info["schema_version"], "stored_bytes": info["stored_bytes"],
                    "default_home": str(default_home), "is_default": Path(info["home"]).resolve() == default_home},
        "counts": info["counts"],
        "by_type": info["by_type"],
        "by_status": info["by_status"],
        "by_origin": origins,
        "by_kind": kinds,
        "jobs": {"by_status": job_counts, "pending": queue.pending(lib, ("request", "image", "image_edit", "audio",
                                                                         "audio_vary"))},  # fmt: skip
        "recent_jobs": [_job_brief(j) for j in jobs.list_jobs(lib, limit=8)],
        "newest": [_dump(a) for a in newest],
        "spend": {"totals": report["totals"], "series": report["series"], "range": report["range"],
                  "by_provider": report["by_provider"]},  # fmt: skip
        "budget": budget.summary(lib),
        "pricing": {"fetched_at": price["fetched_at"], "age_days": price["age_days"]},
    }


# ---------------------------------------------------------------------- assets


def _filters(req: Request) -> BrowseFilters:
    return BrowseFilters(
        q=req.arg("q", "") or "", types=req.args("type"), statuses=req.args("status"), kind=req.arg("kind"),
        origins=req.args("origin"), project=req.arg("project"), style=req.arg("style"),
        collection=req.arg("collection"), tag=req.arg("tag"), category=req.arg("category"), genre=req.arg("genre"),
        theme=req.arg("theme"), color=req.arg("color"), orientation=req.arg("orientation"), tone=req.arg("tone"),
        transparent={"true": True, "false": False}.get(req.arg("transparent") or ""), similar=req.arg("similar"),
    )  # fmt: skip


@router.route("GET", "/api/assets")
def list_assets(req: Request, lib: Library) -> dict[str, Any]:
    offset, limit = req.int_arg("offset", 0), req.int_arg("limit", 60)
    total, items = browse(lib, _filters(req), sort=req.arg("sort", "newest") or "newest", offset=offset, limit=limit)
    return {"total": total, "offset": offset, "limit": limit, "items": [_dump(a) for a in items],
            "sorts": list(SORTS)}  # fmt: skip


@router.route("GET", "/api/facets")
def get_facets(req: Request, lib: Library) -> dict[str, Any]:
    return {"facets": facets(lib)}


@router.route("GET", r"/api/assets/(?P<ref>[^/]+)")
def asset_detail(req: Request, lib: Library) -> dict[str, Any]:
    asset = lib.get_asset(req.match["ref"])
    generator = asset.metadata.get("generator") or {}
    job = None
    usage = None
    if job_id := generator.get("job_id"):
        try:
            job = _job_brief(jobs.get_job(lib, job_id))
        except NotFoundError:
            job = None
        row = lib.conn.execute("SELECT * FROM usage_records WHERE job_id = ?", (job_id,)).fetchone()
        if row:
            usage = {k: row[k] for k in ("provider", "model", "images", "input_text_tokens", "input_image_tokens",
                                         "output_image_tokens", "output_text_tokens", "total_tokens", "cost_usd",
                                         "estimated_cost_usd", "price_source")}  # fmt: skip
    animation = asset.metadata.get("animation")
    frames = []
    if animation:
        summaries = {s.id: s for s in lib.summaries([f["asset_id"] for f in animation.get("frames", [])])}
        frames = [{"name": f["name"], "asset_id": f["asset_id"], "cell": f.get("cell"),
                   "variant_id": summaries[f["asset_id"]].primary_variant.id}
                  for f in animation.get("frames", []) if f["asset_id"] in summaries
                  and summaries[f["asset_id"]].primary_variant]  # fmt: skip
    return {"asset": _dump(asset), "lineage": lineage.lineage(lib, asset.id), "job": job, "usage": usage,
            "frames": frames, "can_regenerate": generator.get("engine") == "ai-image" and bool(generator.get("job_id")),
            "siblings": review.siblings(lib, asset.id)}  # fmt: skip


# ---------------------------------------------------------------------- asset actions


@router.route("POST", r"/api/assets/(?P<ref>[^/]+)/update")
def update_asset(req: Request, lib: Library) -> dict[str, Any]:
    body = dict(req.body or {})
    tags = body.pop("tags", None)
    patch = AssetPatch(**body)
    if tags is not None:
        current = set(lib.get_asset(req.match["ref"]).tags)
        wanted = {t.strip() for t in tags if t.strip()}
        patch = patch.model_copy(update={"add_tags": sorted(wanted - current), "remove_tags": sorted(current - wanted)})
    return {"asset": _dump(lib.update_asset(req.match["ref"], patch))}


@router.route("POST", r"/api/assets/(?P<ref>[^/]+)/review")
def review_asset(req: Request, lib: Library) -> dict[str, Any]:
    body = req.body or {}
    action = body.get("action")
    note = body.get("note") or None
    if action == "approve":
        return {"review": review.approve(lib, [req.match["ref"]], note=note, force=bool(body.get("force")))}
    if action == "pick":
        return {"review": review.pick(lib, req.match["ref"], note=note, force=bool(body.get("force")))}
    if action == "restore":
        restored = review.restore(lib, [req.match["ref"]], note=note)["restored"]
        return {"review": {"status": next(iter(restored.values()), "unchanged"), "asset_ids": list(restored)}}
    statuses = {"reject": Status.REJECTED, "archive": Status.ARCHIVED, "candidate": Status.CANDIDATE}
    if action not in statuses:
        raise InvalidInputError("action must be approve, pick, reject, archive or candidate")
    return {"review": {"status": statuses[action].value, "asset_ids": lib.set_status([req.match["ref"]],
                                                                                     statuses[action], note=note)}}  # fmt: skip


@router.route("POST", "/api/review/bulk")
def bulk_review(req: Request, lib: Library) -> dict[str, Any]:
    """One action on many assets (from the browser's multi-select): approve, reject, archive, restore, candidate."""
    body = req.body or {}
    ids = [str(i) for i in body.get("ids") or []]
    action = body.get("action")
    if not ids:
        raise InvalidInputError("Select at least one asset")
    note = body.get("note") or None
    if action == "approve":
        return {"review": review.approve(lib, ids, note=note, force=bool(body.get("force")))}
    if action == "restore":
        return {"review": review.restore(lib, ids, note=note)}
    statuses = {"reject": Status.REJECTED, "archive": Status.ARCHIVED, "candidate": Status.CANDIDATE}
    if action not in statuses:
        raise InvalidInputError("action must be approve, reject, archive, restore or candidate")
    return {"review": {"status": statuses[action].value, "asset_ids": lib.set_status(ids, statuses[action], note=note)}}


@router.route("GET", r"/api/assets/(?P<ref>[^/]+)/similar")
def similar_assets(req: Request, lib: Library) -> dict[str, Any]:
    ranked = visual.similar(lib, req.match["ref"], limit=req.int_arg("limit", 8),
                            where="a.status IN ('approved', 'candidate')")  # fmt: skip
    summaries = {s.id: s for s in lib.summaries([i for i, _, _ in ranked])}
    return {"items": [{**_dump(summaries[i]), "score": score, "distance": d} for i, score, d in ranked if i in summaries]}


@router.route("POST", r"/api/assets/(?P<ref>[^/]+)/regenerate")
def regenerate_asset(req: Request, lib: Library) -> dict[str, Any]:
    from assetforge.regenerate import regenerate

    body = req.body or {}
    return {"regenerate": regenerate(lib, req.match["ref"], feedback=str(body.get("feedback") or ""),
                                     n=int(body.get("n") or 1), art=body.get("art") or None,
                                     style=body.get("style") or None, max_cost=body.get("max_cost"),
                                     reject_previous=bool(body.get("reject_previous")),
                                     dry_run=bool(body.get("dry_run")), settings=settings_for(lib))}  # fmt: skip


@router.route("POST", r"/api/assets/(?P<ref>[^/]+)/slice")
def slice_sheet(req: Request, lib: Library) -> dict[str, Any]:
    """Slice a sheet (or, with `dry_run`, return the grid, subject boxes, names and issues it would use)."""
    from assetforge.image.sprites import parse_animation, plan_slice, slice_asset

    body = req.body or {}
    animations = dict(parse_animation(a) for a in body.get("animations") or [] if str(a).strip())
    names = [n.strip() for n in body.get("names") or [] if n.strip()] or None
    columns = int(body["columns"]) if body.get("columns") else None
    rows = int(body["rows"]) if body.get("rows") else None
    common = {"columns": columns, "rows": rows, "names": names, "animations": animations,
              "cell": int(body["cell"]) if body.get("cell") else None, "anchor": body.get("anchor") or "bottom",
              "margin": int(body.get("margin") or 4), "base": body.get("base") or None, "fit": body.get("fit") or "auto"}  # fmt: skip
    if body.get("dry_run"):
        return {"plan": _dump(plan_slice(lib, req.match["ref"], **common))}
    outcome = slice_asset(lib, req.match["ref"], **common, allow_crowded=bool(body.get("allow_crowded")),
                          replace_previous=body.get("replace_previous", True) is not False)  # fmt: skip
    return {"slice": _dump(outcome)}


# ---------------------------------------------------------------------- art direction


def _profile_dict(p: art.ArtProfile) -> dict[str, Any]:
    resolved = art.resolve_for_display(p)
    return {"project": p.project, "art": p.direction.model_dump(mode="json", exclude_defaults=True),
            "reference_asset_ids": p.reference_asset_ids, "updated_at": p.updated_at, "yaml": art.profile_yaml(p),
            **resolved}  # fmt: skip


@router.route("GET", "/api/art")
def art_overview(req: Request, lib: Library) -> dict[str, Any]:
    presets = [p.model_dump(mode="json", exclude_defaults=True) | {"name": p.name, "source": p.source,
               "prompt_text": art.render(p.direction(), preset=p)} for p in art.presets(lib).values()]  # fmt: skip
    projects = sorted({r[0] for r in lib.conn.execute("SELECT DISTINCT project FROM assets WHERE project IS NOT NULL")})
    profiles = [_profile_dict(p) | {"prompt_text": art.resolve(lib, project=p.project, style=None, art=None).text(),
                                     "layers": art.resolve(lib, project=p.project, style=None, art=None).layers}
                for p in art.list_profiles(lib)]  # fmt: skip
    return {"presets": presets, "profiles": profiles, "projects": projects,
            "fields": {k: f.description or "" for k, f in art.ArtDirection.model_fields.items()},
            "custom_presets_dir": str(lib.paths.home / "presets")}  # fmt: skip


@router.route("POST", r"/api/art/profiles/(?P<project>[^/]+)")
def save_profile(req: Request, lib: Library) -> dict[str, Any]:
    body = req.body or {}
    if body.get("yaml"):
        file_project, direction = art.parse_profile_yaml(str(body["yaml"]))
        project = req.match["project"] if req.match["project"] != "_" else file_project
    else:
        project, direction = req.match["project"], art.ArtDirection(**(body.get("art") or {}))
    if not project:
        raise InvalidInputError("Name the game (or add `game:` to the YAML)")
    profile = art.set_profile(lib, project, direction, replace=bool(body.get("replace", True)))
    return {"profile": _profile_dict(profile)}


@router.route("POST", r"/api/art/profiles/(?P<project>[^/]+)/delete")
def delete_profile(req: Request, lib: Library) -> dict[str, Any]:
    art.delete_profile(lib, req.match["project"])
    return {"deleted": req.match["project"]}


@router.route("POST", "/api/art/preview")
def art_preview(req: Request, lib: Library) -> dict[str, Any]:
    body = {**(req.body or {}), "dry_run": True}
    body.setdefault("prompt", "the subject")
    result = generate_image(lib, ImageRequest(**body), settings_for(lib))
    return {"preview": {"prompt": result.prompt, "art": result.art, "layers": result.art_layers,
                        "provider": result.provider, "model": result.model,
                        "estimated_cost_usd": result.estimated_cost_usd}}  # fmt: skip


def _variant(lib: Library, variant_id: str) -> Variant:
    asset = lib.get_asset(variant_id)
    variant = next((v for v in asset.variants if v.id == variant_id), None)
    if variant is None:
        raise NotFoundError(f"No variant {variant_id}")
    return variant


@router.route("GET", r"/media/variant/(?P<variant>var_\d+)")
def media_variant(req: Request, lib: Library) -> FileResponse:
    variant = _variant(lib, req.match["variant"])
    path = Path(variant.file.path)
    if not path.is_file():
        raise NotFoundError("The file is missing from the library; run `assetforge verify`")
    return FileResponse(path, variant.file.mime_type, IMMUTABLE)


@router.route("GET", r"/media/thumb/(?P<variant>var_\d+)")
def media_thumb(req: Request, lib: Library) -> FileResponse:
    size = req.int_arg("size", 256)
    if size not in THUMB_SIZES:
        raise InvalidInputError(f"size must be one of {THUMB_SIZES}")
    return FileResponse(previews.thumbnail(lib, _variant(lib, req.match["variant"]), size), "image/png", IMMUTABLE)


@router.route("POST", "/api/reveal")
def reveal(req: Request, lib: Library) -> dict[str, Any]:
    variant_id = str((req.body or {}).get("variant_id", ""))
    if not variant_id.startswith("var_"):
        raise InvalidInputError("variant_id is required")
    path = Path(_variant(lib, variant_id).file.path)
    if not path.is_file():
        raise NotFoundError("The file is missing from the library")
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False, timeout=10)
    return {"revealed": str(path)}


@router.route("GET", "/api/collections")
def list_collections(req: Request, lib: Library) -> dict[str, Any]:
    return {"collections": [_dump(c) for c in coll.list_collections(lib)]}


# ---------------------------------------------------------------------- jobs + usage


@router.route("GET", "/api/jobs")
def list_jobs(req: Request, lib: Library) -> dict[str, Any]:
    status = req.arg("status")
    items = jobs.list_jobs(lib, status=JobStatus(status) if status else None, kind=req.arg("kind"),
                           limit=req.int_arg("limit", 100))  # fmt: skip
    return {"jobs": [_job_brief(j) for j in items]}


@router.route("GET", r"/api/jobs/(?P<job>job_\d+)")
def job_detail(req: Request, lib: Library) -> dict[str, Any]:
    job = jobs.get_job(lib, req.match["job"])
    result = None
    if job.status == JobStatus.SUCCEEDED and job.kind in ("image", "image_edit"):
        result = _dump(image_result_from_job(lib, job))
    elif job.kind == "request":
        from assetforge.request import result_from_job

        result = _dump(result_from_job(lib, job))
    return {"job": {**_job_brief(job), "request": job.request, "result": job.result}, "result": result}


@router.route("GET", "/api/report")
def report(req: Request, lib: Library) -> dict[str, Any]:
    rng = reporting.resolve_range(req.arg("range"), req.arg("from"), req.arg("to"), tz_name=req.arg("tz"))
    data = reporting.build(lib, rng, interval=req.arg("interval", "day") or "day", provider=req.arg("provider"),
                           model=req.arg("model"), project=req.arg("project"))  # fmt: skip
    return {"report": data}


# ---------------------------------------------------------------------- generation


@router.route("GET", "/api/audio/recipes")
def audio_recipes(req: Request, lib: Library) -> dict[str, Any]:
    return {
        "recipes": [r.to_dict() for r in sorted(RECIPES.values(), key=lambda r: r.name)],
        "presets": [{"name": n, "description": p.get("description", "")} for n, p in sorted(PRESETS.items())],
        "effects": [e.to_dict() for e in EFFECTS.values()],
    }


@router.route("GET", "/api/image/options")
def image_options(req: Request, lib: Library) -> dict[str, Any]:
    settings = settings_for(lib)
    names = [n for n in FACTORIES if n != "fake" or settings.fake_provider_enabled]
    providers = [get_provider(n, settings).describe() for n in names]
    return {
        "providers": providers,
        "styles": [*({"name": p.name, "description": p.summary} for p in art.presets(lib).values()),
                   *(_dump(s) for s in styles.list_styles(lib))],
        "types": IMAGE_TYPES,
        "transparent_by_default": sorted(t.value for t in TRANSPARENT_BY_DEFAULT),
        "aspects": list(ASPECTS),
        "qualities": ["auto", "low", "medium", "high", "max"],
        "budget": budget.summary(lib),
    }


@router.route("POST", "/api/generate/audio")
def generate_audio_route(req: Request, lib: Library) -> dict[str, Any]:
    result = generate_audio(lib, AudioRequest(**(req.body or {})))
    return {"result": _dump(result)}


@router.route("POST", "/api/generate/image/estimate")
def estimate_image(req: Request, lib: Library) -> dict[str, Any]:
    body = {**(req.body or {}), "dry_run": True}
    return {"estimate": _dump(generate_image(lib, ImageRequest(**body), settings_for(lib)))}


@router.route("POST", "/api/generate/image")
def generate_image_route(req: Request, lib: Library) -> dict[str, Any]:
    """Start generation in the background; return the job id as soon as the job exists (or the result if reused)."""
    image_request = ImageRequest(**{**(req.body or {}), "dry_run": False})
    home = req.server.home
    started = threading.Event()
    holder: dict[str, Any] = {}

    def on_job(job_id: str) -> None:
        holder["job_id"] = job_id
        started.set()

    def work() -> None:
        with Library(home) as own:
            try:
                holder["result"] = _dump(generate_image(own, image_request, settings_for(own), on_job=on_job))
            except AssetForgeError as exc:
                holder["error"] = exc
            except Exception as exc:  # recorded on the job by the engine; surface it here too
                holder["error"] = exc
            finally:
                started.set()

    threading.Thread(target=work, name="image-generation", daemon=True).start()
    started.wait(JOB_START_WAIT)
    if "job_id" in holder:
        return {"state": "running", "job_id": holder["job_id"]}
    if "result" in holder:
        return {"state": "done", "result": holder["result"]}
    if "error" in holder:
        raise holder["error"]
    return {"state": "starting"}


@router.route("GET", "/api/kinds")
def kinds(req: Request, lib: Library) -> dict[str, Any]:
    return {"types": [t.value for t in AssetType], "kinds": [k.value for k in FileKind]}
