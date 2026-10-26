# CLAUDE.md — AssetForge

AssetForge is a standalone, Python, CLI-first game asset warehouse. AI agents working in any game repo call
it to **search for an existing asset before generating a new one**, then receive a validated file path plus metadata.

## How to work in this repo (user instructions)

- Do **all implementation and testing directly in this directory** (`/Users/sumansaurabh/IdeaClan/Games/AssetForge`).
  Do not use git worktrees, scratch copies or a separate sandbox for this project's work.
- For manual CLI testing, point the library inside the repo, never at `~/.assetforge`:
  `export ASSETFORGE_HOME=$PWD/.dev/library` (`.dev/` is gitignored and safe to delete).
- Every phase must end with a working, tested CLI, not just modules.

## Commands

```bash
uv sync                       # install deps (Python 3.12+, uses .venv)
uv run pytest                 # full suite (~30s, 492 tests, multi-process concurrency tests, no network)
uv run assetforge --help      # CLI entry point (also: uv run python -m assetforge)
```

## Design principles (from the spec; keep them)

1. CLI-first, Python-first, local-first. Image generation is the only thing that calls external APIs (OpenAI/Gemini).
2. Search before generation. Don't pay for an asset that already exists.
3. Non-destructive. Stored objects are read-only; edits/processing create new variants or derived assets with lineage.
4. Game-independent. Manages assets, never gameplay code.
5. Agent-friendly: `--json` (or `ASSETFORGE_JSON=1`) prints exactly one JSON document on stdout with
   `"status": "ok" | "partial" | "error"`. Diagnostics go to stderr. Commands never prompt. IDs are stable.

## Architecture

- `src/assetforge/cli.py`: Typer app, the public contract. Each command uses `with session(ctx, json_) as s:`,
  which opens the `Library`, maps `AssetForgeError` → JSON error + exit code, and closes the DB.
- `src/assetforge/output.py`: JSON vs Rich rendering. Only JSON goes to stdout in `--json` mode.
- `src/assetforge/errors.py`: error classes with stable `code` strings, and `ExitCode`
  (0 ok, 1 error, 2 usage, 3 not found, 4 invalid input, 5 conflict, 6 storage, 7 policy, 8 partial).
- `src/assetforge/config.py`: `ASSETFORGE_HOME` (default `~/.assetforge`) and the on-disk layout.
- `src/assetforge/core/db.py`: SQLite (WAL, FTS5), explicit migrations list (`MIGRATIONS`; append, never edit
  shipped ones), `transaction()` = short `BEGIN IMMEDIATE`, `next_id()` → `ast_00001`, `var_00001`, `col_00001`.
- `core/storage.py`: content-addressed store `objects/sha256/<ab>/<sha256><ext>`, atomic temp-file + `os.replace`.
- `core/metadata.py`: file kind, MIME, image dims/alpha (Pillow), audio duration/rate (soundfile, ffprobe fallback).
- `core/classify.py`: infers asset type and tags from path/filename during import.
- `core/catalog.py`: `Library`, the Python API: `import_paths`, `register_file` (entry point for generators,
  dedupes by hash), `add_variant`, `get_asset`, `resolve_asset_id` (ast_/var_/key/project:key), `update_asset`,
  `set_status`, `record_usage`, `add_lineage`, `verify`.
- `core/search.py`: FTS5 prefix match + synonyms, ranked by coverage (share of query terms matched), then BM25,
  then style/project affinity. Defaults hide rejected/archived and `commercial_use=restricted` assets.
  `--project` applies reuse scopes: global + that project's assets + style-scoped assets matching `--style`.
- `core/collections.py`: named sets of assets.
- `core/models.py`: enums (AssetType, Scope, Status, Origin, CommercialUse, FileKind) and Pydantic output models.
- `core/jobs.py`: `generation_jobs` records (request, fingerprint, engine/provider, result/error, cost) and
  `find_completed(kind, fingerprint)` for request-level reuse.
- `cli_common.py` holds `session()`, `JsonOpt`, `dump`, `parse_pairs`. `cli_audio.py` holds the `audio` and `jobs` sub-apps.

### Procedural audio (Phase 02)

- `audio/synth.py`: oscillators (polyBLEP square/saw), noise colours, sweeps, envelopes, static and time-varying
  filters, `soft_clip`. Pure NumPy/SciPy. Randomness comes only from the passed `Generator`.
