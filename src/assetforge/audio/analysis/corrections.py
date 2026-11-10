"""The correction engine: from findings to a plan, from a plan to a new variant, and the re-evaluation that
says whether the target problem improved and what else moved.

    plan = plan_corrections(findings, kind=…)         # {"dsp": [ops…], "composition": {…}, "layer": {…}, "regenerate": …}
    result = correct_asset(lib, asset_id, plan, …)    # applies the DSP chain as a derivative variant (lineage kept)
    verdict = compare(before, after, target_codes)    # improved / unchanged / worse, plus regressions

Rules the owner set: a precise correction beats a regeneration; DSP cannot fix a composition; a voice-like
layer is removed or patched, never hidden behind EQ; every correction is re-measured; the number of automatic
rounds is bounded by the caller. The DSP chain is one `process_audio_asset` call, so the variant carries its
ops, its loudness and its parent in the catalog.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from assetforge.audio.analysis import analyze_file, to_json
from assetforge.audio.analysis.diagnose import diagnose, summarize

# findings whose first correction is DSP, in a sensible chain order (filters before dynamics before loop)
DSP_ORDER = ["dc_remove", "declick", "highpass", "lowpass", "peak_eq", "shelf", "dialogue_dip", "polarity", "narrow", "balance", "compress", "expand",
             "replace_segment", "reloop", "normalize", "gain"]  # fmt: skip


def plan_corrections(findings: list[dict[str, Any]], *, kind: str, prefer: str = "precise",
                     allow_regenerate: bool = True) -> dict[str, Any]:  # fmt: skip
    """Pick one correction per finding, grouped by how it is applied. `prefer="precise"` takes a DSP or
    layer fix over a regeneration whenever one is offered; `prefer="regenerate"` does the opposite."""
    dsp: list[dict[str, Any]] = []
    composition: dict[str, Any] = {}
    layer: dict[str, Any] = {}
    regenerate: dict[str, Any] | None = None
    method: list[dict[str, Any]] = []
    reasons: list[str] = []
    for f in findings:
        if f["severity"] == "note" and f["code"] not in ("harmonic_stasis",):
            continue
        options = f["corrections"]
        if not options:
            continue
        if prefer == "precise":
            ranked = sorted(options, key=lambda c: {"dsp": 0, "layer": 1, "composition": 2, "regenerate": 3, "method": 4}[c["kind"]])
        else:
            ranked = sorted(options, key=lambda c: {"regenerate": 0, "composition": 1, "layer": 2, "dsp": 3, "method": 4}[c["kind"]])
        chosen = ranked[0]
        if chosen["kind"] == "regenerate" and not allow_regenerate:
            chosen = next((c for c in ranked if c["kind"] != "regenerate"), None)
            if chosen is None:
                continue
        if chosen["kind"] == "dsp":
            if chosen["action"] == "replace_segment":
                for seg in chosen["params"].get("segments", []):
                    dsp.append({"op": "replace_segment", "start_s": seg["start_s"], "end_s": seg["end_s"], "donor_start_s": None})
            else:
                dsp.append({"op": chosen["action"], **{k: v for k, v in chosen["params"].items()}})
        elif chosen["kind"] == "composition":
            for k, v in chosen["params"].items():
                if isinstance(v, (int, float)) and isinstance(composition.get(k), (int, float)):
                    composition[k] = composition[k] + v
                else:
                    composition[k] = v
        elif chosen["kind"] == "layer":
            layer.update(chosen["params"])
            if chosen["action"] == "drop_layer":
                layer.setdefault("drop", []).append(chosen["params"].get("bed"))
        elif chosen["kind"] == "regenerate":
            regenerate = {**(regenerate or {}), **chosen["params"]}
        elif chosen["kind"] == "method":
            method.append(chosen)
        reasons.append(f"{f['code']} → {chosen['action']} ({chosen['why']})")
    dsp.sort(key=lambda o: DSP_ORDER.index(o["op"]) if o["op"] in DSP_ORDER else 99)
    # a LUFS normalise at the end keeps the level honest after filters and dynamics changed it
    if dsp and not any(o["op"] == "normalize" for o in dsp):
        from assetforge.audio.master import LOUDNESS_TARGETS

        dsp.append({"op": "normalize", "mode": "lufs", "target": LOUDNESS_TARGETS.get(kind, -16.0), "ceiling_db": -1.5})
    return {"dsp": dsp, "composition": composition, "layer": layer, "regenerate": regenerate, "method": method, "reasons": reasons}


def fill_donors(plan: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """`replace_segment` needs a donor: the cleanest second (lowest speech score, not silent) at least two
    seconds away from the patch, chosen from the report's speech timeline."""
    speech = (report.get("speech") or {}).get("segments", [])
    if not speech:
        return plan
    clean = sorted(speech, key=lambda s: s["score"])
    for op in plan["dsp"]:
        if op.get("op") == "replace_segment" and op.get("donor_start_s") is None:
            length = op["end_s"] - op["start_s"]
            for s in clean:
                if abs(s["start_s"] - op["start_s"]) >= 2.0 and s["end_s"] - s["start_s"] >= length:
                    op["donor_start_s"] = s["start_s"]
                    break
    plan["dsp"] = [op for op in plan["dsp"] if not (op.get("op") == "replace_segment" and op.get("donor_start_s") is None)]
    return plan


