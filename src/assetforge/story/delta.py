"""What changed between two releases of a story (`vn.delta`), and how many bytes a release needs per download
profile. Pure functions over a `get(key) -> bytes` reader, so the same code reads a local release folder, the
documents a compile is about to write, or objects fetched from the bucket.

Classification (docs/narrative/27): a chapter's *content* documents are `graph`, `presentation`, `assets` and
`locales/<lang>`; they are compared by digest. `bundle.json` and `downloads.json` name the release in their keys,
so they are new in every release and count as structure (always fetched with the manifest, never "changed").
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from assetforge.story import contract_models as cm

Getter = Callable[[str], bytes]
CONTENT = ("graph", "presentation", "assets")
PROFILES = ("core", "standard")


@dataclass
class Tree:
    """The descriptors of one release, read through its documents."""

    release: dict[str, Any]
    manifest: dict[str, Any]  # descriptor of release.json
    chapters: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)  # cid → name → descriptor
    structure: dict[str, dict[str, Any]] = field(default_factory=dict)  # key → descriptor (bundles, downloads, migrations)
    release_docs: dict[str, dict[str, Any]] = field(default_factory=dict)  # "locales/<lang>" → descriptor
    registries: dict[str, dict[str, Any]] = field(default_factory=dict)  # cid → vn.assets document
    downloads: dict[str, dict[str, Any]] = field(default_factory=dict)  # cid → vn.downloads document
    blobs: dict[str, dict[str, Any]] = field(default_factory=dict)  # key → descriptor (every variant, cover)

    def content(self) -> dict[str, dict[str, Any]]:
        """Every content document descriptor by key."""
        out = {d["key"]: d for docs in self.chapters.values() for d in docs.values()}
        out |= {d["key"]: d for d in self.release_docs.values()}
        return out

    def digests(self) -> set[str]:
        every = [self.manifest, *self.structure.values(), *self.content().values(), *self.blobs.values()]
        return {d["sha256"] for d in every}


def release_key(story_id: str, release_id: str) -> str:
    return f"stories/{story_id}/releases/{release_id}/release.json"


def read_tree(get: Getter, story_id: str, release_id: str) -> Tree:
    key = release_key(story_id, release_id)
    data = get(key)
    release = json.loads(data)
    tree = Tree(release, cm.descriptor(key, data))
    for code, desc in release.get("locales", {}).items():
        tree.release_docs[f"locales/{code}"] = desc
    for desc in release.get("migrations", []):
        tree.structure[desc["key"]] = desc
    if release.get("cover"):
        tree.blobs[release["cover"]["file"]["key"]] = release["cover"]["file"]
    for ref in release["chapters"]:
        cid = ref["id"]
        tree.structure[ref["manifest"]["key"]] = ref["manifest"]
        bundle = json.loads(get(ref["manifest"]["key"]))
        docs = {name: bundle[name] for name in CONTENT}
        docs |= {f"locales/{code}": d for code, d in bundle["locales"].items()}
        tree.chapters[cid] = docs
        tree.structure[bundle["downloads"]["key"]] = bundle["downloads"]
        registry = json.loads(get(bundle["assets"]["key"]))
        tree.registries[cid] = registry
        tree.downloads[cid] = json.loads(get(bundle["downloads"]["key"]))
        for asset in registry["assets"].values():
            for v in asset["variants"]:
                tree.blobs[v["file"]["key"]] = v["file"]
    return tree


def profile_blobs(tree: Tree, profile: str) -> dict[str, dict[str, Any]]:
    """The blobs a profile needs, one per (asset, quality): the largest variant, an upper bound (an app picks one
    codec). The cover is always included."""
    out: dict[str, dict[str, Any]] = {}
    for cid, downloads in tree.downloads.items():
        plan = downloads["profiles"].get(profile)
        if plan is None:
            continue
        assets = tree.registries[cid]["assets"]
        for lid in plan["assets"]:
            asset = assets.get(lid)
            if asset is None:
                continue
            for quality in plan["qualities"]:
                variants = [v["file"] for v in asset["variants"] if v.get("quality", "standard") == quality]
                if variants:
                    best = max(variants, key=lambda f: (f["bytes"], f["key"]))
                    out[best["key"]] = best
    if tree.release.get("cover"):
        f = tree.release["cover"]["file"]
        out[f["key"]] = f
    return out


def download_bytes(tree: Tree, have: set[str] | None = None) -> dict[str, int]:
    """Bytes an app downloads for each profile: every document plus the profile's blobs, minus digests it has."""
    have = have or set()
    docs = [tree.manifest, *tree.structure.values(), *tree.content().values()]
    out = {}
    for profile in PROFILES:
        every = {d["key"]: d for d in docs} | profile_blobs(tree, profile)
        out[profile] = sum(d["bytes"] for d in every.values() if d["sha256"] not in have)
    return out


