"""The signed visual-novel catalog (`catalog/<channel>/current.json`): an Ed25519 envelope around the exact JSON bytes.

    {"alg": "Ed25519", "keyId": "<16 hex>", "payload": "<base64 of the payload bytes>",
     "signature": "<base64 of sign(PROTOCOL_PREFIX + payload bytes)>"}

The private key lives at `$ASSETFORGE_HOME/keys/vn-catalog.ed25519` (PKCS#8 PEM, mode 0600, made on first use),
never in the library's objects or the app; the public key sits next to it (`.pub`, raw 32 bytes as base64) and is
all a consumer needs. Each new catalog carries `catalogSequence` = previous + 1; a rollback is a new sequence that
points a story at an older release.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from assetforge.core.util import utcnow
from assetforge.errors import InvalidInputError, PolicyError
from assetforge.story import contract_models as cm

PROTOCOL_PREFIX = b"assetforge.vn.catalog.v2\n"
KEY_NAME = "vn-catalog.ed25519"


def key_paths(home: Path) -> tuple[Path, Path]:
    private = Path(home) / "keys" / KEY_NAME
    return private, private.with_name(KEY_NAME + ".pub")


def key_id(public: Ed25519PublicKey) -> str:
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def load_or_create_key(home: Path) -> Ed25519PrivateKey:
    private_path, public_path = key_paths(home)
    if private_path.exists():
        key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise InvalidInputError(f"{private_path} is not an Ed25519 key")
        return key
    private_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(private_path.parent, 0o700)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public_path.write_text(base64.b64encode(raw).decode() + "\n")
    return key


def load_public(home: Path) -> Ed25519PublicKey | None:
    _, public_path = key_paths(home)
    if not public_path.exists():
        private_path, _ = key_paths(home)
        return load_or_create_key(home).public_key() if private_path.exists() else None
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(public_path.read_text().strip()))


def sign(payload: dict[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    cm.Catalog.model_validate(payload)
    data = cm.canonical(payload)
    return {"alg": "Ed25519", "keyId": key_id(key.public_key()), "payload": base64.b64encode(data).decode(),
            "signature": base64.b64encode(key.sign(PROTOCOL_PREFIX + data)).decode()}  # fmt: skip


def verify(envelope: dict[str, Any], public: Ed25519PublicKey) -> dict[str, Any]:
    """The payload of a catalog envelope signed by `public`; PolicyError when it is not."""
    try:
        cm.Envelope.model_validate(envelope)
        if envelope["keyId"] != key_id(public):
            raise PolicyError("The catalog was signed with another key", key_id=envelope["keyId"])
        data = base64.b64decode(envelope["payload"], validate=True)
        public.verify(base64.b64decode(envelope["signature"], validate=True), PROTOCOL_PREFIX + data)
        payload = json.loads(data)
        cm.Catalog.model_validate(payload)
    except InvalidSignature as exc:
        raise PolicyError("The catalog signature does not verify") from exc
    except (ValueError, TypeError) as exc:
        raise PolicyError(f"The catalog envelope is malformed: {exc}") from exc
    return payload


def release_ref(release: dict[str, Any], manifest: dict[str, Any], *, published_at: str,
                delta: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """One listed release of a story entry."""
    ref = {"releaseId": release["releaseId"], "graphId": release["graphId"],
           "minEngineVersion": release["requires"]["minEngineVersion"], "publishedAt": published_at, "manifest": manifest}  # fmt: skip
    if delta is not None:
        ref["delta"] = delta
    return ref


def entry_for(release: dict[str, Any], manifest: dict[str, Any], title: str, summary: str, previous: dict[str, Any] | None,
              *, at: str | None = None, delta: dict[str, Any] | None = None, chapter_count: int | None = None,
              download_bytes: dict[str, int] | None = None, ref: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """This story's catalog entry pointing at `release` (kept releases of the story stay listed, its status is kept).
    Sequences and `updatedAt` are filled by `next_catalog`. `ref` reuses an existing listed-release record (promotion)."""
    ref = ref or release_ref(release, manifest, published_at=at or utcnow(), delta=delta)
    kept = [r for r in (previous or {}).get("releases", []) if r["releaseId"] != release["releaseId"]]
    cover = release.get("cover")
    return {"storyId": release["storyId"], "status": (previous or {}).get("status", "live"), "title": title, "summary": summary,
            "defaultLocale": release["defaultLocale"], "locales": sorted(release.get("locales") or [release["defaultLocale"]]),
            "thumbnail": cover["file"] if cover else None, "current": release["releaseId"],
            "chapterCount": len(release.get("chapters", [])) if chapter_count is None else chapter_count,
            "downloadBytes": download_bytes or {"core": 0, "standard": 0},
            "releases": sorted([*kept, ref], key=lambda r: r["releaseId"])}  # fmt: skip


def next_catalog(previous: dict[str, Any] | None, story: dict[str, Any], *, channel: str = "staging",
                 change: dict[str, Any] | None = None, at: str | None = None) -> dict[str, Any]:  # fmt: skip
    """The next catalog payload: this story's entry added or replaced, sequence bumped (never reused), the entry's
    `updatedSequence` moved to it, and the change prepended to the feed (newest first, at most 50)."""
    at = at or utcnow()
    sequence = int((previous or {}).get("catalogSequence", 0)) + 1
    old = next((s for s in (previous or {}).get("stories", []) if s["storyId"] == story["storyId"]), None)
    story = {**story, "addedSequence": old["addedSequence"] if old else sequence, "updatedSequence": sequence, "updatedAt": at}
    if change is None:
        change = {"type": "story_added" if old is None else "release_published", "storyId": story["storyId"],
                  "releaseId": story["current"], "fromReleaseId": old["current"] if old else None}  # fmt: skip
    change = {k: v for k, v in {"sequence": sequence, "at": at, **change}.items() if v is not None}
    stories = [s for s in (previous or {}).get("stories", []) if s["storyId"] != story["storyId"]]
    return {"kind": "vn.catalog", "schemaVersion": cm.SCHEMA_VERSION, "channel": channel, "catalogSequence": sequence,
            "generatedAt": at, "stories": sorted([*stories, story], key=lambda s: s["storyId"]),
            "changes": [change, *(previous or {}).get("changes", [])][: cm.MAX_CHANGES]}  # fmt: skip
