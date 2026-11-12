"""Publishing page routes (`#/backend`): the content backend in the R2 bucket (publish/backend.py, doc 27).
Every write is a POST that returns the plan unless the body has `confirm: true`; nothing is written otherwise."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from assetforge.app.server import Request, Router
from assetforge.core.catalog import Library
from assetforge.errors import InvalidInputError, NotFoundError


def _home(lib: Library) -> Path:
    return lib.paths.home


def _release_dir(lib: Library, story: str, release: str | None) -> Path:
    """A registered story id → its release folder (the latest unless `release`)."""
    from assetforge.story import contract
    from assetforge.story.project import StoryProject, registered

    root = registered(_home(lib)).get(story)
    if root is None:
        raise NotFoundError(f"No registered story {story!r}", hint="Publishing starts from a story in this library")
    return contract.release_dir(StoryProject(root), release or None)


def _body(req: Request) -> dict[str, Any]:
    body = dict(req.body or {})
    if not str(body.get("story", "")).strip() and req.path.rsplit("/", 1)[-1] not in ("verify", "repair"):
        raise InvalidInputError("Which story? Pass `story`")
    return body


def register_routes(router: Router) -> None:
    @router.route("GET", "/api/backend/status")
    def backend_status(req: Request, lib: Library) -> dict[str, Any]:
        """`?cached=1` answers from the last view without touching the network (the page opens instantly)."""
        from assetforge.publish import backend

        live = req.arg("cached") not in ("1", "true")
        return backend.status(home=_home(lib), live=live, save=live)

    @router.route("POST", "/api/backend/plan")
    def backend_plan(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.publish import backend

        body = _body(req)
        return {"plan": backend.plan(_release_dir(lib, str(body["story"]), body.get("release")), home=_home(lib))}

    @router.route("POST", "/api/backend/publish")
    def backend_publish(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.publish import backend

        body = _body(req)
        path = _release_dir(lib, str(body["story"]), body.get("release"))
        plan = backend.plan(path, home=_home(lib))
        if body.get("confirm") is True and plan["publishable"]:
            return {"written": True, "plan": plan, "receipt": backend.publish(path, yes=True, home=_home(lib))}
        return {"written": False, "plan": plan}

    @router.route("POST", "/api/backend/promote")
    def backend_promote(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.publish import backend

        body = _body(req)
        return backend.promote(str(body["story"]), release=body.get("release") or None, yes=body.get("confirm") is True, home=_home(lib))

    @router.route("POST", "/api/backend/rollback")
    def backend_rollback(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.publish import backend

        body = _body(req)
        if not body.get("release"):
            raise InvalidInputError("Which release? Pass `release`")
        return backend.rollback(str(body["story"]), str(body["release"]), channel=str(body.get("channel") or "staging"),
                                yes=body.get("confirm") is True, home=_home(lib))  # fmt: skip

    @router.route("POST", "/api/backend/(?P<what>unlist|relist)")
    def backend_listing(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.publish import backend

        body = _body(req)
        return backend.set_listed(str(body["story"]), listed=req.match["what"] == "relist", channel=str(body.get("channel") or "staging"),
                                  yes=body.get("confirm") is True, home=_home(lib))  # fmt: skip

    @router.route("POST", "/api/backend/verify")
    def backend_verify(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.publish import backend

        body = dict(req.body or {})
        return backend.verify(home=_home(lib), channel=body.get("channel") or None, deep=bool(body.get("deep")),
                              public=bool(body.get("public")))  # fmt: skip

    @router.route("POST", "/api/backend/repair")
    def backend_repair(req: Request, lib: Library) -> dict[str, Any]:
        from assetforge.publish import backend

        body = dict(req.body or {})
        return backend.repair(home=_home(lib), yes=body.get("confirm") is True, deep=bool(body.get("deep")))
