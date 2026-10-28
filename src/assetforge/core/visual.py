"""Cheap visual features for discovery: dominant colours, tone, transparency, aspect and a perceptual hash.

Everything is computed locally with Pillow + NumPy when a file is cataloged. The features feed the
search index (colour names and technical words such as "transparent" or "landscape" become FTS terms),
the `--color/--orientation/--tone` filters, `similar` and near-duplicate detection.
"""

from __future__ import annotations

import colorsys
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from PIL import Image as PILImage

    from assetforge.core.catalog import Library

COLOR_NAMES = ("red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "brown", "black", "white", "gray")
DARK_LUMA, LIGHT_LUMA = 0.32, 0.68
NEAR_DUPLICATE_BITS = 6  # dHash Hamming distance (of 64) treated as the same picture...
NEAR_DUPLICATE_COLORS = 0.6  # ...when the colour mixes also overlap this much (dHash alone ignores hue)


def color_name(rgb: tuple[int, int, int]) -> str:
    """Map an sRGB colour to one of COLOR_NAMES (coarse, so queries like "green" or "dark red" work)."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    if v < 0.16:
        return "black"
    if s < 0.13:
        return "white" if v > 0.85 else "gray"
    hue = h * 360
    if 12 <= hue < 48 and v < 0.62:
        return "brown"
    for limit, name in ((12, "red"), (40, "orange"), (68, "yellow"), (160, "green"), (195, "teal"),
                        (255, "blue"), (290, "purple"), (340, "pink"), (361, "red")):  # fmt: skip
        if hue < limit:
            return name
    return "red"


def parse_color(value: str) -> str:
    """A colour filter value: a name from COLOR_NAMES or a hex colour (mapped to its name)."""
    value = value.strip().lower()
    if value in COLOR_NAMES or value == "grey":
        return "gray" if value == "grey" else value
    text = value.lstrip("#")
    if len(text) in (3, 6) and all(c in "0123456789abcdef" for c in text):
        if len(text) == 3:
            text = "".join(c * 2 for c in text)
        return color_name((int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)))
    from assetforge.errors import InvalidInputError

    raise InvalidInputError(f"Unknown colour {value!r}", hint=f"Use a hex colour or one of: {', '.join(COLOR_NAMES)}")


def features_from_image(image: PILImage.Image) -> dict[str, Any]:
    from PIL import Image

    rgba = image.convert("RGBA")
    width, height = rgba.size
    small = rgba.copy()
    small.thumbnail((96, 96), Image.Resampling.BILINEAR)
    pixels = np.asarray(small, dtype=np.uint8).reshape(-1, 4)
    opaque = pixels[pixels[:, 3] > 128][:, :3]
    alpha_fraction = float(1 - (pixels[:, 3] > 16).mean()) if len(pixels) else 0.0
    if len(opaque) == 0:
        mean_luma, colors = 0.0, []
    else:
        luma = (opaque @ np.array([0.2126, 0.7152, 0.0722])) / 255
        mean_luma = float(luma.mean())
        colors = _dominant_colors(opaque)
    return {
        "aspect": round(width / height, 4) if height else None,
        "alpha_fraction": round(alpha_fraction, 4),
        "mean_luma": round(mean_luma, 4),
        "colors": colors,
        "dhash": dhash(rgba),
    }


def features(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        image.seek(0)
        return features_from_image(image)


def _dominant_colors(rgb: np.ndarray, top: int = 5) -> list[dict[str, Any]]:
    from PIL import Image

    strip = Image.fromarray(rgb.reshape(1, -1, 3), "RGB")
    quantized = strip.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = sorted(quantized.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    result: list[dict[str, Any]] = []
    for count, index in counts[:top]:
        r, g, b = palette[index * 3 : index * 3 + 3]
        result.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "name": color_name((r, g, b)), "share": round(count / total, 3)})
    return result


def dhash(image: PILImage.Image) -> str:
    """64-bit difference hash of the picture composited on mid-gray, as 16 hex digits."""
    from PIL import Image

    base = Image.new("RGBA", image.size, (128, 128, 128, 255))
    base.alpha_composite(image.convert("RGBA"))
    gray = np.asarray(base.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (gray[:, 1:] > gray[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def palette_overlap(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(min(v, b.get(k, 0.0)) for k, v in a.items())


def _same_picture(a: Any, b: Any, threshold: int) -> int | None:
    """dHash distance when rows a and b show the same picture, else None."""
    distance = hamming(a["dhash"], b["dhash"])
    if distance > threshold:
        return None
    overlap = palette_overlap(color_shares(json.loads(a["colors"] or "[]")), color_shares(json.loads(b["colors"] or "[]")))
    return distance if overlap >= NEAR_DUPLICATE_COLORS else None


def color_shares(colors: list[dict[str, Any]] | None) -> dict[str, float]:
    shares: dict[str, float] = {}
    for c in colors or []:
        shares[c["name"]] = shares.get(c["name"], 0.0) + c["share"]
    return shares


def orientation(aspect: float | None) -> str | None:
    if not aspect:
        return None
    if aspect > 1.15:
        return "landscape"
    if aspect < 0.87:
        return "portrait"
    return "square"


def tone(mean_luma: float | None) -> str | None:
    if mean_luma is None:
        return None
    if mean_luma < DARK_LUMA:
        return "dark"
    if mean_luma > LIGHT_LUMA:
        return "light"
    return "mid"


def tech_terms(row: Any) -> str:
    """Searchable technical words for an asset's primary file (`row` has asset_files columns)."""
    if row is None or row["kind"] is None:
        return ""
    words = [row["kind"], (row["ext"] or "").lstrip(".")]
    if row["kind"] in ("image", "vector"):
        words.append("transparent" if row["has_alpha"] else "opaque")
        words += [w for w in (orientation(row["aspect"]), tone(row["mean_luma"])) if w]
    elif row["kind"] == "audio" and "audio" in row.keys():
        from assetforge.audio.features import tech_terms as audio_terms

        words += audio_terms(row["audio"], loop=bool(row["loop"]))
    return " ".join(w for w in words if w)


def color_terms(colors_json: str | None) -> str:
    """Colour names weighted by share (repeated), so a mostly-green image ranks above a speck of green."""
    shares = color_shares(json.loads(colors_json) if colors_json else [])
    return " ".join(" ".join([name] * max(1, round(share * 4))) for name, share in shares.items() if share >= 0.05)


# ---------------------------------------------------------------------- similarity


def _features_row(lib: Library, asset_id: str) -> Any:
    return lib.conn.execute(
        "SELECT a.id, a.type, a.style, a.category, a.project, f.sha256, f.rel_path, f.kind, f.dhash, f.colors, "
        "f.aspect FROM assets a JOIN asset_variants v ON v.id = a.primary_variant_id "
        "JOIN asset_files f ON f.sha256 = v.file_sha256 WHERE a.id = ?",
        (asset_id,),
    ).fetchone()


def ensure_features(lib: Library, sha256: str) -> None:
    """Compute and store features for a cataloged image file that predates them."""
    from assetforge.core import db
    from assetforge.core.util import dumps

    row = lib.conn.execute("SELECT rel_path, kind, ext, dhash FROM asset_files WHERE sha256 = ?", (sha256,)).fetchone()
    if row is None or row["dhash"] or row["kind"] not in ("image", "vector"):
        return
    try:
        if row["kind"] == "vector":
            from assetforge.image.svg import render_svg

            image = render_svg(lib.store.abs_path(row["rel_path"]), size=256)
            feats = features_from_image(image)
        else:
            feats = features(lib.store.abs_path(row["rel_path"]))
    except Exception:  # an undecodable file is QC's business; leave the features empty
        return
    with db.transaction(lib.conn):
        lib.conn.execute(
            "UPDATE asset_files SET aspect = ?, alpha_fraction = ?, mean_luma = ?, colors = ?, dhash = ? "
            "WHERE sha256 = ?",
            (feats["aspect"], feats["alpha_fraction"], feats["mean_luma"], dumps(feats["colors"]), feats["dhash"],
             sha256),
        )  # fmt: skip


def similar(lib: Library, ref: str, *, limit: int = 10, where: str = "1", params: list[Any] | None = None,
            include_self: bool = False) -> list[tuple[str, float, int]]:  # fmt: skip
    """Assets that look like `ref`: (asset_id, similarity 0-1, dhash distance), best first.

    Similarity mixes the perceptual hash (shape and layout), the colour mix, and shared
    type/category/style. `where`/`params` narrow the candidates (an SQL condition on `a`/`pf`).
    """
    from assetforge.errors import InvalidInputError

    asset_id = lib.resolve_asset_id(ref)
    base = _features_row(lib, asset_id)
    if base is None or base["kind"] not in ("image", "vector"):
        raise InvalidInputError(f"{asset_id} is not an image; similarity works on images", asset_id=asset_id)
    ensure_features(lib, base["sha256"])
    base = _features_row(lib, asset_id)
    if not base["dhash"]:
        raise InvalidInputError(f"Cannot read the image of {asset_id}", asset_id=asset_id)
    base_colors = color_shares(json.loads(base["colors"] or "[]"))
    rows = lib.conn.execute(
        "SELECT a.id, a.type, a.style, a.category, pf.dhash, pf.colors, pf.aspect FROM assets a "
        "JOIN asset_variants pv ON pv.id = a.primary_variant_id JOIN asset_files pf ON pf.sha256 = pv.file_sha256 "
        f"WHERE pf.dhash IS NOT NULL AND {where}",
        params or [],
    ).fetchall()
    scored: list[tuple[str, float, int]] = []
    for row in rows:
        if row["id"] == asset_id and not include_self:
            continue
        distance = hamming(base["dhash"], row["dhash"])
        shape = max(0.0, 1 - distance / 32)
        shares = color_shares(json.loads(row["colors"] or "[]"))
        palette = palette_overlap(base_colors, shares)
        aspect = 1.0 if not (row["aspect"] and base["aspect"]) else min(row["aspect"], base["aspect"]) / max(
            row["aspect"], base["aspect"])  # fmt: skip
        score = 0.5 * shape + 0.25 * palette + 0.1 * aspect
        score += 0.06 * (row["type"] == base["type"]) + 0.05 * bool(base["category"] and row["category"] == base[
            "category"]) + 0.04 * bool(base["style"] and row["style"] == base["style"])  # fmt: skip
        scored.append((row["id"], round(score, 4), distance))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:limit]


