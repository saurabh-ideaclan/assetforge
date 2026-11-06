# Interactive-story benchmark vs AssetForge: gap analysis

**Goal:** stories as engaging as the category leaders (Choices, Episode, Chapters, Choice of Games). We copy their
*craft patterns*, never a specific story.

**How this was made:**
- Research by Claude Sonnet. I re-checked the two primary sources (marked ✔) word for word.
- Our numbers are measured from the three stories AssetForge has written: *Static Tomorrow* (Claude Sonnet),
  *The Fog Lantern* (GPT-5.6) and the robotics story (Claude Haiku).

## 1. The benchmark

| Metric | Category leaders | Source |
|---|---|---|
| Chapter length | **~2,000 words** (chapters 1–4), ~1,500 after (500–800 script lines) | Episode writer guide ✔ |
| Choice frequency | **≥ 1 choice per 15–20 lines of dialogue** | Episode ✔ |
| Words between choices | input after **~100–200 words**; **never 400+** | Choice of Games ✔ |
| Scene-changing choice | a choice that majorly changes scenes **at least every other chapter** | Episode ✔ |
| Dialogue box | **2–3 lines of text max**, a reaction or expression on every line, quick scene changes | Episode ✔ |
| Consequences | **delayed branching**: "if" sections in later chapters show how earlier choices matter (CoG's #1 mistake when missing) | Choice of Games ✔ |
| Stats | at least one from each category (skills, personality, morality, world, resources, goals); hidden from the player | Choice of Games ✔ / Ingold (secondary) |
| Gating | set early, check later; gate options only at extremes (< 30% / > 70%) | Emily Short |
| Option quality | every option worth choosing; 3 options on separate axes (warm / honest / guarded) | Choice of Games |
| Retention | weekly chapters, each ends with a reason to come back (Pixelberry: day-7 retention above 50%) | PocketGamer.biz |
| Choice mix per ~100 lines (estimate) | ~3 flavour, ~2 relationship, ~1 establishing, ~1 test (from ch. 4), ~1 plot branch per 1–2 chapters, climax choices read the state | research synthesis |

## 2. What we produce today (measured)

| Metric | Static Tomorrow | Fog Lantern | Robotics | Benchmark | Gap |
|---|---|---|---|---|---|
| Words per chapter | 304 | 201 | 218 | ~1,500–2,000 | **5–9× too short** |
| Reading time per chapter | 1.5 min | 1.1 min | 1.1 min | ~6–10 min | **5–7×** |
| Choices per chapter | **0.20** | 0.33 | 0.38 | ~6–8 (1 per 15–20 lines) | **~20–30× too few** |
| Choices inside a chapter | **0** | 0 | 0 | most choices | **missing feature** |
| Lines before the first choice | 42 | 19 | 25 | ≤ 15–20 | too late |
| Words between choices | ~1,500 | ~400 | ~450 | ≤ 200 (never 400) | **wall of text** |
| Variables read later | **0 of 12 writes** | 0 of 6 | 1 of 5 | every chapter has a callback | **no consequences** |
| Endings gated by earlier play | no (one free final choice) | no | partly | climax reads accumulated state | missing |
| Chapters ending on a hook | 2/15 | 5/6 | 1/8 | every chapter (estimate) | inconsistent |
| Options per choice | 2–3 | 2 | 2 | 3, on separate axes | weak |
| Locations per chapter | 1 | 1 | 1 | several quick scene changes | missing feature |
| Expression changes | 13–24 per chapter | ok | ok | on most lines | **fine** |
| CGs per chapter | 0.6 | 0.8 | 0.75 | 1–2 (estimate) | ok |

What we already do well:
- world-building and character voice;
- consistent cast and art;
- expressions;
- clean validation;
- the art and sound pipeline.

The gaps are in **the structure of play**, not the prose.

## 3. What the system needs (by impact)

**Format (the system can't express these yet):**
1. **Choices inside a chapter that rejoin.** Today a choice may only end a scene, so every choice is a scene split.
   Add choice options with their own 2–6-line *reaction* that then continues the chapter: branch and bottleneck.
   This is the single biggest gap, because 20–30× more choices is impossible without it.
2. **Conditional content.** "If" lines and options shown only when a variable holds (e.g. `trust_meher >= 2`), so later
   chapters can call back to earlier choices, which is delayed branching.
3. **Scene changes inside a chapter**: a background / location / time change node, so a chapter can move quickly
   between 2–3 places.
4. **A designed stat model**:
   - relationship points per main character;
   - 2–3 personality axes (e.g. bold ↔ careful, honest ↔ guarded);
   - one resource or world variable;
   - all hidden.

   The writer declares them in the outline with their purpose and the thresholds used to gate endings.
5. **Ending gates**: endings that read the accumulated stats, plus 1–2 climax choices.
6. **Chapter vs scene**: a chapter is the unit players read (6–8 min); it holds several scenes. Today one scene is
   one chapter.
7. *(optional)* Protagonist name and pronouns, inserted into text.

**Writing (prompts, planning and checks):**

8. **A chapter beat template** (from the research), with a target of 60–90 dialogue boxes:
   1. hook (3–6 boxes);
   2. first choice by box 8;
   3. setup;
   4. complication;
   5. midpoint test/branch with a reaction;
   6. a **callback** to an earlier choice;
   7. escalation;
   8. a **cliffhanger**.
9. **Write long chapters reliably**: write each chapter in 2–3 beat calls (each ~25–35 boxes), not one call. A single
   call drifts short (we get 15–25 lines).
10. **Choice design rules in the prompt**:
    - 3 options on separate axes, each worth choosing;
    - a mix of flavour, relationship, establishing, test and branch choices per the table;
    - gate only at extremes.
11. **A narrative linter**: an automatic check per chapter that measures what's in section 2 (words between
    choices, first-choice position, choices per chapter, callbacks, variable read/write balance, hook ending,
    box length). It fails a chapter that breaks the hard rules (400+ words without a choice, no callback after
    chapter 3, an unused variable). A failed chapter goes into a targeted repair round, like scene validation today.
12. **Season arc**:
    - chapters 1–2 establish the world and the stats;
    - 3–4 hold the first test;
    - 5 is the midpoint reversal;
    - 6–7 bring consequences back;
    - 8 is the crisis;
    - 9 holds the climax choices;
    - 10 has 3–4 gated endings.

**Player and app:**

13. **Show consequences without numbers**: "Meher will remember that." toasts, and reaction lines when a stat
    changes.
14. **End-of-chapter card**: chapter title, a "your choices" recap, a teaser for the next chapter, and "Replay
    chapter".
15. **A story quality panel** in the app: the linter's numbers per chapter against the benchmark, with red/green and a
    "fix this chapter" button (`improve` with the linter's findings as feedback).
16. Dialogue boxes capped at about 2–3 lines; long lines split automatically.

**Not needed for a generator** (a business choice): premium choices, keys and ads. The data model can allow a
`premium` flag on an option later.

## 4. Proposed upgrade path

| Phase | What | Done when |
|---|---|---|
| 1. Format | Gaps 1–6: in-chapter choices with reactions, conditional lines/options, location nodes, stat model, ending gates, chapters made of scenes. Validator, preview and player support them | A hand-made sample chapter with 7 choices, 2 callbacks and 2 locations plays correctly |
| 2. Writing | Gaps 8–12: beat template, multi-call chapters, choice rules, narrative linter and repair, season arc | A generated chapter meets the benchmark: ≥ 1,500 words, ≥ 6 choices, first choice by line 15, no 400-word stretch, ≥ 1 callback from chapter 3 on, hook ending |
| 3. Player/app | Gaps 13–16: consequence toasts, chapter-end card, quality panel, box length | Visible in the app |
| 4. Benchmark story | An **original** 10-chapter teen story written to this formula, measured with the linter, then played end to end | Every chapter green on the quality panel; endings depend on earlier choices |

Phase 1 is the foundation: without in-chapter choices and conditions, no prompt can reach the benchmark.
