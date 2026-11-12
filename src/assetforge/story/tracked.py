"""A story step context that reports to a durable task (core/tasks.py) without touching the steps themselves.

Every model call the steps make is appended to `ctx.calls`; here that list records each call as a finished task
step and checks the task's cancel flag, and `ctx.config` (read before every call) checks it too. So a story task
can be cancelled between model calls: the call in flight finishes and is cached, nothing is half-written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assetforge.core.tasks import Task
from assetforge.story import steps
from assetforge.story.models import StoryConfig


def call_label(task: str) -> str:
    from assetforge.story.progress import _label

    name = task if task.startswith("story.") else f"story.{task}"
    try:
        return _label(name, {}, {})
    except Exception:  # noqa: BLE001 - a label is never worth failing for
        return task


class CallLog(list):  # type: ignore[type-arg]
    """`ctx.calls` that also reports to the task (shared by worker contexts, so parallel calls report too)."""

    def __init__(self, task: Task | None, items: Any = ()) -> None:
        super().__init__(items)
        self.task = task

    def append(self, item: Any) -> None:  # noqa: D102
        super().append(item)
        if self.task is None or not isinstance(item, dict):
            return
        if item.get("task"):
            how = "reused" if item.get("reused") else " ".join(x for x in (item.get("provider"), item.get("model")) if x)
            try:
                self.task.done_step(f"{call_label(str(item['task']))}{f' · {how}' if how else ''}")
            except Exception:  # noqa: BLE001 - reporting never breaks the work
                pass
        self.task.check_cancel()


@dataclass
class TrackedCtx(steps.Ctx):
    task: Task | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.calls, CallLog):
            self.calls = CallLog(self.task, self.calls)

    @property
    def config(self) -> StoryConfig:
        if self.task is not None:
            self.task.check_cancel()
        return self.project.config


def make_ctx(task: Task | None, **kwargs: Any) -> steps.Ctx:
    """A tracked context when there is a task, the plain one otherwise."""
    return TrackedCtx(task=task, **kwargs) if task is not None else steps.Ctx(**kwargs)
