# 27. Content backend without a server (plan, 2026-11-12)

The owner's ask: a "pseudo backend" that lives in AssetForge only. It manages the published content in the R2
bucket and maintains an index file the app can parse to find new and updated content. No server, no database:
**the bucket is the database, AssetForge is its only writer, the app is a read-only client.** This extends the v2
content contract and publisher (doc 26); it replaces nothing in the story pipeline.

## Principles

1. Every write is explicit (`--yes` on the CLI, `confirm: true` in the app). Nothing publishes after a write, an
   export, an asset run or a compile. Every command has a plan / dry-run form that only reads.
2. Content is immutable and content-addressed; only small pointer files change. A pointer is always written last.
3. One trust root: the index is a signed envelope (Ed25519, as today). Everything else is reached through
   descriptors `{key, mediaType, bytes, sha256}`, so the app can verify every byte it downloads.
4. The app never lists the bucket and never needs credentials: it fetches one index URL, then follows keys.
5. Nothing is deleted by default. Rollback and unlisting are new index sequences, never removals.

## Bucket layout

All keys sit under an optional prefix (`ASSETFORGE_R2_PREFIX`, default empty) so the content can live in its own
bucket or inside an existing one. Keys inside documents stay relative to that prefix; the prefix and the public
origin are delivery configuration, never content.

```text
catalog/<channel>/current.json          signed index, the one file the app polls (no-cache, ETag)
catalog/<channel>/history/<seq>.json    every index ever published on that channel (immutable)
stories/<story>/releases/<rid>/…        the v2 release documents (immutable; unchanged from doc 26)
stories/<story>/releases/<rid>/delta-from-<old>.json   what changed since the previous release (immutable)
blobs/sha256/<digest>.<ext>             media, shared by every release and story (immutable)
```

Channels: `staging` (where `publish` writes) and `production` (reached only by `promote`). A test build of the app
reads staging; the shipped app reads production. Promotion writes one index and uploads nothing. Nothing has been
published yet, so `catalog/current.json` simply moves to `catalog/<channel>/current.json`; no migration.

## The index (`vn.catalog`)

The payload of the signed envelope. Additive to doc 26's catalog:

```json
{
  "kind": "vn.catalog", "schemaVersion": "2.0",
  "channel": "staging", "catalogSequence": 7, "generatedAt": "2026-11-12T09:30:00Z",
  "stories": [{
    "storyId": "nobody-rides-the-tail", "status": "live",
    "title": "…", "summary": "…", "defaultLocale": "en", "locales": ["en"],
    "thumbnail": { "key": "blobs/sha256/….webp", "mediaType": "image/webp", "bytes": 0, "sha256": "…" },
    "current": "r0002", "addedSequence": 3, "updatedSequence": 7, "updatedAt": "…",
    "chapterCount": 13, "downloadBytes": { "core": 0, "standard": 0 },
    "releases": [{
      "releaseId": "r0002", "graphId": "….graph.1", "minEngineVersion": "2.0.0", "publishedAt": "…",
      "manifest": { "key": "stories/…/r0002/release.json", "mediaType": "application/json", "bytes": 0, "sha256": "…" },
      "delta": { "key": "stories/…/r0002/delta-from-r0001.json", "mediaType": "application/json", "bytes": 0, "sha256": "…" }
    }]
  }],
  "changes": [{
    "sequence": 7, "at": "…", "type": "release_published", "storyId": "…", "releaseId": "r0002", "fromReleaseId": "r0001",
    "summary": { "chaptersAdded": 0, "chaptersChanged": 2, "chaptersRemoved": 0, "textOnly": true, "graphChanged": false, "downloadBytes": 18240 }
  }]
}
```

- `updatedSequence` is the sequence at which a story's entry last changed. **The app's whole "what is new" check
  is: stories whose `updatedSequence` is greater than the last sequence it processed.**
