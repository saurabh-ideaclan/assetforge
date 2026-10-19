"""Google Gemini image adapter ("Nano Banana" models via google-genai generate_content).

Gemini returns opaque images, so transparency is produced by the engine (key-colour background
removal). Reference images are passed inline with the prompt; an edit is the source image plus
an instruction. Each call returns one image, so n > 1 makes n calls.
"""

from __future__ import annotations

from typing import Any

from assetforge.errors import ProviderError
from assetforge.image.providers.base import (
    Capabilities,
    ImageProvider,
    Operation,
    ProviderCall,
    ProviderImage,
    ProviderResponse,
    nearest_aspect,
)

DEFAULT_MODEL = "gemini-3.1-flash-image"  # Nano Banana 2
GEMINI_ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9")
# USD per output image by image_size. Estimates; check Google's pricing page.
IMAGE_PRICES: dict[str, dict[str, float]] = {
    "gemini-2.5-flash-image": {"1K": 0.039},
    "gemini-3.1-flash-lite-image": {"1K": 0.034, "2K": 0.05, "4K": 0.08},
    "gemini-3.1-flash-image": {"1K": 0.067, "2K": 0.101, "4K": 0.151},
    "gemini-3-pro-image": {"1K": 0.134, "2K": 0.134, "4K": 0.24},
}
QUALITY_TO_SIZE = {"auto": "1K", "low": "1K", "medium": "1K", "high": "2K", "max": "4K"}


def _base_model(model: str) -> str:
    return model.removeprefix("models/").removesuffix("-preview")


def _supports_image_size(model: str) -> bool:
    return not _base_model(model).startswith("gemini-2.5")


def estimate_cost(model: str, image_size: str, count: int) -> float | None:
    prices = IMAGE_PRICES.get(_base_model(model))
    if not prices:
        return None
    return round(prices.get(image_size, max(prices.values())) * count, 4)


def _add_usage(usage: dict[str, int], meta: Any) -> None:
    """Accumulate Gemini usage_metadata, split by modality where the API reports it."""
    if meta is None:
        return
    for key in ("prompt_token_count", "candidates_token_count", "thoughts_token_count", "total_token_count"):
        usage[key] = usage.get(key, 0) + (getattr(meta, key, None) or 0)
    for details, prefix in (("prompt_tokens_details", "prompt"), ("candidates_tokens_details", "output")):
        for item in getattr(meta, details, None) or []:
            modality = str(getattr(getattr(item, "modality", None), "value", getattr(item, "modality", ""))).upper()
            kind = "image" if "IMAGE" in modality else "text"
            usage[f"{prefix}_{kind}_tokens"] = usage.get(f"{prefix}_{kind}_tokens", 0) + (item.token_count or 0)


