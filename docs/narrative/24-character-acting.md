# 24. Character acting: performances from the story, not an emotion matrix

The owner's brief of 2026-11-11 ("Correction to Character Acting and Sprite Generation"): characters should act,
not cycle through predefined expressions. No fixed checklist of emotions and poses; the written story, the
character's personality and the scene decide which performances exist, and only those get made.

## What was wrong

The asset manifest made one sprite per expression name the cast file listed and the chapters used ("worried",
"angry", "smirk"), each prompted as "with a <name> expression, change only the face". That is a matrix with the
unused cells skipped: the emotion label decides the image, not the person or the moment. Chapter 1's seventeen
sprites were made that way before the brief arrived; they stay in the library as candidates and the acting pass
judges them (section 4).

## The system (`src/assetforge/story/acting.py`, `acting.json`)

1. **Acting profile** per character (`profile`): drafted by a model from the approved Character Studio profile
   (identity, personality, narrative, build): resting posture, mannerisms, how they express or suppress feeling,
   presence, characteristic gestures, what changes under stress, how they are around particular people, how the
   body language evolves over the arc tied to story events, and what they would never do. It is `proposed` until
   the owner approves it (or edits and approves) in the Studio. Every performance follows it.
2. **Performance analysis** per character (`analyze`): the model reads the written chapters as the player sees
   them, with this character's *current sprite marked on each appearance* (`acting_view`), plus the profile and
   the list of existing sprites with the scenes that use them. For every significant appearance it answers the
   brief's seven questions (feeling; communicating or concealing; with whom; how this person would physically
   answer; does the moment need a different performance; would an existing sprite do; would a new one materially
   improve the scene) and proposes, per scene, at most one or two **performances**: scene, line range, moment,
   emotional context, intent, counterpart, a short name, a nine-part direction (face, eyes and gaze, head,
   shoulders, torso, arms and hands, weight, intensity, silhouette), the need (`reuse` an existing sprite /
   `face_only` on an existing body / `new_pose`), and a justification that names the scene. It also gives a
   **verdict on every existing sprite**: does it work for the scenes that use it, given who this person is.
   A new analysis replaces earlier *proposed* performances for the same scenes and keeps decided ones.
3. **Decisions** (`decide`): approve (optionally adjusting the direction first), modify, reuse an existing sprite,
   dismiss. Approving a `face_only` or `new_pose` performance *retargets* the character's lines in that range to
   the performance's name and registers the name as one of the character's expressions, so the validator, the
   player and the asset manifest all know it. Reuse retargets to the existing sprite and closes the performance.
4. **Generation** (`generate`, and `assets.manifest(acting=directions(project))`): only approved gaps, from the
   master reference (plus the existing body for `face_only`), with the acting direction as the image brief.
   The manifest's generic "with a <name> expression" line is used only for sprites no performance describes.
5. **Studio** (`views/characters.js`, "Acting and performances"): the profile with approve / request changes /
   edit, the sprites the chapters use with their scenes and verdicts, the analysis button, and each performance
   with its moment, feeling, intent, editable direction, justification and the actions Approve / Reuse existing /
   Save direction / Dismiss, then Generate from the master once approved.