- `audio/recipes/`: `base.py` (Param/Recipe/`@recipe` registry, global PRESETS, `resolve_params`, `vary_params`)
  plus `ui.py`, `motion.py`, `impact.py`, `reward.py` (21 recipes). Every recipe gets the common params `duration`,
  `pitch` and `brightness`. Presets only apply the params a recipe has. **Changing a recipe's sound means bumping its
  `version`.** Otherwise `audio reproduce` reports drift for older assets.
- `audio/effects.py`: Pedalboard effect chain stored as data (`{"name", "params"}`) with all defaults resolved.
- `audio/export.py`: `finish()` (DC removal, tail trim, fades, peak normalize), PCM16, `pcm_sha256`, WAV/OGG/MP3 writing.
- `audio/variations.py`: `(seed, index)` → per-variant params and noise seed. v1 is canonical unless `new_variant`.
- `audio/generate.py`: `generate_audio`, `vary_audio`, `reproduce_audio`. One request = one asset with variants
  `v1..vN` (16-bit WAV masters, each storing everything needed to re-render bit-for-bit) plus `vN_ogg`/`vN_mp3`
  delivery copies (the first is primary). Reuse happens at two levels: same seeded request → fingerprint reuse;
  identical rendered v1 → content reuse, topping up missing variants. Adding variants is idempotent per label,
  which makes concurrent identical requests safe.
- `validation/audio.py`: `analyze()` → peak/RMS/crest/clipping/DC/silence + `issues`. `silent`/`clipping` fail QC.
- CLI naming: the spec's `--style soft` is **`--preset soft`**. `--style` always means art direction (cute-jungle).

### AI images (Phase 03)

- `config.py` `load_settings()`: API keys and model overrides, read in this order: process env, then `./.env` (the repo
  the agent runs in), then `$ASSETFORGE_HOME/.env`. Only `SETTING_KEYS` are read; nothing is exported.
- `image/providers/`: `base.py` (ProviderCall/Response, Capabilities, ASPECTS), `openai.py` (images.generate,
  plus images.edit whenever input images are given; defaults `gpt-image-2.5-flare` / `gpt-image-2.5-sunburst`; native
  transparency; flexible WxH on gpt-image-2+), `gemini.py` (generate_content with IMAGE modality; default
  `gemini-3.1-flash-image`; no transparency, no masks, one image per call), `fake.py` (offline; enabled by
  `ASSETFORGE_ENABLE_FAKE_PROVIDER=1`; **never falls back to paid providers**). `__init__.py` holds the registry,
  `provider_chain()` and `OVERRIDES` (tests pin fakes there). Adapters map SDK errors to
  `ProviderError(kind=…)`: rate_limited/unavailable/bad_response retry; everything except `content` falls back.
  Out-of-credits 429s are `quota` (no retry).
- `image/prompts.py`: per-type framing, style-profile block, and the transparency phrase. For providers without
  native alpha it asks for a solid key colour (magenta, or green when the subject is pink/purple).
- `image/transparency.py`: removes only background pixels connected to the border, with a soft edge and un-mixing
  of the background colour.
- `image/engine.py`: provider chain with retries/backoff (`engine.sleep`), then `persist_outputs` writes raw bytes +
  `response.json` to `jobs/<job_id>/` **before** cataloging, then `finalize` (key out, QC, webp, register, lineage).
  `generate.py`/`edit.py` hold the request models and fingerprints, and `resume.py` handles `jobs resume`.
  Each candidate (n) becomes its own asset: variants `original` (provider bytes) → `transparent` (keyed) → `webp`.
  Edits create a new asset linked `edited_from` (or `--as-variant` → `edit_N` on the source).
- `core/styles.py`: style profiles (`style create/update…`). `--style X` sets the asset's style, and if a profile X
  exists, its description, instructions and palette go into the prompt and its reference assets are sent as images.
- `validation/image.py`: blank/empty (errors), no_transparency, touches_edge, background_not_removed (warnings).
### Processing, QC, review, lineage (Phase 04)

- `image/process.py` and `audio/process.py`: deterministic op pipelines (`[{"op": "trim"}, …, {"op": "format", …}]`,
  with `format` always last) plus named `PRESETS`. Images: trim/crop/resize (contain|cover|pad, never stretch;
  premultiplied-alpha resampling)/scale/pad/recolor/flip/rotate/remove_background. Audio: trim_silence/cut/gain/
  normalize (LUFS or peak, with a peak ceiling)/fade/channels/resample/loop.
- `core/derive.py`: a derivative is a new variant (parent = source variant, `params.ops` recorded) or, with
  `new_asset`, a new asset linked `derived_from` that keeps the source's license and commercial_use. The same
  (source, ops) returns the existing variant. The default source skips processor-made primaries (they are leaves),
  so re-running a command is a no-op; chain explicitly with `--variant`.
