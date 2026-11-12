"""The content backend without a server (docs/narrative/27): the bucket is the database, AssetForge its only
writer, the app a read-only client that polls one signed index per channel.

Layout (every key under `ASSETFORGE_R2_PREFIX`, applied by `r2.Store`; keys inside documents stay relative):

    catalog/<channel>/current.json          signed `vn.catalog` envelope, no-cache (the one file the app polls)
    catalog/<channel>/history/<seq>.json    every index ever published on that channel (immutable, create-only)
    stories/<story>/releases/<rid>/…        the v2 release documents (+ delta-from-<old>.json)
    blobs/sha256/<digest>.<ext>             media shared by every release

Channels: `staging` (where `publish` writes) and `production` (reached only by `promote`). Every write needs an
explicit yes and has a read-only plan form; the index is always written last (history first, create-only, so two
writers cannot both take a sequence; then `current.json`). Nothing is deleted except by `gc`, which only removes
objects no index in any history references, inside the prefix, under `blobs/` or `stories/`.

Local records: `$ASSETFORGE_HOME/backend/log.jsonl` (one line per write) and `backend/state.json` (the last index
seen per channel, which the app's Publishing page opens with).
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assetforge.core.util import utcnow
from assetforge.errors import AssetForgeError, ConflictError, InvalidInputError, NotFoundError, PolicyError, PublishError
from assetforge.publish import r2
from assetforge.publish.r2 import IMMUTABLE, MUTABLE, R2Config, Store
from assetforge.story import catalog_sign, contract
from assetforge.story import contract_models as cm
from assetforge.story import delta as deltas
from assetforge.story.project import write_atomic

STAGING, PRODUCTION = "staging", "production"
CHANNELS = (STAGING, PRODUCTION)
RECEIPTS = "publish.json"
GC_NAMESPACES = ("blobs/", "stories/")  # gc never deletes anything else, even inside the prefix
_HISTORY = re.compile(r"^catalog/(?P<channel>[a-z]+)/history/(?P<seq>\d+)\.json$")


def index_key(channel: str) -> str:
    return f"catalog/{_channel(channel)}/current.json"


def history_key(channel: str, sequence: int) -> str:
    return f"catalog/{_channel(channel)}/history/{int(sequence)}.json"


def _channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise InvalidInputError(f"Unknown channel {channel!r}", hint="Use staging or production")
    return channel


# ---------------------------------------------------------------------- local records


def backend_dir(home: Path) -> Path:
    return Path(home) / "backend"


def load_state(home: Path) -> dict[str, Any]:
    try:
        return json.loads((backend_dir(home) / "state.json").read_text())
    except (OSError, ValueError):
        return {"channels": {}, "updated_at": None}


def _save_state(home: Path, store: Store, indexes: list[Index]) -> None:
    state = load_state(home)
    for idx in indexes:
        state["channels"][idx.channel] = {"sequence": idx.sequence, "generatedAt": (idx.payload or {}).get("generatedAt"),
                                          "seen_at": utcnow(), "payload": idx.payload}  # fmt: skip
    state["bucket"], state["prefix"], state["updated_at"] = store.bucket, store.prefix, utcnow()
    backend_dir(home).mkdir(parents=True, exist_ok=True)
    write_atomic(backend_dir(home) / "state.json", json.dumps(state, indent=2, ensure_ascii=False) + "\n", backup=False)


def _log(home: Path, command: str, **fields: Any) -> None:
    backend_dir(home).mkdir(parents=True, exist_ok=True)
    line = {"at": utcnow(), "command": command, **fields}
    with (backend_dir(home) / "log.jsonl").open("a") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def read_log(home: Path, limit: int = 50) -> list[dict[str, Any]]:
    try:
        lines = (backend_dir(home) / "log.jsonl").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------- the index


@dataclass
class Index:
    channel: str
    payload: dict[str, Any] | None = None
    raw: bytes | None = None
    sequence: int = 0
    stale_pointer: bool = False  # current.json lags behind a history entry (an interrupted write)

    def story(self, story_id: str) -> dict[str, Any] | None:
        return next((s for s in (self.payload or {}).get("stories", []) if s["storyId"] == story_id), None)


def _public_key(home: Path) -> Any:
    public = catalog_sign.load_public(home)
    if public is None:
        raise PolicyError("The bucket has an index but this library has no catalog key to verify it",
                          hint="Restore $ASSETFORGE_HOME/keys/vn-catalog.ed25519 from where it was made")  # fmt: skip
    return public


def _verify(data: bytes, home: Path, channel: str, key: str) -> dict[str, Any]:
    payload = catalog_sign.verify(json.loads(data), _public_key(home))
    if payload.get("channel") != channel:
        raise PolicyError(f"{key} is an index for channel {payload.get('channel')!r}, not {channel!r}")
    return payload


def read_index(store: Store, channel: str, home: Path) -> Index:
    """The newest verified index of a channel. When `current.json` lags behind a history entry (a write that
    stopped between the two), the newest history entry is the truth and `stale_pointer` says so."""
    idx = Index(channel)
    data = store.get(index_key(channel))
    if data is not None:
        idx.payload, idx.raw = _verify(data, home, channel, index_key(channel)), data
        idx.sequence = int(idx.payload["catalogSequence"])
    while (newer := store.get(history_key(channel, idx.sequence + 1))) is not None:
        idx.payload, idx.raw = _verify(newer, home, channel, history_key(channel, idx.sequence + 1)), newer
        idx.sequence, idx.stale_pointer = int(idx.payload["catalogSequence"]), True
    return idx


def commit_index(store: Store, previous: Index, payload: dict[str, Any], key: Any) -> int:
    """Write the next index of a channel: history first with a create-only condition (the loser of a race gets a
    ConflictError and nothing else is written), then `current.json` last."""
    sequence = int(payload["catalogSequence"])
    if sequence != previous.sequence + 1 or payload["channel"] != previous.channel:
        raise ConflictError("The index to write does not follow the one read", sequence=sequence, previous=previous.sequence)
    body = cm.canonical(catalog_sign.sign(payload, key))
    if not store.create(history_key(previous.channel, sequence), body, "application/json", IMMUTABLE):
        raise ConflictError(f"Index sequence {sequence} on {previous.channel} was taken by another writer",
                            hint="Re-run: it reads the newer index and plans again")  # fmt: skip
    store.put(index_key(previous.channel), body, "application/json", MUTABLE)
    return sequence


# ---------------------------------------------------------------------- walking a published release


@dataclass
class Walk:
    """Every object a published release needs, read from the bucket (with the local copy as a fallback)."""

    tree: deltas.Tree | None = None
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)  # key → descriptor
    fetched: dict[str, str] = field(default_factory=dict)  # key → "ok" | "corrupted" (documents read on the way)
    problems: list[dict[str, Any]] = field(default_factory=list)


def local_objects(home: Path) -> dict[str, Path]:
    """key → local file, for every release of every registered story (the source for `repair`)."""
    from assetforge.story.project import registered

    found: dict[str, Path] = {}
    for root in registered(home).values():
        releases = Path(root) / "releases"
        if not releases.is_dir():
            continue
        for directory in sorted(releases.iterdir()):
            if not (cm.RELEASE_ID.match(directory.name) and (directory / "release.json").is_file()):
                continue
            try:
                _, objs = contract.release_objects(directory)
            except (OSError, ValueError, KeyError, cm.ContractError):
                continue
            found |= {o.key: o.path for o in objs}
    return found


def walk_release(store: Store, ref: dict[str, Any], story_id: str, local: dict[str, Path] | None = None) -> Walk:
    walk = Walk()
    expected: dict[str, dict[str, Any]] = {ref["manifest"]["key"]: ref["manifest"]}

    def get(key: str) -> bytes:
        data = store.get(key)
        want = expected.get(key)
        ok = data is not None and (want is None or (len(data) == want["bytes"] and cm.sha256(data) == want["sha256"]))
        walk.fetched[key] = "ok" if ok else ("missing" if data is None else "corrupted")
        if not ok and local and key in local and local[key].is_file():
            data = local[key].read_bytes()  # keep walking from the local copy
        if data is None:
            raise KeyError(key)
        if key.endswith("/release.json"):
            rel = json.loads(data)
            for desc in [*rel.get("locales", {}).values(), *rel.get("migrations", [])]:
                expected[desc["key"]] = desc
            for c in rel["chapters"]:
                expected[c["manifest"]["key"]] = c["manifest"]
        elif key.endswith("/bundle.json"):
            bundle = json.loads(data)
            for name in ("graph", "presentation", "assets", "downloads"):
                expected[bundle[name]["key"]] = bundle[name]
            for desc in bundle["locales"].values():
                expected[desc["key"]] = desc
        return data

    rid = ref["releaseId"]
    try:
        walk.tree = deltas.read_tree(get, story_id, rid)
    except (KeyError, ValueError) as exc:
        walk.problems.append({"key": str(exc).strip("'"), "problem": "unreadable", "release": rid})
        return walk
    t = walk.tree
    walk.objects = {ref["manifest"]["key"]: ref["manifest"]} | dict(t.structure) | t.content() | dict(t.blobs)
    if ref.get("delta"):
        walk.objects[ref["delta"]["key"]] = ref["delta"]
    return walk


def _check_object(store: Store, desc: dict[str, Any], deep: bool, fetched: str | None) -> str:
    if fetched is not None:
        return fetched
    head = store.head(desc["key"])
    if head is None:
        return "missing"
    meta = {str(k).lower(): v for k, v in (head.get("Metadata") or {}).items()}
    if int(head.get("ContentLength", -1)) != desc["bytes"] or meta.get("sha256", desc["sha256"]) != desc["sha256"]:
        return "corrupted"
    if deep:
        data = store.get(desc["key"])
        if data is None:
            return "missing"
        if cm.sha256(data) != desc["sha256"]:
            return "corrupted"
    return "ok"


def _entry_from(walk: Walk, ref: dict[str, Any], previous: dict[str, Any] | None, store: Store) -> dict[str, Any]:
    """A story entry pointing at a release already in the bucket (promotion, rollback)."""
    assert walk.tree is not None
    release = walk.tree.release
    lang = release["defaultLocale"]
    data = store.get(release["locales"][lang]["key"]) or b"{}"
    strings = json.loads(data).get("strings", {})
    return catalog_sign.entry_for(release, ref["manifest"], strings.get(release["titleKey"], release["storyId"]),
                                  strings.get(release["summaryKey"], ""), previous, ref=ref,
                                  download_bytes=deltas.download_bytes(walk.tree))  # fmt: skip


# ---------------------------------------------------------------------- plan and publish


def _store(home: Path, client: Any, cfg: R2Config | None) -> Store:
    return r2.open_store(home, client, cfg)


def _classify(store: Store, objs: list[contract.Obj]) -> list[dict[str, Any]]:
    rows = []
    for o in objs:
        head = store.head(o.key)
        if head is None:
            action = "upload"
        elif o.role == "blob":  # content-addressed: the same size under the same digest is the same file
            action = "skip" if int(head.get("ContentLength", -1)) == o.bytes else "conflict"
        else:
            remote = store.get(o.key)
            action = "skip" if remote is not None and cm.sha256(remote) == o.sha256 else "conflict"
        rows.append({"key": o.key, "role": o.role, "bytes": o.bytes, "action": action})
    return rows


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for action in ("upload", "skip", "conflict", "unknown"):
        chosen = [r for r in rows if r["action"] == action]
        out[action] = {"count": len(chosen), "bytes": sum(r["bytes"] for r in chosen)}
    return out


def plan(release: Path, *, home: Path, client: Any = None, cfg: R2Config | None = None, channel: str = STAGING) -> dict[str, Any]:
    """What `publish` would upload, skip and refuse, with byte totals (reads only; never writes)."""
    doc, objs = contract.release_objects(release)
    issues = contract.validate_release(release)
    errors = [i.model_dump() for i in issues if i.level == "error"]
    result: dict[str, Any] = {"story": doc["storyId"], "release": doc["releaseId"], "graph_id": doc["graphId"], "channel": channel,
                              "draft": bool(doc.get("draft")), "valid": not errors, "errors": errors[:20], "remote": "checked",
                              "delta": next((o.key for o in objs if "/delta-from-" in o.key), None)}  # fmt: skip
    try:
        store = _store(home, client, cfg)
        store.preflight()
        rows = _classify(store, objs)
        idx = read_index(store, channel, home)
        entry = idx.story(doc["storyId"])
        result["config"] = store.cfg.describe()
        result["catalog"] = {"key": index_key(channel), "channel": channel, "current_sequence": idx.sequence or None,
                             "next_sequence": idx.sequence + 1, "current_release": entry["current"] if entry else None}  # fmt: skip
    except (r2.PublishNotConfiguredError, r2.PublishAccessDeniedError, r2.PublishBucketMissingError) as exc:
        rows = [{"key": o.key, "role": o.role, "bytes": o.bytes, "action": "unknown"} for o in objs]
        result["remote"] = f"not checked: {exc.message}"
        result["remote_error"] = exc.to_dict()
    result["objects"] = rows
    result["totals"] = _totals(rows)
    result["conflicts"] = [r["key"] for r in rows if r["action"] == "conflict"]
    result["publishable"] = result["valid"] and not result["draft"] and not result["conflicts"] and result["remote"] == "checked"
    return result


def publish(release: Path, *, yes: bool, home: Path, client: Any = None, cfg: R2Config | None = None,
            channel: str = STAGING) -> dict[str, Any]:  # fmt: skip
    """Upload the release (its delta included) and publish the signed index of `channel` (staging) last. Refuses
    without `yes`, for drafts, for releases that do not validate, and when any object already published differs.
    Publishing the release that is already current changes nothing; publishing an older listed one is a rollback."""
    if not yes:
        raise PolicyError("Publishing needs an explicit yes", hint="Look at the plan (`backend plan` / `story publish` without --yes), then pass --yes")
    _channel(channel)
    doc, objs = contract.release_objects(release)
    if doc.get("draft"):
        raise PolicyError("A draft release can never be published", hint="Make the missing assets, then compile again")
    errors = [i for i in contract.validate_release(release) if i.level == "error"]
    if errors:
        raise PolicyError("The release does not validate", errors=[e.model_dump() for e in errors[:20]])
    store = _store(home, client, cfg)
    store.preflight()
    key = catalog_sign.load_or_create_key(home)
    previous = read_index(store, channel, home)
    rows = _classify(store, objs)
    conflicts = [r["key"] for r in rows if r["action"] == "conflict"]
    if conflicts:
        raise ConflictError("Objects already published under these keys differ; published objects are never overwritten",
                            conflicts=conflicts[:20], hint="Compile a new release instead")  # fmt: skip
    sid, rid = doc["storyId"], doc["releaseId"]
    old_entry = previous.story(sid)
    base = {"release": rid, "story": sid, "graph_id": doc["graphId"], "channel": channel, "bucket": store.bucket, "prefix": store.prefix}
    if old_entry and old_entry["current"] == rid and all(r["action"] == "skip" for r in rows):
        return {**base, "sequence": previous.sequence, "unchanged": True, "uploaded": 0, "uploaded_bytes": 0,
                "skipped": len(objs), "objects": [], "at": utcnow(), "message": f"{rid} is already current on {channel}"}  # fmt: skip
    action = {r["key"]: r["action"] for r in rows}
    uploaded: list[contract.Obj] = []
    for o in objs[:-1]:  # everything but release.json
        if action[o.key] == "upload":
            store.put(o.key, o.path.read_bytes(), o.media_type, IMMUTABLE)
            uploaded.append(o)
    for o in objs[:-1]:  # the release is complete before its manifest appears
        head = store.head(o.key)
        if head is None or int(head.get("ContentLength", -1)) != o.bytes:
            raise PublishError(f"{o.key} is missing or incomplete after upload", hint="Re-run `backend publish --yes`")
    top = objs[-1]
    if action[top.key] == "upload":
        store.put(top.key, top.path.read_bytes(), top.media_type, IMMUTABLE)
        uploaded.append(top)
    if store.head(top.key) is None:
        raise PublishError(f"{top.key} did not arrive")
    at = utcnow()
    payload = _publish_payload(release, doc, objs, top, old_entry, previous, at)
    sequence = commit_index(store, previous, payload, key)
    receipt = {**base, "sequence": sequence, "uploaded": len(uploaded), "uploaded_bytes": sum(o.bytes for o in uploaded),
               "skipped": len(objs) - len(uploaded), "objects": [o.key for o in uploaded], "at": at,
               "change": payload["changes"][0]["type"]}  # fmt: skip
    _record(release.parent, receipt)
    _log(home, "publish", channel=channel, story=sid, release=rid, sequence=sequence, objects=len(uploaded),
         bytes=receipt["uploaded_bytes"])  # fmt: skip
    _save_state(home, store, [Index(channel, payload, None, sequence)])
    return receipt


def _publish_payload(release: Path, doc: dict[str, Any], objs: list[contract.Obj], top: contract.Obj, old_entry: dict[str, Any] | None,
                     previous: Index, at: str) -> dict[str, Any]:  # fmt: skip
    sid, rid = doc["storyId"], doc["releaseId"]
    root = release.parent
    tree = deltas.read_tree(lambda k: contract.local_path(root, sid, k).read_bytes(), sid, rid)
    strings = json.loads(contract.local_path(root, sid, doc["locales"][doc["defaultLocale"]]["key"]).read_bytes())["strings"]
    manifest = {"key": top.key, "mediaType": top.media_type, "bytes": top.bytes, "sha256": top.sha256}
    listed = next((r for r in (old_entry or {}).get("releases", []) if r["releaseId"] == rid), None)
    delta_obj = next((o for o in objs if "/delta-from-" in o.key), None)
    delta_desc = {"key": delta_obj.key, "mediaType": delta_obj.media_type, "bytes": delta_obj.bytes, "sha256": delta_obj.sha256} if delta_obj else None
    entry = catalog_sign.entry_for(doc, manifest, strings.get(doc["titleKey"], sid), strings.get(doc["summaryKey"], ""), old_entry,
                                   at=at, delta=delta_desc, ref=listed, download_bytes=deltas.download_bytes(tree))  # fmt: skip
    change: dict[str, Any] = {"storyId": sid, "releaseId": rid, "fromReleaseId": old_entry["current"] if old_entry else None}
    if old_entry is None:
        change["type"] = "story_added"
    elif listed is not None:
        change["type"] = "rollback" if rid < old_entry["current"] else "release_published"
    else:
        change["type"] = "release_published"
        if delta_obj is not None:
            ddoc = json.loads(delta_obj.path.read_bytes())
            if ddoc["fromRelease"] == old_entry["current"]:
                change["summary"] = deltas.summary(ddoc)
    return catalog_sign.next_catalog(previous.payload, entry, channel=previous.channel, change=change, at=at)


def _record(root: Path, receipt: dict[str, Any]) -> None:
    path = root / RECEIPTS
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {"receipts": []}
    data["receipts"].append(receipt)
    write_atomic(path, json.dumps(data, indent=2) + "\n", backup=False)
    index = contract.load_index(root)
    for entry in index.get("releases", []):
        if entry["releaseId"] == receipt["release"]:
            mark = {"sequence": receipt["sequence"], "at": receipt["at"], "bucket": receipt["bucket"], "channel": receipt["channel"]}
            entry["published"] = entry.get("published") or mark
            entry.setdefault("channels", {})[receipt["channel"]] = mark
    contract.save_index(root, index)


# ---------------------------------------------------------------------- index-only operations


def _need_yes(yes: bool, result: dict[str, Any]) -> dict[str, Any] | None:
    if yes:
        return None
    return {**result, "written": False, "message": result.get("message", "Plan only: nothing was written. Pass --yes to do it.")}


def _write_entry(home: Path, store: Store, previous: Index, entry: dict[str, Any], change: dict[str, Any], command: str) -> dict[str, Any]:
    key = catalog_sign.load_or_create_key(home)
    at = utcnow()
    payload = catalog_sign.next_catalog(previous.payload, entry, channel=previous.channel, change=change, at=at)
    sequence = commit_index(store, previous, payload, key)
    _log(home, command, channel=previous.channel, story=entry["storyId"], release=entry["current"], sequence=sequence, objects=0, bytes=0)
    _save_state(home, store, [Index(previous.channel, payload, None, sequence)])
    return {"written": True, "sequence": sequence, "channel": previous.channel, "story": entry["storyId"],
            "release": entry["current"], "status": entry["status"], "change": payload["changes"][0], "uploaded": 0}  # fmt: skip


def promote(story_id: str, *, release: str | None = None, yes: bool, home: Path, client: Any = None,
            cfg: R2Config | None = None) -> dict[str, Any]:  # fmt: skip
    """Make a release already on staging current on production. Every object it needs is checked first; nothing
    is uploaded but the production index."""
    store = _store(home, client, cfg)
    store.preflight()
    staging = read_index(store, STAGING, home)
    entry = staging.story(story_id)
    if entry is None:
        raise NotFoundError(f"{story_id} is not on staging", hint="Publish it first (`backend publish STORY --yes`)")
    rid = release or entry["current"]
    ref = next((r for r in entry["releases"] if r["releaseId"] == rid), None)
    if ref is None:
        raise PolicyError(f"{rid} is not published on staging", hint="Publish it to staging first", listed=[r["releaseId"] for r in entry["releases"]])
    walk = walk_release(store, ref, story_id, _maybe_local(home))
    missing = [p["key"] for p in walk.problems] + [k for k, d in walk.objects.items()
                                                    if _check_object(store, d, False, walk.fetched.get(k)) != "ok"]  # fmt: skip
    production = read_index(store, PRODUCTION, home)
    old = production.story(story_id)
    result = {"story": story_id, "release": rid, "channel": PRODUCTION, "objects": len(walk.objects), "missing": missing,
              "from_release": old["current"] if old else None, "current_sequence": production.sequence or None,
              "next_sequence": production.sequence + 1, "uploads": 0,
              "promotable": not missing and walk.tree is not None and not (old and old["current"] == rid)}  # fmt: skip
    if old and old["current"] == rid:
        result["message"] = f"{rid} is already current on production"
    if (plan_only := _need_yes(yes, result)) is not None:
        return plan_only
    if missing or walk.tree is None:
        raise PolicyError(f"{rid} is incomplete in the bucket; nothing was promoted", missing=missing[:20],
                          hint="Run `backend verify` and `backend repair --yes`")  # fmt: skip
    if old and old["current"] == rid:
        return {**result, "written": False}
    new_entry = _entry_from(walk, ref, old, store)
    change = {"type": "promoted", "storyId": story_id, "releaseId": rid, "fromReleaseId": old["current"] if old else None}
    return {**result, **_write_entry(home, store, production, new_entry, change, "promote")}


def rollback(story_id: str, release: str, *, channel: str, yes: bool, home: Path, client: Any = None,
             cfg: R2Config | None = None) -> dict[str, Any]:  # fmt: skip
    """A new sequence on `channel` pointing the story at an older release already listed there."""
    store = _store(home, client, cfg)
    store.preflight()
    idx = read_index(store, _channel(channel), home)
    entry = idx.story(story_id)
    if entry is None:
        raise NotFoundError(f"{story_id} is not on {channel}")
    ref = next((r for r in entry["releases"] if r["releaseId"] == release), None)
    if ref is None:
        raise PolicyError(f"{release} was never published on {channel}", listed=[r["releaseId"] for r in entry["releases"]])
    walk = walk_release(store, ref, story_id, _maybe_local(home))
    missing = [p["key"] for p in walk.problems] + [k for k, d in walk.objects.items()
                                                    if _check_object(store, d, False, walk.fetched.get(k)) != "ok"]  # fmt: skip
    result = {"story": story_id, "release": release, "channel": channel, "from_release": entry["current"], "missing": missing,
              "current_sequence": idx.sequence, "next_sequence": idx.sequence + 1, "uploads": 0}  # fmt: skip
    if entry["current"] == release:
        result["message"] = f"{release} is already current on {channel}"
    if (plan_only := _need_yes(yes, result)) is not None:
        return plan_only
    if missing or walk.tree is None:
        raise PolicyError(f"{release} is incomplete in the bucket; nothing changed", missing=missing[:20])
    if entry["current"] == release:
        return {**result, "written": False}
    new_entry = _entry_from(walk, ref, entry, store)
    change = {"type": "rollback", "storyId": story_id, "releaseId": release, "fromReleaseId": entry["current"]}
    return {**result, **_write_entry(home, store, idx, new_entry, change, "rollback")}


def set_listed(story_id: str, *, listed: bool, channel: str, yes: bool, home: Path, client: Any = None,
               cfg: R2Config | None = None) -> dict[str, Any]:  # fmt: skip
    """Unlist (hide from the shelf; the entry and saves keep working) or relist a story on `channel`."""
    store = _store(home, client, cfg)
    store.preflight()
    idx = read_index(store, _channel(channel), home)
    entry = idx.story(story_id)
    if entry is None:
        raise NotFoundError(f"{story_id} is not on {channel}")
    status = "live" if listed else "unlisted"
    result = {"story": story_id, "channel": channel, "status_from": entry["status"], "status_to": status,
              "current_sequence": idx.sequence, "next_sequence": idx.sequence + 1}  # fmt: skip
    if entry["status"] == status:
        result["message"] = f"{story_id} is already {status} on {channel}"
    if (plan_only := _need_yes(yes, result)) is not None:
        return plan_only
    if entry["status"] == status:
        return {**result, "written": False}
    change = {"type": "story_relisted" if listed else "story_unlisted", "storyId": story_id, "releaseId": entry["current"]}
    return {**result, **_write_entry(home, store, idx, {**entry, "status": status}, change, "relist" if listed else "unlist")}


def _maybe_local(home: Path) -> dict[str, Path]:
    try:
        return local_objects(home)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------- verify, repair


PublicFetch = Callable[[str, str], tuple[int, bytes, dict[str, str]]]  # (url, method) → (status, body, headers)


def https_fetch(url: str, method: str = "GET") -> tuple[int, bytes, dict[str, str]]:
    """Anonymous HTTPS, as the app does (no credentials)."""
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "assetforge-verify"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - the configured public origin
            return response.status, response.read() if method == "GET" else b"", {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, b"", {}
    except urllib.error.URLError as exc:
        return 0, str(exc.reason).encode(), {}


def verify(*, home: Path, channel: str | None = None, deep: bool = False, public: bool = False, client: Any = None,
           cfg: R2Config | None = None, public_fetch: PublicFetch | None = None) -> dict[str, Any]:  # fmt: skip
    """HEAD every object any listed release needs (`deep`: download and hash; `public`: anonymous HTTPS through
    `ASSETFORGE_R2_PUBLIC_BASE`). Reads only."""
    store = _store(home, client, cfg)
    store.preflight()
    local = _maybe_local(home)
    channels = [_channel(channel)] if channel else list(CHANNELS)
    report: dict[str, Any] = {"channels": {}, "bucket": store.bucket, "prefix": store.prefix, "deep": deep}
    wanted: dict[str, dict[str, Any]] = {}
    state: dict[str, str] = {}
    problems: list[dict[str, Any]] = []
    walked: set[str] = set()
    indexes = []
    for ch in channels:
        idx = read_index(store, ch, home)
        indexes.append(idx)
        stories = (idx.payload or {}).get("stories", [])
        report["channels"][ch] = {"sequence": idx.sequence, "stale_pointer": idx.stale_pointer, "stories": len(stories),
                                  "releases": sum(len(s["releases"]) for s in stories)}  # fmt: skip
        for story in stories:
            for ref in story["releases"]:
                if ref["manifest"]["key"] in walked:
                    continue
                walked.add(ref["manifest"]["key"])
                walk = walk_release(store, ref, story["storyId"], local)
                problems += walk.problems
                for k, d in walk.objects.items():
                    wanted[k] = d
                    if k in walk.fetched:
                        state[k] = walk.fetched[k]
    for k, d in sorted(wanted.items()):
        if k not in state:
            state[k] = _check_object(store, d, deep, None)
    rows = [{"key": k, "state": state[k], "bytes": wanted[k]["bytes"], "sha256": wanted[k]["sha256"],
             "mediaType": wanted[k]["mediaType"]} for k in sorted(wanted)]  # fmt: skip
    report["objects"] = len(rows)
    report["ok"] = sum(r["state"] == "ok" for r in rows)
    report["missing"] = [r for r in rows if r["state"] == "missing"]
    report["corrupted"] = [r for r in rows if r["state"] == "corrupted"]
    report["unreadable"] = problems
    if public:
        report["public"] = _verify_public(store, indexes, sorted(wanted), home, public_fetch or https_fetch)
    report["healthy"] = not report["missing"] and not report["corrupted"] and not problems and (
        not public or report["public"].get("ok", False))  # fmt: skip
    return report


def _verify_public(store: Store, indexes: list[Index], keys: list[str], home: Path, fetch: PublicFetch) -> dict[str, Any]:
    if not store.cfg.public_base:
        return {"ok": False, "error": "ASSETFORGE_R2_PUBLIC_BASE is not set", "hint": "Set it to the bucket's public origin"}
    out: dict[str, Any] = {"ok": True, "indexes": {}, "unreachable": []}
    for idx in indexes:
        url = store.cfg.public_url(index_key(idx.channel)) or ""
        status, body, _ = fetch(url, "GET")
        seen: dict[str, Any] = {"url": url, "status": status}
        if status == 200:
            try:
                seen["sequence"] = _verify(body, home, idx.channel, url)["catalogSequence"]
            except (PolicyError, ValueError) as exc:
                seen["error"] = str(exc)
        out["indexes"][idx.channel] = seen
        if idx.payload is not None and seen.get("sequence") != idx.sequence:
            out["ok"] = False
    for key in keys:
        status, _, _ = fetch(store.cfg.public_url(key) or "", "HEAD")
        if status != 200:
            out["unreachable"].append({"key": key, "status": status})
    out["ok"] = out["ok"] and not out["unreachable"]
    return out


def repair(*, home: Path, yes: bool, client: Any = None, cfg: R2Config | None = None, deep: bool = False) -> dict[str, Any]:
    """Re-upload objects `verify` found missing or corrupted, only from a local copy with the declared digest."""
    store = _store(home, client, cfg)
    report = verify(home=home, deep=deep, client=store.client, cfg=store.cfg)
    local = _maybe_local(home)
    fixable, unfixable = [], []
    for row in report["missing"] + report["corrupted"]:
        path = local.get(row["key"])
        if path is not None and path.is_file():
            data = path.read_bytes()
            if len(data) == row["bytes"] and cm.sha256(data) == row["sha256"]:
                fixable.append({**row, "path": str(path)})
                continue
        unfixable.append({**row, "reason": "no local copy with the declared digest"})
    result = {"fixable": fixable, "unfixable": unfixable, "verified": {k: report[k] for k in ("objects", "ok")}}
    if (plan_only := _need_yes(yes, result)) is not None:
        return plan_only
    for row in fixable:
        store.put(row["key"], Path(row["path"]).read_bytes(), row["mediaType"], IMMUTABLE)
    if fixable:
        _log(home, "repair", objects=len(fixable), bytes=sum(r["bytes"] for r in fixable), keys=[r["key"] for r in fixable][:50])
    return {**result, "written": bool(fixable), "repaired": len(fixable)}


# ---------------------------------------------------------------------- inventory, gc


def inventory(*, home: Path, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    """Every object under the prefix, marked referenced (by some index in some channel's history, or an index
    file itself) or orphaned. `complete` is False when some index or release could not be read: then nothing can
    be called an orphan with certainty."""
    store = _store(home, client, cfg)
    store.preflight()
    listing = store.list()
    local = _maybe_local(home)
    referenced: set[str] = set()
    unreadable: list[dict[str, Any]] = []
    walked: set[str] = set()
    indexes: list[str] = sorted({r["key"] for r in listing if _HISTORY.match(r["key"])} | {index_key(c) for c in CHANNELS})
    for key in indexes:
        data = store.get(key)
        if data is None:
            continue
        referenced.add(key)
        channel = key.split("/")[1]
        try:
            payload = _verify(data, home, channel, key) if channel in CHANNELS else None
        except (PolicyError, ValueError) as exc:
            unreadable.append({"key": key, "problem": str(exc)})
            continue
        for story in (payload or {}).get("stories", []):
            for ref in story["releases"]:
                if ref["manifest"]["key"] in walked:
                    continue
                walked.add(ref["manifest"]["key"])
                walk = walk_release(store, ref, story["storyId"], local)
                unreadable += walk.problems
                referenced |= set(walk.objects)
                if story.get("thumbnail"):
                    referenced.add(story["thumbnail"]["key"])
    rows = [{**r, "referenced": r["key"] in referenced} for r in sorted(listing, key=lambda r: r["key"])]
    return {"bucket": store.bucket, "prefix": store.prefix, "objects": rows, "count": len(rows),
            "bytes": sum(r["bytes"] for r in rows), "orphans": [r for r in rows if not r["referenced"]],
            "unreadable": unreadable, "complete": not unreadable, "_store": store}  # fmt: skip


def ls(*, home: Path, orphans: bool = False, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    inv = inventory(home=home, client=client, cfg=cfg)
    inv.pop("_store")
    if orphans:
        inv["objects"] = inv["orphans"]
    return inv


def gc(*, home: Path, yes: bool = False, delete_orphans: bool = False, whole_bucket: bool = False, client: Any = None,
       cfg: R2Config | None = None) -> dict[str, Any]:  # fmt: skip
    """Delete orphans only (under `blobs/` or `stories/`, inside the prefix); never an object any index in any
    history references. A dry run unless `yes` and `delete_orphans`; refused when the prefix is empty unless
    `whole_bucket` says the whole bucket is ours, and refused when any index or release could not be read."""
    inv = inventory(home=home, client=client, cfg=cfg)
    store: Store = inv.pop("_store")
    deletable = [r for r in inv["orphans"] if r["key"].startswith(GC_NAMESPACES)]
    kept = [r for r in inv["orphans"] if not r["key"].startswith(GC_NAMESPACES)]
    result = {"bucket": store.bucket, "prefix": store.prefix, "orphans": deletable, "left_alone": kept,
              "bytes": sum(r["bytes"] for r in deletable), "complete": inv["complete"], "unreadable": inv["unreadable"]}  # fmt: skip
    if not (yes and delete_orphans):
        return {**result, "written": False, "message": "Dry run: nothing was deleted. Pass --yes --delete-orphans to delete."}
    if not store.prefix and not whole_bucket:
        raise PolicyError("Refusing to delete with no key prefix: the bucket may hold other products' objects",
                          hint="Set ASSETFORGE_R2_PREFIX, or pass --whole-bucket if the entire bucket belongs to AssetForge")  # fmt: skip
    if not inv["complete"]:
        raise PolicyError("Some indexes or releases could not be read; orphans are uncertain, nothing was deleted",
                          unreadable=inv["unreadable"][:20])  # fmt: skip
    for row in deletable:
        store.delete(row["key"])
    if deletable:
        _log(home, "gc", objects=len(deletable), bytes=result["bytes"], keys=[r["key"] for r in deletable][:50])
    return {**result, "written": bool(deletable), "deleted": len(deletable)}


# ---------------------------------------------------------------------- status, pull


def _local_stories(home: Path) -> dict[str, dict[str, Any]]:
    """storyId → {registered id, local latest release, title} for every registered story with compiled releases."""
    from assetforge.story.project import registered

    out: dict[str, dict[str, Any]] = {}
    for sid, root in registered(home).items():
        index = contract.load_index(Path(root) / "releases")
        releases = index.get("releases") or []
        if not releases and not index.get("storyId"):
            continue
        story_id = index.get("storyId") or sid
        out[story_id] = {"registered": sid, "root": str(root), "local_latest": releases[-1]["releaseId"] if releases else None,
                         "local_valid": bool(releases[-1].get("valid")) if releases else None}  # fmt: skip
    return out


def _story_state(local: str | None, staging: dict[str, Any] | None, production: dict[str, Any] | None) -> str:
    if staging is None and production is None:
        return "never published"
    if staging is not None and staging["status"] == "unlisted":
        return "unlisted on staging"
    if local and staging and local > staging["current"]:
        return "local ahead of staging"
    if staging and (production is None or production["current"] != staging["current"]):
        return "staging ahead of production"
    if production is not None and production["status"] == "unlisted":
        return "unlisted on production"
    return "in sync"


def status(*, home: Path, channel: str | None = None, client: Any = None, cfg: R2Config | None = None,
           save: bool = False, live: bool = True) -> dict[str, Any]:  # fmt: skip
    """Configuration, reachability, each channel's sequence and every story (local latest, staging, production,
    state). Reads only (with `save`, refreshes the local `backend/state.json` view; `live=False` touches no
    network and answers from that view)."""
    out: dict[str, Any] = {"connection": {"ok": False}, "channels": {}, "source": "live" if live else "cache"}
    pub = catalog_sign.key_paths(home)[1]
    out["key"] = None
    if pub.exists():
        public = catalog_sign.load_public(home)
        out["key"] = {"key_id": catalog_sign.key_id(public), "public_key": pub.read_text().strip(), "path": str(pub)}
    indexes: dict[str, Index] = {}
    store: Store | None = None
    try:
        store = _store(home, client, cfg)
        out["config"] = store.cfg.describe()
        if not live:
            raise _CacheOnly
        store.preflight()
        for ch in ([_channel(channel)] if channel else CHANNELS):
            indexes[ch] = read_index(store, ch, home)
        out["connection"] = {"ok": True}
    except _CacheOnly:
        out["connection"] = {"ok": None, "checked": False}
    except AssetForgeError as exc:
        out.setdefault("config", {"configured": False})
        out["connection"] = {"ok": False, "error": exc.to_dict(), "fix": exc.hint}
        out["source"] = "cache"
    state = load_state(home)
    cfg_now = store.cfg if store else None
    for ch in [_channel(channel)] if channel else CHANNELS:
        idx = indexes.get(ch)
        cached = state["channels"].get(ch) or {}
        payload = idx.payload if idx else cached.get("payload")
        out["channels"][ch] = {"sequence": idx.sequence if idx else cached.get("sequence"), "generatedAt": (payload or {}).get("generatedAt"),
                               "index_key": index_key(ch), "index_url": cfg_now.public_url(index_key(ch)) if cfg_now else None,
                               "stale_pointer": idx.stale_pointer if idx else None, "seen_at": None if idx else cached.get("seen_at"),
                               "stories": (payload or {}).get("stories", []), "changes": (payload or {}).get("changes", [])}  # fmt: skip
    local = _local_stories(home)
    by_channel = {ch: {s["storyId"]: s for s in v["stories"]} for ch, v in out["channels"].items()}
    ids = sorted(set(local) | {sid for v in by_channel.values() for sid in v})
    rows = []
    for sid in ids:
        st, pr = by_channel.get(STAGING, {}).get(sid), by_channel.get(PRODUCTION, {}).get(sid)
        loc = local.get(sid, {})
        rows.append({"storyId": sid, "title": (st or pr or {}).get("title"), "registered": loc.get("registered"),
                     "local_latest": loc.get("local_latest"), "local_valid": loc.get("local_valid"),
                     "staging": st["current"] if st else None, "staging_status": st["status"] if st else None,
                     "production": pr["current"] if pr else None, "production_status": pr["status"] if pr else None,
                     "staging_releases": [r["releaseId"] for r in st["releases"]] if st else [],
                     "production_releases": [r["releaseId"] for r in pr["releases"]] if pr else [],
                     "state": _story_state(loc.get("local_latest"), st, pr)})  # fmt: skip
    out["stories"] = rows
    out["cached_at"] = state.get("updated_at")
    out["log"] = read_log(home, 20)
    if save and store is not None and indexes:
        _save_state(home, store, list(indexes.values()))
    return out


class _CacheOnly(Exception):
    """status(live=False): skip the network."""


def pull(*, home: Path, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    """Rebuild the local published-state records from the bucket: `backend/state.json` and, for every registered
    story, which of its local releases each channel lists (index.json `channels`)."""
    store = _store(home, client, cfg)
    store.preflight()
    indexes = [read_index(store, ch, home) for ch in CHANNELS]
    _save_state(home, store, indexes)
    updated = []
    for story_id, loc in _local_stories(home).items():
        root = Path(loc["root"]) / "releases"
        index = contract.load_index(root)
        changed = False
        for entry in index.get("releases", []):
            marks = {}
            for idx in indexes:
                story = idx.story(story_id)
                ref = next((r for r in (story or {}).get("releases", []) if r["releaseId"] == entry["releaseId"]), None)
                if ref is not None:
                    marks[idx.channel] = {"sequence": idx.sequence, "at": ref["publishedAt"], "bucket": store.bucket,
                                          "channel": idx.channel, "current": story["current"] == entry["releaseId"]}  # fmt: skip
            if marks != entry.get("channels", {}):
                entry["channels"] = marks
                if marks and not entry.get("published"):
                    first = marks.get(STAGING) or next(iter(marks.values()))
                    entry["published"] = {k: first[k] for k in ("sequence", "at", "bucket", "channel")}
                changed = True
        if changed:
            contract.save_index(root, index)
            updated.append(story_id)
    return {"channels": {i.channel: i.sequence for i in indexes}, "stories_updated": updated,
            "state": str(backend_dir(home) / "state.json")}  # fmt: skip


# ---------------------------------------------------------------------- client kit


def schemas() -> dict[str, dict[str, Any]]:
    """JSON Schema of every document kind, from the pydantic models (by alias)."""
    models = {"vn.catalog": cm.Catalog, "vn.catalog.envelope": cm.Envelope, "vn.release": cm.Release,
              "vn.chapter.bundle": cm.Bundle, "vn.chapter": cm.Graph, "vn.presentation": cm.Presentation,
              "vn.assets": cm.AssetRegistry, "vn.locale": cm.Locale, "vn.downloads": cm.Downloads,
              "vn.migration": cm.Migration, "vn.delta": cm.Delta}  # fmt: skip
    return {kind: model.model_json_schema(by_alias=True) for kind, model in models.items()}


def _sample_index() -> dict[str, Any]:
    d = lambda key, n: {"key": key, "mediaType": cm.media_type(key), "bytes": n, "sha256": "0" * 64}  # noqa: E731
    base = "stories/sample-story/releases"
    sample = {"kind": "vn.catalog", "schemaVersion": cm.SCHEMA_VERSION, "channel": "staging", "catalogSequence": 7,
              "generatedAt": "2026-11-12T09:30:00Z",
              "stories": [{"storyId": "sample-story", "status": "live", "title": "Sample Story", "summary": "One line.",
                           "defaultLocale": "en", "locales": ["en"], "thumbnail": d("blobs/sha256/" + "0" * 64 + ".webp", 48210),
                           "current": "r0002", "addedSequence": 3, "updatedSequence": 7, "updatedAt": "2026-11-12T09:30:00Z",
                           "chapterCount": 13, "downloadBytes": {"core": 812004, "standard": 5120330},
                           "releases": [{"releaseId": "r0001", "graphId": "sample-story.graph.1", "minEngineVersion": cm.ENGINE_VERSION,
                                         "publishedAt": "2026-11-10T08:00:00Z", "manifest": d(f"{base}/r0001/release.json", 4410)},
                                        {"releaseId": "r0002", "graphId": "sample-story.graph.1", "minEngineVersion": cm.ENGINE_VERSION,
                                         "publishedAt": "2026-11-12T09:30:00Z", "manifest": d(f"{base}/r0002/release.json", 4420),
                                         "delta": d(f"{base}/r0002/delta-from-r0001.json", 910)}]}],
              "changes": [{"sequence": 7, "at": "2026-11-12T09:30:00Z", "type": "release_published", "storyId": "sample-story",
                           "releaseId": "r0002", "fromReleaseId": "r0001",
                           "summary": {"chaptersAdded": 0, "chaptersChanged": 2, "chaptersRemoved": 0, "textOnly": True,
                                       "graphChanged": False, "downloadBytes": 18240}}]}  # fmt: skip
    cm.Catalog.model_validate(sample)
    return sample


def _sample_delta() -> dict[str, Any]:
    base = "stories/sample-story/releases/r0002"
    sample = {"kind": "vn.delta", "schemaVersion": cm.SCHEMA_VERSION, "storyId": "sample-story", "fromRelease": "r0001",
              "toRelease": "r0002", "fromGraphId": "sample-story.graph.1", "toGraphId": "sample-story.graph.1", "graphChanged": False,
              "migration": {"key": f"{base}/migration-from-r0001.json", "mediaType": "application/json", "bytes": 120, "sha256": "0" * 64},
              "chapters": {"added": [], "removed": [], "unchanged": ["ch03"],
                           "changed": [{"id": "ch01", "documents": ["locales/en"], "textOnly": True, "bytes": 9120}]},
              "releaseDocuments": [], "blobs": {"added": [], "dropped": []}, "downloadBytes": {"core": 18240, "standard": 18240}}  # fmt: skip
    cm.Delta.model_validate(sample)
    return sample


def client_kit(*, home: Path, out: Path, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    """Write what the app team needs into `out` (local only): the public key, JSON Schemas, a sample index and
    delta, the reference client and INTEGRATION.md."""
    out = Path(out).expanduser().resolve()
    (out / "schemas").mkdir(parents=True, exist_ok=True)
    (out / "samples").mkdir(parents=True, exist_ok=True)
    written = []

    def write(rel: str, text: str) -> None:
        (out / rel).write_text(text)
        written.append(rel)

    pub = catalog_sign.key_paths(home)[1]
    key_info = None
    if pub.exists():
        public = catalog_sign.load_public(home)
        key_info = {"keyId": catalog_sign.key_id(public), "publicKey": pub.read_text().strip(), "alg": "Ed25519",
                    "protocolPrefix": catalog_sign.PROTOCOL_PREFIX.decode()}  # fmt: skip
        write("public-key.json", json.dumps(key_info, indent=2) + "\n")
    for kind, schema in schemas().items():
        write(f"schemas/{kind}.schema.json", json.dumps(schema, indent=2) + "\n")
    write("samples/index.sample.json", json.dumps(_sample_index(), indent=2) + "\n")
    write("samples/delta.sample.json", json.dumps(_sample_delta(), indent=2) + "\n")
    shutil.copyfile(Path(__file__).with_name("client.py"), out / "client.py")
    written.append("client.py")
    urls = {}
    try:
        c = cfg or r2.resolve_config(home)
        urls = {ch: c.public_url(index_key(ch)) for ch in CHANNELS}
    except AssetForgeError:
        pass
    write("INTEGRATION.md", _integration(key_info, urls))
    warnings = [] if key_info else ["No catalog key yet: it is made on the first publish; run client-kit again after it."]
    return {"out": str(out), "files": sorted(written), "public_key": key_info, "index_urls": urls, "warnings": warnings}


def _integration(key: dict[str, Any] | None, urls: dict[str, str | None]) -> str:
    key_line = f"`{key['publicKey']}` (keyId `{key['keyId']}`, also in `public-key.json`)" if key else "not made yet (first publish)"
    url_lines = "\n".join(f"- {ch}: `{u}`" for ch, u in urls.items() if u) or "- set `ASSETFORGE_R2_PUBLIC_BASE` and run client-kit again"
    return f"""# Visual-novel content: integration guide

