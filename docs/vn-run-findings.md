# Visual-novel pipeline: bugs, mistakes and improvements

Run under test: **"Static Tomorrow"**, a 12-scene, 3-ending coming-of-age mystery about a 16-year-old boy.
- Written by `claude-cli` / `sonnet` on the Team plan.
- Library: `.dev/live`. Story folder: `.dev/live/stories/static-tomorrow`.

Status legend: **fixed** = already fixed in `feature/vn-stories` · **open** = still to do · **idea** = improvement.

## A. Bugs and mistakes found earlier in this work

| # | What | Impact | Status |
|---|---|---|---|
| A1 | `strict_schema` dropped every *field* named `title` (it treated it as the JSON-Schema keyword) | Codex answered without scene titles; every outline failed validation | fixed + test |
| A2 | When every provider failed, the reported error was the most "actionable" one, not the one you asked for | The real problem (codex output shape) was hidden behind claude's expired login | fixed + test |
| A3 | `MusicRequest.key` meant both the musical key and the asset key | `music compose --key "D minor"` crashed; keyed music requests lost their key | fixed + test |
| A4 | Gemini CLI asks "trust this folder?" and restarts mid-sign-in when run in a folder with settings | Pasted codes failed (`invalid_grant`) | fixed (empty temp dir) |
| A5 | Gemini CLI refuses headless runs in untrusted folders | Every Gemini call failed | fixed (`--skip-trust`) |
| A6 | Codex's own default model (`gpt-6.1-sol`) was rejected by the older CLI | `auto` with codex failed out of the box | fixed (walks the plan's models) |
| A7 | A Finder-launched app has a bare PATH | The Mac app couldn't find `claude`/`codex`/`node` | fixed (`search_path`) |
| A8 | A parent Claude Code session's env vars leaked into the child `claude` | Child CLI auth broke | fixed (env scrub) |
| A9 | Player: a CG stayed on screen for the rest of the scene | Characters and expressions were hidden behind it | fixed (cleared on show/hide) |
| A10 | Player: `display:flex` overrode `[hidden]` on the menu | Save/Load showed on the title screen | fixed |
| A11 | Gemini "signed in" shows ✓ even when Google refuses the free tier | You only find out at the first story call | **open**: verify with one tiny call after sign-in |

## B. Observations from this run (filled in while it runs)

| # | What | Impact | Status |
|---|---|---|---|
| B1 | The Claude provider recorded the **first** model in `modelUsage`. Claude Code makes a small Haiku side call on every run, so jobs said `claude-haiku-4-5` although `--model sonnet` wrote the text (`claude-sonnet-5`) | Wrong model shown in jobs, the writer picker's "worked ✓" and reports | fixed: pick the model with the most output tokens, + test |
| B2 | Every `claude -p` call also bills a Haiku side call (≈500 input tokens) | Small, unavoidable extra plan usage per call | note |
| B3 | Asked for 12 scenes; the outline has **15** (3 branch-only scenes) | Scene count isn't enforced. More calls and more art than planned | open: count "scenes per route" instead, or warn when off by more than 20% |
| B4 | There's no "chapter" concept: you asked for 10 chapters, and AssetForge only knows scenes. Each route here is 11 scenes | Can't ask for "10 chapters per playthrough" | partly fixed: chapter numbers and title cards in the app and player; the wizard asks for "chapters per playthrough". Still open: enforce route length in the outline |
| B5 | **Branches are diamonds**: each early choice changes exactly one scene, then merges straight back (s_dev_hangout/s_meher_poster → s_mist_trail_walk; s_act/s_ignore_broadcast → s_dad_confrontation) | Early choices feel cosmetic | open: ask for branches that last 2+ scenes, and add a "branch depth" check |
| B6 | All 3 endings come from **one free choice** in the final scene. The 4 variables (trust_meher, dev_bond, ripple, dad_secret) don't gate any ending in the outline | Earlier decisions don't decide the ending | open: the outline prompt must tie endings to variables (`require` on ending options), plus a validator warning "endings not influenced by variables" |
| B7 | Timing with Sonnet: world 22 s, cast 65 s, outline 2.6 min, scenes about 1.5 min each with 2 in parallel → about 15 min for 15 scenes | Fine in the background; the app should show "scene 5/15" progress | note |
| B6b | Confirmed in the finished script: variables change 12 times, but **0 choices are conditional**, so the variables have no effect at all | Dead state | open (same fix as B6) |
| B6c | Minor characters barely speak (the mother has 5 lines, the rival 6, across 11 chapters) | The rival thread is thin | idea: cast-balance check in `story check` |
| B8 | Cast is 7 characters with 5–6 expressions each, across 7 locations × up to 3 times of day | Up to about 40 sprites and 19 backgrounds; image cost grows with cast size | idea: art budget (max sprites, expressions only where used — already partly done) |

