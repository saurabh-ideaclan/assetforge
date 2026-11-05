"""Ask a text provider for an answer shaped like a Pydantic model, reliably and only once.

* Each call is a `generation_jobs` row (kind `text`) keyed by a fingerprint of task, prompt, schema, provider
  and model, so repeating a step returns the stored answer without spending plan quota (`fresh=True` skips it).
* The prompt and answer are written to `jobs/<id>/` before parsing, so nothing paid for is ever lost.
* Answers are validated against the model; one repair round sends the validation errors back. A provider
  that still fails, or fails with a fallback-able error, hands over to the next provider in the chain.
* `limits.provider_slot` caps concurrent calls per provider across every agent sharing the library.
"""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from assetforge.config import Settings, load_settings
from assetforge.core import jobs, limits
from assetforge.core.models import JobStatus
from assetforge.core.util import dumps, loads
from assetforge.errors import AssetForgeError, ProviderError
from assetforge.llm import provider_chain
from assetforge.llm.base import TextCall, TextProvider, TextResponse

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

M = TypeVar("M", bound=BaseModel)
RETRY_DELAYS = (2.0, 8.0)
sleep: Callable[[float], None] = time.sleep  # replaced in tests
DROP_KEYS = {"title", "default", "examples", "pattern", "minLength", "maxLength", "format", "minimum", "maximum",
             "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "uniqueItems", "discriminator"}  # fmt: skip


@dataclass
class Structured:
    data: Any
    job_id: str
    provider: str
    model: str
    billing: str
    reused: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------- schema + parsing


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema in the strict subset every provider accepts: closed objects, every property
    required (optional ones nullable instead), no defaults or numeric/length keywords (Pydantic re-checks)."""
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        # `properties`/`$defs` map *names* to schemas: a field called "title" is not the `title` keyword
        node = {k: ({name: walk(s) for name, s in v.items()} if k in ("properties", "$defs") else walk(v))
                for k, v in node.items() if k not in DROP_KEYS}  # fmt: skip
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties", {})
            required = set(node.get("required", []))
            for name, prop in list(props.items()):
                if name not in required and not _nullable(prop):
                    description = prop.pop("description", None) if "$ref" in prop else None
                    props[name] = {"anyOf": [prop, {"type": "null"}], **({"description": description} if description else {})}
            node["required"] = list(props)
            node["additionalProperties"] = False
        if "$ref" in node and len(node) > 1:  # strict mode allows no siblings next to $ref
            node = {"$ref": node["$ref"]}
        return node

    return walk(schema)


def _nullable(prop: dict[str, Any]) -> bool:
    return prop.get("type") == "null" or any(p.get("type") == "null" for p in prop.get("anyOf", []))


def extract_json(text: str) -> Any:
    """The JSON value in a model's reply, tolerating code fences and prose around it."""
    body = text.strip()
    if fence := re.search(r"```(?:json)?\s*(.*?)```", body, re.S):
        body = fence.group(1).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (body.find("{"), body.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON found in the answer")
    start = min(starts)
    end = body.rfind("}" if body[start] == "{" else "]")
    return json.loads(body[start:end + 1])


def drop_nulls(value: Any) -> Any:
    """Nullable-for-strictness fields come back as null; dropping them lets the model's defaults apply."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def _validate(model: type[M], response: TextResponse) -> M:
    data = response.data if response.data is not None else extract_json(response.text)
    return model.model_validate(drop_nulls(data))


def _problems(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "\n".join(f"- {'.'.join(str(p) for p in e['loc']) or 'answer'}: {e['msg']}" for e in exc.errors()[:30])
    return f"- {exc}"


# ---------------------------------------------------------------------- jobs


def _cached(lib: Library, fp: str) -> Any:
    row = lib.conn.execute(
        "SELECT * FROM generation_jobs WHERE kind = 'text' AND fingerprint = ? AND status = 'succeeded' "
        "ORDER BY created_at DESC, id DESC LIMIT 1", (fp,)).fetchone()  # fmt: skip
    return row


def _persist(lib: Library, job_id: str, name: str, content: str) -> None:
    directory = lib.paths.jobs / job_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(content)


# ---------------------------------------------------------------------- entry point


def generate(lib: Library, call: TextCall, model: type[M], *, provider: str | None = None, fallback: bool = True,
             fresh: bool = False, settings: Settings | None = None, project: str | None = None) -> Structured:  # fmt: skip
    """Return a validated `model` instance (in `.data`) for `call`, reusing an identical earlier answer."""
    settings = settings or load_settings(lib.paths.home)
    call.schema = strict_schema(model)
    fp = jobs.fingerprint({"task": call.task, "system": call.system, "prompt": call.prompt, "schema": call.schema,
                           "provider": provider or settings.get("ASSETFORGE_TEXT_PROVIDER") or "auto", "model": call.model})  # fmt: skip
    if not fresh and (row := _cached(lib, fp)) is not None:
        stored = loads(row["result"], {})
        try:
            data = model.model_validate(stored.get("data"))
            return Structured(data=data, job_id=row["id"], provider=row["provider"] or "", model=row["model"] or "",
                              billing=stored.get("billing", ""), reused=True, usage=stored.get("usage", {}))  # fmt: skip
        except ValidationError:
            pass  # the model changed since: ask again
    chain = provider_chain(provider, settings, fallback=fallback)
    if not chain:
        raise ProviderError(
            "No text provider is available", provider="auto", kind="not_configured",
            hint="Install and sign in to a CLI (claude, codex or gemini); see `assetforge llm providers`.")  # fmt: skip
    job_id = jobs.create_job(lib, "text", {"task": call.task, "provider": provider or "auto", "model": call.model,
                                           "project": project, "prompt_chars": len(call.prompt)}, fingerprint=fp)  # fmt: skip
    _persist(lib, job_id, "prompt.md", f"# system\n\n{call.system}\n\n# prompt\n\n{call.prompt}\n")
    _persist(lib, job_id, "schema.json", json.dumps(call.schema, indent=2))
    attempts: list[dict[str, Any]] = []
    errors: list[ProviderError] = []
    for prov in chain:
        try:
            response, data = _ask(lib, job_id, prov, call, model, attempts)
        except ProviderError as exc:
            errors.append(exc)
            if exc.kind == "bad_response":  # answered, but not in the required shape (other errors are logged in _ask)
                attempts.append({"provider": prov.name, "task": call.task, "error": exc.to_dict()})
            if not exc.allows_fallback:
                break
            continue
        result = {"data": data.model_dump(mode="json"), "billing": response.billing, "usage": response.usage,
                  "task": call.task, "attempts": attempts}  # fmt: skip
        jobs.update_job(lib, job_id, status=JobStatus.SUCCEEDED.value, provider=response.provider, model=response.model,
                        result=result, cost_usd=0.0 if response.billing != "api" else None, attempts=len(attempts))  # fmt: skip
        return Structured(data=data, job_id=job_id, provider=response.provider, model=response.model,
                          billing=response.billing, usage=response.usage, attempts=attempts)  # fmt: skip
    best = _most_useful(errors)
    every = [{"provider": e.provider, "kind": e.kind, "message": e.message[:500]} for e in errors]
    jobs.update_job(lib, job_id, status=JobStatus.FAILED.value, error={**best.to_dict(), "errors": every,
                                                                       "attempts": attempts}, attempts=len(attempts))  # fmt: skip
    raise ProviderError(best.message, provider=best.provider, kind=best.kind, hint=best.hint, job_id=job_id,
                        tried=list(dict.fromkeys(e.provider for e in errors)), errors=every)  # fmt: skip


def _most_useful(errors: list[ProviderError]) -> ProviderError:
    """The first provider's error (the one asked for, or auto's first pick), unless it simply isn't set up;
    every provider's error is still listed in `details.errors`."""
    for exc in errors:
        if exc.kind != "not_configured":
            return exc
    return errors[0]


def _ask(lib: Library, job_id: str, prov: TextProvider, call: TextCall, model: type[M],
         attempts: list[dict[str, Any]]) -> tuple[TextResponse, M]:  # fmt: skip
    response = _with_retries(lib, job_id, prov, call, attempts)
    try:
        return response, _validate(model, response)
    except (ValidationError, ValueError) as exc:
        problems = _problems(exc)
    repair = TextCall(
        prompt=(f"{call.prompt}\n\n---\n\nYour previous answer did not match the required JSON schema:\n{problems}\n\n"
                f"Previous answer:\n{response.text[:12000] or json.dumps(response.data)[:12000]}\n\n"
                "Return the complete corrected JSON answer."),
        system=call.system, schema=call.schema, model=call.model, timeout=call.timeout, task=f"{call.task}.repair",
        offline=call.offline)  # fmt: skip
    response = _with_retries(lib, job_id, prov, repair, attempts)
    try:
        return response, _validate(model, response)
    except (ValidationError, ValueError) as exc:
        raise ProviderError(f"{prov.name} answer did not match the schema after a repair round:\n{_problems(exc)}",
                            provider=prov.name, kind="bad_response") from None  # fmt: skip


def _with_retries(lib: Library, job_id: str, prov: TextProvider, call: TextCall,
                  attempts: list[dict[str, Any]]) -> TextResponse:  # fmt: skip
    ok, reason = prov.available()
    if not ok:
        attempts.append({"provider": prov.name, "error": reason})
        raise ProviderError(f"{prov.name}: {reason}", provider=prov.name, kind="not_configured", hint=prov.login_hint or None)
    for attempt in range(len(RETRY_DELAYS) + 1):
        started = time.monotonic()
        try:
            with limits.provider_slot(lib, prov.name):
                response = prov.run(call)
        except ProviderError as exc:
            attempts.append({"provider": prov.name, "task": call.task, "error": exc.to_dict(),
                             "seconds": round(time.monotonic() - started, 2)})  # fmt: skip
            if exc.retryable and attempt < len(RETRY_DELAYS):
                sleep(RETRY_DELAYS[attempt])
                continue
            raise
        except AssetForgeError:
            raise
        n = len(attempts) + 1
        _persist(lib, job_id, f"answer_{n}.txt", response.text if response.data is None else dumps(response.data))
        attempts.append({"provider": response.provider, "model": response.model, "task": call.task,
                         "usage": response.usage, "seconds": round(time.monotonic() - started, 2)})  # fmt: skip
        return response
    raise AssertionError("unreachable")