- `audio/loudness.py`: BS.1770-4 integrated LUFS, verified against the spec's coefficients and reference tones.
- `core/qc.py`: `check_asset` stores `metadata.qc` (errors vs warnings) and logs a `qc` event. Errors:
  blank/empty/silent/clipping/missing_file/undecodable.
- `core/review.py`: `approve` runs QC first and raises `PolicyError` (exit 7) on errors unless `force`. `pick`
  approves one candidate and rejects its siblings from the same job. `queue` lists candidates oldest first.
- `core/previews.py`: cached thumbnails (`previews/thumbs/`, audio becomes a waveform), PNG contact sheets for
  people or vision models, and an HTML gallery with audio players. `core/lineage.py` builds ancestor/descendant
  graphs and the variant tree.
- `review` is a Typer sub-app (`cli_process.py`): approve/reject/archive/candidate/pick/queue.
### Export (Phase 05)

- `exporters/generic.py` `export(lib, ExportRequest)`: select (refs | collection | query/types | project usage),
  filter (approved only unless `include_candidates`, and restricted only with `allow_restricted`), then name files
  `key` or `<type-dir>/<name>` (`_2` suffix on collisions). Web formats are cataloged derivatives labelled
  `<ops>_of_<source label>` (e.g. `webp_of_original`), so re-exports reuse them. Writes are atomic and skip
  unchanged files. The export dir is owned through `assetforge-export.json`: `clean` only deletes files listed
  there, and foreign files are never overwritten without `overwrite`. Usage is recorded with the alias as usage_key.
- `exporters/atlas.py`: trim → shelf pack (all widths tried, smallest area wins; power-of-two optional; multi-pack
  with `related_multi_packs`) → TexturePacker-hash JSON per format (`<name>.png.json`, `<name>.webp.json`).
  `name_01, name_02…` frames become `animations.name`. Atlas types: character/sprite/icon/ui/effect.
- `exporters/pixijs.py`: `manifest.json` (one bundle; textures `alias.{webp,png}`, atlases `name.{webp,png}.json`),
  `sounds.json` (audio is kept out of the Pixi manifest: core Pixi would route .ogg to its video loader), and an
  optional TS constants file. `ASSET_CREDITS.md` lists imported assets with licenses or attributions.
- Verified in a real browser with pixi.js 8.22.0 (`.dev/pixi-game/index.html`, served by the `pixi-load-test`
  config in `.claude/launch.json`): bundle load, WebP→PNG fallback, atlas frames/animations, trim offsets.
### Multi-agent reliability (Phase 06)

- `request.py` `handle()`: the agent entry point. Reuse ladder: key (asset key or project usage_key) → identical
  request (finished, or in flight: join it) → search (coverage ≥ `request.reuse_threshold`, approved only unless
  `accept_candidates`, not restricted) → generate. SFX: recipe and preset are inferred from the description, and
  the seed is derived from the request fingerprint so retries reproduce the same sound. Images go to
  `generate_image` with `on_job` recording `inner_job_id`, so a retried request resumes the paid image job.
  `process`, `key` and project usage are applied after generation.
- `core/queue.py`: request jobs have leases (`lease_owner`, `lease_expires_at`) renewed by a `keep_alive` thread.
  `claim_next`/`claim` are atomic. `create_request_job` atomically joins an identical queued/running/succeeded
  job. `finish` only succeeds for the current lease owner. `recover` requeues expired leases (or fails them after
  `max_attempts`), marks orphaned unleased generation jobs `ABANDONED`, and sweeps old `tmp/` entries.
- `worker.py`: N threads, each with its own `Library`. Retryable errors are requeued with backoff (`RETRY_DELAYS`).
- `core/limits.py`: per-provider concurrency slots and an RPM token bucket in SQLite (shared by all processes).
  `core/budget.py`: daily/monthly/per-call limits, with the estimate reserved on the job atomically before each
  paid call. `core/settings_store.py`: library-wide `config` keys. Providers implement `estimate_cost(call)`.
- The engine reports the most useful chain failure: retryable first, else the primary provider's error.
- Errors carry `retryable` and optional `hint`. Unexpected exceptions become `INTERNAL_ERROR` JSON, with the
  traceback in `<library>/logs/errors.log`. `errors.CATALOG` backs `assetforge errors`.
- Schema v2 migration adds the queue columns and the `settings`, `provider_slots` and `rate_buckets` tables.
### Usage metering, pricing and reports