Routes: `GET /api/stories/{id}/acting[?character=]`, `POST …/acting/{cid}/profile` (patch | state),
`POST …/acting/performances/{pid}` (action approve | modify | reuse | dismiss); run actions `acting_profile`,
`acting_analyze` (scenes), `acting_generate` (performance). Task kind `acting` is heavy (Opus when managed on
Claude; the owner's plan puts ChatGPT first, so pass Opus explicitly for acting work). Tests:
`tests/test_story_acting.py`.

## Priorities the code keeps

Narrative necessity, character authenticity, visual consistency, emotional communication, asset reuse,
production efficiency, in that order. Nothing is generated because an emotion category exists; the analysis is
told to prefer reuse, to change the body only when the eyes are not enough, and to avoid theatrical poses unless
the person and the moment call for them. Development is earned: the profile's `development` field ties posture
changes to story events, and the analysis sees the chapters in order.

## Validation on Eli Hale, chapters 1–3

Run on 2026-11-11 with Opus for both calls (profile `acting.profile.eli_hale`, analysis `acting.analyze.eli_hale`
over chapters 1, 2 and both routes of 3). Nothing was generated; the proposals wait in the Studio.

**The profile** (proposed, for the owner to approve): weight back on his heels, one shoulder dropped as if
half-turned to leave, hands in pockets, eyes doing the work; a finger-against-thumb count when nerves rise; a
flat mouth that tightens at one corner for dry amusement rather than a grin; short cutting hand movements that
stop at the wrist; help given with the body angled away. Under stress the count goes audible, the jaw sets, the
feet plant, and he gets quieter and sharper. Near his father he goes still and square and the count stops.
Development: after bearing in the Seam and letting go in chapter 3, the shoulders drop a fraction and a hand can
rest openly on someone he has chosen to trust; the count slows when he stops carrying alone. Would never: a wide
open grin, sweeping arm gestures or theatrical shrugs, a slouch, pleading hands, standing with his back to a door.

**Verdicts on the existing sprites.** All five made for chapter 1 work, with conditions: `neutral` is the
master read; `smirk` is right as a one-corner tightening ("thread removal, my best event") and must stay small;
`angry` works for the father's-name and buckle beats *as long as it stays still and planted, not shouting*;
`worried` is his default under threat and carries most of the Seam; `exhausted` is right for the spent body.
`surprised`, used five times in chapters 2 and 3 and not yet made, must be built as contained surprise (brows up,
eyes widen, mouth barely parts), never a gasp: its direction is now the brief the manifest uses.

**Performances proposed** (six moments; four reuse an existing sprite, two are gaps):

| scene | moment | need | why |
|---|---|---|---|
| ch. 1, the clerk writes the father's name | shame flaring into proud fury, every warm thing shut | reuse `angry` | his anger goes still and square; the sprite serves if kept quiet |
| ch. 1, the cold climbs his arm | dread of the impossible inside his own body | reuse `worried` | dread, not shock; the count breaking fits |
| ch. 2, he closes his hand on Noor's wrist and carries | grim resolve, fear held down by the count | **new pose `bearing_strain`** | a forward-braced, head-bowed, knuckles-tight silhouette that recurs in every carry across chapters 2 and 3; the opposite of his back-on-heels line; one pose covers all of them |
| ch. 2, Idris offers paid work before asking about his father | disbelief that recognition came first | reuse `surprised` (contained) | fits once built the contained way |
| ch. 3 shared, "you are not empty, you are full" | the ground shifts under his identity, shock tipping to anger | reuse `surprised` | contained shock a beat before his quiet anger |
| ch. 3 hidden, the shop was paid for with stolen years | a hollow drop, not shock | **face only on `exhausted`: `never_really_ours`** | the spent body is right; the face must read emptied and stricken, not wide-eyed |

So the recommendation is two new images for three chapters, not a matrix, and one correction to how an existing
label is made. Where face changes alone suffice: the shop reveal. Where body language matters: every carry.

**Fixes the run forced:** the model answered `where` with place names, so the prompt now demands line numbers
(retargeting needs them); a reuse that points at a sprite not yet made now briefs that sprite with the
performance's direction, so "surprised" is made contained rather than from the generic label.

## Addendum, later on 2026-11-11: the first generated performances

The owner asked to see posture applied. Eli's analysis was run again with line numbers (the acting view now
numbers every row, options and reactions included, and `view_rows` maps a reviewer's numbers back to nodes;
when a range holds no line of the character, approval inserts a `show` of the performance at its start). Its
recommendations were approved and three sprites generated from the master at high quality, about $0.30:
`surprised` as contained surprise (kneeling, eyes on his own cold hand, mouth barely parted: the Seam ending of
chapter 1), `bearing_the_toll` (feet set wide, bowed into a two-handed grip on a wrist: every carry in chapters 2
and 3) and `never_really_ours_what_h` (shoulders dropped, the phone cupped in numb fingers, the face emptied: the
shop reveal on the hidden route). Identity, outfit and proportions held. The scenes now reference them where the
analysis placed them. Cato's smirk, which had come back with a painted magenta panel, was remade and keyed clean.
