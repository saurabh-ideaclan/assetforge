# 15 · Music and ambience revamp: audit, approach, benchmark

**Date:** 2026-11-11 · **Scope:** the generated music and ambient loops that play under visual-novel scenes.
SFX synthesis (81 recipes) is untouched. Companion research: [16 · Audio technology evaluation](16-audio-technology-evaluation.md).

**Reading this honestly.** I cannot listen. Every quality statement below is a *measurement* (loudness, spectral
centre, busyness, reading-band share, repetition, dynamics, loop seam) or a *structural* fact (which template,
which instruments, how many candidates). The listening page (§6) is where the owner decides whether the measured
improvement is an audible one. "Better" in this document means "closer to what the brief asked for, by numbers
that track known listening problems".

## 1. Audit: why the old loops rarely fit the scene

The owner's complaint: music and ambience generated for a scene rarely felt right and took five to ten rounds of
feedback or a reference track. The benchmark (ten briefs, §5) rendered the *existing* path as the story pipeline
calls it (`sound.json` mood → `MOOD_GENRE` → one composer template; location → the closest catalog ambience scene)
and measured it. Root causes, all [MEASURED]:

| Fault | Evidence (baseline) | Consequence for a reader |
|---|---|---|
| **Mood collapse.** Eleven moods mapped onto four templates; `calm`, `sad` and `resolution` all hit `ambient_drone` with the same energy and the same seed. | `calm_exploration`, `betrayal` and `resolution` produced the **identical file** (same hash, same metrics). | Three scenes with opposite emotional jobs got one piece of music. No amount of feedback fixes a template that cannot express the difference. |
| **Dull, static music.** Every music template rendered with a spectral centre of 350–450 Hz, dynamic range 1.2–2.4 dB over the loop, repetition index 0.35–0.44 (self-similarity at ≥ 2 s lags). | Six music briefs; centre 346–448 Hz; `political_suspense` 1.2 dB range. | Everything sounds like the same warm blur; nothing rises or falls with the scene. |
| **Sub-bass "places".** The `dungeon` scene's energy sat almost entirely below 80 Hz. | `dungeon_mystery` centre **61 Hz**, 0.2 % in the 1–4 kHz band. | A rumble, not a dungeon; on phone speakers it is nearly inaudible, on headphones it is fatigue. |
| **Hiss in the reading band.** Rain beds put most of their energy where dialogue reading attention lives. | `rainy_room` 58.6 % of energy in 1–4 kHz. | Competes with the text; the "too distracting" feedback loop. |
| **No evaluation at all.** The pipeline rendered one take and cataloged it. Nothing compared the result to the brief. | Each baseline brief: one render, zero rejections. | The operator *was* the quality loop. |
| **Nothing to steer with.** `MusicRequest` exposed genre/mood/tempo/energy. No melody prominence, no percussion policy, no arc, no warmth; ambience had no bus filters and no slow evolution. | — | Feedback such as "less melody", "no drums", "warmer", "stop the rumble" had no control to land on. |

Conclusion: the composer and the ambience engine were not the bottleneck as *technologies*; the **mapping from
scene to parameters** and the **absence of a measured review** were. That is why the fix is structured direction
plus a candidate loop, not a new synthesiser. Section 2 says what a neural model would add and why it is a
follow-on experiment rather than part of this delivery.

## 2. Can the current technique reach the bar? What the research says

[16 · Audio technology evaluation](16-audio-technology-evaluation.md) (Sonnet, licence texts read, Fable-checked)
answers the brief's question "determine whether the current technique can achieve the desired quality; if not,
recommend a better approach":

- **Procedural + SoundFont (what we have) can reach "restrained underscore and ambience that fits the scene"** once
  it is directed and reviewed. It cannot reach "cinematic orchestral with performed phrasing": that needs sampled
  performances or a neural model. The benchmark briefs that ask for the former improved; `power_awakening`
  (a heroic cue) is where the ceiling shows.
