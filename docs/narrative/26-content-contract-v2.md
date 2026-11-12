# 26. Content contract v2.0 and R2 publishing

The owner's "Power Fantasy Content Contract v2.0" turns a finished story into an immutable, verifiable release for a
consumer app. AssetForge compiles it (`story/contract.py`) and, only when explicitly asked, publishes it to Cloudflare
R2 (`publish/r2.py`). The demo export (`exporters/vn.py`, `vn_player/`) is unchanged; v2 is an extra output.

## Documents and layout

Every document is `{"kind": "vn.<x>", "schemaVersion": "2.0", …}`, canonical JSON (sorted keys, no whitespace, UTF-8).

| Object key | Kind | Holds |
|---|---|---|
| `catalog/<channel>/current.json` | signed envelope of `vn.catalog` | channel, stories (status, current release, all listed releases, thumbnail, sequences, sizes), change feed; `catalogSequence` (doc 27) |
| `catalog/<channel>/history/<seq>.json` | same | every index ever published on that channel (create-only) |
| `stories/<story>/releases/<rid>/release.json` | `vn.release` | graphId, requires, state schema, chapters → bundle descriptors, endings, release locale, migrations, cover |
| `…/<rid>/locales/<lang>.json` | `vn.locale` | story title/summary, chapter titles, ending titles/summaries, cover alt text |
| `…/<rid>/migration-from-<old>.json` | `vn.migration` | nodeAliases, stateOperations (rename/default) |
| `…/<rid>/delta-from-<old>.json` | `vn.delta` | what changed since the previous release (doc 27); not named by release.json, the index carries its descriptor |
| `…/<rid>/<chapter>/bundle.json` | `vn.chapter.bundle` | descriptors of the five chapter documents |
| `…/<chapter>/graph.json` | `vn.chapter` | entry, speakers (`nameKey`), nodes |
| `…/<chapter>/presentation.json` | `vn.presentation` | complete scene states + cues |
| `…/<chapter>/assets.json` | `vn.assets` | logical id → variants with file descriptors, altKey |
| `…/<chapter>/locales/<lang>.json` | `vn.locale` | every text key the chapter uses (lines, options, lock reasons, names, alt texts, titles) |
| `…/<chapter>/downloads.json` | `vn.downloads` | startupNodes; profiles core / standard / enhanced |
| `blobs/sha256/<digest>.<ext>` | media | the library's own stored file, copied by digest (never re-encoded) |

Locally the same layout sits under `<story>/releases/` (`stories/<story>/releases/` stripped; `blobs/` shared by all
releases) with `index.json` (releases, graph ids, gameplay/content hashes, validation and published state, last report)
and `publish.json` (receipts). Additive fields beyond the brief, because a consumer needs them: release `titleKey`,
`summaryKey`, `endings`, `locales`, `migrations`, `cover`, `draft`; graph `speakers`; scene `audio.loops`;
asset `loop` and audio `quality`; migration `fromGraphId`/`toGraphId`.

## Mapping from the AssetForge script

| Script | Contract |
|---|---|
| declared chapter (several scenes) / no chapters | one chapter graph with its scenes linked / one scene per chapter |
| `narrate`, `say` | `narration`, `dialogue` (speakerId = character id) |
| `set` | `action` (effects) |
| exit `choice` (all options `goto`) | `choice`; `set` → option effects; `require` → `enabledIf` + `disabledReasonKey` ("Requires trust ira at least 3", or "Requires: <flag meaning>") |
| in-chapter `choice` | option target = its reaction lines (`<node>.o<j>.r<k>`), all merging into the node after the choice |
| every option conditional | gate `<node>.gate`: `any(enabledIf…)` → the choice, fallback → `<node>.open` (same options, unlocked), as the demo player does |
| `when` on any node | condition `<node>.w`: case → the node, fallback → the next one |
| `branch` | `condition`, cases in order, fallback = `goto` |
| `jump` / exit into another chapter | `chapterEnd {nextChapter}`; inside the chapter a direct link |
| `ending` | `ending {endingId}` |
| `show/hide/place/music/sfx/cg/ambience` | presentation only: `presentationRef` = a cue named after the node → complete scene state; one-shot sfx = `onNodeEnter` events; looping sfx sit in `audio.loops` until `stop` |
| `cg` | an overlay (`kind: cg`, layout `vn.cg`); `show`/`hide`/`place` clear it, as in the player |
| variable kind flag / relationship / other | `flags.<n>` bool (reject) / `relationships.<n>` int / `stats.<n>` int; min/max → clamp |

Staging follows the player exactly (speaker auto-shown, free slot centre → left → right, place clears the stage,
ambience = node override or the location's loop). When a gated node changes the stage, the paths are kept apart (node
copies `<id>.p<k>`) until their stages match again (at most 8). After an in-chapter choice the chapter continues from
the stage before the choice. Flags: `set n` → `n != 0`; `add +n` → true, `add -n` → false; comparisons are reduced to
`eq true/false` (or always/never). A chapter entered at more than one scene gets an int `route.<chapter>`: the
chapterEnd is preceded by an action setting it, and the chapter's entry is a condition on it.

## Ids, keys, graph ids

- Node ids: `<scene>.n<i>` plus fixed suffixes (`.w`, `.p<k>`, `.o<j>.r<k>`, `.gate`, `.open`, `.c<k>.end`, `.route`);
  text keys: `<chapter>.<scene>.n<i>[.o<j>[.r<k>|.locked]]`, `cast.<id>.name`, `chapter.<id>.title`,
  `ending.<id>.title|summary`, `alt.<logical id>`. All come from ids and positions, never from text, so editing a
  line keeps every key. Scene state keys `s<n>` number states by first appearance.
