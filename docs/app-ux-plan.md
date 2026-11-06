# AssetForge app: stories you can watch, read and play while they're written

## Why

Feedback: the Mac app isn't intuitive, you can't see what StoryCraft is doing or how far it got, you can't read or
play a story until it's completely finished, and there's no catalog of all stories.

Root causes found:
1. **Progress is tracked only for tasks the app itself started**, in memory. Work started from the CLI or by an
   agent is invisible, as with "Static Tomorrow", which was being written while the app showed nothing. A restart
   also forgets everything.
2. The player only runs after *all* scenes are written **and** exported to a folder.
3. Jobs → "Kind" has no `text` option, and story jobs show no description.
4. The story page puts setup (writer, folder path) before content, the stage strip is static, and the new-story
   form shows every field at once.

## What changes

### 1. Live progress from the story's files and jobs (whoever started the work)
- New `story/progress.py` `progress(lib, project)`:
  - **Pipeline stages**: Idea → World → Cast → Outline → Chapters n/N → Art & sound n/N → Playable.
  - **Running now**: text jobs whose `request.project` is the story's project and whose status is `running`. Each
    has a readable label ("Writing chapter 6 · *The First Broadcast*"), writer/model and elapsed time.
  - **Activity feed**: the last finished and failed steps with times.
  - **ETA**: based on the average chapter time so far.
- `GET /api/stories` and `/api/stories/{id}` include it. While anything is running the UI polls every 2 s;
  otherwise it updates through the library revision watcher it already has.
- A **sidebar activity pill** ("● Writing 2 chapters"): click it to open that story. A count badge on Stories.
- Jobs page: a `text` kind, and story jobs labelled "Static Tomorrow · chapter s_arrival".

### 2. Read and play an unfinished story
- **Chapter rail** on the story page, in reading order from the outline. Each chapter is
  - ✓ written: click to read it like a script (names, expressions, narration, choices), or press ▶ Play from here;
  - ⟳ being written now: shows a live spinner;
  - ○ planned: shows the planned summary.
- **Live preview player**: `/preview/{id}/` serves the player with `story.js` built on the fly from the files as
  they are now. No export is needed, and it's always current.
  - Written scenes play normally.
  - Unwritten scenes are stand-ins: the planned summary as narration, a "This chapter is still being written"
    card, and the planned exits, so you can keep playing past them.
  - Art and sound come straight from the library (`/media/variant/…`); missing pieces are placeholders.
- `?scene=<id>` starts the player at a chapter.
- Player improvements:
  - chapter title cards;
  - a "draft" banner;
  - saves that survive edits (clamped, with a version check);
  - no dead end when every choice is conditional (C10);
  - history/backlog (H) and text speed.

### 3. Stories hub (catalog)
- Cards with cover art (title art when it exists, otherwise a generated gradient), genre, a **progress bar**
  (chapters, art) and a status badge: *Writing…*, *Ready to play*, *Needs attention*, *Draft*.
- Search, a filter (All / In progress / Playable) and sort by last activity.
- Every story the library knows: those made in the app, from the CLI, or by agents.
- **New story** opens a 4-step guided form: 1 Idea · 2 Feel (genre, tone, audience and art style chips) · 3 Length
  (chapters per playthrough, endings) · 4 Writer (signed-in subscription and model). The defaults are good, so only
  the idea is required.

### 4. Story page layout
- Header: cover, title, logline, a big status pill and the primary action that fits the stage: *Start writing* /
  *Writing… (watch)* / *Play* / *Make art*.
- Tabs reordered by what you do most: **Chapters** (rail + reader) · **Play** (live preview) · **Improve** ·
  **Art & sound** · **Settings** (writer, model, folder, export).
- Live stage tracker under the header, showing what's running now.

### 5. App-wide polish
- Sidebar: Stories near the top; "Generate" becomes "Create".
- Overview: a "Continue" card for the latest story, with its progress.
- Empty states that say what to do next. Error boxes with a fix button where one exists (e.g. Sign in).

### 6. Review fixes bundled in, because they affect this flow
- C2: free music, ambience and SFX are made even without an image key; a missing image key stops images only.
- C3: rewriting a scene really rewrites it (it bypasses the answer cache).
- C6: "Sign in again" forces a new login.
- C9/C10: player saves and stuck choices (above).

## How it's built
- Backend:
  - `story/progress.py` (new);
  - `story/steps.py` `preview_script()` (partial assembly with stand-in scenes);
  - `app/api_story.py` (progress in list/detail, `/preview/{id}/…`, `/api/activity`);
  - `app/api.py` (text kind + job labels).
- Frontend: `views/story.js` (hub, wizard, story page, reader), `app.js`/`index.html` (sidebar pill, nav),
  `views/jobs.js`, `views/overview.js`, `app.css`.
- Player (`exporters/vn_player/index.html`): done in parallel by a sub-agent, with a fixed data contract
  (`scene.draft`, `scene.summary`, `data.preview`, `?scene=`).
- Tests: progress from fake running jobs, the preview script with unwritten scenes, API routes, and the review fixes.
  Browser check on "Static Tomorrow" while it is still being written.
