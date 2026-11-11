# 23. Narrative clarity: why chapter 1 was hard to follow, and the gates that fix it

The owner's brief of 2026-11-11 ("Critical Narrative Clarity, Natural Dialogue & Reader Comprehension"): the plan
had become good and the written chapter had not. The case study is chapter 1 of *Everyone Owes Me*. This document
is the diagnosis, the production faults behind it, the workflow built in answer, and the before/after on chapter 1.

## 1. Diagnosis of chapter 1 (version 1)

Two readings agree. The director's own read of the chapter file, then a cold read by Sonnet given nothing but the
player-facing text (`clarity.reader_view`: no ids, no conditions, no bible), answering the brief's ten questions per
scene section. The cold read's verdict was `revise`, with 21 concrete points:

| section | first-time reader can follow? | why not |
|---|---|---|
| the trial booth (lines 1–31) | no | "Measure", "NULL", the fee kept, the refusal slip and the cleanup trade are all used before the reader knows what any of them is; the clerk writing the father's name is a wound the reader cannot feel because nothing has said who Tomas Hale is |
| the yard fence, Cato's spark (32–61) | no | a trial badge "seven", a spark that comes from nowhere, nobody says what a spark costs |
| Hale Repair, the accounts (62–129) | **yes** | the money is concrete (a cracked washer, an empty drawer, a screwdriver cut into six squares); every choice's trade-off is on the page |
| the yard at dusk, the Seam (130–182) | no | "Seam" arrives with no grounding; Mara shouting "Kell!" assumes she knows a man the reader has never met; the cold fingers are a hook with nothing to hang on |

