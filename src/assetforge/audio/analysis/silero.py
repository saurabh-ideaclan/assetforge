"""Optional Silero VAD (ONNX) corroboration for the speech-likeness cues.

The model is MIT-licensed (snakers4/silero-vad, `src/silero_vad/data/silero_vad.onnx`, v5) and runs through
`onnxruntime` on the CPU in well under real time. It is installed on request with a pinned SHA-256
(`assetforge audio models install silero-vad --yes`) into `$ASSETFORGE_HOME/models/`; nothing here downloads
on its own, and when the model or onnxruntime is missing `detect` says so instead of guessing. A VAD detects
speech-*like* modulation and voicing, so its output is evidence to combine, not a verdict (see `speech.py`).
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

from assetforge.config import resolve_home

MODELS = {
    "silero-vad": {
        "file": "silero_vad.onnx",
        "url": "https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx",
        "sha256": "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f",  # v5.1.2, verified 2026-11-11
        "licence": "MIT (https://github.com/snakers4/silero-vad/blob/master/LICENSE)",
        "size_mb": 2.3,
    },
}


def models_dir(home: Path | None = None) -> Path:
    return (home or resolve_home()) / "models"


def model_path(name: str = "silero-vad", home: Path | None = None) -> Path | None:
    p = models_dir(home) / MODELS[name]["file"]
    return p if p.exists() else None


def available(home: Path | None = None) -> bool:
    if model_path(home=home) is None:
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def install(name: str = "silero-vad", *, home: Path | None = None, source: Path | None = None) -> dict[str, Any]:
    """Copy (`source`) or download the model, record its SHA-256 and licence in models.json."""
    import json

    spec = MODELS[name]
    target_dir = models_dir(home)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / spec["file"]
    tmp = target.with_suffix(".part")
    if source is not None:
        shutil.copy(source, tmp)
    else:
        with urllib.request.urlopen(spec["url"], timeout=120) as resp, open(tmp, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if spec["sha256"] and digest != spec["sha256"]:
        tmp.unlink()
        raise ValueError(f"{name}: SHA-256 mismatch ({digest[:12]}…), refusing to install")
    tmp.replace(target)
    reg = target_dir / "models.json"
    data = json.loads(reg.read_text()) if reg.exists() else {}
    data[name] = {"file": spec["file"], "sha256": digest, "licence": spec["licence"], "source": str(source) if source else spec["url"]}
    reg.write_text(json.dumps(data, indent=1))
    return data[name]


_SESSION: dict[str, Any] = {}


def _session(path: Path):
    import onnxruntime as ort

    key = str(path)
    if key not in _SESSION:
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        _SESSION[key] = ort.InferenceSession(key, opts, providers=["CPUExecutionProvider"])
    return _SESSION[key]


def detect(x16k: np.ndarray, sr: int = 16000, *, threshold: float = 0.5, home: Path | None = None) -> dict[str, Any]:
    """Speech probability per 32 ms window (16 kHz mono in -1..1) → speech share and segments."""
    path = model_path(home=home)
    if path is None or sr != 16000:
        return {"available": False, "reason": "model not installed" if path is None else "needs 16 kHz input"}
    try:
        sess = _session(path)
    except ImportError:
        return {"available": False, "reason": "onnxruntime not installed"}
    x = np.asarray(x16k, dtype=np.float32)
    win, ctx = 512, 64
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros(ctx, dtype=np.float32)
    probs: list[float] = []
    names = {i.name for i in sess.get_inputs()}
    for i in range(0, len(x) - win + 1, win):
        chunk = x[i:i + win]
        inp = np.concatenate([context, chunk])[None, :]
        feeds: dict[str, Any] = {"input": inp, "state": state, "sr": np.array(16000, dtype=np.int64)}
        feeds = {k: v for k, v in feeds.items() if k in names}
        outs = sess.run(None, feeds)
        probs.append(float(outs[0].reshape(-1)[0]))
        state = outs[1]
        context = chunk[-ctx:]
    p = np.array(probs)
    speech = p >= threshold
    segments = []
    start = None
    for i, s in enumerate(speech):
        if s and start is None:
            start = i
        elif not s and start is not None:
            if (i - start) * win / sr >= 0.25:
                segments.append({"start_s": round(start * win / sr, 2), "end_s": round(i * win / sr, 2),
                                 "mean_prob": round(float(p[start:i].mean()), 2)})  # fmt: skip
            start = None
    if start is not None and (len(speech) - start) * win / sr >= 0.25:
        segments.append({"start_s": round(start * win / sr, 2), "end_s": round(len(speech) * win / sr, 2), "mean_prob": round(float(p[start:].mean()), 2)})
    return {"available": True, "model": "silero-vad v5 (onnx)", "speech_share": round(float(speech.mean()), 3) if len(p) else 0.0,
            "max_prob": round(float(p.max()), 3) if len(p) else 0.0, "segments": segments[:20]}  # fmt: skip
