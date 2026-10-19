"""Seeded variation planning: one request seed fans out into per-variant params and noise seeds.

Variant 1 of a fresh request is the canonical sound (base params, no jitter); later variants
nudge numeric parameters within their ranges. Each variant also gets its own noise seed.
Everything derives from (seed, index) alone, so any variant can be re-rendered on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from assetforge.audio.recipes.base import Recipe, vary_params

STREAM_PARAMS = 1
STREAM_RENDER = 2


def derive_seed(seed: int, index: int, stream: int) -> int:
    return int(np.random.SeedSequence([seed, index, stream]).generate_state(1)[0])


@dataclass(frozen=True)
class VariantPlan:
    index: int
    params: dict[str, Any]
    render_seed: int


def plan_variants(
    recipe: Recipe,
    base: dict[str, Any],
    *,
    seed: int,
    indices: range,
    variation: float,
    canonical_first: bool = True,
) -> list[VariantPlan]:
    plans = []
    for index in indices:
        if canonical_first and index == 1:
            params = dict(base)
        else:
            params = vary_params(recipe, base, variation, np.random.default_rng(derive_seed(seed, index, STREAM_PARAMS)))
        plans.append(VariantPlan(index, params, derive_seed(seed, index, STREAM_RENDER)))
    return plans
