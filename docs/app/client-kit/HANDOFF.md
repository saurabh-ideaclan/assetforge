# Handoff to the Flutter app build: visual-novel content is live (2026-11-12)

From the AssetForge session, on the owner's instruction. This is everything the app needs to load the first story.

## What is live

- Story **Nobody Rides the Tail** (`nobody-rides-the-tail`), release `r0002`, graph `nobody-rides-the-tail.graph.2`,
  locale `en`, 13 chapters, 3 endings, entry chapter `morning_count`.
- Channel `staging` (development), index sequence 1. Nothing is on `production` yet.
- **Index URL** (public, no credentials): `https://media.stage.lookfinity.com/vn/catalog/staging/current.json`
- **Origin + prefix** for every other key: `https://media.stage.lookfinity.com/vn/`. Every `key` in every document is
  relative to it, e.g. `stories/nobody-rides-the-tail/releases/r0002/release.json`, `blobs/sha256/<digest>.webp`.
- Verified: all 327 objects downloaded and hashed against their descriptors; the index returns 200 with
  `Cache-Control: no-cache` and an ETag; everything else is immutable.
- Sizes: whole story about 9.2 MB (`core`) or 58 MB (`standard`); a chapter is roughly 2 MB core, 16 MB standard.

## Trust

- The index is a signed envelope `{alg: "Ed25519", keyId, payload, signature}`; `payload` is base64 of the exact JSON
  bytes; the signature covers the ASCII prefix `assetforge.vn.catalog.v2\n` followed by those bytes.
- Public key (raw 32 bytes, base64): `PcoXIJxsckUUQs8ckCHl1d+7O1CLGbv3uMStn/RiLsM=`, keyId `02840bdde7966966`.
- Reject an unknown keyId, a bad signature, a `catalogSequence` lower than one already accepted, or a `channel` that is
  not yours. On any failure keep the last good index.
- Every other file comes through a descriptor `{key, mediaType, bytes, sha256}`: check size and SHA-256 before use.

## Documents (each has `kind` and `schemaVersion: "2.0"`)

| Kind | Where | Holds |
|---|---|---|
| `vn.catalog` | index payload | stories with `status`, `current`, `updatedSequence`, `thumbnail`, `downloadBytes`, `releases[]` (`manifest`, optional `delta`); `changes[]` feed |
| `vn.release` | `…/r0002/release.json` | `requires` (engine 2.0.0; `graph.branching@1`, `scene.layers@1`, `audio.mix@1`), `stateSchema`, `chapters[]` → bundle, `endings`, `cover`, `locales`, `migrations` |
| `vn.chapter.bundle` | `…/<chapter>/bundle.json` | descriptors of the five chapter documents |
| `vn.chapter` | `graph.json` | nodes: narration, dialogue, choice, condition, action, chapterEnd, ending |
| `vn.presentation` | `presentation.json` | complete scene states and cues with events |
| `vn.assets` | `assets.json` | logical id → variants (descriptor, size, `altKey`) |
| `vn.locale` | `locales/en.json` | every text key |
| `vn.downloads` | `downloads.json` | `startupNodes`; profiles core, standard, enhanced |
| `vn.delta`, `vn.migration` | next to a release | what changed since the previous release; save migration |

State: 8 booleans under `flags.*`, 2 integers under `relationships.*`, 4 under `stats.*`, each with default, min, max
and an overflow rule.

## Sync, in five steps

1. GET the index with `If-None-Match`. 304: done.
2. Verify the envelope.
3. For each story whose `updatedSequence` is greater than the last one processed: fetch the new `release.json` (and
   the delta when the installed release is its `fromRelease`), then each chapter's `bundle.json` and `downloads.json`.
4. Download only documents and blobs whose digest is not cached, to temporary files; verify; move into a cache keyed
   by digest. Activate a release only when the required set for the chosen profile is complete.
5. Record the sequence. A rollback is a newer sequence that points at an older release.

Engine rules: conditions and effects are evaluated locally; effects apply in order to a candidate state and any invalid
operation rejects the transition; a line advance never touches the network; chapters change only through `chapterEnd`;
sound events are at-most-once and suppressed on a passive restore.

## Files on this machine

- This folder: `INTEGRATION.md`, `public-key.json`, `schemas/*.schema.json` (generate Dart models from these),
  `samples/`, `client.py` (a working reference client; port its logic).
- The contract and mapping decisions: `docs/narrative/26-content-contract-v2.md`, `27-content-backend-plan.md`.
- A local copy of exactly what is in the bucket, for offline development (read only):
  `/Users/sumansaurabh/.assetforge/stories/nobody-rides-the-tail/releases/` (`r0002/` documents, `blobs/` media).

## Design for these

- Development setup: a shared stage bucket under the `vn` prefix. Production will be a dedicated bucket with another
  origin. Keep origin, prefix, channel and public key in configuration.
- No gateway: nothing enforces access. `accessPolicyId` is on each chapter (`"default"` today) for a later one.
- Node ids and text keys are positional. A text-only update keeps them and the `graphId`; adding or removing a line
  changes ids and bumps the `graphId`, and the migration file maps old node ids to new ones.
- Images carry no baked text. Audio comes as ogg and m4a variants of one asset: choose by platform.
- The true ending is gated on state and is reached in about 6 % of random plays; not seeing it in testing is expected.

A content fix on the AssetForge side is a new release and a new index sequence, which the sync picks up.