- **Neural, local, commercially usable:** ACE-Step v1 (Apache 2.0, up to 4 min, Audio2Audio/repaint, M2 Max
  RTF ≈ 2× at 27 steps) and ACE-Step 1.5 (MIT tag, reference audio, MLX launch script; licence text gap noted) are
  the candidates. **Stable Audio 3** small/medium are the strongest for short cues and SFX-like beds but are gated
  on Hugging Face and under the Stability Community License (free under US$1M revenue; Enterprise above).
  MusicGen, AudioGen, AudioLDM 2, TangoFlux and YuE weights are **non-commercial** and are ruled out.
- **Hosted:** Stability API at 8 credits (US$0.08) per generation is the only one with clear pricing and an
  audio-to-audio option; ElevenLabs excludes games above a revenue threshold without Enterprise; Suno/Udio have no
  public API. All are production-time only: the brief forbids runtime paid generation in the mobile app, and the
  pipeline already bakes OGG/M4A at build time, so any of them would slot in behind `produce_for_brief` as a
  candidate source without touching the app.
- **Samples:** VSCO 2 Community Edition and the Versilian Community Sample Library are CC0 and are the best legal
  route to orchestral timbre without a model; GeneralUser GS (already installed) is free for commercial rendering.

**Recommendation and status.** Keep the procedural engines as the always-available floor (deterministic, free,
reproducible, loop-exact). Add ACE-Step 1.5 as a *second candidate source* in the same loop, conditioned on our
own composer output as reference audio (never third-party tracks), judged by the same measured review, and only at
production time. This is **not implemented**: the machine has no PyTorch/MLX stack installed and no Hugging Face
token, and a first run is an afternoon of download and timing on an M3 with 16 GB. It needs the owner's go-ahead
(disk, time, and a licence read of the 1.5 weights). Everything below works without it.

## 3. What was built

### 3.1 Direction before sound (`audio/production.py`)

A brief becomes a **structured direction** before anything renders. `MusicDirection`: purpose, emotional
objective, genre template, mood, tempo, key, energy, melody prominence, percussion (none/light/full), arc
(flat/rise/rise_fall/fall), warmth, brightness, under-dialogue flag, instruments per role, space, loop, seconds,
summary and reasoning. `AmbienceDirection`: catalog scene or beds with levels and params, events with rates,
activity, brightness, low/high cut, evolve, indoor, space. `direct()` asks the story's writer (task kind
`audio_direction.<kind>`, Opus when managed) with the real catalog in the prompt, the project's sound identity
(§3.5) and any scene context; with no writer it falls back to a heuristic direction from the words. `clean()`
keeps every name inside the catalog, so a model slip (a melodic instrument on the drum role, a bed that does not
exist) cannot break a render.

### 3.2 Composer controls and templates (`audio/music/composer.py`)

Five restrained templates written for narrative underscore: `fantasy_restrained` (harp, low strings, an
occasional flute phrase, no drums), `melancholy_piano` (sparse piano over slow strings), `court_intrigue` (muted
low strings, slow pizzicato pulse, unresolved harmony), `rising_magic` (drone, shimmering highs, climbing
harmony), `heroic_lift` (strings and horns, a clear rising motif, restrained percussion). The mood map now sends
calm/sad/tense/mysterious/heroic to *different* templates. `Brief` gains `melody` (prominence, gates the melody
track and scales its density and gain), `percussion` (none/light/full; light caps drum energy and drops
fills/crashes), `arc` (section velocities and layer thinning: a rise starts soft and fills, rise_fall comes back
down for the last phrase) and `warmth` (a top-end roll-off on melodic roles). A melodic pick for the drum role
keeps the template's kit. Older requests use none of these and render as before.

### 3.3 Ambience bus and evolution (`audio/ambience/engine.py`)

