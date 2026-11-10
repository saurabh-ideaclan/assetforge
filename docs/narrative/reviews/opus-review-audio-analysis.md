# Review: the Audio Analysis Engine (`audio/analysis/`, correction ops, `quality.events`)

Reviewer stance: DSP / MIR engineer and game sound designer. Scope: every module in `src/assetforge/audio/analysis/`,
the correction ops at the end of `src/assetforge/audio/process.py`, `audio/quality.py` (`events`, `expectations`),
`validation/audio.py` (`loop_seam`, which the engine reuses), `cli_analysis.py` (the correct flow) and the
`.dev/audio-lab` harness. Line numbers are from 2026-11-11 ~02:30 IST. **The code was changing while I reviewed it**
(`diagnose.py` changed at 02:31, `corpus.json`/`corpus.py` at 02:29, `results.json` was rewritten at 02:32, and the
expectations for `music_unrung_tense` and `cave_rumble` changed between two of my reads). See §5.2: that is a
methodology problem, not just bad timing.

Claims marked **[probe]** were checked by running the code on synthetic signals (script:
the session scratchpad `probe.py`). Everything else comes from reading the code.

---

## 1. Correctness findings

### 1.1 Speech / voice-likeness (`analysis/speech.py`): the main deliverable has the worst bugs

