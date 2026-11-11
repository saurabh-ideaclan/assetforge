# 25. Audit against the research handoff (PRD of 2026-11-11)

The handoff's first instruction is to audit the implementation against its PRD, preserve what works, implement P0
incrementally, use chapter 1 as the regression test and generate nothing more until the three-chapter pilot passes
its gates. This is that audit, with what was built in answer the same day.

## P0 artifacts and gates

| PRD artifact / gate | Exists as | State | Gap closed today |
|---|---|---|---|
| ProjectDirective (audience, language, world, genre, tradition, art, naming… independent of the operator) | `story/direction.py` `CreativeDirection`, presets, `system_rules` ("infer nothing from the operator") | done | — |
| StoryPromise | `StoryConfig.premise` + `direction.custom.canon` | partial (free text) | the payoff ledger now holds the promises as records |
| WorldRuleRegistry (actual rule / institutions believe / protagonist believes / exceptions / must understand by) | `world.json` rules were one text; `clarity.json` terms had meaning + concealed | gap | **`stakes.Rule` registry (`rules.json`)** with the four belief layers, drafted by a model from the terms plan |
| CharacterBible (+ acting profile) | `characters.json` profiles; `acting.json` profiles (doc 24) | done | — |
| ReaderKnowledgeState (unknown → revealed per term per chapter) | the terms plan knew only "first needed"; the cold read knew only "context given" | gap | **`stakes.knowledge_state`**: per term, per chapter in reading order, derived from the terms plan, the cold reads and the registry; `violations` when a chapter depends on a term the read found no context for |
| SeasonArc, ChapterBeatSheet | `outline.json` (hook, callback, summary per scene), `prompts.BEATS` | done | — |
| SceneCard (goal, obstacle, turn, payoff, new problem, required knowledge) | the cold read answers these *after* writing (`clarity.SectionRead`) | partial: not an input artifact | left as is: the knowledge block + plan summary are what the writer gets; a pre-write scene card is P1 |
| ChoiceSpec (immediate trade-off, state delta, acknowledgement, delayed consequence) | the linter counted choices; the cold read judged whether stakes were understood | gap on the state side | **`stakes.choice_report`**: every choice classed A/B/C; `same_delta`, `no_acknowledgement`, `only_at_ending`, `late_acknowledgement`, `all_expression`, `unverified` (partial scripts); attached to `lint.json` |
| BranchState / flags validation | `validate.py`: bad_goto, unknown_variable, unread_variable, no_path_to_ending, unreachable_scene, branch_no_fallback, mixed_choice… | done | — |
| PayoffLedger | missing | gap | **`stakes.Ledger` (`payoffs.json`)**: promises drafted from the outline; advanced from each cold read's new `tensions_changed` / `opened`; `no_payoff` per chapter; `stale` promises |
| NarrativeCurves planned + evaluated, cited to scenes | `story/dynamics.py` (six tracks, planned vs estimated, milestones, deviations) | done; curves are labelled estimates | — |
| ColdReaderQA (player-visible text only; the nine questions) | `story/clarity.py` `audit` (reader view only; ten answers per section; terms; dialogue; motivation; choices; continuity; emotion; mysteries) | done | + payoff question (tensions changed / opened) |
| Dialogue adjacency QA | inside the cold read (`dialogue` passages: a line that does not answer the line before, cryptic compression, dramatic one-liners) | done | — |
| EditorialDecision / independent review | `story/evaluate.py` (fifteen questions, a different model from the writer); workflow approvals in `workflow.json` | done | — |
| No silent paid fallback | `llm/plans.py` `paid_fallback` allow / confirm / never; `needs_confirmation` surfaces in the app | done | — |
| Model routing: Sonnet for checks, Opus for architecture and rewrites, per-task overrides | `managed_steps` (Opus for `HEAVY_KINDS`), per-task plan overrides, model pickers on runs; the clarity gate audits with Sonnet and revises with Opus | done | — |
| Audio comfort (silence valid, restrained BGM, separate volumes, loops safe) | doc 21 stabilization: budgets halved, breathing music, crossfades, volume panel | done | block-streamed encodes (libsndfile crash) |
| Character Studio as performance-needs view | doc 24: profile, sprites with scene usage and verdicts, performances with justification and actions | done (P1 item delivered early) | — |

## What the new gates found in the pilot (chapters 1–6, 2026-11-11)

- **Choice QA**: 64 choices (4 expression, 57 state, 3 macro). Three stats, `openness`, `self_preservation` and
  `trust_idris`, are written by dozens of choices and read by no written chapter; `trust_mara` is read only at
  the ending gate. The flags (`kept_trial_receipt`, `told_mara`, `penny_evidence`…) are acknowledged. This is the
  "stat noise" fault the pipeline memory already named: the writer obeys "set a stat" and ignores "read it". The
  acknowledgement pass (below) is the fix for the written chapters; the writer prompt already demands callbacks,
  and the choice report now makes the omission visible at `assemble` time.
- **Payoff ledger**: filled in by the run logged in section 3.
- **Knowledge state**: filled in by the run logged in section 3.

## Pilot structure versus the PRD's three-chapter sketch

The PRD sketches chapter 2's disclosure choice as "tell Noor / hide / report to the Registry" and chapter 3 as
the family history meeting a faction. The approved outline already carries that intent with Mara in the
confidant's seat (chapter 2's exit: tell Mara / keep Bearing between us), the Tithe as the faction, the receipt
and disclosure choices paying off in chapter 3, and the second awakening seeded in chapter 3 and delivered in
chapter 7. The canon is not changed by this audit; the PRD's sketch is treated as confirmation of the design,
not a replacement outline.

## Section 3: run log (2026-11-11, Sonnet drafts and reads, Opus rewrites)

**Payoff ledger.** Eleven promises drafted from the outline (Eli's Null reading, the father's disgrace, the shop's
survival, Weight and memory, Idris's mentorship, Penny's freedom, Cato's debt, Selene's threat, the unexplained
power, Tomas's hidden role, Pike's offer). Every cold read now names the tensions the chapter changed and the
questions it opened; a model matches those readings to promise ids (`payoffs.match`), because word overlap
alone called nine promises stale. Result over the eight written chapters: no chapter without a payoff; every
promise introduced early is advancing; the one stale promise is Idris's mentorship, opened in four chapters and
never materially moved, which is a true note for the chapter-7 plan.

**Rule registry.** Twenty rules with the four belief layers. One entry the model got wrong (it described the
refusal slip as a paper from the father's trial) was corrected by hand; the registry is a draft for the owner to
approve, like the acting profile.

**Reader knowledge state.** Measure, Undertow and Toll are understood from chapter 1 on; Null is
"intentionally misunderstood" from chapter 1 until Settlement reveals it; Bearing, the Ledger and Weight arrive
in chapter 2 as intended. Two violations, both in chapter 5: Director Pike (unpaid route) and the stored claim
(rostered route) are depended on without context, the same chapter the cold read already flagged, which the
re-plan of chapter 5 must fix.

**Choice QA and the acknowledgement pass.** The three personality and trust stats were written by dozens of
choices and read nowhere. An Opus pass added `when`-gated reactions from Noor, Mara and Idris: kept on the
shared route of chapter 3 (clarity 8 → 4 points) and chapter 4 (5 → 5); on chapter 2 and the hidden route of
chapter 3 the first attempt made a wall of text and was discarded, and a stricter attempt (three single lines
placed after existing choices) was run; its result is recorded below. The decision flags were already
acknowledged; `trust_mara` is read only by the ending gate and needs an interim read.

