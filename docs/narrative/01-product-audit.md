# 01 · Current product audit

**Date:** 2026-11-10 · **Author:** Fable (lead) · **Status:** diagnosis for Gate 1
**Evidence:** the four stories the pipeline has written (`~/.assetforge/stories/`), the hand-made sample
`examples/kite-season`, the pipeline source (`src/assetforge/story/`), the player (`exporters/vn_player/`),
`docs/vn-benchmark-gap-analysis.md`, `docs/vn-run-findings.md`, and a measurement script run over every
script.json (numbers below). The independent Opus editorial review is in `reviews/opus-review-monsoon-signal.md`.

Tags: **[MEASURED]** counted from the files · **[READ]** my editorial judgement after reading the chapters ·
**[HYPOTHESIS]** needs real readers to settle.

## 1. What exists

| Layer | What it is | State |
|---|---|---|
| Story format | Scenes of nodes: `say/narrate/show/hide/place/music/sfx/cg/set/choice/branch/jump/ending`; in-chapter choices with reactions that rejoin; `when` conditions; hidden typed variables (relationship/personality/resource/flag/world) with min/max; gated `branch` exits; endings with `requires`; chapters that can hold several scenes | Done, validated, playable |
| Writing pipeline | init → world → cast → outline (`repair_outline`) → write each chapter in ~4 beat calls (~450 words each) → narrative linter → one targeted repair call → refine → continuity `check` → assemble (`script.json`, `notes.md`, `lint.json`) | Done; Monsoon Signal lints 9/9 green |
| Narrative linter | Per chapter: words, boxes, choices, first-choice box, longest stretch without a choice, callbacks, stat writes, long boxes. Hard rules: ≥ 75 % of target words, ≥ target−1 choices, first choice by box 15, never 400+ words without a choice, a callback from chapter 3 | Done |
| Direction inputs | `StoryConfig`: premise, genre, audience, tone, behaviour (author style), chapter_words, chapter_choices, endings; season arc scaled from a 10-chapter template | Done |
| Art + sound | Sprites per expression, backgrounds per time of day, CGs, ambience/music/SFX planned from the catalog | Done; Static Tomorrow has 116 assets, Monsoon Signal has none yet |
| Player | Chapter cards, saves, history, text speed/auto, draft stand-ins, toasts (only for save/load) | Done (demo quality) |
| App | Story hub, wizard, live tracker, chapters reader, Improve tab, Art & sound, Play | Done |
| Monetization | Nothing: no free/locked chapters, no unlock, no coins, no premium choices, no episode-end card, no analytics | **Absent** |
| Editorial QC | The linter (quantities) + `check` (continuity, model-judged, advisory) + `improve` (author feedback → revision plan) | Quantity-gated only |

Stories written so far: *The Fog Lantern* (GPT, 6 scenes), *Robotics* (Haiku, 8), *Static Tomorrow* (Sonnet, 15),
*Monsoon Signal* (Sonnet, 9, the only one written with the current beat/linter pipeline). All four are rated
"teen", all four are mysteries set in a misty Indian hill or harbour town; three of the four are "new kid in a
hill town + a vanished person + a radio/robot". The library also holds 154 assets for a project
`love-wasnt-in-the-plan` (sprites `mc`, `adrian`, `ethan`): a romance set made outside this story pipeline that
should be accounted for when the genre decision is made.

## 2. Measured [MEASURED]

| Metric | Fog Lantern | Robotics | Static Tomorrow | **Monsoon Signal** | Kite Season (hand-made) |
|---|---|---|---|---|---|
| Words / boxes | 1,207 / 103 | 1,747 / 152 | 4,567 / 310 | 9,755 / 653 | 266 / 24 |
| In-chapter choices | 0 | 0 | 0 | 45 | 7 |
| Chapter-exit choices | 2 | 3 | 3 | 5 | 0 |
| Exit choices where **every option goes to the same scene** | 0 | 1 | 0 | **5 of 5** | – |
| Options that are "say nothing / stay quiet / deflect / let it go" | 0 % | 0 % | 14 % | **18 % (25 of 140)** | 6 % |
| Options that change a stat | 4 of 4 | 2 of 6 | 3 of 7 | **137 of 140** | 15 of 17 |
| Options gated by `require` | 0 | 1 | 0 | 9 | 1 |
| Variables: writes → reads | 6 → 0 | 5 → 1 | 12 → 0 | 196 → 43 | 19 → 10 |
| Variables never read | all | 2 of 3 | all | `empathy` (39 writes, 0 reads) | none |
| CGs | 5 | 6 | 9 | **1** | 0 |
| Linter | n/a | n/a | n/a | 9/9 pass, avg 1,466 words, 6.9 choices | – |

