"""Resume an interrupted or failed image job.

If the provider's outputs were already saved (the call was paid for), only the cataloging step
re-runs. Otherwise the stored request is replayed under the same job ID.
"""

from __future__ import annotations

from assetforge.config import Settings
from assetforge.core import jobs
from assetforge.core.catalog import Library
from assetforge.core.models import JobStatus
from assetforge.errors import InvalidInputError
from assetforge.image.engine import ImageResult, close_job, execute, job_dir, result_from_job, settings_for

RESUMABLE_KINDS = ("image", "image_edit")


def resume_image_job(lib: Library, job_id: str, settings: Settings | None = None) -> ImageResult:
    job = jobs.get_job(lib, job_id)
    if job.kind not in RESUMABLE_KINDS:
        raise InvalidInputError(f"{job_id} is a {job.kind} job; only image jobs can be resumed", kind=job.kind)
    if job.status == JobStatus.SUCCEEDED:
        return result_from_job(lib, job)
    record = job.request
    jobs.update_job(lib, job_id, status="running", attempts=job.attempts + 1, error=None)
    if (job_dir(lib, job_id) / "response.json").is_file():
        return close_job(lib, job_id, record)

    settings = settings or settings_for(lib)
    if record["operation"] == "edit":
        from assetforge.image.edit import EditRequest, engine_input as edit_input

        inp, _ = edit_input(lib, EditRequest(**record["request"]))
    else:
        from assetforge.image.generate import ImageRequest, engine_input as generate_input

        inp, _ = generate_input(lib, ImageRequest(**record["request"]))
    return execute(lib, job_id, inp, record, settings)