- `changes` is the last 50 events, newest first (`story_added`, `release_published`, `rollback`, `story_unlisted`,
  `story_relisted`, `promoted`). It is a convenience for a "What's new" screen; the truth is the entries.
- `status: "unlisted"` keeps the entry (and saves keep working) but the app hides the story from its shelf.

## The delta document (`vn.delta`)

Written at compile time next to each release after the first, by comparing the two release folders:

```json
{
  "kind": "vn.delta", "schemaVersion": "2.0", "storyId": "…", "fromRelease": "r0001", "toRelease": "r0002",
  "graphChanged": false, "migration": null,
  "chapters": {
    "added": [], "removed": [], "unchanged": ["ch03", "…"],
    "changed": [{ "id": "ch01", "documents": ["locales/en"], "textOnly": true, "bytes": 9120 }]
  },
  "blobs": { "added": [{ "key": "blobs/sha256/….webp", "bytes": 0 }], "dropped": ["blobs/sha256/….webp"] },
  "downloadBytes": { "core": 0, "standard": 0 }
}
```

An app on r0001 downloads exactly what the delta lists. An app on any other release ignores the delta and diffs
the two `release.json` files by descriptor digest; both paths give the same answer (a test proves it).

## What the app does (the reference client)

`publish/client.py` is a small, dependency-free reference client used by the tests and shipped as documentation.
It is the acceptance test of the whole design: if it can stay in sync, an app can.

1. `GET catalog/<channel>/current.json` with `If-None-Match`. 304: nothing to do.
2. Verify the envelope with the shipped public key. Reject a `catalogSequence` lower than the one already seen
   (replay), an unknown key id, or a payload that fails the schema. On any failure keep the last good index.
3. For each story with `updatedSequence` greater than the last processed sequence: if the installed release has a
   delta to the current one, fetch the delta; otherwise fetch `release.json` and compare descriptors.
4. Download only changed documents and missing blobs (by digest) into temporary files, check size and SHA-256,
   then move them into the cache. Activate the new release only when its required set is complete.
5. Record the processed sequence. A rollback is just another sequence pointing at an older release.

## Operations (`assetforge backend …`, and a Publishing page in the app)

| Command | What it does | Writes |
|---|---|---|
| `backend status [--channel C]` | configuration, reachability, each channel's sequence, every story: local latest release, staging, production, in sync / ahead / never published | no |
| `backend plan STORY [--release rN]` | what a publish would upload, skip and refuse, with byte totals | no |
| `backend publish STORY [--release rN] --yes` | today's publication sequence, onto `staging`; uploads the delta too | yes |
| `backend promote STORY [--release rN] --yes` | makes a release already on staging current on `production`; verifies every object exists first; uploads nothing but the index | index only |
| `backend rollback STORY rN --channel C --yes` | a new sequence pointing at an older published release | index only |
| `backend unlist STORY` / `relist STORY` `--channel C --yes` | hide or show a story; saves keep working | index only |
| `backend verify [--channel C] [--deep] [--public]` | HEAD every object any listed release needs (`--deep`: download and hash; `--public`: anonymous HTTPS through `ASSETFORGE_R2_PUBLIC_BASE`, proving the app can read) | no |
| `backend repair --yes` | re-upload objects `verify` found missing, only when the local copy has the declared digest | yes |
| `backend ls [--orphans]` | inventory by listing the prefix: referenced by some index in some channel's history, or orphaned | no |
| `backend gc --yes --delete-orphans` | delete orphans only; never an object any index in any history references; default is a dry run | deletes |
| `backend pull` | rebuild the local published-state records from the bucket (a new machine, a second operator) | local only |
| `backend client-kit [--out DIR]` | the public key, JSON Schemas of every document kind, a sample index and delta, the reference client and a one-page integration guide for the app team | local only |

