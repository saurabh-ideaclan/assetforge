# AssetForge app: live data and durable tasks (architecture, 2026-11-12)

Answers the brief in `01-application-brief.md`: what is on screen is true, work is visible wherever it was started,
and the app says when it is out of date or cut off. Everything stays stdlib on the server and vanilla ES modules
(no build step) in the page; the CSP is unchanged (`script-src 'self'`, `connect-src 'self'`).

## 1. One change feed (server)

`app/feed.py` keeps one set of **revision markers**, one per *topic*. A topic's revision is an opaque string that
changes whenever what it describes changes, whoever changed it.

| Topic | Made of |
|---|---|
| `library` | assets + variants (counts, newest `updated_at`), newest asset event |
| `jobs` | generation jobs (count, newest `updated_at`) |
| `tasks` | `count(*)` and `total(rev)` of the tasks table, plus the ids of live tasks that went silent |
| `stories` | the story registry (`stories.json`) |
| `story:<id>:text` | `outline.json`, `script.json`, `world.json`, `notes.md`, `feedback.json`, `scenes/*.json`, `alternatives/*.json` |
| `story:<id>:cast` | `cast.json`, `characters.json`, `acting.json`, `names.json` |
| `story:<id>:art` | `visual_canon.json` + the library assets keyed `vn/<slug>/…` (key, status, primary variant, `updated_at`) |
| `story:<id>:audio` | `sound.json`, `audio/*.json` |
| `story:<id>:checks` | `lint.json`, `clarity.json`, `clarity/*.json`, `visual_check.json`, `evaluation.json`, `check.json`, `dynamics.json`, `payoffs.json`, `rules.json` |
| `story:<id>:releases` | `releases/` (top entries and each release's own JSON, never the blob tree), `build/index.html`, `build/story.js` |
| `story:<id>:workflow` | `workflow.json` |
| `story:<id>:settings` | `story.json` |
| `story:<id>:progress` | the story project's `text` jobs (count, newest update, how many running) |
| `publishing` | `<library>/backend/*.json` + every story's releases |
| `logins` | provider sign-ins started from the app (state, link ready) |
| `build` | the running code (see §5) |

Story revisions come from `os.scandir` + `stat` (mtime_ns, size) of the story folder; `.back`, `.tmp` and dot files
are ignored. A whole library with seven stories costs a few milliseconds. Because only files and the catalog are
read, it does not matter whether the app, the CLI, an agent's script or another window made the change.

**The watcher.** One daemon thread per server (`Feed._run`, its own pooled `Library`) recomputes the snapshot once
a second while anyone listens (an open SSE stream, or a poll in the last 30 s) and every 5 s otherwise. A snapshot
whose revisions differ from the last one gets the next `seq`; the last 64 are kept so a client can ask what changed
since its `seq`. A `POST` handled by the app pokes the watcher so the app's own changes reach every window at once.
Every 10 s the watcher also runs `tasks.recover()` (§3), so a task whose process died turns `interrupted` without
anyone asking.

**Routes** (`app/api_live.py`, same token/cookie, Host check and CSP as every route):

- `GET /api/events` — server-sent events on the existing `ThreadingHTTPServer` (one thread per open stream, which
  releases its pooled catalog connection before streaming: `server.StreamResponse`). It sends `retry: 2000`, then
  `event: hello` (the full state, or what changed since `?since=<seq>`), then `event: change` whenever the snapshot
  changes, and `event: ping` every 15 s (visible to the page, so a silent stream can be detected).
- `GET /api/changes?since=<seq>` — the polling fallback with the same payload. An unknown or missing `seq` returns
  the full state with `full: true`.
- `GET /api/version` — `{build, started_at, pid, home}`.

Payload: `{seq, at, build, started_at, full, revisions: {topic: rev}, changed: [topics]}`.
`/api/revision` (the old polling marker) still answers for anything that used it.

## 2. One client store (page)

`static/store.js` is the only module that listens to the server.

- `connect()` opens the EventSource; on a closed stream (e.g. 403) it falls back to polling `/api/changes` (2 s in
  front, 10 s hidden, backing off on errors) and retries the stream now and then. A stream that has been silent for
  40 s is reopened. A change of `started_at` (the server restarted) is treated as a full refresh.
- `watch(topics, fn, {owner, debounce})` — topics are exact or globbed (`story:kite-season:*`, `story:*:text`).
  `fn(changedTopics)` runs debounced (150 ms by default), never twice at once, and once more if changes arrived while
  it ran. A subscription is dropped when its `owner` element leaves the page, and every subscription made while a
  view is mounted is dropped when the view unmounts (`beginScope`/`endScope` in `app.js`).
- `cached(url)` / `load(url)` — a small stale-while-revalidate cache, so a panel that is rebuilt redraws at once
  from the last answer and refreshes behind the scenes (no spinner flash, no scroll jump).
- `onTasks(fn)`, `refreshTasks()` — the task list, refetched on `tasks`; `waitTask(id)` and `waitJob(id)` resolve
  when a task or a generation job ends, through the feed (they replaced the `pollJob` and per-view task loops).
- `onStatus(fn)` — `{connection: live|polling|reconnecting|forbidden, build, outdated}`.

`static/live.js` redraws without losing what the person is doing:

- `rerender(el, render)` renders into a detached element, then swaps the children in one go and restores what the
  person touched: text typed into fields (tracked by a capture-phase `input` listener), toggled `<details>`, scroll
  positions inside the region, the page scroll, focus and caret. **While a text field inside the region has focus,
  the redraw waits** (a small "Updated in the background — show the latest" note) and runs when focus leaves.
- Elements with `data-live-key` (and optionally `data-live-sig`, `data-live-state`) are compared before and after:
  a new key flashes `af-new`, a changed signature flashes `af-changed`, and `missing → made` (a story asset that was
  just generated) counts as new.
- `panel(el, {url, topics, render, deps})` — a self-refreshing panel: cached draw, fetch, redraw only when the data
  (or `deps()`) changed, and on error an error box with **Retry** that keeps whatever it showed.
- `confirmAction({title, lines, cost, note, confirm, danger})` — the one dialog for paid or irreversible actions.

### How a view subscribes

A view returns from its mount function either

- `{topics, refresh}` — the shell calls `refresh(changed)` when a topic changes (the view redraws its own parts,
  usually with `rerender`); or
- `{topics}` only (or is listed in `app.js` `TOPICS`: overview, usage) — the shell re-mounts it into a detached
  element and swaps it in with `rerender(main, …)`, keeping scroll and typing.

Inside a view, panels subscribe themselves with `watch([...], fn, {owner: el})` or `panel(...)`. Form-only views
(create, sound, art direction, AI plan) list no topics: nothing changes under the person's hands.

### The story page

`detail()` in `views/story.js` holds one subscription, `story:<id>:*` + `tasks`. On a change it refetches
`/api/stories/<id>` into the *same* `data` object (tabs keep a reference to it), redraws the header and the tracker,
and redraws the tab body with `rerender` only when the tab's own signature changed:

| Tab | Live through |
|---|---|
| Workflow | body signature (workflow, task state) |
| Characters (incl. the acting panel, concept candidates) | its own `watch` on cast/art/text; acting and candidates draw from the cache first |
| Dynamics | `panel` on `/dynamics` (text, checks, tasks) |
| Chapters (rail + reader) | body signature (chapters, written scenes, cast); lines and rail items are keyed, so a rewritten line flashes |
| Scene audio panel | `panel` on `/audio/<scene>` (audio, art, text) |
| Play | a note: "The story changed since the player loaded — Reload the player" (the player is never reloaded behind the reader's back) |
| Assets (incl. verdict marks) | its own `watch` (art, checks, text, audio, cast); tiles keyed by asset key |
| Improve | body signature (check report, feedback rounds, task) + the evaluation `panel` |
| Settings (incl. the Release card) | body signature (config, names, direction); the Release card watches releases + publishing |

Deep links: `#/story/<id>/<tab>?chapter=…&asset=<key>&show=all|missing|made&panel=audio|release|acting|evaluation`.
`#/story/<id>?tab=…` still works. Tab switches change the hash (Back/Forward work); the chapter, the Assets lens and
filter are written back with `replaceState` as they change.

## 3. Durable tasks

**Table** (schema v7, `core/db.py` `SCHEMA_V7`): `id, kind, title, story, project, state, steps (JSON), progress,
message, started_by, pid, host, command, request (JSON, for Retry), locks (JSON), cancellable, retryable,
cancel_requested, error, result, result_link, log (JSON, last 200 lines), created_at, started_at, ended_at,
heartbeat_at, updated_at, rev`. Times are epoch seconds.

**States:** `queued | running | waiting | succeeded | failed | cancelled | interrupted`.
`waiting` means the work stopped for a decision (a workflow gate, a paid fallback held back): it has no process and
stays under "Waiting for you" until dismissed or superseded by a newer task of the same kind on the same story.

**Module** (`core/tasks.py`):

```python
with tasks.track(lib, "story.write", title="Write chapters", story="kite-season", locks=("text", "cast"),
                 cancellable=True, steps=["World", "Cast", "Chapters"]) as t:
    t.step(0)            # or t.step("A label"), t.done_step("one finished unit"), t.progress(0.4), t.log("…")
    t.check_cancel()     # raises TaskCancelled when Cancel was pressed
    t.succeed(result, link="#/story/kite-season/chapters")   # or t.wait_for_you("Approve the cast")
```

`track` ends the task `succeeded` (unless the body ended it), `failed` with the error's `to_dict()`, `cancelled` on
`TaskCancelled`, `interrupted` on Ctrl-C. A `Task` owns its own SQLite connection and a lock, so any thread may
report on it; a daemon thread writes `heartbeat_at` every 4 s and picks up the cancel flag.

**Never running when dead.** `effective_state()` reads a live row (`queued`/`running`) as `interrupted` when its
heartbeat is older than 30 s, or at once when it ran on this host and its pid is gone. Every read uses it, so the UI
never shows a dead task as running; `recover()` writes it (server start, and the feed every 10 s) and marks the dead
task's still-`running` text jobs for the same project failed (unless another live task works on that story), so the
story tracker stops spinning too. `queue.recover()` (expired leases, orphaned generation jobs) runs at server start.
The jobs list marks a job still `running` 30 min after its last update as `stale`.

**Who registers:**

- The app's story actions (`POST /api/stories/<id>/run`, `api_story.start_story_task`) and Studio productions
  (`POST /api/audio/produce`, `api_audio.start_production`). Their routes keep the old shapes: `/api/story-tasks/<id>`
  and the story's `task` say `state: running|done|failed` (with the new state in `status`); `/api/audio/tasks/<id>`
  likewise.
- The CLI: `cli_common.session()` registers the long commands in `TRACKED` (`story world|names|cast|outline|write|
  refine|improve|check|assets|export|make|contract|publish|art-check`, and `backend publish|promote|rollback|unlist|
  relist|repair|gc` only with `--yes`; never `--dry-run`) with `started_by: cli` (or `$ASSETFORGE_STARTED_BY`), the
  command line, the story (resolved from `--dir`/the story argument) and its asset project. `cli_story._tracked()`
  hands the steps a `story.tracked.TrackedCtx`: each model call becomes a finished step, and the cancel flag is
  checked before and after every call. Tracking never fails a command.
- Any script: `tasks.track(...)` as above (`started_by: script`).

**Cancel** is cooperative and honest: a queued task can always be cancelled; a running one only when its code checks
the flag (`cancellable`). Story actions that go through model calls are cancellable between calls (the call in
flight finishes and stays cached); art/sound production, export, contract and publish are not, and the UI says
"Can't cancel — it will finish what it is doing".

**Retry** re-sends the stored request (app-started story and audio tasks, after failed/cancelled/interrupted, or a
waiting paid fallback with `allow_paid`). CLI tasks show their command line to copy instead.

### Concurrency rule

Each story action declares the story areas it reads or writes (`api_story.LOCKS`), e.g. `write: text, cast,
workflow`; `check: text, checks`; `assets: art, audio, build`; `dynamics_plan: dynamics`; `publish: releases`.
**Two tasks on the same story conflict when their lock sets intersect.** The later one is created `queued`, its
thread waits (`Task.wait_turn`, checking the cancel flag) until no earlier conflicting task is live, then runs. The
run route answers `{task_id, queued, waiting_for}` and the UI says "Queued: waits for …". Tasks with no locks
(`assets_plan`, audio productions, scripts that declare none) never wait. CLI tasks declare the same locks, so an app
task queues behind a terminal run of the same area; the CLI itself never waits (it is the operator's explicit act).

## 4. Activity centre

`static/activity.js`: the sidebar pill opens a drawer with **Running** (running + queued), **Waiting for you**,
**Failed or interrupted**, **Finished**. Each row: title, state, who started it (the app, the CLI, a script), live
elapsed time, steps done/total, progress bar, the current message; expanded: the error, the steps, the log tail, the
command line (copy), kind/id/story/pid. Actions: Cancel (or a disabled "Can't cancel" with the reason), Retry,
Dismiss (waiting), Open/Decide (the result link). A toast (with Open and Details) and the pill badge appear when a
task ends or starts waiting, whatever started it; the Stories badge counts stories with live or waiting work. The ⌘K
palette lists running and waiting tasks and every story tab.

## 5. Out of date, cut off

`build` = hash of the package's Python files (computed at server start: the running code) + a signature of the static
files (re-checked every 2 s, so a changed script is noticed without a restart). The page remembers the first build it
saw; when the feed reports another it shows "AssetForge was updated while this page was open — Reload now" and reloads
by itself once nothing is typed (no focused or typed-into field), nothing is playing (audio/video, including in the
player iframe) and no dialog is open. When the stream drops: "Server not reachable — reconnecting…" with automatic
reconnection; after a restart everything that changed meanwhile is redrawn and dead tasks read `interrupted`.

## 6. P1 status

Done: deep links (tab, chapter, asset, panel, Assets lens/filter; old links work); per-panel error with Retry
(`panel`, story assets, release card, characters, jobs, route-level Retry in `app.js`); one confirm component for
paid or irreversible actions (make missing assets with the last estimate, remake an image, publish a release); Undo
for archive and reject from the toast in the asset browser (single and bulk; each asset returns to the status it
had); palette reaches story tabs and live tasks.

Not done: the confirm component is not yet used on the Publishing page or the Create view (they keep their own
confirm steps); loading/empty states on the older views (overview, usage, art, AI plan) are as before; no
per-asset deep link inside the asset browser beyond `#/assets/<id>` (which already existed).

## 7. What was removed

The hand-written loops: `app.js` `watchLibrary` (3 s), `pollActivity` (4 s), `pollReview` (15 s); `lib.js`
`watchLibrary` and the `setInterval` in `pollJob` (now `store.waitJob`); `story.js` hub refresh (3 s), story page
`setTimeout` loop (2.5 s / 15 s), release card task loop (1.2 s), art-check `watchCheck` (4 s), sign-in poll (1.5 s,
now the `logins` topic); `audio_studio.js` task poll (1.5 s). In Python: the `_tasks`/`_busy` dicts in
`api_story.py` and the `_tasks` dict in `api_audio.py`; "one task per story" (409) became the lock rule. The
`location.reload()` calls after choosing an alternative and after saving a creative direction are gone (the feed
redraws). The only timers left in the page are clocks (elapsed time, a 3 s "safe to reload yet?" check) and the
stream watchdog.
