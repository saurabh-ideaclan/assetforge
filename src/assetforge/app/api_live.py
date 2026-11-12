"""Live data for the app: the change feed (SSE + polling fallback), the build id, and the durable tasks
(list, detail, cancel, retry, dismiss). See docs/app/02-architecture.md."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

from assetforge.app.server import Request, Router, StreamResponse
from assetforge.core import tasks
from assetforge.core.catalog import Library
from assetforge.errors import ConflictError, InvalidInputError

PING_SECONDS = 15.0


def _event(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\nid: {data['seq']}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def register_routes(router: Router) -> None:
    @router.route("GET", "/api/version")
    def version(req: Request, lib: Library) -> dict[str, Any]:
        feed = req.server.feed
        return {"build": feed.build, "started_at": feed.started_at, "pid": os.getpid(), "home": str(lib.paths.home)}

    @router.route("GET", "/api/changes")
    def changes(req: Request, lib: Library) -> dict[str, Any]:
        """Polling fallback for /api/events: the current revisions, and what changed since `since` (a seq)."""
        since = req.arg("since")
        try:
            seq = int(since) if since not in (None, "") else None
        except ValueError as exc:
            raise InvalidInputError("since must be a sequence number") from exc
        return req.server.feed.payload(seq)

    @router.route("GET", "/api/events")
    def events(req: Request, lib: Library) -> StreamResponse:
        """Server-sent events: `hello` with every revision, then `change` with what changed; `ping` every 15 s."""
        feed = req.server.feed
        last = req.arg("since") or None
        feed.listening(+1)

        def stream() -> Iterator[bytes]:
            yield b"retry: 2000\n\n"
            snap = feed.current()
            base = feed.since(int(last)) if last and last.isdigit() else None
            first = snap.payload(build=feed.build, started_at=feed.started_at, since=base, full=base is None)
            yield _event("hello", first)
            seq, pinged = snap.seq, time.time()
            while not feed.stopping:
                nxt = feed.wait(seq, timeout=1.0)
                if feed.stopping or nxt is None:
                    break
                if nxt.seq != seq:
                    prev = feed.since(seq)
                    yield _event("change", nxt.payload(build=feed.build, started_at=feed.started_at, since=prev,
                                                       full=prev is None))  # fmt: skip
                    seq, pinged = nxt.seq, time.time()
                elif time.time() - pinged >= PING_SECONDS:
                    yield b"event: ping\ndata: {}\n\n"
                    pinged = time.time()

        return StreamResponse(stream(), on_close=lambda: feed.listening(-1))

    # ------------------------------------------------------------------ tasks

    @router.route("GET", "/api/tasks")
    def task_list(req: Request, lib: Library) -> dict[str, Any]:
        states = req.args("state") or None
        items = tasks.list_tasks(lib, story=req.arg("story"), states=states, kind=req.arg("kind"),
                                 limit=req.int_arg("limit", 60))  # fmt: skip
        return {"tasks": items, "counts": tasks.counts(lib)}

    @router.route("GET", r"/api/tasks/(?P<tid>[0-9a-f]{6,32})")
    def task_detail(req: Request, lib: Library) -> dict[str, Any]:
        return {"task": tasks.get(lib, req.match["tid"])}

    @router.route("POST", r"/api/tasks/(?P<tid>[0-9a-f]{6,32})/cancel")
    def task_cancel(req: Request, lib: Library) -> dict[str, Any]:
        return {"task": tasks.request_cancel(lib, req.match["tid"])}

    @router.route("POST", r"/api/tasks/(?P<tid>[0-9a-f]{6,32})/dismiss")
    def task_dismiss(req: Request, lib: Library) -> dict[str, Any]:
        return {"task": tasks.dismiss(lib, req.match["tid"])}

    @router.route("POST", r"/api/tasks/(?P<tid>[0-9a-f]{6,32})/retry")
    def task_retry(req: Request, lib: Library) -> dict[str, Any]:
        """Start the same request again (app-started story and audio tasks keep their request)."""
        task = tasks.get(lib, req.match["tid"])
        if not task["retryable"]:
            raise ConflictError("This task can't be retried from the app",
                                hint=f"Run it again: {task['command']}" if task.get("command") else None)  # fmt: skip
        body = dict(task["request"] or {})
        if (req.body or {}).get("allow_paid"):
            body["allow_paid"] = True
        if task["kind"].startswith("story.") and task["story"]:
            from assetforge.app.api_story import start_story_task

            return start_story_task(req.server.home, lib, task["story"], body)
        if task["kind"].startswith("audio."):
            from assetforge.app.api_audio import start_production

            return start_production(lib, body)
        raise ConflictError(f"No way to retry a {task['kind']} task from the app")
