"""JSON API for the desktop app. Thin wrappers over the same core modules the CLI uses."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from assetforge.app.server import FileResponse, Request, Router
from assetforge.audio.effects import EFFECTS
from assetforge.audio.generate import AudioRequest, generate_audio
from assetforge.audio.recipes import PRESETS, RECIPES
from assetforge.core import budget, jobs, lineage, pricing, previews, queue, reporting, styles
from assetforge.core import collections as coll
from assetforge.core.browse import SORTS, BrowseFilters, browse, facets
from assetforge.core.catalog import Library
from assetforge.core.models import AssetType, FileKind, JobStatus, Variant
from assetforge.errors import AssetForgeError, InvalidInputError, NotFoundError
from assetforge.image.engine import result_from_job as image_result_from_job
from assetforge.image.engine import settings_for
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.prompts import TRANSPARENT_BY_DEFAULT
from assetforge.image.providers import FACTORIES, get_provider
from assetforge.image.providers.base import ASPECTS

router = Router()
IMAGE_TYPES = [t.value for t in (AssetType.CHARACTER, AssetType.SPRITE, AssetType.BACKGROUND, AssetType.ICON,
                                 AssetType.TILE, AssetType.UI, AssetType.EFFECT, AssetType.IMAGE)]  # fmt: skip
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
    return {
        "library": {"home": info["home"], "schema_version": info["schema_version"], "stored_bytes": info["stored_bytes"]},
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
        collection=req.arg("collection"), tag=req.arg("tag"),
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
    return {"asset": _dump(asset), "lineage": lineage.lineage(lib, asset.id), "job": job, "usage": usage}


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
        "styles": [_dump(s) for s in styles.list_styles(lib)],
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