`low_cut` (no rumble under a place), `high_cut` (no hiss in the reading band) on the bus, and `evolve`
(seamless slow level drift of the beds, so a two-minute loop breathes without a seam). All default to the old
behaviour; older ambiences reproduce bit for bit.

### 3.4 Measured review and bounded refinement (`audio/quality.py`)

`measure()` returns loudness, spectral centre, flatness, onsets per second, reading-band share (1–4 kHz), sub
share (< 80 Hz), repetition index, event regularity (CV of onset intervals), dynamic range over 3 s windows,
clicks, loop seam and the technical issues. `expectations(kind, direction)` turns the direction into ranges (a
brief with energy 0.2 implies at most ~2.6 onsets/s for music; an under-dialogue piece may put at most 35 % of its
energy in the reading band; ambience sub share ≤ 45 %). `review()` makes technical failures hard (silence,
clipping, audible loop seam, > 20 clicks → unusable) and adherence misses soft, each with a weight and the
**production change it calls for** (`REFINEMENTS`: too busy → energy −0.15, percussion light, melody −0.3; sub
rumble → low cut 90 Hz; in the reading band → warmth +0.4 or high cut 5 kHz; repetitive → rise_fall arc and a new
melody seed…). The loop (`produce_for_brief`) renders N candidates, reviews them, refines the direction *by the
measured problem* of the best one, renders again, at most `MAX_ROUNDS = 2` extra rounds, then keeps the two best
and marks the rest rejected (never deleted; lineage and seeds stay). The chosen asset's `metadata.production`
records brief, direction, score, rounds and the reason. Cost: ten briefs through three rounds of three took the
times in §5; nothing paid is involved unless a hosted model is added later.

**A measurement bug the loop exposed.** The first full runs refined every music cue toward "less": energy and
melody went to zero and the score never moved. The cause was the busyness measure, not the music: the library's
onset detector (`features.analyze`, kept for search tags) counts every spectral bump above the file's own mean,
so tremolo strings or a rain bed read as ten "onsets" a second and a sustained chord was called busy. The review
now counts *salient events* (`quality.events`, the SuperFlux idea: a rise against the maximum of the previous
three frames *and* the neighbouring ±3 bins, so a partial wobbling with vibrato never counts; a floor of 2.5× the
file's median flux, so texture has no events; a local maximum; 60 ms apart). Checked on known signals: one
sustained tremolo chord reads 0.25 events/s instead of 10.7; the same template with a pizzicato pulse 1.2–1.3/s;
a drip-and-creak dungeon 0.8/s. A first version without the frequency filter missed quiet plucks under loud
strings (0.03/s), which is why the check against a known signal has to include a quiet layer under a loud one.
The ranges in `expectations` were recalibrated, and both benchmark arms and the Unrung regeneration were re-run. The lesson is in [LESSONS.md](../../../LESSONS.md): check the measure on a
known-simple signal before letting it steer anything.

The brief warned against assuming a text model can judge audio it cannot hear. It does not judge: the writer
proposes a direction once; the numbers judge; a person approves. The feedback chips in the Studio ("too dramatic",
"doesn't feel medieval", "too distracting during dialogue"…) go back to the writer as *direction* feedback and to
the reviewer as *expectation* changes, not as a request to "make it better".

### 3.5 References and sound identity

`add_reference(project, asset)` stores an approved piece with its measured character in
`$ASSETFORGE_HOME/audio_style/<project>.json`; later directions see the references (with notes) in their prompt,
and candidates report `reference_distance` on the measures that carry style (brightness, noisiness, busyness,
reading-band share, dynamics); ties are broken toward the reference. `theme_variations(asset, moods)` re-renders a
chosen theme with the same melody/harmony/structure seeds under another mood, linked `derived_from`, so a story
can carry one theme through calm, tense and triumphant versions.

### 3.6 Music & Ambience Studio (`#/audio`)

`app/api_audio.py` and `views/audio_studio.js`: brief → direction (summary and the writer's reasoning, editable
creative settings, raw JSON) → candidates (a background task with readable steps) → each candidate with a player,
its score, the measured reasons in words and its open issues → feedback chips and free text → refine → approve
(optionally under a story key, optionally as a reference) → the project's sound identity. Rejected candidates are
listed, not shown. A run that ends below 0.6 says so in a warning instead of pretending.

### 3.7 Narrative integration

Scene cues from [14 · Audio direction](14-audio-direction.md) carry the chapter's context (location, time, mood,
purpose); the Unrung regeneration (§7) passed that context into `produce_for_brief` so the direction knew *why* the
music exists. The brief asked for Narrative Dynamics Studio integration; **that studio does not exist in this
repository** (said in 12, 14 and here), so the integration point is the scene audio panel and the story key.