| B9 | Result for "Static Tomorrow": **15 chapters (11 per playthrough), 3 endings, 12 routes, 310 lines, 4,567 words**, 0 validation errors, 0 automatic fixes. 30 free sound assets made; the art estimate is **57 images ≈ $2.60** (OpenAI) | — | art waiting for your OK |
| B10 | The Mac app showed **no progress** for work started from the CLI or an agent; app tasks lived only in memory | "I can't see any background job" | **fixed**: progress comes from the story files + the jobs table (any source, survives restarts), with a sidebar activity pill, live tracker and ETA |
| B11 | A story could only be played after **all** chapters were written and exported | Nothing to read or play while it's written | **fixed**: chapter rail + reader, and a live preview player (`/preview/<id>/`) with draft stand-ins for unwritten chapters |
| B12 | Wizard with no title used the first words of the idea ("A shy 16-year-old joins the") | Bad titles | **fixed**: the writer names it during world-building (`World.title`, `title_from_writer`); the slug comes from the idea |
| B13 | The story page said "Nothing written yet · Start writing" while an app task was already writing | Confusing | **fixed** |
| B14 | Second run (6 asked → **8** chapters planned) | Same overshoot as B3 | open |

## E. Art run for "Static Tomorrow" (approved by you)

| # | What | Impact | Status |
|---|---|---|---|
| E1 | 57/57 images made (OpenAI `gpt-image-2.5-flare`) in 21 min, 0 failures. **Actual cost $3.24 vs the $2.60 estimate (+24%)** | The estimate is low | open: calibrate `estimate_call` from these 57 jobs (it already uses history once there is some) |
| E2 | Characters stayed consistent across every expression and in every CG; day/evening/night versions kept the same layout | References work as designed | good |
| E3 | Full-body sprites: at VN size the faces are tiny, so expressions (neutral/smile/worried) are hard to tell apart | Expressions add little | **fixed in the player**: knees-up framing (taller sprites anchored below the stage). Idea: generate thigh-up sprites with stronger expressions |
| E4 | The generated dialogue box and choice frames had transparent margins, so they stretched badly and text sat on the frame's edge | Ugly textbox | **fixed**: transparent images (sprites, UI) are trimmed to their content (`web-sprite` processing); the 38 existing ones were trimmed in place |
| E5 | The first sprite *looked* like it had an orange halo, but those pixels are fully transparent | none | false alarm |
| E6 | Art is a separate step after writing; nothing appears while you read | You wait for art | idea (D3): make each chapter's art as soon as it's written, with a cost meter |

## F. Sound: "chapter 1 sounds like static" (your report)

| # | What | Impact | Status |
|---|---|---|---|
| F1 | The house ambience ("creaking floorboards, ticking clock, distant dog barks, rain on the tin roof") was **word-matched to the `haunted_house` scene**: room tone −4 dB + howling wind + outdoor rain, plus crows, a wolf howl and a tolling bell. No clock, no dog | A wall of noise: **12% of energy above 4 kHz, noisiness 0.34** = static | **fixed** |
| F2 | AssetForge's rain bed is modelled on **outdoor** recordings (35% sizzle above 4 kHz); a story heard from indoors needs muffled roof drumming | Rain read as static | **fixed**: `rain.inside` (roof "tinks", warm wash, no sizzle: 1.26 kHz centre, 0% above 4 kHz); outdoor rain unchanged bit for bit |
| F3 | No sounds for a ticking clock or a dog | Details from the story were dropped | **fixed**: `clock_tick` bed (seamless grid), `dog_bark_far` event, `home_interior` / `home_rain` scenes |
| F4 | Word matcher: "distant" summoned a wolf howl, a scene's own layers were added again at −4 dB, and extra beds were loud | Wrong and harsh layers | **fixed**: vague words ignored, scene layers count as covered, extra beds at −12 dB |
| F5 | **The real fix**: the writer now plans each story's sound from the actual catalog (`sound.json`). It can't use unknown names; noise beds are capped (room tone ≤ −20, rain/wind/crowd ≤ −12); and layers already in the chosen scene are dropped (it once added a second clock out of step) | Every place sounds like the story says | **fixed** + tests |
| F6 | `strict_schema` turned free-form maps (role → instrument) into empty closed objects, so the writer could never name instruments | Music ignored the story's instruments | **fixed**: list of {role, instrument} pairs. Note for future models: avoid `dict` fields in LLM schemas |
| F7 | Quiet interiors became a **low hum or drone**: room tone's 50 Hz hum and sub-bass, raised by loudness matching (library centred at 45 Hz) | Droning rooms | **fixed**: `room_tone.low_cut`, and story plans always use room tone without hum and with a low cut |
| F8 | `--redo` that rendered **identical** audio resolved to the copy it had just archived, leaving 5 places silent | Missing ambience after a remake | **fixed**: an identical remake is restored, + test |
| F9 | Music: "calm" used to be synthwave (the word matcher keyed on "synth pads"). Now lofi with guitar, warm pads and soft drums, following the music direction | More fitting score | improved |

