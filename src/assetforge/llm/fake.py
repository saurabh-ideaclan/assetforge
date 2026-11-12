"""Offline text provider (`ASSETFORGE_ENABLE_FAKE_PROVIDER=1`, `--provider fake`). It never falls back to a real
model. When the caller supplies an `offline` builder (the story steps do), its coherent answer is returned;
otherwise a minimal instance of the schema is synthesized. Used by tests and for zero-cost pipeline demos."""

from __future__ import annotations

import json
from typing import Any

from assetforge.llm.base import TextCall, TextProvider, TextResponse


class FakeTextProvider(TextProvider):
    name = "fake"
    billing = "offline"
    supports_images = True  # it never looks at them: the caller's `offline` builder answers

    def available(self) -> tuple[bool, str]:
        return (True, "") if self.settings.fake_provider_enabled else (False, "set ASSETFORGE_ENABLE_FAKE_PROVIDER=1")

    def default_model(self) -> str | None:
        return "fake-text-1"

    def signed_in(self) -> tuple[bool | None, str]:
        return self.available()[0], "offline"

    def models(self) -> list[dict[str, Any]]:
        return [{"id": "", "label": "Default", "description": "Offline placeholder text"}]

    def run(self, call: TextCall) -> TextResponse:
        data = call.offline(call) if call.offline else (synthesize(call.schema) if call.schema else "ok")
        text = data if isinstance(data, str) else json.dumps(data)
        return TextResponse(text=text, data=None if isinstance(data, str) else data, provider=self.name,
                            model="fake-text-1", billing=self.billing,
                            usage={"input_tokens": len(call.prompt) // 4, "output_tokens": len(text) // 4})  # fmt: skip


def synthesize(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return synthesize(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return synthesize(next((s for s in schema["anyOf"] if s.get("type") != "null"), schema["anyOf"][0]), defs)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        return {k: synthesize(v, defs) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [synthesize(schema.get("items", {}), defs)]
    return {"string": "text", "integer": 1, "number": 1.0, "boolean": True, "null": None}.get(kind or "string", "text")
