"""Error types and the documented exit codes of the CLI contract."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    INVALID_INPUT = 4
    CONFLICT = 5
    STORAGE = 6
    POLICY = 7
    PARTIAL = 8
    PROVIDER = 9


class AssetForgeError(Exception):
    """Base error. `code` is the stable machine-readable identifier agents match on.

    `retryable` says whether repeating the same call later may succeed; `hint` is a short next step.
    """

    code = "ERROR"
    exit_code = ExitCode.ERROR
    retryable = False

    def __init__(self, message: str, *, hint: str | None = None, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            data["hint"] = self.hint
        if self.details:
            data["details"] = self.details
        return data


class NotFoundError(AssetForgeError):
    code = "NOT_FOUND"
    exit_code = ExitCode.NOT_FOUND


class InvalidInputError(AssetForgeError):
    code = "INVALID_INPUT"
    exit_code = ExitCode.INVALID_INPUT


class ConflictError(AssetForgeError):
    code = "CONFLICT"
    exit_code = ExitCode.CONFLICT


class StorageError(AssetForgeError):
    code = "STORAGE_ERROR"
    exit_code = ExitCode.STORAGE


class PolicyError(AssetForgeError):
    code = "POLICY_VIOLATION"
    exit_code = ExitCode.POLICY


class BudgetExceededError(PolicyError):
    code = "BUDGET_EXCEEDED"


class InternalError(AssetForgeError):
    """An unexpected bug. The traceback goes to stderr and to <library>/logs/errors.log."""

    code = "INTERNAL_ERROR"


class ProviderError(AssetForgeError):
    """A generation provider failed. `kind` drives retry and fallback decisions.

    kinds: not_configured, auth, quota (out of credits/billing: retrying won't help), rate_limited,
    unavailable, rejected (bad request for this provider/model), content (safety refusal),
    unsupported (capability missing), bad_response.
    """

    exit_code = ExitCode.PROVIDER
    RETRYABLE = frozenset({"rate_limited", "unavailable", "bad_response"})
    FALLBACK = frozenset({"not_configured", "auth", "quota", "rate_limited", "unavailable", "rejected", "unsupported",
                          "bad_response"})  # fmt: skip

    def __init__(self, message: str, *, provider: str, kind: str, **details: Any) -> None:
        super().__init__(message, provider=provider, kind=kind, **details)
        self.provider = provider
        self.kind = kind
        self.code = "CONTENT_REJECTED" if kind == "content" else f"PROVIDER_{kind.upper()}"

    @property  # type: ignore[override]
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE

    @property
    def allows_fallback(self) -> bool:
        return self.kind in self.FALLBACK


# The documented catalog behind `assetforge errors`: code -> (exit code, meaning, what to do).
CATALOG: dict[str, tuple[int, str, str]] = {
    "USAGE": (2, "Bad command-line usage", "Check `assetforge <command> --help`."),
    "NOT_FOUND": (3, "Asset, collection, job, file or style not found", "Search first, or check the ID/key."),
    "INVALID_INPUT": (4, "A value is invalid or a file can't be decoded", "Read `message`/`details` and fix the input."),
    "CONFLICT": (5, "Name/key/label already taken, ambiguous reference, or foreign file in the way",
                 "Pick another name/label, pass a project, or use the documented override flag."),
    "STORAGE_ERROR": (6, "Filesystem or database problem; failed `verify`", "Check disk space/permissions; run `verify`."),
    "POLICY_VIOLATION": (7, "Blocked by policy (QC errors on approve, restricted license)", "Fix the cause or pass --force."),
    "BUDGET_EXCEEDED": (7, "A spending limit would be exceeded", "See `assetforge budget`; raise limits or wait."),
    "PARTIAL": (8, "A batch partly succeeded (status 'partial')", "Inspect the per-item errors."),
    "PROVIDER_NOT_CONFIGURED": (9, "No API key for the provider", "Set the key in the environment or a .env file."),
    "PROVIDER_AUTH": (9, "The provider rejected the API key", "Check the key and its permissions."),
    "PROVIDER_QUOTA": (9, "Out of credits or billing quota at the provider", "Add credits or use another provider."),
    "PROVIDER_RATE_LIMITED": (9, "Rate limited (after retries) or no free provider slot", "Retry later; see `assetforge config`."),
    "PROVIDER_UNAVAILABLE": (9, "Provider outage or timeout (after retries)", "Retry later or use another provider."),
    "PROVIDER_REJECTED": (9, "The provider rejected the request parameters", "Change size/model/options."),
    "PROVIDER_UNSUPPORTED": (9, "The provider/model lacks a needed capability", "Use another provider."),
    "PROVIDER_BAD_RESPONSE": (9, "The provider returned no usable image", "Retry, or rephrase the prompt."),
    "CONTENT_REJECTED": (9, "The provider refused the content (safety)", "Rephrase the prompt; not retried automatically."),
    "INTERNAL_ERROR": (1, "Unexpected bug in AssetForge", "Report it with <library>/logs/errors.log."),
}
