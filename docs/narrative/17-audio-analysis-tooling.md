# 17. Audio analysis tooling: what fits a 4 GB, Python 3.14, CPU-only machine

Date: 2026-11-11. Target: macOS arm64 (M3, 16 GB RAM, ~4 GB free disk), CPython 3.14, uv venv.
Purpose: analyse audio AssetForge generates (FluidSynth music loops, procedural ambiences, synth SFX) for a
commercial mobile visual-novel pipeline. Nothing here was installed; the wheel facts come from the PyPI JSON
API (queried 2026-11-11), the model facts from the Hugging Face API and the GitHub/Zenodo pages named inline.

Legend: [UNKNOWN] = could not verify. [ASSUMPTION] = reasoned, not checked. "cp314 arm64 wheel" means a
macOS arm64 wheel tagged for CPython 3.14 exists on PyPI today.

## 1. Verified wheel / dependency facts (PyPI, 2026-11-11)

| Package | Version | cp314 macOS arm64 wheel | Notes |
|---|---|---|---|
| numba | 0.68.1 | yes (2.8 MB, plus a free-threaded cp314t) | pulls llvmlite |
| llvmlite | 0.50.0 | yes (40.5 MB) | BSD-2 + Apache-2.0 with LLVM exception |
| librosa | 1.0.0 | pure py3-none-any (0.3 MB) | requires numba>=0.61, scikit-learn>=1.6, soxr, pooch, joblib, msgpack, lazy_loader, decorator, soundfile, numpy>=2.1, scipy>=1.15; Python >=3.12 |
| scikit-learn | 1.9.1 | yes (8.3 MB) | librosa dependency |
| soxr | 1.1.0 | yes (0.2 MB) | LGPL-2.1-or-later (dynamic wheel; fine as a tool dependency, mind it if you ever redistribute) |
| essentia | 2.1b6.dev1438 | yes, `macosx_15_0_arm64` (20.5 MB) | deps numpy, pyyaml, six. Needs macOS 15+ (tag), AGPL-3.0 |
| essentia-tensorflow | same | yes, `macosx_15_0_arm64` (98.8 MB) | bundles TF inference; AGPL-3.0 |
| torch | 2.15.0 | yes, `macosx_14_0_arm64` (138.6 MB wheel) | Apache-2.0 + LLVM exception. Unpacked size [ASSUMPTION] several hundred MB; not measured |
| torchaudio | 2.11.1 | wheel is `py3-none-macosx_14_0_arm64` (ABI-agnostic) | requires torch>=2.11. Deprecated-feature churn [UNKNOWN]; not needed if you use soundfile/scipy |
| onnxruntime | 1.31.1 | yes (20.9 MB) | MIT; deps flatbuffers, numpy, packaging, protobuf; Python >=3.11 |
| ai-edge-litert | 2.3.0 | yes (12.8 MB) | deps incl. numpy, flatbuffers, protobuf, ml_dtypes, tqdm, backports.strenum. Licence field empty on PyPI [UNKNOWN] |
| tflite-runtime | 2.14.0 | no macOS wheels listed at all | dead end; use ai-edge-litert or ONNX |
| pyloudnorm | 0.2.0 | pure py (MIT) | deps only numpy, scipy |
| silero-vad | 6.2.3 | pure py (11.3 MB, ships the model files) | **hard dependency torch>=1.12**; onnx path via extra `silero-vad[onnx-cpu]` (onnxruntime>=1.16.1) |
| webrtcvad | 2.0.10 | sdist only, no wheels | needs a C compiler on 3.14 |
| webrtcvad-wheels | 2.0.14.post1 | yes, `macosx_11_0_arm64` (tiny) | MIT; maintained fork with wheels; use this one |
| pyannote.audio | 4.0.7 | pure py | requires torch>=2.8, torchaudio, torchcodec, lightning, torchmetrics, opentelemetry... |
| faster-whisper | 1.2.1 | pure py | ctranslate2 4.9.0 has a cp314 arm64 wheel (1.7 MB); av and tokenizers have abi3/cp314 wheels; also needs onnxruntime. No torch |
| openai-whisper | 20250625 | sdist only | needs torch |
| demucs | 4.1.0 | pure py | needs torch, sphn (cp314 wheel ok), lameenc (ok), julius, einops. Upstream repo archived 2025-01-01 |
| noisereduce | 3.0.3 | pure py (MIT) | scipy, numpy, matplotlib, tqdm, joblib; no torch |
| pyroomacoustics | 0.10.1 | yes, universal2 (1.1 MB) | MIT-style (EPFL-LCAV copyright line; exact SPDX [UNKNOWN]) |
| laion-clap | 1.1.7 | pure py | pins **numpy<2.0.0** and drags wandb, webdataset, pandas, h5py, llvmlite, librosa, torchlibrosa. Conflicts with our numpy 2.5 |
| msclap | 1.3.4 | pure py | pins numpy<2, librosa<0.11, transformers<5, torch, torchaudio. Conflicts with numpy 2.5 and librosa 1.0 |
| transformers | 5.22.2 | pure py (13.1 MB) | Apache-2.0; needs torch to run ClapModel |
| panns-inference | 0.1.1 | pure py | deps matplotlib, librosa, torchlibrosa (torch) |
| frechet-audio-distance | 0.3.4 | pure py | deps torch, torchvision, torchaudio, laion_clap, encodec, resampy, transformers; for py>=3.13 it asks numpy>=2 while laion_clap asks numpy<2, so resolution looks contradictory [ASSUMPTION: untested] |
| pedalboard | 0.9.26 | (already in) | **PyPI lists GNU GPL v3**. Fine for an internal pipeline tool; matters if AssetForge is ever distributed |

