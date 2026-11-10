# 16. Audio technology evaluation: music and ambience loops

Date: 2026-11-11. Target: Apple M3, 16 GB, no NVIDIA GPU. Use case: instrumental music loops and ambience loops for a mobile visual novel, generated at production time, commercially shipped.

Tags: [VERIFIED] read in the cited source; [EDITORIAL] my judgement; [UNKNOWN] could not confirm. "Not found" means the page did not say. No benchmark on this Mac was run. Everything below is from vendor pages, so speeds are vendor claims.

## 1. Local open-weight models

### Headline finding
Stable Audio 3.0 (released 2026-05-20) is new and changes the picture. Small SFX, Small and Medium have open weights; Large is API or enterprise only. [VERIFIED] https://stability.ai/news-updates/meet-stable-audio-3-the-model-family-built-for-artistic-experimentation-with-open-weight-models

### Table A: models and licences

| Model | Generates | Max length / rate | Params | Weights licence (gated?) | Commercial verdict |
|---|---|---|---|---|---|
| Stable Audio 3 Small Music | music | "several minutes"; 120 s used in example; rate not stated (read from config) | 0.6B | Stability AI Community License + Gemma Terms for T5Gemma parts; gated: yes [VERIFIED] https://huggingface.co/stabilityai/stable-audio-3-small-music | Free if org revenue under US$1M; above that, Enterprise Licence [VERIFIED] https://stability.ai/community-license-agreement |
| Stable Audio 3 Small SFX | sound / ambience | duration not stated (example 7 s) | 0.6B | same, gated [VERIFIED] https://huggingface.co/stabilityai/stable-audio-3-small-sfx | same |
| Stable Audio 3 Medium | music | example 180 s and 380 s; announcement says up to 6:20 [VERIFIED] https://huggingface.co/stabilityai/stable-audio-3-medium | 2B | same, gated | same |
| Stable Audio Open 1.0 | sound effects, field recordings; weaker at music, no vocals | 47 s, 44.1 kHz stereo | ~1B | Stability Community License, gated [VERIFIED] https://huggingface.co/stabilityai/stable-audio-open-1.0 | same |
| Stable Audio Open Small | short sounds | 11 s, 44.1 kHz stereo | 0.5B | same, gated; CPU fallback in sample code, no speed figures [VERIFIED] https://huggingface.co/stabilityai/stable-audio-open-small | same |
| ACE-Step v1 (3.5B) | music, instrumental capable | up to 4 min | 3.5B | Apache 2.0 [VERIFIED] https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B ; https://github.com/ace-step/ACE-Step | Allowed, no threshold seen |
| ACE-Step 1.5 | music | 10 s to 10 min | DiT 2B (turbo, sft); XL 4B DiT; LM 0.6/1.7/4B | Hugging Face tag MIT, no licence file in the weights repo per one discussion [VERIFIED tag] https://huggingface.co/ACE-Step/Ace-Step1.5 ; code MIT https://github.com/ace-step/ACE-Step-1.5 | Card says commercial use of output is allowed; training data described as licensed, royalty-free/public-domain and synthetic MIDI audio. The licence-text gap is [UNKNOWN] |
| DiffRhythm | song-length music, instrumental mode | base 1 m35 s; full 4 m45 s | not stated | Code and DiT weights Apache 2.0; VAE weights licence not stated [VERIFIED] https://github.com/ASLP-lab/DiffRhythm | Likely allowed; VAE licence [UNKNOWN] |
| YuE (YuE2) | songs / instrumental via agent skill | not stated | ~3B | Code Apache 2.0; weights CC BY-NC 4.0 with creator permission; companies must contact authors [VERIFIED] https://github.com/multimodal-art-projection/YuE | Blocked for a studio without a deal |
| HeartMuLa (new) | songs | default 4 min | 3B | Apache 2.0 for code and weights since 2026-01-20 [VERIFIED] https://github.com/HeartMuLa/heartlib | Allowed; no instrumental mode or Mac support documented |
| MusicGen (Meta) | music | duration not stated | 300M / 1.5B / 3.3B | Code MIT; weights CC-BY-NC 4.0 [VERIFIED] https://github.com/facebookresearch/audiocraft/blob/main/model_cards/MUSICGEN_MODEL_CARD.md | Blocked |
| AudioGen medium | sound effects | 5 s in examples; 16 kHz EnCodec | 1.5B | cc-by-nc-4.0 [VERIFIED] https://huggingface.co/facebook/audiogen-medium | Blocked |
| AudioLDM 2 | sound, music variants | examples 10 s; saved at 16 kHz | UNet 350M / 750M | cc-by-nc-sa-4.0 [VERIFIED] https://huggingface.co/cvssp/audioldm2 | Blocked |
| TangoFlux | sound / audio | up to 30 s, 44.1 kHz | not stated | "non-commercial research use only" [VERIFIED] https://huggingface.co/declare-lab/TangoFlux | Blocked |

