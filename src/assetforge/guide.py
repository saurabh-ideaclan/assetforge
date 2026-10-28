"""`assetforge guide`: the agent-facing quick reference, as data (kept next to the code it describes)."""

from __future__ import annotations

from typing import Any

from assetforge.art import ArtDirection
from assetforge.core.models import AssetType
from assetforge.methods import METHODS

WORKFLOW = [
    {"step": "search", "do": "Look for something reusable first (text, filters, or visual similarity)",
     "command": 'assetforge search "transparent dark fantasy enemy" --type enemy --json'},
    {"step": "art profile", "do": "Once per game: describe its visual identity (or reuse a preset)",
     "command": "assetforge art profile set my-game --file art.yaml --json"},
    {"step": "decide", "do": "Ask what a request would do: reuse (and how it would be adapted) or generate (and cost)",
     "command": 'assetforge request --type enemy -d "hunched graveyard ghoul" --project my-game --size 128x128 '
                "--dry-run --json"},
    {"step": "get it", "do": "Run the same request without --dry-run; reused assets are resized/converted as variants",
     "command": 'assetforge request --type enemy -d "hunched graveyard ghoul" --project my-game --size 128x128 --json'},
    {"step": "or make it yourself", "do": "SVG icons / procedural textures you wrote: catalog them",
     "command": "assetforge add icon.svg --method svg --type icon --project my-game --json"},
    {"step": "animate", "do": "Generate a pose sheet in one call, then slice it into aligned frames",
     "command": 'assetforge image generate -p "knight" --type spritesheet --grid 4x2 --pose idle --pose walk ... '
                "--project my-game --slice --json"},
    {"step": "sound profile", "do": "Once per game: how it sounds (genre, mood, space, key, tempo, instruments)",
     "command": "assetforge sound profile set my-game --genre synthwave --mood tense --space room --key 'A minor' "
                "--json"},
    {"step": "sounds", "do": "SFX, ambience and music the same way: reuse first, else synthesize (free)",
     "command": "assetforge request --type music -d 'tense boss fight loop 140bpm' --project my-game --sound stems=true "
                "--json"},
    {"step": "review", "do": "Look at the image (path in the JSON); if it misses the brief, regenerate with feedback",
     "command": 'assetforge regenerate ast_00042 -m "too cartoonish; grittier, realistic proportions" --json'},
    {"step": "approve", "do": "Approve the keeper (QC errors block approval)",
     "command": "assetforge review approve ast_00043 --json"},
    {"step": "export", "do": "Write named files, atlases, animations.json and the Pixi manifest into the game",
     "command": "assetforge export --project my-game --out ./public/assets --atlas sprites --json"},
]

COMMANDS = {
    "find": ["search QUERY [--type --category --genre --theme --style --color --orientation --tone --transparent "
             "--min-width --max-width --aspect --format --origin --project --bpm --key --loopable --channels]",
             "similar REF (images or sounds)", "duplicates",
             "get REF", "usage --project P", "methods", "art presets"],
    "make": ["request --type T -d TEXT [--project --style --art-file --size --format --process --method --sound K=V --dry-run]",
             "image generate -p TEXT [--type --style --mood --lighting --palette --avoid --grid --pose --slice]",
             "image edit REF -p CHANGE", "add FILE --method svg|procedural|file", "regenerate REF -m FEEDBACK"],
    "sound": ["audio recipes [NAME]", "audio generate -r RECIPE [--preset -p k=v -n N]", "audio design -f layers.json",
              "audio patch -f patch.json", "audio vary REF", "ambience scenes | layers",
              "ambience generate SCENE [--length --intensity --time --stems --intensity-level]",
              "music genres | instruments", "music compose [--genre --mood --tempo --key --bars|--seconds --form "
              "--stinger --energy -i role=instrument --stems --intensity-layer]",
              "music render --score F | --midi F", "music score REF [--midi out.mid]",
              "music edit REF [--tempo --key --transpose -i role=inst --mute role]",
              "music vary REF [--keep chords|melody]", "sound profile set|show|list", "audio soundfont status|install"],
    "shape": ["process REF --preset web-sprite", "image process REF --resize WxH --recolor ...",
              "sprite slice REF --grid CxR -a NAME=START-END@FPS", "sprite assemble REF... --name N --fps F"],
    "organize": ["update REF [--category --subcategory --theme --genre --add-tag --style]", "use REF --project P",
                 "collection add NAME REF...", "review approve|reject|pick", "art profile set|show|list"],
    "ship": ["export --project P --out DIR [--atlas NAME]", "report --range last-30-days", "budget"],
}

