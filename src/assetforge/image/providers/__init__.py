"""Provider registry and selection."""

from __future__ import annotations

from collections.abc import Callable

from assetforge.config import Settings
from assetforge.errors import InvalidInputError
from assetforge.image.providers.base import ImageProvider
from assetforge.image.providers.fake import FakeProvider
from assetforge.image.providers.gemini import GeminiProvider
from assetforge.image.providers.openai import OpenAIProvider

# Order is the default preference for `--provider auto`: OpenAI first (native transparency).
FACTORIES: dict[str, Callable[[Settings], ImageProvider]] = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "fake": FakeProvider,
}

# Tests (and embedding applications) can pin provider instances here.
OVERRIDES: dict[str, ImageProvider] = {}


def get_provider(name: str, settings: Settings) -> ImageProvider:
    key = name.strip().lower()
    if key in OVERRIDES:
        return OVERRIDES[key]
    if key not in FACTORIES or (key == "fake" and not settings.fake_provider_enabled):
        available = [n for n in FACTORIES if n != "fake" or settings.fake_provider_enabled]
        raise InvalidInputError(f"Unknown image provider {name!r}", available=available)
    return FACTORIES[key](settings)


def provider_chain(requested: str | None, settings: Settings, *, fallback: bool) -> list[ImageProvider]:
    """Providers to try, in order.

    'auto' (or nothing) puts $ASSETFORGE_IMAGE_PROVIDER first if set, otherwise configured providers
    before unconfigured ones. An explicit provider comes first and the real providers follow as fallbacks.
    The fake provider is only ever used when asked for by name (or as the configured default).
    """
    choice = (requested or "auto").strip().lower()
    real = [n for n in FACTORIES if n != "fake"]
    if choice == "auto":
        preferred = (settings.get("ASSETFORGE_IMAGE_PROVIDER") or "").lower() or None
        if preferred is not None:
            first = get_provider(preferred, settings)
            ordered = [first, *(get_provider(n, settings) for n in real if n != first.name)]
        else:
            providers = [get_provider(n, settings) for n in real]
            ordered = sorted(providers, key=lambda p: not p.configured())
    else:
        first = get_provider(choice, settings)
        ordered = [first, *(get_provider(n, settings) for n in real if n != first.name)]
    if not fallback or ordered[0].name == "fake":  # the offline provider never falls back to paid ones
        return ordered[:1]
    return ordered
