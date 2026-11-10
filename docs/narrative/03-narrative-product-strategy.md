# 03 · Narrative product strategy

**Status:** Gate 1 proposal. Sections 1–4 are Fable's diagnosis; section 5 is the pilot recommendation;
section 6 lists the market evidence this rests on (from `02-competitive-and-audience-research.md`) and what is
still assumption.

## 1. Our understanding of the product today

- **Product:** a mobile-first interactive visual novel platform. A reader gets 1–3 chapters free, then unlocks
  chapters with rewarded ads or coins; coins can be bought; a subscription removes the gate. Long-term value
  comes from many stories, genres and routes.
- **Content system:** AssetForge writes a whole branching novel from a one-line premise with the signed-in
  Claude/Codex/Gemini CLIs (world → cast → outline → chapters in beat parts → linter → repair → continuity
  check), then makes its sprites, backgrounds, CGs, ambience and music, and exports a web player. Chapters
  target 1,500 words and 6–8 choices with hidden stats and gated endings.
- **State of the narrative:** four stories written, one (Monsoon Signal) with the current pipeline. It passes
  every structural check and is pleasant to read, and it is not something a reader would pay to continue
  (audit `01`, review `reviews/opus-review-monsoon-signal.md`).
- **State of the business model in the product:** none of it exists yet (no gate, no unlock, no card, no
  analytics). That is fine: it should not be built until the story is worth gating.

## 2. The five most significant reasons the experience is not compelling

| # | Reason | Status | Evidence |
|---|---|---|---|
| 1 | **We are writing the wrong kind of story for this business.** Gentle teen coming-of-age mysteries with low external stakes, one template (misty Indian hill town, vanished relative, radio/robot) reused four times. The format's buyers come for a strong emotional fantasy: contested romance, secret identity, revenge, reversal of fortune, every episode a turn. | Confirmed as a pattern; its market effect is a **hypothesis** until a commercial-genre pilot is tested | `01` W5; `02` §2 |
| 2 | **Choices do not decide anything and the story forgets them.** Every exit choice is cosmetic; 98 % of options nudge a hidden stat; the one choice the reader cares about (confess) is offered seven times with no memory; "whatever you chose" erases dilemmas; callbacks produce false memories. | Confirmed | `01` W1, W2, W10 |
| 3 | **Chapters are not episodes.** One location, opening on weather, a quiet reflective ending, one CG in nine chapters, duplicated beats across chapter breaks, dropped threads, a drifting clock. There is no per-chapter shape (turn → escalation → peak → question). | Confirmed | `01` W3, W4, W9 |
| 4 | **No pressure: antagonist, romance and clock are decorative.** Dev appears once in five chapters and has no agenda; romance goes up in +1s with no obstacle; losing the qualifier costs nothing stated; the central mystery is answered by a note in chapter 5 of 9. | Confirmed | review §4 vi, v |
| 5 | **The quality system measures quantity and approves its own work.** The linter (words, density, first choice, callback count) is satisfied by a story with all of the above. The repair prompt optimises to those numbers, which is how the stat noise was created. No independent review, no reader metrics. | Confirmed | `01` W7 |

Hypotheses that are *not* confirmed and must be tested with readers: that a commercial-genre premise retains
better (H2); that visible consequences raise continuation more than more choices (H3); that 1,500-word
chapters are the right size for ad-gated episodes (H4); where the first unlock should sit (H6); that Zoya, not
the mystery, is the retention asset (H7). See `01` §5.

## 3. The audience promise

Working statement, to be sharpened at Gate 2 once the audience decision is made:

> **Every chapter turns the story and turns on you.** You will feel a want in the first minute, face a choice
> that costs something before the chapter is half done, see that the story remembers it, and leave each chapter
> on a question you need answered.

What "distinctly different from short-form drama" means for us: short drama delivers escalation through actors,
music and cuts; we deliver it through **agency with memory**. The reader is not watching someone be chosen,
betrayed or vindicated; they are the one choosing, and the story keeps the receipts. That is the only
advantage text-and-sprites has over video, so every design principle below protects it.

## 4. Narrative product principles

These guide every story in any genre. They are principles, not a template.

1. **Fantasy first.** A premise is accepted only when it states the reader's fantasy (what they get to be or
   have), the protagonist's want and its cost, an antagonist with an agenda, and what is lost on failure. "A boy
   joins a robotics team" fails this; "a boy hiding that he is the missing legend's brother, in a town that
   would turn on him, with the one girl who could expose him" passes.
2. **A turn in the first minute.** Chapter 1 opens inside a situation that is already wrong, not on weather.
   The reader knows what the protagonist wants and what stands in the way before the first choice.
3. **One decision per chapter that costs something, and the story remembers it.** Flavour and relationship
   choices keep the hands busy; one choice per chapter is a real trade-off whose consequence is *seen* within
   the next chapter and *felt* by the finale. Never re-offer a decision the reader already made.
4. **Consequences are shown, not scored.** Memory lines, changed scenes, changed availability, a "will remember
   that" cue. Hidden stats only gate extremes; decision flags carry the memory.
