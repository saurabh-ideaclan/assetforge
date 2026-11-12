# 28. Visual logic: story art judged against the story's world

The owner's instruction of 2026-11-12, verbatim: "note this mistake and correct so asset generated should not be
illogical and as per story world logic".

## The fault

*Nobody Rides the Tail* is set on one two-mile train crossing a dead continent of pale dust and black rock. The
background `vn/nobody-rides-the-tail/bg/nedun_coupling/evening` (asset `ast_01413`, relit from the day version
`ast_01412`) was asked for the gap between two carriages of that train. It came back as **two trains side by side on
parallel tracks**, joined sideways by a coupling across the gap between them, with snow-capped mountains, a castle
city with waterfalls and green ground on the horizon. Every object the brief named is in the picture (carriages,
coupling, chains, hoses, ladders, a distant engine); the arrangement and the world around them are wrong.

Two things in the pipeline let it through:

1. **The prompt knew nothing about the world.** A background prompt carried one location's description, written
   before the chapters, and nothing about what lies beyond the place. Image models fill every gap with whatever looks
   pretty. Fixed by the **visual canon** (`story/visual_canon.py`, `visual_canon.json`): `world`, `outside`, `always`,
   `never`, per location `description` (as the chapters use the place), `geometry` (what a painter misreads, stated
   flatly: "one train, one track, two carriages of the SAME train joined by one coupling") and `never`, plus `props`.
   `assets.manifest(canon=…)` puts it into every `bg`, `bg_time`, `cg` and title prompt; `canon=None` leaves every
   prompt byte for byte as before.
2. **Nothing looked at a finished image.** QC measures pixels (blank, transparency, edges), never meaning. Fixed by
   the **visual logic check** below.

## The check (`story/visual_check.py`, `visual_check.json`)

Each made image is judged by a vision-capable model through the subscription CLIs (no image cost):

1. **A blind survey first** (task `visual.survey.<key>`): the image alone, no brief, no canon. The model reports the
   layout (separate objects or rows, side by side or one behind the other, what is joined to what and in which
   direction), counts, everything beyond the subject, and any writing. This step exists because of the acceptance
   run: Claude Opus, given the brief and the image together, described the two side-by-side trains as "two carriage
   ends joined at centre by one heavy steel coupling" and counted one train. It had read the brief and saw the brief.
   Asked the same image with no brief, it reported "two parallel lines of railcars ... side by side on parallel tracks,
   cross-coupled to each other". A judge that has read "joined end to end" is primed; a surveyor is not.
2. **The judgement** (task `visual.check.<key>`, kind `visual`, Opus when the plan is managed): the brief (the item's
   own request description from the manifest), the location's canon entry (description, layout, never-list), the
   world's canon (world, outside, always, never, props) and the blind survey as evidence ("the survey is right unless
   you can name the exact part of the image that shows otherwise"). The judge must fill `seen` (describe before
   deciding) and `counts` (vehicles, tracks, people, limbs where a number is implied) before `violations` (rule +
   what the image shows instead), `logic` (physical or spatial impossibilities whatever the brief says),
   `text_in_image` and `matches_brief` (yes | partly | no). It is told never to pass an image because it is beautiful.
   A `bg_time` or expression is shown with its reference image as Image 2, only to check the composition is kept.
   The judgement ends with a **physical plausibility pass** that is independent of the brief and the canon (added after
   the owner found three more faults in remade images that a reviewer had passed): (a) *axes*: for every long or
   linear thing say which way it runs in the frame; a part joining things that lie one after another on an axis runs
   along that axis, so in a view looking along a train the coupling can never be drawn left to right; (b) *closure*:
   vehicles, rooms and containers are closed where a real one is (no coach end you can see the track through);
   (c) *one camera, one moment*: motion blur strongest near a moving viewer and fading with distance (never smeared to
   the horizon), consistent scale fall-off, one set of light sources, one horizon; (d) *integrity*: nothing floating,
   merged, duplicated, extra limbs. The survey reports the same evidence blind (direction of every long thing and
   joint, what can be seen through things, where blur is). The canon now states these as `always`/`geometry` rules
   too, so a fault fires twice: as a broken rule and as a logic fault.
3. `ok` only when `matches_brief == yes` and there are no violations, no logic faults and no lettering.

