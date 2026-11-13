# CLAUDE.md — AssetForge

AssetForge is a standalone, Python, CLI-first game asset warehouse. AI agents working in any game repo call
it to **search for an existing asset before generating a new one**, then receive a validated file path plus metadata.

## How to work in this repo (user instructions)

- Do **all implementation and testing directly in this directory** (the AssetForge repo root).
  Do not use git worktrees, scratch copies or a separate sandbox for this project's work.
- For manual CLI testing, point the library inside the repo, never at `~/.assetforge`:
  `export ASSETFORGE_HOME=$PWD/.dev/library` (`.dev/` is gitignored and safe to delete).
- Every phase must end with a working, tested CLI, not just modules.

## Commands

```bash
uv sync                       # install deps (Python 3.12+, uses .venv)
uv run pytest                 # full suite (~4 min, 2694 tests incl. every recipe × preset; multi-process concurrency tests; no network)
uv run assetforge --help      # CLI entry point (also: uv run python -m assetforge)
```

## Design principles (from the spec; keep them)

1. CLI-first, Python-first, local-first. Only image generation calls external APIs (OpenAI/Gemini), plus story
   text, which goes through the user's signed-in `claude`/`codex`/`gemini` CLIs (API keys only as an opt-in).
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
- `audio/export.py`: `finish()` (DC removal, tail trim, fades, peak normalize), PCM16, `pcm_sha256`, WAV/OGG/MP3/M4A writing.
- `audio/variations.py`: `(seed, index)` → per-variant params and noise seed. v1 is canonical unless `new_variant`.
- `audio/generate.py`: `generate_audio`, `vary_audio`, `reproduce_audio`. One request = one asset with variants
  `v1..vN` (16-bit WAV masters, each storing everything needed to re-render bit-for-bit) plus `vN_ogg` (primary) and
  `vN_m4a` delivery copies (AAC fallback for Safari/iOS; the same for every engine, stems and extras). Reuse happens at two levels: same seeded request → fingerprint reuse;
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
  `POST /api/art/preview`, `POST /api/review/bulk`, `POST /api/assets/bulk-update` (fields + tags, all ids
  validated first), `POST /api/collections/add`, `GET /api/asset-ids` (ids matching the browse filters; it can't
  live under `/api/assets/…`, which the detail route matches first); `/api/assets` hides archived unless `status=archived`
  (`review.restore` returns assets to their pre-archive status); reads are `GET /api/art`, `/api/assets/{id}/similar`, and `/api/assets` accepts
  `color/orientation/tone/transparent/similar` (handled in `core/browse.py`). Live data and tasks: see "App: live
  data and tasks" below (`/api/revision` still answers but nothing in the app polls it). Static files are served
  `no-cache`; Python changes need a server restart (relaunch the app), and open pages then offer to reload.
- `macos/AssetForgeApp.swift`: Cocoa + WKWebView shell. It runs `python -m assetforge [--home LIB] ui --json
  --port 0 --parent-pid PID`, reads the first stdout line `{"url", "port", "token", "home"}`, and loads the URL.
  The library path is in UserDefaults `com.ideaclan.assetforge LibraryPath`. `app/installer.py` (`assetforge
  app install`) compiles it with swiftc and writes Info.plist, `Resources/launch.json` (venv python + repo),
  the icon (Pillow → iconutil) and an ad-hoc signature.
- Dev loop: `.claude/launch.json` → `assetforge-ui` serves `.dev/live` on :8767 with token `dev`
  (open `http://localhost:8767/?token=dev`); `app-dev` serves `.dev/app-lib` (fake providers) on :8776.
- **Tests never call real APIs**: the autouse `no_real_providers` fixture in `tests/conftest.py` chdirs away from the
  repo `.env`, clears key variables, and makes `openai.OpenAI`/`genai.Client` raise. Keep it that way.
  Live checks go in `.dev/` with `ASSETFORGE_HOME=$PWD/.dev/live` and cost real money, so keep them minimal.

### App: live data and tasks

- Design and status: `docs/app/02-architecture.md` (brief: `docs/app/01-application-brief.md`).
- `app/feed.py`: one watcher thread per server computes revision markers once a second while anyone listens (5 s
  idle): `library`, `jobs`, `tasks`, `stories`, `publishing`, `logins`, `build`, and `story:<id>:<area>` for
  text|cast|art|audio|checks|releases|workflow|settings|progress from the story folder's files (scandir + stat;
  `AREAS`), story art = library assets keyed `vn/<slug>/…`, progress = the project's text jobs. Ring of 64 snapshots
  by `seq`; app POSTs `poke()` it; it runs `tasks.recover()` every 10 s.
- `app/api_live.py`: `GET /api/events` (SSE through `server.StreamResponse`: `hello`, `change`, `ping` every 15 s;
  `?since=seq`), `GET /api/changes?since=` (same payload, polling fallback), `GET /api/version` (build = Python hash
  at start + static signature re-checked every 2 s), `/api/tasks[/{id}[/cancel|retry|dismiss]]`.
- `core/tasks.py` + schema v7 `tasks`: `track(lib, kind, title=, story=, project=, locks=, cancellable=, steps=,
  request=, …)` / `create()`; states queued|running|waiting|succeeded|failed|cancelled|interrupted. Heartbeat thread
  (own connection) every 4 s; `effective_state()` = interrupted when the heartbeat is > 30 s old or the pid is dead on
  this host; `recover()` persists it and fails that task's still-running text jobs. `waiting` = stopped for a decision
  (gate, paid fallback); superseded by a newer task of the same kind. Cancel is cooperative (`check_cancel()`);
  queued tasks always cancel. **Concurrency:** tasks on one story with intersecting `locks` queue (`wait_turn`).
- Registration: app story actions (`api_story.LOCKS/CANCELLABLE/TITLES/TABS`, `start_story_task`), Studio runs
  (`api_audio.start_production`), CLI long commands (`cli_common.TRACKED`, in `session()`; backend only with `--yes`;
  `started_by` cli or `$ASSETFORGE_STARTED_BY`), any script (`started_by` script). `story/tracked.py` `TrackedCtx`
  (via `cli_story._tracked`) turns each model call into a step and checks cancel between calls; steps.py is untouched.
  `AppServer` runs `queue.recover` + `tasks.recover` at start (`recover=False` to skip).
- Page: `store.js` is the only listener (`connect`, `watch(topics, fn, {owner})`, scopes per view, `cached/load`,
  `onTasks`, `waitTask`, `waitJob`, `onStatus`); `live.js` (`rerender` keeps typed text/focus/details/scroll and
  defers while a field has focus; `data-live-key/sig/state` flash `af-new`/`af-changed`; `panel()` with Retry;
  `confirmAction()`); `activity.js` (drawer, toasts, badges, offline/outdated banners, auto-reload when nothing is
  typed or playing). Views return `{topics, refresh}` (or `{topics}` → the shell re-mounts in place). Deep links
  `#/story/<id>/<tab>?chapter=&asset=&show=&panel=`; `?tab=` still works. No `setInterval` polls data any more.
- Manual checks: `.dev/app-lib` with the kite-season copy and fake providers (launch config `app-dev`, :8776). The
  clarity gate picks Sonnet/Opus on the signed-in claude CLI unless the run or the story asks for the `fake` provider
  (`steps._clarity_models`); a manual `story write` without `--provider fake` therefore makes real model calls.