| # | Where | Problem | Fix |
|---|---|---|---|
| S1 | `speech.py:135` | The formant bandwidth is wrong by 4×. Pole radius r ↔ bandwidth is B = −(fs/π)·ln r; the code uses −fs/(4π)·ln r. **[probe]** r = 0.97 at 16 kHz gives 155 Hz true, 39 Hz computed. The "narrow resonance < 300 Hz" gate (line 137 comment) actually admits up to 1.2 kHz, which is exactly the broad noise poles the comment says it rejects. **[probe]** Band-passed wind noise gives a formant cue of 0.61. | `bw = -(sr / np.pi) * np.log(np.abs(roots))`. Then re-tune the 300 Hz gate. Real F1/F2 bandwidths are 50–250 Hz; noise poles are wider than 400 Hz. |
| S2 | `speech.py:122-149` | LPC on a harmonic signal locks onto the **harmonics**, not formants. A melody or string note with f0 in 300–900 Hz gives "F1" = f0 and "F2" = 2·f0 (difference > 300 Hz, so the vowel test passes), and F2 "moves" whenever the melody moves. **[probe]** A sawtooth melody gives vowel_share 1.0 and a cue of 0.43. A 440 Hz tremolo tone gives 0.68. | Reject candidates whose frequency is within ±4 % of k·f0 (f0 is already estimated in `voicing_cue`). Or estimate the envelope by cepstral liftering (cut-off below the f0 period) and pick its peaks. Also require F1 bandwidth > ~40 Hz, since a harmonic pole has a near-zero bandwidth. |
| S3 | `speech.py:64-65` | The tremolo penalty can never fire. Peakiness is max/mean over 2–8 Hz in a 1 s window, where the Hann main lobe is ±2 Hz wide (zero-padding to 1024 adds no resolution). A pure 6 Hz tremolo scores 3.9; the threshold is 12. **[probe]** Tremolo cue = 1.0, peakiness 3.9. | Use 3 s windows with a 1 s hop (0.33 Hz resolution) and detrend the envelope (wind gusts leak in as a trend). Measure tremolo as the share of 2–8 Hz energy within ±0.5 Hz of the peak; above 0.6 is one rate (tremolo or LFO), while speech spreads over 3–6 Hz. |
| S4 | `speech.py:183-186` | The crowd rule (m ≥ 0.3, v ≥ 0.4, f ≥ 0.4) is an OR-bypass of the 3-cue/0.6 rule, and S1–S3 make all three of its cues cheap for any moving tonal sound. **[probe]** Tremolo tone: voice_like share 1.0. Sawtooth melody: 0.875. Both pass through the crowd rule. This is why 19/30 music files in `results.json` are `suspected`, and why the approved `sfx_lwp_sfx_door_open` (a creak) is `confirmed`. | After S1–S3, the crowd rule must also require pitch irregularity (glide or jitter above a floor) **and** no single dominant modulation rate. |
| S5 | `speech.py:83-84` | The autocorrelation uses the biased estimator normalised by ac[0], so lag L is attenuated by (1 − L/N). At 80 Hz (L = 200, N = 640) the peak is capped near 0.69. Low male or creature voices are under-scored, and argmax prefers short lags (octave-up errors). `np.correlate(...,"full")` per 10 ms frame is O(N²), about 41 M MAC per second of audio. | Use the normalised difference function (YIN CMNDF) or divide by the overlap energy. Compute it with an FFT. |
| S6 | `speech.py:97-99` | "Glide" counts frame-to-frame f0 changes of 1.5–8 % per 10 ms. Real intonation moves about 0.3–1.5 % per 10 ms, and vibrato peaks near 1.1 %. A 1.5–8 % step is almost always **tracker jitter or octave noise**, so the cue rewards an unstable tracker (noise, crowds, creaks) and ignores real speech glides. | Measure glide as the median absolute slope of a median-filtered f0 track in semitones/s over voiced runs ≥ 100 ms (speech: 2–20 st/s; held note: < 1; vibrato: oscillating around 0). Count jitter (period-to-period) separately. |
| S7 | `speech.py:196-201` | "Confirmed" requires only that **both** detectors fire somewhere in the file, not at the same place. DSP at 10 s and VAD at 50 s gives "confirmed", which `diagnose.py:239-240` turns into `measured` / `hard`. | Confirmed = overlap of DSP voice-like seconds with VAD segments (prob ≥ 0.6) totalling ≥ 0.5 s. |
| S8 | `speech.py:196,198` | Both triggers are **share** based (> 10 % of seconds), so a 2–3 s mumble in a 60 s loop (the owner's actual complaint) only fires through `max_score ≥ 0.7`, and never on the VAD side. `ambience_wind_with_faint_murmur` is missed. | Use event criteria: any co-located run ≥ 0.5 s, plus a share. Use 1 s windows with a 0.5 s hop so a syllable cluster is not split at a window edge. |
| S9 | `diagnose.py:44,48` | Bed names are added verbatim: `"crowd_murmur"` does not match `\bcrowd\b` because `_` is a word character. **[probe]** `_brief_allows_voices(None, {"beds":[{"bed":"crowd_murmur"}]})` is `False`. An ambience whose direction *asks* for a crowd is failed hard for having one. | `.replace("_", " ")` before matching. Add chant, whisper, vocal, laughter, children, monks, singer to `VOICE_WORDS`. Add a creature word list that suppresses `voice_like` (see §3). |

Silero integration (`silero.py`) is correct for v5: 64-sample context + 512, state (2,1,128), int64 sr, float32 input.
**[probe]** On the tremolo and melody signals it returns speech_share 0.0 (max_prob 0.07 / 0.16). The VAD is the
better of the two detectors on tonal music. The DSP cues are currently the weaker vote.

### 1.2 Technical layer (`analysis/signal.py`)

- **BS.1770 integrated and gating (`signal.py:63-71`)**: correct. 400 ms blocks with a 100 ms hop, −70 LUFS absolute
  gate, −10 LU relative gate on the power mean. **K-weighting reuse** (`loudness.k_weighting`) is the libebur128
  re-derivation. The unnormalised HP numerator `[1,-2,1]` matches the standard's 48 kHz table, so it is fine. One
  issue: this module and `loudness.integrated_loudness` are two implementations of the same thing. Delete one, or
  test them against each other on EBU Tech 3341 signals.
- **LRA (`signal.py:72-79`)**: correct per Tech 3342 (3 s short-term, −70 absolute gate, −20 LU relative gate on
  the power mean, 10th–95th percentile). Linear-interpolated percentiles differ from nearest-rank by < 0.1 LU, which
  is fine. LRA on a 20 s loop rests on about 170 heavily overlapped values. Report n with it.
- **True peak (`signal.py:33-36`)**: `resample_poly(x, 4, 1)` uses an 81-tap Kaiser(5) interpolator, which is better
  than the 48-tap filter in Annex 2. Fine. But `op_normalize` (`process.py:88-101`) limits on **sample** peak
  (`ceiling_db`), so the plan's final LUFS normalise can push true peak back over −0.5 dBTP, and `no_headroom`
  reappears as a regression. The ceiling should use `true_peak_dbtp`.
- **Clipping (`signal.py:21,84-94`)**: `CLIP_LEVEL = 0.999` (validation uses 0.9999) on **decoded OGG/M4A** (the
  corpus is .ogg). Lossy decoders overshoot, so a master that was fine can show 3 samples ≥ 0.999 in a run of 2 and
  get a **hard** "clipping cannot be undone". Detect flat tops instead (≥ 3 consecutive equal or near-equal samples
  ≥ 0.99), and run technical checks on the WAV master when one exists.
- **Clicks (`signal.py:122-134`)**: |Δx| > 8 × the 256-sample mean |x|. For white-ish noise this is a ~4.5σ event in
  Δx, roughly 0.3 false clicks/s at 44.1 kHz (an estimate from the normal tail, not a probe), plus every real
  transient after near-silence. That explains `click_limit = 50` for ambience (`diagnose.py:77`), which in turn hides
  real synthesis clicks in beds. Use a residual detector: predict x from an order-16 LPC fitted over ±10 ms and flag
  residual spikes > 8 × the local residual MAD. Noise and transients predict poorly everywhere; a click is a single
  outlier.
- **Stereo correlation (`signal.py:137-158`)**: the Pearson-without-mean formula is fine. But one full-band number
  is dominated by bass. Report correlation for < 200 Hz (must be > 0.5 for mono-safe low end) and > 2 kHz separately.
  Decorrelated ambience at ≈ 0 is normal, not a warning.
- **Loop seam (`validation/audio.py:88`, used by `signal.py:179`)**: `loop_click` needs `jump > max(0.05, …)`. The
  absolute 0.05 floor makes seam clicks **invisible at ambience levels**. **[probe]** A 25×-max-step jump in a
  low-passed bed peaking at 0.03 is not a click; the same file +10 dB is. Ambience is mastered at about −24 to −28
  LUFS, so most ambience seams go unchecked. That is likely why `music_hard_cut_loop` needed relabelling. Fix:
  `jump > max(1e-4, 4 × the p99 of |Δx| over ±50 ms)`. Add a spectral term: the log-mel distance between the last
  and first 46 ms frames against the p95 of adjacent-frame distances.

### 1.3 Spectral layer (`analysis/spectral.py`)

- **Tilt reference is wrong (`spectral.py:86-87`)**: tilt is fitted on **octave-summed** power, and pink noise has
  equal power per octave. **[probe]** Pink gives −0.08 dB/oct and white gives +2.99, not "pink ≈ −3". So
  `interpret.py:47-48` ("> −1.5 = top-heavy") calls a pink-balanced mix top-heavy, and `dated_palette`'s
  `tilt < −8` (`diagnose.py:205`) is 3 dB/oct stricter than intended. Fix the docstring and recalibrate both
  thresholds on the approved corpus.
- **Band shares after rumble became (0, 80) (`spectral.py:20`)**: with a 2048-point FFT the rumble band is bins
  0–3 (0, 21.5, 43, 64.6 Hz), so it now includes **DC and infrasonic** energy. A DC offset is counted twice
  (`dc_offset` and `sub_rumble`; see the synthetic `rumble_only` row, which fires both). Start at 20 Hz, or run the
  shares after removing DC. More fundamentally, all shares are **unweighted power**, so any normal mix puts 40–70 % of
  its power below 250 Hz. "Dialogue share 35 %" is then a proxy for "has little bass", not for masking. This is the
  main reason 7 of 12 approved ambiences get `in_dialogue_band` (see §5). Compute shares on K-weighted power (or
  loudness per band), and state shares as LU relative to integrated loudness.
- **HPSS (`spectral.py:62-67`)**: the masks are correct (Wiener, p = 2; median along time for H and along frequency
  for P). But for stationary noise both medians are equal. **[probe]** White noise gives percussive_share 0.50;
  modulated band noise gives 0.48. So `percussive_share` is about 0.5 for every rain or wind bed. That feeds
  `tempo_unclear` (> 0.3), `interpret._texture` ("tonal with clear attacks") and the reference profile. Add a third
  "residual" class (Driedger 2014: H and P margins, the rest is noise). Report percussive share only of H+P.
- **Mel filterbank (`spectral.py:42-51`)**: the triangles are not area-normalised, so wide high bands collect more
  power (≈ +10 dB at the top vs the bottom with this layout). This biases `density.occupancy` (30 dB rule) toward
  high bands. Also, mel_db is floored at 1e-12, so near-silent bands contribute random ±dB noise to the
  self-similarity vectors. Normalise by width (Slaney) and clip mel_db at frame-max − 80 dB.

### 1.4 Musical layer (`analysis/music.py`)

- **Chroma folding (`music.py:23-31`)**: with 21.5 Hz bins, everything below ~360 Hz spans more than one semitone
  per bin, and the Hann main lobe (±43 Hz) puts each bass note into 2–3 pitch classes chosen by bin rounding.
  **[probe]** A pure A2 (110 Hz) folds to A 0.66, C 0.23, F 0.12. That is an F-major-ish smear from one note, and bass
  dominates power chroma. Fix: start chroma at 250–300 Hz with this FFT, or compute it from an 8192-point FFT with
  triangular per-semitone weights. Use sqrt or log magnitude, not power, so one loud partial does not dominate the
  Krumhansl–Kessler (KK) correlation.
- **Harmonic changes (`music.py:44-55`)**: the docstring says 0.35 and the code uses 0.5. **[probe]** C→Am and C→G
  give cosine distances of 0.29 and 0.29, so the detector **never counts ordinary diatonic changes**. `changes_per_min`
  ≈ 0 for most real progressions, so `harmonic_stasis` and the repetitive finding's "harmony barely moves" evidence
  are produced by the measure itself. Use beat-synchronous chroma (or 0.5 s blocks), a threshold calibrated on
  rendered progressions (≈ 0.15–0.2 after the chroma fix), and report changes per bar when a tempo is known.
- **Foote novelty (`music.py:68-73`)**: the checkerboard sign is right (within-segment +, cross −). There is no
  Gaussian taper, so the novelty curve is noisy, and `mean + 1.5 sd` on a 30 s loop will almost always find a
  "section". Taper the kernel and require a peak prominence of 0.1 of the maximum.
- **Repetition index**: max over lags of the mean diagonal similarity. With few blocks at long lags this max is
  biased upward (a max of noisy means). `quality.measure` has a *different* `repetition_index` (25 log bands,
  frames, hop 1024) with a different threshold (0.32 vs 0.5/0.6). Same name, two scales: rename one.
- **Tempo**: `conf ≥ 0.15` on a raw autocorrelation peak is a low bar. The `rate ≥ 0.5` gate does most of the work.
  Acceptable for "pulse or not".

### 1.5 Events (`quality.events`, `analysis/events.py`)

- **SuperFlux (`quality.py:50-72`)**: two departures from Böck & Widmer. (a) The maximum filter runs on **linear**
  bins (±3 bins = ±65 Hz everywhere). SuperFlux filters a log-frequency (24 bands/oct) spectrogram so the widening
  scales with frequency. Here a 3 % string vibrato on a 3 kHz partial (±90 Hz) still leaks, while ±65 Hz at 100 Hz
  is wider than a semitone. (b) A global threshold (`max(mean + 1.5 sd, 2.5 × median)`) misses events in quiet
  passages of a piece with a loud section. Also, with equal-weighted linear bins, ~75 % of the flux comes from bins
  above 5 kHz, so the count is biased toward HF noise bursts. **[probe]** The detector *is* level-invariant (1.40 vs
  1.45 events/s across a 50 dB gain range for 30 bursts in 20 s), which is good. Fix: log-filterbank before the
  maximum filter, plus a moving-average threshold (±1 s) with a δ.
- **CV as "mechanical" (`diagnose.py:171-173`)**: one CV across every detected event is bimodal for two
  interleaved periodic streams and ≈ 0 for any quantised groove. Quantised MIDI is a grid by construction, so CV < 0.05
  is "it was sequenced", not a defect. The evidence string claims "no velocity life", which is **never measured**.
  Measure the CV of onset peak levels (velocity proxy) and the deviation from the nearest tempo-grid position (ms).
- **`events.py:58`**: "below −60 dBFS" is actually `level_db < −100` in this STFT's scaling. **[probe]** A −60 dBFS
  sine reads −70, so the gate is about −90 dBFS. **`events.py:88`**: `3 <= peak_bin` excludes bin 2 (43 Hz), so
  **50 Hz mains hum cannot be detected** (and 60 Hz sits on bin 3 at the edge). Use an 8192–16384-point FFT for hum
  and check the 50/100/150 or 60/120/180 comb. Windows are `round(frame_rate)` frames (0.9985 s at 44.1 kHz), which
  drifts 0.27 s over 180 s against the 1 s speech segments they are joined with (`speech_by_s` by `int(start_s)`).
  Join by time overlap.

### 1.6 Corrections (`process.py`, `analysis/corrections.py`)

- **`op_balance` sign (`diagnose.py:103` + `process.py:264-272`)**: the finding passes `db = −balance_db` and the
  op's positive value moves level to the right. **[probe]** A 6.02 dB left-heavy file becomes **12.04 dB**
  left-heavy. The correction doubles the defect. Pass `+balance_db`.
- **`op_narrow` for `phase_cancellation` (`diagnose.py:99-100`)**: narrowing keeps mid and scales side. The mono sum
  *is* 2·mid, so it is **unchanged**. **[probe]** Mono RMS 0.0996 before and after. Correlation stays −1.0. Real
  fixes: a polarity flip when broadband correlation < −0.7, a per-band polarity/delay alignment (cross-correlation
  lag < 1 ms) when one band is anti-phase, or a mono low end below 150 Hz when the lows are anti-phase. Note that
  mono-ing also leaves mid, so it only helps when the anti-phase content is in side.
- **Clipping (`diagnose.py:66-69`)**: `prefer="precise"` ranks the `gain −6` DSP option first. The appended LUFS
  normalise then adds the 6 dB back (up to the ceiling). Net effect: nothing, reported as a correction. Either
  implement declipping (AR/least-squares interpolation of flat-top runs ≤ 2 ms, which is about 30 lines of NumPy) or
  mark that option `kind: "none"` so the planner skips it.
- **`too_dull` ambience → `highpass 120` (`diagnose.py:156-158`)** and `quality.REFINEMENTS["too_dull"]`
  (`low_cut: 120`): removing the lows raises the centroid metric without adding any air. This is metric gaming. Use
  a high shelf (+3 dB at 4 kHz) or an added air layer.
- **`hissy` → `lowpass 7000` (`diagnose.py:140-143`)** fires regardless of brief, and rain legitimately has > 12 %
  of its power above 8 kHz. The correction removes the rain. Gate it on the rain/water/steam signature and the brief.
- **Filters and dynamics on loops**: `op_peak_eq`, `op_shelf`, `op_dialogue_dip` (`lfilter`, zero initial state),
  `op_highpass`/`op_lowpass` (`sosfiltfilt` with edge padding) and `_envelope_db` (starts at 1e-6) all create a
  start/end transient. On a loopable asset that **creates the seam defect the engine then reports**. Process loops
  circularly: prepend the last 0.5–1 s, filter, drop the prefix (and for filtfilt, wrap both ends).
- **`op_reloop` (`process.py:222-233`)**: the wrap is sample-continuous (out[−1] = x[−n−1] and out[0] = x[−n]), and
  the reversed sine ramp is the cosine. Correct. But: (a) the docstring's "fade placed at the quietest point" is not
  implemented; (b) it **shortens the file by 120 ms**, so a bar-aligned music loop loses its grid and skips a beat
  every cycle, which `loop_seam` cannot see (it measures one sample step and 250 ms RMS); (c) equal-power gives a
  +3 dB bump on correlated (tonal) material, which needs equal-gain. Keep the length: crossfade the tail into a
  pre-roll copy of the head, or for music cut on a bar and crossfade over a beat. Then test the seam with an onset
  grid phase check.
- **`op_replace_segment` donor (`corrections.py:82-97`)**: one donor (the lowest-score second ≥ 2 s away) is reused
  for **every** patch, so adjacent flagged seconds become two copies of the same second, an obvious repeat in a bed
  with drips or birds. The lowest speech score also tends to be the quietest or most static second, so the patch
  dips in level. The donor is not excluded from other patch regions and is not level- or spectrum-matched. The
  60 ms crossfade sits *inside* the patched region, so 60 ms of the voice stays at each edge. Fix: merge adjacent
  segments, choose distinct donors by minimising log-mel distance to the patch's neighbourhood under the constraint
  "not voice-like and not patched", RMS-match, and crossfade outside the region.
- **`correct_file` re-diagnoses without the brief (`corrections.py:138`)**: `diagnose(after_report, kind=kind)`
  drops `brief`, `direction` and `loop`. Without a brief, `voice_like` becomes `note` and is filtered out of
  `compare`, so it reports **"fixed"** when nothing was fixed. The same applies to direction-only findings
  (too_bright, too_busy) and loop-only ones. Pass the same arguments as the "before" call.
- **Envelope followers (`process.py:275-284`)**: a Python per-sample loop. **[probe]** 0.6 s per stereo minute
  (1.9 s for 180 s), which is acceptable offline but not inside a correct→re-measure loop × candidates × rounds.
  The detector is a peak follower on |x| (not RMS). **[probe]** 4:1 compression of a 50 Hz sine gives 0.31 % THD,
  which is acceptable at the gentle defaults. To vectorise: run the attack/release recursion at a 1 ms control rate
  on block maxima (44× fewer iterations), then interpolate the gain. `op_expand` peak-normalises to 0.98 *after*
  expanding, and the plan's later LUFS normalise is ceiling-limited, so an expanded file can land under target
  (`loudness_off` regression).
- **Plan merging (`corrections.py:58-63`)**: numeric composition deltas are summed across findings that measure the
  same thing (in_dialogue_band warmth +0.4 and too_bright warmth +0.4 make +0.8). Deduplicate by parameter and take
  the max, not the sum.

## 2. Taxonomy review (`diagnose.py`)

### 2.1 Labelled `measured`, actually heuristic or calibrated guesses

"Measured" should mean a standardised measurement against a standard or contractual limit (LUFS target, dBTP, clip,
DC, sample-continuous seam). These are not:

| Code (line) | Why it is not `measured` |
|---|---|
| `clicks` (79) | A threshold detector with a known false-alarm rate on noise and transients (§1.2). Use `estimated` for ambience and sfx. |
| `in_dialogue_band` (145) | The share is measured, but the threshold and the harm model ("reading attention lives in 1–4 kHz") are hypotheses. Text on screen is not masked by audio. Masking only applies when there is voice-over. 7/12 approved ambiences trip it. Should be `estimated`, and `note` unless the project has VO. |
| `hissy` (141) | "No place hisses that much" is a judgement, and wrong for rain or steam. |
| `too_bright` / `too_dull` (152/156) | The range comes from an invented mapping `250 + 400·brightness` (`quality.py:116`). |
| `too_busy` / `too_static` (166/169/217) | The events/s vs "energy" mapping is invented. The detector is heuristic. |
| `mechanical` (173) | The evidence claims velocity, which is not measured (§1.5). |
| `flat_dynamics` (197) | LRA is measured, but "nothing rises or falls" is a fault only against a brief. Under-dialogue VN loops *should* be flat-ish, which contradicts the `in_dialogue_band` rationale. |
| `harmonic_stasis` (200) | It is produced by the broken chord-change measure (§1.4). |
| `mechanical_events` / `static_bed` (220/223) | They rest on heuristic events. |
| `voice_like` confirmed (239) | Two heuristics (one of them a trained model) agreeing is still an estimate, and today they need not even agree on the same second (S7). Use `estimated`, always with `listen` pointers. Nothing in this engine should be `measured` + `hard` without a listener for an *intent* finding. |
| `voices_present` (246) | Same as above. |

Add a `basis` field to every finding: definition, threshold, threshold source (standard / brief mapping / corpus with
n), and the detector's measured false-alarm rate on approved assets. Without it, "measured" carries no checkable meaning.

### 2.2 Wrong cause classes

- `clipping` → `mix` (66). In generated assets this is almost always rendering gain staging (`synthesis`, or `method`
  for a provider). Gain was never mixed.
- `mechanical` → `synthesis` (173). The cause is performance or sequencing (`composition`/`method`), and the
  correction is `humanize`, a composition field. The cause and the correction disagree.
- `too_bright` / `in_dialogue_band` for music → `mix` (145/152), with a composition correction (warmth, melody).
  Orchestration is composition. Pick the class the correction acts on.
- `loop_seam` → `sound_design` (86). It is a render/edit method failure (non-loop-aware render), so `method`.
- `voice_like` → `source` (239) is right for ambience beds. For music it is an instrument choice (choir or vocal
  patch, `composition`).
- **`emotion` is declared (line 9) and never emitted.** No finding has `cause="emotion"`. The class exists only in
  the docstring.

### 2.3 Corrections that do not address the stated cause

`stereo_balance` (inverted sign), `phase_cancellation` (mono sum unchanged), `clipping` (gain then re-normalise),
`too_dull` ambience (high-pass), `hissy` on rain (removes the sound), `over_compressed` → `expand` (cannot restore
limited transients; at best a cosmetic LRA bump), `voice_like` → `drop_layer crowd_murmur` hard-coded whether or not
that bed exists (and the precise planner picks `replace_segment` first anyway, which patches a *source* problem with
a band-aid). `dc_offset` → `dc_remove` and `sub_rumble` → `highpass` do address their causes.

### 2.4 Symptoms from the brief with no detector

1. **Mood / emotion mismatch**: the whole `emotion` class. Proxy: an arousal estimate (loudness, events/s, centroid,
   tempo) and a valence estimate (mode clarity from KK major−minor, roughness, register), both regressed on the
   approved/rejected history with the brief's mood. Always `estimated`.
2. **Dissonance / sourness / detuning** (cheap synth music): sensory roughness (Sethares/Vassilakis over the top 20
   spectral peaks per frame), and cents deviation of peak frequencies from the 12-TET grid after tuning estimation.
3. **Unintended tonal artefacts in noise beds** (whistling filter resonances, ringing, "singing wind"): persistent
   narrow peaks (> 15 dB above the local median, lasting > 2 s) in a bed whose brief is non-tonal. This is also the
   right home for wind howl (§3).
4. **Comb / phasey / "underwater" colouration** (short-delay chorus, codec swirl): a cepstral peak at 0.5–20 ms
   quefrency in noise frames, and spectral-ripple periodicity.
5. **Aliasing / buzzy synthesis**: energy above 0.35·fs not harmonically related to the detected f0; foldover
   partials mirrored around Nyquist.
6. **Audible repeated one-shots** (the same drip every 4 s, "machine-gun" repeats): cluster event spectrogram
   patches (64 mel × 150 ms, cosine > 0.95). Report the repetition count and period.
7. **Startle / distraction under text**: momentary-loudness jumps > 6 LU within 400 ms over the bed's short-term
   level. This is the measurable form of "it pulls attention off the line".
8. **Truncated tails** (sfx and stings): the last 50 ms sits above −40 dB relative to the peak with a decay slope that
   predicts more tail.
9. **Codec damage in the delivery file**: compare the delivery OGG/M4A with its WAV master (spectral difference > 16 kHz
   cut-off, pre-echo before transients).
10. **Loop rhythmic discontinuity**: onset-grid phase across the wrap (§1.6 reloop).

## 3. Speech-likeness design

Corroborating cues plus a VAD is the right shape. As implemented, the cues are not independent: modulation, pauses and
the crowd rule all read the same 300–3400 Hz envelope, and voicing and formants both reward *any* harmonic sound
(S2, S5, S6). So "≥ 3 of 4 cues" mostly means "a moving, harmonic, amplitude-modulated sound". The required
`f ≥ 0.3` gate is a single point of failure that S1 and S2 have broken.

Expected behaviour today (P = probe, R = reasoning):

| Input | Today | Why | After the fixes |
|---|---|---|---|
| Tremolo strings | **FP** (P: share 1.0) | modulation 1.0 (S3), voicing 0.5, harmonic "formants" 0.68 → crowd rule | tremolo rate test + harmonic rejection → negative |
| Choir pad | FP whenever chords move (R): real vowel formants, harmonic, slow attack; usually saved by low modulation | – | still voice-like *by nature*: classify as "sung vowels", allowed when the brief says choir; otherwise `listen` |
| Bird calls | mostly TN (P: chirps give voicing 0.0, formants 0.12). **FP risk for low birds** (doves, owls, frogs at 300–800 Hz: harmonic hoots, 2–4 Hz syllables) | – | harmonic rejection + glide-shape (birds sweep > 50 st/s) |
| Wind with howl | borderline (P: voicing 0.5, formants 0.4, modulation 0 in steady wind; gusts at 2–3 Hz would pass the crowd rule). Plain band noise gets formant 0.61 (S1) | – | howls are perceptually voice-like ("ghost wind"); report them under the tonal-artefact detector, not as speech |
| Synthesized crowd murmur | TP when sparse (P: `murmur_only` confirmed); **FN when dense** (> 6 talkers flatten 2–8 Hz modulation below 0.3) and when faint (`wind_with_faint_murmur` missed) | – | add a babble cue (below) |
| Single creature growl | **TP by construction**: `creatures.beast` is glottal pulses through a moving vocal tract | `VOICE_WORDS` has no creature words, so a forest bed with a growl event fails `voice_like` | treat creature words in the brief as allowed; add a roughness / sub-harmonic cue (growls have period doubling, speech rarely does) to label it "creature" |

Concrete NumPy/SciPy improvements, in order:

1. Fix S1 (bandwidth), S2 (harmonic rejection or cepstral envelope), S3 (3 s windows, tremolo rate test), S5
   (YIN-style normalisation, FFT), S6 (glide in st/s), S7/S8 (co-location, event criteria).
2. Add a **babble cue** for dense crowds: a modulation spectrum computed per critical band (8–12 gammatone or
   octave bands), where speech babble shows 2–8 Hz modulation *across many bands with low cross-band coherence*.
   Tremolo is coherent across bands; wind is below 2 Hz.
3. Add a **spectral-envelope dynamics cue**: delta-MFCC energy in 2–8 Hz (formant transitions without explicit LPC).
   It is robust to harmonic locking.
4. Replace the hand weights with a logistic combination fitted on the corpus: the cues as features, and
   leave-one-file-out cross-validation. Report the operating point as an approved-set false-alarm rate.
5. Trust the VAD more for music: in `music` kind, require VAD co-location for anything above `note`. Today's rule
   (`diagnose.py:229`) already half-does this.

## 4. Reference profiles and preference ranking (`analysis/reference.py`)

- **z with small n (`reference.py:75,92`)**: `np.std` (ddof = 0), floored at scale/2, z-threshold 1.5, 17 features.
  **[probe]** A candidate drawn from the *same* population as n = 5 references exceeds |z| ≥ 1.5 on a feature with
  p = 0.29, so it gets ~4.9 "notable departures" by chance (n = 10: 3.5; n = 30: 2.7). The floor reduces this
  somewhat. The `notable` words are mostly noise until n ≥ 20. Use ddof = 1, inflate by √(1 + 1/n) (the predictive
  sd), raise the threshold to 2.5, and print n beside every statement.
- **Correlated features double-count**: centroid, rolloff, tilt, harsh share, dialogue share and flatness all
  measure brightness. With equal weight in the RMS distance (line 94), one brightness difference counts about 5×.
  Group the features (brightness, density, dynamics, space, structure), take one z per group (first principal
  component, or the mean), or use a shrinkage-covariance Mahalanobis distance once n ≥ 2 × the features.
- **`lufs` and `lra_lu` are in the identity**: candidates are normalised before delivery, so loudness distance ranks
  mastering, not character. Drop `lufs`. Keep LRA.
- **Mood-blind averaging**: one profile per kind averages tense, calm and tender cues. Ranking by distance to that
  mean rewards the bland middle and punishes a candidate that is *correctly* busier for a tense brief. Condition on
  the mood, or rank by distance to the nearest approved reference with a similar direction.
- **`rank` ignores the preference history (`reference.py:198`)**: the sort key is `(usable, distance,
  −preference_score)`. Float distances never tie, so preference never decides anything when a profile exists. Use a
  single score, e.g. `−distance/σ + λ·pref`, with λ chosen when there is data to choose it.
- **Nearest-neighbour preference**: `min` over rows makes one outlier approval decisive, and feature scales are the
  hand "noticeable" units. With tens of rows, use k = 3 mean distance and report "insufficient data" under ~10
  approve/reject rows per kind.
- **Storage race**: `reference.save_style` and `production.add_reference/set_preferences` all do read-modify-
  `write_text` on the same `audio_style/<project>.json`, with no lock and no atomic replace. Concurrent approvals from
  the app and the CLI will drop history rows. Use temp-file + `os.replace` and a lock (the repo already does this in
  `story/project.py`).
- `quality.reference_distance` is a third, incompatible distance over the same idea. Pick one.

## 5. Evidence from the harness, and priorities

### 5.1 What `results.json` says (as of 02:32)

- Approved set: **13/34 files get a non-note finding**: `in_dialogue_band` ×7 and `loudness_off` ×7 on approved
  ambience, `sub_rumble` ×3, `harsh` ×2, and `voice_like` on the approved gala ambience. A detector that fails 38 %
  of what the owner approved is miscalibrated, or its targets are (the loudness target is plainly not what the
  approved beds were delivered at).
- `speech` is `suspected` on **19/30 music files** and `confirmed` on an approved door creak. This matches S1–S4.
- Labels are not independent of the detector: the expectations for `music_unrung_tense` (repetitive → none),
  `cave_rumble` and `metronomic_arp` (`mechanical_events` → `mechanical`) changed between my two reads while the code
  was also changing. With 7 rejected files this is fitting the labels to the detector.

### 5.2 Methodology fix (part of priority 4)

Freeze the labels, set blind by a listener, *before* running. Keep a held-out half. Report per-detector precision
and recall plus the approved-set false-alarm rate. Never edit `expect` in the same session as thresholds.

### 5.3 The five changes with the best quality per hour

1. **Make the voice detector trustworthy (1–1.5 days).** S1 (one line), S2 (harmonic rejection, ~40 lines), S3
   (3 s windows and the tremolo test), S5/S6, S7 co-location and S8 event criteria, the `crowd_murmur` underscore
   bug, creature words. Re-run the 19 "suspected" music files and the faint murmur. This is the owner's number-one
   complaint, and today the detector flags melodies and misses faint murmurs.
2. **Stop corrections that make things worse (0.5 day).** Balance sign, phase (polarity or delay instead of
   narrow), clipping (declip or skip), `too_dull` and `hissy` brief-gating, circular processing of loops for every
   filter or dynamics op, true-peak ceiling in normalise, `correct_file` re-diagnosing with the same brief, distinct
   level-matched donors. Each is under an hour and each is a correction that currently lies in its own `compare`.
3. **Loop seam measure and reloop (0.5 day).** A level-relative click test (remove the 0.05 floor), a spectral seam
   term, an onset-grid phase check, and a length-preserving reloop (bar-aware for music). Loops are the dominant
   asset type for VN ambience and music.
4. **Recalibrate on the approved set and relabel confidence (0.5–1 day).** Frozen labels (§5.2). Shares on
   K-weighted power from 20 Hz. The tilt anchor. `in_dialogue_band` only with VO. The loudness targets that match
   approved delivery. The `basis` field, with `measured` reserved for standard measures. Goal: < 10 % approved-set
   false alarms before any detector may emit `problem`.
5. **Fix harmony and ranking statistics (0.5 day).** Chroma from ≥ 250 Hz or an 8192-point FFT, recalibrate the
   chord-change threshold on rendered I–vi–IV–V progressions, and suppress `harmonic_stasis` until then. In
   `reference.py`: ddof = 1 and the predictive sd, threshold 2.5, grouped features, drop LUFS, a combined
   preference score in `rank`, atomic and locked writes.

Deliberately not on the list: a trained tagger, replacing SuperFlux, or the emotion class. Those are worth doing only
after (1)–(4) make the existing numbers honest.
