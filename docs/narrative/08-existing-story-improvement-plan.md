# 08 · Existing story improvement plan (prioritised)

**Scope:** what to preserve, improve, redesign or postpone in the current product, derived from
`01-product-audit.md`. Each item names the dependency and the gate it belongs to. Items marked *pipeline* are
changes to the writing system (they fix every future story); items marked *story* are changes to Monsoon
Signal (the candidate pilot).

## 1. Preserve

| What | Why |
|---|---|
| The story format (in-chapter choices, reactions, `when`, `place`, typed stats, gated exits, chapters of scenes) | It can express everything the benchmark needs; the player handles it |
| Multi-part chapter writing, resumable jobs, linter hard rules, validation, `check`, `improve` | Reliable; the floor is right, it just is not the ceiling |
| Character voices and banter in Monsoon Signal (Zoya, Pillai, Dev's "smug, not stupid") | The best thing in the current output; keep the cast and voices in any re-architecture |
| Monsoon Signal's world, cast and the A.R. initials discovery | Usable canon for the pilot if the owner keeps this story |
| Art/sound pipeline, player, app tooling | Not the problem; no spend here until the story passes |

## 2. Improve (keep the mechanism, raise what it does)

| # | Item | Kind | Fix | Depends on | Gate |
|---|---|---|---|---|---|
| I1 | Chapter-exit choices that all lead to the same scene with mismatched text | pipeline | Stop forcing the outline's choice list onto the scene end (`prompts._exit_rule`). An exit is either a real fork (different scenes) or a `jump`; the cliffhanger is a line, not a fake choice. Linter error: `fake_exit` | – | G2 |
| I2 | Forgotten decisions (confession offered three times) | pipeline | Outline declares **decision flags** with the chapter that sets them; the scene prompt lists "already decided" facts; linter error `reoffered_decision` when an option's text matches a flag already settable earlier; `when` guards required on repeat offers | I1 | G2 |
| I3 | Duplicated beats across chapters | pipeline | The last part of a chapter receives the next chapter's planned opening and is told not to write it; `check` gets an explicit "same event twice" question; linter warning on duplicated proper-noun events | – | G2 |
| I4 | Stat noise (98 % of options write a stat; `empathy` never read) | pipeline | Cap writes per chapter (≈ 1 per choice, not per option); the repair prompt no longer asks for a `set` on every inserted choice; linter error when a variable has writes and 0 reads by the final chapter (today a warning); `empathy` either read in a gate or deleted | I2 | G2 |
| I5 | Verbal tics and repeated metaphors | pipeline | A per-story "do not repeat" list fed to each part (phrases used in earlier chapters); linter warning on repeated 4-grams and on the same opening image twice | – | G2 |
| I6 | Only 1 CG in 9 chapters | pipeline | Beat sheet names the CG moment; linter warns when a non-ending chapter has none | beat sheets | G3 |
| I7 | Chapter openings on weather; endings on reflection | pipeline | Beat template: open on a turn or a line of dialogue (no establishing narration before box 2); end on a question/decision/reveal; rubric item with examples | `04` | G2 |
| I8 | `check` is advisory and self-reviewed | pipeline | Independent review step with the rubric, different model from the drafter, hard failures block `assemble` unless `--force` | `07` | G2 |
| I9 | "Whatever you chose" erases outcomes; callbacks keyed on aggregate stats produce false memories | pipeline | Linter error on "whatever/whichever you (chose\|decide\|played)"; callbacks must key on the decision flag (`when flag_x == 1`), aggregate stats only gate extremes; the scene prompt forbids narration that merges options | I2 | G2 |
| I10 | No continuity state between parts; threads dropped; timeline drifts | pipeline | A story-state ledger (timeline/deadline, objects and where they are, revealed facts, open threads, secrets the player may have told) is produced by each part, passed to the next part and chapter, and checked by `check` with explicit questions (duplicated events, dropped threads, deadline consistency) | – | G2 |
| I11 | Outline opens threads it never closes; the central question is answered mid-season | pipeline | Outline schema: per chapter `opens` / `closes` / `reversal` / `antagonist_move`; validator errors if a thread is never closed or the central question closes before the final act | D3 | G2 |

## 3. Redesign

| # | Item | Kind | What changes | Gate |
|---|---|---|---|---|
| D1 | Premise and audience defaults | pipeline + story | A Level-1 premise sheet is required before `world`; the wizard asks for audience fantasy, genre promise and stakes, not just "premise, genre, audience". Default rating stops being "teen" unless chosen. A premise critique (Opus) is part of `story init` | G1 decides the genre track; G2 the framework |
| D2 | Choice design | pipeline | Each planned chapter carries a choice list: type (expression / relationship / plot / route), the cost of each option, what it writes, and where it is read. Options must differ on an axis the reader can feel (not speak / ask / stay silent). Max one "silence" option per chapter | G2 (`05`) |
| D3 | Episode structure | pipeline | A chapter is 2–4 scenes with cuts (`place`), an emotional shape (peak and release), a CG, a cliffhanger with a question, and a recap-able "what you decided" | G2 (`04`) |
| D4 | Ending gates | pipeline | Gates designed as earned paths: each ending names the 2–3 decisions that lead to it, each foreshadowed by a `when` line in the chapter after the decision; the fallback ending must be satisfying on its own terms; replay value stated | G2 (`05`) |
| D5 | Monsoon Signal chapters 1–3 (if chosen as pilot) | story | Re-architect the beat sheets, not the prose: chapter 1 opens on the initials, the confession becomes one real decision with memory, chapter 2 gets a real fork (Dev's blueprint leak), chapter 3 ends on a question not a mood. Details in `09` after Gate 1 | G3 |
| D6 | Mystery pacing | story | Arjun's "alive and ashamed" reveal moves from chapter 5 to the final act; chapters 5–7 need a mid-season reversal that is not the answer; the blueprint-leak thread becomes the B-plot that carries the antagonist | G3 |
| D7 | Monsoon Signal canon fix (flagged for approval) | story | The world file's "Kabir's family lived here before" contradicts the secret-identity engine the chapters rely on. Either Kabir is genuinely new (change `world.history`) or the town *does* know and the drama is being the Rastogi boy in public. Opus and I recommend the first; it is a canon change and needs the owner's OK | G3 |

## 4. Build (new, after the story is right)

| # | Item | Depends on | Gate |
|---|---|---|---|
| B1 | Consequence feedback in the player: "Zoya will remember that" toasts on decision flags; visible relationship changes without numbers | D2 | G4 |
| B2 | End-of-chapter card: title, your decisions, teaser line, "next chapter" with the unlock | `06` | G4 |
| B3 | Free/locked chapter model and the unlock point (ad, coins, subscription) as data on the story | `06` | G4 |
| B4 | Analytics events: chapter start/finish, choice picked, time per chapter, unlock shown/accepted, drop-off | – | G4 (needed for the reader test) |
| B5 | Quality panel in the app: rubric results per chapter next to the linter's numbers | I8 | after G4 |

## 5. Postpone

| What | Why |
|---|---|
| Art for Monsoon Signal | No images until the three chapters pass review; the cast and look are stable, so nothing is lost by waiting |
| Premium (paid) choices | Not until the trust questions in `06` are answered with data; the data model can take a `premium` flag later |
| Ren'Py/Ink export, voice lines, lip-flap, story management features | Not on the path to a compelling pilot |
| Semantic search, other platform work | Unrelated to the narrative problem |
| Writing a new 10-chapter story with the current pipeline | Would reproduce W1–W7 at full length and cost |