Not found: Tango (original) and MiniMax Music 3 primary sources. A blog calls MiniMax Music 3 open-weight with an unnamed licence; I did not verify it. [UNKNOWN]

### Table B: Mac fit, speed, conditioning, loops

| Model | Apple Silicon evidence | Reference audio / editing | Loop support |
|---|---|---|---|
| Stable Audio 3 Small/Medium | Card claims "less than a few seconds on a MacBook Pro M4" and "consumer-grade hardware"; MPS not named; vendor claim only [VERIFIED claim] https://huggingface.co/stabilityai/stable-audio-3-medium | Inpainting, single and multi-segment editing, causal continuation [VERIFIED] announcement above | Not found |
| Stable Audio Open (both) | CPU fallback only; no timings | Not found on card | Not found |
| ACE-Step v1 | M2 Max RTF 2.27x at 27 steps (about 26 s per minute of audio) and 1.03x at 60 steps; macOS needs `--bf16 false` [VERIFIED] https://github.com/ace-step/ACE-Step | Audio2Audio, repaint, extend, lyric edit [VERIFIED] | Not found |
| ACE-Step 1.5 | MPS listed, macOS launch script using MLX; no Apple timings; 6-8 GB tier = 2B turbo + 0.6B LM; community MLX ports exist, quality unverified [VERIFIED] https://github.com/ACE-Step/ACE-Step-1.5 | Reference audio, cover, repaint on base/sft/turbo [VERIFIED] | Card says "short loops" in its scale range; no loop-seam feature seen |
| DiffRhythm | macOS support added 2025-03-11; base needs at least 8 GB with `--chunked` | Reference WAV or text-prompt style reference [VERIFIED] | Not found |
| HeartMuLa | Not mentioned; CUDA default | Reference audio not yet supported | Not found |

[EDITORIAL] No candidate documents seamless loop output. Loops will need our own `crossfade_loop`/`fold_loop` post-processing (already in the repo) plus the seam check in section 4.

Memory: only ACE-Step 1.5 gives per-tier numbers (above). Stable Audio models give none. [UNKNOWN] for the rest.

Output terms: Community License says "You own any outputs", subject to law; commercial users must register; free below US$1M annual revenue, licence ends on passing it [VERIFIED] https://stability.ai/community-license-agreement . Apache/MIT models place no output claim, but ACE-Step 1.5's disclaimer asks users to verify originality [VERIFIED] https://github.com/ACE-Step/ACE-Step-1.5 .

## 2. Hosted APIs