- Background waits (agents too): never `until ! pgrep -f <name>` (the loop's own command line matches, so it never
  ends); wait on a pid or a file, with a time limit.

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
- Schema v5: `assets.parent_asset_id` (frames → their sheet, backfilled from `metadata.frame.sheet`); summaries
  carry `parent_asset_id` and `children`. `browse(group=True)` maps parts to visible parents (the app's default),
  `parent=` lists parts; `review.archive/restore` cascade to parts (restore only those archived with the sheet).
- `image/sprites.py`: `guess_grid` (job's `sheet` request > prompt "N columns and M rows" > `detect_grid` from
  subject rows), `plan_slice` (dry run), crowded cells refused unless `allow_crowded`, `fit` shared|each|auto,
  names from `prompt_items`, re-slice archives stale frames. `slice_image` (components → grid cells → despill → scale → anchored cells + report),
  `slice_asset`/`assemble_assets` (frame assets `<base>_<anim>_<nn>`, `metadata.frame.pivot/fps/loop`, collection,
  aligned sheet variant, `metadata.animation`). Export writes atlas frame `anchor` and `animations.json`.
- `core/qc.check_asset` adds expectation warnings from `metadata.expect` (size/aspect/format), near-duplicates and
  slice reports. The engine stores `expect` and runs it for every new candidate. Names come from
  `engine.subject_name` (style/filler words dropped).
- CLI modules: `cli_discover.py` (similar, duplicates, reindex), `cli_art.py`, `cli_sprite.py`, `cli_create.py`
  (add, regenerate, guide, methods).

### Game audio: designs, ambience, music (post-v1)

- Schema v6: `asset_files.audio` (features from `audio/features.py`: duration, channels, LUFS, centroid, rolloff,
  flatness, onset rate, estimated bpm/key, `loop_seam_ok`) and `sound_profiles`. Search/browse filters
  `bpm key loopable channels` (`core/search.audio_filters`) prefer exact `metadata.audio` (generated) over estimates;
  FTS `tech` gets loop/stereo/short/long/bright/dark/noisy/tonal/calm/busy/major/minor; `similar` dispatches on file
  kind; `reindex` fills audio features.
- `sound.py`: `SoundDirection` (genre, mood, era, space, key, tempo range, instruments, sfx_preset, loudness…),
  `SPACES` (effect chains), project profiles (`sound profile …`, or `sound:` in an art profile YAML), `resolve`.
- Shared DSP: `audio/dsp.py` (stereo helpers, RBJ biquads, `sweep_filter`, `drift(period=)`, FM, Karplus-Strong,
  echoes), `audio/looping.py` (`crossfade_loop` for textures, `fold_loop` for decays, `circular_effects` for
  reverb in steady state), `audio/master.py` (LUFS target per kind, stateless soft limiter, no trim for loops).
  `validation/audio.loop_seam/seam_ok` judge clicks and level jumps relative to the sound's own variation.
- `audio/pipeline.py`: engines register `render(spec, index, seed, sr) -> Take(mix, extras, files, info)`;
  `produce` does fingerprint reuse (only when the caller fixed the seed), mastering (extras get the mix's gain so
  stems sum), QC, and cataloging (`vN` WAV masters, `vN_<fmt>` delivery, `vN_<extra>`, `vN_midi`); `vary` and
  `reproduce` work for every engine. `active_home` tells engines which library is rendering (SoundFonts).
  The original SFX engine (`audio/generate.py`, `assetforge-synth`) is untouched, so old sounds still reproduce.
- `audio/recipes/`: 80 recipes in 10 modules (ui, reward, motion, impact, weapons, magic, creatures, mechanical,
  foley, world); `Recipe.to_dict` reports `category` (module). New presets don't change old ones.
  `audio/design.py` (`assetforge-design`): `Patch` (oscillators/FM/noise/pluck, envelopes, filter, LFOs) and
  layered `Design`.
- `audio/ambience/` (`assetforge-ambience`): `beds.py` (18), `events.py` (25, many reuse recipes via
  `from_recipe`), `scenes/*.yaml` (24, plus `$ASSETFORGE_HOME/scenes`), `engine.py` (resolve scene + request into a
  stored `AmbienceSpec`; intensity = bed level ±6 dB and event density; stems, intensity versions).
- `audio/music/` (`assetforge-music`): `theory.py` (keys/modes, roman numerals, voicing, meter), `score.py`
  (`Score`/`Track`/`Note`, beats), `instruments.py` (30 synths, GM programs, aliases), `drums.py` (4 kits, GM drum
  map), `midi.py` (mido), `composer.py` (17 genre templates, `Brief`, aspect seeds so `energy` and `fixed` keep the
  tune), `render.py` (per-role stems, loops wrap), `generate.py` (`MusicRequest`, profile → brief, edit/vary),
  `soundfont.py` (fluidsynth CLI per track; `auto` resolves at request time and the SoundFont sha is in the spec).
- `audio/references.py` + `references.json`: numeric targets (third-octave shape, noisiness, flutter) measured
  from freely licensed field recordings (Wikimedia Commons, Freesound CC0; recordings not shipped, kept in
  `.dev/references` with `.dev/compare.py` / `make_targets.py` for analysis). `REFERENCE_FOR` maps recipes,
  beds and scenes (`name/case` for several targets per recipe) to targets; `tests/test_audio_references.py` keeps
  each within 4.5 dB mean and also checks impact decay times and that voices aren't chopped into bursts (the
  spectrum alone missed both), and that growl/roar have no single dominant pulse rate (fixed pitch + steady pulses
  = chainsaw) and a dense, non-clicky waveform. Creature voices are matched to designed game creature sounds
  (`growl_monster`, `roar_monster`), not wildlife. `creatures.beast` builds them cycle by cycle
  (`glottal_pulses`: jitter, shimmer, period doubling) through a moving all-pole `vocal_tract` and an allpass
  `disperse` (the fallback when no creature recordings are installed). When reworking a natural sound, compare against a real recording first
  (spectrogram, spectrum, envelope) instead of tuning by guesswork. Most real impacts and fire are noise-led:
  long-ringing sine modes read as synthetic.
- `audio/creatures/` (`assetforge-creature`, CLI `cli_creature.py`): the real creature voices. `pack.py` installs five
  CC0 OpenGameArt packs (pinned SHA-256s, `creature install --yes`, or `--from` a folder; `add_folder` for your own)
  into `$ASSETFORGE_HOME/creatures/`, slices long takes and catalogs clips in `index.json` with family (beast, alien,
  insect, slime, critter), roles (body, growl, roar, grunt, attack, breath, hurt, death, voice) and features.
  `engine.py` layers identity (+ double) + body (low-passed, pitched down) + grit (high-passed, aggression,
  centroid ≤ 3.5 kHz) + intake/tail breath; size = varispeed (`size_shift`), lengths fit per sound (`LENGTHS`),
  presets require their own kind of clip (`prefer`, e.g. wolves always snarl). Specs store pack versions;
  `masters.can_rerender` keeps masters whose packs are missing. `creature_from_words` routes `request`
  descriptions (growl/roar always; attack/hurt/death/grunt only with a named creature) when packs are installed.
- `audio/recipes/physical.py`: modal resonances, smooth strikes, N-wave shocks, windowed `crackle` (blast/rubble),
  sharp broadband `clicks` (embers, snow crust), `exp_env`, `flame`, `fade_tail`.
- `audio/infer.py`: description → recipe/patch, scene + extra layers, or music brief. `request` routes `sfx`,
  `ambience`, `music` through `sound_plan` (`sound` dict / `--sound k=v` overrides). Export: `sounds.json` loop
  points, music facts, `extras`, `--audio-takes` variations, `--audio-sprite`.
- `audio/codecs.py`: M4A via ffmpeg's native AAC encoder (its MP4 edit list trims the priming, so decoded length
  is exact and loops stay aligned; tested). No ffmpeg → M4A is skipped with one stderr warning, never an error.
  `read_audio` decodes anything (soundfile, then ffmpeg). Export defaults to `--audio-format ogg m4a`:
  `sounds.json` `src` = [ogg, m4a], sprites get an `.m4a` twin, older assets get M4A derived from the WAV
  master (no re-render).
- `audio/masters.py`: WAV masters are never deleted automatically. `audio prune-masters [--older-than D
  --project P --type T --dry-run]` deletes re-renderable masters only (procedural; SoundFont music only while
  that .sf2 sha is installed; never imported audio; a shared file only when every user is prunable), marking
  `asset_files.extra.pruned`. `ensure_file` re-renders a pruned master and checks its SHA-256 before anything
  reads it (process, export, analyze, the app's media route); `verify`/QC treat pruned files as fine.
  `audio restore-masters REF`. Possible later: FLAC masters (lossless, ~half the size).
- CLI: `cli_sound.py` (sound profile, spaces), `cli_ambience.py`, `cli_music.py`, `cli_audio.py` (design, patch,
  soundfont). App: `/api/sound/options`, `/api/generate/{design,ambience,music}`, `/api/sound/request` (the
  agents' `request`), `/api/sound/profiles/{project}[/delete]`, `/api/assets/{id}/sound/vary` (keep=chords|melody
  for music), `/api/assets/{id}/music/edit`, asset detail `sound`, `views/sound.js`.

### Visual-novel stories + text providers (post-v1)

- `llm/`: text generation, built like `image/providers/` (FACTORIES, `OVERRIDES`, `provider_chain`, `ProviderError` kinds).
  - **Preferred providers are the signed-in CLIs, so no API key is needed:**
    - `claude_cli.py` runs `claude -p --output-format json --json-schema --tools "" --no-session-persistence`.
      It must **never use `--bare`**: bare mode skips the keychain, where the plan login lives.
    - `codex_cli.py` runs `codex exec --sandbox read-only --ephemeral --json --output-schema -o answer.txt`.
    - `gemini_cli.py` puts the schema in the prompt.
  - `api.py` holds the API fallbacks. They are **opt-in**: a key alone is not enough, a `ASSETFORGE_*_TEXT_MODEL` must also be set.
  - `fake.py` uses a caller-supplied `offline` builder.
  - `runner.py`:
    - The prompt goes in via stdin, and the CLI runs in an empty temp cwd.
    - The env is scrubbed of `CLAUDE_CODE_*`/`ANTHROPIC_*`/`OPENAI_*`… A parent Claude Code session's env breaks the child CLI's auth.
    - `classify()` maps CLI text to error kinds.
    - Tests patch `runner.spawn`/`runner.which`.
- `llm/structured.py` `generate(lib, TextCall, Model)`:
  - `strict_schema()` produces closed objects, with every property required and optional ones nullable. Keywords
    such as `title` are dropped, but never field names: `properties`/`$defs` are name maps.
  - Each call is a `generation_jobs` row of kind `text`, fingerprinted on task, prompt, schema, provider and model.
    A cache hit unless `fresh`.
  - Prompt and answers go to `jobs/<id>/`, followed by one repair round, retries (`structured.sleep`) and fallback.
    The reported error is the requested provider's, and `details.errors` lists all of them.
- `story/` follows storycraftr's workflow, extended for branching visual novels.
  - `models.py` covers StoryConfig, World, Cast, Outline, SceneDraft, Script and Node (a flat `kind` + fields).
  - `prompts.py` sends a compact JSON bible in place of RAG. **Bump `PROMPT_VERSION` when wording changes.**
  - `steps.py` runs init → world → cast → outline (`repair_outline`) → write (resumable, BFS order, so-far
    summaries) → refine → check, then `assemble` writes `script.json` + `notes.md`.
  - `validate.py`:
    - `validate_script` covers links, reachability, no path to an ending, cast and variables.
    - `conform_scene` forces the planned exit and fixes expressions and speakers without a model call.
  - `offline.py` builds a coherent story for the fake provider.
  - `project.py` handles the folder layout, atomic writes and `.back` copies.
- Story format (Phase 1 of `docs/vn-benchmark-gap-analysis.md`; sample: `examples/kite-season`, 7 in-chapter
  choices, 4 callbacks, 3 places, an ending gate):
  - A `choice` whose options have no `goto` is an **in-chapter choice**: the option's `reaction` lines (`Line`: speaker
    or narration) play, then the scene carries on. A choice whose options all have `goto` ends the scene (never mixed).
    `is_exit()`/`node_exits()` decide; `TERMINAL` only lists kinds that *can* end a scene.
  - Any node may have `when` (all conditions must hold): callbacks to earlier choices. `reads()` lists the variables
    a node checks; the validator warns `unread_variable` for stats nothing reads.
  - `place` (location, time?, music?, text?) moves within a chapter and clears the stage; `music` changes the mood.
    `models.walk(scene)` yields each node (and reaction line) with the place/time/music in effect: assets, usage and the
    sound plan use it, so backgrounds, ambiences and moods reached mid-scene get made.
  - `branch` (branches + fallback `goto`) is a gated exit; planned as `PlannedScene.branches` (+ `next` fallback) and
    put in place by `conform_scene`. `Ending.requires` records what an ending needs. `PlannedScene.exits()` lists all exits.
  - `Variable.kind` (relationship/personality/resource/flag/world), `character`, `min`/`max` (the player clamps).
  - `Outline.chapters`/`Script.chapters` + `scene.chapter`: a chapter can hold several scenes; the player shows a chapter
    card only when the chapter changes, and `progress.chapter_numbers` numbers by declared chapter.
  - The player keeps `place`, `mood`, `react` (the reaction being played) and `chapter` in its save.
