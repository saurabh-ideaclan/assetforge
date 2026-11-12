"""Publish a compiled visual-novel release to Cloudflare R2 (S3 API). Only `publish(..., yes=True)` writes, and
nothing in AssetForge calls it automatically.

Sequence: validate → HEAD every object (plan) → refuse on any conflict → upload blobs and chapter documents
(a blob already there with the same size is skipped; a document already there must be byte-identical) → HEAD
every uploaded object again → upload `release.json` → sign and publish the catalog last (`catalog/history/<seq>`
first, then `catalog/current.json`). Published objects are never overwritten and nothing expires.

Configuration (settings: env, ./.env, $ASSETFORGE_HOME/.env): `ASSETFORGE_R2_BUCKET`, `ASSETFORGE_R2_ENV_FILE`
(a foreign .env holding `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ACCOUNT_ID`; read in place, never copied), optional
direct `ASSETFORGE_R2_ACCOUNT_ID` / `ASSETFORGE_R2_ACCESS_KEY` / `ASSETFORGE_R2_SECRET_KEY` overrides, and
`ASSETFORGE_R2_PUBLIC_BASE` (the public domain, for documentation only). boto3 is the optional `publish` extra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assetforge.config import load_settings, read_env_file
from assetforge.core.util import utcnow
from assetforge.errors import (ConflictError, PolicyError, PublishAccessDeniedError, PublishBucketMissingError, PublishError,
                               PublishNotConfiguredError)  # fmt: skip
from assetforge.story import catalog_sign, contract
from assetforge.story import contract_models as cm
from assetforge.story.project import write_atomic

ENV_FILE_KEYS = ("R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ACCOUNT_ID")
CATALOG_KEY = "catalog/current.json"
IMMUTABLE = "public, max-age=31536000, immutable"
MUTABLE = "no-cache"
RECEIPTS = "publish.json"


@dataclass(frozen=True)
class R2Config:
    bucket: str
    account_id: str = field(repr=False)
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    public_base: str | None = None
    source: str = "settings"

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    def describe(self) -> dict[str, Any]:
        """Safe to print: no keys, the account id shortened."""
        return {"bucket": self.bucket, "endpoint": f"https://…{self.account_id[-4:]}.r2.cloudflarestorage.com",
                "public_base": self.public_base, "credentials_from": self.source}  # fmt: skip


def resolve_config(home: Path | None = None) -> R2Config:
    settings = load_settings(home)
    values: dict[str, str | None] = {"account": None, "access": None, "secret": None}
    source = "settings"
    if env_file := settings.get("ASSETFORGE_R2_ENV_FILE"):
        path = Path(env_file).expanduser()
        if not path.is_file():
            raise PublishNotConfiguredError(f"ASSETFORGE_R2_ENV_FILE points at a missing file: {path}")
        foreign = read_env_file(path, ENV_FILE_KEYS)
        values = {"account": foreign.get("R2_ACCOUNT_ID"), "access": foreign.get("R2_ACCESS_KEY"),
                  "secret": foreign.get("R2_SECRET_KEY")}  # fmt: skip
        source = str(path)
    for name, key in (("account", "ASSETFORGE_R2_ACCOUNT_ID"), ("access", "ASSETFORGE_R2_ACCESS_KEY"),
                      ("secret", "ASSETFORGE_R2_SECRET_KEY")):  # fmt: skip
        values[name] = settings.get(key) or values[name]
    bucket = settings.get("ASSETFORGE_R2_BUCKET")
    missing = [k for k, v in (("ASSETFORGE_R2_BUCKET", bucket), ("account id", values["account"]),
                              ("access key", values["access"]), ("secret key", values["secret"])) if not v]  # fmt: skip
    if missing:
        raise PublishNotConfiguredError(f"R2 publishing is not configured: missing {', '.join(missing)}",
                                        hint="Set ASSETFORGE_R2_BUCKET and ASSETFORGE_R2_ENV_FILE (or the direct key settings)")  # fmt: skip
    return R2Config(bucket=str(bucket), account_id=str(values["account"]), access_key=str(values["access"]),
                    secret_key=str(values["secret"]), public_base=settings.get("ASSETFORGE_R2_PUBLIC_BASE"), source=source)  # fmt: skip


def make_client(cfg: R2Config) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise PublishNotConfiguredError("boto3 is not installed", hint="uv sync --extra publish") from exc
    return boto3.client("s3", endpoint_url=cfg.endpoint, aws_access_key_id=cfg.access_key,
                        aws_secret_access_key=cfg.secret_key, region_name="auto",
                        config=Config(signature_version="s3v4", retries={"max_attempts": 4, "mode": "standard"}))  # fmt: skip


# ---------------------------------------------------------------------- S3 helpers


def _code(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    code = str((response.get("Error") or {}).get("Code") or "")
    status = str((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or "")
    return code or status


def _missing(code: str | None) -> bool:
    return code in ("404", "NoSuchKey", "NotFound", "NoSuchBucket")


def _denied(code: str | None) -> bool:
    return code in ("403", "AccessDenied", "Forbidden", "InvalidAccessKeyId", "SignatureDoesNotMatch")


def _head(client: Any, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - botocore errors carry `response`; anything else is re-raised
        code = _code(exc)
        if code is None:
            raise
        if _missing(code):
            return None
        if _denied(code):
            raise PublishAccessDeniedError(f"Access denied reading {key} in {bucket}", hint="Scope the R2 token to this bucket") from exc
        raise PublishError(f"HEAD {key} failed: {code}") from exc


def _get(client: Any, bucket: str, key: str) -> bytes | None:
    try:
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001
        code = _code(exc)
        if code is None:
            raise
        if _missing(code):
            return None
        if _denied(code):
            raise PublishAccessDeniedError(f"Access denied reading {key}") from exc
        raise PublishError(f"GET {key} failed: {code}") from exc


def _put(client: Any, bucket: str, key: str, data: bytes, media_type: str, cache: str) -> None:
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=media_type, CacheControl=cache,
                          Metadata={"sha256": cm.sha256(data)})  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        code = _code(exc)
        if code is None:
            raise
        if _denied(code):
            raise PublishAccessDeniedError(f"Access denied writing {key}", hint="The token needs write access to the bucket") from exc
        raise PublishError(f"PUT {key} failed: {code}", hint="Re-run: finished objects are skipped") from exc


def preflight(client: Any, cfg: R2Config) -> dict[str, Any]:
    """Can we reach the bucket? Missing and denied are reported differently."""
    try:
        client.head_bucket(Bucket=cfg.bucket)
    except Exception as exc:  # noqa: BLE001
        code = _code(exc)
        if code is None:
            raise
        if _missing(code):
            raise PublishBucketMissingError(f"Bucket {cfg.bucket!r} does not exist", hint="Create it in Cloudflare R2 first") from exc
        if _denied(code):
            raise PublishAccessDeniedError(f"The R2 token cannot reach bucket {cfg.bucket!r}",
                                           hint="Scope the API token to this bucket (Object Read & Write)") from exc  # fmt: skip
        raise PublishError(f"Bucket check failed: {code}") from exc
    return {"bucket": cfg.bucket, "reachable": True}


def _remote_catalog(client: Any, bucket: str, home: Path) -> tuple[bytes | None, dict[str, Any] | None]:
    data = _get(client, bucket, CATALOG_KEY)
    if data is None:
        return None, None
    public = catalog_sign.load_public(home)
    if public is None:
        raise PolicyError("The bucket has a catalog but this library has no catalog key to verify it",
                          hint="Restore $ASSETFORGE_HOME/keys/vn-catalog.ed25519 from where it was made")  # fmt: skip
    return data, catalog_sign.verify(json.loads(data), public)


# ---------------------------------------------------------------------- plan and publish


def _classify(client: Any, bucket: str, objs: list[contract.Obj]) -> list[dict[str, Any]]:
    rows = []
    for o in objs:
        head = _head(client, bucket, o.key)
        if head is None:
            action = "upload"
        elif o.role == "blob":  # content-addressed: the same size under the same digest is the same file
            action = "skip" if int(head.get("ContentLength", -1)) == o.bytes else "conflict"
        else:
            remote = _get(client, bucket, o.key)
            action = "skip" if remote is not None and cm.sha256(remote) == o.sha256 else "conflict"
        rows.append({"key": o.key, "role": o.role, "bytes": o.bytes, "action": action})
    return rows


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for action in ("upload", "skip", "conflict", "unknown"):
        chosen = [r for r in rows if r["action"] == action]
        out[action] = {"count": len(chosen), "bytes": sum(r["bytes"] for r in chosen)}
    return out


def plan(release: Path, *, home: Path, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    """What `publish` would upload and skip, with byte totals (reads only; never writes)."""
    doc, objs = contract.release_objects(release)
    issues = contract.validate_release(release)
    errors = [i.model_dump() for i in issues if i.level == "error"]
    result: dict[str, Any] = {"story": doc["storyId"], "release": doc["releaseId"], "graph_id": doc["graphId"],
                              "draft": bool(doc.get("draft")), "valid": not errors, "errors": errors[:20], "remote": "checked"}  # fmt: skip
    try:
        cfg = cfg or resolve_config(home)
        client = client or make_client(cfg)
        preflight(client, cfg)
        rows = _classify(client, cfg.bucket, objs)
        _, current = _remote_catalog(client, cfg.bucket, home)
        result["config"] = cfg.describe()
        result["catalog"] = {"key": CATALOG_KEY, "current_sequence": current["catalogSequence"] if current else None,
                             "next_sequence": (current["catalogSequence"] if current else 0) + 1}  # fmt: skip
    except (PublishNotConfiguredError, PublishAccessDeniedError, PublishBucketMissingError) as exc:
        rows = [{"key": o.key, "role": o.role, "bytes": o.bytes, "action": "unknown"} for o in objs]
        result["remote"] = f"not checked: {exc.message}"
        result["remote_error"] = exc.to_dict()
    result["objects"] = rows
    result["totals"] = _totals(rows)
    result["conflicts"] = [r["key"] for r in rows if r["action"] == "conflict"]
    result["publishable"] = result["valid"] and not result["draft"] and not result["conflicts"] and result["remote"] == "checked"
    return result


def publish(release: Path, *, yes: bool, home: Path, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    """Upload the release and publish the signed catalog. Refuses without `yes`, for drafts, for releases that do
    not validate, and when any object already published differs (never overwrites)."""
    if not yes:
        raise PolicyError("Publishing needs an explicit yes", hint="Look at the plan (`story publish` without --yes), then pass --yes")
    doc, objs = contract.release_objects(release)
    if doc.get("draft"):
        raise PolicyError("A draft release can never be published", hint="Make the missing assets, then compile again")
    errors = [i for i in contract.validate_release(release) if i.level == "error"]
    if errors:
        raise PolicyError("The release does not validate", errors=[e.model_dump() for e in errors[:20]])
    cfg = cfg or resolve_config(home)
    client = client or make_client(cfg)
    preflight(client, cfg)
    key = catalog_sign.load_or_create_key(home)
    previous_bytes, previous = _remote_catalog(client, cfg.bucket, home)
    rows = _classify(client, cfg.bucket, objs)
    conflicts = [r["key"] for r in rows if r["action"] == "conflict"]
    if conflicts:
        raise ConflictError("Objects already published under these keys differ; published objects are never overwritten",
                            conflicts=conflicts[:20], hint="Compile a new release instead")  # fmt: skip
    action = {r["key"]: r["action"] for r in rows}
    uploaded = []
    for o in objs[:-1]:  # everything but release.json
        if action[o.key] == "upload":
            _put(client, cfg.bucket, o.key, o.path.read_bytes(), o.media_type, IMMUTABLE)
            uploaded.append(o)
    for o in objs[:-1]:  # the release is complete before its manifest appears
        head = _head(client, cfg.bucket, o.key)
        if head is None or int(head.get("ContentLength", -1)) != o.bytes:
            raise PublishError(f"{o.key} is missing or incomplete after upload", hint="Re-run `story publish --yes`")
    top = objs[-1]
    if action[top.key] == "upload":
        _put(client, cfg.bucket, top.key, top.path.read_bytes(), top.media_type, IMMUTABLE)
        uploaded.append(top)
    if _head(client, cfg.bucket, top.key) is None:
        raise PublishError(f"{top.key} did not arrive")
    sequence = _publish_catalog(client, cfg.bucket, release, doc, top, key, previous_bytes, previous)
    receipt = {"release": doc["releaseId"], "story": doc["storyId"], "graph_id": doc["graphId"], "sequence": sequence,
               "bucket": cfg.bucket, "uploaded": len(uploaded), "uploaded_bytes": sum(o.bytes for o in uploaded),
               "skipped": len(objs) - len(uploaded), "objects": [o.key for o in uploaded], "at": utcnow()}  # fmt: skip
    _record(release.parent, receipt)
    return receipt


def _publish_catalog(client: Any, bucket: str, release: Path, doc: dict[str, Any], top: contract.Obj, key: Any,
                     previous_bytes: bytes | None, previous: dict[str, Any] | None) -> int:  # fmt: skip
    lang = doc["defaultLocale"]
    strings = json.loads(contract.local_path(release.parent, doc["storyId"], doc["locales"][lang]["key"]).read_bytes())["strings"]
    manifest = {"key": top.key, "mediaType": top.media_type, "bytes": top.bytes, "sha256": top.sha256}
    old_entry = next((s for s in (previous or {}).get("stories", []) if s["storyId"] == doc["storyId"]), None)
    entry = catalog_sign.entry_for(doc, manifest, strings.get(doc["titleKey"], doc["storyId"]), strings.get(doc["summaryKey"], ""), old_entry)
    payload = catalog_sign.next_catalog(previous, entry)
    body = cm.canonical(catalog_sign.sign(payload, key))
    if previous is not None and previous_bytes is not None:
        history = f"catalog/history/{previous['catalogSequence']}.json"
        if _head(client, bucket, history) is None:  # keep the catalog we are replacing
            _put(client, bucket, history, previous_bytes, "application/json", IMMUTABLE)
    history = f"catalog/history/{payload['catalogSequence']}.json"
    if _head(client, bucket, history) is not None:
        raise ConflictError(f"Catalog sequence {payload['catalogSequence']} already exists; someone published at the same time",
                            hint="Re-run to publish on top of the newer catalog")  # fmt: skip
    _put(client, bucket, history, body, "application/json", IMMUTABLE)
    _put(client, bucket, CATALOG_KEY, body, "application/json", MUTABLE)
    return int(payload["catalogSequence"])


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
            entry["published"] = {"sequence": receipt["sequence"], "at": receipt["at"], "bucket": receipt["bucket"]}
    contract.save_index(root, index)
