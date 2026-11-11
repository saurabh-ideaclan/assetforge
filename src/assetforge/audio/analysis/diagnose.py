"""Diagnosis: from a report (and, when known, the brief) to named problems with evidence, a likely cause
class and the corrections that address *that* cause.

The taxonomy the owner asked for, in code. Every finding carries:
  code        a stable name (`clipping`, `voice_like`, `repetitive`, `in_dialogue_band`…)
  severity    hard (unusable), problem (should be fixed), note (worth knowing)
  confidence  measured (a defined measurement crossed a defined line), estimated (a heuristic signature or
              a threshold calibrated on our corpus), listen (only a listener can settle it; we point at where)
  cause       source | synthesis | composition | sound_design | mix | emotion | method  (the seven classes)
  evidence    the numbers, in words a reviewer can check against the visuals
  where       segments (start_s, end_s) when the problem is local
  corrections ordered options: {"action", "kind": dsp|layer|composition|regenerate|method, "params", "why"}
Corrections name real operations (`audio/process.py` ops, composition fields of `production.refine`, layer
removal in an ambience spec, or a method change), never "ask the prompt for better". A finding about intent
("too dramatic for the scene") is `estimated` or `listen`: the measurements narrow it down, they do not decide.
"""

from __future__ import annotations

import re

from typing import Any

from assetforge.audio.master import LOUDNESS_TARGETS
from assetforge.audio.quality import expectations

VOICE_WORDS = ("voice", "voices", "crowd", "murmur", "chatter", "talk", "speech", "singing", "choir", "people", "market", "tavern", "audience")