`story publish` stays as an alias of `backend publish`. Every write appends a line to
`$ASSETFORGE_HOME/backend/log.jsonl` (time, command, channel, story, release, sequence, objects, bytes) and
refreshes `$ASSETFORGE_HOME/backend/state.json` (the last index seen per channel), which the app page reads so it
opens instantly and says how old its view is.

App page (`views/backend.js`, sidebar "Publishing" under Operations): a connection card (bucket, prefix,
reachable or the exact error and its fix), channel tabs, a table of stories (local, staging, production, state)
with Plan / Publish / Promote / Rollback / Unlist behind a confirm step that shows the plan, the change feed,
verify results, and the app's index URL and public key with copy buttons.

## Configuration

| Setting | Meaning |
|---|---|
| `ASSETFORGE_R2_BUCKET`, `ASSETFORGE_R2_ENV_FILE`, direct credential overrides | as doc 26 |
| `ASSETFORGE_R2_PREFIX` | key prefix inside the bucket, e.g. `vn` (default none) |
| `ASSETFORGE_R2_PUBLIC_BASE` | the public origin; used to print the app's index URL and for `verify --public` |

**Decided (owner, 2026-11-12):** development uses the existing, shared `creative-library-v2-stage` bucket with
`ASSETFORGE_R2_PREFIX=vn`; production will get a dedicated bucket with its own credentials later, which is a
configuration change only (bucket, credentials, prefix, public origin), no code change. Because the stage bucket is
shared with another product, every read, write, list, verify, `ls` and `gc` stays inside `vn/`, and `gc` refuses to
delete with an empty prefix unless `--whole-bucket` says the entire bucket is ours.

## Safety and limits

- Concurrent writers: the history key of the next sequence is written first with a create-only condition
  (`If-None-Match: *` where the endpoint honours it, otherwise HEAD then PUT as today); the loser re-reads and
  re-plans. Two operators cannot both publish sequence N.
- Cache headers: the index `no-cache`; everything else `public, max-age=31536000, immutable`.
- A static bucket cannot enforce access. `accessPolicyId` is carried for a future gateway and enforced by nothing
  here; if chapters must be paid for, that needs a Worker in front of a private bucket (doc 26 §9 of the contract).
- No server-side scheduling. A later option, off by default: an `availableAt` time per chapter in the index entry,
  which the app honours. It hides content from honest clients only.

## Acceptance (tests, no network: the in-memory S3 fake)

1. Publish r0001 to staging; the reference client syncs from empty and ends with a complete, verified cache.
2. Change one line of text, compile r0002, publish: the client downloads one locale document and nothing else;
   the delta and the manifest diff agree.
3. Add an image: the client downloads one blob and the changed chapter documents.
4. Promote r0002: production's index changes, zero objects uploaded; a production client now sees the story.
5. Rollback to r0001: a new sequence; the client switches back without downloading.
6. Unlist: the entry stays, status changes, `updatedSequence` moves.
7. A tampered index, an index signed by another key, and a replayed older sequence are each rejected and the last
   good index stays in use.
8. `verify` finds a deleted and a corrupted object; `repair` restores both; `gc` deletes an orphan and refuses to
   touch a blob referenced only by an old history entry.
9. Two writers race for the same sequence: exactly one wins.
10. Every write command refuses without `--yes`; the plan forms perform no writes (the fake counts them).

## As built (2026-11-12)

Code: `story/delta.py` (release trees, `vn.delta`, download bytes), `story/contract.py` (writes the delta at compile
time, lists it in `release_objects`, validates it), `story/contract_models.py` (`Catalog` with channel and feed,
`Delta`), `story/catalog_sign.py` (`release_ref`, `entry_for`, `next_catalog`), `publish/r2.py` (configuration,
prefix, `Store`), `publish/backend.py` (every operation), `publish/client.py` (reference client), `cli_backend.py`,
`app/api_backend.py`, `app/static/views/backend.js`; tests `tests/test_backend.py` (the ten acceptance cases, prefix,
pull, client kit, app routes) with the shared fake `tests/s3fake.py`.

