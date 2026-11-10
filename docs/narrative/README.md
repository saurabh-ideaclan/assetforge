# Narrative product initiative

Goal: turn the AI-written visual novels into interactive episodic drama that readers continue voluntarily on a
mobile platform (free opening chapters, unlocks by rewarded ad or coins, optional subscription). This folder is
the working set for that initiative; future agents reuse it for every story, any genre.

## Documents

| # | Document | Purpose | Status |
|---|---|---|---|
| 01 | [Current product audit](01-product-audit.md) | What exists, what works, measured weaknesses, root causes, hypotheses | **done** (Gate 1) |
| 02 | [Competitive and audience research](02-competitive-and-audience-research.md) | Evidence from Choices, Episode, Chapters, Romance Club, Choice of Games, short-drama apps; tagged VERIFIED / EDITORIAL / ASSUMPTION | **done** (Sonnet, checked by Fable) |
| 03 | [Narrative product strategy](03-narrative-product-strategy.md) | Audience promise, the five reasons we are not compelling, principles, differentiation | **done** (Gate 1) |
| 04 | Story design framework | Premise → season → episode → dialogue standards (Levels 1, 2, 3, 5) | after Gate 1 |
| 05 | Interactive narrative framework | Choice types, memory, branching/convergence, routes, ending gates (Level 4) | after Gate 1 |
| 06 | Engagement and monetization framework | Free chapters, unlock point, cliffhangers, trust rules, experiments | after Gate 1 |
| 07 | Editorial quality rubric | 15 criteria, failure examples, hard fails, review method, approval thresholds | after Gate 1 |
| 08 | [Existing story improvement plan](08-existing-story-improvement-plan.md) | Preserve / improve / redesign / build / postpone, with dependencies | **done** (Gate 1) |
| 09 | [Pilot story design](09-pilot-story-design.md) | The Power Fantasy pilot: premise sheet, power system, cast, season architecture, chapter 1–3 beat sheets, pipeline brief | **done** (Opus draft, Fable-reviewed) |
| 10 | [Production workflow](10-production-workflow.md) | Fable / Sonnet / Opus responsibilities, review independence, gates | **done** |
| 11 | [Genre and tradition research](11-genre-and-tradition-research.md) | Sources behind `story/genres.yaml` and `story/traditions.yaml`; what must be right for Korean-webnovel-style power fantasy in this format | **done** (Sonnet) |
| 12 | [Production UX audit](12-production-ux-audit.md) | What existed for providers, workflow, characters and approvals; the revised flow behind the AI plans, workflow gates and Character Studio | **done** |
| 13 | [Pilot QA: The Unrung](13-unrung-pilot-qa.md) | Chapters 1–3 measured against the direction: what held, what the writer ignored, what the system caught | **done** |

**Gate 1 outcome (2026-11-10, owner's brief):** the pilot is a *new* Power Fantasy story for a global adult
audience in simple English (medieval high fantasy, Western-inspired names, Korean web-novel techniques, manhwa
art), written through the new creative-direction system (`story/direction.py`, documented in `CLAUDE.md`).
Monsoon Signal stays as it is. The owner's own background must never shape a story: every story now carries an
explicit direction, and the writer is told to infer nothing from the operator.
| – | [reviews/](reviews/) | Model reviews, named by model, kept as evidence | ongoing |

## Roadmap and gates

```
Gate 1  diagnosis + strategy accepted; pilot track and audience chosen        ← we are here
   │
   ├─ M2  frameworks 04–07 (design + rubric), linter upgrades I1–I5, I8      (depends on Gate 1 genre decision)
   │        │
Gate 2  frameworks + rubric approved
   │
   ├─ M3  pilot: premise sheet, season architecture, 3 beat sheets (09)     (Opus critique on each)
   │
Gate 3  beat sheets approved
   │
   ├─ M4  write 3 chapters to the beat sheets; lint; independent review; fix; consequence feedback,
   │      chapter-end card, unlock point, analytics (B1–B4); then art for the 3 chapters only
   │
Gate 4  vertical slice approved for reader testing
   │
   ├─ M5  reader test (funnel, choice distribution, survey)
   │
Gate 5  season go / premise revise
```

Milestone 2 cannot start before Gate 1 because the audience/genre decision changes the frameworks' examples,
the premise sheet, and whether Monsoon Signal's canon is kept.

## Decisions needed at Gate 1

1. **Pilot track.** Recommended: re-architect **Monsoon Signal** chapters 1–3 as the methodology test (it is
   the only story written with the current pipeline, lints green, has a cast and voices worth keeping, has no
   art yet so nothing is wasted), **and** commission one commercial-genre premise sheet (Level 1 only, no
   chapters) so Gate 3 can compare them. See `03` §5 for the reasoning and the alternative.
2. **Audience and rating.** All current stories are "teen". The market evidence in `02` points at adult
   readers for the monetized apps. Decide the primary audience; it sets the premise framework's defaults.
3. **Budget for the pilot.** Text only until Gate 4; art for three chapters after Gate 4 (order of magnitude:
   the Static Tomorrow run cost $3.24 for 57 images).

## Where the files live

- Generated stories: `~/.assetforge/stories/<slug>/` (`story.json`, `outline.json`, `scenes/`, `script.json`,
  `notes.md` readable script, `lint.json`).
- Pipeline: `src/assetforge/story/` (`prompts.py`, `steps.py`, `lint.py`, `validate.py`, `models.py`).
- Player: `src/assetforge/exporters/vn_player/index.html`.
- Earlier analysis: `docs/vn-benchmark-gap-analysis.md`, `docs/vn-run-findings.md`.
