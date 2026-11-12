# Visual-novel content: integration guide

The content lives in a bucket behind a public origin. There is no server: one signed index per channel says which
stories exist and which release of each is current; everything else is immutable and reached through descriptors
`{key, mediaType, bytes, sha256}`, so every byte can be verified. The app never lists the bucket and needs no
credentials. `client.py` is a working reference implementation (stdlib + `cryptography`).

## Ship with the app

- Public key: `PcoXIJxsckUUQs8ckCHl1d+7O1CLGbv3uMStn/RiLsM=` (keyId `02840bdde7966966`, also in `public-key.json`). Ed25519 over `"assetforge.vn.catalog.v2\n"` + the exact payload bytes.
- The channel: test builds read `staging`, the shipped app reads `production`.
- Index URLs (relative keys are appended to the same origin and prefix):
- staging: `https://media.stage.lookfinity.com/vn/catalog/staging/current.json`
- production: `https://media.stage.lookfinity.com/vn/catalog/production/current.json`

## Sync algorithm

1. `GET catalog/<channel>/current.json` with `If-None-Match: <last ETag>`. 304: nothing to do.
2. Parse the envelope `{alg, keyId, payload, signature}`. Reject when `keyId` is unknown, the signature does not
   verify, the payload fails `schemas/vn.catalog.schema.json`, `channel` is not yours, or `catalogSequence` is lower
   than the one you already accepted (a replay). On any failure keep using the last good index.
3. For each story whose `updatedSequence` is greater than the last sequence you processed: `status: "unlisted"`
   hides it from the shelf (keep installs and saves working). Otherwise compare `current` with the installed
   release. If the current release's `delta.fromRelease` is your installed release, fetch the delta
   (`schemas/vn.delta.schema.json`); otherwise fetch the new `release.json` and compare descriptors by `sha256`.
4. Always fetch the new `release.json`, each chapter's `bundle.json` and `downloads.json` (they name the release, so
   they are new every time; they are small). Download only content documents (`graph`, `presentation`, `assets`,
   `locales/<lang>`) and blobs whose digest you do not have, into temporary files; check size and SHA-256; then
   move them into a cache keyed by digest. Activate the release only when its required set is complete.
5. Record the processed sequence. A rollback is just a newer sequence pointing at an older release; with a
   digest-keyed cache it downloads nothing.

`changes` (newest first, at most 50: story_added, release_published, rollback, story_unlisted, story_relisted,
promoted) is for a "What's new" screen; the truth is the story entries.

## Cache headers

- `catalog/<channel>/current.json`: `Cache-Control: no-cache` (revalidate with the ETag every time).
- Everything else: `public, max-age=31536000, immutable`. Keys never get new content.

## Failures

- Network error, 5xx, bad signature, schema failure, older sequence: keep the last good index and cache; retry later.
- A downloaded object whose size or digest differs: discard it, keep the old release active, retry later.
- A release needing a newer engine (`minEngineVersion`): keep the installed one.
- Saves carry the `graphId`; a new release's `migration-from-<old>.json` maps vanished node ids.

## What is and is not enforced

- Enforced by cryptography: who published the index (signature) and what every object is (digests).
- Not enforced: access. Chapters carry `accessPolicyId`, but a public bucket serves every object to anyone who
  knows the key; nothing enforces paid or scheduled content without a gateway (a Worker in front of a private
  bucket). Hiding (`unlisted`) only hides from honest clients.
