"""Offline provider for tests, demos and agent dry runs (enable with ASSETFORGE_ENABLE_FAKE_PROVIDER=1).

Draws a deterministic picture from the prompt hash: a coloured blob on a transparent,
key-colour or plain background, depending on what was asked. `fail_plan` lets tests
script failures, e.g. ["rate_limited", "rate_limited"] fails the first two calls.
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import Any

from PIL import Image, ImageDraw

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

_KEY_RE = re.compile(r"solid pure \w+ \((#[0-9a-fA-F]{6})\) background")


class FakeProvider(ImageProvider):
    name = "fake"
    key_env = "ASSETFORGE_ENABLE_FAKE_PROVIDER"

    def __init__(self, settings: Any, *, native_transparency: bool = True, fail_plan: list[str] | None = None,
                 base: int = 256, name: str = "fake", max_references: int = 4,
                 cost_per_image: float = 0.0) -> None:  # fmt: skip
        super().__init__(settings)
        self.cost_per_image = cost_per_image
        self.name = name
        self.max_references = max_references
        self.native_transparency = native_transparency
        self.fail_plan = list(fail_plan or [])
        self.base = base
        self.calls: list[ProviderCall] = []

    def configured(self) -> bool:
        return True

    def default_model(self, operation: Operation) -> str:
        return f"fake-{operation}-1"

    def capabilities(self, model: str) -> Capabilities:
        return Capabilities(
            native_transparency=self.native_transparency, max_references=self.max_references, supports_mask=True,
            supports_seed=True, max_n_per_call=4, aspects=tuple(ASPECTS),
        )  # fmt: skip

    def estimate_cost(self, call: ProviderCall) -> float:
        return self.cost_per_image * call.n

    def run(self, call: ProviderCall) -> ProviderResponse:
        self.calls.append(call)
        if self.fail_plan:
            kind = self.fail_plan.pop(0)
            raise ProviderError(f"fake {kind} failure", provider=self.name, kind=kind)
        w, h = call.size or self._size(call.aspect)
        images = []
        for i in range(call.n):
            digest = hashlib.sha256(f"{call.prompt}|{call.seed}|{i}|{[p.name for p in call.images]}".encode()).digest()
            images.append(ProviderImage(self._draw(call, w, h, digest), "image/png", revised_prompt=None))
        return ProviderResponse(
            provider=self.name, model=call.model, images=images, usage={"images": call.n},
            cost_usd=self.cost_per_image * call.n, request={"size": f"{w}x{h}", "transparent": call.transparent},
        )  # fmt: skip

    def _size(self, aspect: str) -> tuple[int, int]:
        aw, ah = ASPECTS.get(aspect, (1, 1))
        scale = self.base / max(aw, ah)
        return max(16, int(aw * scale) // 16 * 16), max(16, int(ah * scale) // 16 * 16)

    def _draw(self, call: ProviderCall, w: int, h: int, digest: bytes) -> bytes:
        key = _KEY_RE.search(call.prompt)
        if call.transparent:
            background: tuple[int, ...] = (0, 0, 0, 0)
        elif key:
            hexcode = key.group(1)
            background = tuple(int(hexcode[i : i + 2], 16) for i in (1, 3, 5)) + (255,)
        else:
            background = (digest[3], digest[4], digest[5], 255)
        image = Image.new("RGBA", (w, h), background)
        draw = ImageDraw.Draw(image)
        colour = (40 + digest[0] % 160, 40 + digest[1] % 160, 40 + digest[2] % 160, 255)
        draw.ellipse((w * 0.2, h * 0.15, w * 0.8, h * 0.85), fill=colour)
        draw.rectangle((w * 0.4, h * 0.35, w * 0.6, h * 0.55), fill=(255 - colour[0], 255 - colour[1], 30, 255))
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