5. **Every chapter is an episode.** Open on a turn, 2–4 scenes with cuts, an escalation, a peak worth a CG, a
   release, and a cliffhanger that is a *question or a decision*, never a mood.
6. **Pressure from three directions.** An antagonist who acts every chapter or two, a relationship with an
   obstacle, a clock with a stated cost. The central question closes only in the final act; a smaller question
   opens and closes every chapter.
7. **Earned endings, legible paths.** Endings are reached by 2–3 named decisions, each foreshadowed after it is
   made; the fallback ending is satisfying on its own terms; the reader can say what they would do differently.
8. **Voice is non-negotiable.** Distinct diction per character, subtext over statement, no repeated metaphors
   across chapters, no narration that explains the emotion the line already carried.
9. **The free chapters are the best chapters.** Nothing is held back for the paid ones; the unlock sits after
   investment, on a question, never on a punishment.
10. **No one approves their own work.** Code measures, a different model reviews against the rubric, Fable
    decides, readers settle what reading cannot.

## 5. Pilot recommendation

**Recommended: a two-track Gate 1.**

- **Track A (methodology validation): re-architect Monsoon Signal chapters 1–3.** It is the only story the
  current pipeline wrote, it lints green (so every remaining fault is a real fault, not a length problem), the
  cast and voices are worth keeping, and it has no art yet, so nothing is wasted. Re-architecting its beat
  sheets proves whether the frameworks fix the confirmed weaknesses 2–5. Canon changes needed are small and
  flagged (`08` D7: Kabir must be genuinely new to the town).
- **Track B (premise validation): one commercial-genre premise sheet, Level 1 only.** No chapters, no art: a
  one-page premise written to principle 1 in a genre the evidence in `02` supports (contested romance with a
  secret, or revenge/reversal), with Opus critique. At Gate 3 the owner compares the two premise sheets and
  decides which one gets written to three chapters first.

Why not Static Tomorrow: it has art (116 assets, $3.24) but no in-chapter choices, 0 of 12 variables read,
and three branch-only scenes; it would need to be rewritten from the outline up, and its one strong idea (the
radio that predicts tomorrow) is a better *premise* than a *script*. It is the better candidate for Track B
if the owner prefers a known world over a new one.

Why not "write a new 10-chapter story now": it would reproduce weaknesses 2–5 at full length and cost.

## 6. Evidence and assumptions behind this strategy

From `02-competitive-and-audience-research.md` (Sonnet; three key sources re-read by Fable). What is verified
is mostly *craft guidance from the platforms themselves* and *audience claims from video short-drama
companies*; nothing verified is about text-first readers at a paywall.

| Strategy element | Verified support | What we are assuming |
|---|---|---|
| Hook in the first minute; cliffhanger, never a wind-down (principles 2, 5) | Episode's writer portal: premise, characters and conflict clear "within the first minute or so"; every episode, "especially episode 1", ends on a strong cliffhanger; avoid downers, buttons and wind-downs. Short-drama opening episodes run about 90 seconds with the setup packed in (TIME on ReelShort) | That a text chapter can do in ~10 boxes what video does in 90 seconds |
| Choice density and delayed branching (principles 3, 4) | Choice of Games: a choice every 100–200 words, never 400+; early choices pay off through "if" callbacks later; stats of several kinds; signal consequences before the choice; no option clearly better | That *visible* memory cues raise continuation (H3) |
| Trade-offs, not punishment; the free path is never the bad path (principle 9) | Episode's Gem Choice Guide: free options "neutral or slightly negative", not punishing; readers have the information to choose; price proportional to payoff. Documented complaints: "choices never change the story", paid options that are the obviously best option, paying to save a character (Emily Short, review aggregators) | That the same trust rules transfer from premium *choices* to premium *chapters* |
| Audience and genre (reason 1, principle 1) | ReelShort's parent: 70 % of 45 M monthly users are women; its genres are werewolf, revenge, bride, billionaire (company claims via NPR/TIME). ShortMax lists CEO, mafia, revenge, secret identity, contract marriage. Store ratings: Romance Club/DramaBox/ShortMax 18+, Choices/Chapters 16+, Episode 13+ | That the audience of a *text-and-sprites* product in *our* markets matches the video apps'; that a teen rating is the wrong default. No demographic data exists for Choices, Chapters or Romance Club |
| Chapter size and unlock position (H4, H6) | Short-drama D7 retention falls below 10 % "about when free episodes run out" (Naavik, citing Sensor Tower). Episode 1 gets fewer/cheaper gem choices | That 1,500 words is too long before the first gate; that the gate belongs after chapter 3. Both are experiments, not decisions |
| Monetization mix | Weekly subscriptions dominate short-drama recurring revenue (RevenueCat); rewarded ads give 1 diamond, 3/day in Choices (Udonis); hidden coin costs draw one-star reviews (Naavik, ShortMax reviews) | Our ad/coins/subscription mix; to be designed in `06` with explicit experiments |

Decisions this evidence supports now: write to the Episode/CoG craft rules (hook, density, callbacks, no
punishing free path); show costs before any gate; do not default to a teen rating. Decisions it does not
support yet: genre for a text product, chapter length, gate position, premium choices. Those are Gate 1 and
Gate 5 questions.