## 4. Benchmark method

- **Briefs:** ten, in `.dev/audio-bench/briefs.json`: four ambiences (village square, stone dungeon, throne hall
  audience, rainy wooden bedroom) and six music cues (calm exploration, betrayal, political suspense, growing
  magic tension, heroic power awakening, quiet resolution). Each names the emotional job and what must not happen
  ("never loud", "no beat").
- **Arms:** `baseline` = the engines as the story pipeline called them before this work (one render, no review).
  `revamp` = `produce_for_brief` with the direction written by the managed writer (claude-cli, Sonnet; the
  heuristic fallback is used only when no writer is signed in), three candidates, up to two refinement rounds,
  seed 11 for both. The comparison therefore measures the system *with* its writer, which is how it ships.
- **Metrics:** the `quality.measure` set. Which direction is "better" per metric depends on the brief, so the
  table shows the brief's expectation beside the number: inside the range is the win condition, not "higher".
- **Listening:** `.dev/audio-bench/index.html` plays A and B for each brief in a random, unlabeled order and
  records a preference and a one-line reason to `localStorage`; "Reveal" shows which was which after the vote.
  One listener (the owner) is the planned sample; the page is built so more can be added.
- **What this does not measure:** whether the piece is beautiful, whether the melody is memorable, whether the
  instruments sound real. Those are the owner's call on the page.

## 5. Results

Both arms were measured with the final event detector, and **both are reviewed against the same direction** (the
one the revamp wrote for the brief), so "fit" is comparable. Arrows read old → new. The win condition per column
is "inside the brief's range", not "higher". Render time is for the whole loop (candidates, review, refinement).

