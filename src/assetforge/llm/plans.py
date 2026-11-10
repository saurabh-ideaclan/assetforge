"""Execution plans: which provider and model handle each kind of task, in what fallback order, and whether a
paid API may be used when a subscription is unavailable.

Precedence when a call is made (highest first):
1. the run's own override (`--provider/--model` on the CLI, the model picker on an action);
2. the story's plan (`StoryConfig.ai`);
3. the library plan (`$ASSETFORGE_HOME/ai.json`, edited in the app's AI & workflow settings);
4. managed defaults: Fable picks a sensible model per task kind from what is signed in.

A plan is a list of steps `{provider, model}` tried in order. Steps whose provider bills per token are *paid*:
`paid_fallback` says whether they run automatically (`allow`), need a one-off confirmation (`confirm`, the
default) or never run (`never`). Every job records what was requested, what ran, who chose it and the fallbacks
that happened, so the UI never shows a silent model switch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.config import Settings
from assetforge.errors import InvalidInputError, ProviderError
from assetforge.llm import canonical, get_provider, provider_chain
from assetforge.llm.base import TextProvider

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

PaidPolicy = Literal["allow", "confirm", "never"]
TASK_KINDS: dict[str, str] = {
    "brainstorm": "Story idea brainstorming",
    "research": "Market and genre research",
    "premise": "Story premise",
    "world": "World-building",
    "names": "Naming guide",
    "cast": "Character creation",
    "outline": "Season outline and chapter planning",
    "scene": "Dialogue and scene writing",
    "character": "Character revision",
    "evaluation": "Narrative evaluation and continuity",
    "review": "Editorial review",
    "rewrite": "Story rewriting and revisions",
    "alternatives": "Alternative versions",
}
# which task kinds benefit from the strongest model when Fable chooses
HEAVY_KINDS = frozenset({"premise", "outline", "character", "review", "evaluation", "alternatives"})
LIGHT_KINDS = frozenset({"research", "brainstorm"})


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str | None = None

    def key(self) -> str:
        return f"{self.provider}:{self.model or ''}"


class ExecutionPlan(BaseModel):
    """What to use, in order. `managed` lets Fable pick per task kind from the signed-in providers."""

    model_config = ConfigDict(extra="forbid")
    managed: bool = Field(True, description="Fable chooses the model per task from what is available")
    steps: list[Step] = Field(default_factory=list, description="Manual order of provider/model pairs")
    paid_fallback: PaidPolicy = Field("confirm", description="allow | confirm | never for providers billed per token")
    per_task: dict[str, list[Step]] = Field(default_factory=dict, description="Task kind → its own step list")
    daily_api_limit_usd: float | None = Field(None, ge=0, description="Stop paid calls past this estimate per day")

    def steps_for(self, kind: str) -> list[Step]:
        return self.per_task.get(kind) or self.steps


# ---------------------------------------------------------------------- storage


def _path(home: Path) -> Path:
    return Path(home) / "ai.json"


def load_plan(home: Path) -> ExecutionPlan:
    path = _path(home)
    if not path.exists():
        return ExecutionPlan()
    try:
        return ExecutionPlan(**json.loads(path.read_text()))
    except (ValueError, TypeError) as exc:
        raise InvalidInputError(f"{path} is not a valid execution plan: {exc}") from exc


def save_plan(home: Path, plan: ExecutionPlan) -> Path:
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.model_dump(mode="json"), indent=2))
    return path


# ---------------------------------------------------------------------- resolution


def task_kind(task: str) -> str:
    """`story.scene.s01.p2` → scene; `story.check` → evaluation; `story.improve` → rewrite …"""
    name = task.removeprefix("story.").split(".")[0]
    return {"scene": "scene", "refine": "rewrite", "improve": "rewrite", "fix": "rewrite", "check": "evaluation",
            "lint": "evaluation", "review": "review", "world": "world", "names": "names", "cast": "cast",
            "outline": "outline", "premise": "premise", "brainstorm": "brainstorm", "research": "research",
            "character": "character", "alternatives": "alternatives", "sound": "world"}.get(name, name)  # fmt: skip


def managed_steps(kind: str, settings: Settings) -> list[Step]:
    """Fable's choice: the available subscription providers in chain order; on Claude, Opus for heavy creative
    judgement and Sonnet for everything else. Paid API providers come last and are subject to the policy."""
    steps: list[Step] = []
    for p in provider_chain(None, settings):
        model: str | None = None
        if p.name == "claude-cli":
            model = "opus" if kind in HEAVY_KINDS else "sonnet"
        elif p.name == "anthropic-api":
            model = p.default_model()
        steps.append(Step(provider=p.name, model=model))
    return steps


class Resolved(BaseModel):
    """The ordered providers to try for one call, with the bookkeeping the job records."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    kind: str
    chosen_by: Literal["override", "story", "library", "managed"]
    steps: list[Step]
    skipped: list[dict[str, str]] = Field(default_factory=list, description="Paid steps held back by the policy")
    needs_confirmation: Step | None = Field(None, description="The first paid step that needs a human yes")


