# 18. Audio analysis science notes

Implementation notes for the NumPy/SciPy tier of the audio-quality engine (44.1 kHz; MIDI→FluidSynth music
loops, layered procedural ambiences, SFX; music sits under dialogue in a visual novel).

Provenance: written from working knowledge of the standards and papers named below. Web sources were **not
re-fetched while writing**, so numeric constants from standards were checked against memory only. Anything I
could not stand behind is tagged `[UNKNOWN]`; anything that is a house heuristic rather than a published
value is tagged `[HEURISTIC]` and should be tuned on the benchmark set (`.dev/audio-bench`).

Conventions: `x` mono float in [-1, 1], `sr` = 44100, frames are Hann-windowed, `eps = 1e-12`, dB of power
= `10*log10`, dB of amplitude = `20*log10`.

---

## A. Technical measures

### A1. True peak (ITU-R BS.1770-4 Annex 2)
- Oversample 4x for fs <= 48 kHz (2x at 96k, none above), take max |y|, report dBTP = 20 log10(max).
- Practical: `scipy.signal.resample_poly(x, 4, 1, window=('kaiser', 8.6))` per channel. The standard's example
  filter is a 48-tap polyphase FIR (12 taps per phase); a long Kaiser filter is at least as accurate.
- Failure modes: intersample peaks exist only if the signal was clipped/limited at sample level, so a clean
  procedural render rarely differs from sample peak by > 0.5 dB. The metering error of 4x is up to ~0.5 dB
  under-reading on worst-case content (BS.1770 states this) [UNKNOWN: exact figure]. Check the *decoded*
  OGG/M4A too: lossy codecs add 1-3 dB of overshoot on peaks near 0 dBFS. Keep delivery ceiling at -1 dBTP
  (EBU R128 s1 / common streaming practice), -1.5 dBTP before lossy encoding [HEURISTIC].

### A2. Loudness: momentary, short-term, integrated, LRA (BS.1770-4, EBU R128, Tech 3341/3342)
K-weighting (apply to each channel, then power-sum with channel weights 1.0 for L/R/C; surround 1.41):
1. Stage 1 "pre-filter", high shelf: f0 = 1681.974450955533 Hz, G = +3.999843853973347 dB,
   Q = 0.7071752369554196. Biquad via RBJ-style shelving with the standard's analog prototype, bilinear transform
   at actual `sr` (do **not** reuse the 48 kHz coefficients at 44.1 kHz; recompute).
