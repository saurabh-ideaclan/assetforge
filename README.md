# AssetForge

A local-first game asset warehouse with a CLI built for AI agents. Import existing art and audio, search it before
generating anything new, and get stable IDs and file paths back as JSON.

> Status: all six v1 phases are implemented: foundation, procedural audio, AI images, processing & QC, PixiJS
> export, and multi-agent reliability (`request`, job queue, recovery, rate and spend limits). On top of that:
> visual indexing and similarity search, reuse with automatic adaptation, art-style presets and per-game art
> profiles, SVG/procedural assets, sprite-sheet slicing into aligned animations, and a capped review loop.
>
> **Agents: run `assetforge guide --json` first.** It returns the workflow, commands, presets and request format.

## Mac app (for people)

Agents use the CLI. People can use **AssetForge.app**, a native macOS window with:

- **Overview**: counts, spend, what's waiting for review (tiles link to the filtered views).
- **Assets**: search plus filters by type, project, style, category, genre, theme, **colour, shape, tone and
  transparency**, and "find similar". The detail panel has **review** (approve, reject, pick one of a batch),
  editing of name/category/genre/theme/tags, **regenerate with feedback**, **slice a sheet into frames**, an
  **animation player** for sliced sheets, similar-looking assets, QC, lineage and usage. Keyboard: ← → move,
  A approve, X reject, F find similar, / search, Esc close.
- **Generate**: images with a game, a style preset and art direction (mood, lighting, palette, avoid…), sprite
  sheets with per-cell poses and automatic slicing, and the exact prompt and cost before anything is spent.
  Sound effects too.
- **Art direction**: edit each game's art profile (with a live prompt preview and YAML import/export) and browse
  the presets.
- **Usage & cost** and **Jobs**.

```bash
uv run assetforge app install            # builds ~/Applications/AssetForge.app (needs Xcode command line tools)
```

Then open AssetForge from Spotlight, Launchpad or the Dock. **File ▸ Open Library…** switches libraries (the
default is `~/.assetforge`). The app runs this checkout's Python, so code changes apply on the next launch;
re-run `app install` if you move the repo. API keys come from the same places as the CLI (environment,
`.env` in the repo, `.env` in the library).

No Mac app? Run the same UI in any browser:

```bash
uv run assetforge ui --browser
```

The UI server listens on 127.0.0.1 only, on a random port, and every request needs a per-session token.

## For agents: one command

```bash
assetforge request --file ghoul.json --dry-run --json   # would it reuse (which asset, adapted how) or generate (cost)?
assetforge request --file ghoul.json --json             # do it
```

```json
{
  "type": "enemy",
  "description": "hunched graveyard ghoul with lantern eyes",
  "project": "crypt-run",
  "key": "enemy/ghoul",
  "purpose": "common melee enemy of level 1",
  "style": "dark-fantasy",
  "art": {"mood": "cold dread", "lighting": "moonlight from the left", "avoid": ["cute faces"]},
  "size": "256x256",
  "format": "webp",
  "max_cost": 0.2
}
```

