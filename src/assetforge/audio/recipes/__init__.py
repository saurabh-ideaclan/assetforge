"""The SFX recipe registry. Importing this package registers every built-in recipe."""

from assetforge.audio.recipes import impact, motion, reward, ui  # noqa: F401  (registration side effects)
from assetforge.audio.recipes.base import (
    PRESETS,
    RECIPES,
    Param,
    Recipe,
    RenderContext,
    get_recipe,
    render_recipe,
    resolve_params,
    vary_params,
)

__all__ = [
    "PRESETS",
    "RECIPES",
    "Param",
    "Recipe",
    "RenderContext",
    "get_recipe",
    "render_recipe",
    "resolve_params",
    "vary_params",
]
