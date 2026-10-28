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
        "good_for": ["80 seeded, free sound-effect recipes: UI, pickups, jumps, hits, explosions, weapons, magic, "
                     "creatures, machines, doors, footsteps on 10 surfaces, foley, weather one-shots"],
        "limits": ["no speech or vocals", "synthesized, not recorded: close to real foley, not identical"],
        "tips": "Presets (soft, retro, heavy, magical, scifi, horror, underwater, distant, radio, huge…) change the "
                "character; `audio vary` adds seeded variations for round-robin playback.",
    },
    {
        "name": "sound_design",
        "command": "assetforge audio design -f design.json (layers of recipes and synth patches) | audio patch -f patch.json",
        "good_for": ["any sound no recipe covers: layer recipes in time (charge + impact + debris)",
                     "custom synth voices: oscillators, FM, noise, plucks, pitch/filter envelopes, vibrato, drive"],
        "limits": ["you describe the sound as data; `request` builds a simple patch from words when no recipe fits"],
    },
    {
        "name": "audio_ambience",
        "command": "assetforge ambience generate forest_night --length 60 [--stems --intensity-level 0.2 "
                   "--intensity-level 1] (or request --type ambience -d 'rainy forest at night')",
        "good_for": ["seamless environment loops: weather, nature, caves, dungeons, towns, taverns, factories, "
                     "space stations, underwater (24 scenes, 18 beds, 25 events)",
                     "stems per layer and matched calm/intense versions for adaptive mixing"],
        "limits": ["textures and sparse events, not recorded field audio", "no intelligible speech (crowds murmur)"],
    },
    {
        "name": "music_composer",
        "command": "assetforge music compose --genre synthwave --mood tense --seconds 60 [--stems "
                   "--intensity-layer 0.2 --intensity-layer 0.9] | music compose --stinger victory "
                   "(or request --type music -d 'calm fantasy village loop')",
        "good_for": ["original loops, linear cues and stingers in 17 genres (chiptune, synthwave, orchestral, "
                     "battle, boss, village, ambient, lo-fi, puzzle, jazz, rock, horror, mystery, sci-fi, menu, credits)",
                     "stems and matched energy layers sharing tempo, key and tune"],
        "limits": ["rule-based: memorable loops, not hit songs", "no vocals",
                   "acoustic instruments sound synthetic unless a SoundFont is installed"],
        "tips": "Set a sound profile (genre, key, tempo, instruments) so a game's tracks match; `music vary --keep "
                "chords|melody` and `music edit` refine a piece without starting over.",
    },
    {
        "name": "music_score",
        "command": "assetforge music render --score score.json | --midi theme.mid",
        "good_for": ["exact music you (or an agent) wrote note by note", "existing MIDI files",
                     "editing a composed piece: `music score REF` → edit JSON → render"],
        "limits": ["you choose every note"],
    },
    {
        "name": "soundfont",
        "command": "brew install fluid-synth && assetforge audio soundfont install --yes",
        "good_for": ["realistic piano, strings, brass, guitars, choirs and drum kits for music"],
        "limits": ["optional system program and a ~30 MB download; without them music uses synth instruments"],
    },
]