`request` tries, in order: the project's existing asset under `key`; an identical earlier or in-flight request
(joined, not repeated); a matching approved asset found by search (category, genre, theme and the game's art
style break ties). A reused image is **adapted** to the requested `size`/`format`/`process` as a new variant; the
original never changes. Only then does it generate: procedural audio for sounds (recipe and preset inferred from
the description), AI images for visual types (with the game's art profile applied), or a file you made yourself
(`"method": "svg"`, `"file": "icon.svg"`). The reply has a `state` (`ready`, `needs_review`, `queued`, `dry_run`),
`reused`/`reuse_reason`, `adapted`, `alternatives` (near misses), `near_duplicates`, the `asset_id` and paths.

## Install

```bash
uv sync
uv run assetforge --help
```

The library lives in `~/.assetforge` by default. Override it with `--home DIR` or `ASSETFORGE_HOME`.

```
~/.assetforge/
├── catalog.sqlite3          # metadata, tags, FTS index, history (SQLite WAL)
├── objects/sha256/ab/…      # files, stored once per unique content, read-only
├── previews/ jobs/ styles/ collections/ exports/ tmp/
```

## Quick start

```bash
assetforge import ~/packs/kenney-ui --collection kenney --license CC0-1.0 --commercial-use allowed
assetforge search "blue round button" --type ui --transparent
assetforge get ast_00042 --path          # just the file path
assetforge use ast_00042 --project panda-bamboo --as ui/button
assetforge review approve ast_00042 --note "matches style"
```

## Commands

| Command | Purpose |
|---|---|
| `init` / `info` | Create the library / show location and stats |
| `import PATH…` | Import files or folders. Type and tags are inferred from paths; duplicates are detected by SHA-256 |
| `search [QUERY]` | Ranked search. Plain words match names, tags, descriptions, type, style, category, theme, genre, colour names and technical words (`transparent`, `landscape`, `dark`, `svg`). Filters: `--type --category --subcategory --theme --genre --color --orientation --tone --aspect --format --origin --tag --style --project --scope --status --collection --transparent/--opaque --min/max-width/height --min/max-duration-ms --similar REF --all-terms --min-coverage --limit` |
| `similar REF` / `duplicates` | Assets that look like REF (perceptual hash + colour mix) / groups of visually identical assets |
| `reindex` | Compute colour/shape features for older files and rebuild the search index (safe any time) |
| `guide` | One JSON document for agents: workflow, commands, presets, methods, request and profile formats |
| `methods` | Which generation method suits which asset, and each method's limits |
| `art presets` / `art preset NAME` | Art-style presets (built-in + your own YAML in `$ASSETFORGE_HOME/presets/`) |
| `art profile set\|show\|list\|delete` | A game's visual identity (style, mood, palette, lighting, perspective, avoid…), YAML in/out |
| `art preview` | The merged art direction and exact prompt for a request, without calling an API |
| `add FILE --method svg\|procedural\|file` | Catalog a file you made (SVG master + PNG render, script kept as provenance) |
| `sprite slice REF --grid 4x2` / `sprite assemble REF…` | Sheet → aligned frame assets with animation metadata / frames → normalized animation |
| `regenerate REF -m FEEDBACK` | Another version of an AI image with review feedback (capped per asset) |
| `get REF` | Full metadata, variants, file paths, lineage, usage and history. REF is `ast_…`, `var_…`, a key, or `project:key` |
| `update REF` | Edit name, key, type, description, scope, project, style, category, subcategory, theme, genre, license, tags or `--meta k=v` |
| `review approve\|reject\|archive\|candidate REF…` | Set review status. Approval runs QC first; rejected and archived assets are hidden from default search |
| `review pick REF` / `review queue` | Approve one candidate and reject the rest of its batch; list candidates awaiting review |
| `image process` / `audio process` / `process` | Create game-ready derivatives (resize, trim, recolor, convert; trim silence, LUFS normalize, fade, loop…) |
| `preview` | Contact sheet PNG (plus `--html` gallery with audio players) for review by people or vision models |
| `qc` | Quality checks: blank/empty, transparency, padding, clipping, silence, loudness, loop seams |
| `lineage REF` / `link REF --to OTHER` | Where an asset came from, what was made from it, its variant tree |
| `export` | Write named game files (+ atlas, PixiJS `manifest.json`, `sounds.json`, TS constants, credits) into a game repo |
| `use REF --project P` / `usage --project P` | Track which games use which variants |
| `collection create\|list\|show\|add\|remove\|delete` | Named asset sets |
| `verify [--rehash]` | Check that every stored file exists and is unmodified |
| `audio recipes [NAME]` | List SFX recipes, presets and effects, or one recipe's parameters |
| `audio generate` | Procedurally generate a sound with seeded variants (no API calls) |
| `audio vary REF -n N` | Add more variations to a generated sound |
| `audio reproduce REF` | Re-render from the stored recipe and confirm it is bit-identical |
| `audio analyze FILE\|REF` | Peak/RMS level, clipping, DC offset, silence |
| `image generate` | Generate images with OpenAI or Gemini (characters, sprites, backgrounds, icons, tiles, UI, effects) |
| `image edit REF` | Revise an image (plus references/mask); the original is kept and linked |
| `image providers` | Which providers are configured, default models, capabilities |
| `style create\|list\|show\|update\|delete` | Art-direction profiles: description, palette, instructions, reference assets |
| `request` | Get an asset: reuse by key → identical request → search, else generate (sync, or `--async` for workers) |
| `worker` | Run queued requests with bounded concurrency (`--concurrency`, `--drain`); run as many as you like |
| `jobs list\|show\|wait\|cancel\|retry\|recover\|resume` | Inspect, wait on, cancel and retry jobs; recover crashed ones; finish interrupted image jobs |
| `config list\|get\|set\|unset` | Library-wide settings shared by all agents (budgets, provider limits, queue) |
| `budget` | Provider spend vs daily/monthly limits (`--daily 5 --monthly 50 --per-request 0.5` to set) |
| `errors` | The error-code catalog: exit code, meaning, what to do |
| `ui` / `app install` | Desktop UI server (`--browser` to open it) / build the native AssetForge.app |
| `report` | Token usage & cost overview for OpenAI/Gemini over a preset or custom date range (+ `--html`, `--csv`) |
| `pricing show\|update\|set\|unset` | Model prices used for costing: fetched from the internet, overridable |

## Procedural sound effects

21 recipes: `ui_click ui_hover ui_confirm ui_cancel ui_error jump land bounce whoosh cartoon_hit punch crunch
explosion hurt laser coin pickup power_up power_down victory failure`. Presets: `soft punchy retro cartoon heavy
bright dark tiny spacious lofi`. Effects (Pedalboard): `reverb delay distortion bitcrush resample lowpass highpass
chorus phaser compressor pitch_shift gain`.

```bash
assetforge audio generate --recipe cartoon_hit --preset soft --duration 0.35 --variants 5 --seed 42 --format ogg --json
assetforge audio generate -r explosion -p size=0.9 -e reverb:room_size=0.6,wet_level=0.3 --key fx/boom
assetforge audio generate --from ast_00012 -p crunch=0.8          # tweak an earlier sound, linked as derived_from
assetforge audio generate --file beetle-hit.json --json           # JSON request; flags override its fields
assetforge audio vary ast_00012 -n 3                              # more variants on the same asset
assetforge audio reproduce ast_00012
```

- Each request becomes **one asset** with variants `v1…vN`. Every variant has a 16-bit WAV master that stores its
  recipe, version, parameters, effects and noise seed, plus a `vN_ogg`/`vN_mp3` copy when `--format` asks for it.
  That copy becomes the primary file.
- `--preset` sets the sound's character. `--style` is the art direction (e.g. `dark-fantasy`), as everywhere else.
- **Reuse instead of regenerate.** Repeating a seeded request returns the earlier asset (`"reused": true,
  "reuse_reason": "fingerprint"`). If a request renders exactly the same sound as an existing asset, that asset is
  returned (`"identical_content"`). Pass `--new-variant` for a deliberately fresh take.
- Generated assets start as `candidate`, are licensed as original procedural work (`commercial_use: allowed`),
  and pass QC (no silence, no clipping, peak normalized to -1 dBFS) before they are stored.

JSON request schema (all fields optional except `recipe`): `recipe preset params duration duration_ms effects
variants variation seed format sample_rate peak_db new_variant name key description tags scope project style
status collection`.

### Search semantics

- Every query word (plus synonyms, e.g. `sound`→`sfx`, `bg`→`background`, `monster`→`enemy`, `woods`→`forest`)
  is prefix-matched against the name, key, tags, description, type, style, category, theme, genre, the image's
  dominant colour names, and technical words (`transparent`/`opaque`, `landscape`/`portrait`/`square`,
  `dark`/`light`, the file format), with stemming. So `search "transparent dark enemy sprites"` works as typed.
- Every image gets local features when cataloged: up to 5 dominant colours (hex, name, share), aspect ratio,
  transparent share, mean brightness and a 64-bit perceptual hash. `--similar REF` and `similar REF` rank by look;
  `duplicates` finds the same picture saved twice at different sizes. Run `reindex` once on older libraries.
- Each hit reports `coverage` (the share of query words it matched), `matched_terms` and `score`. Agents can use
  `--min-coverage 0.8` to accept only close matches before deciding to generate a new asset.
- By default, results exclude rejected/archived assets and assets marked `commercial_use=restricted`
  (include them with `--include-restricted`).
- `--project P` applies reuse scopes: global assets, assets owned by P, and style-scoped assets whose style
  matches `--style`. `--style` alone boosts matching assets; `--strict-style` filters to them.

## AI images

Set `OPENAI_API_KEY` and/or `GEMINI_API_KEY` in the environment, in a `.env` file in the directory you run from,
or in `$ASSETFORGE_HOME/.env`. Override models with `ASSETFORGE_OPENAI_IMAGE_MODEL`, `ASSETFORGE_OPENAI_EDIT_MODEL`,
`ASSETFORGE_GEMINI_IMAGE_MODEL` or `--model`, and pick the default provider with `ASSETFORGE_IMAGE_PROVIDER`.

```bash
assetforge image generate --type enemy -p "Hunched graveyard ghoul with lantern eyes" --style dark-fantasy \
  --mood "cold dread" --lighting "moonlight from the left" --provider gemini --json
assetforge image generate --type background -p "Flooded crypt corridor" --project crypt-run -n 3
assetforge image generate --type icon -p "Brass key" --art-file brass.yaml --format webp --dry-run   # prompt, no call
assetforge image edit ast_00031 -p "Give the ghoul a tattered red hood"          # new asset, edited_from link
assetforge review approve ast_00031
```

- **Candidates:** `-n 3` makes three assets (status `candidate`); approve one and reject the rest with `review`.
- **Transparency** is on by default for characters, sprites, icons, UI and effects. OpenAI produces it natively.
  For Gemini, AssetForge asks for a flat key-colour background and removes it locally. The provider's original
  image is always kept as the `original` variant, and the cut-out becomes the primary `transparent` variant.
- **Provenance:** every asset stores the provider, model, your prompt, the final prompt sent, the provider request,
  references, job ID, estimated cost and QC results.
- **Reliability:** rate limits and outages are retried with backoff, then the next provider is tried (`--no-fallback`
  to disable). Safety refusals are never retried. Raw outputs are saved to `jobs/<job_id>/` before cataloging, so
  `assetforge jobs resume <job_id>` finishes an interrupted job without paying again.
- **Reuse:** repeating an identical request returns the earlier result without calling the API. Use `--new-variant`
  for a fresh take.
- Cost figures are estimates from token/per-image price tables in the adapters. Check the providers' pricing pages.

## Art direction: presets, game profiles, your own style

Images are only as varied as the direction they get. AssetForge's templates say *what* an asset is (a single
enemy, a tileable texture, a 4×2 sheet); the **look** comes from art direction, in layers (later wins per field):

