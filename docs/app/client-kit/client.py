"""Reference client for the visual-novel content backend (docs/narrative/27). Dependency-free: the standard library
plus `cryptography` for Ed25519 verification only. It is what an app does, written so it can be tested: if it stays
in sync, an app can. It never needs credentials and never lists the bucket; it only GETs keys.

    client = Client(fetch=https_fetcher("https://cdn.example.com/vn"), public_key="<base64 raw key>",
                    cache_dir=Path("cache"), channel="production")
    result = client.sync()

`fetch(key, etag)` returns `(status, body, etag)`: 200 with the bytes, 304 when `etag` still matches, 404 when
missing. Steps:

1. GET `catalog/<channel>/current.json` with If-None-Match; 304 → nothing to do.
2. Verify the envelope (key id, signature, schema, channel); reject a lower `catalogSequence` (replay). On any
   failure the last good index stays in use.
3. For each story whose `updatedSequence` is above the last processed sequence: unlisted → hidden; otherwise
   plan the update from the delta (when it starts at the installed release) or by comparing descriptors.
4. Download only what is missing by digest into temporary files, check size and SHA-256, move into the cache;
   activate the release only when its required set is complete.
5. Record the processed sequence. A rollback is another sequence pointing at an older release.

Cache layout: `state.json`, `objects/<sha256>` (every document and blob by digest), `releases/<story>.json`
(the active release: key → sha256 of its required set).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROTOCOL_PREFIX = b"assetforge.vn.catalog.v2\n"
CONTENT = ("graph", "presentation", "assets")
Fetch = Callable[[str, "str | None"], "tuple[int, bytes, str | None]"]


class Rejected(Exception):
    """The index cannot be trusted; the last good one stays in use."""


USER_AGENT = "AssetForgeClient/1.0"  # the CDN blocks urllib's default `Python-urllib/3.x` agent (HTTP 403)


def https_fetcher(base_url: str, timeout: float = 20.0, user_agent: str = USER_AGENT) -> Fetch:
    """`fetch` over anonymous HTTPS: `base_url` is the public origin plus the prefix. Always sends an explicit
    User-Agent: the content CDN refuses urllib's default one."""
    base = base_url.rstrip("/")

    def fetch(key: str, etag: str | None = None) -> tuple[int, bytes, str | None]:
        headers = {"User-Agent": user_agent, **({"If-None-Match": etag} if etag else {})}
        request = urllib.request.Request(f"{base}/{key}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - the content origin
                return response.status, response.read(), response.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            return exc.code, b"", None

    return fetch


def key_id(raw_public: bytes) -> str:
    return hashlib.sha256(raw_public).hexdigest()[:16]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_descriptor(d: Any) -> None:
    if not (isinstance(d, dict) and isinstance(d.get("key"), str) and isinstance(d.get("bytes"), int)
            and isinstance(d.get("sha256"), str) and len(d["sha256"]) == 64):  # fmt: skip
        raise Rejected(f"bad descriptor {d!r}")
    key = d["key"]
    if key.startswith("/") or ".." in key.split("/") or "://" in key or "?" in key or "#" in key:
        raise Rejected(f"unsafe key {key!r}")


def check_index(payload: Any, channel: str) -> None:
    """The structural part of `schemas/vn.catalog.schema.json` an app must not skip."""
    if not isinstance(payload, dict) or payload.get("kind") != "vn.catalog" or payload.get("schemaVersion") != "2.0":
        raise Rejected("not a vn.catalog 2.0 payload")
    if payload.get("channel") != channel:
        raise Rejected(f"index for channel {payload.get('channel')!r}, expected {channel!r}")
    if not isinstance(payload.get("catalogSequence"), int) or payload["catalogSequence"] < 1:
        raise Rejected("bad catalogSequence")
    for story in payload.get("stories", None) or []:
        for field in ("storyId", "status", "current", "updatedSequence", "releases"):
            if field not in story:
                raise Rejected(f"story entry without {field}")
        if story["status"] not in ("live", "unlisted"):
            raise Rejected(f"unknown status {story['status']!r}")
        for ref in story["releases"]:
            _check_descriptor(ref.get("manifest"))
            if ref.get("delta") is not None:
                _check_descriptor(ref["delta"])
        if story["current"] not in {r.get("releaseId") for r in story["releases"]}:
            raise Rejected(f"{story['storyId']}: current release is not listed")
    if not isinstance(payload.get("stories"), list):
        raise Rejected("stories missing")


def verify_envelope(data: bytes, public_key: bytes, channel: str) -> dict[str, Any]:
    try:
        env = json.loads(data)
        if env.get("alg") != "Ed25519":
            raise Rejected("unknown signature algorithm")
        if env.get("keyId") != key_id(public_key):
            raise Rejected("signed by an unknown key")
        payload_bytes = base64.b64decode(env["payload"], validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(base64.b64decode(env["signature"], validate=True),
                                                              PROTOCOL_PREFIX + payload_bytes)  # fmt: skip
        payload = json.loads(payload_bytes)
    except InvalidSignature as exc:
        raise Rejected("the signature does not verify") from exc
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise Rejected(f"malformed envelope: {exc}") from exc
    check_index(payload, channel)
    return payload


# ---------------------------------------------------------------------- release trees


def tree_of(release: dict[str, Any], bundles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The documents of a release: structure (release-specific manifests: bundles, download plans, migrations),
    content documents (per chapter and release-level), and the cover blob, each as key → descriptor. The
    profile's blobs are added by the caller from the asset registries."""
    structure: dict[str, Any] = {}
    content: dict[str, Any] = {}
    blobs: dict[str, Any] = {}
    chapters: dict[str, dict[str, dict[str, Any]]] = {}
    release_docs = {f"locales/{code}": d for code, d in release.get("locales", {}).items()}
    content |= {d["key"]: d for d in release_docs.values()}
    for d in release.get("migrations", []):
        structure[d["key"]] = d
    if release.get("cover"):
        blobs[release["cover"]["file"]["key"]] = release["cover"]["file"]
    for ref in release["chapters"]:
        bundle = bundles[ref["id"]]
        structure[ref["manifest"]["key"]] = ref["manifest"]
        structure[bundle["downloads"]["key"]] = bundle["downloads"]
        docs = {n: bundle[n] for n in CONTENT} | {f"locales/{c}": d for c, d in bundle["locales"].items()}
        chapters[ref["id"]] = docs
        content |= {d["key"]: d for d in docs.values()}
    return {"structure": structure, "content": content, "chapters": chapters, "release_docs": release_docs, "blobs": blobs}


class Client:
    def __init__(self, fetch: Fetch, public_key: str | bytes, cache_dir: str | Path, *, channel: str = "production",
                 profile: str = "standard", audio: tuple[str, ...] = ("ogg", "mp4")) -> None:  # fmt: skip
        self.fetch = fetch
        self.public_key = base64.b64decode(public_key) if isinstance(public_key, str) else bytes(public_key)
        self.cache = Path(cache_dir)
        self.channel, self.profile, self.audio = channel, profile, audio
        (self.cache / "objects").mkdir(parents=True, exist_ok=True)
        (self.cache / "releases").mkdir(parents=True, exist_ok=True)
        self.state = self._load("state.json") or {"etag": None, "sequence": 0, "processed": 0, "index": None, "stories": {}}
        self.downloaded: dict[str, list[str]] = {}
        self.bytes = 0

    # ------------------------------------------------------------------ cache

    def _load(self, rel: str) -> Any:
        try:
            return json.loads((self.cache / rel).read_text())
        except (OSError, ValueError):
            return None

    def _save(self, rel: str, data: Any) -> None:
        path = self.cache / rel
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, path)

    def has(self, d: dict[str, Any]) -> bool:
        return (self.cache / "objects" / d["sha256"]).is_file()

    def read(self, d: dict[str, Any], category: str) -> bytes:
        """The verified bytes of a descriptor: from the cache by digest, else downloaded (temp file, size and
        digest checked, then moved in)."""
        path = self.cache / "objects" / d["sha256"]
        if path.is_file():
            return path.read_bytes()
        status, body, _ = self.fetch(d["key"], None)
        if status != 200:
            raise Rejected(f"{d['key']}: HTTP {status}")
        if len(body) != d["bytes"] or _sha(body) != d["sha256"]:
            raise Rejected(f"{d['key']}: size or digest does not match its descriptor")
        fd, tmp = tempfile.mkstemp(dir=self.cache / "objects", prefix=".dl-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.replace(tmp, path)
        self.downloaded.setdefault(category, []).append(d["key"])
        self.bytes += len(body)
        return body

    def json(self, d: dict[str, Any], category: str) -> dict[str, Any]:
        return json.loads(self.read(d, category))

    # ------------------------------------------------------------------ sync

    def sync(self) -> dict[str, Any]:
        self.downloaded, self.bytes = {}, 0
        result: dict[str, Any] = {"status": "unchanged", "sequence": self.state["sequence"], "new": [], "changed": [],
                                  "unlisted": [], "relisted": [], "errors": []}  # fmt: skip
        status, body, etag = self.fetch(f"catalog/{self.channel}/current.json", self.state.get("etag"))
        if status == 304:
            result["status"] = "not_modified"
            return result
        if status != 200:
            result.update(status="rejected", errors=[f"index: HTTP {status}"])
            return result
        try:
            payload = verify_envelope(body, self.public_key, self.channel)
            if payload["catalogSequence"] < self.state["sequence"]:
                raise Rejected(f"replayed index: sequence {payload['catalogSequence']} < {self.state['sequence']}")
        except Rejected as exc:
            result.update(status="rejected", errors=[str(exc)])
            return result
        self.state.update(index=payload, sequence=payload["catalogSequence"], etag=etag)
        processed = self.state["processed"]
        for story in payload["stories"]:
            if story["updatedSequence"] <= processed and story["storyId"] in self.state["stories"]:
                continue
            try:
                self._story(story, result)
            except Rejected as exc:  # keep the installed release; try again on the next sync
                result["errors"].append(f"{story['storyId']}: {exc}")
        if not result["errors"]:
            self.state["processed"] = payload["catalogSequence"]
        self._save("state.json", self.state)
        result.update(status="updated" if not result["errors"] else "partial", sequence=payload["catalogSequence"],
                      downloaded=self.downloaded, bytes=self.bytes)  # fmt: skip
        return result

    def _story(self, story: dict[str, Any], result: dict[str, Any]) -> None:
        sid = story["storyId"]
        mine = self.state["stories"].setdefault(sid, {"installed": None, "listed": None})
        was_listed = mine["listed"]
        mine["listed"] = story["status"] == "live"
        if not mine["listed"]:
            if was_listed:
                result["unlisted"].append(sid)
            return
        if was_listed is False:
            result["relisted"].append(sid)
        target = story["current"]
        if mine["installed"] == target:
            return
        ref = next(r for r in story["releases"] if r["releaseId"] == target)
        new = self._tree(ref["manifest"])
        installed = self._load(f"releases/{sid}.json")
        if installed and ref.get("delta") is not None:
            delta = self.json(ref["delta"], "manifests")
            if delta.get("fromRelease") == installed["release"] and delta.get("toRelease") == target:
                wanted = self.plan_from_delta(delta, new, installed)
            else:
                wanted = self.plan_from_manifests(installed, new)
        elif installed:
            wanted = self.plan_from_manifests(installed, new)
        else:
            wanted = {"documents": sorted(new["content"]), "blobs": sorted(new["blobs"])}
        for key in wanted["documents"]:
            self.read(new["content"][key], "documents")
        for key in wanted["blobs"]:
            self.read(new["blobs"][key], "blobs")
        every = {ref["manifest"]["key"]: ref["manifest"]} | new["structure"] | new["content"] | new["blobs"]
        for key, d in every.items():  # completeness: anything a plan missed is fetched now
            if not self.has(d):
                self.read(d, "documents" if key in new["content"] else "blobs" if key in new["blobs"] else "manifests")
        self._save(f"releases/{sid}.json", {"release": target, "objects": {k: d["sha256"] for k, d in sorted(every.items())},
                                            "content": {k: d["sha256"] for k, d in sorted(new["content"].items())},
                                            "blobs": {k: d["sha256"] for k, d in sorted(new["blobs"].items())}})  # fmt: skip
        (result["changed"] if mine["installed"] else result["new"]).append(sid)
        mine["installed"] = target

    def _tree(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Fetch the release manifest and its bundles (structure), then compute the required set."""
        release = self.json(manifest, "manifests")
        bundles = {ref["id"]: self.json(ref["manifest"], "manifests") for ref in release["chapters"]}
        tree = tree_of(release, bundles)
        for ref in release["chapters"]:
            b = bundles[ref["id"]]
            downloads = self.json(b["downloads"], "manifests")
            registry = self.json(b["assets"], "documents")
            plan = downloads["profiles"].get(self.profile) or downloads["profiles"]["standard"]
            for lid in plan["assets"]:
                asset = registry["assets"].get(lid)
                if asset is not None:
                    for d in self._pick(asset, plan["qualities"]):
                        tree["blobs"][d["key"]] = d
        return tree

    def _pick(self, asset: dict[str, Any], qualities: list[str]) -> list[dict[str, Any]]:
        out = []
        for q in qualities:
            variants = [v for v in asset["variants"] if v.get("quality", "standard") == q]
            if asset["type"] == "audio":
                variants.sort(key=lambda v: self.audio.index(v["container"]) if v["container"] in self.audio else 99)
            if variants:
                out.append(variants[0]["file"])
        return out

    # ------------------------------------------------------------------ the two planning paths (they must agree)

    @staticmethod
    def plan_from_delta(delta: dict[str, Any], new: dict[str, Any], installed: dict[str, Any]) -> dict[str, Any]:
        """Content documents of added/changed chapters and changed release documents, plus required blobs the
        delta adds (or that the installed release did not need)."""
        docs: set[str] = set()
        for cid in delta["chapters"]["added"]:
            docs |= {d["key"] for d in new["chapters"][cid].values()}
        for change in delta["chapters"]["changed"]:
            docs |= {new["chapters"][change["id"]][name]["key"] for name in change["documents"]}
        docs |= {new["release_docs"][name]["key"] for name in delta["releaseDocuments"] if name in new["release_docs"]}
        added = {b["key"] for b in delta["blobs"]["added"]}
        had = set(installed["blobs"])
        blobs = {k for k in new["blobs"] if k in added or k not in had}
        return {"documents": sorted(docs), "blobs": sorted(blobs)}

    @staticmethod
    def plan_from_manifests(installed: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        """Compare descriptors by digest with the installed release."""
        have = set(installed["objects"].values())
        return {"documents": sorted(k for k, d in new["content"].items() if d["sha256"] not in have),
                "blobs": sorted(k for k, d in new["blobs"].items() if d["sha256"] not in have and k not in installed["blobs"])}

    # ------------------------------------------------------------------ what the app shows

    def shelf(self) -> list[dict[str, Any]]:
        """Listed stories with an installed release."""
        index = self.state.get("index") or {}
        return [{"storyId": s["storyId"], "title": s.get("title"), "release": self.state["stories"].get(s["storyId"], {}).get("installed")}
                for s in index.get("stories", []) if s["status"] == "live"]  # fmt: skip

    def installed(self, story_id: str) -> dict[str, Any] | None:
        return self._load(f"releases/{story_id}.json")