def delta(old: Tree, new: Tree, delta_bytes: int = 0) -> dict[str, Any]:
    """The `vn.delta` document from `old` to `new` (deterministic: sorted lists, canonical when written).
    `delta_bytes` is the size of the delta document itself when known (it is part of what an app downloads)."""
    added = sorted(set(new.chapters) - set(old.chapters))
    removed = sorted(set(old.chapters) - set(new.chapters))
    unchanged, changed = [], []
    for cid in [c for c in new.chapters if c in old.chapters]:
        names = sorted(n for n, d in new.chapters[cid].items() if old.chapters[cid].get(n, {}).get("sha256") != d["sha256"])
        if not names:
            unchanged.append(cid)
            continue
        changed.append({"id": cid, "documents": names, "textOnly": all(n.startswith("locales/") for n in names),
                        "bytes": sum(new.chapters[cid][n]["bytes"] for n in names)})  # fmt: skip
    release_docs = sorted(n for n, d in new.release_docs.items() if old.release_docs.get(n, {}).get("sha256") != d["sha256"])
    blobs_added = sorted(set(new.blobs) - set(old.blobs))
    migration = next((d for d in new.release.get("migrations", []) if d["key"].endswith(f"/migration-from-{old.release['releaseId']}.json")), None)
    have = old.digests()
    sizes = download_bytes(new, have)
    return {"kind": "vn.delta", "schemaVersion": cm.SCHEMA_VERSION, "storyId": new.release["storyId"],
            "fromRelease": old.release["releaseId"], "toRelease": new.release["releaseId"],
            "fromGraphId": old.release["graphId"], "toGraphId": new.release["graphId"],
            "graphChanged": old.release["graphId"] != new.release["graphId"], "migration": migration,
            "chapters": {"added": added, "removed": removed, "unchanged": sorted(unchanged), "changed": sorted(changed, key=lambda c: c["id"])},
            "releaseDocuments": release_docs,
            "blobs": {"added": [{"key": k, "bytes": new.blobs[k]["bytes"]} for k in blobs_added],
                      "dropped": sorted(set(old.blobs) - set(new.blobs))},
            "downloadBytes": {p: sizes[p] + delta_bytes for p in PROFILES}}  # fmt: skip


def delta_key(story_id: str, release_id: str, from_release: str) -> str:
    return f"stories/{story_id}/releases/{release_id}/delta-from-{from_release}.json"


def summary(doc: dict[str, Any], profile: str = "standard") -> dict[str, Any]:
    """The change-feed summary of a delta."""
    chapters = doc["chapters"]
    return {"chaptersAdded": len(chapters["added"]), "chaptersChanged": len(chapters["changed"]),
            "chaptersRemoved": len(chapters["removed"]),
            "textOnly": not chapters["added"] and not chapters["removed"] and not doc["blobs"]["added"]
                        and all(c["textOnly"] for c in chapters["changed"]),
            "graphChanged": doc["graphChanged"], "downloadBytes": doc["downloadBytes"][profile]}  # fmt: skip
