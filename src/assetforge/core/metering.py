"""Token usage metering and cost calculation for paid provider calls.

Every successful OpenAI/Gemini call is recorded in `usage_records`: input text/image tokens,
output image/text tokens, image count, the pre-call estimate, and the actual cost computed from
current prices (`core/pricing.py`). Pre-call estimates come from your own history first (median
cost per image for the same model/quality/size), then from expected token counts × current
rates, so budgets reserve realistic amounts.
"""

from __future__ import annotations

import json
import statistics
from typing import TYPE_CHECKING, Any

from assetforge.core import db, pricing
from assetforge.core.util import dumps, utcnow

if TYPE_CHECKING:
    from assetforge.core.catalog import Library
    from assetforge.image.providers.base import ImageProvider, ProviderCall

TOKEN_FIELDS = ("input_text_tokens", "input_image_tokens", "output_image_tokens", "output_text_tokens")
INPUT_IMAGE_TOKENS = 560  # typical tokens for one reference image at ~1 MP
HISTORY_WINDOW = 20


def normalize_usage(provider: str, raw: dict[str, Any], images: int) -> dict[str, int]:
    """Map provider-specific usage payloads onto AssetForge's four token counters."""
    out = dict.fromkeys(TOKEN_FIELDS, 0)
    if not raw:
        return out
    if provider == "openai":
        inp = raw.get("input_tokens_details") or {}
        outp = raw.get("output_tokens_details") or {}
        out["input_text_tokens"] = int(inp.get("text_tokens", raw.get("input_tokens", 0)) or 0)
        out["input_image_tokens"] = int(inp.get("image_tokens", 0) or 0)
        if outp:
            out["output_image_tokens"] = int(outp.get("image_tokens", 0) or 0)
            out["output_text_tokens"] = int(outp.get("text_tokens", 0) or 0)
        else:
            out["output_image_tokens"] = int(raw.get("output_tokens", 0) or 0)
    elif provider == "gemini":
        prompt = int(raw.get("prompt_token_count", 0) or 0)
        candidates = int(raw.get("candidates_token_count", 0) or 0)
        thoughts = int(raw.get("thoughts_token_count", 0) or 0)
        if "prompt_image_tokens" in raw or "prompt_text_tokens" in raw:
            out["input_image_tokens"] = int(raw.get("prompt_image_tokens", 0))
            out["input_text_tokens"] = int(raw.get("prompt_text_tokens", 0))
        else:
            out["input_text_tokens"] = prompt
        if "output_image_tokens" in raw or "output_text_tokens" in raw:
            out["output_image_tokens"] = int(raw.get("output_image_tokens", 0))
            out["output_text_tokens"] = int(raw.get("output_text_tokens", 0)) + thoughts
        else:  # image-only responses: candidate tokens are the image
            out["output_image_tokens"] = candidates
            out["output_text_tokens"] = thoughts
    else:
        for field in TOKEN_FIELDS:
            out[field] = int(raw.get(field, 0) or 0)
    return out


def cost_of(tokens: dict[str, int], rates: dict[str, float], *, images: int, input_images: int = 0) -> float | None:
    has_tokens = any(tokens.values())
    if has_tokens and any(f in rates for f in ("input_text", "output_image", "output_text")):
        total = (
            tokens["input_text_tokens"] * rates.get("input_text", 0.0)
            + tokens["input_image_tokens"] * rates.get("input_image", rates.get("input_text", 0.0))
            + tokens["output_image_tokens"] * rates.get("output_image", 0.0)
            + tokens["output_text_tokens"] * rates.get("output_text", 0.0)
        ) / 1_000_000
        return round(total, 6)
    if "per_image" in rates:  # no token counts: fall back to per-image prices
        return round(rates["per_image"] * images + rates.get("per_input_image", 0.0) * input_images, 6)
    return None


