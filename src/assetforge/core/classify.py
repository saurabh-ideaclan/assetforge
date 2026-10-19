"""Heuristics that turn an imported file's path into an asset type and tags."""

from __future__ import annotations

import re

from assetforge.core.metadata import FileMeta
from assetforge.core.models import AssetType, FileKind

_TOKEN = re.compile(r"[a-z0-9]+")

# Checked in order for each path token; the first token that matches decides.
_IMAGE_HINTS: dict[AssetType, set[str]] = {
    AssetType.SPRITESHEET: {"spritesheet", "spritesheets", "sheet", "sheets", "atlas"},
    AssetType.BACKGROUND: {"background", "backgrounds", "bg", "bgs", "backdrop", "parallax", "sky", "scenery"},
    AssetType.TILE: {"tile", "tiles", "tileset", "tilesets", "tilemap", "terrain"},
    AssetType.ICON: {"icon", "icons"},
    AssetType.UI: {"ui", "gui", "hud", "button", "buttons", "panel", "panels", "menu", "cursor"},
    AssetType.EFFECT: {"fx", "vfx", "effect", "effects", "particle", "particles", "explosion"},
    AssetType.CHARACTER: {"character", "characters", "player", "hero", "enemy", "enemies", "npc", "npcs"},
    AssetType.SPRITE: {"sprite", "sprites", "object", "objects", "item", "items", "prop", "props"},
}
_AUDIO_HINTS: dict[AssetType, set[str]] = {
    AssetType.MUSIC: {"music", "bgm", "song", "songs", "soundtrack", "ost", "theme", "jingle", "jingles"},
    AssetType.AMBIENCE: {"ambience", "ambient", "amb", "atmosphere", "atmos"},
    AssetType.VOICE: {"voice", "voices", "vo", "dialog", "dialogue", "speech"},
    AssetType.SFX: {"sfx", "sound", "sounds", "fx", "effects", "ui"},
}
MUSIC_MIN_DURATION_MS = 20_000


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _match(candidates: list[str], hints: dict[AssetType, set[str]]) -> AssetType | None:
    for token in candidates:
        for asset_type, words in hints.items():
            if token in words:
                return asset_type
    return None


def infer_type(stem: str, dir_parts: tuple[str, ...], meta: FileMeta) -> AssetType:
    """Filename tokens first, then directory names from the nearest one outward."""
    candidates = tokens(stem) + [t for part in reversed(dir_parts) for t in tokens(part)]
    if meta.kind in (FileKind.IMAGE, FileKind.VECTOR):
        return _match(candidates, _IMAGE_HINTS) or AssetType.IMAGE
    if meta.kind is FileKind.AUDIO:
        if found := _match(candidates, _AUDIO_HINTS):
            return found
        if meta.duration_ms is not None and meta.duration_ms >= MUSIC_MIN_DURATION_MS:
            return AssetType.MUSIC
        return AssetType.SFX
    if meta.kind is FileKind.FONT:
        return AssetType.FONT
    if meta.kind is FileKind.SOURCE:
        return AssetType.SOURCE
    return AssetType.OTHER


def path_tags(dir_parts: tuple[str, ...]) -> list[str]:
    """Directory names below the import root become tags: ui/buttons/x.png -> ['ui', 'buttons']."""
    tags: list[str] = []
    for part in dir_parts:
        tag = "-".join(tokens(part))
        if tag and not tag.isdigit() and tag not in tags:
            tags.append(tag)
    return tags