The content lives in a bucket behind a public origin. There is no server: one signed index per channel says which
stories exist and which release of each is current; everything else is immutable and reached through descriptors
`{{key, mediaType, bytes, sha256}}`, so every byte can be verified. The app never lists the bucket and needs no
credentials. `client.py` is a working reference implementation (stdlib + `cryptography`).

## Ship with the app

- Public key: {key_line}. Ed25519 over `"{catalog_sign.PROTOCOL_PREFIX.decode().strip()}\\n"` + the exact payload bytes.
- The channel: test builds read `staging`, the shipped app reads `production`.
- Index URLs (relative keys are appended to the same origin and prefix):
{url_lines}

## Sync algorithm

1. `GET catalog/<channel>/current.json` with `If-None-Match: <last ETag>`. 304: nothing to do.
2. Parse the envelope `{{alg, keyId, payload, signature}}`. Reject when `keyId` is unknown, the signature does not
   verify, the payload fails `schemas/vn.catalog.schema.json`, `channel` is not yours, or `catalogSequence` is lower
   than the one you already accepted (a replay). On any failure keep using the last good index.
3. For each story whose `updatedSequence` is greater than the last sequence you processed: `status: "unlisted"`
   hides it from the shelf (keep installs and saves working). Otherwise compare `current` with the installed
   release. If the current release's `delta.fromRelease` is your installed release, fetch the delta
   (`schemas/vn.delta.schema.json`); otherwise fetch the new `release.json` and compare descriptors by `sha256`.
