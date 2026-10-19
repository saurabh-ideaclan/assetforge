"""Deterministic audio finishing: an ordered list of ops applied to a source variant.

Ops are plain data stored on the resulting variant, like the image processor. Normalization
targets loudness (LUFS) by default, so SFX from different sources sit at a consistent perceived
level. A peak ceiling is respected by lowering the gain, never by clipping or limiting.
"""

from __future__ import annotations

import inspect
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile
from scipy.signal import resample_poly

from assetforge.audio.loudness import integrated_loudness
from assetforge.core.catalog import Library
from assetforge.core.derive import Derivative, register_derivative, source_variant
from assetforge.core.models import FileKind
from assetforge.errors import InvalidInputError

PROCESSOR_VERSION = 1
FORMATS = {"wav": ("WAV", "PCM_16", ".wav"), "ogg": ("OGG", "VORBIS", ".ogg"), "mp3": ("MP3", "MPEG_LAYER_III", ".mp3")}

PRESETS: dict[str, list[dict[str, Any]]] = {
    "web-sfx": [
        {"op": "trim_silence"},
        {"op": "channels", "count": 1},
        {"op": "normalize", "mode": "lufs", "target": -16.0, "ceiling_db": -1.0},
        {"op": "fade", "out_ms": 8},
        {"op": "format", "format": "ogg", "quality": 0.6},
    ],
    "web-music": [
        {"op": "normalize", "mode": "lufs", "target": -18.0, "ceiling_db": -1.0},
        {"op": "format", "format": "ogg", "quality": 0.6},
    ],
    "game-wav": [
        {"op": "trim_silence"},
        {"op": "normalize", "mode": "peak", "target": -1.0},
        {"op": "format", "format": "wav"},
    ],
    "loop": [{"op": "loop", "crossfade_ms": 60}, {"op": "format", "format": "ogg", "quality": 0.6}],
}

Audio = tuple[np.ndarray, int]  # (samples shaped (n, channels), sample rate)


def _db(x: float) -> float:
    return 20 * np.log10(x) if x > 0 else -120.0


# ---------------------------------------------------------------------- ops


def op_trim_silence(audio: Audio, threshold_db: float = -50.0, keep_ms: float = 10.0, start: bool = True,
                    end: bool = True) -> Audio:  # fmt: skip
    data, sr = audio
    level = np.max(np.abs(data), axis=1)
    loud = np.flatnonzero(level >= 10 ** (threshold_db / 20))
    if loud.size == 0:
        raise InvalidInputError("The sound is silent at this threshold; nothing to keep")
    keep = int(keep_ms / 1000 * sr)
    first = max(0, loud[0] - keep) if start else 0
    last = min(len(data), loud[-1] + 1 + keep) if end else len(data)
    return data[first:last], sr


def op_cut(audio: Audio, start_ms: float = 0.0, end_ms: float | None = None) -> Audio:
    data, sr = audio
    a = int(start_ms / 1000 * sr)
    b = len(data) if end_ms is None else int(end_ms / 1000 * sr)
    if not 0 <= a < b <= len(data):
        raise InvalidInputError(f"cut {start_ms}..{end_ms} ms is outside the {len(data) * 1000 / sr:.0f} ms sound")
    return data[a:b], sr


def op_gain(audio: Audio, db: float) -> Audio:
    data, sr = audio
    return data * 10 ** (db / 20), sr


def op_normalize(audio: Audio, mode: str = "lufs", target: float = -16.0, ceiling_db: float = -1.0) -> Audio:
    data, sr = audio
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak == 0:
        raise InvalidInputError("Cannot normalize silence")
    if mode == "peak":
        gain_db = target - _db(peak)
    elif mode == "lufs":
        gain_db = target - integrated_loudness(data, sr)
    else:
        raise InvalidInputError("normalize mode must be lufs or peak")
    gain_db = min(gain_db, ceiling_db - _db(peak))  # never push the peak past the ceiling
    return data * 10 ** (gain_db / 20), sr


def op_fade(audio: Audio, in_ms: float = 0.0, out_ms: float = 0.0, curve: str = "sine") -> Audio:
    data, sr = audio
    data = data.copy()
    for ms, at_start in ((in_ms, True), (out_ms, False)):
        n = min(len(data), int(ms / 1000 * sr))
        if n <= 0:
            continue
        ramp = np.linspace(0.0, 1.0, n)
        if curve == "sine":
            ramp = np.sin(ramp * np.pi / 2)
        elif curve != "linear":
            raise InvalidInputError("fade curve must be sine or linear")
        if at_start:
            data[:n] *= ramp[:, None]
        else:
            data[-n:] *= ramp[::-1, None]
    return data, sr


def op_channels(audio: Audio, count: int = 1) -> Audio:
    data, sr = audio
    if count == 1:
        return data.mean(axis=1, keepdims=True), sr
    if count == 2:
        return (np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:, :2]), sr
    raise InvalidInputError("channels count must be 1 or 2")