| Brief | Old path | New direction | events/s | centre Hz | reading band | sub share | repetition | range dB | fit score (open problems) old → new | render s |
|---|---|---|---|---|---|---|---|---|---|---|
| Quiet medieval village, daytime (ambience) | meadow | forest_air, crowd_murmur, wind | 1.20 → 2.35 | 727 → 594 | 0.15 → 0.09 | 0.30 → 0.00 | 0.09 → 0.09 | 3.5 → 7.4 | 1.00 (none) → 1.00 (none) | 2.6 → 18.0 |
| Mysterious underground dungeon (ambience) | dungeon | cave_resonance, room_tone, wind | 1.68 → 1.32 | 61 → 588 | 0.00 → 0.14 | 0.74 → 0.01 | 0.04 → 0.12 | 5.0 → 3.5 | 0.60 (too_dull, sub_rumble) → 1.00 (none) | 1.6 → 10.4 |
| Royal court atmosphere (ambience) | custom layers | room_tone, crowd_murmur | 2.10 → 1.87 | 578 → 572 | 0.08 → 0.07 | 0.00 → 0.00 | 0.10 → 0.10 | 10.5 → 8.5 | 0.85 (too_busy) → 1.00 (none) | 4.2 → 12.3 |
| Rainy night-time room (ambience) | home_rain | room_tone, rain, fire_crackle | 3.75 → 5.00 | 1242 → 964 | 0.59 → 0.38 | 0.00 → 0.01 | 0.08 → 0.03 | 2.9 → 1.1 | 0.50 (too_busy, in_dialogue_band) → 0.75 (too_busy) | 2.3 → 36.5 |
| Calm fantasy exploration (music) | ambient_drone energy 0.3 | fantasy_village | 0.70 → 2.51 | 346 → 419 | 0.06 → 0.01 | 0.29 → 0.10 | 0.37 → 0.51 | 2.0 → 2.9 | 0.65 (too_dull, repetitive) → 0.55 (too_busy, repetitive) | 1.5 → 17.4 |
| Emotional betrayal (music) | ambient_drone energy 0.3 | melancholy_piano | 0.72 → 0.71 | 351 → 583 | 0.04 → 0.15 | 0.27 → 0.10 | 0.41 → 0.23 | 1.4 → 3.6 | 0.70 (repetitive, flat_dynamics) → 1.00 (none) | 1.4 → 8.2 |
| Subtle political suspense (music) | mystery energy 0.35 | court_intrigue | 2.02 → 0.97 | 419 → 524 | 0.08 → 0.11 | 0.06 → 0.04 | 0.35 → 0.25 | 1.2 → 3.9 | 0.45 (too_busy, repetitive, flat_dynamics) → 1.00 (none) | 2.2 → 10.1 |
| Growing magical tension (music) | mystery energy 0.45 | rising_magic | 1.43 → 0.42 | 418 → 138 | 0.08 → 0.02 | 0.06 → 0.50 | 0.36 → 0.21 | 1.5 → 3.2 | 0.65 (too_dull, repetitive) → 0.85 (too_dull) | 2.2 → 10.0 |
| Heroic power awakening (music) | orchestral_adventure energy 0.7 | heroic_lift | 1.29 → 1.15 | 448 → 690 | 0.09 → 0.20 | 0.05 → 0.01 | 0.44 → 0.36 | 2.4 → 2.0 | 0.65 (too_dull, repetitive) → 0.80 (repetitive) | 2.8 → 6.3 |
| Restrained post-conflict resolution (music) | ambient_drone energy 0.3 | melancholy_piano | 0.70 → 0.65 | 346 → 549 | 0.06 → 0.12 | 0.29 → 0.09 | 0.37 → 0.23 | 2.0 → 4.0 | 0.65 (too_dull, repetitive) → 1.00 (none) | 1.4 → 11.3 |

**Totals [MEASURED]:** open problems across the ten briefs **18 → 5**; briefs with no open problem **1 → 6**;
the three briefs the old path rendered as one identical file (calm, betrayal, resolution: same hash) are now
three different pieces from two templates. Every new piece loops seamlessly, sits at its loudness target and
has no technical failure. Render time rose from ~2 s to 6–37 s per brief: the loop renders three to nine
candidates, all local and free.

**Where it got worse or stayed wrong (read before celebrating):**
- *Calm exploration* is the one regression: the writer's direction picked `fantasy_village` and the arpeggio
  pattern is metronomic (event regularity 0.01) and more self-similar (0.51 vs 0.37). The refinement rounds
  did not escape it because the template, not a parameter, is the cause. Fix: the calm/exploration mood should
  be steered to `fantasy_restrained` in the prompt's template notes (the Unrung run chose it for its own calm cue), and the
  arp density needs a humanised pattern. Both are small; neither is done here.
- *Magical tension* is now a sub-bass drone (centre 138 Hz, half the energy below 80 Hz): the direction asked for
  `sub_bass` under the strings and the loop accepted a 0.85 score without a second round. A stop rule of "no
  open problem or two rounds" instead of "score ≥ 0.8" would have run the `too_dull` refinement. One line, not
  done here so the reported numbers stay those of the code as shipped.
- *Rainy room* still exceeds its event budget (rain-on-roof drops read as events) and lost dynamic range; the
  reading-band share did fall from 59 % to 38 %, which is the complaint that mattered.