Sources: https://pypi.org/pypi/<name>/json for each row.

## 2. Candidate-by-candidate

Format: use for us / footprint / licence code | weights / M3 CPU / verdict.

**librosa 1.0.0.** Features (STFT, MFCC, onset, chroma, tempo, harmonic/percussive split, spectral flatness, HPSS).
Needs numba 0.68.1 + llvmlite 0.50.0 (both have cp314 arm64 wheels, so 3.14 is not the blocker it was in 2024),
scikit-learn (8 MB) and soxr. Disk: roughly 100 MB total [ASSUMPTION; wheels sum to ~55 MB compressed]. ISC, no
weights. CPU: fine, but numba JIT adds multi-second first-call latency. We already compute loudness, centroid,
flatness, onsets and bpm in `audio/features.py`/`quality.py` with NumPy/SciPy. Verdict: **optional extra**
(`audio-lab`), only for ad-hoc comparison against our own metrics (e.g. validating chroma/key); not on the
runtime path.

**essentia / essentia-tensorflow.** Mature MIR (key, bpm, loudness EBU R128, danceability), arm64 wheels exist for
3.14 (macOS 15+ tag). Code AGPL-3.0 (https://api.github.com/repos/MTG/essentia, LICENSE file "Essentia Licensing.txt").
Models: "All the models created by the MTG are licensed under CC BY-NC-SA 4.0" with a proprietary licence on
request (https://essentia.upf.edu/models.html). Non-commercial weights plus AGPL = unacceptable for a commercial
pipeline unless we buy the licence. Verdict: **reject** (use only for offline human-side research, never shipped
or used to gate output).

**torchaudio 2.11.1.** I/O, resampling, transforms; wheel is ABI-agnostic but forces torch>=2.11.
We have soundfile + scipy.signal.resample_poly. Verdict: **reject** as a direct dependency (only comes in
transitively with the torch tier).

**Spotify pedalboard 0.9.26.** Already in. Effects and file I/O (reads many formats) only; no analysis models.
GPLv3 on PyPI metadata: note for any future distribution. Verdict: **keep, as is**.

**pyloudnorm 0.2.0.** BS.1770 loudness. We already have `audio/loudness.py` verified against the standard; this
would be a cross-check only. MIT, pure Python on numpy/scipy, ~0 MB. Verdict: **optional extra / test-only
cross-check**; no need in runtime.

**LAION CLAP.** Text/audio embedding; zero-shot labels ("speech", "wind", "ocean waves") and audio-text similarity.
- Code: CC0-1.0 (https://github.com/LAION-AI/CLAP). Original checkpoints repo `lukewys/laion_clap` is tagged
  cc0-1.0 on Hugging Face; sizes: 630k-audioset-best.pt 1,864 MB, fusion 1,864 MB, music_* 2,353 MB each
  (HF API). The GitHub README states no licence for checkpoints and says of the music/speech ones "Please take
  your own risk" (temporary release). Training data (LAION-Audio-630k) is described as collected with captions
  from several sources; the authors state they cannot release the trained dataset for copyright reasons
  (README). So the CC0 tag on the weights is a hosting-repo tag, and upstream data rights are
  **[UNKNOWN]**.
- HF Transformers `ClapModel` checkpoints: `laion/clap-htsat-unfused` and `-fused` Apache-2.0, 614.5 MB;
  `laion/larger_clap_general` and `larger_clap_music_and_speech` Apache-2.0, 776.4 MB (HF API).
- The `laion-clap` pip package is a poor fit (numpy<2 pin, wandb). Use `transformers` + `torch` instead.
- CPU: ~150M-class HTSAT-base audio tower; a 10 s clip is well under a few seconds on M3 CPU [ASSUMPTION; not
  benchmarked here]. Disk: torch + transformers + 0.6 GB weights = Tier 2.
- Verdict: **optional extra (Tier 2)** for reference-similarity scoring ("does this ambience sound like
  'rain on roof'?"). Treat output as a soft signal; CLAP is weak at negation ("no speech").

**Microsoft CLAP (msclap 1.3.4).** Code MIT. Weights on `microsoft/msclap`: tagged `ms-pl` (Microsoft Public
License) on HF; CLAP_weights_2023.pth is 690 MB, 2022 is 2.3 GB, clapcap 1.7 GB. numpy<2 / librosa<0.11 /
transformers<5 pins clash with our stack. Verdict: **reject** (same capability as the HF CLAP, worse pinning).

**YAMNet (MobileNetV1, 3.7M weights, 521 AudioSet classes, 0.96 s patches).**
- Code/model repo: https://github.com/tensorflow/models/tree/master/research/audioset/yamnet states no licence on
  the page. TF Hub now redirects to Kaggle (`kaggle.com/models/google/yamnet`); I could not read the Kaggle licence
  field (page not retrievable). Apache-2.0 is the commonly attributed licence and the audiomagic ONNX repo states
  "Apache 2.0, Copyright 2022 Google LLC; AudioSet ontology CC BY 4.0", but that is a third party restating it.
  Official-source licence: **[UNKNOWN, verify on Kaggle before shipping]**.
- Quality (same README): balanced mAP 0.306, d-prime 2.318 on AudioSet eval. Mediocre but adequate for "speech
  present / wind / water / music" gating. The class map drops gendered Speech/Singing; "Speech" is retained.
- Format options: Google's TFLite quantized variant takes a fixed 15,600-sample frame (0.975 s) and returns 521
  scores (https://www.tensorflow.org/lite/examples/audio_classification/overview, via search snippet; the "3.7 MB
  size" figure was not confirmed). Runtime would be `ai-edge-litert` 2.3.0 (cp314 arm64 wheel, 12.8 MB), since
  `tflite-runtime` has no macOS wheels.
- ONNX options, with provenance: (a) `audiomagic/yamnet-onnx`: tf2onnx opset 15 of Google's SavedModel v1, "no
  weights retrained, fine-tuned, pruned or quantized", input `waveform` float32 [num_samples] 16 kHz mono,
  dynamic length (https://huggingface.co/audiomagic/yamnet-onnx; uploader is a third party, file size not
  shown); (b) `qualcomm/YamNet`: 14.2 MB, 3.73M params, input 1x1x96x64 (pre-computed log-mel), tagged MIT, but
  based on the `w-hc/torch_audioset` port, i.e. a re-trained/ported model, not Google's file; licence of the
  underlying weights unclear (https://huggingface.co/qualcomm/YamNet); (c) `niobures/YAMNet` onnx mirror
  (unverified provenance).
- Verdict: **adopt in Tier 1 if the Kaggle licence checks out**; prefer converting ourselves once with tf2onnx
  in a throw-away env (needs TF just for the conversion, not on the 4 GB machine) or taking (a) with a SHA-256
  pin recorded in the repo.

**PANNs (CNN14).** https://github.com/qiuqiangkong/audioset_tagging_cnn is MIT; weights on Zenodo record
3987831 are **CC-BY-4.0** (verified via zenodo API). Sizes: Cnn14_mAP=0.431 is 327 MB; Cnn10 25 MB; Cnn6 24 MB;
MobileNetV1 24 MB (mAP 0.389); MobileNetV2 21 MB (mAP 0.383). `panns_inference` 0.1.1 needs librosa +
torchlibrosa + torch (it is also old, last push 2024-03). Best licence story among AudioSet taggers (CC-BY needs
attribution only, but note the AudioSet training audio itself is YouTube content; the same applies to every
AudioSet model). CPU: Cnn10/MobileNet are cheap on M3 [ASSUMPTION]. Verdict: **optional extra (Tier 2)** via
the repo's model code directly, MobileNetV1 weights (24 MB) as the small tagger; ONNX export of these is not
something I found with verified provenance, so export it ourselves.

**AST (MIT/ast-finetuned-audioset-10-10-0.4593).** HF: BSD-3-Clause, 346 MB safetensors; code BSD-3-Clause
(YuanGongND/ast). Xenova ONNX conversion exists with variants of 346.8 / 173.7 / 90.8 / 60 / 51 MB (int8
`model_quantized.onnx` 90.8 MB; HF API) but the Xenova repo has no licence tag, so inherited-licence
[UNKNOWN, ASSUMPTION BSD-3]. Strongest published AudioSet mAP of the group (0.459 by checkpoint name) but a
1024-token ViT; heavy for a 10-second ambience on CPU [ASSUMPTION]. Verdict: **research only** (could run with
onnxruntime + a feature extractor in pure NumPy; use as an offline check, not a gate).

**BEATs.** https://github.com/microsoft/unilm/tree/master/beats: README says only "licensed under the license
found in the LICENSE file in the root"; the repo-level licence is MIT (GitHub API), but there is no LICENSE inside
`beats/` and checkpoints are OneDrive links without a licence note or sizes. Checkpoint licence **[UNKNOWN]**.
Verdict: **reject** until Microsoft states checkpoint terms.

**EfficientAT (mn10_as).** Code MIT (https://github.com/fschmid56/EfficientAT). mn10_as: 4.88M params, 0.54 G
MACs, AudioSet mAP 47.1; weights auto-downloaded from GitHub Releases; README states no separate weights licence
and no sizes ([UNKNOWN] licence for weights; likely small, ~20 MB [ASSUMPTION]). Needs torch; repo pins
Python 3.10.8 and ffmpeg <4.4. This is the best accuracy per MAC of the CNN taggers (mAP 47.1 vs YAMNet 30.6
balanced/PANN MobileNetV1 38.9; different eval protocols, do not over-read). Verdict: **optional extra
(Tier 2)** if we export to ONNX ourselves; **research only** until the weights licence is confirmed with the
author.

**Silero VAD 6.2.3.** The ONNX is 2.3 MB (`silero_vad.onnx` 2,327,524 B; 16k-op15 1.29 MB; half 1.28 MB, from the
GitHub contents API). Code and model: the repo LICENSE file is MIT, the README body states "Published under
permissive license (MIT)", but one README badge has alt text "CC BY-NC 4.0" while its image and link are MIT.
Net: MIT per the LICENSE file; the stray alt text looks like a typo [flag it in our NOTICE file]. Training: "over
6000 languages" per README. The pip package hard-requires torch; avoid it. Instead take the ONNX straight from
`onnx-community/silero-vad` (HF, MIT-tagged, model.onnx 2.2 MB, fp16 1.2 MB) or the file shipped inside the
wheel and run it with onnxruntime (the model needs stateful 512-sample chunks at 16 kHz; wrapper code from the
repo's `utils_vad.py` must be re-implemented: "you must handle I/O and adapt wrappers yourself" per README).
Verdict: **adopt now (Tier 1)** via onnxruntime; pin SHA-256 of the file.

**webrtcvad.** Use `webrtcvad-wheels` 2.0.14.post1 (cp314 arm64 wheel, MIT). GMM-based energy/spectral VAD for
10/20/30 ms frames at 8/16/32/48 kHz. A third-party 2026 study found Silero "consistently and significantly
outperformed" WebRTC VAD on a 1.1 h set (https://arxiv.org/html/2601.17270v1; vendor Picovoice reports the
opposite at a 25 % FPR operating point). Verdict: **optional extra**; useful as a fast, dependency-free second
opinion. WebRTC is far more triggered by generic noise.

**pyannote.audio 4.0.7.** Code MIT; `pyannote/segmentation-3.0` and `speaker-diarization-3.1` are MIT-tagged but
**gated** (auto-approve after accepting conditions and sharing contact info). Segmentation model is 5.9 MB. The
package needs torch>=2.8, lightning, torchcodec. Overkill: we need "is there a voice", not "who". Verdict:
**reject**.

**Whisper / faster-whisper.** faster-whisper 1.2.1 (MIT) runs on `ctranslate2` (no torch). CPU on Apple
Silicon runs CTranslate2 on CPU only (no Metal) [ASSUMPTION; not checked]. Weights: Systran/faster-whisper-tiny
(MIT-tagged) 75.5 MB, base 145.2 MB. OpenAI's original checkpoint repo `openai/whisper-tiny` is Apache-2.0
(HF API; the GitHub repo is MIT). Whisper is known to hallucinate text on non-speech audio; use the
`no_speech_prob`, `avg_logprob` and `compression_ratio` fields, never the text itself, as evidence. Verdict:
**optional extra (Tier 1b)**; `tiny` as the speech-confirmation step only on segments flagged by VAD.

**Demucs.** Code MIT, repo archived by owner on 2025-01-01 (maintenance fork `adefossez/demucs`). README gives no
licence statement for the pretrained weights **[UNKNOWN]** and no file sizes; `htdemucs_ft` is ~4x slower than
`htdemucs`; README says CPU runs about 1.5x track duration (hardware unspecified). Needs torch. Use for
us: separate a vocal-like layer from an ambience. Verdict: **research only**.

**noisereduce 3.0.3.** Spectral gating, MIT, numpy/scipy only, no model. We have no noisy recordings to
clean (we generate audio); it can only damage synthesized material. Verdict: **reject** for this pipeline.

**pyroomacoustics 0.10.1.** Image-source/RIR simulation, cp314 universal2 wheel (1.1 MB). We already have
reverb in `audio/effects.py` (Pedalboard) and `SPACES`. Could generate physically-based RIRs to replace
Pedalboard's algorithmic reverb. Verdict: **optional extra**, low priority.

**frechet_audio_distance 0.3.4.** MIT; wraps VGGish/PANN/CLAP/EnCodec embeddings. Pulls torch, torchvision,
torchaudio, laion_clap (numpy<2) and its own numpy>=2 for 3.13+; conflicting constraints [ASSUMPTION]. FAD is a
distribution-level metric needing hundreds of clips; meaningless for one 10 s loop. Verdict: **reject** (if
batch regression across the 80 recipes is ever wanted, compute Frechet distance ourselves on PANN or CLAP
embeddings in 20 lines of NumPy).

**onnxruntime 1.31.1.** cp314 macOS arm64 wheel (20.9 MB), MIT. CPU EP is enough for Silero and YAMNet.
Verdict: **adopt now (Tier 1)**.

## 3. Install plan under a 4 GB disk budget

Disk numbers below are wheel/file sizes (verified); unpacked sizes are [ASSUMPTION] about 2-3x for torch.

**Tier 0: always on (no new deps).** NumPy 2.5, SciPy 1.18, soundfile, pedalboard 0.9.26. Everything already in
`audio/quality.py`, `features.py`, `loudness.py`. Add pure-NumPy harmonicity (autocorrelation pitch strength),
spectral flux and formant-ish LPC checks here (section 4). Cost 0 MB.

**Tier 1: light ML (`uv sync --extra analysis`), about 30 MB wheels plus models.**
- `onnxruntime==1.31.1` (20.9 MB)
- Silero VAD `silero_vad.onnx` (2.3 MB; MIT; pin SHA-256) - run directly, **do not** `pip install silero-vad` (drags torch)
- `webrtcvad-wheels==2.0.14.post1` (optional second opinion)
- One sound-event tagger: YAMNet ONNX (about 15 MB) **only after** the Kaggle licence is confirmed and the
  conversion hash is pinned; otherwise no tagger in Tier 1 (VAD + harmonicity only).
- Optional: `faster-whisper==1.2.1` + `ctranslate2==4.9.0` + `av` + `tokenizers` (all have 3.14 arm64 wheels)
  + `faster-whisper-tiny` 75.5 MB. About 120-200 MB total [ASSUMPTION].

**Tier 2: torch-based (`--extra audio-lab`), expect >=1.5 GB with models; do not enable on this disk without
freeing space.**
- `torch==2.15.0`: cp314 arm64 wheel exists today (138.6 MB compressed). Unpacked size [UNKNOWN; measure with
  `du` after install in a scratch venv].
- `transformers==5.22.2` (13.1 MB) + `laion/clap-htsat-unfused` (614.5 MB) for CLAP; or `larger_clap_general`
  776.4 MB.
- PANNs MobileNetV1 weights 24 MB (CC-BY-4.0) + model code from the repo (MIT).
- Demucs not recommended (archived, weights licence unknown).
- Do not use `laion-clap`, `msclap`, `frechet-audio-distance` (numpy<2 pins).
- Use `HF_HOME` and `TORCH_HOME` on an external drive to keep weights off the 4 GB.

**Pins to avoid:** `tflite-runtime` (no macOS wheel), `webrtcvad` (sdist only), `openai-whisper` (sdist + torch),
`essentia*` (AGPL, NC models).

## 4. "Speech-like artifact in ambience": what a VAD actually detects

Practical summary, with the evidence limits stated.

- **What a VAD does.** It labels short frames (Silero v5/v6 uses 32 ms chunks at 16 kHz; WebRTC 10/20/30 ms) as
  speech vs non-speech from spectral/temporal cues learned or modelled from speech: harmonic structure at
  80-400 Hz fundamental, energy in 300-3400 Hz, syllabic amplitude modulation of 3-8 Hz, formant-like spectral
  peaks. It is not a detector of "a human voice"; it is a detector of "speech-like modulation".
- **Known false positives (third-party sources, search 2026-11-11).** Silero stays below 0.8 on pure music in one
  practitioner test but occasionally assigns high probability to static noise; a university study found music
  streams "challenging for all the models" (https://arxiv.org/html/2601.17270v1, via search summary); WebRTC
  VAD is energy-sensitive and trips on generic noise. Based on the mechanism (not verified by a source I read):
  wind with tonal howl, crowd murmur (literally babble), vocal-like synth pads/choirs, bowed strings with
  vibrato, and 4-6 Hz tremolo/vibrato textures are all plausible triggers. Our own generated ambiences (crowd
  murmur, wind, choir pads) are the highest-risk content class.
- **How the field corroborates.** No single detector is trusted. Common stack: (1) VAD speech probability and
  duration of the longest speech-labelled run; (2) an AudioSet tagger (YAMNet/PANNs/AST) giving "Speech",
  "Conversation", "Narration, monologue", "Crowd", "Whispering" vs "Wind", "Rain", "Music"; (3) a
  signal-level harmonicity/formant check (autocorrelation peak > 0.5 at lags 2.5-12 ms with a continuously
  gliding f0; LPC formants F1/F2 plausibly spaced); (4) ASR evidence: Whisper's `no_speech_prob`, low
  `avg_logprob`, high `compression_ratio` and repeated hallucinated text ("Thank you.") indicate non-speech,
  while stable multi-word output with high logprob indicates real speech. Treat Whisper text as evidence of
  speech-likeness only; ASR hallucinates on noise.
- **Recommended gate for us.** Fail only when at least two independent signals agree (VAD>0.6 for >=1 s
  cumulative AND (tagger speech-class>0.3 OR harmonicity+formant flag)); otherwise "warn". Log per-signal
  numbers in `metadata.qc` so thresholds can be tuned on our 80 recipes + 24 scenes. Thresholds above are
  [ASSUMPTION], not published values; calibrate on labelled renders first.
- **Mandatory calibration.** Render a labelled set: pure ambiences (should pass), the same with a real speech
  clip mixed at -20 dB (should fail), and known confusers (crowd bed, choir, wind). Record ROC before gating.

## 5. Decisions

1. Tier 0 stays the default and the only blocking gate until Tier 1 is calibrated.
2. Tier 1 adds `onnxruntime` + Silero ONNX (adopt now); YAMNet after licence verification; faster-whisper tiny
   optional.
3. Tier 2 torch stack: not on this machine's current 4 GB; revisit when disk is freed. CLAP via HF transformers
   if ever.
4. Record each model's SHA-256, source URL and licence in `NOTICE`/`models.json` when added.
5. Open items: Kaggle YAMNet licence; EfficientAT and BEATs weights licence; torch unpacked size; M3 latency
   benchmarks for any model (none measured in this document).

## Sources actually read

- PyPI JSON API for all packages in section 1 (2026-11-11)
- https://huggingface.co/api/models/<id> for laion/clap-*, larger_clap_*, lukewys/laion_clap, microsoft/msclap,
  MIT/ast-finetuned-audioset-10-10-0.4593, Xenova/ast-..., pyannote/*, Systran/faster-whisper-*, openai/whisper-tiny,
  onnx-community/silero-vad, deepghs/silero-vad-onnx
- https://github.com/snakers4/silero-vad (README, LICENSE, data dir via API)
- https://github.com/LAION-AI/CLAP
- https://github.com/tensorflow/models/tree/master/research/audioset/yamnet
- https://huggingface.co/audiomagic/yamnet-onnx, https://huggingface.co/qualcomm/YamNet
- https://github.com/fschmid56/EfficientAT, https://github.com/facebookresearch/demucs
- https://github.com/microsoft/unilm/tree/master/beats, https://essentia.upf.edu/models.html
- https://zenodo.org/records/3987831 (PANNs, CC-BY-4.0, via API)
- https://arxiv.org/html/2601.17270v1 and other VAD comparisons: search-result summaries only, not read in full
