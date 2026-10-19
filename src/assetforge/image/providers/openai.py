"""OpenAI GPT Image adapter (images.generate / images.edit)."""

from __future__ import annotations

import base64
from contextlib import ExitStack
from typing import Any

from assetforge.errors import ProviderError
from assetforge.image.providers.base import (
    ASPECTS,
    Capabilities,
    ImageProvider,
    Operation,
    ProviderCall,
    ProviderImage,
    ProviderResponse,
)

DEFAULT_GENERATE_MODEL = "gpt-image-2.5-flare"  # "fast, high-quality everyday image generation"
DEFAULT_EDIT_MODEL = "gpt-image-2.5-sunburst"  # "editing precision"
STANDARD_SIZES = {"1:1": (1024, 1024), "3:2": (1536, 1024), "2:3": (1024, 1536)}
# ~1 megapixel, both edges divisible by 16, for models that accept arbitrary WIDTHxHEIGHT.
FLEX_SIZES = {
    "1:1": (1024, 1024), "2:3": (1024, 1536), "3:2": (1536, 1024), "3:4": (960, 1280), "4:3": (1280, 960),
    "9:16": (864, 1536), "16:9": (1536, 864), "21:9": (1792, 768),
}  # fmt: skip
# USD per 1M tokens: (text input, image input, image output). Estimates; check OpenAI's pricing page.
# Rough per-megapixel image cost by quality, for budget reservations before a call.
ESTIMATE_PER_MEGAPIXEL = {"low": 0.02, "medium": 0.07, "auto": 0.07, "high": 0.21, "max": 0.4}
TOKEN_PRICES = {
    "gpt-image-1": (5.0, 10.0, 40.0),
    "gpt-image-1-mini": (2.0, 2.5, 8.0),
    "gpt-image-1.5": (5.0, 8.0, 32.0),
    "gpt-image-2": (5.0, 8.0, 30.0),
}


def _family(model: str) -> str:
    for prefix in ("gpt-image-2.5", "gpt-image-2", "gpt-image-1.5", "gpt-image-1-mini", "gpt-image-1"):
        if model.startswith(prefix):
            return prefix
    return model


def _flexible_sizes(model: str) -> bool:
    return _family(model) in ("gpt-image-2", "gpt-image-2.5")


def _quality(model: str, quality: str) -> str:
    if quality == "max":
        return "max" if _family(model) == "gpt-image-2.5" else "high"
    return quality


def estimate_cost(model: str, usage: dict[str, Any]) -> float | None:
    family = _family(model)
    prices = TOKEN_PRICES.get(family) or (TOKEN_PRICES["gpt-image-2"] if family == "gpt-image-2.5" else None)
    if not prices or not usage:
        return None
    details = usage.get("input_tokens_details") or {}
    text_in = details.get("text_tokens", usage.get("input_tokens", 0))
    image_in = details.get("image_tokens", 0)
    out = usage.get("output_tokens", 0)
    return round((text_in * prices[0] + image_in * prices[1] + out * prices[2]) / 1_000_000, 4)


