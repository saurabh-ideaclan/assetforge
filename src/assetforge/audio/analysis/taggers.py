"""Sound-event taggers (AudioSet-style): an adapter slot, empty until a model with a verified licence is
installed. `tag(path)` returns `{"available": False, "reason": …}` or the model's labels with confidences.

Candidates and why none is installed by default (docs/narrative/17-audio-analysis-tooling.md): YAMNet
(Apache-2.0 code; the ONNX conversions on the web are third-party, provenance unverified), PANNs
MobileNetV1 (CC-BY-4.0 weights, needs torch), CLAP (Apache-2.0, 600 MB + torch). The adapter looks for
`$ASSETFORGE_HOME/models/tagger.onnx` + `tagger.json` (class names, input spec, licence) so an owner who has
verified a model can drop it in without a code change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assetforge.config import resolve_home


def spec(home: Path | None = None) -> dict[str, Any] | None:
    d = (home or resolve_home()) / "models"
    if (d / "tagger.onnx").exists() and (d / "tagger.json").exists():
        try:
            return {**json.loads((d / "tagger.json").read_text()), "path": str(d / "tagger.onnx")}
        except ValueError:
            return None
    return None


def tag(path, *, home: Path | None = None) -> dict[str, Any]:
    s = spec(home)
    if s is None:
        return {"available": False, "reason": "no sound-event tagger installed (models/tagger.onnx + tagger.json)"}
    try:
        import onnxruntime as ort  # noqa: F401
    except ImportError:
        return {"available": False, "reason": "onnxruntime not installed"}
    # The input contract is declared by tagger.json; the only one implemented is YAMNet-style:
    # 16 kHz mono waveform in, (frames × classes) scores out, 0.48 s frames at 0.96 s... Keep it explicit.
    if s.get("input") != "waveform_16k":
        return {"available": False, "reason": f"unsupported tagger input contract {s.get('input')!r}"}
    import numpy as np

    from assetforge.audio.analysis.speech import _resample
    from assetforge.audio.codecs import read_audio

    x, sr = read_audio(path, always_2d=False)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    x16 = _resample(np.asarray(mono, dtype=np.float64), sr).astype(np.float32)
    sess = ort.InferenceSession(s["path"], providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    scores = sess.run(None, {name: x16})[0]
    classes = s.get("classes") or []
    mean = scores.mean(axis=0) if scores.ndim == 2 else scores
    top = np.argsort(mean)[::-1][:10]
    return {"available": True, "model": s.get("name", "tagger"), "licence": s.get("licence"),
            "labels": [{"label": classes[i] if i < len(classes) else str(i), "confidence": round(float(mean[i]), 3)} for i in top]}  # fmt: skip
