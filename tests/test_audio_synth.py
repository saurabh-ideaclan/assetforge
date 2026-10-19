from __future__ import annotations

import numpy as np
import pytest

from assetforge.audio import synth as s
from assetforge.audio.effects import apply_effects, parse_effect, resolve_effects
from assetforge.audio.export import finish, pcm_sha256, to_pcm16
from assetforge.errors import InvalidInputError
from assetforge.validation.audio import analyze

SR = 44100


def dominant_hz(x: np.ndarray, sr: int = SR) -> float:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / sr)[np.argmax(spectrum)])


@pytest.mark.parametrize("shape", ["sine", "square", "saw", "triangle"])
def test_oscillators_hit_the_requested_pitch(shape):
    x = s.osc(shape, 440.0, SR, SR)
    assert abs(dominant_hz(x) - 440.0) < 2.0
    assert np.max(np.abs(x)) <= 1.2


def test_sweep_and_steps():
    f = s.sweep(100, 400, 1000)
    assert f[0] == pytest.approx(100) and f[-1] == pytest.approx(400)
    assert f[500] == pytest.approx(200, rel=0.01)  # exponential: geometric midpoint
    assert list(np.unique(s.steps([1, 2, 3], 9))) == [1, 2, 3]


def test_noise_colors_tilt_the_spectrum():
    rng = np.random.default_rng(0)
    def low_high_ratio(x):
        spec = np.abs(np.fft.rfft(x)) ** 2
        freqs = np.fft.rfftfreq(len(x), 1 / SR)
        return spec[(freqs > 50) & (freqs < 500)].mean() / spec[(freqs > 5000) & (freqs < 10000)].mean()

    white, pink, brown = (low_high_ratio(s.noise(SR, rng, c)) for c in ("white", "pink", "brown"))
    assert white < pink < brown


def test_envelopes_and_filters():
    env = s.perc(1000, SR, attack=0.001, curve=3)
    assert len(env) == 1000 and env.max() == pytest.approx(1.0, abs=0.05) and env[-1] == 0
    assert len(s.adsr(5000, SR)) == 5000
    tone = s.osc("sine", 8000, SR, SR)
    assert np.std(s.lowpass(tone, 500, SR, order=4)) < 0.05 * np.std(tone)
    swept = s.filter_track(s.noise(SR, np.random.default_rng(1)), "lowpass", s.sweep(8000, 200, SR), SR)
    assert np.all(np.isfinite(swept))


def test_effects_parse_resolve_and_apply():
    assert parse_effect("reverb:room_size=0.5, wet_level=0.3") == {
        "name": "reverb", "params": {"room_size": 0.5, "wet_level": 0.3},
    }  # fmt: skip
    resolved = resolve_effects(["reverb:room_size=0.5"])
    assert resolved[0]["params"]["damping"] == 0.5  # defaults filled in for exact replay
    with pytest.raises(InvalidInputError):
        resolve_effects(["warp"])
    with pytest.raises(InvalidInputError):
        resolve_effects(["reverb:size=1"])
    with pytest.raises(InvalidInputError):
        resolve_effects(["reverb:room_size=5"])

    click = np.zeros(SR // 10)
    click[0] = 1.0
    wet = apply_effects(click, resolved, SR)
    assert len(wet) > len(click)  # reverb tail was given room
    assert np.abs(wet[len(click):]).max() > 0


def test_finish_normalizes_trims_and_removes_dc():
    x = np.concatenate([0.2 * np.sin(np.linspace(0, 200 * np.pi, 4410)) + 0.3, np.zeros(44100)])
    y = finish(x, SR, peak_db=-1.0)
    assert len(y) < 10000  # silent tail trimmed
    assert np.max(np.abs(y)) == pytest.approx(10 ** (-1 / 20), rel=1e-6)
    qc = analyze(y, SR)
    assert qc["ok"] and "dc_offset" not in qc["issues"]
    assert y[0] == 0 and y[-1] == 0  # click-free edges


def test_analyze_flags_problems():
    assert analyze(np.zeros(1000), SR)["issues"] == ["silent"]
    clipped = analyze(np.clip(2 * np.sin(np.linspace(0, 50, 4000)), -1, 1), SR)
    assert "clipping" in clipped["issues"] and not clipped["ok"]
    late = np.concatenate([np.zeros(SR // 5), 0.5 * np.ones(1000)])
    assert "leading_silence" in analyze(late, SR)["issues"]


def test_pcm_hash_is_stable():
    pcm = to_pcm16(np.linspace(-1, 1, 100))
    assert pcm.dtype == np.int16 and pcm.max() == 32767
    assert pcm_sha256(pcm) == pcm_sha256(pcm.copy())