2. Stage 2 "RLB" high-pass: f0 = 38.13547087602444 Hz, Q = 0.5003270373238773.
(Constants as published in libebur128 / pyloudnorm; AssetForge's `audio/loudness.py` already verifies them.)

Measurement:
- Block loudness for window length T: `z = mean(y_k^2)` over the window; `L = -0.691 + 10 log10(sum_c G_c z_c)` LUFS.
- Momentary: T = 400 ms. Short-term: T = 3 s. Both are sliding (update >= every 100 ms for 400 ms windows).
- Integrated: 400 ms blocks, 75 % overlap (step 100 ms). Gate 1 absolute: drop blocks with L < -70 LUFS.
  Gate 2 relative: mean of surviving blocks computed **in the power domain**, minus 10 LU (R128/BS.1770-4 uses
  -10 LU); drop blocks below it; integrated = `-0.691 + 10 log10(mean power of remaining)`.
- LRA (Tech 3342): short-term (3 s) blocks at >= 1 Hz hop, absolute gate -70 LUFS, relative gate **-20 LU**
  below the power-mean of the absolute-gated blocks, then LRA = P95 - P10 of the remaining short-term values (in LU).
- Failure modes: clips < 400 ms have no valid block (pad with zeros to one block, flag `too_short`); looped music
  has no program-like arc so LRA mostly reads "how much the arrangement breathes", not mix quality; a 20 s loop
  gives only ~18 short-term values, so LRA is noisy (use hop 0.25 s, report as indicative); silence-gated LUFS
  rewards sparse content (quiet intro is excluded while a loud hit dominates), so **also report ungated RMS dBFS**.
- Targets for this project: music bed integrated -20 to -18 LUFS, short-term max <= -15 LUFS under dialogue,
  ambience -30 to -24 LUFS, SFX peak-normalised plus a LUFS band per class [HEURISTIC; existing `audio/master.py`
  already owns per-kind LUFS targets, align with it].

### A3. Crest factor and dynamics
- Crest = `20 log10(max|x| / rms(x))` over the whole file; also a **short-term crest** on 3 s windows (median).
- Typical: unprocessed synthesized pads 12-20 dB; acoustic/SoundFont orchestral 14-22 dB; loudness-war music < 8.
  Noise beds (Gaussian) sit at ~11-13 dB for whole-file peak/RMS, so "low crest" there is normal, not a defect.
- Dynamic range proxy: `P95 - P10` of 400 ms RMS in dB (like LRA but on ungated RMS; robust for short loops).
- Failure: a single click inflates peak and makes crest look good; compute crest on peak after click removal or
  use the 99.9th percentile of |x| (`crest_p999`).

### A4. Clipping
- Flag a run when `|x| >= 0.999` (tolerates PCM16 rounding: 32767/32768 = 0.99997) for **>= 3 consecutive samples**
  (>= 2 for sparse SFX); report `clip_runs`, `clip_samples_ratio`, `longest_run`.
- Also test "flat top at file max": count samples within 1 LSB of `max|x|` when `max|x| < 0.999` (pre-normalisation
  clipping later scaled down). Flag if runs >= 3 and count > 0.0005 * N.
- Failure: square waves legitimately sit at ±level for many samples (polyBLEP ones are band-limited, so near-ideal
  squares have plateaus but not at full scale); hard-limited music has many 1-2 sample touches (not clipping runs).
  Inspect the decoded lossy copy for intersample overs via A1 instead.

### A5. DC offset
- `dc = mean(x)`; flag `|dc| > 0.002` (-54 dBFS) [HEURISTIC]. Also check windowed DC (1 s windows) to catch drift:
  `max|mean_w| > 0.005`. Remove with a 1st-order HP at 10-20 Hz (`audio/export.finish` already does DC removal).
- Failure: very low rumble (< 20 Hz) looks like DC in short windows; evaluate on >= 2 s windows.

### A6. Discontinuity / click detection
Time-domain (cheap, sample-accurate):
1. `d = diff(x, n=2)` (second difference; kills slowly varying content, keeps steps and spikes).
2. Local scale: `s[n] = median(|d|)` over a centred 10 ms window (hop 1 sample via `scipy.ndimage.median_filter`
   or 1 ms hop then interpolate); floor `s >= 1e-5`.
3. Flag `|d[n]| > k * s[n]` with k = 10-12 and `|d[n]| > 0.01` (absolute floor so silence-noise is ignored).
   Merge hits closer than 2 ms into one event; report time, size, and `d/s`.
4. Step test for first-difference jumps: `|x[n]-x[n-1]| > max(0.2, 8 * rms_local)` for DC steps.
Spectral (catches clicks hidden under loud material and tiny ticks): STFT n_fft 512, hop 64; broadband flatness
spike: frame is a click if spectral flatness > 0.5 and HF (>8 kHz) energy rises > 15 dB over the median of
the previous 8 frames while LF doesn't. [HEURISTIC]
- Failure: genuine transients (snare, plucks, `clicks` in snow crust, embers) are *designed* clicks; gate detection
  by content class (skip for `crackle`/`clicks` recipes) or require the event to be **isolated from the onset
  grid**. Square/saw edges at low sr trigger k-tests; polyBLEP removes that. Loop seams are the highest-value place
  to run it (A8).

### A7. Silence distribution
- Frame RMS (20 ms, hop 10 ms) in dBFS; silent frame = RMS < -60 dBFS (SFX/music) or < -70 (ambient beds).
- Report: leading silence ms, trailing silence ms, fraction silent, count and length histogram of internal silences,
  longest internal gap. Loops: leading/trailing silence > 20 ms is a defect (breaks the loop); music gaps > 1.5 s
  inside a loop are suspicious unless the brief says "silence" cue.
- Failure: fade tails look like trailing silence; classify decay by monotonic slope.

### A8. Stereo
- Correlation: `r = sum(L*R) / sqrt(sum(L^2) * sum(R^2))` over the file and per 1 s window. Mean r < 0 is
  phase trouble (mono collapse loses energy). r > 0.98 = effectively mono (fine for dialogue-adjacent beds).
- Mid/side: `M=(L+R)/2`, `S=(L-R)/2`; `side_ratio_dB = 10 log10(sum S^2 / sum M^2)`. Typical music -6 to -15 dB.
  Bass (< 120 Hz) side energy should be < -20 dB below mid (mono-compatibility).
- Also **mono-fold loss**: `10 log10(sum M^2 / (0.5*(sum L^2 + sum R^2)))`; < -3 dB flags destructive summing.
- Failure: delay-based widening (Haas) or per-channel decorrelated noise give r ≈ 0 while still sounding fine;
  only use correlation as a flag when combined with the fold-loss figure.

### A9. Loop-boundary continuity (looped music and ambience)
Let `N` = length; compare tail `T = x[N-w:]` with head `H = x[:w]`, w = 50 ms and 500 ms.
1. **Sample/derivative continuity**: step = `|x[0] - x[N-1]|`; slope mismatch =
   `|(x[1]-x[0]) - (x[N-1]-x[N-2])|`; compare against the local `s` of A6. Pass when both < 4*s. Zero-crossing
   cuts with matched slope sign give the best continuity.
2. **Level continuity**: `|rms_dB(T) - rms_dB(H)|` and also against the whole-loop RMS; pass < 1.5 dB (music),
   < 1 dB (beds). Use 400 ms windows straddling the seam: `rms(x[N-200ms:] ++ x[:200ms])` vs ±1 s neighbours.
3. **Spectral continuity**: log-mel (64 bands) mean of last 500 ms vs first 500 ms; distance
   `mean|ΔdB|`, pass < 2.5 dB, plus centroid delta < 15 %. Also tonal check: chroma cosine distance between
   the seam halves < 0.25 (music) [HEURISTIC].
4. **Phase/envelope**: for pulsed/tremolo content, the modulation phase must continue: compare envelope
   autocorrelation at lag = period vs seam gap; simplest is to concatenate `x ++ x` and run the A6 detector plus a
   novelty (Foote) peak test at the seam (C6): a seam novelty > 1.5 * median novelty of the loop is flagged.
5. **Tail overhang**: reverb/release tails that ring past the end need folding (G3). Test: energy in the final
   200 ms relative to the first 200 ms; and `x ++ x` must not show a bump (>1.5 dB) at the seam.
- Failure: stochastic textures never match sample-wise; judge by level+spectrum, not waveform. Loops of exact bar
  lengths still fail if FluidSynth's release tails are trimmed (the render must wrap tails, `looping.fold_loop`).

---

## B. Spectral / timbral

### B1. STFT parameters at 44.1 kHz
| Purpose | n_fft | hop | Notes |
|---|---|---|---|
| Spectral shape, centroid, rolloff, bands | 4096 (93 ms) | 1024 | bin = 10.8 Hz; resolves bass |
| Onsets / flux / transients | 1024-2048 | 441 (10 ms) | |
| Chroma, harmony | 8192 (186 ms) or 4096 | 2048 | low-note resolution (semitone at 110 Hz = 6.5 Hz) |
| Clicks | 512 | 64 | |
Window Hann (sqrt-Hann for analysis/synthesis pairs). Use power spectrum `|X|^2 / sum(w^2)`; zero-pad to avoid
scalloping bias in the 55-110 Hz range; `scipy.signal.stft(..., boundary='zeros')` or manual framing.

### B2. Mel filterbank
- 64 bands for descriptors (128 for visualisation); f_min 30 Hz, f_max 16 kHz (or 20 kHz); Slaney mel scale with
  area-normalised triangles (librosa default), power spectrogram, `10*log10(max(S, 1e-10))`, optionally
  `top_db = 80`. mel(f) (HTK) = `2595 log10(1 + f/700)`.
- Failure: area-normalisation makes noise look flat; for tilt statements, use 1/3-octave bands (B4).

### B3. Descriptors (per frame `t`, magnitude `X_t[k]`, freq `f_k`; use power-weighted means)
- Centroid `C = Σ f_k X_k / Σ X_k`. Bandwidth `B = sqrt(Σ X_k (f_k - C)^2 / Σ X_k)`.
- Rolloff: smallest `f` with cumulative power >= 85 % (use 95 % for "air").
- Flatness `exp(mean(ln P_k)) / mean(P_k)` over 100 Hz-16 kHz, P = power (add eps); 1 = noise, → 0 tonal.
- Flux `Σ_k max(0, X_t[k] - X_{t-1}[k])` on log-compressed magnitude (`log(1+γX)`, γ = 100) normalised by bins.
- Also: spectral slope (linear regression of dB-per-octave against log2 f, 80 Hz-12 kHz), crest of spectrum, zero-crossing rate.
- Failure: centroid of a loud-bass signal is low even if bright; always give frame-median **and** energy-weighted
  value; silent frames give garbage (drop frames below -60 dBFS).

### B4. HPSS (Fitzgerald 2010, "Harmonic/Percussive Separation using Median Filtering", DAFx)
- Spectrogram `S` (magnitude). Median-filter along time → `H~` (sustained tones are horizontal ridges);
  along frequency → `P~` (transients are vertical ridges). Kernel ~ 17 frames (≈ 0.2-0.4 s at hop 441-1024) and
  ~17 bins (≈ 100-200 Hz at 4096). Soft mask `M_H = H~^p / (H~^p + P~^p)`, p = 2 (Wiener-like); margin > 1 gives
  a residual. Apply to complex STFT and invert (or just compute **energy ratio** H : P without inverting).
- Use: `percussive_ratio` for drum density in music; in ambiences, high P implies events (dogs, thunder, clicks),
  high H with high flatness implies beds. Report `harmonic_ratio` = Σ(M_H S^2) / Σ S^2.
- Failure: reverb tails and noise beds go "harmonic" (smooth in time); bass notes with sharp attacks leak into P;
  vibrato smears horizontal ridges. Not a tonal-vs-noise detector (use flatness).

### B5. Octave / third-octave energy and balance
- 1/3-octave bands (ISO 266, centres 25 Hz-16 kHz, base-2 `f_c = 1000 * 2^(n/3)`), band power from the STFT by
  summing bins; report level relative to total in dB.
- **Slope definition matters.** Pink noise = -3 dB/octave in *per-Hz PSD*, i.e. flat *energy per octave band*. Typical
  mixed music has octave-band energy falling roughly -3 to -6 dB/octave above ~250 Hz (per-Hz PSD ≈ -6 to -9 dB/oct)
  [UNKNOWN: exact population value; take it from your own reference set]. Say which one you fit; the engine should
  fit slope on **1/3-octave band levels** between 125 Hz and 12.5 kHz and compare against the reference profile
  rather than a universal constant. Ambient pad music is steeper (-6 to -9), bright SoundFont pop-style patches flatter.
- Deviations (compare band level to a straight-line fit and to the reference z-score, B-score > 1.5 σ):
  - Rumble: > -20 dB relative to 250-500 Hz band energy below 80 Hz, or any content < 40 Hz > -35 dBFS.
  - Mud: 200-500 Hz bump of > 4-5 dB over the fitted line (buildup of layered pads and piano).
  - Harshness / presence peak: 2-5 kHz > 3-4 dB over the line; also narrow (> 6 dB, Q > 5) peaks are resonances.
  - Hiss / fizz: > 8 kHz level within 25 dB of the 250-500 Hz band for music, or rising slope above 8 kHz
    (noise beds are exempt; they are noise).
  - Dull: rolloff95 < 4 kHz for music meant to be bright.
  [HEURISTIC thresholds]
- Failure: the spectrum is content-dependent (solo flute has no bass); use thresholds as flags relative to the
  piece's reference group (genre/instrumentation), not as hard fails.

### B6. Transient density
- Events/s from the SuperFlux onset detector (C2) after the "median floor"; split by band (< 250 Hz, 250-4 kHz,
  > 4 kHz). `transient_sharpness`: median over onsets of attack time (10 %→90 % of peak envelope, ms).
- Failure: tremolo, rain, and crackle produce hundreds of "onsets"; classify as texture when onset rate > 15/s and
  intervals have CV > 0.8 (irregular, no beat).

---

## C. Music

### C1. Tempo and beat confidence
- Onset envelope `o[t]` = mean positive flux over a 24-48 band log-mel, hop 10-11 ms (≈ 90-100 Hz).
- Tempogram (Grosche & Müller 2011): local autocorrelation or Fourier of `o` over 8 s windows, step 0.5 s, mapped to
  BPM 40-240. Global tempo from the mean tempogram.
- Ellis 2007 ("Beat Tracking by Dynamic Programming", JNMR): ACF of `o` with log-Gaussian tempo prior
  (centre 120 BPM, σ = 1.4 octaves); DP maximises `Σ o[t_i] - α Σ (log((t_i - t_{i-1})/τ))^2`, α ≈ 100
  (tightness), τ = 60/BPM s.
- Confidence: (a) `acf_peak_prominence` = ACF(lag*)/ACF(0) minus median ACF around; (b) fraction of onset flux
  falling within ±40 ms of the beat grid; (c) tempogram stability (std of local BPM / mean). Combine: confident if
  (a) > 0.2 and (b) > 0.5 and (c) < 3 %.
- **For MIDI-rendered loops the true BPM is known** (tempo events): verify the render against it; a >2 %
  mismatch or beat-fraction ≤ 0.35 flags a rendering/pattern defect, not a "wrong estimate". Handle octave errors
  (x2, /2, 3/2) by accepting ratios {1/2, 2/3, 1, 3/2, 2} as matches.
- **BPM is meaningless when**: no pulsed percussion or bass ostinato; free-time pads/ambient; heavy rubato; onset
  rate < 0.5/s; flat ACF (prominence < 0.1). Report `tempo: null` and `pulse_strength` instead.
- Failure: syncopation and swing double/halve; sustained strings with slow attacks produce soft onset peaks.

### C2. Onset detection
- Spectral flux: `SF[t] = Σ_k H(|X_t[k]| - |X_{t-1}[k]|)`, `H(x) = (x + |x|)/2`, with log-magnitude
  `log(1 + γ|X|)`.
- **SuperFlux** (Böck & Widmer, DAFx 2013): log-filtered spectrogram with 24 bands/octave (≈ 138 bands, 27.5 Hz-16 kHz),
  hop 441 (10 ms) at n_fft 2048; take reference frame lag `μ` (1-3 frames, from the frame-size rule
  μ = max(1, round(0.5 (w - hopsize·overlap)/hop))) and a **max-filter over 3 frequency bins** on that reference:
  `SF[t] = Σ_k H(X_t[k] - max(X_{t-μ}[k-1:k+1]))`. Vibrato and tremolo then no longer fire onsets.
- Peak-pick: `SF[t]` is a local max over ±(pre_max, post_max) = 30 ms / 30 ms, exceeds the local mean over
  (pre_avg, post_avg) = 100 ms / 70 ms by δ (≈ 0.05-0.2 of the normalised flux), and ≥ 30 ms since the last
  accepted onset (combine). [Constants in the paper are for an ML-tuned set; verify on our renders.]
- Adaptive threshold alternative: `thr[t] = median(SF[t-W:t+W]) + k * MAD`, W ≈ 0.5 s, k ≈ 3-4.
- Failure: soft strings (no transient) = missed onsets (use phase- or pitch-change onsets: chroma jumps);
  reverb makes beats smear; a noise bed in a quiet segment yields noise-floor onsets (apply an energy floor).

### C3. Chroma, key, tonal stability
- Chroma (STFT-based): n_fft 8192 (or 4096), hop 2048, use bins 55-2000 Hz, weight by `log(1 + 100·mag)`, fold bin
  frequency to `pc = round(12 log2(f/440)) + 69 mod 12` (with a tuning offset estimated as the circular mean of
  deviations from 12-TET, in ±50 cents), optionally smooth over beats; L2-normalise per frame. CQT chroma is
  better in the bass (need `librosa`/`nnAudio`; with base NumPy a log-spaced constant-Q via FFT is doable [HEURISTIC:
  STFT is acceptable at 8192]).
- Key (Krumhansl-Schmuckler, Krumhansl-Kessler 1982 profiles):
  - major `[6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]`
  - minor `[6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]`
  - Pearson r between mean chroma (rotated) and each of the 24 profiles; key = argmax; confidence = `r1 - r2`
    (> 0.1 decent) plus absolute `r1 > 0.6`. Temperley or Albrecht-Shanahan profiles are marginally better on pop
    [UNKNOWN: gain size].
- For MIDI-rendered music: the key is known; use measured key as an **audit**: mismatch with relative major/minor
  is normal; mismatch elsewhere hints at bad voicing/modulation.
- Tonal stability = 1 - mean chroma change rate: `mean(1 - cos(c_t, c_{t-1}))` at hop ≈ 0.25-0.5 s; plus
  `entropy(mean chroma)` (flat chroma = atonal/noise).
- Failure: bass-heavy mixes bias key to the bass pitch; drones fake tonic; modal music (dorian etc.) gets mapped to
  neighbouring major/minor; percussion-only chroma is noise.

### C4. Harmonic change rate (chord-change proxy)
- Beat-synchronous (or 0.5 s) chroma; **tonal centroid** (Harte et al. 2006): 6-D projection of chroma onto circles of
  fifths/minor thirds/major thirds; chord-change strength = ‖TC_t - TC_{t-1}‖. Peak count per bar → change rate.
  Alternative: template matching against 24 triads + HMM (Viterbi, self-transition 0.9-0.95) and count label changes.
- Typical bands for restrained VN underscore: 0.25-1 changes per bar [HEURISTIC]; > 2 per bar sounds restless.
- Failure: arpeggios appear as chord changes in short frames (use ≥ 1 beat); suspended/added-tone chords confuse
  template matching.

### C5. Self-similarity and novelty segmentation (Foote 2000, "Automatic Audio Segmentation Using a Measure of Audio Novelty")
- Features: beat-synchronous (or 0.5 s) [chroma, MFCC-13 delta-free, optionally RMS], z-scored per dimension,
  concatenated chroma weight 1.0 / timbre 0.5 [HEURISTIC]. SSM `S[i,j] = cos(f_i, f_j)`.
- Novelty: correlate main diagonal with a Gaussian-tapered checkerboard kernel of half-width L
  (L ≈ 8 s of beats → 8-16 beats), `N[i] = Σ_{u,v} K[u,v] S[i+u, i+v]`. Pick peaks with prominence > 0.1·max.
  Large L finds sections (verse/chorus), small L finds phrase changes.
- A **seam** test is the same kernel at `i = N` of the tiled `x ++ x`.
- Failure: SSM boundaries fail for slowly evolving ambient pieces (novelty ~ flat, which is fine if intended).

### C6. Repetition measures
- Diagonal structure: lag matrix (`S` shifted by lag), mean along each lag; repetition index =
  max over lags in 2-32 beats of mean lag-similarity minus global mean; lag histogram = histogram of diagonal-path
  lengths above threshold 0.9 (use time-lag filtering with median smoothing along diagonals: Serrà 2012-style
  path enhancement).
- Report: `self_repeat_period` (bars), `period_score`, and the **fraction of the SSM above 0.9** (high = very
  repetitive, which fatigues over a long scene; compare against a target [0.3, 0.7] for a loop [HEURISTIC]).
- Failure: sustained pads give high similarity trivially; mask frames with low novelty change or subtract the
  mean feature before cosine to avoid "everything is similar".

### C7. Arrangement density and energy arc
- Density proxies: spectral occupancy `P(frame band energy above -40 dB of max)` across 1/3-octave bands;
  onset rate per band; number of simultaneous pitch classes (chroma entropy exp(H)); with MIDI available, count
  active tracks/notes per bar (preferred, exact: don't infer from audio what the score knows).
- Energy arc: 1 s RMS (dB) → smooth (2-4 s Gaussian) → fit linear + quadratic; `arc_range` = P95 - P5; `arc_shape`
  = rise/rise-fall/fall by sign of slope, plus the time of the max (position/length). For loops require `start ≈ end`
  (< 1.5 dB) and flag `arc_range < 2 dB` as flat (monotonous) for music cues meant to develop.

---

## D. Psychoacoustics

- **Equal loudness (ISO 226:2003)**: sensitivity peaks ~2-5 kHz (about 12-15 dB more sensitive than at 1 kHz near
  3-4 kHz at moderate levels) and falls steeply below 200 Hz; contours flatten at high levels. LUFS (K-weighting)
  only approximates this with a 4 dB shelf and an HPF, calibrated on speech/broadband programme; a bright 3 kHz
  tone reads quieter in LUFS than it feels, and sub-bass reads quieter than it is. So LUFS ≠ perceived intensity:
  supplement with an A- or loudness-model figure (ISO 532-1 Zwicker loudness in sone, via `mosqito` [UNKNOWN: license]
  or an A-weighted RMS) and an EBU-style short-term max.
- **Masking**: simultaneous masking spreads in critical (Bark/ERB) bands; masker excitation spreads upward in
  frequency more than downward (upward spread of masking, steeper at high levels: slope ≈ -25 dB/Bark at low
  levels, ~-10 to -15 dB/Bark at 80 dB SPL [UNKNOWN: precise numbers]). Temporal: forward masking up to ~100-200 ms,
  backward ~20 ms.
- **Music under speech**: intelligibility lives in ~0.5-4 kHz (SII band-importance peaks ~1.5-3 kHz; ANSI S3.5);
  consonant cues at 2-4 kHz. So the music bed should have a "pocket" there: practical test = compute the music's
  1/3-octave levels 500 Hz-4 kHz and compare to a **speech long-term spectrum** (LTASS, ~ -3 dB/oct above 500 Hz,
  speech at -23 LUFS typical). Rule of thumb: music energy in 1-4 kHz bands should be ≥ 10-15 dB below dialogue
  in the same band, i.e. overall music about 12-18 LU below dialogue [HEURISTIC; the standard-based
  figure for TV is that "background" should be ≥ 15 dB below, ITU/ATSC-ish; exact value UNKNOWN]. Practical
  meaning of "spectral masking of dialogue": the dB by which music exceeds `speech_band_level - SNR_needed` in each
  band (≈ +6 dB SNR in the 1-4 kHz bands for easy listening, more for non-native readers).
  Implement a **dialogue-masking index** = Σ_bands w_b · max(0, L_music,b - (L_speech,b - 12 dB)) with SII-like
  weights `w_b`, and a **pocket depth** = mean music level 1-4 kHz minus mean level 200-500 Hz + 62.5 Hz-125 Hz
  neighbours (a dip is good). Sustained melody lead (flute/piano lead) at 1-3 kHz is the main offender.
- **Sharpness** (Zwicker; DIN 45692): `S = 0.11 · ∫ N'(z) g(z) z dz / ∫ N'(z) dz` acum, with
  `g(z) = 1` for z < 15.8 Bark and `0.15·exp(0.42(z - 15.8)) + 0.85` above (Aures weighting in DIN 45692; von
  Bismarck is the other variant). Rough proxy when no specific-loudness model is available: loudness-weighted
  centroid on the Bark scale of band energies above a 3 kHz emphasis. Typical natural sounds 1-2.5 acum; >3 is
  "shrill" [UNKNOWN: threshold].
- **Roughness** (Daniel & Weber 1997; Zwicker & Fastl): modulation 15-300 Hz in critical bands, peak ~70 Hz.
  Proxy: Hilbert envelope per 1/3-octave band (500 Hz-8 kHz), energy of modulation spectrum in 20-150 Hz relative to
  the band's mean level (modulation depth m); roughness ∝ Σ_b m_b². Beat roughness from close partials (< 1 critical
  band apart) is a real SoundFont flaw (detuned pad unisons, chorus).
- **Fatigue drivers** (all [HEURISTIC] population tendencies): sustained 2-5 kHz energy (equal-loudness peak),
  low crest/short-term dynamic range (no relief), constant modulation/high onset rate for minutes, high
  sharpness, no silence or rests, repeated identical short loops (< 30 s) heard for long scenes. Measure:
  fraction of time 2-5 kHz band is within 6 dB of its P95; `crest_short`; `rest_fraction`; loop length.
- **Perceived distance**: cues are level (≈ -6 dB per doubling in free field), high-frequency loss (air absorption;
  at 10 m 1-2 dB at 4 kHz, much more at 10 kHz; hence a low-pass with slope), **direct-to-reverberant ratio**
  (D/R falls with distance), reduced modulation depth and onset sharpness. Zahorik (2002) shows D/R and level
  dominate. Proxies: centroid and 4-10 kHz share (lower = farther), early-decay slope / tail energy share from the
  autocorrelation envelope of decays, `onset_sharpness`. Failure: a dull-recorded close source reads "far".
- **Emotion correlates** (Juslin & Laukka 2003; Gabrielsson & Lindström 2010; Hevner 1936): faster tempo, higher
  loudness, brighter timbre, staccato, major mode ↔ higher arousal/positivity; slower, softer, legato, minor, low
  register ↔ sad/calm. **They are population tendencies; context, culture, and training modulate them strongly**;
  use them to check the brief direction (is "tense" actually fast/dissonant/high-roughness?) never to claim a
  listener will feel X.

---

## E. Speech-likeness without ML

Goal: detect unwanted speech-like content (choir/vocal pad aahs read as voices; a vocal in a bed would clash with
dialogue). Always an evidence score with a reason list, never a binary verdict.

1. **Modulation spectrum** (Houtgast & Steeneken 1985; Elliott & Theunissen 2009): 4-6 bands in 100-4000 Hz, rectify
   or Hilbert envelope, low-pass 30 Hz, decimate to 100 Hz, FFT of 4-8 s segments; speech envelope peaks near
   **4 Hz** (syllable rate 3-6 Hz, extends 2-8 Hz) with energy falling above 10-16 Hz. Feature
   `mod_ratio = E(2-8 Hz) / E(0.5-32 Hz)`, `peak_hz`. Speech-like: peak in 3-6 Hz, ratio > 0.45, broad (CV of
   peak) across ≥ 3 bands coherently (cross-band envelope correlation > 0.4 = single voice).
2. **Formants** (LPC): resample to 16 kHz, pre-emphasise (0.97), 25 ms Hamming frames, hop 10 ms, order
   `2 + fs/1000 = 18` (autocorrelation/Levinson via `scipy.linalg.solve_toeplitz`), roots of A(z) → freq/bandwidth;
   keep complex roots with bandwidth < 400 Hz. Frames with F1 ∈ 300-900 Hz **and** F2 ∈ 800-2500 Hz (F2 > F1 + 300)
   are vowel-like; compute `vowel_frame_fraction`, and F1/F2 movement over 100-300 ms (diphthong-like change).
3. **Harmonicity / HNR & pitch**: YIN/normalised ACF on 40 ms frames; f0 in **80-400 Hz** (accept 60-500 for children/
   singing); voiced if `1 - d' < 0.15`; HNR = `10 log10(r / (1 - r))` with r the ACF peak (voiced speech 10-25 dB). Pitch
   contour: speech glides continuously, 1-3 semitones per syllable, declination over phrase; fraction of voiced
   frames 40-70 % (speech ≈ 60 %); correlation of contour with a stepwise melody (quantised to semitones →
   music).
4. **Syllabic flux pattern**: flux/envelope peaks at 3-8/s with irregular intervals (CV 0.3-0.6), alternating
   voiced runs 80-250 ms and short gaps/unvoiced (high-frequency noise bursts): `pause_fraction` 15-30 %.

**Evidence score** (soft AND, so one cue never decides):
```
s = 0.30*S(mod_ratio, 0.35→0.6) + 0.25*S(vowel_frame_fraction, 0.2→0.5)
  + 0.20*S(hnr_voiced_fraction·in_pitch_range, 0.3→0.6) + 0.15*S(contour_glide_score, 0.3→0.7)
  + 0.10*S(pause_alt_score, 0.3→0.7)           # S = clipped linear ramp [lo→hi] to [0,1]
speech_likely if s >= 0.6 and at least 3 of 4 cue groups > 0.4
```
[HEURISTIC weights; tune on dialogue vs bed recordings, none shipped here.]

False positives and discriminators:
- **Tremolo strings / chopped pads**: modulation peak at 4-6 Hz is *periodic* (high ACF of envelope at lag = period,
  narrow peak) and the pitch is a steady note not gliding; formants static. Speech modulation is broad and irregular.
- **Wind gusts**: energy < 1 Hz modulation; no pitch (HNR < 3 dB), flatness high, formant roots unstable.
- **Crowd murmur beds**: many overlapping voices fuse; modulation ratio drops (< 0.3) and cross-band coherence
  falls; pitch tracks fragmentary. Report `babble_like` if f0 candidates multi-peaked and modulation weak: this is
  desired for background, so tag it, don't fail it.
- **Bird calls**: f0 1-8 kHz (outside 80-400), FM sweeps > 10 Hz modulation, no formant pair.
- **Vocal/choir samples** (real positive): SoundFont "choir aahs/voice oohs" → high HNR + F1/F2 vowel structure but
  *stable* modulation spectrum; rate as `voice_like` with a different message than `speech_like`; both mask dialogue.

---

## F. Reference comparison and distribution metrics

- **Per-feature z-score** against a reference profile (mean μ_f, std σ_f over a labelled set of good clips of
  the same class): `z = (x_f - μ_f)/max(σ_f, σ_min)`. Use robust stats (median/MAD·1.4826) and per-class profiles
  (`measure` already carries reference targets for sound classes). Aggregate: report the top 3 |z| features as reasons;
  overall distance = Mahalanobis on a small decorrelated feature set (≤ 15 dims) with shrinkage covariance
  (`Σ + λI`, λ = 0.1·trace/d). With ≤ 10 reference clips, covariance is unusable: use diagonal z only.
- **Log-mel distance**: mean over time of log-mel frames (64 bands) and std → compare `μ` and `σ` vectors:
  `d = mean|Δμ| (dB) + 0.5 mean|Δσ| (dB)`; frame-aligned distance (L1 in dB) only when comparing renders of the
  same score (A/B); for unrelated clips use long-term statistics (not alignable), optionally on 3-5 s excerpts to
  avoid length bias.
- **Fréchet Audio Distance** (Kilgour et al., Interspeech 2019): fits a multivariate Gaussian to embeddings of
  two *sets* (VGGish 128-d at 16 kHz originally; later work uses PANNs, CLAP, MERT) and computes
  `‖μ1 - μ2‖² + Tr(Σ1 + Σ2 - 2 (Σ1 Σ2)^{1/2})`. It is a **distribution distance**, needs hundreds to thousands
  of clips to make the covariance estimate stable (embedding dim 128-2048), is biased by sample size, and gives
  no per-clip score; embedding choice changes the ranking (Gui et al. 2024, "Adapting Frechet Audio Distance for
  Generative Music Evaluation", ICASSP) [UNKNOWN: which embedding best matches human ratings for game underscore].
  Needs an ML model: **outside the base tier**. Use only as an offline benchmark metric between "baseline" and
  "revamp" arms, not in the diagnosis path.
- **"Similar character but not a copy", measurable version**: two-level test.
  1. *Character similarity* (want **small** distance): long-term log-mel μ/σ distance (above), tonal profile
     (mean chroma correlation, key/mode equal), tempo within ±10 %, density/onset-rate ratio within ×1.5, reading-band
     share and sharpness within reference z < 1.
  2. *Not a copy* (want **large** distance): beat-synchronous chroma cross-similarity (max normalised
     cross-correlation of chroma sequences over transpositions, via the cross-SSM `S_AB`): copy if long diagonals
     (≥ 8 beats) with similarity > 0.9 exist; melodic n-gram overlap when MIDI is available (pitch-interval 4-grams
     shared > 25 %); identical note/onset pattern hash; audio fingerprint (landmark/peak-pair hash) matches > 5 %.
  Thresholds are [HEURISTIC]; calibrate with pairs rendered from the same brief with different seeds (should land
  "similar not copy") and a clip against itself with ±3 % tempo/transposition (should be flagged "copy").

---

## G. Corrections that are real DSP

All corrections must be **deterministic, parameter-logged and reversible by reproduction** (record in
`metadata.production`), and each is followed by re-measurement; a correction that fails to move the measure is dropped.

1. **Harshness: dynamic EQ / de-essing.** Split a sidechain band (bandpass, 2-5 kHz for harshness, 5-9 kHz for
   sibilance-like ticks); detect with RMS (10 ms attack / 100 ms release); when band level exceeds a threshold set
   to `reference_line + 2 dB` (B5), apply a peaking cut (Q 1.0-1.5, centre at the measured peak) of
   `min(gain_max = -4 dB, ratio-based reduction)`, smoothed with 5-20 ms attack and 150-300 ms release. Static fix
   alternative: gentle high-shelf `-2 to -3 dB @ 6 kHz` (S = 0.7) or 2nd-order low-pass at 12-14 kHz for hiss.
   Never process the dialogue pocket in a way that adds level; for under-dialogue cues consider a broad
   -2 to -4 dB dip (Q 0.7) at 1.5-3.5 kHz (the "pocket"). Artifacts: pumping and tonal-change if release too short;
   dynamic EQ applied to a tonal-noise mixture dulls transients.
2. **Rumble / mud**: Butterworth HPF 2nd-4th order at 30-40 Hz (music), 60-120 Hz (ambience beds that are not
   thunder/rumble by design); mud: peaking -2 to -4 dB at 300-400 Hz, Q 0.8-1.2 only when the 200-500 Hz band is > 4 dB
   above the fitted line.
3. **Dynamics.** *Too dynamic/too peaky*: gentle compressor ratio 1.5-2:1, threshold ~3-6 dB above RMS, attack 20-40 ms,
   release 150-300 ms (program-dependent), make-up to restore LUFS, ≤ 3 dB gain reduction typical; soft knee 6 dB.
   *Too flat / fatiguing*: expansion rather than compression: downward expander ratio 1.3-1.5:1 with threshold
   ~10 dB below RMS opens up gaps; or an **envelope modulation** over bars (±1.5 dB slow swell, 8-16 beats,
   inserted at bar boundaries) as arrangement-level fix (better done by regenerating with `arc`). Upward
   compression/expansion of already-limited material cannot restore crest; regenerate instead.
   Artifacts: breathing, noise-floor lift (ambiences), distortion with attack < 5 ms.
4. **Re-looping with equal-power crossfade.** Choose the loop length as an integer number of bars from the MIDI
   tempo (`samples = round(bars·beats·60/BPM·sr)`); cut at a zero-crossing with matching slope sign within ±2 ms of the
   bar boundary. Crossfade of length `Lx` = 50-150 ms for music (500 ms-2 s for beds): `out = a(t)·tail + b(t)·head`
   with `a = cos(π t / 2Lx)`, `b = sin(π t / 2Lx)` (equal-power, for uncorrelated material) or linear (equal-gain,
   when the two segments are highly correlated, e.g. same sustained note: equal-power would bump +3 dB). For
   decaying music (reverb/release tails) prefer **tail folding**: render a few seconds past the loop end, add the
   overhang onto the head (overlap-add) and truncate, so the seam is a mix, not a fade. Verify with A9 (`x ++ x`).
   Artifacts: audible dip/rise for fades on rhythmic hits (a note on the seam attacks twice or vanishes): keep note
   onsets outside the crossfade zone.
5. **Segment replacement for ambiences.** Locate offending windows (click, event that repeats too often, speech-like
   segment, level hole) via the detectors; pick a donor from elsewhere in the same render/stem matched by RMS
   (±1 dB) and spectral distance (log-mel mean < 2 dB), splice with 100-500 ms equal-power crossfades, then re-run seam and
   repetition tests. For event layers, re-render the event with a new seed rather than splice. Artifact: repeated
   donor makes audible loops (> 30 s donor pool or ≥ 3 donors).
6. **Spectral gating / noise reduction** (only for unwanted hiss in *tonal* content; never on noise-based
   ambiences, which would remove the content itself). STFT n_fft 2048, hop 512 (75 % overlap), noise PSD from the
   quietest 10-20 % frames (percentile) or a known-silent portion; spectral subtraction `|Y|² = max(|X|² - α·N, β|X|²)`
   with α 1.0-2.0 over-subtraction and floor β = 0.05-0.1 (-20 to -26 dB), or Wiener gain `G = ξ/(1+ξ)` with
   decision-directed a-priori SNR `ξ = a·G_prev²·γ_prev + (1-a)·max(γ-1, 0)`, a = 0.98 (Ephraim & Malah 1984/85),
   `G ≥ 0.1-0.2` floor; smooth gain over time (2-3 frames) and frequency (3 bins). Artifacts: **musical noise**
   (isolated spectral peaks warble), pre-echo smear on transients, dull top end. Prefer lowering the cause (synth noise
   param, bus `high_cut`) over denoising.

---

## Implementation checklist and engine notes
- Compute once per file: mono + L/R, 16-bit-decoded copy of delivered OGG/M4A for A1/A4, STFT 4096/1024 and
  1024/441 caches, 1/3-octave band matrix, onset envelope, chroma, beat-sync features; share between measures.
- Each metric returns `value`, `unit`, `flag` (ok/warn/fail) and `reason` text with a measured number and a
  suggested remedy, so `quality.REFINEMENTS` can map problems to regeneration parameters.
- Treat MIDI-derived ground truth (tempo, key, tracks, bars) as authoritative for music; audio estimates audit it.
- Calibrate every `[HEURISTIC]` threshold on `.dev/audio-bench` renders and record the chosen values with their
  basis. Do not present a heuristic as a published standard in user-facing text.

## Key references
- ITU-R BS.1770-4 (2015); EBU R128 (2020); EBU Tech 3341 (loudness metering), Tech 3342 (LRA).
- Fitzgerald, "Harmonic/Percussive Separation using Median Filtering", DAFx-10 (2010).
- Böck & Widmer, "Maximum Filter Vibrato Suppression for Onset Detection", DAFx-13 (SuperFlux).
- Ellis, "Beat Tracking by Dynamic Programming", J. New Music Research 36(1), 2007.
- Grosche & Müller, "Extracting Predominant Local Pulse Information from Music Recordings", IEEE TASLP 2011.
- Foote, "Automatic Audio Segmentation Using a Measure of Audio Novelty", ICME 2000.
- Krumhansl, *Cognitive Foundations of Musical Pitch* (1990); Harte, Sandler & Gasser, "Detecting Harmonic Change in Musical Audio" (2006).
- Serrà et al., "Unsupervised Detection of Music Boundaries by Time Series Structure Features", AAAI 2012.
- Houtgast & Steeneken, "A review of the MTF concept..." JASA 77 (1985); Elliott & Theunissen, PLoS Comp. Biol. 2009.
- ISO 226:2003; ISO 532-1; DIN 45692 (sharpness); Daniel & Weber, "Psychoacoustical roughness", Acustica 1997; ANSI S3.5 (SII).
- Zahorik, "Assessing auditory distance perception using virtual acoustics", JASA 111 (2002).
- Juslin & Laukka, "Communication of emotions in vocal expression and music performance", Psych. Bull. 129 (2003).
- Kilgour et al., "Fréchet Audio Distance", Interspeech 2019; Gui et al., "Adapting Frechet Audio Distance for Generative Music Evaluation", ICASSP 2024.
- Ephraim & Malah, "Speech enhancement using a MMSE short-time spectral amplitude estimator", IEEE TASSP 1984.
