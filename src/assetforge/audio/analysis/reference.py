"""Reference audio intelligence: what the owner's approved sound *measures* like, as a reusable profile, and
the preference record that the production loop learns from.

A profile is the per-feature mean and spread over a set of files (the project's approved music, say): a
candidate is then placed by z-scores, so "far from the identity" names the feature (brighter, busier, more
in the reading band…) instead of a single distance. Profiles are stored with the sound identity in
`$ASSETFORGE_HOME/audio_style/<project>.json` (next to `production.add_reference`'s entries) and feed the
direction prompt as measured guidance. Nothing is copied from a reference; only its measured character.

Preference records: every approve / reject with the brief, the candidate's findings and the owner's words,
appended to the same file. `rank()` uses them as a nearest-approved heuristic until there are enough to
fit anything (learning-to-rank is for when the record has hundreds of rows, not tens).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from assetforge.audio.analysis import analyze_file

FEATURES = {
    # name → (how to read it from a report, scale for a one-unit "noticeable" difference)
    "lufs": (lambda r: r["technical"]["loudness"]["integrated"], 2.0),
    "lra_lu": (lambda r: r["technical"]["loudness"]["lra"], 2.0),
    "crest_db": (lambda r: r["technical"]["crest_db"], 3.0),
    "centroid_log": (lambda r: float(np.log2(max(r["spectral"]["summary"]["centroid"], 1))), 0.5),
    "rolloff_log": (lambda r: float(np.log2(max(r["spectral"]["summary"]["rolloff"], 1))), 0.5),
    "flatness": (lambda r: r["spectral"]["summary"]["flatness"], 0.1),
    "tilt": (lambda r: r["spectral"]["bands"]["tilt_db_per_octave"], 2.0),
    "dialogue_share": (lambda r: r["spectral"]["bands"]["shares"]["dialogue"], 0.1),
    "rumble_share": (lambda r: r["spectral"]["bands"]["shares"]["rumble"], 0.1),
    "harsh_share": (lambda r: r["spectral"]["bands"]["shares"]["harsh"], 0.1),
    "percussive_share": (lambda r: r["spectral"]["transients"]["percussive_share"], 0.15),
    "events_per_s": (lambda r: (r.get("music") or {}).get("events_per_s", 0.0), 1.0),
    "repetition": (lambda r: ((r.get("music") or {}).get("structure") or {}).get("repetition_index", 0.0), 0.15),
    "harmonic_changes": (lambda r: ((r.get("music") or {}).get("harmony") or {}).get("changes_per_min", 0.0), 6.0),
    "occupancy": (lambda r: ((r.get("music") or {}).get("density") or {}).get("occupancy", 0.0), 0.15),
    "arc_range_lu": (lambda r: ((r.get("music") or {}).get("energy_arc") or {}).get("range_lu", 0.0), 2.0),
    "stereo_width": (lambda r: r["technical"]["stereo"]["width"], 0.3),
}
WORDS = {"centroid_log": ("darker", "brighter"), "rolloff_log": ("duller", "more open"), "flatness": ("more tonal", "noisier"),
         "tilt": ("more top-light", "more top-heavy"), "dialogue_share": ("clearer of the reading band", "more in the reading band"),
         "rumble_share": ("less rumble", "more rumble"), "harsh_share": ("softer", "harsher"), "percussive_share": ("smoother", "more percussive"),
         "events_per_s": ("sparser", "busier"), "repetition": ("more varied", "more repetitive"), "harmonic_changes": ("more static harmony", "more harmonic motion"),
         "occupancy": ("thinner", "denser"), "arc_range_lu": ("flatter", "more dynamic"), "stereo_width": ("narrower", "wider"),
         "lufs": ("quieter", "louder"), "lra_lu": ("more even", "more dynamic in level"), "crest_db": ("more compressed", "more peaky")}  # fmt: skip


def features_of(report: dict[str, Any]) -> dict[str, float]:
    if (report.get("spectral") or {}).get("silent"):
        return {}
    out = {}
    for name, (read, _) in FEATURES.items():
        try:
            out[name] = round(float(read(report)), 4)
        except (KeyError, TypeError):
            continue
    return out


def profile(reports: list[dict[str, Any]], *, name: str = "", note: str = "") -> dict[str, Any]:
    """Mean and spread per feature over the reports (spread floored at the feature's noticeable scale / 2)."""
    rows = [features_of(r) for r in reports]
    rows = [r for r in rows if r]
    stats: dict[str, Any] = {}
    for fname, (_, scale) in FEATURES.items():
        vals = np.array([r[fname] for r in rows if fname in r], dtype=float)
        if vals.size == 0:
            continue
        stats[fname] = {"mean": round(float(vals.mean()), 4), "sd": round(float(max(vals.std(), scale / 2)), 4), "n": int(vals.size),
                        "min": round(float(vals.min()), 4), "max": round(float(vals.max()), 4)}  # fmt: skip
    return {"name": name, "note": note, "n": len(rows), "features": stats, "made_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def profile_files(paths: list[Path], *, kind: str | None = None, name: str = "", note: str = "") -> dict[str, Any]:
    return profile([analyze_file(p, kind=kind, with_vad=False) for p in paths], name=name, note=note)


def place(report: dict[str, Any], prof: dict[str, Any]) -> dict[str, Any]:
    """Where a candidate sits against a profile: z per feature, the notable departures in words, and a
    distance (RMS of the clipped z's) that is comparable across candidates of the same profile."""
    f = features_of(report)
    zs: dict[str, float] = {}
    for name, st in prof.get("features", {}).items():
        if name in f:
            zs[name] = round(float((f[name] - st["mean"]) / st["sd"]), 2)
    # with a handful of references, |z| ≥ 1.5 happens by chance several times over 17 features: ask for 2.5 and
    # report at most three, and only one of the brightness family (centroid, rolloff and tilt move together)
    family = {"rolloff_log": "bright", "tilt": "bright", "centroid_log": "bright"}
    seen_family: set[str] = set()
    notable = []
    ordered = sorted(((abs(z), name, z) for name, z in zs.items() if abs(z) >= 2.5), key=lambda t: (t[1] != "centroid_log", -t[0]))
    for az, name, z in ordered:
        fam = family.get(name)
        if fam and fam in seen_family:
            continue
        if fam:
            seen_family.add(fam)
        notable.append((az, name, z))
        if len(notable) == 3:
            break
    words = [f"{WORDS[name][1] if z > 0 else WORDS[name][0]} than the identity (z {z:+.1f})" for _, name, z in notable if name in WORDS]
    dist = round(float(np.sqrt(np.mean([min(abs(z), 4.0) ** 2 for z in zs.values()]))), 3) if zs else None
    return {"z": zs, "notable": words, "distance": dist, "features": f}


def describe(prof: dict[str, Any]) -> list[str]:
    """The profile in production words: the measured character future directions should aim for."""
    st = prof.get("features", {})
    if not st:
        return []
    out = []
    c = 2 ** st["centroid_log"]["mean"] if "centroid_log" in st else None
    if c:
        out.append(f"spectral centre around {c:.0f} Hz ({'dark' if c < 700 else 'warm' if c < 1500 else 'bright'})")
    if "events_per_s" in st:
        out.append(f"about {st['events_per_s']['mean']:.1f} salient events per second")
    if "dialogue_share" in st:
        out.append(f"{st['dialogue_share']['mean']:.0%} of the energy in the 1–4 kHz reading band")
    if "percussive_share" in st:
        out.append(f"percussive share {st['percussive_share']['mean']:.0%}")
    if "repetition" in st:
        out.append(f"self-similarity {st['repetition']['mean']:.2f}")
    if "arc_range_lu" in st:
        out.append(f"energy arc of {st['arc_range_lu']['mean']:.1f} LU")
    if "lra_lu" in st:
        out.append(f"loudness range {st['lra_lu']['mean']:.1f} LU")
    if "stereo_width" in st:
        out.append(f"stereo width {st['stereo_width']['mean']:.2f}")
    return out


# ---------------------------------------------------------------- storage next to the sound identity


def _style_path(home: Path, project: str) -> Path:
    return home / "audio_style" / f"{project}.json"


def load_style(home: Path, project: str) -> dict[str, Any]:
    p = _style_path(home, project)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"project": project, "references": [], "preferences": {}}


def save_style(home: Path, project: str, data: dict[str, Any]) -> None:
    p = _style_path(home, project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(p)  # atomic: production.add_reference writes the same file


def set_profile(home: Path, project: str, kind: str, prof: dict[str, Any]) -> dict[str, Any]:
    data = load_style(home, project)
    data.setdefault("profiles", {})[kind] = prof
    save_style(home, project, data)
    return data


def record_preference(home: Path, project: str, *, asset_id: str, decision: str, kind: str, brief: str = "",
                      findings: list[dict[str, Any]] | None = None, feedback: str = "", features: dict[str, float] | None = None,
                      changes: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Append one approve/reject row: what was asked, what was measured, what the owner said, what changed."""
    data = load_style(home, project)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "asset_id": asset_id, "decision": decision, "kind": kind, "brief": brief[:500],
           "findings": [f["code"] for f in (findings or []) if f["severity"] != "note"], "feedback": feedback[:500],
           "features": features or {}, "changes": changes or []}  # fmt: skip
    data.setdefault("history", []).append(row)
    data["history"] = data["history"][-500:]
    save_style(home, project, data)
    return row


def rank(home: Path, project: str, kind: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order candidates (each with a `report`) by: usable first, then closeness to the approved profile, then
    the nearest-approved heuristic from the preference history (features of approved rows pull, rejected push).
    Returns the candidates with `placement` and `preference_score` added. Honest about thin data: with no
    history the preference score is 0 for everyone."""
    data = load_style(home, project)
    prof = (data.get("profiles") or {}).get(kind)
    hist = [h for h in data.get("history", []) if h.get("kind") == kind and h.get("features")]
    approved = [h["features"] for h in hist if h["decision"] == "approved"]
    rejected = [h["features"] for h in hist if h["decision"] == "rejected"]
    out = []
    for c in candidates:
        f = features_of(c["report"])
        placement = place(c["report"], prof) if prof else None

        def near(rows: list[dict[str, float]]) -> float:
            if not rows or not f:
                return 0.0
            ds = []
            for r in rows:
                common = [k for k in f if k in r]
                if not common:
                    continue
                ds.append(float(np.sqrt(np.mean([((f[k] - r[k]) / FEATURES[k][1]) ** 2 for k in common]))))
            return min(ds) if ds else 0.0

        pref = 0.0
        if approved or rejected:
            da, dr = near(approved), near(rejected)
            pref = round(float((dr - da) / (dr + da + 1e-9)), 3) if (approved and rejected) else round(float(-da), 3) if approved else round(float(dr), 3)
        out.append({**c, "placement": placement, "preference_score": pref})
    usable = lambda c: 0 if (c.get("summary") or {}).get("usable", True) else 1  # noqa: E731
    # one continuous key: distance to the identity, pulled down by the preference score (never a tie-break that
    # float distances would never reach)
    out.sort(key=lambda c: (usable(c), ((c["placement"] or {}).get("distance") or 0.0) - 0.5 * c["preference_score"]))
    return out
