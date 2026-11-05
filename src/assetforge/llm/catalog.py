"""What text models the user can write with: each provider's sign-in state (cheap local checks, no model call),
the models their subscription offers, and which provider/model pairs have already worked in this library."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from assetforge.config import Settings
from assetforge.llm import all_providers, provider_chain
from assetforge.llm.base import TextProvider

if TYPE_CHECKING:
    from assetforge.core.catalog import Library


def used_models(lib: Library) -> dict[tuple[str, str], dict[str, Any]]:
    rows = lib.conn.execute(
        "SELECT provider, model, COUNT(*) AS calls, MAX(updated_at) AS last FROM generation_jobs "
        "WHERE kind = 'text' AND status = 'succeeded' AND provider IS NOT NULL GROUP BY provider, model").fetchall()  # fmt: skip
    return {(r["provider"], r["model"] or ""): {"calls": r["calls"], "last_used": r["last"]} for r in rows}


def _entry(p: TextProvider, used: dict[tuple[str, str], dict[str, Any]], check_login: bool) -> dict[str, Any]:
    available, reason = p.available()
    signed, account = p.signed_in() if (available and check_login) else (None, reason)
    default = p.default_model() or ""
    models = []
    for m in p.models():
        stats = used.get((p.name, m["id"])) or (used.get((p.name, default)) if not m["id"] and default else None)
        models.append({**m, "worked": bool(stats), **(stats or {})})
    note = getattr(p, "version_note", lambda: None)() if available else None
    return {"name": p.name, "billing": p.billing, "note": note, "installed": available, "signed_in": signed, "account": account,
            "usable": available and signed is not False, "default_model": default or None, "models": models,
            "login_hint": p.login_hint or None, "reason": None if available else reason}  # fmt: skip


def describe(lib: Library, settings: Settings, *, check_login: bool = True) -> dict[str, Any]:
    used = used_models(lib)
    providers = all_providers(settings)
    with ThreadPoolExecutor(max_workers=max(1, len(providers))) as pool:  # status checks spawn CLIs: run together
        entries = list(pool.map(lambda p: _entry(p, used, check_login), providers))
    usable = {e["name"] for e in entries if e["usable"]}
    auto = [p.name for p in provider_chain(None, settings) if p.name in usable]
    return {"providers": entries, "auto": auto, "recommended": auto[0] if auto else None}