| Service | Price | Output rights | Instrumental / length / loops / reference | API |
|---|---|---|---|---|
| Stability, Stable Audio 3.0 (to 6 min) and 2.5 (to 3 min) | 8 credits each; 1 credit = US$0.01 [VERIFIED] https://docs.stability.ai/pricing | Trained on licensed data; Community License terms for models; API-specific output terms not read [UNKNOWN] | 2.5 has audio-to-audio and inpaint per reseller pages (not Stability's own) https://comfy.org/hub/workflows/stability-audio-2-5-audio-inpainting-6c6b1b1f2443/ ; loops not found | Yes |
| ElevenLabs Music (`music_v2_5`) | Music pricing not found. SFX: 40 credits per second when duration set [VERIFIED] https://elevenlabs.io/docs/overview/capabilities/sound-effects | Paid plans: commercial; Free plan excludes games; Studio Games (lifetime game revenue over US$500k, or developer revenue over US$1M) need Enterprise Music; library creation and resale prohibited except Enterprise [VERIFIED] https://elevenlabs.io/music-terms | 3 s to 5 min; instrumental feature listed, parameter not described; 30 s reference audio on paid plans; inpainting in UI, API unconfirmed [VERIFIED] https://elevenlabs.io/docs/overview/capabilities/music . SFX: up to 30 s, looping option, but page contradicts itself | Paid plans |
| Google Lyria (Vertex: `lyria-002`, `lyria-3-clip-preview`, `lyria-3-pro-preview`; Gemini API: Lyria 3.5) | Not found | Not found; SynthID watermark on all output [VERIFIED] https://aistudio.google.com/docs/music-generation | Lyria 2 clips are 32.8 s, negative prompt and seed supported [VERIFIED] https://docs.cloud.google.com/vertex-ai/generative-ai/docs/music/generate-music ; instrumental via prompt only; no reference audio | Yes, preview status [UNKNOWN] |
| Suno | n/a | Paid-plan commercial rights claimed by secondary sources | No public API; invite-only partner programme announced July 2026 https://www.digitalmusicnews.com/2026/07/03/suno-is-opening-an-api-partner-program/ ; "Suno API" products are third-party wrappers | Not available |
| Udio | n/a | not found | No official public API per secondary sources [UNKNOWN] | Not confirmed |
| Mubert | Trial US$49/mo, Startup US$199, Startup+ US$499 [VERIFIED] https://mubert.com/api | "Cleared for monetization" from Startup up; Trial excludes monetized content | 15 s to 25 min; loops not mentioned | Yes |
| Soundraw | US$29.99/mo (100 songs) or US$300/mo (1,000, 6-month minimum) [VERIFIED] https://www.soundraw.io/api | "Safe for commercial use"; plan licence text not read | Loops, instrumental not stated | Yes |
| Beatoven.ai | API pricing not found; consumer plans from secondary sources | Paid-plan commercial licence per secondary sources [UNKNOWN] | not found | Unconfirmed |

[EDITORIAL] Stability is the best hosted fit: cheap per call, instrumental-leaning, long tracks, edit modes, and the same rights regime as the local models.

## 3. Sample-based and procedural

| Resource | Licence | Notes |
|---|---|---|
| GeneralUser GS | Free use "private or commercial"; author cannot be 100% sure of every sample's origin [VERIFIED via licence text mirror] https://users.cs.jmu.edu/bernstdh/web/common/lectures/javaexamples/auditory/described/GeneralUser%20GS%201.44%20SoftSynth/LICENSE.txt (v1.44; the author's page fetch failed) | Sample-origin caveat matters if shipping the SoundFont itself; rendering audio is safer |
| FluidR3_GM | MIT per original readme [VERIFIED] https://people.debian.org/~tg/sf/FluidR3Mono_GM/copyright | Partly built from public-domain samples; keep notices |
| Virtual Playing Orchestra | Layered: mixes CC Sampling Plus 1.0 (SSO), CC BY-SA 3.0 and CC BY-SA 4.0 parts; website licence wins [VERIFIED via search] https://os.zynthian.org/factory_sfz/Other/VPO3-perf/Documentation/license.htm | Share-alike risk; I could not read VPO's own page [UNKNOWN] |
| Sonatina Symphonic Orchestra | CC Sampling Plus 1.0 [VERIFIED] https://sfzinstruments.github.io/orchestra/sso | Freesound describes Sampling+ as NC-like and no advertising, retired by Creative Commons [VERIFIED] https://freesound.org/help/faq/ . [EDITORIAL] Treat as not safe for a commercial app |
| Salamander Grand Piano | CC-BY-3.0 [VERIFIED] https://sfzinstruments.github.io/pianos/salamander/ | Commercial use fine with credit; 394 MB (16-bit) to 1.18 GB |
| VSCO 2 Community Edition | CC0 (Versilian site, Freesound announcement) [VERIFIED via search] https://versilian-studios.com/vsco-community/ | Best-licensed orchestral source; derivative packs may mix licences |
| Versilian Community Sample Library | CC0-1.0 listed [VERIFIED via search] https://sfzinstruments.github.io/misc | Broader VSCO extension |
| Philharmonia samples | Commercial use allowed; cannot be resold "as is" [VERIFIED] https://philharmonia.co.uk/resources/sound-samples/ | Fine as rendered audio, not as a redistributed instrument |
| FluidSynth | LGPL-2.1 [VERIFIED] https://github.com/FluidSynth/fluidsynth | Calling the CLI at production time raises no linking issue |
| sfizz | BSD-2-Clause; repository archived 2026-06-21 [VERIFIED] https://github.com/sfztools/sfizz | Works today, no future fixes |

