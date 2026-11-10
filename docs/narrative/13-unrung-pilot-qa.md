# 13 · Pilot QA: *The Unrung*, chapters 1–3

**Date:** 2026-11-10 · **Story:** `~/.assetforge/stories/the-unrung/` · **Writer:** claude-cli / sonnet (subscription)
**Direction:** preset `medieval-regression-power-fantasy` + the Opus canon from `09-pilot-story-design.md`.
**Method:** linter (`lint_scene`), the direction QA script (words, options, flags, idioms, real-world references,
repeated phrases), and a read of each chapter. [MEASURED] unless marked [READ].

## 1. Did the settings reach the story?

| Setting | Evidence in the chapters | Verdict |
|---|---|---|
| Global audience, no inferred culture | Zero real-world place, religion or regional words in 3 chapters; names only from the naming guide (Lucan, Nell, Maren, Conrad, Aurelian, Hesketh, Wat) | **held** |
| Simple English | Longest box 21–48 words; 0–1 dialogue lines over 25 words; no modern idioms matched | **held** |
| Medieval world, Western-fantasy names | Bellhold, Reckoning, rope-yard, forks, Marsh Pens; name order given-family | **held** |
| Korean web-novel techniques | Rank made explicit by chapter 3 (Unrung → First/Second Bell); onlookers react at the bell; chapter endings are a reveal (dead forks), a question (Maren's lantern) and a summons (Sable's seal) | **held** |
| Choices meaningful, remembered | All 19 choices are in-chapter with 3 options; chapter 2 sets `flag_wat_taken`, chapter 3 sets `flag_rang_second` and reads earlier choices in 13 `when` lines; "say nothing" options down to 1–2 per chapter; no "whatever you chose" | **mostly held** |
| Every chapter one costly decision | Chapter 1's decision (save Conrad / only the wave) is written as a choice but **sets no `flag_saved_conrad`**, so chapters 2–3 cannot read it | **missed** |
| A CG per chapter | 0 `cg` nodes in 3 chapters | **missed** |
| Day counter opener | Chapters open inside a moment (rope burns, boots in the yard, the Cracked Bell) but never with "Day X · N days" | **missed** (the writer kept the better half of the instruction) |
| Stat discipline | `pride` written 10 / 8 / 10 times; `hearing` once | **weak** |
| Cast expressions | Hesketh used `surprised`/`sad` the cast never declared (3 fallbacks to neutral) | **gap in the cast step** |

## 2. What the system caught and fixed on its own

- The cast step first dropped the canon characters (Nell, Hesketh, Felix): the cast prompt did not receive the
  canon. Fixed (`prompts.cast_prompt` carries it); the re-run produced all eight.
- The outline referenced characters the cast lacked and failed: `repair_outline` now unties such stats instead
  of refusing.
- Seven planned chapter-exit "choices" had one destination: the repair turned them into plain jumps.
- The linter-repair call for chapter 3 wrote "test line one/two/three" to move the first choice up, and the
  repair was accepted because it reduced the error count. New hard rule `placeholder_text`; the chapter was
  rewritten and now opens on the Cracked Bell.

## 3. Numbers

| Chapter | Words (linter) | Boxes | Choices | First choice box | Longest stretch | Callbacks (`when`) | Flags set | Lint |
|---|---|---|---|---|---|---|---|---|
| 1 Unrung | 1,651 | 98 | 7 | 7 | 298 | 1 | none | ok (warn: no_cg, no_decision_flag) |
| 2 The Listening | 1,349 | 69 | 6 | 6 | 366 | 2 | flag_wat_taken | ok (warn: no_cg, long_box) |
| 3 First Bell | 1,513 | 96 | 6 | 12 | 379 | 13 | flag_rang_second | ok (warn: no_cg, two_options) |

## 4. Read [READ]

- Chapter 1 opens on the rope and the memory of the axe, reaches the bell-shock and the first Hush fast, and
  ends on "every fork in the square has just gone dead". It is the strongest opening the pipeline has produced.
- Chapter 2 is Hesketh's chapter: the hand-signs are a real voice, the fork sweep is a pressure scene, and the
  lash decision is the first costly choice that the next chapter remembers.
- Chapter 3 pays off chapters 1–2 in thirteen conditional lines, rings the bell in public with onlookers
  reacting, and ends on Sable's seal. Maren's warmth stays tied to a question, as the design asked.
- Weaknesses to fix in the beat sheet, not the prose: chapter 1's decision must write its flag; the Hush's
  hearing cost is shown once only; no chapter marks a peak with a CG.

## 5. Next fixes (pipeline, small)

1. Scene prompt: name the chapter's planned flag explicitly (parse `flag_*` from the plan's summary/hook) and make
   `no_decision_flag` an error when the plan names one.
2. Scene prompt: one `cg` per non-ending chapter as a hard rule after the direction asks for it.
3. Cast step: expressions a writer may use come from the profile; the Character Studio should add `surprised`
   and `sad` for Hesketh before art.
4. Stat writes: cap personality writes per chapter (≤ 3) in the repair note.