- *Heroic awakening* and *tense* pieces remain "repetitive" by the self-similarity measure: a 40-second loop of
  a motif-driven template repeats by design. The measure needs a motif-aware variant before it should steer.


## 6. How to listen

```bash
open .dev/audio-bench/index.html
```

Pick A or B per brief before pressing Reveal; the page keeps the votes. Regenerate either arm with
`uv run python .dev/audio-bench/bench.py baseline|revamp` (the revamp arm takes a few minutes; nothing is paid).

## 7. The Unrung

The story's five music moods and five location ambiences (chapters 1–3) were regenerated through the loop with
the story's own context (title, logline, setting, the world's music direction, genre), a project sound identity
(`restrained orchestral fantasy… percussion only for payoffs`, avoiding synth leads, drum kits and jingles) and
the story keys, so the chosen pieces replaced the old ones in place. Directions were written by the story's
configured writer (claude-cli, Sonnet; cached between runs); three candidates per cue, up to two refinement rounds,
seed 21. The old pieces are what the plain pipeline made on 2026-11-10; both are measured with the final detector
and reviewed against the new direction.

| Cue | Old (mood → template / scene) | New direction | events/s | centre Hz | reading band | sub share | repetition | range dB | fit old → new (open) | rounds |
|---|---|---|---|---|---|---|---|---|---|---|
| tense (music) | mystery | court_intrigue; e 0.28, no percussion, rise_fall | 1.13 → 1.30 | 896 → 522 | 0.28 → 0.13 | 0.03 → 0.02 | 0.35 → 0.44 | 3.2 → 4.9 | 0.80 (repetitive) → 0.80 (repetitive) | 1 |
| dark (music) | horror | melancholy_piano; e 0.22, none, rise_fall | 0.44 → 1.01 | 1141 → 382 | 0.42 → 0.04 | 0.02 → 0.03 | 0.78 → 0.55 | 2.2 → 4.7 | 0.55 (in_dialogue_band, repetitive) → 0.80 (repetitive) | 1 |
| heroic (music) | orchestral_adventure | heroic_lift; e 0.40, light, rise_fall | 1.65 → 0.73 | 1006 → 577 | 0.34 → 0.14 | 0.14 → 0.02 | 0.49 → 0.44 | 2.8 → 5.3 | 0.80 (repetitive) → 0.80 (repetitive) | 1 |
| eerie (music) | horror | rising_magic; e 0.25, none, rise_fall | 1.00 → 1.23 | 455 → 785 | 0.05 → 0.34 | 0.00 → 0.03 | 0.66 → 0.37 | 1.4 → 5.3 | 0.70 (repetitive, flat_dynamics) → 0.80 (repetitive) | 1 |
| mysterious (music) | mystery | fantasy_restrained; e 0.20, none, flat | 1.25 → 1.33 | 560 → 636 | 0.15 → 0.04 | 0.03 → 0.01 | 0.51 → 0.36 | 1.6 → 3.9 | 0.80 (repetitive) → 0.80 (repetitive) | 1 |
| highbell_square (ambience) | location layers | room_tone, wind, crowd_murmur; activity 0.55, cut 80/5000 Hz | 1.97 → 2.10 | 587 → 583 | 0.09 → 0.09 | 0.00 → 0.00 | 0.10 → 0.07 | 6.0 → 7.9 | 1.00 (none) → 1.00 (none) | 1 |
| lowfen_rope_yard (ambience) | river | wind, forest_air, room_tone; activity 0.25, cut 90/5500 Hz | 1.50 → 1.83 | 2225 → 945 | 0.45 → 0.30 | 0.06 → 0.04 | 0.05 → 0.13 | 1.4 → 2.9 | 0.75 (in_dialogue_band) → 1.00 (none) | 1 |
| bellhold_tower (ambience) | location layers | room_tone, cave_resonance; activity 0.15, cut 210/0 Hz | 5.10 → 4.80 | 145 → 214 | 0.01 → 0.02 | 0.16 → 0.04 | 0.02 → 0.03 | 1.9 → 1.6 | 0.60 (too_busy, too_dull) → 0.75 (too_busy) | 3 |
| dungeon (ambience) | dungeon | room_tone, cave_resonance, water_stream; activity 0.15, cut 90/0 Hz | 2.68 → 0.78 | 73 → 1543 | 0.00 → 0.37 | 0.58 → 0.07 | 0.02 → 0.05 | 5.2 → 1.7 | 0.35 (too_busy, too_dull, sub_rumble) → 1.00 (none) | 1 |
| cave (ambience) | cave | room_tone, crowd_murmur, cave_resonance; activity 0.50, cut 90/5000 Hz | 0.32 → 1.35 | 98 → 531 | 0.01 → 0.07 | 0.37 → 0.03 | 0.04 → 0.12 | 2.9 → 13.6 | 0.85 (too_dull) → 0.85 (too_busy) | 1 |