- `core/pricing.py`: prices from a LiteLLM-format catalog (`pricing.source_url`, default the LiteLLM GitHub
  JSON), parsed to OpenAI/Gemini image models only and stored in `pricing_snapshots`. Resolution is field by
  field: `price_overrides` > latest snapshot > `BUILTIN`, with dated and `-preview` model names falling back to
  their family. `ensure_fresh()` (called before paid calls) refreshes when older than `pricing.max_age_days`; it
  never raises and is disabled by `ASSETFORGE_PRICING_OFFLINE=1` (tests set this in an autouse fixture).
- `core/metering.py`: `record_call` normalizes OpenAI/Gemini usage into input text/image and output image/text
  tokens, prices them with current rates (per-image fallback), and writes one `usage_records` row per job.
  `estimate_call` = median of past cost per image for the same provider/model/quality/size, else expected tokens
  (`provider.expected_output_tokens`) × current price, else per-image price, else the adapter's figure.
  `backfill` creates records for older jobs from `jobs/<id>/response.json`.
- `core/reporting.py`: preset/custom ranges (custom dates inclusive, any IANA tz), aggregation by
  provider/model/project/operation, a zero-filled day/week/month series, top calls, and estimate accuracy.
  `core/report_html.py` renders a standalone HTML report (stat tiles, column chart with tooltips, tables,
  light/dark), and `to_csv` exports raw records.
- CLI: `report` and `pricing show|update|set|unset` (`cli_report.py`). Schema v3 adds `usage_records`,
  `pricing_snapshots` and `price_overrides`.
### Desktop app

- `app/server.py`: stdlib `ThreadingHTTPServer` on 127.0.0.1 with a random port and session token (query, then
  HttpOnly cookie, or `X-AssetForge-Token`), Host-header check, a pool of `Library(pooled=True)` connections,
  static files with CSP, `FileResponse` with HTTP Range (audio seeking), and a `--parent-pid` watchdog.
- `app/api.py`: `router` endpoints (`/api/overview|assets|assets/{id}|facets|jobs|report|audio/recipes|
  image/options|generate/*|reveal`, `/media/variant/{id}`, `/media/thumb/{id}`) wrapping core modules. Image
  generation runs in a background thread and returns `job_id` via `on_job`; the UI polls `/api/jobs/{id}`.
- `core/browse.py`: exact filters + sort + offset/limit + total (shows all statuses), plus `facets()`.
  Text queries use `search()` ranking.
- `app/static/`: vanilla ES-module SPA (`app.js` hash router, `lib.js` helpers: column chart, chips/palette inputs,
  segmented control, collapsible sections, job polling; `views/*.js`: overview, assets, generate, art, usage, jobs).
  No build step. `tokens.css` is shared with `core/report_html.py`. The server only does GET/POST, so actions are
  `POST /api/assets/{id}/update|review|regenerate|slice`, `POST /api/art/profiles/{project}[/delete]`,
  `POST /api/art/preview`; reads are `GET /api/art`, `/api/assets/{id}/similar`, and `/api/assets` accepts
  `color/orientation/tone/transparent/similar` (handled in `core/browse.py`). Static files are served `no-cache`,
  but Python changes need a server restart (relaunch the app).
- `macos/AssetForgeApp.swift`: Cocoa + WKWebView shell. It runs `python -m assetforge [--home LIB] ui --json
  --port 0 --parent-pid PID`, reads the first stdout line `{"url", "port", "token", "home"}`, and loads the URL.
  The library path is in UserDefaults `com.ideaclan.assetforge LibraryPath`. `app/installer.py` (`assetforge
  app install`) compiles it with swiftc and writes Info.plist, `Resources/launch.json` (venv python + repo),
  the icon (Pillow → iconutil) and an ad-hoc signature.
- Dev loop: `.claude/launch.json` → `assetforge-ui` serves `.dev/live` on :8767 with token `dev`
  (open `http://localhost:8767/?token=dev`).
- **Tests never call real APIs**: the autouse `no_real_providers` fixture in `tests/conftest.py` chdirs away from the
  repo `.env`, clears key variables, and makes `openai.OpenAI`/`genai.Client` raise. Keep it that way.
  Live checks go in `.dev/` with `ASSETFORGE_HOME=$PWD/.dev/live` and cost real money, so keep them minimal.

### Discovery, art direction, reuse, sprites (post-v1)

- Schema v4: `assets.category/subcategory/theme/genre` (kebab slugs), `asset_files.aspect/alpha_fraction/
  mean_luma/colors/dhash`, `art_profiles`, and `assets_fts` with `category theme genre colors tech` columns.
  `Library.__init__` refills FTS whenever its row count differs from `assets` (after the v4 migration).