`review(ctx, groups=("bg","bg_time","cg","ui"))` checks every made image in those groups and stores verdicts by asset
key; a verdict whose `asset_id` is not the key's current asset is stale and the image is checked again. Sprites
(`char`, `expr`) can be judged too (brief only: the character's look and, for performances, the acting brief), but are
off by default. UI frames (textbox, choice) are judged against their brief only; the canon applies to scene art.

`fix(ctx, key, max_rounds=2)` is the only paid step: it archives the failed image, requests it again through
`request.handle` (refs resolved as `assets.produce` does) with the faults appended to the description ("The previous
attempt was rejected: it broke "one single train on one track": two trains on parallel tracks ... Paint it so that
every one of these holds: ..."), checks the remake, and repeats. Every round's feedback lists all faults seen so far
and how many attempts failed, so a remake never trades one fault for another and never reuses the previous request.
After `max_rounds` the best attempt (ok first, then match, then fewest faults) is restored and the rest stay archived.
A remade `bg` also remakes and checks the `bg_time` images that were relit from it.

## Image input for the text providers (`llm/`)

`TextCall.images` (empty by default: text-only calls and their fingerprints are unchanged). Image calls put each
image's SHA-256 in the job fingerprint, and the runner copies the images into the CLI's empty temp folder.

- `codex exec --image <abs path>` per image (supported, verified).
- `claude -p --tools Read --allowedTools "Read(<abs path>)"…`: Read is the only tool in the session and it is allowed
  on exactly the staged files; a read of anything else is denied (verified: a request to read a repo file came back
  as a permission denial). The prompt names the files and asks Claude to open each before answering.
- `gemini-cli` and the API fallbacks: `supports_images = False`; `structured.generate` skips them for an image call
  (recorded as `choice.image_blind_skipped`) and says so clearly when no provider can see.

## Running it

```bash
assetforge story art-check nobody-rides-the-tail            # judge every made bg, bg_time, cg, ui (subscription)
assetforge story art-check STORY --groups bg --groups cg    # a subset; --all re-judges checked images
assetforge story art-check STORY --fix --max-rounds 2       # PAID: remake failures with their faults as feedback
```

App: the story's Assets tab has "Check art logic" (run action `art_check`) and a mark under each judged image (a green
"logic" tick, or the first fault in red with the rest as the tooltip and a Remake button, run action `art_fix`).
`GET /api/stories/{id}/visual-check` returns the verdicts and the canon; run action `visual_canon` drafts the canon.

## What it cannot see

- It is a model looking at a picture. It misses small things (a third hand half hidden, a lamp on the wrong side), can
  still be argued into a reading, and its counts of many similar things (carriages in a long row) are estimates.
- It knows only what the brief and the canon say. A fault the canon never names ("Charge is blue") is caught only if
  the canon states it; keep the canon's `never` list concrete.
- It judges one image at a time: consistency across images (the same carriage interior in two chapters) is not
  checked here (`characters.consistency` covers sprites by dHash and palette, heuristically).
- A pass is not an approval. A person still approves story art; the check removes the obviously illogical before
  anyone has to look.

## Acceptance (2026-11-12)

Run on `ast_01413` (read by id from the owner's library, read-only; story folder copied to `.dev/visual-check/story`;
job records in `.dev/visual-check-lib`; verdicts in `.dev/visual-check/verdict_*.json`).

| Image | Judge | Verdict | Names the fault |
|---|---|---|---|
| `ast_01413` two trains, castle city | codex-cli (gpt-6.1-sol), one-stage prompt | not ok, `no` | yes: "Two separate parallel carriage rows occupy the left and right sides" against "One single train on one single track" |
| `ast_01413` | claude-cli Opus, one-stage prompt | not ok, `no` | **no**: horizon faults only; it described "two carriage ends ... joined at centre" and counted one train |
| `ast_01413` | claude-cli Opus, blind survey + judgement | not ok, `no` | yes: the coupling "running left-to-right across the frame, joining the two cars side-by-side", plus the axis logic fault |
| `ast_01424` hollow coach, sideways coupling, blur | codex-cli, with the plausibility pass | not ok, `no` | axis and closure yes ("a large missing end wall and an unobstructed view of the track beneath it"); the landscape blur was **not** flagged |
| `ast_01445` sideways coupling, view along the train | codex-cli | not ok, `no` | yes: "the coupling bridges left-right across their longitudinal slot", blur too far into the plain |
| `ast_01446` sideways coupling, view along the train | codex-cli | not ok, `no` | yes: "the chain and coupling run across the frame" while the bodies run along the view; motion blur |

The one-stage Opus miss is why the survey exists. The last three were judged against the canon as it stands now
(which also states the axis, closure and blur rules), so each fault fires both as a broken rule and as a `logic`
entry from the plausibility pass.
