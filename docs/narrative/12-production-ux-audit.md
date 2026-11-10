# 12 · Production UX audit and revised flow (Phase A)

**Date:** 2026-11-10 · **Scope:** human oversight, provider controls, character management, creative transparency.
**Method:** read of `llm/`, `story/`, `app/api_story.py`, `app/static/views/*.js`, `story/progress.py`, `core/review.py`.

## 1. What exists today

| Area | Exists | Gap against the brief |
|---|---|---|
| Subscription execution | `claude-cli` (Claude plan via the `claude` CLI), `codex-cli` (ChatGPT plan via `codex`), `gemini-cli` (Google sign-in). Local sign-in checks, model lists, "worked" marks, in-app login | Real and authorised: these are the vendors' own CLIs. No per-task choice, no execution-order control |
| API execution | `openai-api`, `gemini-api`, opt-in (key **and** a model setting) | **No Anthropic API provider.** No paid-fallback confirmation, no spend rule for text |
| Fallback | `provider_chain`: requested provider first, then every available provider in a fixed order | Not configurable; subscription can fall to a paid API silently if the API is opted in |
| Transparency | Every call is a `generation_jobs` row with provider, model, usage, attempts; story page feed shows provider/model per step | Shows *what ran*, not *what was asked for*, whether Fable chose it, or that a fallback happened; subscription "cost" is a notional number from the CLI |
| Workflow | `progress()` derives stage from files (world/cast/outline/chapters) and running jobs with readable labels, ETA | Four stages, no explanation of what the AI is doing or what is decided, no "needs you" state, nothing between cast and outline |
| Approvals | none; `write` runs world → cast → outline → every chapter in one go | No gates, no modes, no approval history, no change-impact for characters |
| Characters | `cast.json`: name, role, age, personality, look, voice, expressions; editable only by re-running `cast` or `improve` | No structured profile, no approval/locking, no canon vs proposal, no versions, no AI revision of one character |
| Character art | neutral sprite generated from `look`; expressions edited from it with the neutral as reference; `review.pick` approves one candidate and rejects siblings; QC and near-duplicate checks | No concept candidates (n=1), no "master reference" concept, no consistency review against the approved identity, art prompt uses the pipeline's `look` string, not an approved visual spec |
| Revision | `improve` (feedback → revision plan → scenes), `refine` (one scene + note), `--provider/--model` per run | No "alternatives", no per-task model override in the UI, no diff/compare before applying |
| Narrative Dynamics / curves | **does not exist in this repository** | The brief lists it under "preserve"; nothing to preserve here. Not built in this phase |

## 2. The four questions, mapped

| Question | Where it is answered after this work |
|---|---|
| What is being created? | Story page → **Workflow** tab: current activity in plain words, what the AI is trying to do in this stage |
| What has been decided? | Workflow stage cards (decisions, approvals with dates), Character Studio approval badges, direction summary |
| What can I change? | Each stage card names its editable inputs; Character Studio edits per section; Improve/Refine with a model picker; AI & workflow settings |
| What needs my approval? | "Needs you" state on a stage, the approval mode, pending list on the story page and the hub badge |

## 3. Revised flow

Stages (real, derived from files + approvals): Idea → Creative direction → World → Characters → Story architecture
→ Chapter planning → Interactive routes → Writing → Narrative evaluation → Visual direction → Assets → Final review.

- **Modes:** `auto` (gates only before paid art), `guided` (default: approve characters, architecture, visual
  identities), `manual` (every stage).
- **Gate behaviour:** `write` runs until the next unapproved gate and stops with `paused_at` and a human action;
  approving resumes. Changing an approved thing shows impact first (chapters, routes, assets affected).
- **Models:** library-wide execution plan (order of provider/model pairs, paid-fallback policy, per-task kind
  overrides, managed vs manual), story overrides, and a per-run override on every action. Each job records
  requested vs used, who chose it, and fallbacks.
- **Characters:** profiles with identity / personality / narrative / visual sections, per-section approval and
  locks, proposals vs canon, versions; approved profiles write `cast.json` (the pipeline's input) and the image
  prompts; concept candidates → master reference → expressions with references → consistency review.

## 4. Build order (this initiative)

B execution plans + Anthropic API provider → C workflow stages, gates, activity → D Character Studio →
E visual references → UI → tests → validation on *The Unrung*.
