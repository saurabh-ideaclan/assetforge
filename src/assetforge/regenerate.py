"""`assetforge regenerate`: the review loop. The agent looks at an AI image (`get`/`preview` give it the
paths), decides it misses the brief, and asks for another version with concrete feedback.

The original request is re-run with the feedback added and any art-direction overrides merged in.
New candidates are linked `variant_of` the original, and attempts are capped per original asset by
`generation.max_regenerations` (default 3), on top of the usual budget and `--max-cost` limits, so a
loop can't spend without bound.
"""

from __future__ import annotations

import re
from typing import Any

from assetforge.config import Settings
from assetforge.core import jobs, settings_store
from assetforge.core.catalog import Library
from assetforge.core.models import LineageRelation, Status
from assetforge.errors import InvalidInputError, RetryLimitError
from assetforge.image.engine import ENGINE


def attempts_so_far(lib: Library, root: str) -> int:
    row = lib.conn.execute(
        "SELECT max(json_extract(metadata, '$.regeneration.attempt')) FROM assets "
        "WHERE json_extract(metadata, '$.regeneration.of') = ?",
        (root,),
    ).fetchone()
    return int(row[0] or 0)


def regenerate(lib: Library, ref: str, *, feedback: str, n: int = 1, art: dict[str, Any] | None = None,
               style: str | None = None, max_cost: float | None = None, reject_previous: bool = False,
               dry_run: bool = False, settings: Settings | None = None) -> dict[str, Any]:  # fmt: skip
    from assetforge.image.edit import EditRequest, edit_image
    from assetforge.image.generate import ImageRequest, generate_image

    feedback = feedback.strip()
    if len(feedback) < 3:
        raise InvalidInputError("Say what to change: --feedback 'less cartoonish, darker lighting, no outline'")
    asset = lib.get_asset(ref)
    generator = asset.metadata.get("generator") or {}
    if generator.get("engine") != ENGINE or not generator.get("job_id"):
        raise InvalidInputError(f"{asset.id} was not made by AI image generation",
                                hint="Sounds: `audio vary`; your own files: edit and `add` again")  # fmt: skip
    root = (asset.metadata.get("regeneration") or {}).get("of") or asset.id
    done = attempts_so_far(lib, root)
    limit = settings_store.get(lib, "generation.max_regenerations")
    if done >= limit:
        raise RetryLimitError(
            f"{root} has been regenerated {done} time(s), the limit is {limit}",
            hint="Rethink the request (different style, method or reference), or raise the cap with "
                 "`assetforge config set generation.max_regenerations N`",
            asset_id=root, attempts=done, limit=limit,
        )  # fmt: skip
    attempt = done + 1
    job = jobs.get_job(lib, generator["job_id"])
    data = {k: v for k, v in job.request.get("request", {}).items() if k not in ("key", "dry_run")}
    original_prompt = data.get("prompt", "")
    data |= {"prompt": f"{original_prompt.rstrip().rstrip('.')}. Changes after review: {feedback.rstrip('.')}.",
             "n": n, "new_variant": True, "name": f"{re.sub(r'_r[0-9]+$', '', asset.name)}_r{attempt}", "status": Status.CANDIDATE,
             "dry_run": dry_run}  # fmt: skip
    if max_cost is not None:
        data["max_cost"] = max_cost
    if generator.get("operation") == "edit":
        data.pop("name", None)
        result = edit_image(lib, EditRequest(**{k: v for k, v in data.items() if k in EditRequest.model_fields}), settings)
    else:
        if style:
            data["style"] = style
        if art:
            data["art"] = {**(data.get("art") or {}), **art}
        result = generate_image(lib, ImageRequest(**data), settings)
    out: dict[str, Any] = {"asset_id": asset.id, "root": root, "attempt": attempt, "limit": limit,
                           "remaining": limit - attempt, "feedback": feedback}  # fmt: skip
    if dry_run:
        return out | {"dry_run": True, "prompt": result.prompt, "estimated_cost_usd": result.estimated_cost_usd}
    for candidate in result.assets:
        lib.merge_metadata(candidate.asset_id, {"regeneration": {"of": root, "attempt": attempt, "previous": asset.id,
                                                                 "feedback": feedback}})  # fmt: skip
        lib.add_lineage(candidate.asset_id, root, LineageRelation.VARIANT_OF)
    review = asset.metadata.get("review") or {}
    lib.merge_metadata(asset.id, {"review": {**review, "feedback": [*review.get("feedback", []), feedback],
                                             "regenerated_as": [*review.get("regenerated_as", []),
                                                                *(c.asset_id for c in result.assets)]}},
                       event="feedback")  # fmt: skip
    if reject_previous:
        lib.set_status([asset.id], Status.REJECTED, note=feedback)
    return out | {"result": result.model_dump(mode="json")}
