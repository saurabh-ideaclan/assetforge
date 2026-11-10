"""The human-readable reading of a report: what the sound is, how it is built, what seems wrong and what to
do, in sentences that say how sure each statement is. Deterministic text from the numbers: no model writes
it, so it never claims to have heard anything."""

from __future__ import annotations

from typing import Any


def _brightness(c: float) -> str:
    if c < 300:
        return "very dark"
    if c < 700:
        return "dark"
    if c < 1500:
        return "warm"
    if c < 3000:
        return "bright"
    return "very bright"


def _texture(sp: dict[str, Any]) -> str:
    tex = sp.get("texture", "mixed")
    perc = sp["transients"]["percussive_share"]
    if tex == "tonal":
        return "tonal and sustained" if perc < 0.2 else "tonal with clear attacks"
    if tex == "noisy":
        return "noise-like (a texture rather than notes)"
    return "mixed tonal and noise content"


def describe(report: dict[str, Any], findings: list[dict[str, Any]] | None = None) -> dict[str, list[str]]:
    """Sections of short sentences. Each number keeps the unit; estimates say so."""
    t = report["technical"]
    sp = report.get("spectral") or {}
    m = report.get("music") or {}
    speech = report.get("speech") or {}
    ev = report.get("events") or {}
    kind = report.get("kind") or "sound"
    out: dict[str, list[str]] = {}
    ident = [f"A {t['duration_s']:.0f} s {'stereo' if t['channels'] >= 2 else 'mono'} {kind} at {t['loudness']['integrated']} LUFS "
             f"(true peak {t['true_peak_dbtp']} dBTP, crest {t['crest_db']} dB, loudness range {t['loudness']['lra']} LU)."]
    if sp and not sp.get("silent"):
        s = sp["summary"]
        ident.append(f"It is {_brightness(s['centroid'])} (spectral centre {s['centroid']:.0f} Hz, 85 % of the energy below {s['rolloff']:.0f} Hz) and {_texture(sp)}.")
        tilt = sp["bands"]["tilt_db_per_octave"]
        ident.append(f"Spectral tilt {tilt} dB/octave of octave-band energy (pink noise = 0, a balanced mix about -1 to -3)" +
                     (": top-light." if tilt < -5 else ": top-heavy." if tilt > 1.5 else ": within the usual range."))
    out["what_it_is"] = ident
    if m:
        mus = []
        tempo = m["tempo"]
        if tempo["bpm"]:
            mus.append(f"Pulse near {tempo['bpm']} BPM (confidence {tempo['confidence']}).")
        else:
            mus.append("No confident pulse, so tempo is not a meaningful property here.")
        key = m["key"]
        mus.append(f"Key reads as {key['name']} (correlation {key['confidence']})." if key["name"] else
                   ("Tonal, but no key stands out." if key["tonal"] else "Not tonal enough for a key."))
        mus.append(f"{m['events_per_s']} salient events per second" + (f", spaced with regularity CV {m['event_regularity_cv']} (under 0.1 is machine-exact)." if m.get("event_regularity_cv") is not None else "."))
        st = m["structure"]
        mus.append(f"{st.get('sections', 1)} section(s); self-similarity {st['repetition_index']}" + (f" at a {st['best_lag_s']} s lag" if st.get("best_lag_s") else "") +
                   f"; harmony changes {m['harmony']['changes_per_min']} times a minute.")
        arc = m["energy_arc"]
        mus.append(f"Energy arc: {arc['shape']} ({arc['range_lu']} LU between the quietest and loudest quarter).")
        mus.append(f"Arrangement occupies {m['density']['occupancy']:.0%} of the spectrum on average.")
        out["structure"] = mus
    if speech:
        v = speech.get("verdict")
        line = {"confirmed": f"Voice-like content is confirmed by two independent methods in {speech['voice_like_share']:.0%} of the seconds",
                "suspected": f"Voice-like content is suspected (one method fired; strongest second scores {speech['max_score']:.2f})",
                "unlikely": "No voice-like content found"}[v] + f" — {speech.get('verdict_basis', '')}."
        if speech.get("strongest") and v != "unlikely":
            s0 = speech["strongest"][0]
            line += f" Listen at {s0['start_s']}–{s0['end_s']} s."
        out["voices"] = [line]
    if ev:
        heard = ", ".join(f"{e['label']} ({e['share']:.0%} of the time)" for e in ev["summary"][:5])
        out["signatures"] = [f"Acoustic signatures ({ev['method']}; these are patterns, not recognition): {heard}."]
        if ev.get("tagger", {}).get("available"):
            out["signatures"].append("Tagger labels: " + ", ".join(f"{l['label']} {l['confidence']}" for l in ev["tagger"]["labels"][:5]) + ".")
    if findings is not None:
        lines = []
        for f in findings:
            sev = {"hard": "Unusable", "problem": "Problem", "note": "Note"}[f["severity"]]
            conf = {"measured": "measured", "estimated": "estimated", "listen": "needs a listener"}[f["confidence"]]
            fix = f["corrections"][0] if f["corrections"] else None
            where = f" at {', '.join(f'{w['start_s']}–{w['end_s']} s' for w in f['where'][:3])}" if f.get("where") else ""
            lines.append(f"{sev} ({conf}, {f['cause'].replace('_', ' ')}): {f['message']}{where}." +
                         (f" Suggested: {fix['action']} ({fix['why']})." if fix else ""))
        out["findings"] = lines or ["No problems found by measurement. Whether it is right for the scene is a listener's call."]
    return out


def text(report: dict[str, Any], findings: list[dict[str, Any]] | None = None) -> str:
    sections = describe(report, findings)
    titles = {"what_it_is": "What it is", "structure": "Structure", "voices": "Voices", "signatures": "What it contains", "findings": "Findings"}
    return "\n\n".join(f"{titles.get(k, k)}\n" + "\n".join("- " + s for s in v) for k, v in sections.items())