def is_paid(provider: TextProvider) -> bool:
    return provider.billing == "api"


def resolve(task: str, settings: Settings, *, home: Path | None = None, story_plan: ExecutionPlan | None = None,
            override_provider: str | None = None, override_model: str | None = None,
            allow_paid: bool = False) -> Resolved:  # fmt: skip
    """Turn the precedence rules into an ordered list of steps for this task. Paid steps are kept, held back
    (`skipped`) or flagged for confirmation according to the effective `paid_fallback` policy."""
    kind = task_kind(task)
    library_plan = load_plan(home) if home is not None else ExecutionPlan()
    plan = story_plan or library_plan
    if override_provider:
        first = Step(provider=canonical(override_provider), model=override_model or None)
        rest = [s for s in (plan.steps_for(kind) if not plan.managed else managed_steps(kind, settings)) if s.key() != first.key()]
        steps, chosen = [first, *rest], "override"
    elif override_model and plan.managed:
        steps = [Step(provider=s.provider, model=override_model if i == 0 else s.model) for i, s in enumerate(managed_steps(kind, settings))]
        chosen = "override"
    elif story_plan is not None and (story_plan.steps_for(kind) or not story_plan.managed):
        steps, chosen = story_plan.steps_for(kind), "story"
    elif not plan.managed and plan.steps_for(kind):
        steps, chosen = plan.steps_for(kind), "library"
    else:
        steps, chosen = managed_steps(kind, settings), "managed"
    policy = plan.paid_fallback
    kept: list[Step] = []
    skipped: list[dict[str, str]] = []
    needs: Step | None = None
    for step in steps:
        try:
            paid = is_paid(get_provider(step.provider, settings))
        except InvalidInputError:
            skipped.append({"provider": step.provider, "reason": "unknown provider"})
            continue
        if paid and not allow_paid and chosen != "override":
            if policy == "never":
                skipped.append({"provider": step.provider, "reason": "paid fallback is off"})
                continue
            if policy == "confirm":
                needs = needs or step
                skipped.append({"provider": step.provider, "reason": "paid fallback needs your confirmation"})
                continue
        kept.append(step)
    return Resolved(kind=kind, chosen_by=chosen, steps=kept, skipped=skipped, needs_confirmation=needs)


def confirmation_error(resolved: Resolved, task: str) -> ProviderError:
    """Raised when every subscription step failed and the next step is paid but unconfirmed."""
    step = resolved.needs_confirmation
    assert step is not None
    return ProviderError(
        f"The subscription providers could not finish {task}; the next option, {step.provider}"
        + (f" ({step.model})" if step.model else "") + ", is billed per token and needs your confirmation.",
        provider=step.provider, kind="needs_confirmation",
        hint="Re-run with paid fallback allowed, or change the paid-fallback policy in AI & workflow settings.")  # fmt: skip


def describe_choice(resolved: Resolved, used_provider: str | None, used_model: str | None) -> dict[str, Any]:
    """The transparency record stored on a job and shown to the operator."""
    requested = resolved.steps[0] if resolved.steps else None
    fallbacks = []
    if requested and used_provider and (used_provider != requested.provider or (requested.model and used_model != requested.model)):
        fallbacks.append({"from": requested.model_dump(), "to": {"provider": used_provider, "model": used_model}})
    return {"kind": resolved.kind, "chosen_by": resolved.chosen_by,
            "requested": requested.model_dump() if requested else None,
            "used": {"provider": used_provider, "model": used_model} if used_provider else None,
            "fallbacks": fallbacks, "held_back": resolved.skipped}  # fmt: skip
