"""Provider abstraction for text generation (story writing).

The preferred providers are the official command-line tools you are already signed in to (`claude` for a
Claude plan, `codex` for a ChatGPT plan, `gemini` for a Google account): they bill your subscription, so no
API key is needed. API-key providers exist only as an opt-in fallback. Every adapter maps failures onto
`ProviderError(kind=…)` with the same retry/fallback meaning as the image providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from assetforge.config import Settings

Billing = Literal["subscription", "api", "offline"]


@dataclass
class TextCall:
    prompt: str
    system: str = ""
    schema: dict[str, Any] | None = None  # strict JSON schema of the answer (see structured.strict_schema)
    model: str | None = None
    timeout: float = 600.0
    task: str = "text"  # e.g. story.world; recorded on the job, used by the offline provider
    # Offline answer builder for the fake provider (keeps demos and tests coherent without a model).
    offline: Callable[[TextCall], Any] | None = None


@dataclass
class TextResponse:
    text: str
    provider: str
    model: str
    billing: Billing
    data: Any = None  # parsed structured output when the provider returned it natively
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class TextProvider(ABC):
    name: str = ""
    billing: Billing = "subscription"
    login_hint: str = ""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(usable now, why not). Cheap: never makes a model call."""

    def default_model(self) -> str | None:
        return None

    @abstractmethod
    def run(self, call: TextCall) -> TextResponse: ...

    def signed_in(self) -> tuple[bool | None, str]:
        """(True/False, account detail) from a cheap local status check, or (None, why unknown). No model call."""
        return None, ""

    def models(self) -> list[dict[str, Any]]:
        """Models this account can pick: [{id, label, description}]. The first entry `""` means the tool's default."""
        return [{"id": "", "label": "Default", "description": "Whatever the tool is configured to use"}]

    def describe(self) -> dict[str, Any]:
        ok, reason = self.available()
        return {"name": self.name, "available": ok, "reason": reason or None, "billing": self.billing,
                "model": self.default_model() or "provider default", "login_hint": self.login_hint or None}  # fmt: skip
