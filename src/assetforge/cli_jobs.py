"""`assetforge jobs …`: inspect generation jobs and resume interrupted image jobs."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from assetforge.cli_common import JsonOpt, dump, session
from assetforge.cli_image import render_image_result
from assetforge.core import jobs, queue
from assetforge.core.models import JobStatus
from assetforge.image.resume import resume_image_job

jobs_app = typer.Typer(help="Inspect generation jobs; resume interrupted image jobs.", no_args_is_help=True)


@jobs_app.command("list")
def jobs_list(
    ctx: typer.Context,
    status: Annotated[JobStatus | None, typer.Option(help="Filter by status.")] = None,
    kind: Annotated[str | None, typer.Option(help="Filter by kind, e.g. audio.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum jobs.")] = 50,
    json_: JsonOpt = False,
) -> None:
    """List recent generation jobs."""
    with session(ctx, json_) as s:
        items = jobs.list_jobs(s.lib, status=status, kind=kind, limit=limit)

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Job", "Kind", "Status", "Asset", "Model", "Created"):
                table.add_column(column)
            for j in items:
                table.add_row(j.id, j.kind, j.status.value, j.asset_id or "-", j.model or "-", j.created_at)
            c.print(table)

        s.out.emit({"count": len(items), "jobs": dump(items)}, human)


@jobs_app.command("show")
def jobs_show(ctx: typer.Context, job_id: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Show one job's request, result or error."""
    with session(ctx, json_) as s:
        job = jobs.get_job(s.lib, job_id)
        s.out.emit({"job": dump(job)}, lambda c: c.print_json(json.dumps(dump(job))))


@jobs_app.command("resume")
def jobs_resume(ctx: typer.Context, job_id: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Finish an interrupted image job: catalog saved outputs, or re-run the stored request."""
    with session(ctx, json_) as s:
        result = resume_image_job(s.lib, job_id)
        s.out.emit(result.model_dump(mode="json"), lambda c: render_image_result(c, result))


@jobs_app.command("wait")
def jobs_wait(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument()],
    timeout: Annotated[float, typer.Option(help="Seconds to wait.")] = 900.0,
    json_: JsonOpt = False,
) -> None:
    """Block until a job finishes (or the timeout passes) and print its final state."""
    with session(ctx, json_) as s:
        job = queue.wait(s.lib, job_id, timeout=timeout)
        payload: dict = {"job": dump(job)}
        if job.kind == "request":
            from assetforge.request import result_from_job

            payload["result"] = result_from_job(s.lib, job).model_dump(mode="json")
        s.out.emit(payload, lambda c: c.print(f"{job.id}: {job.status.value}"))


@jobs_app.command("cancel")
def jobs_cancel(ctx: typer.Context, job_id: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Cancel a queued job."""
    with session(ctx, json_) as s:
        job = queue.cancel(s.lib, job_id)
        s.out.emit({"job": dump(job)}, lambda c: c.print(f"{job.id}: {job.status.value}"))


@jobs_app.command("retry")
def jobs_retry(ctx: typer.Context, job_id: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Put a failed or cancelled request job back in the queue."""
    with session(ctx, json_) as s:
        job = queue.retry(s.lib, job_id)
        s.out.emit({"job": dump(job)}, lambda c: c.print(f"{job.id}: {job.status.value}"))


@jobs_app.command("recover")
def jobs_recover(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Requeue jobs whose worker died (expired lease) and mark orphaned generation jobs as failed."""
    with session(ctx, json_) as s:
        report = queue.recover(s.lib)
        s.out.emit({"recovered": report}, lambda c: c.print(
            f"requeued {len(report['requeued'])}, failed {len(report['failed'])}, abandoned {len(report['abandoned'])}"))
