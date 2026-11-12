# AssetForge app: from makeshift to application (brief, 2026-11-12)

The owner's words: the app "is like a makeshift, it was not actually a full-fledged application, but I think it
needs it now… much things do not refresh [when] other tasks [run]". The owner now works in the app all day: reads
chapters, reviews art as it is generated, and expects what is on screen to be what is true.

## What the audit found (2026-11-12)

1. **The app only notices library changes.** `GET /api/revision` reports counts and timestamps of assets, variants,
   events and jobs. Nothing reports a story's own files: chapters, `script.json`, `acting.json`, `clarity/`, `audio/`,
   `visual_canon.json`, `visual_check.json`, `lint.json`, `releases/`, `workflow.json`, `characters.json`. Work done by
   the CLI, an agent or another process (most of today's production) never reaches an open page.
2. **The story page refreshes three tabs out of eight**, and only when a chapter's *state* or the task state
   changes (`detail.refresh` in `views/story.js`): a rewritten chapter, a new sprite, a new verdict, a new release, an
   approved performance or a changed setting stays stale until the page is reloaded. Assets, Play, Improve,
   Dynamics and Settings never refresh.
3. **Eight separate hand-written polling loops** (`setInterval` / `setTimeout` in `app.js`, `lib.js`, `story.js`,
   `assets.js`, `audio_studio.js`, `generate.js`), each with its own idea of "busy". No shared data layer.
4. **Tasks live in a Python dict** (`_tasks`, `_busy` in `api_story.py`; another registry in `api_audio.py`): lost on
   restart, one per story, invisible when the work was started from the CLI or by an agent, no cancel, no log, no
   history, no notification when a task ends or needs a decision.
5. **A changed server is silent.** Python changes need a restart, static files are `no-cache`; an open page keeps
   running old JavaScript against a new API and nothing says so.
6. **Routing is partial.** `#/story/<id>/settings` opens the first tab; a tab, a chapter, an asset or a panel cannot
   all be linked to.
7. **Errors are local and quiet**: a failed fetch inside a tab leaves it half-drawn; there is no place that says the
   server is unreachable, a provider is signed out, or a paid step is waiting for a yes.

## What "an application" means here

**P0: what is on screen is true.**
- One change feed for everything: library, each story (per area: text, cast/acting, art, audio, checks, releases,
  workflow, settings), tasks, jobs, publishing state. Server-sent events over the existing stdlib server (GET, token
  cookie, Host check), with reconnect and a polling fallback. Story changes are detected from the files, so it does
  not matter who wrote them.
- One client store. A view says what it depends on; it re-renders that part in place and keeps scroll position,
  selection, open panels, playing audio and anything typed. Every tab of the story page is live. New and changed
  things are marked for a moment so the eye finds them.
- No hand-written polling loops left in views.

**P0: work is visible wherever it was started.**
- A durable task record (library SQLite, a new migration) for every long piece of work: started from the app, the
  CLI or an agent's own script. Kind, story, state, steps with progress, started/ended, who started it, error,
  result link, a short log.
- An Activity centre (a drawer from the sidebar pill): running, waiting for you, finished, failed; cancel where the
  work can stop cleanly; retry; open the result. A toast and a badge when something ends or needs a decision.
- Tasks survive a server restart (running ones are marked interrupted, never left spinning). More than one task per
  story when they do not touch the same files; otherwise queued, and the UI says so.

**P0: the app says when it is out of date or cut off.**
- A build id from the server; when it changes the page offers to reload (and reloads itself when nothing is typed).
- A clear state for "server not reachable" with automatic reconnect.

**P1: it behaves like one product.**
- Full deep links: tab, chapter, asset, panel, filter. Back and forward work.
- Every view has loading, empty and error states with a retry; an error in one panel never blanks the page.
- Paid or irreversible actions go through one confirm component that shows what will happen and the cost estimate.
- Archive and reject can be undone from the toast.
- Keyboard: the palette reaches every story tab and every running task.

**Out of scope:** a build step or framework (vanilla ES modules stay), a rewrite of views that already work, the
story pipeline, the player, anything that changes generated content.

## Acceptance (the owner's own complaint, as tests)

1. With a story page open on the Assets tab, an image made by a separate process appears within three seconds,
   marked new, without a reload and without losing scroll.
2. With a chapter open in the reader, the same chapter rewritten by a separate process updates in place.
3. A run started from the CLI shows in the Activity centre with its steps, and a toast appears when it ends.
4. Restart the server while the page is open: the page says so, reconnects, offers the reload, and no task spins forever.
5. Type in a form, let unrelated data change: nothing typed is lost.
6. `#/story/<id>/assets?chapter=…` opens exactly that.