Result for "Static Tomorrow": all 7 ambiences are centred at 475–777 Hz with ≤ 1.6% above 4 kHz and within 3 dB of each
other. Chapter 1 went from 12% hiss / noisiness 0.34 to 0.7% / 0.00.

## C. Code review (independent reviewer, read-only)

C1 and C7 were confirmed by me and fixed right away. The reviewer confirmed C2–C5 by running them offline. The rest
come from reading the code.

| # | Sev | Where | What | Scenario | Status |
|---|---|---|---|---|---|
| C1 | **high** | `vn_player/index.html` character placeholder | Model-written character name inserted as HTML | A name like `<img onerror=…>` runs script in the app's origin (with its auth cookie) | **fixed** (text only) |
| C2 | med | `story/assets.py` `produce` | Stops the whole run at the first auth/quota/not-configured error, and images come first | No image key → no music, ambience or SFX either, although sound is free | **fixed**: only images stop, + test |
| C3 | med | `steps.write --scene/--rewrite` | Same prompt → cached answer comes back | "Rewrite" is a no-op; after a `refine` it silently restores the old draft | **fixed** (fresh), + test |
| C4 | med | `repair_outline` | Doesn't check that every scene can reach an ending | A looping outline costs every scene call before `assemble` catches it | open |
| C5 | med | `exporters/vn.py` | Two keys on one asset (shared SFX via search) keep only the first key | Second cue is silent and not listed as missing | open |
| C6 | med | `llm/login.py` | "Sign in again" ends at once because `signed_in()` is already true | Can't switch accounts or repair an expired login | **fixed** (waits for the tool; Gemini's old login set aside and restored on failure) |
| C7 | med | `llm/runner.classify` | "Connection refused" was read as a content refusal; `log ?in` matched "catalog index" | A network blip stopped the whole chain with a misleading error | **fixed** + tests |
| C8 | med | `steps.write` | Scenes written for an old outline are kept after re-outlining | Jumps to removed scenes; validation errors with no hint | open |
| C9 | med | player save/load | Restores scene and index blindly | After `improve` shortens a scene, Continue lands on a blank screen | **fixed** (versioned saves, clamping) |
| C10 | low | player | If every option in a choice has a failing condition, no buttons show | Game stuck | **fixed** |
| C11 | low | `steps._unique_ids` | `_2` suffix can exceed the 48-character id limit, or collide | `cast.json` fails to load | open |
| C12 | low | `api_story` `/run` | Missing `scene` or a string `limit`/`scenes` → internal errors | Confusing 500s | open |
| C13 | low | `llm/login.py` | Double close of the pty fd possible; sessions never pruned; leak if popen fails | Rare fd reuse; memory growth | partly fixed (double close guarded) |
| C14 | low | `prefer_google_login` | Permanently switches the Gemini CLI to Google login | Breaks a Gemini CLI set up for API key or Vertex | open |
| C15 | suspected | codex parser | Any JSONL `error` event fails the call even when an answer exists | Transient "reconnecting" events discard good answers | to check |
| C16 | suspected | `structured` | Non-provider exceptions leave the text job "running" | Stale job rows | to check |

## D. Scope of improvements (not done yet, ranked)

1. **Story structure that matters** (B5, B6). The outline prompt and validator should require:
   - endings gated by variables;
   - branches at least two chapters long;
   - at least one conditional choice;
   - a reported "choices that matter" score.
2. **Hit the length asked for** (B3, B14). Plan "chapters per playthrough" exactly, and check every route's length.
3. **Art as chapters finish**: optionally make each chapter's backgrounds and sprites as soon as it's written, with a running cost meter and a budget cap. Today art is a separate step after writing.
4. **Pick up loose ends from the review**: C4 (looping outline), C5 (shared SFX keys), C8 (stale scenes after re-outlining), C11, C12, C14, C15, C16.
5. **A notification when a story finishes** (macOS notification through the app shell), plus a "copy story link" for teammates.
6. **Story management**: duplicate, archive and delete stories; a version history of each chapter (the `.back` files exist but aren't shown).
7. **Exports for real engines**: Ren'Py and Ink, plus a PixiJS scene template; the script JSON is already engine-neutral.
8. **Gemini through an AI Studio key** (planned earlier; one setting enables it as a fallback today).
9. **Verify a Gemini sign-in with one tiny call** (A11), so "signed in" means "can write".
10. **Voice lines** (TTS per character) and a lip-flap/talk animation on sprites.
