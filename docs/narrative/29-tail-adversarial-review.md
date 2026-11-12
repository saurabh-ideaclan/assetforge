# 29. *Nobody Rides the Tail*: the showrunner's adversarial review (2026-11-12)

The owner asked for the whole story with every asset, then an adversarial review by the showrunner. This is that
review: what was checked, what was wrong, what was changed, and which faults became standing checks. The story is
13 scenes (8 chapters per playthrough, three paired routes, three endings), written by ChatGPT through the clarity
gate; text fixes by hand; art by the image providers; audio procedural.

## How it was reviewed

| Pass | Who | What it can see |
|---|---|---|
| Per-chapter cold read | Sonnet, reader view only | one chapter: terms, dialogue, motivation, choice stakes |
| End-to-end read | the showrunner, all 13 chapters in order | facts, names and promises across chapters |
| Whole-story checks | deterministic | name drift, overwritten stats, late acknowledgement, hidden illustrations |
| Playthrough simulation | 20,000 random plays | whether each ending can be earned, how stats spread |
| Real player | three scripted runs through the app's player | assets, staging, sound, errors, every chapter and ending |
| Art, by eye | the showrunner and the owner | every background, illustration, sprite |
| Art, by judge | a vision model: blind survey, then judgement against brief and canon | world, layout, physical logic |
| Editorial evaluation | Opus, the owner's fifteen questions | story design |

## Findings and fixes

### Story (no per-chapter gate saw any of these)

1. **The true ending could not be reached.** It needs two kits in reserve plus two pieces of evidence that each
   cost a kit; from four kits with one unavoidable spend, the most a player could keep was one. Zero of 20,000
   simulated plays reached it. Fixed: five kits at the start (and the text that states the count). Now 5.9 % true,
   9.2 % good, 85 % neutral under random play; all three reached in the real player.
2. **One dead woman, three names.** Rafe's mother was Evara, Dema and Eda in different chapters. Unified as Evara.
3. **A fact that changed by route.** The hidden stop lasted forty-three minutes on one route and nineteen on the other.
4. **A count that changed between chapters.** Seven survivors announced, six rescued. Six everywhere.
5. **The climax contradicted its ending.** A revision that made the climax pass its cold read added "it is not
   stopping the whole Line", while the true ending is the first full stop. Rewritten; and the story now says once,
   at the midpoint and at the climax, *why* stopping matters (a standing train loads a Hollow's whole store, and
   with that much Charge the Engine drops no one).
6. **Dropped threads.** Sarun's private offer (his mother on the Roll for his obedience) was never answered: now a
   flag read in chapters 5 and 7. A rescued Walker wrote "IT FOLLOWED MY HAND", hinting at a second person with the
   power: changed to "IT CAME AWAY IN MY HAND".
7. **Chapter joins.** Chapter 3 opened without acknowledging the accusation chapter 2 ended on; chapters 4, 5 and the
   good ending started mid-action with no place or bridge.
8. **Stats.** Ten choices overwrote a stat (`set`) instead of adjusting it; four stats were written by dozens of
   choices and first read four chapters later; two were pinned at their cap in 99 % of plays. Fixed: adjust, hand-
   written acknowledgement lines within two chapters, wider ranges, the good ending's gates retuned.
9. **Emotion held too tightly at the payoff.** The name going into brass was a spelling check. Rafe now fails to
   say it once; Sarun says "I counted her as weight, Rafe Oduya. I was wrong to." From the evaluation: the mother
   now has a choice of her own (she gave up her seat and walked back to car 212), and the car has other riders.
10. **An exit option that lied.** "Take the route plate to the records keeper" was offered to players who had left
    the plate behind.

### Art (the owner found the first; the rest are the same family)

1. **Illogical layout.** "The gap between two carriages" came back as two trains side by side; then as a coupling
   drawn left to right in a view along the train; a coach ahead was hollow, with track visible through it.
2. **The wrong world.** A castle city, snow peaks, forests and open water outside a train crossing a dead dust
   continent; the whole plain smeared with motion blur.
3. **A place described before the story decided what it was.** A rescued slum carriage painted as an Engine lounge.
4. **Everyone in every illustration.** The prompt listed a chapter's whole cast, so a moment that belongs to one man
   showed five.
5. **General expressions painted as actions.** "Stern" was a lunge across a doorway, "worried" a man kneeling over
   a crate, because a sprite reused across scenes was briefed with one scene's moment.
6. **A character of the wrong sex**, and a title posing hero and antagonist as a duo.
7. **An illustration nobody could see**: placed before the staging that clears it.

Fixes: the visual canon (drafted after the chapters; world, outside, always/never, camera and axis, solid vehicles,
blur only up close) in every background, illustration and title prompt; illustrations take the place as materials
only and state their own camera; only the people an illustration's text names; general sprites keep the master's
stance; the vision judge with a blind survey first and a hard/soft split. 16 base images, 13 illustrations and 34
sprites were made; 8 base images, all illustrations at least once and 30 sprites were remade after review.

### Audio

1. **Doubled sounds.** The planner cued a sound beside 29 sound lines the writer had already written. A planned cue
   now adopts the written line.
2. **The signature sound had no recipe.** "Whistle blasts" was inferred as an explosion. New `steam_whistle` recipe;
   one blast for a halt, two for a grade, three for a cutting, read from the description.
3. **A fix that did nothing, twice.** The old render was returned for the same words (the request fingerprint did not
   include the recipe), then the blast-count preset was silently ignored (wrong preset shape). Both verified by
   reading the rendered asset's parameters and measuring the file: three tones, two gaps.
4. **Player.** A fade could set a volume a hair below zero and throw.

## What became a standing check

`stakes.play_report` (unreachable ending, dead end, saturated stat), `stakes.continuity_report` (name drift),
`choice_report` `absolute_set`, lint `hidden_cg`, `visual_canon` + `visual_check`, the illustration cast rule, the
general-sprite brief, cue adoption, the recipe in the sound fingerprint. Each is in `lint.json` or runs before an
asset is shown.

## What this review cannot claim

Nobody has listened to the audio: it is measured, not heard. The cold reader's verdict moves by a few points
between runs on unchanged text, so "ok" and "revise" near the line are noise. The vision judge is a model looking at
a picture: it caught every fault the owner raised, and it also once failed 26 images out of 26 on details. The
owner's own read and play remain the review that counts.