class GeminiProvider(ImageProvider):
    name = "gemini"
    key_env = "GEMINI_API_KEY"
    quality_affects_cost = False  # quality only picks the image size, which size_key() includes

    def __init__(self, settings: Any, client: Any = None) -> None:
        super().__init__(settings)
        self._client = client

    def configured(self) -> bool:
        return bool(self._client or self.settings.gemini_api_key)

    def default_model(self, operation: Operation) -> str:
        if operation == "edit":
            return self.settings.get("ASSETFORGE_GEMINI_EDIT_MODEL") or self.default_model("generate")
        return self.settings.get("ASSETFORGE_GEMINI_IMAGE_MODEL") or DEFAULT_MODEL

    def capabilities(self, model: str) -> Capabilities:
        return Capabilities(
            native_transparency=False,
            max_references=3 if _base_model(model).startswith("gemini-2.5") else 14,
            supports_mask=False,
            supports_seed=True,
            max_n_per_call=1,
            aspects=GEMINI_ASPECTS,
        )

    def client(self) -> Any:
        if self._client is None:
            self.require_configured()
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def _image_size(self, call: ProviderCall) -> str:
        if call.size:
            longest = max(call.size)
            return "1K" if longest <= 1024 else "2K" if longest <= 2048 else "4K"
        return QUALITY_TO_SIZE[call.quality]

    def size_key(self, call: ProviderCall) -> str:
        aspect = nearest_aspect(*call.size, list(GEMINI_ASPECTS)) if call.size else call.aspect
        return f"{aspect}@{self._image_size(call) if _supports_image_size(call.model) else '1K'}"

    def expected_output_tokens(self, call: ProviderCall) -> int | None:
        # Google's documented image tokens: 2.5 Flash 1290; 3.x 1120 (1K), 1680 (2K), 2520 (4K).
        if not _supports_image_size(call.model):
            return 1290
        return {"1K": 1120, "2K": 1680, "4K": 2520}[self._image_size(call)]

    def estimate_cost(self, call: ProviderCall) -> float:
        size = QUALITY_TO_SIZE[call.quality]
        if call.size:
            longest = max(call.size)
            size = "1K" if longest <= 1024 else "2K" if longest <= 2048 else "4K"
        return estimate_cost(call.model, size, call.n) or 0.15 * call.n

    def run(self, call: ProviderCall) -> ProviderResponse:
        from google.genai import types
        from PIL import Image

        if call.mask:
            raise ProviderError("Gemini does not support edit masks", provider=self.name, kind="unsupported")
        aspect = nearest_aspect(*call.size, list(GEMINI_ASPECTS)) if call.size else call.aspect
        if aspect not in GEMINI_ASPECTS:
            aspect = "1:1"
        image_config: dict[str, Any] = {"aspect_ratio": aspect}
        image_size = QUALITY_TO_SIZE[call.quality]
        if call.size:
            longest = max(call.size)
            image_size = "1K" if longest <= 1024 else "2K" if longest <= 2048 else "4K"
        if _supports_image_size(call.model):
            image_config["image_size"] = image_size
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(**image_config),
            seed=call.seed,
        )
        inputs = [Image.open(p) for p in call.images]
        contents: list[Any] = [*inputs, call.prompt]
        client = self.client()
        images: list[ProviderImage] = []
        usage: dict[str, int] = {}
        try:
            for i in range(call.n):
                if call.seed is not None and i:
                    config.seed = call.seed + i
                response = client.models.generate_content(model=call.model, contents=contents, config=config)
                images.append(self._extract(response))
                _add_usage(usage, getattr(response, "usage_metadata", None))
        except ProviderError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc
        finally:
            for image in inputs:
                image.close()
        return ProviderResponse(
            provider=self.name, model=call.model, images=images, usage=usage,
            cost_usd=estimate_cost(call.model, image_config.get("image_size", "1K"), len(images)),
            request={"aspect_ratio": aspect, "image_size": image_config.get("image_size"), "seed": call.seed,
                     "reference_images": len(call.images)},
        )  # fmt: skip

    def _extract(self, response: Any) -> ProviderImage:
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise ProviderError(f"Gemini blocked the prompt: {feedback.block_reason}", provider=self.name, kind="content")
        text = []
        for candidate in getattr(response, "candidates", None) or []:
            reason = str(getattr(candidate, "finish_reason", "") or "")
            if any(word in reason for word in ("SAFETY", "PROHIBITED", "BLOCKLIST", "IMAGE_SAFETY")):
                raise ProviderError(f"Gemini refused the image: {reason}", provider=self.name, kind="content")
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "thought", False):
                    continue
                inline = getattr(part, "inline_data", None)
                if inline is not None and inline.data:
                    return ProviderImage(inline.data, inline.mime_type or "image/png")
                if getattr(part, "text", None):
                    text.append(part.text)
        detail = (" Model said: " + " ".join(text)[:300]) if text else ""
        raise ProviderError(f"Gemini returned no image.{detail}", provider=self.name, kind="bad_response")

    def _map_error(self, exc: Exception) -> ProviderError:
        from google.genai import errors

        status = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        if isinstance(exc, errors.ServerError):
            kind = "unavailable"
        elif isinstance(exc, errors.ClientError):
            kind = {401: "auth", 403: "auth", 404: "rejected", 429: "rate_limited"}.get(status or 0, "rejected")
            if kind == "rate_limited" and any(w in message.lower() for w in ("billing", "prepay", "credits")):
                kind = "quota"
        elif isinstance(exc, TimeoutError | ConnectionError):
            kind = "unavailable"
        else:
            kind = "bad_response"
        return ProviderError(f"Gemini: {message}", provider=self.name, kind=kind, status=status)