Seven terms used without context (NULL, Measure, Toll, the badge number, the spark, the Warden's coat, Seam); two
dialogue exchanges that do not answer the line before; two actions the reader cannot explain; one choice (how to
react as the floor moves) made before the reader can see how much danger there is; three questions that were
friction, not curiosity: why the father's name disqualifies the son, what a Measure score means long-term, whether
Mara knows Kell.

What was right: the writing is concrete, the choices are real, the family scene works, hardship is shown in
events, and the chapter-end hook is good. Nothing in the plot had to change.

## 2. The production faults behind it

The chapter was written by the brief the pipeline gives the writer, and the brief caused the symptoms:

1. **Every box capped at one or two sentences, 30 words.** That rule produced the coded fragments: a writer told
   to compress will drop the sentence that explains.
2. **A choice every 150–200 words.** The benchmark asks 6 choices in 1,500 words (one per 250); the prompt asked
   for more, so scenes ran to the next choice instead of letting a conversation land.
3. **The bible handed over as author knowledge.** The writer got the world as JSON and nothing about what the
   reader has been shown, so characters spoke as if the player had read the planning files.
4. **No dialogue continuity rule.** "Vivid, specific, memorable" with no "answer the line before" yields
   dramatic one-liners.
5. **No choice-setup rule.** The linter checked that choices exist and when; nothing checked that the reader knew
   what was being decided.
6. **One gate, the linter.** Word counts, choice counts, walls, callbacks: all shape, no comprehension. A coherent
   outline passed straight into a script nobody had read as a reader.

## 3. The workflow now (clarity without simplification)

Three separate gates, because a coherent outline is not a readable script, and a readable script is not yet a
playable experience. All in `src/assetforge/story/clarity.py`; the standards in `prompts.py` (PROMPT_VERSION 5).

- **Stage A, scene planning: the knowledge model.** `terms_plan` lists every fictional term the reader must
  understand, the first chapter whose scene depends on it, how it reaches the reader there (an action, an object, a
  line someone would say) and what stays concealed until when. For this story: 16 terms (Measure, Null, Undertow and
  Toll in chapter 1; Tithe, Bearing, Ledger, Weight in chapter 2; the collector, Selene and the birth Weight in
  chapter 3; Drift, Calling, Settlement later). The writer's prompt gets the slice for its chapter: already met /
  introduce now, by this means / not yet.
- **Stage B, drafting: the standards.** Boxes of one to three sentences up to ~45 words, varied; choices 200–300
  words apart, and before each one the reader already knows what is decided, why it matters now and the immediate
  trade-off; `CLARITY_RULES`: a term is understood the first time a scene depends on it, through what people do and
  say; every line answers the line before; plain speech most of the time; cause and effect visible; emotion on the
  page; transitions name where, when and who. Perspective fixed and documented: second person, present tense, spoken
  lines labelled (`clarity.PERSPECTIVE`).
- **Stages C–F, the cold read.** `audit` gives a model (Sonnet) the reader view of the chapter and of the
  chapters before it on the path, and nothing else. It answers the ten first-time-reader questions per section,
  checks each term for context, walks the dialogue exchange by exchange, checks every choice's stakes, separates
  intentional mystery from friction, and proposes the exact fix per passage. Verdict `revise` when any section is
  not easy, any choice is not understood, or three or more dialogue/continuity faults. Stored in
  `clarity/<scene>.json`: the per-chapter clarity report the brief asks for, with line numbers.
- **Stage G, revision.** `revise` rewrites the chapter from the report's fixes (Opus), keeping plot, choices,
  effects and ending; the linter and a second cold read decide whether the revision is kept.
- **Stage H, the rendered novel.** `leaks` finds ids, conditions or markup in player-facing text; the preview is
  played through every branch of the revised chapter.
- **Inside `steps.write`** every future chapter gets the audit after the linter and one revision on `revise`
  (`_clarity_gate`), so the standard applies automatically. The outline being coherent no longer approves a chapter.

## 4. Chapter 1: before and after

Opus revised the chapter from the report's 21 points plus the knowledge block and the director's notes; Sonnet
read the result cold again. Verdict `ok`: all four sections easy, 8 points left (one term, "the Firm", and one
friction question: why the clerk wants the slip back), every choice understood, no leaks. The linter still passes
(1,897 words, 8 choices, first choice at box 13). All three routes play through in the preview with no metadata
in the text. The plot, every choice and its effects, and the ending are unchanged.

**The opening.** Before (v1, lines 1–6):

> The trial clerk pushes your work gloves back through the slot. He keeps the entry fee. / Behind him, a board
> lists assistant wages: eighteen coins per shift. Your mother's parts bill is folded inside your glove. /
> Noor: He hasn't even touched a winch. Let him try. / The clerk taps NULL on your Registry card.

After (v2, lines 1–11):

> Trial day at the Firm's yard. Under a banner reading REGISTRY OFFICE, applicants line up to show their Measure
> and win a paid place on a Warden crew. / A Measure is the number the Registry gives you: how much power you may
> borrow. The board chalks the day's rankings. You are here for the assistant job that needs only a one. / You
> slide your card and the entry fee through the slot. Your mother's parts bill is folded inside your glove, the
> reason you need eighteen coins a shift. / … / He taps one word on the card: NULL. No number at all. / Eli: Null
> still has two hands. / A clerk in a grey coat snorts. 'He won't last past the first lift. A Null can't borrow a
> thing.' / Noor: Null just means no shift board will hire him. It doesn't mean he's useless. Let him try a winch.

The reader now knows where they are, what a Measure is, why Eli is there and what NULL costs him, before the
first choice. One line (2) is more expository than the standard wants; it buys the rest of the chapter.

**The father's name.** Before: "Under REASON, he has written your father's name beside your Measure." After adds:
"You go still. Everyone in Calder knows the name. Tomas Hale, the assessor jailed for faking Registry readings for
money." and "A faker's son, the slur goes. Can't be trusted near the trials." The wound is felt, and what Tomas
did stays as vague as the season needs.

**The Toll.** Before: Cato's spark burns a buckle; nothing says what power costs. After: the attendant at the
blocks ("Lift past your Measure, the Undertow takes payment. Memory, warmth, years. They call it the Toll."), then
Cato loses Noor's name mid-sentence and Eli names it: "You forgot her name. That's the Toll." The rule the whole
story runs on is learned by watching someone pay it.

**The Seam.** Before: the floor lifts, "A Seam. Eli, back away." After, Mara at the shop door: "Stay clear of the
open ground, that's where the Seams drop"; at the yard the crane, dust from a crack, a bolt rolling uphill; a
Warden explained in one clause as he appears; Mara: "Kell! I didn't know you worked this yard. Behind you!";
Noor: "A Seam. I've heard of them. Never seen one." The known danger arrives instead of a word.

**Planted for chapter 3's cost.** The radio Eli's father taught him to fix ("the first thing his hands and yours
ever mended together") and Noor's laugh ("quick and real, the lamp shaking in her hand. For one second the
counting in your head stops.") now exist, so losing one later will hurt.

**Left open on purpose:** why Eli's card says NULL when he can do the work; what the cold in his fingers is.

## 5. Chapters 2 and 3, and every future chapter

The same gate ran on chapters 2 and 3 (results in `clarity/<scene>.json`). First round: chapter 2 (The Seam) 14 → 13 points after an Opus revision, kept, still `revise`; chapter 3 on the hidden route 17 → 9, kept, still `revise`; chapter 3 on the shared route 17 points, its revision discarded because it tripped the wall-of-text rule. Both branches of chapter 3 lacked context for Weight / stored claims, Oren and the bank loan. Second round (knowledge block, box rule stated harder): chapter 2 13 → 7 points, kept, still `revise` by the strict rule (three or more dialogue/continuity notes); chapter 3 hidden route 9 → 4, `ok`; chapter 3 shared route: two Opus revisions read no better and were discarded, so it was rewritten from scratch under the new standards; the gate inside `write` read the fresh draft at 15 points, revised once and discarded the revision as no better. Final state of the pilot: chapter 1 `ok` (8 points), chapter 2 `revise` at 7 points with every section easy (the strict rule: three or more dialogue notes), chapter 3 hidden route `ok` (4), chapter 3 shared route `revise` (one section not easy: the laundrette rescue, where Weight, the stored claim and Oren arrive together). That route needs a different pass: a planned scene that introduces those three things one at a time, not another revision. From now on `steps.write` audits every chapter after the linter and revises once on
`revise`, with the knowledge block in the writer's prompt; the outline being coherent no longer approves a
chapter, and no evaluator sees the planning documents when it judges the text.