- Logical asset ids are the manifest key after `vn/<slug>/` with `/` → `.` (`char.ira.smile`, `bg.beach.evening`,
  `music.calm`, `amb.repair_shop`, `sfx.door`, `cg.kiss`, `ui.title`).
- `graphId` = `<story>.graph.<n>`; `n` goes up when the gameplay hash differs from the previous release's. The hash
  covers node ids, types, targets, cases, effects, enabledIf, nextChapter, endingId, entries and the state schema;
  text keys, speakers, presentation refs and assets do not count. A text or art change keeps the graph id; adding or
  removing a node changes it (ids are positional).
- A recompile whose documents equal the latest release's (content hash with the release id masked) reports
  "identical to rNNNN, no new release". Release ids `r0001…` are never reused (the highest folder or index entry + 1).
- Every new release after the first gets `migration-from-<previous>.json`: aliases for every old node id that no longer
  exists (same chapter, type and text if unique; else the nearest earlier presenting node of the same scene; else the
  chapter entry) and `default`/`rename` state operations. All targets are validated to exist.

## What blocks a release

Any error finding: a scene not written yet (stand-in from the plan), a script validation error, a manifest asset with
no usable library file (`missing_asset`), a missing delivery format (no OGG for a sound), an asset the manifest does
not know, or a validation error of the written documents (schema, descriptor digest/size/media type/key, targets,
cues, scenes, asset types, text keys, speakers, chapters, endings, typed conditions/effects, condition depth ≤ 6,
automatic-only cycles, > 64 automatic steps, nodes that can reach no end, ≤ 6,000 nodes per chapter, documents ≤ 4 MB,
an all-conditional choice reachable without its gate, a download plan missing assets of reachable nodes).
`--allow-missing` writes `releases/draft/` instead: replaced on each compile, marked `"draft": true`, never published.
`--no-derive` never adds variants to the library (web formats are otherwise derived like `assetforge export` does:
WebP for images with the lossless PNG source as the `high` variant, OGG + M4A for sound).

## Publication sequence (`story publish [--release rN] [--yes]`)

Without `--yes` (or, in the app, without `confirm: true`): the plan only (HEAD/GET reads, no writes). With it:

1. refuse a draft, a release that does not validate, or no explicit yes;
2. `head_bucket` (missing bucket and denied access are different errors), load or create the signing key, read and
   verify the current catalog with our public key (a catalog signed by another key is refused);
3. HEAD every object: blobs already there with the same size are skipped; any document already there must be
   byte-identical, otherwise the whole publish is refused before writing (published objects are never overwritten);
4. upload blobs, then chapter documents, release locale and migration (`Cache-Control: immutable`, `x-amz-meta-sha256`);
5. HEAD every object again (the release must be complete), then upload `release.json`;
6. index last: write `catalog/staging/history/<seq+1>.json` create-only (two writers cannot both take a sequence),
   then `catalog/staging/current.json` (`no-cache`). Production is reached only by `backend promote` (doc 27). Receipts go to `releases/publish.json`; `index.json` marks the release published.

Publishing the release already current changes nothing. Publishing an older release again is a rollback: nothing is uploaded and the catalog gets a new sequence pointing at it.
Nothing expires. Nothing calls `publish` automatically (not write, export, assets, compile or the app's compile button).

Catalog envelope: `{alg: "Ed25519", keyId (sha256 of the raw public key, 16 hex), payload: base64(exact JSON bytes),
signature: base64(sign("assetforge.vn.catalog.v2\n" + payload bytes))}`. Private key:
`$ASSETFORGE_HOME/keys/vn-catalog.ed25519` (PKCS#8 PEM, mode 0600, made on first publish); public key next to it
(`.pub`, base64 raw) is all the consumer app ships.

## R2 configuration

(Every key sits under `ASSETFORGE_R2_PREFIX` when it is set; keys inside documents stay relative. See doc 27 for
channels, the index fields, deltas and the `assetforge backend …` operations.)

| Setting | Meaning |
|---|---|
| `ASSETFORGE_R2_BUCKET` | the bucket (required) |
| `ASSETFORGE_R2_ENV_FILE` | path to another repo's `.env`; only `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ACCOUNT_ID` are read, in place, never copied |
| `ASSETFORGE_R2_ACCOUNT_ID`, `ASSETFORGE_R2_ACCESS_KEY`, `ASSETFORGE_R2_SECRET_KEY` | direct overrides (win over the file) |
| `ASSETFORGE_R2_PREFIX` | key prefix inside the bucket (`vn` in development) |
| `ASSETFORGE_R2_PUBLIC_BASE` | the public origin: the app's index URL and `backend verify --public` (descriptors carry keys, never URLs) |

Endpoint `https://<account>.r2.cloudflarestorage.com`, region `auto`, boto3 from the optional extra
(`uv sync --extra publish`). **Buckets (owner, 2026-11-12):** development uses the shared
`creative-library-v2-stage` bucket with `ASSETFORGE_R2_PREFIX=vn` (everything AssetForge reads, writes, lists or
deletes stays under `vn/`); production gets a dedicated bucket and credentials later, a configuration change only.

## Pilot smoke test (2026-11-12)

*The Weight He Carries* compiled read-only (a copy of the catalog, `--no-derive`, output to a temp folder): a draft,
16 chapters, 1,924 nodes, 65 assets (41 missing: 16 sprites, 8 ambiences, 8 backgrounds, 4 sfx, 3 CGs, 2 music),
8 chapters unwritten, and one real script error: `what_he_carries_hidden` reads and writes `penny_moved`, which the
outline never declares. Structural validation of the documents found nothing else.