def record_call(
    lib: Library,
    *,
    job_id: str | None,
    operation: str,
    provider: str,
    model: str,
    raw_usage: dict[str, Any],
    images: int,
    input_images: int = 0,
    quality: str | None = None,
    size: str | None = None,
    estimated_cost: float | None = None,
    fallback_cost: float | None = None,
    project: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Store one call's usage and return the record (with its computed cost)."""
    tokens = normalize_usage(provider, raw_usage, images)
    rates, source = pricing.rates_for(lib, provider, model)
    cost = cost_of(tokens, rates, images=images, input_images=input_images)
    if cost is None:
        cost, source = fallback_cost, "adapter-estimate" if fallback_cost is not None else source
    record = {
        "created_at": created_at or utcnow(),
        "provider": provider,
        "model": model,
        "operation": operation,
        "job_id": job_id,
        "project": project,
        "quality": quality,
        "size": size,
        "images": images,
        **tokens,
        "total_tokens": sum(tokens.values()),
        "cost_usd": cost,
        "estimated_cost_usd": estimated_cost,
        "price_source": source,
        "rates": dumps(rates),
        "raw_usage": dumps(raw_usage or {}),
    }
    with db.transaction(lib.conn):
        if job_id and lib.conn.execute("SELECT 1 FROM usage_records WHERE job_id = ?", (job_id,)).fetchone():
            lib.conn.execute("DELETE FROM usage_records WHERE job_id = ?", (job_id,))  # re-recorded on resume
        columns = ", ".join(record)
        lib.conn.execute(f"INSERT INTO usage_records ({columns}) VALUES ({', '.join('?' * len(record))})",
                         list(record.values()))  # fmt: skip
    return record


def estimate_call(lib: Library, provider: ImageProvider, call: ProviderCall) -> tuple[float, str]:
    """USD estimate for a call before making it, plus how it was estimated."""
    size = provider.size_key(call)
    quality = call.quality
    rows = lib.conn.execute(
        "SELECT cost_usd, images FROM usage_records WHERE provider = ? AND model = ? AND ifnull(quality, '') = ? "
        "AND ifnull(size, '') = ? AND images > 0 AND cost_usd IS NOT NULL ORDER BY id DESC LIMIT ?",
        (provider.name, call.model, quality or "", size or "", HISTORY_WINDOW),
    ).fetchall()
    rates, _ = pricing.rates_for(lib, provider.name, call.model)
    input_cost = len(call.images) * (
        rates.get("per_input_image")
        or INPUT_IMAGE_TOKENS * rates.get("input_image", rates.get("input_text", 0.0)) / 1_000_000
    )
    if rows:
        per_image = statistics.median(r["cost_usd"] / r["images"] for r in rows)
        return round(per_image * call.n + input_cost, 4), f"history ({len(rows)} similar calls)"
    expected = provider.expected_output_tokens(call)
    if expected and "output_image" in rates:
        prompt_cost = len(call.prompt) / 4 * rates.get("input_text", 0.0) / 1_000_000
        per_image = expected * rates["output_image"] / 1_000_000
        return round(per_image * call.n + prompt_cost + input_cost, 4), "expected tokens x current prices"
    if "per_image" in rates:
        return round(rates["per_image"] * call.n + input_cost, 4), "per-image price"
    return round(provider.estimate_cost(call), 4), "adapter estimate"


def backfill(lib: Library) -> int:
    """Create usage records for finished image jobs that predate metering (from their saved response.json)."""
    created = 0
    rows = lib.conn.execute(
        "SELECT j.id, j.kind, j.request, j.created_at FROM generation_jobs j WHERE j.kind IN ('image', 'image_edit') "
        "AND j.status = 'succeeded' AND NOT EXISTS (SELECT 1 FROM usage_records u WHERE u.job_id = j.id)"
    ).fetchall()
    for row in rows:
        path = lib.paths.jobs / row["id"] / "response.json"
        if not path.is_file():
            continue
        response = json.loads(path.read_text())
        request = json.loads(row["request"] or "{}")
        provider_request = response.get("provider_request") or {}
        record_call(
            lib, job_id=row["id"], operation="edit" if row["kind"] == "image_edit" else "generate",
            provider=response["provider"], model=response["model"], raw_usage=response.get("usage") or {},
            images=len(response.get("outputs", [])), input_images=provider_request.get("reference_images", 0) or 0,
            quality=(request.get("request") or {}).get("quality"),
            size=provider_request.get("size") or provider_request.get("image_size"),
            fallback_cost=response.get("cost_usd"), project=request.get("project"), created_at=row["created_at"],
        )  # fmt: skip
        created += 1
    return created