4. Always fetch the new `release.json`, each chapter's `bundle.json` and `downloads.json` (they name the release, so
   they are new every time; they are small). Download only content documents (`graph`, `presentation`, `assets`,
   `locales/<lang>`) and blobs whose digest you do not have, into temporary files; check size and SHA-256; then
   move them into a cache keyed by digest. Activate the release only when its required set is complete.
5. Record the processed sequence. A rollback is just a newer sequence pointing at an older release; with a
   digest-keyed cache it downloads nothing.

`changes` (newest first, at most 50: story_added, release_published, rollback, story_unlisted, story_relisted,
promoted) is for a "What's new" screen; the truth is the story entries.

## Cache headers

- `catalog/<channel>/current.json`: `Cache-Control: no-cache` (revalidate with the ETag every time).
- Everything else: `public, max-age=31536000, immutable`. Keys never get new content.

## Failures

- Network error, 5xx, bad signature, schema failure, older sequence: keep the last good index and cache; retry later.
- A downloaded object whose size or digest differs: discard it, keep the old release active, retry later.
- A release needing a newer engine (`minEngineVersion`): keep the installed one.
- Saves carry the `graphId`; a new release's `migration-from-<old>.json` maps vanished node ids.

## What is and is not enforced

- Enforced by cryptography: who published the index (signature) and what every object is (digests).
- Not enforced: access. Chapters carry `accessPolicyId`, but a public bucket serves every object to anyone who
  knows the key; nothing enforces paid or scheduled content without a gateway (a Worker in front of a private
  bucket). Hiding (`unlisted`) only hides from honest clients.
"""
