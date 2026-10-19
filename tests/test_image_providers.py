"""Provider adapters against mocked SDK clients: request mapping, response parsing, error mapping."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from PIL import Image

from assetforge.config import Settings, load_settings
from assetforge.errors import InvalidInputError, ProviderError
from assetforge.image import providers
from assetforge.image.providers import provider_chain
from assetforge.image.providers.base import ProviderCall
from assetforge.image.providers.fake import FakeProvider
from assetforge.image.providers.gemini import GeminiProvider
from assetforge.image.providers.openai import OpenAIProvider, estimate_cost

from conftest import make_png


def png_bytes(size=(8, 8), colour=(10, 200, 30, 255)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, colour).save(out, format="PNG")
    return out.getvalue()


SETTINGS = Settings({"OPENAI_API_KEY": "sk-test", "GEMINI_API_KEY": "g-test"})


# ---------------------------------------------------------------------- OpenAI


class FakeImages:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error = error

    def _respond(self, kind: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((kind, kwargs))
        if self.error:
            raise self.error
        data = [SimpleNamespace(b64_json=base64.b64encode(png_bytes()).decode(), revised_prompt=None)
                for _ in range(kwargs["n"])]  # fmt: skip
        usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 100, "output_tokens": 4000,
                                                   "input_tokens_details": {"text_tokens": 100, "image_tokens": 0}})  # fmt: skip
        return SimpleNamespace(data=data, output_format=kwargs["output_format"], usage=usage)

    def generate(self, **kwargs: Any) -> Any:
        return self._respond("generate", kwargs)

    def edit(self, **kwargs: Any) -> Any:
        return self._respond("edit", kwargs)


def openai_provider(error: Exception | None = None) -> tuple[OpenAIProvider, FakeImages]:
    images = FakeImages(error)
    return OpenAIProvider(SETTINGS, client=SimpleNamespace(images=images)), images


def test_openai_generate_maps_size_quality_and_transparency():
    provider, images = openai_provider()
    call = ProviderCall("generate", "gpt-image-2.5-flare", "a beetle", n=2, aspect="16:9", quality="max",
                        transparent=True)  # fmt: skip
    response = provider.run(call)

    kind, sent = images.calls[0]
    assert kind == "generate"
    assert sent["size"] == "1536x864" and sent["quality"] == "max" and sent["background"] == "transparent"
    assert sent["output_format"] == "png" and sent["n"] == 2
    assert len(response.images) == 2 and response.images[0].mime_type == "image/png"
    assert Image.open(io.BytesIO(response.images[0].data)).size == (8, 8)
    assert response.cost_usd == estimate_cost("gpt-image-2.5-flare", response.usage) > 0


def test_openai_size_rules_per_model():
    provider, _ = openai_provider()
    assert provider.size_for(ProviderCall("generate", "gpt-image-1", "x", aspect="16:9")) == "1536x1024"
    assert provider.size_for(ProviderCall("generate", "gpt-image-2", "x", aspect="21:9")) == "1792x768"
    assert provider.size_for(ProviderCall("generate", "gpt-image-2", "x", size=(2048, 1152))) == "2048x1152"
    with pytest.raises(ProviderError) as exc:
        provider.size_for(ProviderCall("generate", "gpt-image-2", "x", size=(1000, 1000)))
    assert exc.value.kind == "unsupported"
    with pytest.raises(ProviderError):
        provider.size_for(ProviderCall("generate", "gpt-image-1", "x", size=(2048, 1152)))
    quality = []
    for model in ("gpt-image-2", "gpt-image-2.5-sunburst"):
        p, imgs = openai_provider()
        p.run(ProviderCall("generate", model, "x", quality="max"))
        quality.append(imgs.calls[0][1]["quality"])
    assert quality == ["high", "max"]


def test_openai_reference_images_use_the_edit_endpoint(tmp_path):
    provider, images = openai_provider()
    refs = [make_png(tmp_path / "a.png"), make_png(tmp_path / "b.png")]
    mask = make_png(tmp_path / "mask.png", color=(0, 0, 0, 0))
    provider.run(ProviderCall("edit", "gpt-image-2.5-sunburst", "make it red", images=refs, mask=mask))
    kind, sent = images.calls[0]
    assert kind == "edit"
    assert isinstance(sent["image"], list) and len(sent["image"]) == 2
    assert sent["mask"].name.endswith("mask.png")
    assert "background" not in sent


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (openai.RateLimitError("slow", response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
                               body=None), "rate_limited"),
        (openai.RateLimitError("You have no credits remaining", response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
                               body={"code": "credit_balance_exhausted"}), "quota"),
        (openai.AuthenticationError("bad key", response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
                                    body=None), "auth"),
        (openai.BadRequestError("blocked", response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                                body={"code": "moderation_blocked"}), "content"),
        (openai.BadRequestError("bad size", response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                                body={"code": "invalid_value"}), "rejected"),
        (openai.InternalServerError("boom", response=httpx.Response(500, request=httpx.Request("POST", "https://x")),
                                    body=None), "unavailable"),
    ],
)  # fmt: skip
def test_openai_errors_are_classified(error, kind):
    provider, _ = openai_provider(error)
    with pytest.raises(ProviderError) as exc:
        provider.run(ProviderCall("generate", "gpt-image-2", "x"))
    assert exc.value.kind == kind and exc.value.provider == "openai"
    assert exc.value.retryable == (kind in ("rate_limited", "unavailable"))
    assert exc.value.allows_fallback == (kind != "content")


# ---------------------------------------------------------------------- Gemini


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append({**kwargs, "seed": kwargs["config"].seed})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def gemini_response(*, image: bool = True, text: str | None = None, finish: str = "STOP", thought: bool = False) -> Any:
    parts = []
    if thought:
        parts.append(SimpleNamespace(thought=True, inline_data=SimpleNamespace(data=b"thinking", mime_type="image/png"),
                                     text=None))  # fmt: skip
    if text:
        parts.append(SimpleNamespace(thought=False, inline_data=None, text=text))
    if image:
        parts.append(SimpleNamespace(thought=False, inline_data=SimpleNamespace(data=png_bytes(), mime_type="image/png"),
                                     text=None))  # fmt: skip
    candidate = SimpleNamespace(finish_reason=finish, content=SimpleNamespace(parts=parts))
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=1290, total_token_count=1300)
    return SimpleNamespace(candidates=[candidate], prompt_feedback=None, usage_metadata=usage)


def gemini_provider(responses: list[Any]) -> tuple[GeminiProvider, FakeModels]:
    models = FakeModels(responses)
    return GeminiProvider(SETTINGS, client=SimpleNamespace(models=models)), models


def test_gemini_generate_with_references_and_multiple_images(tmp_path):
    provider, models = gemini_provider([gemini_response(thought=True), gemini_response()])
    ref = make_png(tmp_path / "ref.png")
    response = provider.run(ProviderCall("generate", "gemini-3.1-flash-image", "a beetle", n=2, aspect="16:9",
                                         quality="high", images=[ref], seed=7))  # fmt: skip
    assert len(models.calls) == 2 and [c["seed"] for c in models.calls] == [7, 8]
    config = models.calls[0]["config"]
    assert config.response_modalities == ["IMAGE"]
    assert config.image_config.aspect_ratio == "16:9" and config.image_config.image_size == "2K"
    assert len(models.calls[0]["contents"]) == 2 and models.calls[0]["contents"][-1] == "a beetle"
    assert len(response.images) == 2 and response.images[0].data != b"thinking"
    assert response.cost_usd == pytest.approx(2 * 0.101)


def test_gemini_older_model_omits_image_size_and_maps_sizes():
    provider, models = gemini_provider([gemini_response()])
    provider.run(ProviderCall("generate", "gemini-2.5-flash-image", "x", size=(1536, 864)))
    config = models.calls[0]["config"]
    assert config.image_config.aspect_ratio == "16:9" and config.image_config.image_size is None


def test_gemini_refusals_and_empty_responses():
    provider, _ = gemini_provider([gemini_response(image=False, finish="IMAGE_SAFETY")])
    with pytest.raises(ProviderError) as exc:
        provider.run(ProviderCall("generate", "gemini-3.1-flash-image", "x"))
    assert exc.value.kind == "content" and exc.value.code == "CONTENT_REJECTED"

    provider, _ = gemini_provider([gemini_response(image=False, text="I can't draw that")])
    with pytest.raises(ProviderError) as exc:
        provider.run(ProviderCall("generate", "gemini-3.1-flash-image", "x"))
    assert exc.value.kind == "bad_response" and "can't draw" in exc.value.message

    provider, _ = gemini_provider([genai_errors.ClientError(429, {"error": {"code": 429, "message": "quota"}})])
    with pytest.raises(ProviderError) as exc:
        provider.run(ProviderCall("generate", "gemini-3.1-flash-image", "x"))
    assert exc.value.kind == "rate_limited" and exc.value.retryable

    provider, _ = gemini_provider([])
    with pytest.raises(ProviderError) as exc:
        provider.run(ProviderCall("edit", "gemini-3.1-flash-image", "x", mask=Path("m.png")))
    assert exc.value.kind == "unsupported"


# ---------------------------------------------------------------------- selection + settings


def test_provider_chain_order_and_fallback():
    names = lambda chain: [p.name for p in chain]  # noqa: E731
    assert names(provider_chain("gemini", SETTINGS, fallback=True)) == ["gemini", "openai"]
    assert names(provider_chain("gemini", SETTINGS, fallback=False)) == ["gemini"]
    only_gemini = Settings({"GEMINI_API_KEY": "g"})
    assert names(provider_chain("auto", only_gemini, fallback=True)) == ["gemini", "openai"]
    preferred = Settings({"OPENAI_API_KEY": "k", "GEMINI_API_KEY": "g", "ASSETFORGE_IMAGE_PROVIDER": "gemini"})
    assert names(provider_chain(None, preferred, fallback=True)) == ["gemini", "openai"]
    with pytest.raises(InvalidInputError):
        provider_chain("midjourney", SETTINGS, fallback=True)
    with pytest.raises(InvalidInputError):
        provider_chain("fake", SETTINGS, fallback=True)  # disabled unless explicitly enabled
    fake_ok = Settings({"ASSETFORGE_ENABLE_FAKE_PROVIDER": "1", "OPENAI_API_KEY": "k"})
    assert names(provider_chain("fake", fake_ok, fallback=True)) == ["fake"]  # never falls back to paid APIs
    providers.OVERRIDES["openai"] = FakeProvider(SETTINGS, name="openai")
    assert isinstance(provider_chain("openai", SETTINGS, fallback=False)[0], FakeProvider)


def test_unconfigured_provider_reports_how_to_fix():
    provider = OpenAIProvider(Settings({}))
    with pytest.raises(ProviderError) as exc:
        provider.require_configured()
    assert exc.value.code == "PROVIDER_NOT_CONFIGURED" and "OPENAI_API_KEY" in exc.value.message


def test_settings_precedence_and_filtering(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("OPENAI_API_KEY=from-home\nGEMINI_API_KEY=from-home\nUNRELATED=x\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("# comment\nexport GEMINI_API_KEY='from-cwd'\nASSETFORGE_IMAGE_PROVIDER=gemini\n")
    monkeypatch.setenv("ASSETFORGE_IMAGE_PROVIDER", "openai")
    settings = load_settings(home, cwd=work)
    assert settings.openai_api_key == "from-home"
    assert settings.gemini_api_key == "from-cwd"
    assert settings.get("ASSETFORGE_IMAGE_PROVIDER") == "openai"
    assert "UNRELATED" not in settings.values