def near_duplicates(lib: Library, asset_id: str, *, threshold: int = NEAR_DUPLICATE_BITS) -> list[dict[str, Any]]:
    """Other assets whose primary image is visually the same picture (dHash distance ≤ threshold)."""
    base = _features_row(lib, asset_id)
    if base is None or not base["dhash"]:
        return []
    rows = lib.conn.execute(
        "SELECT a.id, pf.dhash, pf.colors FROM assets a JOIN asset_variants pv ON pv.id = a.primary_variant_id "
        "JOIN asset_files pf ON pf.sha256 = pv.file_sha256 WHERE pf.dhash IS NOT NULL AND a.id != ? "
        "AND a.status != 'rejected'",
        (asset_id,),
    ).fetchall()
    found = [{"asset_id": r["id"], "distance": d} for r in rows if (d := _same_picture(base, r, threshold)) is not None]
    return sorted(found, key=lambda f: (f["distance"], f["asset_id"]))


def duplicate_groups(lib: Library, *, threshold: int = NEAR_DUPLICATE_BITS, include_rejected: bool = False
                     ) -> list[list[dict[str, Any]]]:  # fmt: skip
    """Clusters of assets whose primary images look the same (single-linkage on dHash distance)."""
    rows = lib.conn.execute(
        "SELECT a.id, a.name, a.status, pf.dhash, pf.colors FROM assets a JOIN asset_variants pv ON pv.id = a.primary_variant_id "
        "JOIN asset_files pf ON pf.sha256 = pv.file_sha256 WHERE pf.dhash IS NOT NULL"
        + ("" if include_rejected else " AND a.status != 'rejected'") + " ORDER BY a.id"
    ).fetchall()  # fmt: skip
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if _same_picture(rows[i], rows[j], threshold) is not None:
                parent[find(j)] = find(i)
    groups: dict[int, list[dict[str, Any]]] = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append({"asset_id": row["id"], "name": row["name"], "status": row["status"]})
    return [g for g in groups.values() if len(g) > 1]