- Writing to the benchmark (Phase 2):
  - `story/lint.py` measures each chapter on one path (an in-chapter choice adds its options' average reaction):
    words, boxes, choices, first-choice box, the longest stretch without a choice, callbacks, stat writes, long boxes.
    Hard rules (errors): ≥ 75% of `StoryConfig.chapter_words`, ≥ `chapter_choices` − 1 choices, first choice by
    box 15, never 400+ words without a choice, a callback from chapter 3 on. Ending chapters only get the wall rule.
    `lint_story` → `lint.json` on every `assemble`; CLI `story lint`.
  - `steps.write` writes a chapter in `lint.parts_for(config)` calls (~450 words each, 4 for 1,500; asked for 140% since
    Sonnet delivers ~70%; part 1 must reach the first choice by box 8; tasks `scene.<id>.p1..p4`,
    endings in one call), each given the beats for its part (`prompts.BEATS`/`part_beats`) and what earlier parts
    wrote. Then the linter; a failing chapter gets one `scene.<id>.fix` call with `repair_note`, kept only if it
    lints better. `progress._base` groups all calls of a chapter.
  - The outline prompt carries the season arc (`prompts.season_arc`), the stat design (typed variables) and asks for
    `hook`/`callback` per chapter, `requires` per ending and gated `branches` with a `next` fallback. The scene prompt
    carries `CHOICE_RULES` and `LINES`.
  - `repair_outline`: gates without a fallback fall back to the open ending (no `requires`) or an orphan scene; an
    `ending` that is not an ending id (models write the kind) becomes the scene's own id when that is an ending; dead
    ends in the final scenes get an unused ending.
- `story/assets.py`:
  - `manifest()` maps the script onto `AssetRequest`s keyed `vn/<slug>/…` in project `vn-<slug>`.
  - Expressions, time-of-day backgrounds, CGs and title art take `refs` (keys resolved to the asset ids made earlier).
  - `produce()`:
    - First checks the key via `request.by_key`.
    - Images use `always_generate`, so identity is never a search match; SFX use search reuse.
    - Items whose refs don't exist yet are `blocked`.
    - The run stops after auth, quota or not-configured errors.
- `exporters/vn.py`: uses the generic export (webp, ogg+m4a) and writes `story.json`, `story.js`
  (`window.STORY`, so file:// works) and `vn_player/index.html`. It never replaces foreign files. `window.vn` is a test hook.
- Picking a model:
  - Each provider has `signed_in()` (local checks: `claude auth status --json`, `codex login status`, gemini's
    oauth file) and `models()`. Claude's are aliases. Codex's are the `visibility: list` entries in
    `$CODEX_HOME/models_cache.json`. The Codex desktop app rewrites that file too, so it can list models the
    older CLI rejects ("requires a newer version"); `version_note()` warns about this.
  - With no model chosen, `CodexCLIProvider.run` walks the listed models past rejections and remembers the first
    that works (`_WORKING`).
  - `llm/catalog.describe()` merges all of this and marks models with successful `text` jobs as `worked`.
  - `runner.search_path()` adds `~/.local/bin`, Homebrew, npm/bun/volta/nvm dirs, because a Finder-launched app has a bare PATH.
- `llm/login.py` signs in from the app:
  - Runs `claude auth login --claudeai`, `gemini` (interactive; `-p` refuses manual auth) or `codex login`.
  - Uses a 4000-column pty so links never wrap, in an empty temp dir, because Gemini in a folder with settings asks
    for trust and restarts, which breaks the pasted code (`invalid_grant`). It also forces Gemini's
    `security.auth.selectedType` to `oauth-personal`.
  - Pulls the link out of the output, and `submit()` writes the code to the pty.
  - Success = the provider's `signed_in()`; the whole flow times out after 10 min.
  - API: `POST /api/llm/login`, `GET /api/llm/login/{id}`, `POST …/code`, `…/open` (`open` its own URL), and `…/cancel`.
  - Tests use a stand-in CLI script (`runner.popen` is blocked by `conftest` unless restored).
- Gemini CLI needs `--skip-trust` headless. Google's retired free individual tier ("no longer supported…
  Antigravity") maps to `kind=unsupported` with a hint. Antigravity has no public CLI (its `language_server` uses a private protocol).
- `steps.improve(feedback, include_check, scenes)`: a `RevisionPlan` editor call, then `_refine_scene` per edit
  run through `_parallel` (each thread opens its own Library), then a `feedback.json` history entry and `assemble`.
- Desktop app:
  - `app/api_story.py` (registered at the end of `api.py`) serves `/api/llm/providers` (cached 60 s),
    `/api/stories` (GET list / POST create in `<library>/stories/<slug>`), `/api/stories/{id}` (detail),
    `/{id}/settings`, `/{id}/run` (`write|improve|refine|check|assets_plan|assets|export`), and
    `/play/{id}/…` (only files in `build/`).
  - `/run` starts a durable task (`api_story.start_story_task`; queued when its `LOCKS` meet a live task on the
    story; `/api/story-tasks/{id}` keeps the old shape), which re-exports the demo after writing, improving or making
    assets.
  - `story/project.register()` keeps `<library>/stories.json` (id → folder); CLI steps register too.
  - The UI lives in `views/story.js` (list + new story with a writer picker; detail tabs Script/Improve/Assets/Play with an iframe player).
- `story/progress.py` builds the live view from the story files plus `generation_jobs` (kind `text`,
  `request.project` = the story's project), so work started by the CLI, an agent or the app all shows, and it
  survives restarts. `running` jobs older than 30 min count as stale.
  - `progress()` returns the stage, steps, chapters in reading order with `written|writing|revising|planned` and
    BFS chapter numbers, readable labels, a feed and an ETA.
  - `preview_script()` keeps written scenes as they are and turns unwritten ones into stand-ins
    (`draft: true`, `summary`, plan exits).
  - `library_assets()` maps `vn/<slug>/…` keys to `/media/variant/<id>.<ext>`; the extension lets the player pick
    ogg or m4a, and the media route accepts it.
  - App routes:
    - `/preview/{id}/` is the player (RawResponse with `PLAYER_CSP`: no API access from model-written pages);
    - `/preview/{id}/story.js` is live data;
    - `/api/activity` is the sidebar pill;
    - `/api/stories` lists and `/api/stories/{id}` details, both carrying `progress`; the detail also has
      `written` scenes and `cover`.
  - UI (`views/story.js`):
    - hub (search, filter, progress bars, covers);
    - a 4-step wizard (`#/story/new`);
    - the story page with a live tracker and tabs: Chapters (rail + reader), Play (live iframe,
      `?scene=` / `&chapter=`), Improve, Art & sound, Settings.
  - The player (`vn_player/index.html`) has chapter cards, a draft banner, a Preview tag, versioned saves,
    History (H), text speed / Auto, and no `innerHTML`.
  - An untitled story gets `title_from_writer`: `World.title` names it, and the slug comes from the idea.
- `story/sound.py` holds the story sound plan:
  - `SoundPlan` gives each location a scene/beds/events and each mood a genre, instruments as a list of
    `RolePick`, energy and tempo.
  - The prompt carries the real catalog. `clean()` drops unknown names, caps noise beds, drops layers the chosen
    scene already has, and covers every location.
  - It is saved as `sound.json`; `assets.manifest(sound_plan=)` passes explicit `sound` options.
  - `produce(redo=, replan=)`: `redo` archives and remakes, restoring identical remakes; `replan` asks for a new plan.
  - The ambience additions are `rain.inside` (indoor roof drumming), the `clock_tick` bed, the `dog_bark_far`
    event, `room_tone.low_cut`, and the scenes `home_interior` / `home_rain`.
  - All new params default to the old behaviour, so older ambiences reproduce bit for bit.
  - The `infer.ambience_from_words` `VAGUE` words never summon layers.
- App shell (redesigned after research on Eagle, Linear, Raycast and the Apple HIG):
  - `index.html` and `shell.css` provide a grouped sidebar (Workspace / Library / Make / Direction / Operations)
    with Review and Stories badges, and an activity pill.
  - The theme switch (light/auto/dark) is stored in `localStorage af-theme`.
  - The top bar has breadcrumbs (`window.afCrumb(title)` from views), a ⌘K trigger and New/Create buttons.
  - Below 860 px the sidebar becomes an icon rail.
  - `tokens.css` has calm neutrals and the indigo accent (`--accent-soft`, `--shadow-pop`, `--ease`).
  - `palette.js` is the ⌘K palette: pages, stories, asset search with thumbnails and actions. Its classes are
    `cmdk-*`, because `.palette` is taken by the colour palette input.
- `views/assets.js` + `views/assets.css` (`ab-*` classes) add:
  - S/M/L density (⌘= / ⌘-) and Space Quick Look (← → A X P);
  - hover play and progress on audio cards;
  - quick filter chips;
  - status dots;
  - "Used in stories" via `GET /api/assets/{id}/stories`.
- Story assets (`story/usage.py`):
  - `story_assets()` lists every asset a story uses (characters × expressions, places × times + ambience, CGs,
    UI, music, SFX) with its library asset and the chapters that use it. It works mid-writing via `preview_script`.
  - `stories_using()` is the reverse lookup.
  - Route: `GET /api/stories/{id}/assets`. The story page's Assets tab (`storyAssetsTab`) has a chapter lens and
    an all/missing/made filter, with generation controls below.
- CLI: `cli_story.py` (`story …`, `llm providers|models|test`). The preview config `vn-demo` serves `.dev/vn-demo`.
- `MusicRequest.key` is the **musical** key; the asset key is `asset_key`. `request` and `music compose --asset-key`
  both pass it, and `generate_music` never uses the musical key as the catalog key.

### Creative direction (audience, genre, world, naming, narrative style, art)

- `story/direction.py`: `CreativeDirection` (region, readers, writing_style, genre + subgenres, tradition, themes,
  world_type, culture, `world` prefs, naming + reserved/overrides, pacing/tone/drama/romance/mystery/progression/
  depth/choices/cliffhangers, art_style + notes, `custom` instructions/requirements/exclusions/canon). Stored as
  `StoryConfig.direction`; **`None` keeps the pre-direction behaviour byte for byte** (older stories are untouched).
  Precedence: explicit instructions > the story's saved direction and written canon > preset > `DEFAULTS`.
  `system_rules()` tells the writer to infer nothing from the operator or earlier stories.
- Knowledge files: `story/genres.yaml` (6 primaries, 24 subgenres with `kind` premise|mechanic|setting|protagonist|
  theme, mechanics, beats, `pairs_well`/`conflicts_with`) and `story/traditions.yaml` (6 narrative traditions as
  techniques). Presets: `story/direction_presets/*.yaml` and `$ASSETFORGE_HOME/story_presets/` (same name wins).
  `resolve()`, `warnings()` (contradictions, never refusals), `summary()`, `impact()` (what a change leaves stale).
- Prompts: `world_block` (market ≠ setting, premise sheet, visual direction), `names_block`, `plan_block` (season
  rules: questions opened/closed, antagonist moves, one remembered decision per chapter), `scene_block` (chapter
  shape, no "whatever you chose", settled decisions never re-offered), `check_block`. `PROMPT_VERSION` 4.
- Naming guide: `steps.names` (`names.json`, `NamingGuide`) runs after the world and before the cast whenever a
  direction exists; `cast` follows it and `name_overrides` (by id or role) win; missing reserved names are reported.
- Writing: `repair_outline` turns an exit choice whose options all lead to one scene into `next`; every chapter part
  gets `upcoming` (the next chapters' plans, not to be written) and `decided` (flags set by earlier chapters).
- Art: `assets.story_art_direction` builds the project's art profile from the direction's `art_style` preset
  (`art_presets/manhwa-fantasy|light-novel-anime|western-fantasy|cinematic-semi-realistic|stylized-vn|dark-fantasy`)
  plus notes; the narrative tradition never touches the look.
- CLI: `story init --preset X --direction '{…}'|@file`, `story presets [--save NAME --dir D] [--delete N]`,
  `story direction [--preset X] [--set k=v] [--yes]`, `story names`. App: `GET /api/story/direction`,
  `POST /api/story/direction/check`, `POST /api/story/presets[/{name}/delete]`, `POST /api/stories` with
  `preset`/`direction`, `POST /api/stories/{id}/settings` with `direction` (+ `confirm` after an impact review).
  UI: `views/direction.js` (`directionForm`: presets, groups A–F, live summary and warnings) in the wizard's
  Direction step and the story Settings tab. Docs: `docs/narrative/` (audit, research, strategy, pilot design).

### AI execution plans, workflow gates, Character Studio (post-v1)

- `llm/plans.py`: `ExecutionPlan` (managed | manual `steps` of provider/model, `paid_fallback` allow|confirm|never,
  `per_task` overrides, `daily_api_limit_usd`) stored library-wide in `$ASSETFORGE_HOME/ai.json` and per story in
  `StoryConfig.ai`. `resolve(task, settings, home, story_plan, override_provider/model, allow_paid)` applies the
  precedence run override > story > library > managed (`managed_steps`: subscriptions in chain order, Opus for
  `HEAVY_KINDS` on Claude, Sonnet otherwise; billed APIs last). `structured.generate(..., resolved=)` tries the steps
  with each step's own model and records `result.choice` (requested, used, chosen_by, fallbacks, held_back) on
  the job; a paid step held back by `confirm` raises `ProviderError(kind="needs_confirmation")` once the
  subscriptions fail. `Ctx.allow_paid` / `/run {allow_paid}` is the one-off yes. `llm/anthropic_api.py` is the
  opt-in Anthropic API provider (`ANTHROPIC_API_KEY` + `ASSETFORGE_ANTHROPIC_TEXT_MODEL`; Messages API with
  `output_config.format`); never edit the OpenAI/Gemini files to add Anthropic calls. Tests block
  `anthropic.Anthropic` like the other SDK clients.
- `story/workflow.py`: twelve `STAGES` derived from files + `workflow.json` (mode auto|guided|manual, approvals,
  history, `paused_at`). `blocked_by(project, step)` is the gate the app's `write`/`assets`/`export` actions check
  (`BEFORE_STEP`); a blocked run returns `{paused_at, needs}` and the UI offers Continue after approval.
  `status()` gives each stage done/working/next/decisions/inputs and `needs_you`; `character_impact()` lists the
  chapters, routes and art keys a character change touches.
- `story/characters.py`: `CharacterProfile` (identity/personality/narrative/visual, per-section `approvals`,
  `locked`, versions, `reference`, `proposals`) in `characters.json`, derived from `cast.json` and written back by
  `sync_cast` (approving a section locks it). `revise(ctx, cid, request)` is a model call (`prompts.character_prompt`,
  task kind `character`) that stores a proposal; `apply_proposal` makes it canon. `concepts()` makes n unkeyed
  candidates from `visual_spec()`, `set_master()` picks one (review.pick, the neutral key moves to it, siblings are
  rejected, visual approved), `consistency()` compares expressions to the master by dHash/palette (a heuristic,
  says so). `steps.alternatives`/`choose_alternative` keep chapter variants in `alternatives/<scene>.json`.
- Linter additions: `placeholder_text` (error: a repair call once wrote "test line one"), `no_cg`, `no_decision_flag`
  (warnings; `lint_scene(..., flags=)`).
- App: `GET/POST /api/ai/plan`; `/api/stories/{id}/workflow` (GET, POST mode|approve|changes|reopen);
  `/api/stories/{id}/characters[/{cid}[/approval|/lock|/proposals/{pid}|/master|/consistency|/impact]]`;
  `/alternatives/{scene}[/{alt}]`; run actions `character`, `alternatives`, `concepts`, plus `provider`/`model`/
  `allow_paid` on every run. Views: `workflow.js` (default story tab), `characters.js`, `ai.js` (`#/ai`,
  `modelSelect`), model pickers on Improve/Check/Alternatives; the activity feed shows who chose the model and
  any fallback. Docs: `docs/narrative/12-production-ux-audit.md`.

### Narrative audio direction (post-v1)

- `story/audio_direction.py`: per-chapter `AudioPlan` of `Cue`s (ambience | music | sfx | accent | silence | stop,
  `what`, `text`, `purpose`, `at` = the line index it plays with, `loop`, `fade`, `level`, `when` for branch-only
  sounds, `status`) in `audio/<scene>.json`. `plan(ctx, scene)` is a model call (task `audio.<scene>`, kind
  `audio`, Opus when managed) over the numbered lines, the catalog (recipes, ambience scenes, moods) and
  `direction.audio_block`; `clean` maps names to the catalog; `review` is deterministic (profile budgets per
  1,000 words, opening ambience, one accent, silence in tense chapters, loops stopped, repetition, sync, crowded
  lines); `apply` writes the cues into the chapter as nodes marked with `why` (replacing earlier cues, exit kept
  last); `statuses` reads the library (needs_generation / needs_review / approved); `intensity` is the per-line
  strip the panel draws. Nothing is planned during brainstorming; sounds are made by the normal `assets` step.
- Node model additions (all optional, older scripts unchanged): kind `ambience` (`ambience`: location id, catalog
  scene or `none`), `Mood` `silence` (music stops; no asset), sfx `loop`/`stop`/`level`, `fade`, `why`. The
  manifest skips `silence` and makes `vn/<slug>/amb/<scene>` loops for named catalog scenes.
- `CreativeDirection.audio` (`AudioPrefs`: profile minimal|balanced|cinematic|custom, music/sfx/ambience/accents/
  silence levels, style) drives the prompt and the review budgets; group G in the direction form.
- `audio/recipes/narrative.py`: `knock`, `bell` (bronze partials, `cracked`), `phone_ring` (electronic | classic |
  vibrate), `chain`, `page_turn`, `hooves`, `armor`; presets `distant`/`muffled`/`huge` give material and distance.
- Player: volume panel (master/music/sounds/ambience, mute; persisted in prefs), crossfaded music/ambience with
  per-node `fade`, `silence`, `ambience` nodes, looping sfx with `stop`, every sound stopped on scene change, load,
  restart or route jump; loops pause when the page is hidden. The story never depends on sound.
- App: `GET/POST /api/stories/{id}/audio/{scene}` (apply | update | remove | add | approve_asset | reject_asset |
  regenerate), run actions `audio_plan` (scene, note, model) and `audio_make` (= assets audio only, SFX reuse by
  search). The scene audio panel sits under each chapter in the Chapters tab (plan, intensity strip, review,
  per-cue preview/approve/replace/regenerate/remove, Preview with audio).

### Audio production revamp (music and ambience)

- Diagnosis and benchmark: `docs/narrative/15-audio-revamp.md`; technology research `16-audio-technology-evaluation.md`;
  benchmark harness `.dev/audio-bench/bench.py` (ten briefs, `baseline` vs `revamp`, measured metrics, listening page).
- `audio/production.py`: `MusicDirection` / `AmbienceDirection` (structured briefs), `direct()` (a writer call with the
  catalog and the project's sound identity; heuristic fallback without a writer; task `audio_direction.<kind>`),
  `produce_for_brief()` (candidates → `quality.review` → `refine` by the measured problem, at most `MAX_ROUNDS`
  rounds → the two best kept, the rest rejected, the chosen one's `metadata.production` records brief, direction and
  score), `style_guide`/`add_reference` (`$ASSETFORGE_HOME/audio_style/<project>.json`: approved references with their
  measured character, fed into later directions and used as a selection target), `theme_variations` (same melody and
  harmony seeds, another mood). `audio/quality.py`: `measure` (loudness, centroid, flatness, salient events/s from `events()` (superflux
  + a median floor, so tremolo strings and rain beds are not "busy"; `features.analyze`'s onset rate is only for search
  tags), reading-band share,
  sub share, repetition index, event regularity, dynamic range, clicks, loop seam) and `review` (hard technical
  failures, brief-adherence misses with weights, `REFINEMENTS` per problem). Nothing listens; a person approves.
- Composer: five restrained templates (`fantasy_restrained`, `melancholy_piano`, `court_intrigue`, `rising_magic`,
  `heroic_lift`), a mood map that no longer collapses calm/sad/resolution into one template, and `Brief` controls
  `melody` (prominence), `percussion` (none|light|full), `arc` (rise|rise_fall|fall: section velocities and layer
  thinning), `warmth` (top-end roll-off). `generate_music(..., compose_extra=)` passes them through.
- Ambience: `AmbienceSpec/Request.low_cut`, `high_cut` (bus filters: no rumble under a place, no hiss in the reading
  band), `evolve` (seamless slow level drift of the beds).
- App: `app/api_audio.py` (`/api/audio/direct`, `/produce` background tasks with readable steps, `/tasks/{id}`,
  `/approve` (key, reject siblings, as_reference), `/style/{project}`, `/measure/{asset}`, `/catalog`); view
  `views/audio_studio.js` at `#/audio` (brief → direction → settings → candidates with measured reasons → feedback
  chips → refine → approve → sound identity). The scene audio panel's music/ambience cues link there with
  `kind/brief/project/key/story` in the URL (`studioLink` in `views/story.js`; `_audio_view` returns `project`).
  Benchmark: `.dev/audio-bench/bench.py baseline|revamp` renders the ten briefs, measures them and rebuilds
  `index.html`, a blind A/B page (arms shuffled per row, labels and numbers hidden until Reveal, picks kept in
  localStorage, "Copy results" exports them). Write-up: `docs/narrative/15-audio-revamp.md`.

### Reading-first audio (stabilization, 2026-11-11)

- The owner's priority order: comfortable audio for long reads, then story quality; advanced audio research is deferred.
  Player (`vn_player/index.html`): defaults music 0.40 / ambience 0.35 / sfx 0.70, crossfades 2.4 s music / 1.8 s
  ambience, and music **breathes** (`BREATHE`: after 2 loop passes, 5 s fade out, 36 s rest, return at 85 %);
  `window.vn.audio()` logs what played for long-read tests. `AUDIO_BUDGETS` halved (balanced: 1.2 music, 2.5 sfx per
  1,000 words); `review` warns `music_churn` (two changes under 300 words apart); `statuses` warns on music loops
  under 60 s; the plan prompt carries RESTRAINT RULES (no cue depends on timing, a chapter may have no music, no sfx
  because something is said). Story music is 96 s loops at energy ≤ 0.5 (`sound.music_options`); Studio
  `MusicDirection` defaults 96 s / warmth 0.45. `sound.clean` drops `crowd_murmur` unless the script puts people at
  the place; `request._without_voices` measures every story ambience render and re-renders with the bed muted when
  voice-like content is found in a voiceless brief (`generated.voice_check`). Audit: `docs/narrative/21`.
- **Text writer: ChatGPT first** (owner, 2026-11-11). The library plan `~/.assetforge/ai.json` and the pilot's `StoryConfig.ai`
  are manual: `codex-cli` (ChatGPT subscription, first listed model that works, `gpt-6.1-sol` today) → `claude-cli sonnet`
  → `gemini-cli`; paid APIs `confirm`. Prompts were tuned on Sonnet (part sizes ask for 140 %); re-tune on GPT output
  when the pilot's chapters are written. Names: global and invented, never tied to one country (`naming: custom`).
- **Narrative Dynamics + branching** (`story/dynamics.py`, `dynamics.json`, kept out of `project.FILES` to avoid a circular
  import): six tracks per chapter (valence −5…5; tension, suspense, capability, status, family_conflict 0–10) and
  milestones. `plan(ctx)` is a writer call over the outline (task `dynamics.plan`, kind `dynamics` in `HEAVY_KINDS`);
  `estimate(ctx, chapter)` rates a written chapter from its lines (an estimate, labelled so); `review` applies the
  genre's shape rules (flat_tension, tension_plateau, status_ahead_of_capability, no_recognition, constant_victory,
  constant_suffering, deviations ≥ 3) and `graph(outline, scenes)` lays out the branching (nodes by chapter, edges
  choice|branch|next with what the written options set, decisions, convergence, endings and gates). App: `GET
  /api/stories/{id}/dynamics`, run actions `dynamics_plan` / `dynamics_estimate` (chapter); the story page's
  Dynamics tab (`dynamicsTab` in `views/story.js`: SVG line chart planned solid / estimated dashed, findings,
  milestones, the branching map with hover details). Tests: `tests/test_story_dynamics.py`.
- **Narrative clarity** (`story/clarity.py`; owner's brief 2026-11-11: chapters were hard to follow although the plan
  was sound). Three gates, because a coherent outline ≠ a readable script ≠ a playable experience: `reader_view(scene)`
  is the chapter exactly as the player meets it (no ids, conditions, expressions; route-only lines marked), and every
  audit reads only that; `terms_plan(ctx)` is the knowledge model (`clarity.json`: each fictional term, first chapter
  needed, how it is introduced, what stays concealed) and `knowledge_block()` hands the writer the slice for the chapter
  (met / introduce now / not yet); `audit(ctx, scene)` is the cold read (task `clarity.<scene>`, Sonnet when Claude is
  signed in): ten first-reader answers per section, terms without context, dialogue continuity exchange by exchange,
  motivation, choice stakes, continuity, emotion, intentional vs unintended mystery, verdict ok|revise, stored in
  `clarity/<scene>.json`; `revise()` rewrites from `revision_note(report)` (Opus when signed in); `leaks()` is Stage H
  (ids/conditions/markup in player text). `steps.write` → `_clarity_gate`: audit after the linter, one revision on
  `revise`, kept only if it lints and reads no worse. Writer standards in `prompts.py` (PROMPT_VERSION 5): boxes 1–3
  sentences up to ~45 words, choices 200–300 words apart with stakes shown first, `CLARITY_RULES` (terms in the
  moment, every line answers the last, plain speech, cause and effect, emotion on the page, transitions), perspective
  fixed as second person present with labelled speech (`clarity.PERSPECTIVE`). Tests: `tests/test_story_clarity.py`.
- **Character acting** (`story/acting.py`, `acting.json`; owner's brief 2026-11-11: characters act, no emotion matrix). Per
  character: an `ActingProfile` drafted from the Studio profile (`profile`, task `acting.profile.<cid>`, kind `acting` in
  `HEAVY_KINDS`; proposed until approved); `analyze` reads the written chapters with the character's current sprite marked
  (`acting_view`) plus existing sprites and their scene usage, gives a verdict per sprite and proposes `Performance`s (scene,
  line range, moment, feeling, intent, counterpart, nine-part `Direction`, need reuse|face_only|new_pose, justification);
  `decide` approve|modify|reuse|dismiss, where approve retargets the lines to the performance name and registers it as an
  expression; `directions()` feeds `assets.manifest(acting=…)` so the manifest asks for exactly the sprites scenes use with
  the acting brief (reuse directions brief unmade sprites too); `generate(pid)` makes one approved gap from the master.
  Routes `GET /api/stories/{id}/acting[?character=]`, `POST …/acting/{cid}/profile`, `POST …/acting/performances/{pid}`;
  run actions `acting_profile`, `acting_analyze`, `acting_generate`; Studio panel "Acting and performances" in
  `views/characters.js`. Doc `docs/narrative/24-character-acting.md`; tests `tests/test_story_acting.py`.
- Image prompts: `NO_TEXT` forbids lettering in any script and painted swatches; `art.render` names palette colours in words
  (`_palette_words`), never hex (models paint hex lists as swatch strips); `transparency.remove_background` also removes
  enclosed key-coloured pools ≥ `HOLE_MIN_PIXELS` with mean distance < `HOLE_MAX_MEAN` (arm gaps, keyed panels).
- **Choices, promises, rules** (`story/stakes.py`; research handoff PRD 2026-11-11, audit in `docs/narrative/25-prd-audit.md`):
  `choice_report(script, unwritten_after=)` classes every choice A expression | B state | C macro and finds `same_delta`,
  `no_acknowledgement` (a stat written and never read), `only_at_ending`, `late_acknowledgement` (> `ACK_HORIZON` chapters),
  `all_expression`, `unverified` (partial script); it rides in `lint_story()["choices"]`. Payoff ledger `payoffs.json`:
  `draft_ledger` (task `payoffs.ledger`) from the outline, `advance_ledger` from each cold read's `tensions_changed` /
  `opened` (new `ClarityReport` fields; a chapter that changed nothing is `no_payoff`). Rule registry `rules.json`:
  `draft_rules` (task `rules.registry`) with actual rule / institutions believe / protagonist believes / exceptions /
  must_understand_by / concealed_until; `knowledge_state()` gives each term's reader state per chapter (unknown → revealed)
  and `violations` when a chapter depends on a term its cold read found no context for. Route `GET /api/stories/{id}/stakes`;
  run actions `payoffs_draft`, `rules_draft`. Tests `tests/test_story_stakes.py`.
- **Whole-story checks** (2026-11-12, doc 29; each chapter is written and cold-read alone, so these see what no chapter gate can):
  `stakes.play_report(script)` plays the script 3,000 times with random choices (state, clamps, gates as the player applies
  them): `unreachable_ending`, `dead_end`, `loop` (errors), `unvisited_scene`, `rare_ending`, `saturated_stat`;
  `stakes.continuity_report` finds an off-cast person given different names per chapter (`name_drift`); `choice_report`
  adds `absolute_set` (a stat overwritten instead of adjusted) and exempts ending chapters from `all_expression`; lint
  `hidden_cg` (an illustration followed by `show`/`hide`/`place` before any line is cleared unseen). All ride in
  `lint_story()` (`play`, `continuity`, `choices`).
- **Art follows the story world** (doc 28): `story/visual_canon.py` (`visual_canon.json`, drafted from the written script:
  `world`, `outside`, `always`/`never`, per-location description + `geometry` + `never`, `props`; task `visual.canon`)
  feeds `assets.manifest(canon=)`: backgrounds take the place with its camera and layout, illustrations take it as
  materials only (`location_text(illustration=True)`) and name only the people their own text names (`assets._cg_cast`),
  the title shows the protagonist alone. `story/visual_check.py`: per image a blind survey then a judgement against brief
  and canon (codex-cli or claude-cli with images); violations are `hard` (world, people, layout, time) or `soft` (a
  detail); `ok` = no hard violation, no logic fault, no readable text; `fix` remakes with the hard faults as feedback.
  CLI `story art-check`, run actions `visual_canon`, `art_check`, `art_fix`. `acting.directions`: a sprite reused across
  scenes is a general one (the master's stance, face only, no props); only approved poses are moment-specific.
- **Portrait screen** (owner 2026-11-12: the stories are read in the mobile app, on a phone held upright, so every picture
  is made for portrait). `StoryConfig.screen` portrait | landscape; `steps.init` makes new stories portrait; unset (stories
  from before) = landscape with prompts byte for byte as they were. Portrait: backgrounds, time-of-day versions,
  illustrations and the title are `9:16` with composition wording for a tall frame (`assets.PORTRAIT_BG/CG/TITLE`: features
  in the upper two thirds and central band, lower third open for characters and the dialogue box), the dialogue box
  `16:9`; sprites were already `2:3`. `image/prompts.framing()` gives tall backgrounds a tall framing sentence
  (`PROMPT_VERSION` 4); `visual_canon.PORTRAIT_FRAME` makes the canon choose cameras for a tall frame (long things run away
  from the viewer, not side to side). `assets.screen_report` lists full-screen pictures made in the other shape (story
  detail `screen`, the Settings tab's Screen card, CLI `story screen [portrait|landscape]`, `story init --screen`);
  remaking them is `story assets --only bg --only cg --only ui --redo` (paid, so only on the owner's word). The player
  gets `STORY.screen` and has a `body.portrait` layout (9:16 stage, sizes as shares of the narrow width). Release image
  variants already carry `layout` from the real pixels. Tests `tests/test_story_screen.py`.
- **Each story its own people** (owner 2026-11-13: the second manhwa story's cast came out as the first's). A built-in art
  preset's body/face/palette/glow lines ("8 heads, sharp jawlines", a fixed palette, "glowing runes", "wide vistas") are
  replaced per story in `assets.story_art_direction` (`OWN_BODIES`, `OWN_LIGHT`, `OWN_TEXTURE`, portrait-aware composition,
  `world_palette` from the world's colour words); sprite briefs say the body and face are the character's own. Profiles
  are stored once per project, so older stories keep theirs. `visual_canon.world_block(canon, mention=text)` lists only the
  props the picture's own text names (the Gauge had been painted into every room); `bg_time` may relight and change sky
  already in the reference, never add a window. Music `Key` resolves scale aliases (`aeolian`).
- **Story sound**: `audio_direction.apply` lets a planned one-shot cue adopt the writer's own sound line beside it (one
  each, within two nodes) so a sound never plays twice; recipe `steam_whistle` (presets one|two|three = blasts);
  `request.infer_preset` reads a recipe's own presets from the description first; `request.fingerprint` includes the
  resolved recipe, its version and preset for sounds, so a better recipe for the same words is rendered.
- **Editorial evaluation** (`story/evaluate.py`, `evaluation.json`): the owner's fifteen questions (`QUESTIONS`) answered
  yes|partly|no with scene evidence and a targeted change per miss; task `evaluation` (kind in `HEAVY_KINDS`); run it on a
  different model from the writer (the Improve tab's card defaults to claude-cli opus). `summary()` counts and lists the
  changes; `as_feedback()` turns them into an `improve` note ("Apply its changes with Improve"). Routes: `GET
  /api/stories/{id}/evaluation`, run action `evaluate` (scenes, fresh, provider/model). Tests: `tests/test_story_evaluate.py`.
- **Visual canon + visual logic check** (owner 2026-11-12: a "gap between two carriages" came back as two trains side by
  side with a castle city on a dead dust continent; doc `docs/narrative/28-visual-logic.md`). `story/visual_canon.py`
  (`visual_canon.json`: world, outside, always, never, per-location description/geometry/never, props) goes into every
  bg/bg_time/cg/title prompt via `assets.manifest(canon=…)`. `story/visual_check.py` judges finished images with a
  vision model on the subscription CLIs: `check` = a **blind survey** first (task `visual.survey.<key>`, image only: a
  judge that has read the brief sees the brief; Opus did) then the judgement (task `visual.check.<key>`, kind `visual`:
  brief + location canon + world canon + survey as evidence; `seen`/`counts` before `violations`/`logic`/
  `text_in_image`/`matches_brief`; `ok` only when yes and nothing else). `review` stores verdicts by key in
  `<story>/visual_check.json` (stale when the key's asset id changed); `fix` (the only paid step) archives, re-requests
  through `request.handle` with every fault so far appended, re-checks, keeps the best of `max_rounds`, and remakes the
  `bg_time`s of a remade `bg`. Image input: `TextCall.images` (sha256 in the fingerprint only when present),
  `supports_images` on codex-cli (`--image`) and claude-cli (`--tools Read --allowedTools "Read(<staged file>)"`),
  not gemini/APIs (skipped for image calls). CLI `story art-check [STORY] [--groups] [--fix] [--limit]`; app `GET
  /api/stories/{id}/visual-check`, run actions `visual_canon`, `art_check`, `art_fix`, marks in the Assets tab.
  Tests `tests/test_story_visual_check.py`.
- Power Fantasy pilot *Everyone Owes Me* (earlier titles Null, The Weight He Carries; slug unchanged): concept `docs/narrative/22-power-fantasy-concept.md` (Opus review applied), research `20`,
  preset `story/direction_presets/weight-power-fantasy.yaml`, story `~/.assetforge/stories/the-weight-he-carries`
  (guided mode). Cast and outline approved 2026-11-11; chapters 1-6 written (ChatGPT) through the clarity gate; chapters 3 (shared route) and 5 were re-planned as three movements introducing one new thing each (doc 23 §5-6) and pass. Chapter 1's art is made; Eli's first performances exist (doc 24 addendum). Next: chapters 7+ only after the owner's read; the second awakening is chapter 7.
- Second story *Nobody Rides the Tail* (2026-11-12; preset `story/direction_presets/line-power-fantasy.yaml`, story
  `~/.assetforge/stories/nobody-rides-the-tail`, ChatGPT): a two-mile train that never stops, tail cars cut loose on the grades; coupler Rafe
  Oduya, 23, keeps whatever he carries aboard before the whistle. Complete: 13 scenes, 3 endings, 26 backgrounds/illustrations/UI, 40 sprites,
  audio; reviewed adversarially (doc 29). The owner rejected the first idea (a tide city, a 19-year-old porter) for weak emotion and
  setting and reviews generated art closely against story logic. R2 publishing only on the owner's word or when a story is complete;
  development uses the shared stage bucket under the `vn` prefix.

### Audio Analysis Engine, diagnosis, corrections, Workbench (Computational Audio Intelligence)

- Research: `docs/narrative/17-audio-analysis-tooling.md` (wheels, licences, install tiers; verified against PyPI/HF),
  `18-audio-science-notes.md` (DSP/MIR/psychoacoustics notes, from memory, tagged), `19-audio-intelligence.md`
  (the audit, architecture, corpus results, limits), `reviews/opus-review-audio-analysis.md`.
- `audio/analysis/` (NumPy/SciPy; `onnxruntime` is the optional `analysis` extra): `analyze_file(path, kind)` →
  `technical` (`signal.py`: true peak 4×, momentary/short-term/integrated LUFS + LRA, clipping runs, DC, silence map,
  clicks, stereo balance/width/correlation, loop seam), `spectral` (`spectral.py`: one 2048/512 STFT → mel 64, centroid/
  bandwidth/rolloff/flatness/flux timelines, HPSS percussive share, octave balance + tilt, named bands rumble/mud/
  dialogue(1–4k)/harsh/hiss), `music` (`music.py`: salient events via `quality.events`, tempo with confidence or
  "not meaningful", chroma/key/tonal stability, harmonic changes/min, Foote self-similarity sections + repetition index,
  energy arc, density), `speech` (`speech.py`: four per-second cues — syllabic modulation, harmonicity, LPC formants,
  pause structure — plus a crowd rule; `silero.py` ONNX VAD corroborates when installed (`audio models install
  silero-vad --yes`, sha pinned, MIT); verdict confirmed/suspected/unlikely with its basis), `events` (`events.py`:
  heuristic acoustic signatures rain_like/wind/drips/footsteps/metal/hum/music/speech_like with confidence; `taggers.py`
  is an adapter slot for a verified ONNX tagger). `to_json` drops the arrays; `visuals.py` renders waveform/spectrogram/
  mel/chroma/self-similarity PNGs + `timelines.json`.
- `diagnose.py`: findings `{code, severity hard|problem|note, confidence measured|estimated|listen, cause source|
  synthesis|composition|sound_design|mix|emotion|method, evidence, where, corrections[{action, kind dsp|layer|
  composition|regenerate|method, params, why}]}`. Thresholds were calibrated on `.dev/audio-lab` (71 files: owner-
  approved assets, rejected pieces, the benchmark, constructed cases); `run.py` reports hits of `expect` labels (16/17).
  A brief decides whether voices belong (`_brief_allows_voices`, negation-aware); with no brief, intent is unknown and
  such findings are notes. `interpret.py` writes the plain-language reading; nothing in it claims to have listened.
- `corrections.py`: `plan_corrections` (one correction per finding, DSP first when `prefer="precise"`), `fill_donors`
  (a voice-like second is patched from the cleanest second ≥ 2 s away), `correct_asset` (the DSP chain as a
  `process_audio_asset` variant with lineage), `compare` (fixed / remaining / regressions). New ops in `audio/process.py`:
  highpass, lowpass, peak_eq, shelf, dialogue_dip, dc_remove, declick, reloop, replace_segment, narrow, balance,
  compress, expand. `production.produce_for_brief` now runs the deep diagnosis on every candidate (a confirmed voice in
  a voiceless brief is a hard failure) and `_correct_in_place` fixes the best candidate's DSP-fixable problems before
  any regeneration, keeping the corrected variant only when `compare` says improved; `REFINEMENTS["voice_like"]`
  drops the `crowd_murmur` bed on the next round.
- `reference.py`: measured profiles (mean/sd per feature with sd floors) stored under `audio_style/<project>.json`
  `profiles[kind]`; `place()` gives z-scores and words ("brighter than the identity"); `record_preference` appends
  approve/reject rows (brief, findings, feedback, features); `rank()` = usable → distance → nearest-approved heuristic.
- CLI (`cli_analysis.py`): `audio inspect|diagnose|correct|profile`, `audio models status|install`. App: `GET
  /api/audio/analysis/{ref}`, `/media/analysis/{var}/{png}`, `POST /api/audio/correct` (plan or explicit ops → variant +
  comparison), `POST /api/audio/preference`, `GET/POST /api/audio/profile/{project}`; view `views/audio_lab.js` at
  `#/lab/<ref>` (the Workbench: findings with Apply buttons, human-friendly controls mapped to real ops or a Studio hand-
  off for recompositions, waveform/loudness/spectrum/chroma/self-similarity/voice/event panels, identity placement,
  approve/reject with feedback). Studio candidates link to it ("Inspect").
- Corpus and harness: `.dev/audio-lab/corpus.py` builds `corpus/` + `corpus.json` (labels, `expect`, `loop`);
  `run.py` → `results.json`. Known miss: a murmur 10 dB under wind. Known limits: the event signatures are patterns, not
  recognition; "repetitive" and "mechanical" are estimates; nothing measures whether a melody is good.
- Demonstration (listen to it): `.dev/audio-intel-demo/make.py [fix|produce|page|<brief id>]` builds `demo.json` +
  `index.html` (launch config `audio-intel-demo`, :8774). Part A runs `correct_file` on rejected story assets, a benchmark
  render and constructed cases (before/after audio, spectrograms, findings, the comparison verdict, or "declined, because…");
  Part B runs `produce_for_brief` in its own library (`lib/`, SoundFont + Silero linked) for four briefs, one of them a
  constructed direction with the `crowd_murmur` bed under a "no people" brief. Rules the demo forced: the brief decides
  whether voices belong (never the direction's bed names); `replace_segment` is declined when voice-like seconds cover more
  than `MAX_PATCH_SHARE` (a layer, not a patch job) and donors must score ≤ `CLEAN_DONOR_SCORE`; a finding whose only option
  is `regenerate` condemns the file, so no DSP is run on it; a seam level step gets a 500 ms crossfade (a click 120 ms);
  expected sounds match whole words; `production._deep` measures the WAV master, not the OGG copy.

### Sharing (GitHub)

- Two private repos: this code repo and a library repo made by `assetforge library snapshot DEST`
  (`core/snapshot.py`, `cli_library.py`): SQLite backup API copy of the catalog + referenced objects, no jobs/previews/
  logs/backups/soundfonts/creatures. WAVs: re-renderable masters go through `masters.prune`; every other WAV is
  marked `extra.withheld` (verify/QC skip it, `ensure_file` raises) and its OGG/M4A child becomes primary.
  Pruned/withheld marks carry over between runs. Refuses if the catalog contains a key-shaped string.
- `core/merge.py` (`library merge SRC`): brings another library in. The source is read from a backup-API copy
  (migrated if older). Every id is planned before any row is written, so ids inside JSON (metadata, params, job
  request/result, profile references) are rewritten through one map. An asset whose primary file is already here is
  linked; a key taken by a different asset stays here and the incoming one gets `metadata.merged_key`. A job with the
  same kind, fingerprint and start time is linked, so costs aren't counted twice. Queued/running jobs come in as
  `cancelled`. Story folders are copied into `<library>/stories/` and registered. The ledger is in `<library>/merges/`.
- Secrets: `.env` is ignored; `scripts/check_secrets.sh` runs from `.githooks/pre-commit`
  (`git config core.hooksPath .githooks`) and `.github/workflows/secrets.yml`. Never commit `.env`.

### Devices: shared spend and updates from main

- Several Macs share one set of provider keys, each with its own library. `core/devices.py`: `device.json` (stable id +
  hostname, `ASSETFORGE_DEVICE_NAME` overrides) and the spend ledger, an orphan branch `spend-ledger` of
  `sync.remote` (empty = this checkout's origin) cloned at `$ASSETFORGE_HOME/sync/ledger`; each device writes only
  `devices/<id>.json` (daily spend by provider, 62 days; running calls count their estimate), pushes when the numbers
  change or every 24 h, and on a push race refetches and rewrites its own file. `budget.spent/summary/reserve` add the
  others' last synced spend while `budget.include_other_devices` is on (default); reads never touch the network.
- `updater.py`: devices follow `origin/main` only, fast-forward only. A checkout that is dirty or has commits GitHub
  lacks is never touched (the one you develop on); a clean side branch is switched back to main. Pulls wait until no
  generation job / app task is running and, when AssetForge.app is open, `update.idle_minutes` without input
  (`HIDIdleTime`); then `uv sync` if the lock changed, `app install` if `macos/` changed, and the app is relaunched
  with `open -g`. `publish` pushes main and refuses when another local branch has commits main lacks;
  `.githooks/pre-push` refuses pushes to anything but main (`ASSETFORGE_ALLOW_BRANCH_PUSH=1` overrides).
- CLI `sync status|spend|update [--now]|publish|tick|install|uninstall`. `sync install` writes the launch agent
  `~/Library/LaunchAgents/com.ideaclan.assetforge.sync.plist` (`sync tick` every 10 min: spend when
  `sync.interval_minutes` (60) passed, then the update; log `$ASSETFORGE_HOME/sync/agent.log`, state `sync/state.json`).
  Git runs non-interactive (`BatchMode` SSH), so each device needs working git credentials. Tests `tests/test_sync.py`.

### Content contract v2 + R2 publishing

- `story/contract.py` `compile_release(lib, project, out_dir=None, *, allow_missing, derive)`: script.json (else the
  written chapters + plan stand-ins, which block) → `vn.release` / per chapter `vn.chapter.bundle`, `vn.chapter` graph,
  `vn.presentation`, `vn.assets`, `vn.locale`, `vn.downloads` (+ release locale, `vn.migration`), canonical JSON,
  descriptors `{key, mediaType, bytes, sha256}`, blobs = library files copied by digest. Written to
  `<story>/releases/<rid>/` (bucket layout minus `stories/<id>/releases/`; `blobs/` shared) with `index.json`; staged and
  moved whole; never modified. `--allow-missing` → `releases/draft/` (`"draft": true`, never publishable).
  `derive=False` adds no library variants (web formats are otherwise derived as `export` does).
- Mapping: declared chapter = one graph (else scene = chapter); say/narrate → dialogue/narration, set → action, branch →
  condition, cross-chapter goto → chapterEnd, `when` → condition `<id>.w`, in-chapter choice → reaction chains merging
  after it, all-conditional choice → `.gate` + `.open`; staging folded into presentation (`ChapterBuilder`, `Stage`
  mirrors the player; gated staging splits paths `.p<k>`, ≤ 8). Ids/text keys are positional
  (`<scene>.n<i>`, `<chapter>.<scene>.n<i>.o<j>.r<k>`), never from text. Multi-entry chapters get `route.<chapter>`.
- `graphId` `<story>.graph.<n>` bumps only when `gameplay_hash` (ids, types, targets, conditions, effects, entries, state
  schema) changes; same content hash as the latest → "identical, no new release". `migration()` aliases vanished node
  ids (same text, else nearest earlier presenting node, else chapter entry) + default/rename state ops.
- `validate_release(dir)`: models (`story/contract_models.py`, `check_key` refuses traversal/absolute/query/encoded/
  unknown media), digests, every reference, typed state, condition depth 6, automatic cycles / 64 steps, no-way-out
  nodes, locked choices without their gate, download closure over every reachable branch.
- `story/catalog_sign.py`: Ed25519 envelope (`PROTOCOL_PREFIX` + exact payload bytes), key at
  `$ASSETFORGE_HOME/keys/vn-catalog.ed25519` (0600, made on first use, `.pub` next to it), `next_catalog` bumps
  `catalogSequence`; a rollback is a new sequence pointing at an older release.
- `publish/r2.py`: `resolve_config` (`ASSETFORGE_R2_BUCKET`, `ASSETFORGE_R2_PREFIX`, `ASSETFORGE_R2_ENV_FILE` read in
  place for `R2_ACCESS_KEY/R2_SECRET_KEY/R2_ACCOUNT_ID`, direct overrides, `ASSETFORGE_R2_PUBLIC_BASE`), boto3 lazily
  (extra `publish`, built only in `make_client`), `preflight` (missing vs denied), `Store` (see below); `plan`/`publish`
  are aliases of `publish/backend.py`'s: blobs + documents (skip same-size blobs; differing documents refuse
  everything) → HEAD all → release.json → index history (create-only) then `catalog/staging/current.json` last;
  receipts `releases/publish.json`. Errors `PUBLISH_*`. Nothing calls publish automatically; tests use a fake client.
- CLI `story contract|releases|publish [STORY] [--yes]` (STORY = registered id or folder); app `GET
  /api/stories/{id}/releases`, run actions `contract` and `publish` (plan unless `confirm: true` and publishable), the
  Settings tab's "Release (content contract v2)" card (`releaseCard`). Doc `docs/narrative/26-content-contract-v2.md`.

### Content backend (channels, index, deltas, reference client)

- The bucket is the database, AssetForge its only writer, the app a read-only client (doc 27, "As built" lists the
  deviations). **Buckets:** development = the shared `creative-library-v2-stage` + `ASSETFORGE_R2_PREFIX=vn`;
  production = a dedicated bucket and credentials later (configuration only). `r2.Store` applies the prefix to every
  call (`full`/`relative`), lists only `<prefix>/`, and has `create` (`IfNoneMatch="*"`, else HEAD then PUT).
- Layout: `catalog/<channel>/current.json` (signed `vn.catalog`, no-cache) + `catalog/<channel>/history/<seq>.json`
  (create-only); channels `staging` (publish) and `production` (only `promote`). Index payload: `channel`,
  `generatedAt`, per story `status` live|unlisted, `locales`, `addedSequence`/`updatedSequence`/`updatedAt`,
  `chapterCount`, `downloadBytes` {core, standard}, releases with `publishedAt` and `delta`; `changes` (≤ 50, newest
  first). `catalog_sign.next_catalog(previous, entry, channel=, change=)` moves only the touched entry's sequence.
- `story/delta.py`: `read_tree(get, story, rid)` (any getter: local folder, documents about to be written, bucket),
  `delta(old, new)` → `vn.delta` (content = graph/presentation/assets/locales; bundle/downloads are per-release
  structure; a new graph id changes every graph), `download_bytes`, `summary`. `compile_release` writes
  `delta-from-<previous>.json` into each release after the first; `release_objects` lists it; `validate_release`
  recomputes it when the older release is present.
- `publish/backend.py`: `read_index` (verifies, follows history past a stale `current.json`), `commit_index` (history
  create-only then current; the loser gets `ConflictError`), `plan`, `publish`, `promote`, `rollback`, `set_listed`,
  `verify(deep, public)`, `repair`, `inventory`/`ls`, `gc` (orphans under `blobs/`/`stories/` only; refuses an empty
  prefix without `whole_bucket` and any unreadable index), `status(live=, save=)`, `pull`, `client_kit`. Writes need
  `yes` (else the plan with `written: false`). `$ASSETFORGE_HOME/backend/log.jsonl` + `state.json`.
- `publish/client.py`: reference client (stdlib + `cryptography`; `fetch(key, etag)`; digest-keyed cache;
  `plan_from_delta` == `plan_from_manifests`, tested). Shipped by `backend client-kit` with schemas, samples, the key
  and `INTEGRATION.md`.
- CLI `backend status|plan|publish|promote|rollback|unlist|relist|verify|repair|ls|gc|pull|client-kit`
  (`cli_backend.py`; `story publish` is an alias). App `/api/backend/status[?cached=1]|plan|publish|promote|rollback|
  unlist|relist|verify|repair` (`app/api_backend.py`; writes only with `confirm: true`), `views/backend.js` at
  `#/backend` (sidebar "Publishing"). Tests `tests/test_backend.py` with the shared fake `tests/s3fake.py`.

Schema v8 (2026-11-13): `project_usage` has `usage_key` in its primary key (`''` = no key, read back as None), so one
asset can serve several keys in one project (a story reused one sound for two cues and lost one); the identical-request
rung of `request.handle` binds the request's key again. Publishing ends with the quick `backend verify`; `--deep` only
when the bucket, CDN, origin or credentials change, a client reports a bad file, or the owner asks.

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

All six phases of the v1 plan are done, plus discovery/art-direction/sprites, synthesized game audio (above) and the Mac app.
Possible next steps (not started): semantic/vector search, speech/voice, in-app export.

Generators should call `Library.register_file(path, NewAsset(origin=..., status="candidate", metadata={recipe/prompt}), params=...)`
and use `add_variant` / `add_lineage` for derivatives, so search, dedupe and provenance work automatically.
Out of scope for v1: game engine, visual editor, cloud backend, vector search, AI (model-generated) music, web UI.
