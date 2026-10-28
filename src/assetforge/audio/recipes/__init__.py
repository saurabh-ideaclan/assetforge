"""The SFX recipe registry. Importing this package registers every built-in recipe."""

from assetforge.audio.recipes import (  # noqa: F401  (registration side effects)
    creatures,
    foley,
    impact,
    magic,
    mechanical,
    motion,
    reward,
    ui,
    weapons,
    world,
)
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