def _f(code: str, severity: str, confidence: str, cause: str, evidence: str, corrections: list[dict[str, Any]],
       where: list[dict[str, Any]] | None = None, message: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {"code": code, "severity": severity, "confidence": confidence, "cause": cause, "evidence": evidence,
            "where": where or [], "corrections": corrections, "message": message or evidence}  # fmt: skip


NEGATIONS = r"(?:\bno\b|\bwithout\b|\bnever\b|\bnot\b|\bfree of\b|\bempty of\b|\bdeserted\b)"


def _brief_allows_voices(brief: str | None, direction: dict[str, Any] | None) -> bool:
    """Does the scene have people in it? The brief decides: "crowd murmur" or "a busy market" yes; "no voices",
    "without people", "deserted", "nobody" no (a negation within three words before a voice word cancels it).
    Only when the brief says nothing does the direction's environment phrase count. Bed names never count: a
    `crowd_murmur` pick under a brief with nobody in it is exactly the mistake this finding exists to catch."""
    words = "|".join(re.escape(w) for w in VOICE_WORDS)
    negated = NEGATIONS + r"(?:\s+\w+){0,3}?\s+(?:" + words + r")\b"

    def scan(text: str) -> bool | None:
        text = text.lower().replace("_", " ")
        if not text.strip():
            return None
        positive = any(re.search(r"\b" + re.escape(w) + r"\b", re.sub(negated, " ", text)) for w in VOICE_WORDS)
        if positive:
            return True
        if re.search(negated, text) or re.search(r"\b(nobody|no one|no-one|noone|deserted|empty|uninhabited|abandoned|alone)\b", text):
            return False
        return None

    verdict = scan(brief or "")
    if verdict is None and direction:
        verdict = scan(str(direction.get("environment", "")))
    return bool(verdict)


def diagnose(report: dict[str, Any], *, kind: str | None = None, brief: str | None = None,
             direction: dict[str, Any] | None = None, loop: bool | None = None) -> list[dict[str, Any]]:  # fmt: skip
    kind = kind or report.get("kind") or "music"
    t = report["technical"]
    findings: list[dict[str, Any]] = []
    dur = t["duration_s"]
    loop_known = loop is not None
    expect_loop = bool(loop) if loop_known else kind == "ambience"  # a music file may be linear: a seam is then a note
    sfx = kind == "sfx"

    # ---------------------------------------------------------------- technical (hard)
    if report.get("spectral", {}).get("silent") or t["peak_dbfs"] < -60:
        return [_f("silent", "hard", "measured", "synthesis", f"peak {t['peak_dbfs']} dBFS: nothing audible",
                   [{"action": "regenerate", "kind": "regenerate", "params": {}, "why": "there is no sound to fix"}])]
    if t["clipping"]:
        findings.append(_f("clipping", "hard", "measured", "mix",
                           f"{t['clipped_samples']} samples at full scale, longest run {t['longest_run']}: flattened waveform",
                           [{"action": "re-render quieter", "kind": "regenerate", "params": {"gain_db": -6}, "why": "clipping cannot be undone after the fact; no DSP step is offered"}]))  # fmt: skip
    elif t["true_peak_dbtp"] > -0.5:
        findings.append(_f("no_headroom", "problem", "measured", "mix", f"true peak {t['true_peak_dbtp']} dBTP; lossy encoding will overshoot",
                           [{"action": "normalize", "kind": "dsp", "params": {"mode": "peak", "target": -1.5}, "why": "leave 1.5 dB for the codec"}]))
    if abs(t["dc_offset"]) > 0.002:
        findings.append(_f("dc_offset", "problem", "measured", "synthesis", f"DC offset {t['dc_offset']}: wastes headroom, thumps on loop",
                           [{"action": "dc_remove", "kind": "dsp", "params": {}, "why": "a high-pass at 10 Hz removes it"}]))
    d = t["discontinuities"]
    click_limit = 3 if kind == "music" else 50 if kind == "ambience" else 20  # rain drops and foley clicks are the sound
    if d["count"] > click_limit:
        findings.append(_f("clicks", "hard" if d["count"] > 20 * (1 if kind == "music" else 5) else "problem", "measured", "synthesis",
                           f"{d['count']} sample-level discontinuities (first at {', '.join(str(p) for p in d['positions_s'][:3])} s)",
                           [{"action": "declick", "kind": "dsp", "params": {}, "why": "interpolate across each click"},
                            {"action": "regenerate", "kind": "regenerate", "params": {}, "why": "if the clicks are the synthesis (block edges), re-render"}],
                           where=[{"start_s": p, "end_s": p + 0.01} for p in d["positions_s"][:10]]))  # fmt: skip
    lp = t.get("loop")
    if lp and not lp["seam_ok"] and (expect_loop or (kind == "music" and not loop_known)):
        # a click is fixed by a short crossfade; a level step needs one longer than the 250 ms the level is judged
        # over, so the two levels blend inside that window (measured: 500 ms takes a 7.9 dB step to 0.3 dB)
        cross = 500 if lp["loop_level_jump_db"] > max(4.0, lp.get("level_change_p95_db", 0) + 3) else 120
        findings.append(_f("loop_seam", "hard" if expect_loop else "note", "measured", "sound_design",
                           f"the loop boundary is audible: click={lp['loop_click']}, level jump {lp['loop_level_jump_db']} dB "
                           f"(the sound itself moves {lp['level_change_p95_db']} dB)",
                           ([{"action": "reseed", "kind": "regenerate", "params": {"reseed": "structure"}, "why": "a bar-aligned render loops by construction; a crossfade would shorten the bar grid"},
                             {"action": "reloop", "kind": "dsp", "params": {"crossfade_ms": cross}, "why": f"equal-power crossfade (shortens the loop by {cross} ms)"}] if kind == "music" else
                            [{"action": "reloop", "kind": "dsp", "params": {"crossfade_ms": cross}, "why": "equal-power crossfade of the tail into the head"},
                             {"action": "reseed", "kind": "regenerate", "params": {"reseed": True}, "why": "re-render the beds past the loop point"}]),
                           where=[{"start_s": max(0.0, dur - 0.5), "end_s": dur}]))  # fmt: skip
    sil = t["silence"]
    if kind == "music" and sil["gaps"] and expect_loop:
        findings.append(_f("internal_silence", "problem", "measured", "composition",
                           f"{len(sil['gaps'])} silent gap(s) over 200 ms inside the piece", [{"action": "recompose", "kind": "composition", "params": {"energy": +0.1}, "why": "the arrangement drops out"}],
                           where=sil["gaps"]))  # fmt: skip
    st = t["stereo"]
    if st["channels"] >= 2 and st["correlation"] < -0.3:
        findings.append(_f("phase_cancellation", "problem", "measured", "mix", f"L/R correlation {st['correlation']}: a mono sum loses content",
                           [{"action": "polarity", "kind": "dsp", "params": {}, "why": "flip one channel: anti-phase content becomes in-phase"} if st["correlation"] < -0.7 else
                            {"action": "narrow", "kind": "dsp", "params": {"width": 0.6}, "why": "less side, more mid"}]))
    if st["channels"] >= 2 and abs(st["balance_db"]) > 4:
        findings.append(_f("stereo_balance", "problem" if abs(st["balance_db"]) > 6 else "note", "estimated", "mix", f"left/right differ by {st['balance_db']} dB",
                           [{"action": "balance", "kind": "dsp", "params": {"db": st["balance_db"]}, "why": "move level toward the quieter side"}]))

    # ---------------------------------------------------------------- loudness and dynamics (mix)
    target = LOUDNESS_TARGETS.get(kind, -16.0)
    lufs = t["loudness"]["integrated"]
    if lufs > -120 and abs(lufs - target) > 2.0 and not sfx:
        findings.append(_f("loudness_off", "problem" if abs(lufs - target) > 4.0 else "note", "measured", "mix", f"{lufs} LUFS, the {kind} target is {target}",
                           [{"action": "normalize", "kind": "dsp", "params": {"mode": "lufs", "target": target, "ceiling_db": -1.5}, "why": "level only; nothing else changes"}]))
    if kind == "music" and t["crest_db"] < 8 and t["loudness"]["lra"] < 2:
        findings.append(_f("over_compressed", "problem", "estimated", "mix",
                           f"crest {t['crest_db']} dB and loudness range {t['loudness']['lra']} LU: no transients, no breathing",
                           [{"action": "expand", "kind": "dsp", "params": {"ratio": 1.3}, "why": "gentle upward expansion restores some life"},
                            {"action": "recompose", "kind": "composition", "params": {"arc": "rise_fall"}, "why": "write the dynamics in instead"}]))  # fmt: skip

    sp = report.get("spectral") or {}
    if not sp or sp.get("silent"):
        return findings
    shares = sp["bands"]["shares"]
    summ = sp["summary"]
    ex = expectations(kind if kind in ("music", "ambience") else "music", direction or {})
    # ---------------------------------------------------------------- spectral balance (mix / sound design)
    rumbly = shares["rumble"] > (0.45 if kind == "ambience" else 0.35) or (summ["centroid"] < 110 and shares["rumble"] > 0.3)
    if rumbly:
        findings.append(_f("sub_rumble", "problem" if (shares["rumble"] > 0.6 or summ["centroid"] < 100) else "note", "estimated",
                           "sound_design" if kind == "ambience" else "mix",
                           f"{shares['rumble']:.0%} of the energy is below 80 Hz (centre {summ['centroid']:.0f} Hz): a rumble, not a place",
                           ([{"action": "regenerate", "kind": "regenerate", "params": {"low_cut": 90}, "why": "nothing but rumble: there is no place under it for a filter to reveal"}]
                            if shares["rumble"] > 0.9 else
                            [{"action": "highpass", "kind": "dsp", "params": {"hz": 90}, "why": "take the rumble out of the bus"},
                             {"action": "relayer", "kind": "layer", "params": {"low_cut": 90}, "why": "re-render the beds with the bus low-cut"}])))  # fmt: skip
    if shares["mud"] > 0.62 and kind == "music" and summ["bandwidth"] > 300:
        findings.append(_f("muddy", "problem", "estimated", "mix", f"{shares['mud']:.0%} of the energy sits in 200–500 Hz",
                           [{"action": "peak_eq", "kind": "dsp", "params": {"hz": 300, "q": 1.0, "gain_db": -3}, "why": "clear the low-mids"},
                            {"action": "recompose", "kind": "composition", "params": {"instrument_swap": "brighter"}, "why": "voicing sits too low"}]))  # fmt: skip
    harsh_centroid = summ["centroid_p90"] > 3500
    if shares["harsh"] > 0.4 and harsh_centroid and not sfx:
        findings.append(_f("harsh", "problem", "estimated", "mix", f"{shares['harsh']:.0%} of the energy in 2–5 kHz with the centre reaching {summ['centroid_p90']} Hz: fatiguing",
                           [{"action": "peak_eq", "kind": "dsp", "params": {"hz": 3200, "q": 1.2, "gain_db": -3}, "why": "soften the presence region"},
                            {"action": "lowpass", "kind": "dsp", "params": {"hz": 6000}, "why": "stronger, for a bed"}]))  # fmt: skip
    if kind == "ambience" and shares["hiss"] > 0.12:
        findings.append(_f("hissy", "problem", "estimated", "sound_design", f"{shares['hiss']:.0%} of the energy above 8 kHz",
                           [{"action": "lowpass", "kind": "dsp", "params": {"hz": 7000}, "why": "no place hisses that much"},
                            {"action": "relayer", "kind": "layer", "params": {"high_cut": 6000}, "why": "re-render with the bus high-cut"}]))  # fmt: skip
    if shares["dialogue"] > ex["speech_band_share_max"] and not sfx:
        findings.append(_f("in_dialogue_band", "problem", "estimated", "mix",
                           f"{shares['dialogue']:.0%} of the energy sits in the 1–4 kHz band where reading attention lives (limit {ex['speech_band_share_max']:.0%})",
                           [{"action": "dialogue_dip", "kind": "dsp", "params": {"db": -4}, "why": "a broad 1–4 kHz dip under the text"},
                            {"action": "recompose", "kind": "composition", "params": {"warmth": +0.4, "melody": -0.3}, "why": "write it warmer and lower"} if kind == "music" else
                            {"action": "relayer", "kind": "layer", "params": {"high_cut": 5000, "level_scale": -2}, "why": "darker beds"}]))  # fmt: skip
    lo, hi = ex["centroid_hz"]
    if direction and summ["centroid"] > hi:
        findings.append(_f("too_bright", "problem", "estimated", "mix", f"spectral centre {summ['centroid']:.0f} Hz, above the {hi:.0f} Hz the direction implies",
                           [{"action": "recompose", "kind": "composition", "params": {"warmth": +0.4}, "why": "roll the top off the instruments"} if kind == "music" else
                            {"action": "lowpass", "kind": "dsp", "params": {"hz": 5000}, "why": "darken the bed"}]))
    elif direction and summ["centroid"] < lo:
        findings.append(_f("too_dull", "problem", "estimated", "mix", f"spectral centre {summ['centroid']:.0f} Hz, below the {lo:.0f} Hz the direction implies",
                           [{"action": "recompose", "kind": "composition", "params": {"warmth": -0.3, "instrument_swap": "brighter"}, "why": "brighter voicing"} if kind == "music" else
                            {"action": "highpass", "kind": "dsp", "params": {"hz": 120}, "why": "let the air through"}]))

    # ---------------------------------------------------------------- music: structure and development
    m = report.get("music") or {}
    if kind == "music" and m:
        rate = m["events_per_s"]
        lo_e, hi_e = ex["onset_rate"]
        if direction and rate > hi_e:
            findings.append(_f("too_busy", "problem", "estimated", "composition", f"{rate} events/s; the direction's energy implies at most {hi_e:.1f}",
                               [{"action": "recompose", "kind": "composition", "params": {"energy": -0.15, "percussion": "light", "melody": -0.3}, "why": "thin the arrangement"}]))
        elif direction and rate < lo_e:
            findings.append(_f("too_static", "problem", "estimated", "composition", f"{rate} events/s; the direction's energy implies at least {lo_e:.1f}",
                               [{"action": "recompose", "kind": "composition", "params": {"energy": +0.15, "melody": +0.3}, "why": "give it something to follow"}]))
        cv = m.get("event_regularity_cv")
        if cv is not None and cv < 0.05 and rate > 1.0:
            findings.append(_f("mechanical", "problem", "estimated", "synthesis", f"events arrive on an exact grid (timing CV {cv}) with no velocity life",
                               [{"action": "humanize", "kind": "composition", "params": {"timing_ms": 15, "velocity": 0.2}, "why": "performed timing and dynamics"}]))
        s = m["structure"]
        hc = m["harmony"]
        rep = s["repetition_index"]
        # approved loops measure 0.25–0.9 here (a chord cycle repeats by design), so repetition is a problem only
        # when nothing else moves either; above 0.5 it is worth a note
        if rep > 0.5 and dur > 20:
            cause = "composition"
            why = []
            if hc["changes_per_min"] < 4:
                why.append(f"harmony barely moves ({hc['changes_per_min']} changes/min)")
            if s.get("sections", 1) <= 2:
                why.append("one section repeats")
            if m["density"]["quarters"] and max(m["density"]["quarters"]) - min(m["density"]["quarters"]) < 0.05:
                why.append("the arrangement never thins or fills")
            serious = rep > 0.6 and hc["changes_per_min"] < 3
            findings.append(_f("repetitive", "problem" if serious else "note", "estimated", cause,
                               f"self-similarity {rep} at a {s['best_lag_s']} s lag" + (": " + "; ".join(why) if why else "") +
                               ("" if serious else " (a loop repeats by design; a listener decides whether this one wears)"),
                               [{"action": "recompose", "kind": "composition", "params": {"arc": "rise_fall", "reseed": "melody"}, "why": "a second phrase and a dynamic arc"},
                                {"action": "variation", "kind": "composition", "params": {"section": "B", "instrument_swap": "counter"}, "why": "change orchestration in the later section"}]))  # fmt: skip
        arc = m["energy_arc"]
        if arc["shape"] == "flat" and dur > 30 and t["loudness"]["lra"] < 1.5:
            findings.append(_f("flat_dynamics", "problem", "estimated", "composition", f"loudness range {t['loudness']['lra']} LU over {dur:.0f} s: nothing rises or falls",
                               [{"action": "recompose", "kind": "composition", "params": {"arc": "rise_fall"}, "why": "shape the sections"}]))
        if m["key"]["tonal"] and hc["changes_per_min"] < 2 and dur > 30 and not (direction and "drone" in str(direction.get("genre", ""))):
            findings.append(_f("harmonic_stasis", "note", "estimated", "composition", f"{hc['changes_per_min']} harmonic changes per minute: a drone unless that is the intent",
                               [{"action": "recompose", "kind": "composition", "params": {"reseed": "harmony"}, "why": "a progression that moves"}]))
        if m["tempo"]["bpm"] is None and sp["transients"]["percussive_share"] > 0.3:
            findings.append(_f("tempo_unclear", "note", "estimated", "composition", "percussive content with no confident pulse",
                               [{"action": "recompose", "kind": "composition", "params": {"percussion": "light"}, "why": "a steadier pattern or none"}]))
        if summ["rolloff"] < 3500 and sp["transients"]["attack_db"] > 20 and sp["bands"]["tilt_db_per_octave"] < -5:
            findings.append(_f("dated_palette", "note", "listen", "method",
                               f"narrow bandwidth (rolloff {summ['rolloff']:.0f} Hz), hard attacks ({sp['transients']['attack_db']} dB) and a steep tilt "
                               f"({sp['bands']['tilt_db_per_octave']} dB/oct): the signature of a thin sample set; only a listener can call it 'retro'",
                               [{"action": "instrument_rendering", "kind": "method", "params": {"backend": "soundfont", "program": "ensemble"}, "why": "render through fuller samples"},
                                {"action": "recompose", "kind": "composition", "params": {"instrument_swap": "warmer"}, "why": "ensemble voicing instead of solo patches"}]))  # fmt: skip

    # ---------------------------------------------------------------- intent: mood against tonality and pace (an estimate at best)
    if kind == "music" and m and direction and direction.get("mood"):
        mood = str(direction.get("mood", "")).lower()
        key = m["key"]
        bpm = m["tempo"]["bpm"]
        dark_words = ("sad", "grief", "dread", "tense", "dark", "melanchol", "mourn", "loss", "fear", "eerie")
        bright_words = ("happy", "playful", "heroic", "triumph", "joy", "cheer", "bright", "hope")
        if any(w in mood for w in dark_words) and key["name"] and "major" in key["name"] and key["confidence"] > 0.75 and bpm and bpm > 110:
            findings.append(_f("mood_mismatch", "problem", "listen", "emotion",
                               f"the direction asks for '{mood}' but the piece reads as {key['name']} at {bpm} BPM: brisk and bright on paper",
                               [{"action": "recompose", "kind": "composition", "params": {"mood": mood, "tempo": -20}, "why": "mode and pace carry most of the mood; only a listener can confirm"}]))
        elif any(w in mood for w in bright_words) and key["name"] and "minor" in key["name"] and key["confidence"] > 0.75 and m["events_per_s"] < 0.5:
            findings.append(_f("mood_mismatch", "problem", "listen", "emotion",
                               f"the direction asks for '{mood}' but the piece reads as {key['name']} with {m['events_per_s']} events/s: slow and dark on paper",
                               [{"action": "recompose", "kind": "composition", "params": {"mood": mood, "energy": +0.2}, "why": "mode and pace carry most of the mood; only a listener can confirm"}]))

    # ---------------------------------------------------------------- ambience: scene plausibility
    if kind == "ambience" and m:
        rate = m["events_per_s"]
        cv = m.get("event_regularity_cv")
        if direction and rate > ex["onset_rate"][1]:
            findings.append(_f("too_busy", "problem", "estimated", "sound_design", f"{rate} events/s against an activity of {direction.get('activity', 0.4)}",
                               [{"action": "relayer", "kind": "layer", "params": {"event_rate_scale": 0.6, "intensity": -0.15}, "why": "fewer, quieter events"}]))
        if cv is not None and cv < ex.get("event_cv_min", 0.45) and rate > 0.5:
            findings.append(_f("mechanical_events", "problem", "estimated", "sound_design", f"events arrive on a near-regular grid (CV {cv})",
                               [{"action": "relayer", "kind": "layer", "params": {"reseed": True, "event_rate_scale": 0.8}, "why": "natural (heavy-tailed) spacing"}]))
        if t["loudness"]["lra"] < 0.5 and rate < 0.2 and dur > 20:
            findings.append(_f("static_bed", "note", "estimated", "sound_design", "nothing changes over the loop: level range under 0.5 LU and no events",
                               [{"action": "relayer", "kind": "layer", "params": {"evolve": +0.5}, "why": "slow level breathing of the beds"}]))

    # ---------------------------------------------------------------- voices where none belong
    speech = report.get("speech") or {}
    if speech and kind in ("ambience", "music"):
        allowed = _brief_allows_voices(brief, direction)
        verdict = speech.get("verdict")
        fires = verdict == "confirmed" or (verdict == "suspected" and kind == "ambience")  # the DSP cues alone misread melodies
        if fires and not allowed:
            where = [{"start_s": s["start_s"], "end_s": s["end_s"], "score": s["score"]} for s in speech["segments"] if s["voice_like"]][:12]
            basis = speech.get("verdict_basis", "")
            vad = speech.get("vad") or {}
            ev = (f"voice-like content in {speech['voice_like_share']:.0%} of the seconds (strongest {speech['max_score']:.2f}); {basis}"
                  + (f"; VAD speech share {vad.get('speech_share')}" if vad.get("available") else ""))
            forbids = brief is not None or direction is not None  # with no brief the intent is unknown: flag, don't condemn
            findings.append(_f("voice_like", ("hard" if forbids else "problem") if verdict == "confirmed" else ("problem" if forbids else "note"),
                               "measured" if verdict == "confirmed" else "estimated", "source",
                               ev, [{"action": "drop_layer", "kind": "layer", "params": {"bed": "crowd_murmur"}, "why": "the synthesized-talker bed is the usual source"},
                                    {"action": "replace_segment", "kind": "dsp", "params": {"segments": where}, "why": "patch the seconds with clean material from the same file"},
                                    {"action": "regenerate", "kind": "regenerate", "params": {"avoid": ["crowd_murmur"]}, "why": "if it is everywhere, the layer choice was wrong"}],
                               where=where, message="something in this sound will be heard as people talking, and the brief has no people in it"))  # fmt: skip
        elif verdict == "confirmed" and allowed:
            findings.append(_f("voices_present", "note", "measured", "source", f"voice-like content in {speech['voice_like_share']:.0%} of the seconds, as the brief allows", []))

    # ---------------------------------------------------------------- semantic expectation from the brief (estimated)
    ev = report.get("events") or {}
    if brief and ev:
        got = {e["label"] for e in ev.get("summary", []) if e["share"] >= 0.1}
        text = brief.lower()
        words = set(re.findall(r"[a-z]+", text))  # whole words: "window" is not wind, "drain" is not rain
        wants = {"rain_like": bool(words & {"rain", "raining", "rainy", "downpour", "drizzle"}), "wind": bool(words & {"wind", "windy", "breeze", "gust", "gusts"}),
                 "water_drips": bool(words & {"drip", "drips", "dripping"}), "footsteps": bool(words & {"footstep", "footsteps", "boots"}), "music": kind == "music"}  # fmt: skip
        missing = [k for k, w in wants.items() if w and k not in got]
        if missing:
            findings.append(_f("expected_sound_missing", "note", "estimated", "sound_design",
                               f"the brief asks for {', '.join(missing)} and no matching signature was found (heuristic signatures; listen before acting)",
                               [{"action": "relayer", "kind": "layer", "params": {"add": missing}, "why": "add the layer the brief names"}]))
    order = {"hard": 0, "problem": 1, "note": 2}
    findings.sort(key=lambda f: (order[f["severity"]], 0 if f["confidence"] == "measured" else 1))
    return findings


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    hard = [f for f in findings if f["severity"] == "hard"]
    problems = [f for f in findings if f["severity"] == "problem"]
    notes = [f for f in findings if f["severity"] == "note"]
    causes: dict[str, int] = {}
    for f in findings:
        if f["severity"] != "note":
            causes[f.get("cause", "unknown")] = causes.get(f.get("cause", "unknown"), 0) + 1
    return {"usable": not hard, "hard": [f["code"] for f in hard], "problems": [f["code"] for f in problems],
            "notes": [f["code"] for f in notes], "causes": causes,
            "needs_listening": [f["code"] for f in findings if f.get("confidence") == "listen"]}  # fmt: skip