1. the preset named by the game's profile, 2. the game's **art profile**, 3. a preset named in the request
(`--style horror`), 4. the request's own `art` fields (`--mood`, `--lighting`, `--palette`, `--avoid`, `--art-file`).

```bash
assetforge art presets                          # 18 looks: realistic-2d, pixel-art, dark-fantasy, horror, sci-fi…
assetforge art preset horror                    # its palette, lighting, shapes, outlines, shading, texture, avoid
assetforge art profile set --file art.yaml      # a game's visual identity
assetforge art profile show space-survivor --yaml
assetforge art preview -p "scavenger drone" --type enemy --project space-survivor   # merged direction + prompt
```

```yaml
# art.yaml
game: space_survivor
style: sci-fi            # a preset, or any words ("ukiyo-e woodblock print")
mood: dark
perspective: side_view
palette: ["#17212D", "#38536B", "#FF7A32"]
lighting: cinematic
detail: high             # high also asks the provider for larger images
avoid: [childish_shapes, toy_like_objects, generic_cubes]
```

- Every `ArtDirection` field is free text: `style description genre theme mood palette lighting perspective shapes
  outline shading texture detail composition notes avoid references`. Nothing limits an agent to the presets.
- Presets are YAML. Add or override one by dropping `NAME.yaml` into `$ASSETFORGE_HOME/presets/`.
- Profile reference assets (`--reference`) are sent as art-style references, and the prompt says which input image
  is a subject reference and which is a style reference.
