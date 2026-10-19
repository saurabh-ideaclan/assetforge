"""Prompt templates: turn a short agent request into a game-asset-specific provider prompt.

The user's words always come first; templates add framing (single centred subject, no text,
room around the silhouette), the style profile, and background instructions. The final
prompt is stored on every generated variant so results can be audited and repeated.
"""

from __future__ import annotations

from dataclasses import dataclass

from assetforge.core.models import AssetType, StyleProfile

FRAMING: dict[AssetType, str] = {
    AssetType.CHARACTER: "A single game character, full body, centered with empty margin around it, "
    "clear readable silhouette, game-ready 2D asset.",
    AssetType.SPRITE: "A single game object sprite, centered with empty margin around it, clear silhouette, "
    "game-ready 2D asset.",
    AssetType.ICON: "A single game icon, centered, bold simple shapes, readable at 64 pixels.",
    AssetType.BACKGROUND: "A game background scene with a wide composition and no characters, "
    "leaving clear space for gameplay in the foreground.",
    AssetType.TILE: "A seamless tileable texture whose edges wrap perfectly, viewed straight on, even lighting.",
    AssetType.UI: "A single game UI element, centered, crisp clean edges.",
    AssetType.EFFECT: "A single game visual-effect sprite, centered and isolated.",
    AssetType.SPRITESHEET: "A game sprite sheet: identical-size frames on an even grid, same character in every frame.",
}
NO_TEXT = "No text, letters, watermark or signature."

DEFAULT_ASPECT: dict[AssetType, str] = {AssetType.BACKGROUND: "16:9", AssetType.CHARACTER: "2:3"}
TRANSPARENT_BY_DEFAULT = {AssetType.CHARACTER, AssetType.SPRITE, AssetType.ICON, AssetType.UI, AssetType.EFFECT}

# Key colours for providers without native transparency; magenta first because games are full of greens.
KEY_COLOURS = [("magenta", "#FF00FF"), ("green", "#00FF00")]


def pick_key_colour(text: str, palette: list[str]) -> tuple[str, str]:
    lowered = text.lower()
    clashes_magenta = any(w in lowered for w in ("pink", "magenta", "purple", "violet", "fuchsia")) or any(
        p.upper() in ("#FF00FF", "#FF33FF", "#E040FB") for p in palette
    )
    return KEY_COLOURS[1] if clashes_magenta else KEY_COLOURS[0]


@dataclass(frozen=True)
class PromptParts:
    final: str
    key_colour: str | None  # hex, when the background must be keyed out afterwards


def style_text(style_name: str | None, profile: StyleProfile | None, has_style_refs: bool) -> str:
    if profile is not None:
        parts = [f"Art style: {profile.description or profile.name.replace('-', ' ')}."]
        if profile.instructions:
            parts.append(profile.instructions.rstrip(".") + ".")
        if profile.palette:
            parts.append("Colour palette: " + ", ".join(profile.palette) + ".")
        if has_style_refs:
            parts.append("Match the art style of the style reference images exactly.")
        return " ".join(parts)
    if style_name:
        return f"Art style: {style_name.replace('-', ' ')}."
    return ""


def build_prompt(
    prompt: str,
    *,
    asset_type: AssetType,
    transparent: bool,
    native_transparency: bool,
    style_name: str | None = None,
    profile: StyleProfile | None = None,
    has_style_refs: bool = False,
    has_user_refs: bool = False,
    negative: str | None = None,
    raw: bool = False,
    edit: bool = False,
) -> PromptParts:
    if raw:
        return PromptParts(prompt.strip(), None)
    parts = [prompt.strip().rstrip(".") + "."]
    if not edit and asset_type in FRAMING:
        parts.append(FRAMING[asset_type])
    if style := style_text(style_name, profile, has_style_refs):
        parts.append(style)
    if has_user_refs:
        parts.append("Use the provided reference image(s) for the subject's design and keep it consistent.")
    if edit:
        parts.append("Keep everything that the instruction does not mention unchanged.")
    key_hex = None
    if transparent and native_transparency:
        parts.append("Transparent background: only the subject, no backdrop, no ground shadow.")
    elif transparent:
        name, key_hex = pick_key_colour(prompt + " " + (profile.instructions if profile else ""),
                                        profile.palette if profile else [])  # fmt: skip
        parts.append(
            f"Place the subject on a perfectly flat, solid pure {name} ({key_hex}) background that fills the whole "
            f"frame: no gradient, no shadow, no floor, and no {name} anywhere in the subject."
        )
    if negative:
        parts.append(f"Avoid: {negative.strip().rstrip('.')}.")
    parts.append(NO_TEXT)
    return PromptParts(" ".join(parts), key_hex)