Variable detail for Monsoon Signal (writes → reads): trust_zoya 45 → 13, bold 51 → 4, empathy 39 → 0,
trust_pillai 24 → 4, resolve 17 → 4, trust_dev 6 → 12, flag_told_zoya 8 → 4, flag_truth 6 → 2.

Repeated phrasing in Monsoon Signal (4-grams ×3+): "for the first time in two years", "you don't have to",
"mr pillai says nothing", "just so you know"; by hand: "the rice-color thing" ×3, "like a dropped tool" ×2,
"Bold. Reckless, but bold" ×2, "I contain multitudes" ×2, rain on the tin roof opens every chapter.

## 3. What works and must be preserved

- **The format can express the benchmark.** In-chapter choices with reactions, `when` lines, places, gated
  exits and typed stats are the right primitives; Kite Season proves the player handles them. Nothing here
  needs rebuilding. [READ]
- **Voice and banter.** Zoya ("My temper is a renewable resource, sir") and Pillai ("Pahadi doesn't hate. It
  merely holds a grudge") are distinct and likeable; Hindi-English code-switching is natural. The prose is
  clean, specific and never generic-AI-bland line by line. [READ]
- **Reliability of the pipeline.** Four-part chapter writing hits length; the linter's hard rules are
  effective at what they measure (words, density, first-choice position); repair rounds are kept only when
  better; validation catches broken links; everything is resumable and reproducible. [MEASURED]
- **Art and sound consistency** (Static Tomorrow run: 57/57 images, consistent characters across expressions
  and CGs; story-planned sound). [READ, from `vn-run-findings.md`]
- **Live progress, preview player, per-chapter reading in the app.** Good production tooling.

## 4. Confirmed weaknesses

Ordered by how much they damage the reader's reason to continue.

### W1 · Choices are frequent but almost none of them decide anything [MEASURED + READ]
- Every chapter-exit choice in Monsoon Signal is cosmetic: all three options jump to the same scene
  (`s1_arrival` → `s2_test` ×3, `s2_test` → `s2_reversal` ×3, …). The option texts are the outline's planned
  choices pasted onto a different moment: chapter 2 ends with Pahadi's log loading and offers "Match Dev's
  confidence and challenge him back / Stay diplomatic and compliment Crestline's work / Deflect with a joke".
  This is a pipeline artifact (`prompts._exit_rule` forces the planned choice list onto the scene end).
- 98 % of options move a hidden stat, so a stat change is not a decision, it is noise. `bold` moves 51 times
  and is read 4 times; `empathy` moves 39 times and is read never. The player cannot tell what mattered.
- 18 % of options are "say nothing / deflect". Nearly every choice is the same triad: speak / ask / stay
  silent. Options rarely have a cost, so there is nothing to weigh.
- Reads that do exist are mostly one-line flavour (`if trust_zoya >= 2: "You held your ground with Dev
  earlier"`). No scene, relationship state or available action changes.

### W2 · The story has no memory of the player's biggest choice [READ]
Kabir is offered "tell them Arjun was my brother" in chapter 1 (at the trophy), again in chapter 2 ("Tell her
now – the welding signature"), and again in chapter 3 ("Yeah. He was my brother"). If the player confessed in
chapter 1, chapters 2 and 3 play as if he had not. Only the chapter-3 exit sets `flag_told_zoya`. This is the
single most trust-destroying failure for a reader: the one choice they cared about is forgotten.

### W3 · Duplicated and mis-sequenced beats across chapter boundaries [READ]
Chapter 2's last part reboots Pahadi, finds the log "dated the night he disappeared" and ends on it; chapter 3
opens with "You said you'd actually look inside him today" and discovers the same log again. Cause: part 4 of
a chapter is written without the next chapter's plan and drifts into it; nothing checks for duplicated events.

### W4 · No episode design: chapters are long scenes, not episodes [READ]
- One location per chapter (the `place` node is used only as the opening header). No cuts, no change of
  rhythm.
- Chapter openings restate the weather ("Rain hits the tin roof…" ×5) instead of opening on a turn.
- Chapter endings are quiet reflections ("Tomorrow. The tower."), not cliffhangers with a question the reader
  must answer. The outline's `hook` fields are decent but the writer does not land them.
- 1 CG in nine chapters: the big moments (hearing Arjun's voice, the tower in the storm) get no image.
- Expression changes are fine, but there is no per-chapter emotional shape (no peak, no valley, no release).

### W5 · The premise pattern is literary coming-of-age, not addictive drama [READ, HYPOTHESIS for market]
All four stories: a gentle 15–16-year-old, a misty town, a vanished relative, a secret an adult will not tell,
a robotics/radio MacGuffin. Low stakes (qualify for regionals), a mild rival, a romance that never exceeds
banter, a mystery that is answered by chapter 5 (Arjun's logbook: alive, ashamed) with four chapters left.
There is no power fantasy, no transgression, no reversal of fortune, no "I must know what happens". Compare the
category leaders' catalogues (see `02-competitive-and-audience-research.md`): their top genres are romance
sub-genres, revenge, secret identity, contract marriage, campus and mystery with high personal stakes, written
for adult women. Our "teen" default and our premise generator sit outside that market. Whether a gentle
coming-of-age mystery can monetize on this platform is a **hypothesis that has not been tested and that the
current output does not support**.

### W6 · The endings are not earned legibly [READ + MEASURED]
Two endings; the "good" one requires `trust_zoya >= 4 and resolve >= 4 and flag_truth == 1`. The player
never learns these exist, nothing on screen signals progress toward them, and the fallback ending ("A New
Frequency") is written as consolation. A reader who lands on it does not know what they did wrong, so there is
no reason to replay. The repair logic (`repair_outline`) guarantees an ending is reachable; nothing guarantees
the gate is *fair* (reachable on a plausible path, foreshadowed, worth replaying for).

### W7 · The quality system measures quantity and approves its own work [MEASURED]
- The linter has no notion of hook, trade-off, cliffhanger, duplicated beat, repeated phrase, forgotten
  flag, or legible consequence. Monsoon Signal is 9/9 green and still has W1–W6.
- The repair prompt optimises to the linter's numbers ("insert a NEW short in-chapter choice… a `set` on a
  declared stat"), which is how 137 of 140 options came to carry stat changes.
- `check` (continuity) runs after the fact, is advisory, and did not catch W2 or W3.
- There is no independent review step and no reader-facing metric. The gap analysis already noted "hook
  ending" and "variable read/write balance" as needed checks; they were not implemented.

### W8 · The business model has no footprint in the product [MEASURED]
No free/locked chapter concept, no end-of-chapter card, no "X will remember that", no premium or locked option,
no coins, no analytics events (chapter start/finish, choice picked, drop-off). None of the engagement loop can
be measured, so none of the monetization questions can be answered today.

### W9 · Threads are opened and dropped; parts are written without a continuity ledger [READ, confirmed by the Opus review]
The blueprint leak (chapter 2), the locked cabinet and the antenna coil (chapter 3), and Arjun's recorded
voice (chapter 3's cliffhanger) are never mentioned again in chapters 4–5. The deadline is "ten weeks"
(ch 1), "six weeks" (ch 2) and "six hours" (ch 5). The notebook Pillai locks in his drawer in chapter 1 is
in Kabir's bag in chapter 4. The world file says Kabir's family lived in Nileshwar before the disappearance,
which makes the "new kid with a secret" premise impossible on its own terms. Cause: each ~450-word part is
written from the plan and the earlier parts of its own chapter, with no running state (timeline, objects,
revealed facts, open threads, what the player has already confessed).

### W10 · Outcomes are erased and memories are false [MEASURED + READ]
"Whatever you chose / whichever way you played it" appears seven times, each time collapsing the options
just offered (the lock-pick, the board-vs-notebook dilemma). Callbacks key on aggregate stats rather than the
choice itself, so `trust_zoya >= 2` plays "You held your ground with Dev" for a player who stayed silent and
`trust_dev <= 0` plays "you told him off" for a player who joked. The good ending's `flag_truth` is set only
by chapter 6–7 choices, so chapters 1–5 barely influence which ending the reader gets.

## 5. Hypotheses that need real readers [HYPOTHESIS]

| # | Hypothesis | Why it cannot be settled by reading |
|---|---|---|
| H1 | Readers will not pay or watch an ad after Monsoon Signal chapter 1 because nothing is at stake for *them* yet | Only a drop-off curve shows this |
| H2 | A commercial-genre premise (romance/revenge/secret identity) written by the same pipeline retains far better than the current coming-of-age mysteries | Needs an A/B of two pilots |
| H3 | Showing consequences ("Zoya will remember that", a changed scene) raises chapter-2 continuation more than adding choices does | Needs an experiment on the same chapter |
| H4 | 1,500 words / 6–8 choices per chapter (the benchmark we adopted) is the right size for *our* readers on *our* player | Benchmark comes from text-heavy apps; short-drama readers may want 800-word episodes |
| H5 | Three options per choice beats two for engagement | Writer guides say so; not measured here |
| H6 | The first unlock should sit after chapter 3, not 1 | Depends on how fast investment builds; needs funnel data |
| H7 | Zoya (the relationship), not the mystery, is why a reader would continue | A/B a romance-forward vs. mystery-forward chapter-1 ending |
| H8 | Readers experience the "neutral" ending as a bad ending they did not choose | Post-ending survey on the pilot |
| H9 | Knowing from the logline that the brother built the robot lowers chapter-1 completion (no surprise left) | A/B two loglines on the store/hub card |

## 6. Root causes (ranked)

| # | Root cause | Kind | Evidence | Most effective fix |
|---|---|---|---|---|
| R1 | Choices are generated to satisfy a density target, not designed as decisions with trade-offs and memory | process (prompt + linter + repair) | W1, W2, 98 % stat-noise, 5/5 fake exits | A choice-design spec per chapter (types, costs, what each option is *for*) written at outline time, and a linter that checks memory (flags set are read, confessions not re-offered) and exit variety; remove the forced exit list |
| R2 | No episode-level design: chapters are written as one long scene split into four calls, no cut structure, no landing of the hook | process (beat template + part writing) | W3, W4 | A per-chapter beat sheet with scenes/cuts, open-on-a-turn, end-on-a-question, a CG moment; part N sees the next chapter's opening and must not write it |
| R3 | Premise and audience default to gentle teen coming-of-age mystery; no market-facing fantasy | design (brief + wizard defaults + premise generation) | W5, four of four stories | A premise framework (audience fantasy, genre promise, stakes) and a genre decision at Gate 1 |
| R4 | Quality control is quantity-only and self-approving | process | W7 | An editorial rubric with hard failures, an independent review call (different model) before approval, and reader metrics |
| R5 | Consequences are invisible and endings are opaque | design + player | W1, W6, W8, W10 | Callbacks keyed on the choice flag, not aggregate trust; consequence feedback in the player (memory toasts, changed scenes); ending gates designed as *earned paths* with foreshadowing; an end-of-chapter card |
| R6 | No continuity state between the calls that write a chapter, and no thread ledger in the outline | process | W3, W9 | A story-state ledger (timeline, objects, revealed facts, open threads, confessed secrets) passed to every part and updated after it; the outline tracks every thread to the chapter that closes it |

The Opus review (`reviews/opus-review-monsoon-signal.md`) ranks the same causes: premise brief → outline
without escalation or thread tracking → parts without continuity state → choices written to a count →
decorative stats with false memories. I agree with its ranking and adopt it; the one place I differ is that I
treat the premise question as a Gate 1 business decision rather than a pure writing fix, because the current
stories are not failing at being coming-of-age mysteries, they are failing at being the product the business
model needs.

## 7. What to solve before any art or UI spend

1. R3 (what story we are telling and for whom): it decides every later asset.
2. R1 + R2 (choices and episode shape): the pilot's beat sheets.
3. R4 (an independent gate): otherwise the next 1,500-word chapter will lint green and still fail.
4. Only then: player consequence feedback (R5), chapter-end card, unlock point, analytics (W8).
Art for the pilot comes after the three chapters pass editorial review on beat sheets and text.
