"""Anthropic API text provider (billed per token): the opt-in fallback behind the `claude` CLI subscription.

Like the other API providers it only counts as available when both the key and a model are set
(`ANTHROPIC_API_KEY` + `ASSETFORGE_ANTHROPIC_TEXT_MODEL`), so nothing is billed unless the operator asked for
it. Structured answers use the Messages API's `output_config.format` JSON schema, so the reply is the JSON the
story step needs. A safety refusal is reported as a `content` error (no retry, no fallback), everything else
maps onto the usual ProviderError kinds.
"""

from __future__ import annotations

import json
from typing import Any

from assetforge.errors import ProviderError
from assetforge.llm.base import TextCall, TextProvider, TextResponse

MODELS = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "description": "Most capable; best for structure and judgement"},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "description": "Strong writing at lower cost"},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "description": "Fast and cheap for light tasks"},
]
DEFAULT_SYSTEM = "You write visual-novel content as JSON for a production pipeline. Return only the JSON asked for."


class AnthropicAPIProvider(TextProvider):
    name = "anthropic-api"
    billing = "api"
    login_hint = "Set ANTHROPIC_API_KEY and ASSETFORGE_ANTHROPIC_TEXT_MODEL in .env to allow billed calls."

    def signed_in(self) -> tuple[bool | None, str]:
        key = self.settings.get("ANTHROPIC_API_KEY")
        return bool(key), "API key" if key else "no key"

    def default_model(self) -> str | None:
        return self.settings.get("ASSETFORGE_ANTHROPIC_TEXT_MODEL")

    def models(self) -> list[dict[str, Any]]:
        return MODELS if self.available()[0] else []

    def available(self) -> tuple[bool, str]:
        if not self.settings.get("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY not set"
        if not self.default_model():
            return False, "opt-in: set ASSETFORGE_ANTHROPIC_TEXT_MODEL to use the API (billed per token)"
        return True, ""

    def run(self, call: TextCall) -> TextResponse:
        import anthropic

        ok, reason = self.available()
        if not ok:
            raise ProviderError(f"anthropic-api: {reason}", provider=self.name, kind="not_configured")
        model = call.model or self.default_model() or "claude-opus-5-5"
        kwargs: dict[str, Any] = {}
        if call.schema:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": call.schema}}
        try:
            client = anthropic.Anthropic(api_key=self.settings.get("ANTHROPIC_API_KEY"), timeout=call.timeout, max_retries=0)
            response = client.messages.create(model=model, max_tokens=16000, system=call.system or DEFAULT_SYSTEM,
                                              messages=[{"role": "user", "content": call.prompt}], **kwargs)  # fmt: skip
        except anthropic.AuthenticationError as exc:
            raise ProviderError(f"anthropic-api: {exc.message}", provider=self.name, kind="auth", hint=self.login_hint) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError(f"anthropic-api: {exc.message}", provider=self.name, kind="auth") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"anthropic-api: unknown model {model!r}: {exc.message}", provider=self.name,
                                kind="not_configured", hint="Pick a model from `assetforge llm models`") from exc  # fmt: skip
        except anthropic.RateLimitError as exc:
            raise ProviderError(f"anthropic-api: rate limited: {exc.message}", provider=self.name, kind="rate_limited") from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError(f"anthropic-api: {exc.message}", provider=self.name, kind="bad_request") from exc
        except anthropic.APIStatusError as exc:
            kind = "unavailable" if exc.status_code >= 500 else "bad_request"
            raise ProviderError(f"anthropic-api: HTTP {exc.status_code}: {exc.message}", provider=self.name, kind=kind) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"anthropic-api: connection failed: {exc}", provider=self.name, kind="unavailable") from exc
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            why = getattr(details, "explanation", None) or getattr(details, "category", None) or "safety refusal"
            raise ProviderError(f"anthropic-api declined the request: {why}", provider=self.name, kind="content")
        text = "".join(block.text for block in response.content if block.type == "text")
        if response.stop_reason == "max_tokens":
            raise ProviderError("anthropic-api: the answer hit the length limit", provider=self.name, kind="bad_response")
        usage = response.usage
        data = None
        if call.schema:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        return TextResponse(text=text, provider=self.name, model=response.model or model, billing=self.billing, data=data,
                            usage={"input_tokens": (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
                                   + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
                                   "output_tokens": usage.output_tokens or 0},
                            raw={"request_id": getattr(response, "_request_id", None), "stop_reason": response.stop_reason})  # fmt: skip