def op_resample(audio: Audio, rate: int) -> Audio:
    data, sr = audio
    if rate == sr:
        return audio
    if not 8000 <= rate <= 96000:
        raise InvalidInputError("resample rate must be between 8000 and 96000")
    g = np.gcd(rate, sr)
    return resample_poly(data, rate // g, sr // g, axis=0), rate


def op_loop(audio: Audio, crossfade_ms: float = 50.0) -> Audio:
    """Make the end flow into the start: crossfade the tail into the head and drop the tail."""
    data, sr = audio
    n = int(crossfade_ms / 1000 * sr)
    if n <= 0 or 2 * n >= len(data):
        raise InvalidInputError("loop crossfade must be shorter than half the sound")
    ramp = np.sin(np.linspace(0.0, np.pi / 2, n))[:, None]
    out = data[:-n].copy()
    out[:n] = data[:n] * ramp + data[-n:] * ramp[::-1]
    return out, sr


OPS: dict[str, Callable[..., Audio]] = {
    "trim_silence": op_trim_silence,
    "cut": op_cut,
    "gain": op_gain,
    "normalize": op_normalize,
    "fade": op_fade,
    "channels": op_channels,
    "resample": op_resample,
    "loop": op_loop,
}


def validate_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not ops:
        raise InvalidInputError("No processing operations given")
    clean = []
    for i, raw in enumerate(ops):
        if not isinstance(raw, dict) or "op" not in raw:
            raise InvalidInputError(f"Operation #{i + 1} must be an object with an 'op' field")
        name, args = str(raw["op"]).replace("-", "_"), {k: v for k, v in raw.items() if k != "op"}
        if name == "format":
            if i != len(ops) - 1:
                raise InvalidInputError("'format' must be the last operation")
            if args.get("format", "wav") not in FORMATS:
                raise InvalidInputError(f"Unknown audio format {args.get('format')!r}", allowed=list(FORMATS))
            unknown = set(args) - {"format", "quality"}
        elif name in OPS:
            unknown = set(args) - (set(inspect.signature(OPS[name]).parameters) - {"audio"})
        else:
            raise InvalidInputError(f"Unknown audio operation {name!r}", allowed=[*OPS, "format"])
        if unknown:
            raise InvalidInputError(f"Unknown argument(s) for {name}: {', '.join(sorted(unknown))}")
        clean.append({"op": name, **args})
    return clean


def apply_ops(audio: Audio, ops: list[dict[str, Any]]) -> tuple[Audio, dict[str, Any]]:
    encoding: dict[str, Any] = {"format": "wav"}
    for op in ops:
        args = {k: v for k, v in op.items() if k != "op"}
        if op["op"] == "format":
            encoding = {"format": "wav", **args}
            continue
        try:
            audio = OPS[op["op"]](audio, **args)
        except TypeError as exc:
            raise InvalidInputError(f"Bad arguments for {op['op']}: {exc}") from exc
    return audio, encoding


def load(path: Path) -> Audio:
    try:
        data, sr = soundfile.read(str(path), dtype="float64", always_2d=True)
    except Exception as exc:
        raise InvalidInputError(f"Cannot decode audio {path.name}: {exc}") from exc
    return data, sr


def encode(audio: Audio, path: Path, encoding: dict[str, Any]) -> Path:
    data, sr = audio
    container, subtype, ext = FORMATS[encoding.get("format", "wav")]
    target = path.with_suffix(ext)
    data = np.clip(data, -1.0, 1.0)
    kwargs: dict[str, Any] = {}
    if container != "WAV" and "quality" in encoding:
        kwargs["compression_level"] = float(np.clip(1.0 - float(encoding["quality"]), 0.0, 1.0))
    if container == "WAV":
        data = np.round(data * 32767).astype(np.int16)
    soundfile.write(str(target), data, sr, format=container, subtype=subtype, **kwargs)
    return target


def ops_label(ops: list[dict[str, Any]]) -> str:
    parts = []
    for op in ops:
        name = op["op"]
        if name == "normalize":
            mode = op.get("mode", "lufs")
            parts.append(f"{op.get('target', -16):g}{'lufs' if mode == 'lufs' else 'db'}")
        elif name == "channels":
            parts.append("mono" if op.get("count", 1) == 1 else "stereo")
        elif name == "resample":
            parts.append(f"{op['rate'] // 1000}k")
        elif name == "format":
            parts.append(op.get("format", "wav"))
        elif name == "trim_silence":
            parts.append("trim")
        else:
            parts.append(name)
    return "_".join(parts).replace("-", "m")


def expand_preset(preset: str) -> list[dict[str, Any]]:
    if preset not in PRESETS:
        raise InvalidInputError(f"Unknown audio preset {preset!r}", allowed=sorted(PRESETS))
    return [dict(op) for op in PRESETS[preset]]


def process_audio_asset(
    lib: Library,
    ref: str,
    ops: list[dict[str, Any]],
    *,
    variant: str | None = None,
    label: str | None = None,
    make_primary: bool = False,
    new_asset: bool = False,
    name: str | None = None,
) -> Derivative:
    ops = validate_ops(ops)
    asset = lib.get_asset(ref)
    source = source_variant(asset, variant)
    if source.file.kind != FileKind.AUDIO:
        raise InvalidInputError(f"{asset.id} ({source.label}) is not audio")
    audio, encoding = apply_ops(load(Path(source.file.path)), ops)
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="process-"))
    try:
        output = encode(audio, tmp / "derivative", encoding)
        params = {
            "ops": ops, "processor": "assetforge-audio", "processor_version": PROCESSOR_VERSION,
            "loudness_lufs": integrated_loudness(audio[0], audio[1]),
        }  # fmt: skip
        return register_derivative(lib, asset, source, output, label=label or ops_label(ops), params=params,
                                   make_primary=make_primary, new_asset=new_asset, name=name)  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