- Generated assets record the merged direction and its layers (`metadata.generator.art`, `art_layers`), and inherit
  the profile's genre and theme for search. `--no-profile` ignores the profile for one request.
- `style create` profiles from earlier versions still work as a layer.

## Choosing a method

`assetforge methods` lists each method with what it suits and its limits: **AI images** (illustration,
backgrounds, characters; paid, drifts between calls), **edits** (new poses of an existing design), **SVG**
(icons/UI the agent writes itself; crisp, not painterly), **procedural images** (patterns, particles from the
agent's own script), **processing** (resize/recolour/crop), **sprite slicing/assembly**, and **procedural audio**.

```bash
assetforge add heart.svg --method svg --type icon --size 256 -d "red heart health icon" --project crypt-run
assetforge add noise.png --method procedural --script make_noise.py --type texture -d "grey static noise"
```

SVG keeps the vector master (variant `svg`) and gets a resvg PNG render as its primary file, so it is QC'd,
searchable by colour and exported like any image. Procedural assets keep their script in `metadata.generator`.

## Sprites and animation

Separately generated images rarely animate well. Draw all poses in **one** call on a grid, then slice:

```bash
assetforge image generate -p "armoured knight" --type spritesheet --grid 4x2 \
  --pose "walk contact" --pose "walk down" --pose "walk pass" --pose "walk up" \
  --pose "idle 1" --pose "idle 2" --pose "attack windup" --pose "attack strike" --project castle --json
assetforge sprite slice ast_00051 --grid 4x2 -a walk=0-3@8 -a idle=4-5@3 -a attack=6-7@12:once --cell 128
assetforge sprite assemble ast_00060 ast_00061 ast_00062 --name torch_flicker --fps 12   # normalize loose frames
```

Leave `--grid` out and the grid is read from how the sheet was generated, its prompt ("6 columns and 3 rows"), or
the image itself; frame names come from the prompt's numbered cells. A grid that would put two separate subjects
in one cell is refused with the grid the sheet really has (`--dry-run` shows the plan first). Slicing finds each
subject by alpha (key-colour spill and enclosed key-colour holes removed), then `--fit shared` scales **all frames
by one factor** so a character never changes size (animations), while `--fit each` fills each cell (icon/item
sets; `auto` picks), and pads them into equal cells on a common baseline (`--anchor bottom`) or centre.
Re-slicing archives the frames of the earlier slice (`--keep-previous` to keep them).
Frames become assets named `knight_walk_01…` in a `knight-frames` collection (linked `derived_from` the sheet),
the sheet gets an aligned variant, and `metadata.animation` stores the grid, cell size, anchor, fps and loop.
A report flags missing or extra frames, captions the model added, and frames whose heights differ by more than 25%.
`--slice` on `image generate` (or `"slice": true` with a `sheet` in `request`) does this right after generating.

On `export --atlas`, frames are packed with their pivot as the Pixi frame `anchor`, the atlas lists the
animations, and `animations.json` gives each animation's frames, fps, loop and anchor.

## Processing, QC and review

```bash
assetforge preview --job job_00012 --html                     # contact sheet of a generation batch
assetforge review pick ast_00031 --note "best silhouette"     # approve it, reject its siblings
assetforge image process ast_00031 --preset web-sprite --primary
assetforge image process ast_00040 --resize 1280x720 --mode cover --format webp --quality 85
assetforge image process ast_00031 --hue 120 --new-asset --name beetle_blue
assetforge audio process ast_00007 --preset web-sfx --primary   # trim, mono, -16 LUFS, fade, ogg
assetforge qc --status candidate
assetforge lineage ast_00031
```

- **Derivatives never replace originals.** Each one is a new variant whose parent is the source variant and
  whose `ops` are recorded, or a new asset linked `derived_from` with `--new-asset`. Running the same processing
  again returns the existing result. `--primary` makes the derivative the file `get --path` returns.
- Image presets: `game-sprite`, `web-sprite`, `web-background`, `pixel-art-2x`, `icon-128`. Audio presets:
  `web-sfx`, `web-music`, `game-wav`, `loop`. Any op sequence can also come from `--ops-file ops.json`.
- Resizing never stretches: `contain` fits, `cover` fills and crops, `pad` fits then pads to the exact size.
- Loudness normalization uses ITU-R BS.1770 LUFS, and the peak ceiling is respected by lowering gain, never by clipping.
- **Archive** hides assets from every view and search without deleting anything: `review archive REF…`,
  `review restore REF…` (back to its previous status). In the app: the Archive tab, `E`, or ⌘/⇧-click to select
  several and use the bulk bar.
- `review approve` runs QC first. Blank/empty images, silent/clipped audio and missing files block approval
  (exit 7, `POLICY_VIOLATION`) unless `--force`.
- QC also checks what was asked for: `size_mismatch`/`aspect_mismatch`/`format_mismatch` against the request,
  `near_duplicate` against the library, and `sheet_frames` for sheets whose slice found missing or merged frames.
  Generated and added assets are checked automatically; the result is in `metadata.qc` and the command's JSON.

### The review loop

The agent judges fit itself: `get REF --json` and `preview` give it the image paths to look at. If an image misses
the brief, ask for another version with concrete feedback:

```bash
assetforge regenerate ast_00042 -m "too cartoonish: realistic proportions, grittier texture, no outline" \
  --style dark-fantasy --avoid "cute faces" --reject-previous --json
```

The original request is re-run with the feedback and overrides, new candidates are linked `variant_of` the
original, and attempts per original are capped by `generation.max_regenerations` (default 3; exit 7,
`RETRY_LIMIT`), on top of the budget and `--max-cost`. `--dry-run` shows the revised prompt and cost.

## Export to a game (PixiJS)

```bash
assetforge export --collection panda-bamboo --project panda-bamboo \
  --out ../panda-bamboo/public/assets --atlas sprites --typescript ../panda-bamboo/src/assets.ts
```

```
public/assets/
├── manifest.json               # PixiJS v8 manifest: one bundle named after the collection/project
├── sprites.{png,webp}          # atlas of characters, sprites, icons, UI and effects (trimmed, packed)
├── sprites.{png,webp}.json     # spritesheet data; frames named by alias; `walk_01…` → animations.walk
├── backgrounds/jungle.{webp,png}
├── sfx/coin.{ogg,mp3}
├── sounds.json                 # alias → [ogg, mp3] for @pixi/sound or any audio library
├── ASSET_CREDITS.md            # licenses/attributions of imported assets, when any
└── assetforge-export.json      # what AssetForge owns here (enables incremental re-export and --clean)
```

```ts
import { Assets, Sprite, AnimatedSprite, Texture } from "pixi.js";
const manifest = await (await fetch("assets/manifest.json")).json();
await Assets.init({ basePath: "assets/", manifest, texturePreference: { format: ["webp", "png"] } });
await Assets.loadBundle("panda-bamboo");
const bg = new Sprite(Assets.get("backgrounds/jungle"));
const hero = new Sprite(Texture.from("characters/beetle"));          // atlas frame by alias
const walk = new AnimatedSprite(Assets.get("sprites").animations["characters/beetle_walk"]);
```

- Files are named by asset key (`enemy/beetle/hit.ogg`) or `<type>/<name>`. Use `--layout flat` for bare names.
- Only **approved** assets are exported by default (`--include-candidates` to override), and assets marked
  `commercial_use: restricted` are skipped unless `--allow-restricted`. Skipped assets are listed with reasons.
- WebP/PNG and OGG/MP3 copies are cataloged derivatives, so the library knows exactly which file each game ships.
  `--max-image-size 1024` also downscales large images.
- Re-running an export only rewrites changed files. `--clean` removes files AssetForge exported earlier that are no
  longer selected. Files AssetForge didn't create are never overwritten (`--overwrite` to force).
- With `--project`, each exported asset is recorded in that project's usage under its alias.

## Many agents, one library

- **Reuse first.** Generated assets start as `candidate`. Approve them (`review`) and other games' `request`
  calls will find and reuse them. Style-scoped assets are only offered to requests of the same style.
- **No duplicate work.** Identical requests from different agents share one job: late arrivals wait for it and
  get the same asset. Identical image requests reuse the finished job instead of paying again.
- **Queue.** `request --async` returns a `job_id` immediately; `assetforge worker` processes the queue (several
  workers or machines can share one library); `jobs wait <id>` blocks until the result is ready.
- **Crash recovery.** Running jobs hold a lease renewed by a heartbeat. If a process dies, `jobs recover` (also
  run by every worker and waiter) requeues the job once the lease expires. A retried request resumes its saved
  (already paid for) image outputs, and audio re-renders deterministically, so nothing is duplicated.
- **Rate and spend limits** apply across all processes: `limits.<provider>.max_concurrent`,
  `limits.<provider>.rpm`, `budget.daily_usd`, `budget.monthly_usd`, `budget.per_request_usd` (or `--max-cost`).
  Each paid call reserves its estimated cost atomically before it runs, so concurrent agents can't overshoot.

```bash
assetforge config set limits.openai.max_concurrent 2
assetforge budget --daily 5 --monthly 50
assetforge request --file spec.json --async --json      # → {"state": "queued", "job_id": "job_00042"}
assetforge worker --concurrency 4                         # in another terminal / machine
assetforge jobs wait job_00042 --json
```

## Token usage, prices and cost reports

Every OpenAI and Gemini call records its token usage: input text and image tokens, output image and text tokens,
image count, the job, the project, the pre-call estimate, and the **actual cost**, computed from current prices.

- **Prices come from the internet.** `assetforge pricing update` fetches the
  [LiteLLM model price catalog](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json),
  which tracks the providers' official pricing. Prices refresh automatically when older than 7 days
  (`pricing.max_age_days`, `pricing.auto_update`), fall back to built-in defaults offline, and can be pinned per
  model (`assetforge pricing set gpt-image-2.5-flare output_image 28`). Units: USD per 1M tokens; `per_image` is
  USD per image.
- **Estimates learn from your history.** Before a paid call, AssetForge estimates its cost from the median actual
  cost of your previous calls with the same model, quality and size, or from expected tokens × current prices.
  The estimate is reserved against budgets; the report shows how accurate estimates were.

```bash
assetforge report                                   # last 30 days (UTC)
assetforge report --range this-month --tz Asia/Kolkata
assetforge report --from 2026-10-01 --to 2026-10-19 --interval week --provider gemini
assetforge report --range last-month --project panda-bamboo --html cost.html --csv usage.csv --json
```

Presets: `today`, `yesterday`, `this-week`, `last-7-days`, `this-month`, `last-month`, `last-30-days`,
`this-year`, `all`. Custom `--from`/`--to` take dates (inclusive) or ISO datetimes; `--tz` takes `UTC`, `local` or
any IANA zone. The report covers totals, cost per day/week/month, breakdowns by provider, model, project and
operation, the most expensive calls, the prices used and their source, and current budget status.
`--html` writes a standalone page with a chart; `--csv` writes the raw usage records.

## JSON contract for agents

- Add `--json` (or set `ASSETFORGE_JSON=1`). stdout then carries exactly one JSON document, and diagnostics
  go to stderr. No command ever prompts.
- Every document has `"status"`: `"ok"`, `"partial"` (some items of a batch failed) or `"error"`.
- Errors look like `{"status": "error", "error": {"code": "NOT_FOUND", "message": "…", "retryable": false,
  "hint": "…", "details": {…}}}`. `retryable` says whether trying the same call later may work. Even unexpected
  crashes answer this way (`INTERNAL_ERROR`, with the traceback in `<library>/logs/errors.log`).
  `assetforge errors` lists every code.

| Exit | Code | Meaning |
|---|---|---|
| 0 | | success |
| 1 | `ERROR` | unexpected failure / every item of a batch failed |
| 2 | `USAGE` | bad command-line usage |
| 3 | `NOT_FOUND` | asset, collection or path not found |
| 4 | `INVALID_INPUT` | invalid value or undecodable file |
| 5 | `CONFLICT` | duplicate key/name, ambiguous reference |
| 6 | `STORAGE_ERROR` | filesystem/database problem, failed `verify` |
| 7 | `POLICY_VIOLATION` / `BUDGET_EXCEEDED` | blocked by policy: QC errors on approve, or a spending limit |
| 8 | (status `partial`) | batch partly succeeded |
| 9 | `PROVIDER_*` / `CONTENT_REJECTED` | image provider failed: `PROVIDER_NOT_CONFIGURED`, `PROVIDER_AUTH`, `PROVIDER_QUOTA`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `PROVIDER_REJECTED`, `PROVIDER_UNSUPPORTED`, `PROVIDER_BAD_RESPONSE` |

## Python API

```python
from assetforge.core.catalog import Library
from assetforge.core.search import SearchQuery, search

with Library() as lib:
    hits = search(lib, SearchQuery(text="coin pickup", types=["sfx"], min_coverage=0.5)).results
```

Generators register their output with `Library.register_file(path, NewAsset(...), params=...)`. It dedupes by
content and records provenance.

## Development

```bash
uv run pytest
export ASSETFORGE_HOME=$PWD/.dev/library   # manual testing without touching ~/.assetforge
```

See `CLAUDE.md` for architecture notes and the roadmap.
