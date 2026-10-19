"""Provider abstraction for AI image generation.

The engine speaks only in `ProviderCall`/`ProviderResponse`. Each adapter maps those onto its
SDK, declares per-model `Capabilities`, and turns SDK exceptions into `ProviderError` with a
`kind` (rate_limited, auth, content, …) that drives retries and fallback. Model names, sizes
and prices live in the adapters, so a new provider model is a one-line change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from assetforge.config import Settings
from assetforge.errors import ProviderError

Operation = Literal["generate", "edit"]
Quality = Literal["auto", "low", "medium", "high", "max"]

# Aspect ratios AssetForge accepts; each adapter maps them onto what its models support.
ASPECTS: dict[str, tuple[int, int]] = {
    "1:1": (1, 1),
    "2:3": (2, 3),
    "3:2": (3, 2),
    "3:4": (3, 4),
    "4:3": (4, 3),
    "9:16": (9, 16),
    "16:9": (16, 9),
    "21:9": (21, 9),
}


def nearest_aspect(width: int, height: int, allowed: list[str] | None = None) -> str:
    ratio = width / height
    choices = allowed or list(ASPECTS)
    return min(choices, key=lambda a: abs(ASPECTS[a][0] / ASPECTS[a][1] - ratio))


@dataclass(frozen=True)
class Capabilities:
    native_transparency: bool
    max_references: int  # input images per call, including the image being edited
    supports_mask: bool
    supports_seed: bool
    max_n_per_call: int
    aspects: tuple[str, ...]
    output_formats: tuple[str, ...] = ("png",)


@dataclass
class ProviderCall:
    operation: Operation
    model: str
    prompt: str
    n: int = 1
    aspect: str = "1:1"
    size: tuple[int, int] | None = None  # exact pixels when the caller asked for them
    quality: Quality = "auto"
    transparent: bool = False  # only set when the model supports it natively
    output_format: str = "png"
    images: list[Path] = field(default_factory=list)  # edit source first, then references
    mask: Path | None = None
    seed: int | None = None


@dataclass
class ProviderImage:
    data: bytes
    mime_type: str
    revised_prompt: str | None = None

    @property
    def ext(self) -> str:
        return {"image/png": ".png", "image/webp": ".webp", "image/jpeg": ".jpg"}.get(self.mime_type, ".png")


@dataclass
class ProviderResponse:
    provider: str
    model: str
    images: list[ProviderImage]
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    request: dict[str, Any] = field(default_factory=dict)  # what was actually sent (sizes, quality…)


class ImageProvider(ABC):
    name: str = ""
    key_env: str = ""
    # Whether `quality` changes the price beyond what `size_key()` already captures (cost-history matching).
    quality_affects_cost: bool = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def default_model(self, operation: Operation) -> str: ...

    @abstractmethod
    def capabilities(self, model: str) -> Capabilities: ...

    def size_key(self, call: ProviderCall) -> str:
        """How this provider sizes the call (e.g. 1536x864, 2K); groups usage history for estimates."""
        return f"{call.size[0]}x{call.size[1]}" if call.size else call.aspect

    def expected_output_tokens(self, call: ProviderCall) -> int | None:
        """Typical output image tokens per image, used with current prices to estimate cost."""
        return None

    def estimate_cost(self, call: ProviderCall) -> float:
        """Upper-bound-ish USD estimate used to reserve budget before the call; actual cost replaces it."""
        return 0.0

    @abstractmethod
    def run(self, call: ProviderCall) -> ProviderResponse:
        """Generate (no images) or edit/reference (images given) and return all output images."""

    def require_configured(self) -> None:
        if not self.configured():
            raise ProviderError(
                f"{self.name} is not configured: set {self.key_env} in the environment or a .env file",
                provider=self.name,
                kind="not_configured",
            )

    def describe(self) -> dict[str, Any]:
        generate_model = self.default_model("generate")
        caps = self.capabilities(generate_model)
        return {
            "name": self.name,
            "configured": self.configured(),
            "key_env": self.key_env,
            "default_models": {"generate": generate_model, "edit": self.default_model("edit")},
            "native_transparency": caps.native_transparency,
            "max_references": caps.max_references,
            "supports_mask": caps.supports_mask,
            "aspects": list(caps.aspects),
        }
