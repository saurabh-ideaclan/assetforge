# 10 · Production workflow: Fable, Sonnet, Opus, and the gates

**Status:** v1, applies from Gate 1 onward. Every agent working on stories follows this. Fable (the lead) owns
every decision in the "Decides" column; delegation never moves ownership.

## 1. Roles

| Role | Does | Does not |
|---|---|---|
| **Fable (lead)** | Plans the work, writes briefs, picks the model per task, reads every delegated result against the source, resolves disagreements, makes the creative/product call, signs each gate, keeps the docs in `docs/narrative/` current | Accept a result unread; let a model approve its own work; write a whole season before a gate |
| **Sonnet** | Research with sources; structured audits of scripts (counts, taxonomies, continuity tables); first drafts of supporting documents; chapter prose drafts to an approved beat sheet; mechanical repairs (split long boxes, fix an expression) | Judge whether a premise is compelling; design branch architecture; approve a chapter |
| **Opus** | Premise critique; season architecture; design of route-defining choices and ending gates; adversarial editorial review of a chapter or a beat sheet; hard character/motivation problems; rewriting a scene that failed review twice | Routine drafting; counting; research summaries; anything the linter can measure |
| **Linter / validator (code)** | Hard structural rules, measured, before any model review | Judge quality |

Rule of thumb: **Sonnet produces, Opus judges, code measures, Fable decides.** Any task where a wrong answer
costs a rewrite of more than one chapter goes to Opus or to Fable directly.

## 2. Every delegated task

- A brief states: goal, the files to read, what "done" looks like, the output path, the tags to use
  ([VERIFIED]/[EDITORIAL]/[ASSUMPTION] for research; [MEASURED]/[READ]/[HYPOTHESIS] for audits), and what the
  agent must not do (no rewrites, no invented numbers, no other files).
- Fable reads the output against the source material before using it. A claim without evidence is dropped.
- Two models are only asked the same question when independent disagreement is the point (a premise verdict,
  a gate review). Never for research or drafting.
- Model outputs are stored under `docs/narrative/reviews/` with the model name in the filename, so later
  agents know whose judgement they are reading.

## 3. The story production line (per story)

| Stage | Who | Output | Gate |
|---|---|---|---|
| S0 Premise brief | Fable, Opus critique | Level-1 sheet (audience fantasy, genre promise, protagonist, central conflict, hook, why this story) | **G-Premise:** owner approves the premise and audience |
| S1 Season architecture | Opus draft, Fable edit | Level-2 sheet: milestones, arcs, reversals, stat design, route map, ending gates with earned paths | **G-Season:** Fable signs; owner sees the route map |
| S2 Chapter beat sheets (first 3) | Fable + Opus | Per chapter: open-on-a-turn, scenes/cuts, choice list with type/cost/memory, callback, CG moment, cliffhanger question, unlock position | **G-Beats:** Opus adversarial review; Fable signs |
| S3 Chapter drafts | Sonnet (pipeline) to the beat sheet | Script nodes | Linter (hard rules) → independent review (rubric, Opus or Fable) → fix round → **G-Chapter** |
| S4 Vertical slice | Fable | 3 chapters playable, chapter-end cards, consequence feedback | **G-Slice:** owner plays it; reader test plan agreed |
| S5 Reader test | owner + Fable | Funnel data (start → ch1 end → ch2 start → ch3 end), choice distribution, survey | **G-Pilot:** continue to season, or revise premise |
| S6 Season | pipeline per chapter, gates per chapter | Full story | per-chapter G-Chapter |
| S7 Art, sound, export | pipeline | Assets, build | after G-Chapter for the chapters concerned |

No stage starts before the previous gate is signed. Art is never generated for an unsigned chapter.

## 4. Review independence

- The model that drafted a chapter never reviews it. The pipeline records the drafting model per job
  (`generation_jobs.request.model`), so the review step can pick a different one.
- A chapter review uses the rubric in `07-editorial-quality-rubric.md` and must cite lines.
- A chapter can pass the linter and fail review. The linter is a floor, not an approval.
- Two consecutive failed fix rounds escalate to Fable, who re-designs the beat sheet rather than asking for
  a third prose pass.

## 5. Approval gates for this initiative

| Gate | Decision the owner makes | Material |
|---|---|---|
| **Gate 1 (now)** | Accept the diagnosis; choose the pilot track (Monsoon Signal re-architecture, or a commercial-genre premise, or both); confirm audience/rating | `01`, `02`, `03`, `08`, `README` |
| **Gate 2** | Approve the Story Design, Interactive Narrative and Engagement frameworks and the rubric | `04`–`07` |
| **Gate 3** | Approve the pilot's premise sheet, season architecture and three beat sheets | `09` |
| **Gate 4** | Approve the three written chapters for reader testing (and the art budget for them) | reviews + lint + build |
| **Gate 5** | Read the reader-test results; decide season vs. revise | test report |