**Totals [MEASURED]:** open problems **14 → 7**; the four reading-band offenders (the `dark` cue at 42 %, the
rope yard at 45 %, the `heroic` and `tense` cues at 34 % and 28 %) all dropped under the 35 % line except the
`eerie` cue, which rose to 34 % because its direction put bell glints and a flute in the top; the two sub-rumble
"places" (dungeon 58 %, cave 37 % below 80 Hz) became rooms with air in them. The pieces that stayed at 0.80 are
all held there by the self-similarity measure (see §5): the music *is* a loop of its motif.

What the directions say, in the writer's own words (`production_comparison.json`, shortened): *tense* "muted low
strings and a slow pizzicato pulse… a thin flute hints at Nell's motif without warmth or resolution"; *dark*
"sparse piano carries the weight of loss while low bowed strings sustain dread… a single flute phrase… left distant
to suggest the silence now in Lucan's hearing"; *heroic* "keeps the approved restrained-orchestral identity… flute
adds a human counter-line echoing Nell's motif, orchestral percussion only for the payoff". The story's motif
(Nell's flute) was carried across cues by the writer because the project identity and the story context were in
the prompt; that is the reference-driven behaviour the brief asked for, and it is the writer's doing, not a
melody engine's.

**Where it stands:** every cue key now points at the loop's pick, status `candidate`; the previous pieces and the
unchosen candidates are `rejected` (nothing deleted). Approval stays with the owner, in the scene audio panel or
the Studio (**Open in Studio** on a cue carries its purpose, chapter soundscape, project and key). The two
`bellhold_tower` and `cave` cues still exceed their event budget: the cave is the Hearing hall as a resonant crowd,
and crowd murmur reads as events; the tower's "far footsteps and metal" are the events the writer asked for. Both
are judgement calls for a listener, which is why neither was forced further.


## 8. Limitations and what is next

- **No ears.** Measured adherence is a proxy. A brief satisfied on every number can still be boring; a reference
  that the owner loves can sit outside every range. The ranges were calibrated on this benchmark and the Unrung
  cues only.
- **The ceiling.** Procedural instruments do not phrase. Heroic and "cinematic" briefs improve in shape (arc,
  motif, percussion policy) but keep a synthetic timbre. The next step is the ACE-Step experiment in §2 (owner's
  go-ahead needed), then CC0 orchestral samples through sfizz/FluidSynth as a cheaper middle.
- **One writer round.** The direction is written once per run and refined by numbers; a second writer round
  that reads the measured problems is cheap to add and is the first thing to try if directions keep missing.
- **Studio hand-off is one way.** A music or ambience cue's "Open in Studio" link carries its purpose, the
  chapter's soundscape summary, the project and the story key; approving in the Studio under that key wires the
  piece into the story. The panel does not yet show the Studio's measured reasons on the cue itself.
- **Melody memorability** is not measured at all. A motif-repetition measure (how often the top voice's contour
  recurs) would be the next metric.