def correct_asset(lib, asset_id: str, plan: dict[str, Any], *, label: str | None = None, variant: str | None = None) -> dict[str, Any] | None:
    """Apply the plan's DSP chain to the asset as a new variant (lineage, ops and loudness recorded). Returns
    the derivative, or None when the plan has no DSP step (composition / layer / regeneration are the
    caller's: production.refine, the ambience spec, or a new render)."""
    from assetforge.audio.process import process_audio_asset

    if not plan.get("dsp"):
        return None
    ops = [{k: v for k, v in op.items() if v is not None} for op in plan["dsp"]]
    d = process_audio_asset(lib, asset_id, ops, variant=variant, label=label or "corrected")
    return {"asset_id": d.asset_id, "variant_id": d.variant_id, "label": d.label, "path": d.path, "ops": ops, "reused": d.reused}


def compare(before: dict[str, Any], after: dict[str, Any], target_codes: list[str]) -> dict[str, Any]:
    """Did the targeted problems go away, and did anything else get worse? Works on diagnosed reports
    (those with `findings`)."""
    b = {f["code"]: f for f in before.get("findings", []) if f["severity"] != "note"}
    a = {f["code"]: f for f in after.get("findings", []) if f["severity"] != "note"}
    fixed = [c for c in target_codes if c in b and c not in a]
    remaining = [c for c in target_codes if c in a]
    regressions = [c for c in a if c not in b]
    verdict = "improved" if fixed and not regressions else "mixed" if fixed else "unchanged" if not regressions else "worse"
    return {"verdict": verdict, "fixed": fixed, "remaining": remaining, "regressions": regressions,
            "before": summarize(before.get("findings", [])), "after": summarize(after.get("findings", []))}  # fmt: skip


def correct_file(path: Path, out_path: Path, findings: list[dict[str, Any]], report: dict[str, Any], *, kind: str,
                 brief: str | None = None, direction: dict[str, Any] | None = None, loop: bool | None = None) -> dict[str, Any]:  # fmt: skip
    """The file-level loop used by the harness and the CLI: plan → apply to a WAV → re-analyse → compare."""
    from assetforge.audio.process import apply_ops, encode, load

    plan = fill_donors(plan_corrections(findings, kind=kind), report)
    ops = [{k: v for k, v in op.items() if v is not None} for op in plan["dsp"]]
    if not ops:
        return {"plan": plan, "applied": False, "reason": "no DSP step in the plan (composition, layer or regeneration needed)"}
    audio, enc = apply_ops(load(path), ops)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    encode(audio, out_path.with_suffix(""), {"format": "wav"})
    after_report = analyze_file(out_path, kind=kind)
    after_findings = diagnose(after_report, kind=kind, brief=brief, direction=direction, loop=loop)
    targets = [f["code"] for f in findings if f["severity"] != "note"]
    before = {"findings": findings}
    after = {"findings": after_findings}
    return {"plan": plan, "applied": True, "ops": ops, "output": str(out_path), "after": to_json(after_report) | {"findings": after_findings},
            "comparison": compare(before, after, targets)}  # fmt: skip
