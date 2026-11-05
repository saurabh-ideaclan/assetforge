"""Text-generation providers (registry and selection), mirroring `assetforge.image.providers`."""

from __future__ import annotations

from collections.abc import Callable

from assetforge.config import Settings
from assetforge.errors import InvalidInputError
from assetforge.llm.api import GeminiAPIProvider, OpenAIAPIProvider
from assetforge.llm.base import TextCall, TextProvider, TextResponse
from assetforge.llm.claude_cli import ClaudeCLIProvider
from assetforge.llm.codex_cli import CodexCLIProvider
from assetforge.llm.fake import FakeTextProvider
from assetforge.llm.gemini_cli import GeminiCLIProvider

__all__ = ["FACTORIES", "OVERRIDES", "TextCall", "TextProvider", "TextResponse", "get_provider", "provider_chain"]

# Order is the `auto` preference: signed-in subscriptions first, opt-in API keys last.
FACTORIES: dict[str, Callable[[Settings], TextProvider]] = {
    "claude-cli": ClaudeCLIProvider,
    "codex-cli": CodexCLIProvider,
    "gemini-cli": GeminiCLIProvider,
    "openai-api": OpenAIAPIProvider,
    "gemini-api": GeminiAPIProvider,
    "fake": FakeTextProvider,
}
ALIASES = {"claude": "claude-cli", "codex": "codex-cli", "chatgpt": "codex-cli", "gemini": "gemini-cli",
           "openai": "openai-api"}  # fmt: skip

# Tests (and embedding applications) can pin provider instances here.
OVERRIDES: dict[str, TextProvider] = {}


def canonical(name: str) -> str:
    key = name.strip().lower()
    return ALIASES.get(key, key)


def get_provider(name: str, settings: Settings) -> TextProvider:
    key = canonical(name)
    if key in OVERRIDES:
        return OVERRIDES[key]
    if key not in FACTORIES or (key == "fake" and not settings.fake_provider_enabled):
        known = [n for n in FACTORIES if n != "fake" or settings.fake_provider_enabled]
        raise InvalidInputError(f"Unknown text provider {name!r}", available=known, aliases=ALIASES)
    return FACTORIES[key](settings)


def all_providers(settings: Settings) -> list[TextProvider]:
    names = [*FACTORIES, *(n for n in OVERRIDES if n not in FACTORIES)]
    return [get_provider(n, settings) for n in names if n != "fake" or settings.fake_provider_enabled or n in OVERRIDES]


def provider_chain(requested: str | None, settings: Settings, *, fallback: bool = True) -> list[TextProvider]:
    """Providers to try, in order. `auto` = $ASSETFORGE_TEXT_PROVIDER first if set, then every available
    provider in FACTORIES order. An explicit provider is always tried (so its error is reported) and available
    ones follow as fallbacks. The fake provider never falls back to a real one, and nothing falls back to it."""
    choice = canonical(requested or settings.get("ASSETFORGE_TEXT_PROVIDER") or "auto")
    real = [get_provider(n, settings) for n in FACTORIES if n != "fake"]
    available = [p for p in real if p.available()[0]]
    if choice == "auto":
        return available if fallback else available[:1]
    first = get_provider(choice, settings)
    if first.name == "fake" or not fallback:
        return [first]
    return [first, *(p for p in available if p.name != first.name)]