Where it differs from the plan above, and why:

1. **Structure vs content in the delta.** `bundle.json` and `downloads.json` name the release in their keys, so they
   are new in every release; they count as structure (always fetched with the manifest, like `release.json`, the
   delta and the migration) and never appear as "changed". A chapter's `documents` lists only `graph`,
   `presentation`, `assets`, `locales/<lang>`. "Downloads one locale document and nothing else" (acceptance 2) holds
   for content documents and blobs; the client reports the structure it fetched separately (`downloaded.manifests`).
2. **A new graph id touches every chapter's graph.** `graph.json` carries the `graphId`, so a gameplay change lists
   every chapter's `graph` as changed (acceptance 3: the image's chapter changes four documents, the other only its
   graph). Changing that would change the v2 contract; not done.
3. **Delta fields beyond the plan's sketch:** `fromGraphId`/`toGraphId`, and `releaseDocuments` (release-level
   documents whose digest changed, e.g. `locales/en` when the title changes), which the client needs. `migration` is
   the migration's descriptor; it is never null, because every release after the first has one (doc 26).
4. **`downloadBytes`** (index entry and delta) is an upper bound: every document plus, per asset and quality of the
   profile, the largest variant (an app downloads one codec). A delta's figure is what an app on `fromRelease` lacks.
5. **Thumbnail** is the cover's file descriptor, as the plan's JSON shows (doc 26 had the whole `cover`); the alt
   text stays in `release.json`.
6. **Republishing the release already current on staging is a no-op** (no new sequence; it used to mint one).
   Publishing an older release already listed is recorded as `rollback`.
7. **Plan forms.** In Python `publish(yes=False)` still raises (its plan form is `plan`); `promote`, `rollback`,
   `set_listed`, `repair`, `gc` return their plan with `written: false`. On the CLI every write prints its plan
   without `--yes` (exit 0); in the app without `confirm: true`.
8. **Stale pointers.** `read_index` follows `history/<seq+1>` past `current.json`, so a write interrupted between the
   two is finished by the next one instead of blocking it.
9. **Create-only history.** `IfNoneMatch="*"` first; a client without the parameter (`TypeError`,
   `ParamValidationError`) or an endpoint answering 501 falls back to HEAD then PUT (a small race window remains
   there). 412/409 → `ConflictError`, nothing else written. Whether R2 honours `If-None-Match` on PUT was not checked
   against the live endpoint (no network writes were allowed).
10. **gc** deletes only orphans under `blobs/` and `stories/` (anything else inside the prefix is reported and left
    alone), counts every index file as referenced, refuses when any index or release cannot be read (orphans would
    be uncertain), and refuses an empty prefix without `--whole-bucket` (the owner's decision above).
11. **verify** without `--deep` catches missing objects and size or `x-amz-meta-sha256` mismatches; same-size wrong
    bytes need `--deep`. `--public` GETs each channel's index anonymously (signature and sequence checked) and HEADs
    every object through `ASSETFORGE_R2_PUBLIC_BASE`.
12. **status** reads only, locally too (the CLI never writes `state.json`); the app's live status, every write and
    `pull` refresh it. `GET /api/backend/status?cached=1` answers from it without the network.
13. **pull** writes `channels` (per channel: sequence, publishedAt, current) onto each local release in the story's
    `releases/index.json`, and `state.json`; it downloads no release.
14. **Production feed.** Every promotion is a `promoted` change (no separate `story_added` on production).
15. **client-kit** before the first publish writes everything but the key and says so (`status: partial`); it never
    creates the signing key.
16. **Reference client:** profile `standard` by default, audio `ogg` before `mp4`; one variant per asset and quality.
    A digest-keyed cache makes rollback free. `r2.plan`/`r2.publish` remain as aliases of the backend's.

