"""Cloudflare R2 (S3 API): configuration, the lazily built boto3 client, and `Store`, the only way any AssetForge
code touches the bucket. Every key goes through `Store.full`, which applies `ASSETFORGE_R2_PREFIX`; keys inside
documents stay relative. The operations (plan, publish, promote, verify, gc…) live in `publish/backend.py`;
`plan`/`publish` here are kept as aliases of the backend's.

Configuration (settings: env, ./.env, $ASSETFORGE_HOME/.env): `ASSETFORGE_R2_BUCKET`, `ASSETFORGE_R2_PREFIX`
(a key prefix such as `vn`, so the content can share a bucket), `ASSETFORGE_R2_ENV_FILE` (a foreign .env holding
`R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ACCOUNT_ID`; read in place, never copied), optional direct
`ASSETFORGE_R2_ACCOUNT_ID` / `ASSETFORGE_R2_ACCESS_KEY` / `ASSETFORGE_R2_SECRET_KEY` overrides, and
`ASSETFORGE_R2_PUBLIC_BASE` (the public origin: the app's index URL and `verify --public`). boto3 is the optional
`publish` extra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assetforge.config import load_settings, read_env_file
from assetforge.errors import PublishAccessDeniedError, PublishBucketMissingError, PublishError, PublishNotConfiguredError
from assetforge.story import contract_models as cm

ENV_FILE_KEYS = ("R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ACCOUNT_ID")
IMMUTABLE = "public, max-age=31536000, immutable"
MUTABLE = "no-cache"


@dataclass(frozen=True)
class R2Config:
    bucket: str
    account_id: str = field(repr=False)
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    public_base: str | None = None
    source: str = "settings"
    prefix: str = ""

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    def describe(self) -> dict[str, Any]:
        """Safe to print: no keys, the account id shortened."""
        return {"bucket": self.bucket, "prefix": self.prefix, "endpoint": f"https://…{self.account_id[-4:]}.r2.cloudflarestorage.com",
                "public_base": self.public_base, "credentials_from": self.source, "configured": True}  # fmt: skip

    def public_url(self, key: str) -> str | None:
        """The anonymous HTTPS URL of a key (prefix applied), when a public origin is configured."""
        if not self.public_base:
            return None
        return self.public_base.rstrip("/") + "/" + (f"{self.prefix}/{key}" if self.prefix else key)


def clean_prefix(value: str | None) -> str:
    """`vn`, `/vn/` → `vn`; segments like object keys (no traversal, no spaces, no encodings)."""
    text = (value or "").strip().strip("/")
    if not text:
        return ""
    for segment in text.split("/"):
        if segment in (".", "..") or not cm._KEY_SEGMENT.match(segment):
            raise PublishNotConfiguredError(f"ASSETFORGE_R2_PREFIX {value!r} is not a valid key prefix",
                                            hint="Use letters, digits, '-', '_' and '/' only, e.g. vn")  # fmt: skip
    return text


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
                    secret_key=str(values["secret"]), public_base=settings.get("ASSETFORGE_R2_PUBLIC_BASE"), source=source,
                    prefix=clean_prefix(settings.get("ASSETFORGE_R2_PREFIX")))  # fmt: skip


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


# ---------------------------------------------------------------------- the bucket, inside the prefix


class Store:
    """The bucket as AssetForge sees it: every key relative to the prefix, every call counted by kind."""

    def __init__(self, client: Any, cfg: R2Config) -> None:
        self.client, self.cfg, self.bucket = client, cfg, cfg.bucket
        self.prefix = cfg.prefix
        self.writes: list[str] = []
        self.deletes: list[str] = []

    def full(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def relative(self, full_key: str) -> str | None:
        """The relative key of a bucket key, or None when it lies outside the prefix."""
        if not self.prefix:
            return full_key
        return full_key[len(self.prefix) + 1:] if full_key.startswith(self.prefix + "/") else None

    def preflight(self) -> dict[str, Any]:
        return {**preflight(self.client, self.cfg), "prefix": self.prefix}

    def head(self, key: str) -> dict[str, Any] | None:
        return _head(self.client, self.bucket, self.full(key))

    def get(self, key: str) -> bytes | None:
        return _get(self.client, self.bucket, self.full(key))

    def put(self, key: str, data: bytes, media_type: str, cache: str) -> None:
        _put(self.client, self.bucket, self.full(key), data, media_type, cache)
        self.writes.append(key)

    def create(self, key: str, data: bytes, media_type: str, cache: str) -> bool:
        """Write `key` only if it does not exist. Uses `IfNoneMatch="*"` when the client and endpoint accept it,
        otherwise HEAD then PUT. False when the key already exists (someone else took it)."""
        full = self.full(key)
        try:
            self.client.put_object(Bucket=self.bucket, Key=full, Body=data, ContentType=media_type, CacheControl=cache,
                                   Metadata={"sha256": cm.sha256(data)}, IfNoneMatch="*")  # fmt: skip
        except TypeError:  # a client without the parameter
            return self._create_fallback(key, data, media_type, cache)
        except Exception as exc:  # noqa: BLE001
            code = _code(exc)
            if type(exc).__name__ == "ParamValidationError" or code in ("NotImplemented", "501"):
                return self._create_fallback(key, data, media_type, cache)
            if code in ("PreconditionFailed", "412", "ConditionalRequestConflict", "409"):
                return False
            if code is None:
                raise
            if _denied(code):
                raise PublishAccessDeniedError(f"Access denied writing {key}", hint="The token needs write access to the bucket") from exc
            raise PublishError(f"PUT {key} failed: {code}") from exc
        self.writes.append(key)
        return True

    def _create_fallback(self, key: str, data: bytes, media_type: str, cache: str) -> bool:
        if self.head(key) is not None:
            return False
        self.put(key, data, media_type, cache)
        return True

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.full(key))
        except Exception as exc:  # noqa: BLE001
            code = _code(exc)
            if code is None:
                raise
            if _denied(code):
                raise PublishAccessDeniedError(f"Access denied deleting {key}") from exc
            raise PublishError(f"DELETE {key} failed: {code}") from exc
        self.deletes.append(key)

    def list(self) -> list[dict[str, Any]]:
        """Every object under the prefix (`<prefix>/…` only, never the rest of a shared bucket), relative keys."""
        out: list[dict[str, Any]] = []
        token: str | None = None
        start = f"{self.prefix}/" if self.prefix else ""
        while True:
            args: dict[str, Any] = {"Bucket": self.bucket, "Prefix": start}
            if token:
                args["ContinuationToken"] = token
            try:
                page = self.client.list_objects_v2(**args)
            except Exception as exc:  # noqa: BLE001
                code = _code(exc)
                if code is None:
                    raise
                if _denied(code):
                    raise PublishAccessDeniedError("Access denied listing the bucket", hint="The token needs list access") from exc
                raise PublishError(f"LIST failed: {code}") from exc
            for item in page.get("Contents", []) or []:
                rel = self.relative(str(item["Key"]))
                if rel is not None:  # belt and braces: never report anything outside the prefix
                    out.append({"key": rel, "bytes": int(item.get("Size", 0))})
            if not page.get("IsTruncated"):
                return out
            token = page.get("NextContinuationToken")


def open_store(home: Path, client: Any = None, cfg: R2Config | None = None) -> Store:
    cfg = cfg or resolve_config(home)
    return Store(client or make_client(cfg), cfg)


# ---------------------------------------------------------------------- aliases (the operations live in backend.py)


def plan(release: Path, *, home: Path, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    from assetforge.publish import backend

    return backend.plan(release, home=home, client=client, cfg=cfg)


def publish(release: Path, *, yes: bool, home: Path, client: Any = None, cfg: R2Config | None = None) -> dict[str, Any]:
    from assetforge.publish import backend

    return backend.publish(release, yes=yes, home=home, client=client, cfg=cfg)
