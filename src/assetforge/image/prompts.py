"""Prompt templates: turn an agent's request into a game-asset-specific provider prompt.

The user's words always come first. Templates then add neutral framing (what kind of asset it is,
never how it should look), the merged art direction (`assetforge.art`), labelled reference images,
and background instructions. The final prompt is stored on every generated variant so results can
be audited and repeated. Bump PROMPT_VERSION whenever the wording changes: it is part of the request
fingerprint, so identical requests regenerate instead of reusing images made with the old wording.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from assetforge.core.models import ISOLATED_TYPES, AssetType, StyleProfile

PROMPT_VERSION = 2

# Framing says what the asset is and how it sits in the frame. It deliberately says nothing about
# the look (shapes, outlines, colours): that comes from the art direction, so styles stay distinct.
FRAMING: dict[AssetType, str] = {
    AssetType.CHARACTER: "A single game character, full body, centered with empty margin around it, "
    "with a clear readable silhouette.",
    AssetType.ENEMY: "A single enemy for a game, full body, centered with empty margin around it, "
    "with a clear readable silhouette.",
    AssetType.SPRITE: "A single game sprite, centered with empty margin around it, with a clear silhouette.",
    AssetType.OBJECT: "A single game object or prop, centered with empty margin around it, with a clear silhouette.",
    AssetType.ICON: "A single game icon, centered, readable at small sizes.",
    AssetType.BACKGROUND: "A game background scene with a wide composition and no characters, "
    "leaving clear space for gameplay in the foreground.",
    AssetType.TILE: "A seamless tileable game tile whose edges wrap perfectly, viewed straight on, even lighting.",
    AssetType.TEXTURE: "A seamless tileable surface texture whose edges wrap perfectly, viewed straight on, "
    "even lighting.",
    AssetType.UI: "A single game UI element, centered, with crisp clean edges.",
    AssetType.EFFECT: "A single game visual-effect sprite, centered and isolated.",
    AssetType.SPRITESHEET: "A game sprite sheet: equal-size frames on an even grid with wide empty gaps between "
    "frames.",
    AssetType.ANIMATION: "Animation frames for a game on an even grid with wide empty gaps between frames.",
}
NO_TEXT = "No text, letters, watermark or signature."

DEFAULT_ASPECT: dict[AssetType, str] = {AssetType.BACKGROUND: "16:9", AssetType.CHARACTER: "2:3",
                                        AssetType.ENEMY: "2:3"}  # fmt: skip
TRANSPARENT_BY_DEFAULT = ISOLATED_TYPES | {AssetType.SPRITESHEET, AssetType.ANIMATION}

# Key colours for providers without native transparency; magenta first because games are full of greens.
KEY_COLOURS = [("magenta", "#FF00FF"), ("green", "#00FF00")]


class SheetSpec(BaseModel):
    """Layout of a generated sprite sheet: the grid, and what goes in each cell (row by row)."""

    model_config = ConfigDict(extra="forbid")

    columns: int = Field(ge=1, le=12)
    rows: int = Field(1, ge=1, le=12)
    poses: list[str] = Field(default_factory=list, description="One entry per cell, left to right, top to bottom")
    one_character: bool = Field(True, description="Every frame shows the same character (animation/pose sheet)")
    baseline: bool = Field(True, description="Feet/bases on one shared baseline in every cell")

    @property
    def frames(self) -> int:
        return self.columns * self.rows


def sheet_text(sheet: SheetSpec) -> str:
    parts = [f"A sprite sheet laid out as a grid of {sheet.columns} columns by {sheet.rows} rows "
             f"({sheet.frames} equal cells) with wide empty gaps: nothing touches another cell or the image edge."]  # fmt: skip
    if sheet.one_character:
        parts.append("Every cell shows the same character with an identical design, size, colours and lighting; "
                     "only the pose changes.")  # fmt: skip
    else:
        parts.append("Each cell holds one separate item, all at a consistent scale, art style and lighting.")
    if sheet.baseline:
        parts.append("Feet or bases sit on the same baseline in every cell.")
    if sheet.poses:
        rows = [sheet.poses[i : i + sheet.columns] for i in range(0, len(sheet.poses), sheet.columns)]
        for number, row in enumerate(rows, start=1):
            cells = "; ".join(f"{i}. {pose.strip().rstrip('.')}" for i, pose in enumerate(row, start=1))
            parts.append(f"Row {number}, left to right: {cells}.")
    return " ".join(parts)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def pick_key_colour(text: str, palette: list[str]) -> tuple[str, str]:
    """The key colour least likely to appear in the subject: avoid words and palette colours near it."""
    lowered = text.lower()
    if any(w in lowered for w in ("pink", "magenta", "purple", "violet", "fuchsia")):
        return KEY_COLOURS[1]
    if palette:
        def nearest(key_hex: str) -> float:
            kr, kg, kb = _hex_rgb(key_hex)
            return min(((r - kr) ** 2 + (g - kg) ** 2 + (b - kb) ** 2) ** 0.5 for r, g, b in map(_hex_rgb, palette))

        return max(KEY_COLOURS, key=lambda key: nearest(key[1]))
    return KEY_COLOURS[0]


@dataclass(frozen=True)
class PromptParts:
    final: str
    key_colour: str | None  # hex, when the background must be keyed out afterwards


def style_text(style_name: str | None, profile: StyleProfile | None, has_style_refs: bool) -> str:
    """Art text for a legacy style profile or bare style name (kept for callers without an ArtDirection)."""
    from assetforge import art

    if profile is not None:
        direction = art.merge(art.ArtDirection(style=profile.name), art.from_style_profile(profile))
        return art.render(direction)
    if style_name:
        return art.render(art.ArtDirection(style=style_name))
    return ""


def reference_text(*, user_refs: int, style_refs: int, edit: bool) -> str:
    """Say which input image is which: providers like Gemini receive the images without any labels."""
    parts: list[str] = []
    index = 2 if edit else 1
    if user_refs:
        span = f"Image {index}" if user_refs == 1 else f"Images {index}-{index + user_refs - 1}"
        parts.append(f"{span}: subject reference; keep the subject's design consistent with it.")
        index += user_refs
    if style_refs:
        span = f"Image {index}" if style_refs == 1 else f"Images {index}-{index + style_refs - 1}"
        parts.append(f"{span}: art-style reference only; match its rendering style, palette and line work, "
                     "not its subject.")  # fmt: skip
    if edit and parts:
        parts.insert(0, "Image 1 is the image to edit.")
    return " ".join(parts)


def keyed_background_text(name: str, key_hex: str) -> str:
    return (f"Render the subject with its full lighting, shading and detail, isolated on a flat, solid pure {name} "
            f"({key_hex}) background that fills the whole frame (it will be removed): no gradient or floor on the "
            f"background, and no {name} in the subject.")  # fmt: skip


def build_prompt(
    prompt: str,
    *,
    asset_type: AssetType,
    transparent: bool,
    native_transparency: bool,
    art_text: str = "",
    palette: list[str] | None = None,
    style_name: str | None = None,
    profile: StyleProfile | None = None,
    has_style_refs: bool = False,
    has_user_refs: bool = False,
    user_refs: int | None = None,
    style_refs: int | None = None,
    negative: str | None = None,
    raw: bool = False,
    edit: bool = False,
    sheet: SheetSpec | None = None,
    purpose: str | None = None,
) -> PromptParts:
    palette = list(palette or (profile.palette if profile else []))
    key_hex = None
    keyed = transparent and not native_transparency
    if raw:
        # The agent's words exactly, plus the one technical sentence keying needs to work at all.
        final = prompt.strip()
        if keyed:
            name, key_hex = pick_key_colour(prompt, palette)
            final = f"{final.rstrip('.')}. {keyed_background_text(name, key_hex)}"
        return PromptParts(final, key_hex)
    parts = [prompt.strip().rstrip(".") + "."]
    if purpose:
        parts.append(f"Purpose in the game: {purpose.strip().rstrip('.')}.")
    if sheet is not None:
        parts.append(sheet_text(sheet))
    elif not edit and asset_type in FRAMING:
        parts.append(FRAMING[asset_type])
    art = art_text or style_text(style_name, profile, has_style_refs)
    if art:
        parts.append(art)
    refs = reference_text(user_refs=user_refs if user_refs is not None else int(has_user_refs),
                          style_refs=style_refs if style_refs is not None else int(has_style_refs), edit=edit)  # fmt: skip
    if refs:
        parts.append(refs)
    if edit:
        parts.append("Keep everything that the instruction does not mention unchanged.")
    if transparent and native_transparency:
        parts.append("Transparent background: only the subject, no backdrop, no ground shadow.")
    elif keyed:
        name, key_hex = pick_key_colour(f"{prompt} {art}", palette)
        parts.append(keyed_background_text(name, key_hex))
    if negative:
        parts.append(f"Avoid: {negative.strip().rstrip('.')}.")
    parts.append(NO_TEXT)
    return PromptParts(" ".join(parts), key_hex)