- `core/visual.py`: features computed at cataloging (`metadata._read_image` → `FileMeta.visual`): top-5 colours
  (named via HSV buckets), alpha share, mean luma, aspect, 64-bit dHash on a gray composite. `similar()`,
  `near_duplicates()` (dHash ≤ 6 **and** colour overlap ≥ 0.6), `duplicate_groups()`, `ensure_features()`.
  `tech_terms`/`color_terms` feed FTS so plain queries match "transparent", "landscape", "dark", "green".
- `art.py`: `ArtDirection` (all-optional free text), YAML presets in `art_presets/` plus `$ASSETFORGE_HOME/presets/`,
  per-project `art_profiles`, `resolve()` merge order preset(profile) < profile < preset(request) < legacy style
  profile < request art, `render()` → labelled prompt lines. `detail: high` → quality high.
- `image/prompts.py`: look-neutral `FRAMING`; `SheetSpec` grid framing; labelled references; keyed-background text
  that keeps lighting; raw prompts still get the key sentence. **Bump `PROMPT_VERSION` when wording changes**
  (it is in the image fingerprint, with the merged direction).
- `request.py`: re-ranks search hits with category/genre/theme bonuses, adapts reused assets (`adapt_ops`: resize
  pad/cover, process preset, format) via `process_*_asset` as variants (or `adapt_as_new_asset`), `plan()` for
  `dry_run`, `method: svg|procedural|file` → `add.add_file`, `sheet` + `slice`.
- `add.py` (agent-made files; SVG master + resvg PNG via `image/svg.py`), `methods.py`, `guide.py`,
  `regenerate.py` (feedback loop, `generation.max_regenerations`, `RetryLimitError`).
- `image/sprites.py`: `slice_image` (components → grid cells → despill → shared scale → anchored cells + report),
  `slice_asset`/`assemble_assets` (frame assets `<base>_<anim>_<nn>`, `metadata.frame.pivot/fps/loop`, collection,
  aligned sheet variant, `metadata.animation`). Export writes atlas frame `anchor` and `animations.json`.
- `core/qc.check_asset` adds expectation warnings from `metadata.expect` (size/aspect/format), near-duplicates and
  slice reports. The engine stores `expect` and runs it for every new candidate. Names come from
  `engine.subject_name` (style/filler words dropped).
- CLI modules: `cli_discover.py` (similar, duplicates, reindex), `cli_art.py`, `cli_sprite.py`, `cli_create.py`
  (add, regenerate, guide, methods).

Data model: **Asset** (logical item) → **Variant** (labelled rendering) → **File** (content by SHA-256).
Identical bytes are stored once and resolve to the asset that first registered them. Generation jobs, lineage,
project usage, style profiles and an asset event history all have tables already (schema v1).

Conventions: keep SQLite transactions short and never hold one across slow work (API calls, synthesis).
Store files first, then register them in one transaction. Names are snake_case, while tags, projects, styles and
collections are kebab-case. Keys look like `enemy/beetle/hit`.

## Roadmap status

| Phase | Scope | Status |
|---|---|---|
| 01 Foundation | schema, storage, hashing, metadata, import, search, collections, review, usage, verify, JSON CLI | **done** |
| 02 Procedural audio | synth, 21 seeded SFX recipes, 10 presets, 12 effects, variations, QC, WAV/OGG/MP3, `audio recipes/generate/vary/reproduce/analyze`, `jobs` | **done** |
| 03 AI images | OpenAI + Gemini adapters, fallback/retries, styles, references, edits, masks, keyed transparency, QC, job persistence + `jobs resume`, `image generate/edit/providers`, `style …` | **done** |
| 04 Processing & QC | image ops + presets, audio finishing with LUFS, `qc`, `preview` sheets/HTML, QC-gated `review` (pick/queue), `lineage`/`link` | **done** |
| 05 Export | `export` (named files, web formats, incremental/clean), atlas packing + animations, PixiJS manifest, sounds.json, TS constants, credits | **done** |
| 06 Reliability | `request` (reuse ladder), durable queue + `worker`, leases/recovery, provider slots + RPM, budgets, structured errors | **done** |

All six phases of the v1 plan are done, plus discovery/art-direction/sprites (above) and the Mac app.
Possible next steps (not started): semantic/vector search, MIDI music, in-app review and export.

Generators should call `Library.register_file(path, NewAsset(origin=..., status="candidate", metadata={recipe/prompt}), params=...)`
and use `add_variant` / `add_lineage` for derivatives, so search, dedupe and provenance work automatically.
Out of scope for v1: game engine, visual editor, cloud backend, vector search, full AI music composer, web UI.
