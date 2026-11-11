# 21. Narrative systems audit (2026-11-11)

Phase 2 of the "Audio Stabilization, Visual Novel Refinement & Power Fantasy" brief: what the story pipeline
has, what to preserve, what the new Power Fantasy pilot needs that is missing. Read with
[12-production-ux-audit.md](12-production-ux-audit.md) (the last audit) and [19](19-audio-intelligence.md) §9.

## 1. Phase 1 first: what was disrupting the audio, and what changed

Measured on the Unrung (the only story with produced music and ambience), not guessed:

| symptom the owner reports | what the files showed | change |
|---|---|---|
| music "irritating, repetitive" | 4 music changes in a 1,250–1,570-word chapter (one every ~2 minutes of reading); loops of 48–66 s under beats a reader sits on for minutes | budgets halved (`AUDIO_BUDGETS`: balanced 1.2 music / 2.5 sfx per 1,000 words), `music_churn` review warning (two changes under 300 words apart), story music now 96 s loops at energy ≤ 0.5, Studio default 96 s / warmth 0.45; the player's music **breathes**: after two full passes it fades out over 5 s, rests 36 s, returns 15 % quieter |
| "voice-like" ambience | the `crowd_murmur` bed (synthesized talkers), chosen by the sound-plan writer for a deserted square | the sound plan drops `crowd_murmur` unless the script says people are there; every story ambience render is measured and, when voice-like content is found in a place with nobody in it, rejected and re-rendered with the bed muted (`request._without_voices`) |
| too much sound on dialogue | 5–9 sfx per chapter, several on lines that only mention the thing | prompt restraint rules (no sfx because something is said; no stingers under dialogue; a chapter may have no music; nothing depends on timing), sfx budget 2.5 / 1,000 words |
| music competes with text | player defaults music 0.55 / ambience 0.40 | music 0.40, ambience 0.35, sfx 0.70; crossfades 2.4 s (music) and 1.8 s (ambience) instead of 0.9 s |
| cues that assume cinematic timing | cue `at` is a line index; the player plays them when the line shows | unchanged, and the prompt now states the rule; the breathing rest is the only time-based behaviour, and it is a rest, not a cue |

Verified in a scripted three-minute read of the Unrung in the real app (`window.vn.audio()` log): one music loop,
passes at 86 s and 152 s, rest at 152 s, return at 194 s, two sfx, ambience steady. Not a listening test: the
owner's ears are still the judge, and the remaining advanced work (composition quality, neural models) is deferred
as the brief asks. `statuses` now warns on music assets under 60 s so old short loops are visible in the panel.

## 2. What exists, and is preserved

| system | state | keep |
|---|---|---|
| Creative direction (`story/direction.py`, presets, genres.yaml, traditions.yaml, form groups A–G) | solid; precedence explicit > canon > preset > defaults | yes |
| Naming guide, world, cast steps | solid; cast follows the guide, overrides win | yes |
| Outline → write in parts → lint → fix (`steps.py`, `lint.py`) | solid; benchmark rules (words, choices, first-choice box, wall, callbacks) | yes |
| Story format v2: in-chapter choices, `when` callbacks, `place`/`music`, `branch` gates, typed variables, chapters | solid | yes |
| Workflow gates (12 stages, auto/guided/manual, approvals, impact) | solid | yes; the pilot runs **guided** so the owner approves concept, cast and outline |
| Character Studio (profiles, approvals, locks, proposals, concepts, master, consistency) | solid | yes; the cast review for the pilot happens here |
| Improve / refine / check / alternatives | solid | yes |
| Audio direction per chapter, sound plan, player audio | now restrained (above) | yes |
| AI execution plans (managed: Sonnet for most, Opus for `HEAVY_KINDS`) | solid | yes; the pilot uses managed, Opus for outline and character psychology |

## 3. What the Power Fantasy pilot needs that is not there

1. **Narrative Dynamics.** Does not exist (audit 12 said so; the brief lists it under "use"). The pilot needs it
   in a minimal, honest form: planned per-chapter values for valence, tension, suspense, **capability**, **status**,
   family conflict, plus milestones (victory, setback, reveal), stored with the outline; then the same tracks
   estimated from the written chapters (a model call per chapter, labelled as an estimate), drawn side by side.
   The capability-versus-status gap is the genre's engine, so those two tracks are the ones that matter most.
   Build in Phase 6, after the concept and cast are approved.
2. **Branching view.** No graph view of decisions, routes, consequences and convergence. The outline already has
   everything needed (scenes, exits, `when`, variables); a read-only view is a Phase 6 UI task.
3. **Progression variables.** `Variable.kind` has relationship / personality / resource / flag / world. Power
   progression fits `resource` (capability, cost carried) and `world` (status, exposure), so no model change; the
   outline prompt needs the pilot's stat design handed to it explicitly (done through the direction's canon).
4. **Chapter length for this genre.** The benchmark's 1,500 words / 6 choices per chapter stays; manhwa pacing is
   handled by the beats, not by shorter chapters (research 20 §3).
5. **Evaluation rubric.** The brief's fifteen questions become the `check` prompt's rubric for this story (Phase 8),
   with scene evidence required for every answer; the existing `check` step only looks for contradictions.
6. **Natural feedback.** `improve` already takes free text ("the mother needs more personality") and turns it into
   per-scene edits; what is missing is the diagnosis before the edit (why a chapter is boring). Phase 8 adds an
   editorial pass that names the cause (no reversal, no cost, no decision, stakes not shown) before proposing cuts.

## 4. Research caveat

[20-progression-fantasy-research.md](20-progression-fantasy-research.md) was written by Sonnet with thin sources
and says so; its pacing numbers are estimates. The concept in [22](22-power-fantasy-concept.md) uses its design
principles and the director's own knowledge of the genre, and the pilot's playtest (Phase 9) is where those numbers
get tested.
