"""Opt-in API-key fallbacks (billed per token). They only count as available when both the key and a text
model are set (`ASSETFORGE_OPENAI_TEXT_MODEL`, `ASSETFORGE_GEMINI_TEXT_MODEL`), so nothing is billed to an
API key unless you ask for it. SDK errors are mapped exactly like the image adapters do."""

from __future__ import annotations

from assetforge.errors import ProviderError
from assetforge.image.providers.gemini import GeminiProvider
from assetforge.image.providers.openai import OpenAIProvider
from assetforge.llm.base import TextCall, TextProvider, TextResponse


def _retag(error: ProviderError, name: str) -> ProviderError:
    return ProviderError(error.message, provider=name, kind=error.kind, hint=error.hint)


class OpenAIAPIProvider(TextProvider):
    name = "openai-api"
    billing = "api"

    def signed_in(self) -> tuple[bool | None, str]:
        return bool(self.settings.openai_api_key), "API key" if self.settings.openai_api_key else "no key"

    def models(self) -> list[dict[str, str]]:
        model = self.default_model()
        return [{"id": model, "label": model, "description": "ASSETFORGE_OPENAI_TEXT_MODEL"}] if model else []

    def default_model(self) -> str | None:
        return self.settings.get("ASSETFORGE_OPENAI_TEXT_MODEL")

    def available(self) -> tuple[bool, str]:
        if not self.settings.openai_api_key:
            return False, "OPENAI_API_KEY not set"
        if not self.default_model():
            return False, "opt-in: set ASSETFORGE_OPENAI_TEXT_MODEL to use the API (billed per token)"
        return True, ""

    def run(self, call: TextCall) -> TextResponse:
        import openai

        ok, reason = self.available()
        if not ok:
            raise ProviderError(f"openai-api: {reason}", provider=self.name, kind="not_configured")
        model = call.model or self.default_model() or ""
        messages = [{"role": "system", "content": call.system}] if call.system else []
        messages.append({"role": "user", "content": call.prompt})
        kwargs: dict = {}
        if call.schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "answer", "schema": call.schema, "strict": True}}  # fmt: skip
        try:
            client = openai.OpenAI(api_key=self.settings.openai_api_key, timeout=call.timeout)
            response = client.chat.completions.create(model=model, messages=messages, **kwargs)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise _retag(OpenAIProvider(self.settings)._map_error(exc), self.name) from exc
        text = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}
        return TextResponse(text=text, provider=self.name, model=model, billing=self.billing,
                            usage={"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)})  # fmt: skip


class GeminiAPIProvider(TextProvider):
    name = "gemini-api"
    billing = "api"

    def signed_in(self) -> tuple[bool | None, str]:
        return bool(self.settings.gemini_api_key), "API key" if self.settings.gemini_api_key else "no key"

    def models(self) -> list[dict[str, str]]:
        model = self.default_model()
        return [{"id": model, "label": model, "description": "ASSETFORGE_GEMINI_TEXT_MODEL"}] if model else []

    def default_model(self) -> str | None:
        return self.settings.get("ASSETFORGE_GEMINI_TEXT_MODEL")

    def available(self) -> tuple[bool, str]:
        if not self.settings.gemini_api_key:
            return False, "GEMINI_API_KEY not set"
        if not self.default_model():
            return False, "opt-in: set ASSETFORGE_GEMINI_TEXT_MODEL to use the API (billed per token)"
        return True, ""

    def run(self, call: TextCall) -> TextResponse:
        from google import genai
        from google.genai import types

        ok, reason = self.available()
        if not ok:
            raise ProviderError(f"gemini-api: {reason}", provider=self.name, kind="not_configured")
        model = call.model or self.default_model() or ""
        config = types.GenerateContentConfig(system_instruction=call.system or None)
        if call.schema:
            config.response_mime_type = "application/json"
            config.response_json_schema = call.schema
        try:
            client = genai.Client(api_key=self.settings.gemini_api_key)
            response = client.models.generate_content(model=model, contents=call.prompt, config=config)
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise _retag(GeminiProvider(self.settings)._map_error(exc), self.name) from exc
        meta = getattr(response, "usage_metadata", None)
        return TextResponse(text=response.text or "", provider=self.name, model=model, billing=self.billing,
                            usage={"input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                                   "output_tokens": getattr(meta, "candidates_token_count", 0) or 0})  # fmt: skip