Freesound: CC0 needs no credit; CC-BY allows commercial use but requires credit (list on a credits page); CC-BY-NC and Sampling+ are unsuitable for a commercial app [VERIFIED] https://freesound.org/help/faq/ . [EDITORIAL] Filter to CC0 for bed layers; keep per-sound licence records in AssetForge. Open-source soundscape tools: not researched, not found.

## 4. Evaluation tooling

| Tool | Licence | Use | CPU/MPS |
|---|---|---|---|
| pyloudnorm | MIT [VERIFIED] https://github.com/csteinmetz1/pyloudnorm | BS.1770-4 LUFS, LRA | NumPy/SciPy; runs anywhere. AssetForge already has its own `audio/loudness.py` |
| frechet_audio_distance | MIT; VGGish, PANN, CLAP, EnCodec embeddings [VERIFIED] https://github.com/gudgud96/frechet-audio-distance | FAD and a CLAP score | Not stated [UNKNOWN] |
| LAION CLAP | CC0-1.0 [VERIFIED] https://github.com/LAION-AI/CLAP | Text-audio similarity (prompt adherence) | Examples use CUDA; device argument exists; CPU unconfirmed |
| Microsoft CLAP | MIT, archived [VERIFIED] https://github.com/microsoft/CLAP | Same idea | CPU shown (`use_cuda=False`) |
| audiobox-aesthetics | Mostly CC-BY 4.0; checkpoint licence not stated [VERIFIED] https://github.com/facebookresearch/audiobox-aesthetics | CE, CU, PC, PQ scores | Not stated |
| PANNs | MIT code; checkpoint licence not stated [VERIFIED] https://github.com/qiuqiangkong/audioset_tagging_cnn | 527-class AudioSet tags | CPU not documented |
| PaSST | Apache-2.0 [VERIFIED] https://github.com/kkoutini/PaSST | AudioSet tags, `hear21passt` | CPU not documented |
| Loop seam check | In repo (`validation/audio.loop_seam`) | Click and level jump at the wrap | Pure NumPy |

[EDITORIAL] Run CLAP and taggers on CPU; they are small. Metrics are screens, not judges: listen to anything shipped.

## 5. Recommendation matrix [EDITORIAL]

| Need | Best fit | Try first | Risks |
|---|---|---|---|
| (a) Ambience beds | Keep procedural beds; add Stable Audio 3 Small SFX or Open as extra layers; CC0 Freesound as layer material | 3 Small SFX with 20 prompts, then seam-check | Duration and loop ability undocumented; HF gate; US$1M cap |
| (b) Short cues and themes | Stable Audio 3 Small Music or ACE-Step 1.5 turbo; sample rendering via VSCO 2 CE or GeneralUser GS for orchestral | ACE-Step 1.5 (no gate, permissive tag) vs Stable Audio 3 Small, 10 cues each | ACE-Step 1.5 weights licence text gap; MPS reports thin |
| (c) Seamless music loops | Generate longer, then loop in our `crossfade_loop`; or keep the composer and upgrade samples | Ask models for 60 to 90 s, find a bar-aligned loop point, run seam check | No model promises loops; rhythmic drift at the join |
| (d) Reference-conditioned variations | ACE-Step 1.5 reference audio/cover/repaint; Stable Audio 3 inpaint and continuation; ElevenLabs 30 s reference as hosted option | ACE-Step 1.5 cover on our own composer output | Reference input must be our own audio, never third-party tracks |

Gates before any test can run:
- Hugging Face licence acceptance and a token: all Stable Audio 3 and Stable Audio Open models. Also register with Stability for commercial use.
- API key or paid plan: Stability API, ElevenLabs (paid plan), Google Cloud/Gemini key, Mubert, Soundraw. Suno and Udio: not available.
- No gate seen: ACE-Step (v1, 1.5), DiffRhythm, HeartMuLa, FluidSynth and the CC0 sample sets (gating on ACE-Step and DiffRhythm weights not stated on the pages read).

## Sources worth rereading before a purchase decision
Licence texts: https://stability.ai/community-license-agreement , https://elevenlabs.io/music-terms
