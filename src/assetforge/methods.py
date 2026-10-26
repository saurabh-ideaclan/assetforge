"""Which generation method suits which asset, with each method's limits (`assetforge methods`)."""

from __future__ import annotations

from typing import Any

METHODS: list[dict[str, Any]] = [
    {
        "name": "ai_image",
        "command": "assetforge request --type T -d '...' (or image generate -p '...')",
        "good_for": ["illustrated characters and enemies", "backgrounds and scenes", "detailed props",
                     "painterly, realistic or stylized looks", "pose sheets drawn in one call"],
        "limits": [
            "costs money per image (estimate first with --dry-run)",
            "separate calls drift: the same character may change between images; draw related poses on one "
            "sheet (--grid) or edit from a reference instead",
            "unreliable text, exact counts and precise geometry",
            "Gemini has no real transparency: the background is keyed out (flat key colour), which can leave fringes",
            "default 1K resolution; detail: high (or --quality high) asks for 2K",
        ],
        "tips": "Set a project art profile once; pass --style or art overrides per asset; label references.",
    },
    {
        "name": "edit_variation",
        "command": "assetforge image edit SOURCE -p 'same character, now jumping' (-n 3)",
        "good_for": ["new poses or expressions of an existing design", "recolours that need repainting",
                     "fixing one detail of a good image"],
        "limits": ["paid", "large changes drift from the source; keep instructions small"],
    },
    {
        "name": "svg",
        "command": "write icon.svg yourself, then: assetforge add icon.svg --method svg --type icon --size 256",
        "good_for": ["UI icons and buttons", "flat-vector and minimalist styles", "badges, arrows, frames",
                     "anything that must stay crisp at many sizes"],
        "limits": ["no painterly texture or soft lighting", "complex organic subjects look clip-art-like",
                   "text renders with system fonts"],
        "tips": "The SVG master is kept; a PNG is rendered with resvg and used for QC, search and export.",
    },
    {
        "name": "procedural_image",
        "command": "write gen.py (Pillow/NumPy) yourself, then: assetforge add out.png --method procedural "
                   "--script gen.py",
        "good_for": ["seamless patterns and noise textures", "particles, glows, sparks", "geometric tiles",
                     "placeholder or debug art", "anything parametric you'll want many variants of"],
        "limits": ["only as good as the code; organic characters are impractical"],
        "tips": "The script is stored as provenance so the asset can be regenerated or tweaked later.",
    },
    {
        "name": "process",
        "command": "assetforge process REF --preset web-sprite | image process REF --resize 128x128 --recolor ...",
        "good_for": ["resize, crop, trim, pad", "recolour (hue, tint, saturation)", "flip/rotate",
                     "format conversion", "adapting a reused asset to a new game"],
        "limits": ["can't change what is drawn"],
        "tips": "`request --size/--format/--process` does this automatically when it reuses an asset.",
    },
    {
        "name": "sprite_slice",
        "command": "assetforge sprite slice SHEET --grid 4x2 -a walk=0-3@8 -a idle=4-7@4 --cell 128",
        "good_for": ["turning an AI pose sheet into aligned animation frames", "atlas-ready frames with anchors"],
        "limits": ["needs subjects separated by empty space, one per grid cell"],
        "tips": "Generate with --grid/--pose (one call = consistent design), then slice; or pass --slice.",
    },
    {
        "name": "sprite_assemble",
        "command": "assetforge sprite assemble F1 F2 F3 F4 --name hero_walk --fps 10",
        "good_for": ["normalizing separately made frames (shared scale, common baseline)"],
        "limits": ["can't fix frames whose design differs; regenerate those as a sheet"],
    },
    {
        "name": "audio_procedural",
        "command": "assetforge request --type sfx -d 'soft coin pickup' (or audio generate RECIPE)",
        "good_for": ["UI clicks, pickups, hits, jumps, explosions, alerts (21 recipes, seeded and free)"],
        "limits": ["sound effects only, no music or voice; import licensed music instead"],
    },
]