class OpenAIProvider(ImageProvider):
    name = "openai"
    key_env = "OPENAI_API_KEY"

    def __init__(self, settings: Any, client: Any = None) -> None:
        super().__init__(settings)
        self._client = client

    def configured(self) -> bool:
        return bool(self._client or self.settings.openai_api_key)

    def default_model(self, operation: Operation) -> str:
        if operation == "edit":
            return self.settings.get("ASSETFORGE_OPENAI_EDIT_MODEL") or DEFAULT_EDIT_MODEL
        return self.settings.get("ASSETFORGE_OPENAI_IMAGE_MODEL") or DEFAULT_GENERATE_MODEL

    def capabilities(self, model: str) -> Capabilities:
        flexible = _flexible_sizes(model)
        return Capabilities(
            native_transparency=True,
            max_references=16,
            supports_mask=True,
            supports_seed=False,
            max_n_per_call=10,
            aspects=tuple(FLEX_SIZES if flexible else STANDARD_SIZES),
            output_formats=("png", "webp"),
        )

    def client(self) -> Any:
        if self._client is None:
            self.require_configured()
            import openai

            self._client = openai.OpenAI(api_key=self.settings.openai_api_key, timeout=300, max_retries=0)
        return self._client

    def size_for(self, call: ProviderCall) -> str:
        if call.size:
            w, h = call.size
            if _flexible_sizes(call.model):
                if w % 16 or h % 16:
                    raise ProviderError(f"{call.model} needs sizes divisible by 16, got {w}x{h}",
                                        provider=self.name, kind="unsupported")  # fmt: skip
                return f"{w}x{h}"
            if (w, h) not in STANDARD_SIZES.values():
                raise ProviderError(f"{call.model} supports only 1024x1024, 1536x1024 and 1024x1536",
                                    provider=self.name, kind="unsupported")  # fmt: skip
            return f"{w}x{h}"
        table = FLEX_SIZES if _flexible_sizes(call.model) else STANDARD_SIZES
        if call.aspect in table:
            w, h = table[call.aspect]
        else:  # older models: nearest of the three standard shapes
            target = ASPECTS[call.aspect][0] / ASPECTS[call.aspect][1]
            w, h = min(table.values(), key=lambda s: abs(s[0] / s[1] - target))
        return f"{w}x{h}"

    def size_key(self, call: ProviderCall) -> str:
        return self.size_for(call)

    def expected_output_tokens(self, call: ProviderCall) -> int | None:
        # OpenAI's documented output tokens at 1024x1024 (low 272, medium 1056, high 4160), scaled by area.
        per_mp = {"low": 272, "medium": 1056, "auto": 1056, "high": 4160, "max": 8320}[_quality(call.model, call.quality)]
        w, h = (int(v) for v in self.size_for(call).split("x"))
        return round(per_mp * (w * h) / (1024 * 1024))

    def estimate_cost(self, call: ProviderCall) -> float:
        per_image = ESTIMATE_PER_MEGAPIXEL.get(_quality(call.model, call.quality), 0.07)
        w, h = (int(v) for v in self.size_for(call).split("x"))
        return round(per_image * (w * h) / (1024 * 1024) * call.n + 0.005 * len(call.images), 4)

    def run(self, call: ProviderCall) -> ProviderResponse:
        params: dict[str, Any] = {
            "model": call.model,
            "prompt": call.prompt,
            "n": call.n,
            "size": self.size_for(call),
            "quality": _quality(call.model, call.quality),
            "output_format": call.output_format if call.output_format in ("png", "webp") else "png",
        }
        if call.transparent:
            params["background"] = "transparent"
        client = self.client()
        try:
            if call.images:
                with ExitStack() as stack:
                    files = [stack.enter_context(open(p, "rb")) for p in call.images]
                    extra: dict[str, Any] = {"image": files if len(files) > 1 else files[0]}
                    if call.mask:
                        extra["mask"] = stack.enter_context(open(call.mask, "rb"))
                    response = client.images.edit(**params, **extra)
            else:
                response = client.images.generate(**params)
        except Exception as exc:
            raise self._map_error(exc) from exc
        images = []
        for item in response.data or []:
            if not item.b64_json:
                continue
            mime = f"image/{response.output_format or params['output_format']}"
            images.append(ProviderImage(base64.b64decode(item.b64_json), mime, getattr(item, "revised_prompt", None)))
        if not images:
            raise ProviderError("OpenAI returned no images", provider=self.name, kind="bad_response")
        usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
        return ProviderResponse(
            provider=self.name, model=call.model, images=images, usage=usage,
            cost_usd=estimate_cost(call.model, usage), request={k: v for k, v in params.items() if k != "prompt"},
        )  # fmt: skip

    def _map_error(self, exc: Exception) -> ProviderError:
        import openai

        message = getattr(exc, "message", None) or str(exc)
        code = str(getattr(exc, "code", "") or "")
        if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
            kind = "auth"
        elif isinstance(exc, openai.RateLimitError):
            # 429 also means "out of credits"; retrying that only wastes time.
            quota = any(w in code or w in message for w in ("insufficient_quota", "credit_balance", "billing"))
            kind = "quota" if quota else "rate_limited"
        elif isinstance(exc, openai.APITimeoutError | openai.APIConnectionError | openai.InternalServerError):
            kind = "unavailable"
        elif isinstance(exc, openai.BadRequestError):
            kind = "content" if ("moderation" in code or "safety" in code or "content_policy" in code) else "rejected"
        elif isinstance(exc, openai.NotFoundError):
            kind = "rejected"
        elif isinstance(exc, openai.APIStatusError) and getattr(exc, "status_code", 0) >= 500:
            kind = "unavailable"
        else:
            kind = "bad_response"
        return ProviderError(f"OpenAI: {message}", provider=self.name, kind=kind, provider_code=code or None)