REQUEST_EXAMPLE = {
    "type": "enemy",
    "description": "hunched graveyard ghoul with lantern eyes",
    "project": "crypt-run",
    "purpose": "common melee enemy of level 1",
    "style": "dark-fantasy",
    "art": {"mood": "cold dread", "lighting": "moonlight from the left, green lantern glow",
            "avoid": ["cute faces", "toy-like proportions"]},
    "size": "256x256",
    "format": "webp",
    "category": "creature",
    "max_cost": 0.2,
}

PROFILE_EXAMPLE = """game: space_survivor
style: sci-fi
mood: dark
perspective: side_view
palette: ["#17212D", "#38536B", "#FF7A32"]
lighting: cinematic
detail: high
avoid: [childish_shapes, toy_like_objects, generic_cubes]
"""

SOUND_PROFILE_EXAMPLE = """game: crypt-run
genre: horror
mood: tense
space: cave
key: D minor
tempo: [70, 110]
instruments: [strings, choir, drums_orchestral]
sfx_preset: horror
"""

SOUND_EXAMPLES = [
    {"type": "sfx", "description": "heavy wooden door slam", "project": "crypt-run"},
    {"type": "sfx", "description": "deep rising alien wobble", "sound": {"variants": 4}},
    {"type": "ambience", "description": "rainy forest at night", "sound": {"length": 60, "stems": True}},
    {"type": "music", "description": "tense boss fight loop 140bpm in D minor",
     "sound": {"stems": True, "intensity_layers": [0.3, 0.9]}},
    {"type": "music", "description": "victory jingle", "project": "crypt-run"},
]


def guide_document(presets: list[str], home: str) -> dict[str, Any]:
    return {
        "library": home,
        "principles": [
            "Search before generating; `request` does this for you and adapts close matches.",
            "Every command takes --json and prints one JSON document; nothing ever prompts.",
            "Art direction is yours: use a preset, a game's art profile, or write your own (`art` fields).",
            "Choose the method per asset (see `methods`): AI for illustration, SVG for crisp UI, scripts for "
            "patterns, slicing for animation.",
            "Originals are never modified; processing makes variants or derived assets with lineage.",
        ],
        "workflow": WORKFLOW,
        "commands": COMMANDS,
        "types": [t.value for t in AssetType],
        "art": {"presets": presets, "fields": {name: (f.description or "") for name, f in ArtDirection.model_fields.items()},
                "profile_yaml_example": PROFILE_EXAMPLE,
                "merge_order": "preset of the profile < project profile < requested preset < request art"},
        "request_example": REQUEST_EXAMPLE,
        "sound": {"profile_yaml_example": SOUND_PROFILE_EXAMPLE, "request_examples": SOUND_EXAMPLES,
                  "loudness_lufs": {"sfx": -14, "stinger": -14, "music": -16, "ambience": -24},
                  "notes": ["Loops (ambience, music form loop) are exact-length and seamless; use OGG for them",
                            "Each music take stores its score (JSON + MIDI) for `music edit`",
                            "Stems/intensity versions sum to or match the main mix for adaptive audio"]},
        "methods": [{"name": m["name"], "command": m["command"], "limits": m["limits"]} for m in METHODS],
        "errors": "assetforge errors --json lists every error code, its exit code and what to do",
    }
