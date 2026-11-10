# 14 · Audio direction: audit, design and validation

**Date:** 2026-11-10 · **Scope:** narrative audio as a storytelling layer, built on the existing synthesis.

## 1. Audit of what existed (Phase A)

| Capability | State before | Gap |
|---|---|---|
| SFX synthesis | 81 seeded recipes in 10 modules (doors, levers, footsteps, cloth, glass, swords, spells, thunder, UI…), 20 character presets (`distant`, `muffled`, `huge`, `heavy`, `realistic`…), seeded variations, QC (silence, clipping, DC, leading silence), WAV masters + OGG/M4A delivery | No knock, bell, phone, chain, page, hooves or armour |
| Ambience | 20 beds, 25 events, 27 catalog scenes (dungeon, cave, tavern, forest, rain, storm…), loop seams measured, per-story `sound.json` plan per location | Only one ambience per location; no mid-chapter ambience change |
| Music | 17 genre templates, 30 instruments, loops, per-mood `sound.json` cues, optional SoundFont | No way to stop music (silence); every mood change was a hard cut |
| Story format | `sfx` (one-shot), `music` (mood), `place` (location + music), location ambience | No loops/stops, no fades, no levels, no purpose recorded, no branch-only audio beyond `when` |
| Player | Music and ambience loops with fades, one-shot SFX, a mute button | No volume controls, no stop of running sounds on scene change, no looping SFX, no silence |
| Planning | `sound.json` chosen from the catalog per story | No per-chapter audio direction, no review, no human panel |
| Narrative Dynamics Studio | **does not exist in this repository** | The brief's integration target is absent; the audio-intensity strip stands alone |

Synthesis quality was checked by rendering, not assumed: every new recipe passes QC (no silence, no clipping);
the first `knock` render had 50 ms of leading silence, fixed. Music is composed procedurally; it is adequate for
restrained, atmospheric cues and loops, and remains the weakest layer for "cinematic" wants. No new audio
service was added.

## 2. Design

- **Philosophy in code.** The planner prompt carries the profile (balanced by default), the genre palette and
  the rule that every cue has a purpose and that silence is a choice. The review enforces budgets per 1,000
  words, one accent at most, opening ambience, silence in tense chapters, stopped loops, no repetition, cues on
  the line that shows the event.
- **Seven stages, as asked.** Narrative planning (the scene is written first) → scene direction (`audio_plan`,
  cues with purposes, no files) → editorial review (deterministic rules + the operator) → asset planning (the
  manifest, SFX reused by search, ambience and music by key) → synthesis (existing engines) → review and approval
  (per-cue preview, approve, reject, regenerate) → integration (`apply` writes the nodes the player triggers).
- **Playback mechanism.** One-shots, looping SFX with explicit `stop`, looping ambience by location or catalog
  scene, looping music by mood, `silence`, fades, levels, `when` conditions for branch-only audio; the player
  stops everything on scene change, load, restart and route jumps, and pauses loops in the background.
- **Profiles.** `minimal`, `balanced` (default), `cinematic`, `custom`, with music/sfx/ambience/accent/silence
  levels and a style note, saved in the story's creative direction.
- **Mobile constraints.** OGG + M4A delivery as before, one music and one ambience loop at a time, no runtime
  generation, every event also in the text.

## 3. Validation on *The Unrung* (Phase G)

Three chapters, planned by the story's configured writer (claude-cli / sonnet; in managed mode the `audio` task
kind goes to Opus), reviewed by the rules, applied, synthesised and played.

| Scene | Plan (model's own summary, shortened) | Cues | Review |
|---|---|---|---|
| A · ch 1 *Unrung* (atmospheric introduction) | Square murmur and a single toll carry the dread; tense music low under the dialogue; everything drops to bare ambience when the test-bell overstrikes; the music is stripped to silence before Lucan whispers "Quiet" so the Hush lands in near-total quiet; eerie after, dark at the close | 2 ambience, 3 music, 2 bell sfx, 1 accent, 1 silence | clean |
| B · ch 2 *The Listening* (physical events, branching) | Yard ambience under the whole scene with almost no music; boots, a fork, the lash as a threat; tense music only when the captain turns on Wat, out once he leaves "so the yard's exhale is heard in silence"; plus two **branch-only** cues added by hand: the lash strike on `flag_wat_taken == 0`, Wat dragged away on `== 1` | 1 ambience, 2 music, 7 sfx, 1 accent, 2 silence | warning: too many sfx for the balanced budget (7 vs 5) |
| C · ch 3 *First Bell* (milestone) | Dungeon ambience by the Cracked Bell, a cracked half-toll and a bell that vanishes mid-ring as the Hush works; the hall as a resonant "cave"; the clean First Bell note, then two climbing notes nobody expected; murmurs die into silence before Sable's arrival (the one accent); dark music while he studies Lucan; silence on the unknown seal | 3 ambience, 2 music, 5 sfx, 1 accent, 2 silence | warning: `bell` used 5 times |

Results [MEASURED]:
- 35 cue nodes applied (the chapter text untouched; the exit stayed last).
- 32 sounds made by the existing engines in one `assets` run with no API: 5 music loops, 5 ambiences (two of them
  catalog scenes the planner chose over the location's own: `cave` for the hall's acoustics, `dungeon` for the
  bell-well), 22 effects. Cues that described the same sound resolved to one asset (content reuse), so "hush
  wakes" and "hush pulse" share a file, as do the captain's two steps.
- Two cues show `needs_generation` although a sound exists: search-before-generate matched an older asset that
  carries a different key. Fix: register the matched asset under the cue's key as well (small, pending).
- Played in the browser: `music/tense.ogg` and `amb/highbell_square.ogg` load on the first line,
  `sfx/bell_toll.ogg` loads exactly on the strike line after the first choice; no console errors; the volume
  panel (master, music, sounds, ambience) and mute persist.
- Known overlap: the chapter writer's own `sfx` nodes (`bell_overstrike`, `hush_pulse`) stay next to the plan's
  cues on the same lines, so those beats can sound twice. Fix: the review should flag writer sfx on a planned
  line and `apply` should offer to replace them (pending).

What the brief asked for and the run showed: subtle ambience with little or no music in scene A, synchronised
physical events in scene B with two different sounds on two routes, music, an accent, transitions and deliberate
silence in scene C, all previewable and editable per cue in the panel, all made with the existing synthesis.
