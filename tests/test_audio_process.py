from __future__ import annotations

import numpy as np
import pytest
import soundfile

from assetforge.audio import process as P
from assetforge.audio.loudness import integrated_loudness, k_weighting
from assetforge.core.models import ImportOptions
from assetforge.errors import InvalidInputError
from assetforge.validation.audio import analyze, loop_seam

SR = 48000


def tone(seconds=1.0, amp=0.5, freq=440.0, sr=SR, channels=1):
    t = np.arange(int(seconds * sr)) / sr
    mono = amp * np.sin(2 * np.pi * freq * t)
    return np.stack([mono] * channels, axis=1), sr


def test_loudness_matches_bs1770_references():
    b, a = k_weighting(48000)[0]
    assert np.allclose(b, [1.53512485958697, -2.69169618940638, 1.19839281085285])
    assert np.allclose(a, [1.0, -1.69065929318241, 0.73248077421585])
    assert np.allclose(k_weighting(48000)[1][1], [1.0, -1.99004745483398, 0.99007225036621])
    t = np.arange(SR * 3) / SR
    assert integrated_loudness(np.sin(2 * np.pi * 997 * t), SR) == pytest.approx(-3.01, abs=0.02)
    assert integrated_loudness(0.1 * np.sin(2 * np.pi * 997 * t), SR) == pytest.approx(-23.01, abs=0.02)
    assert integrated_loudness(np.stack([np.sin(2 * np.pi * 997 * t)] * 2, axis=1), SR) == pytest.approx(0, abs=0.02)
    assert integrated_loudness(np.zeros(SR), SR) == -120.0


def test_trim_cut_gain_fade():
    data, sr = tone(0.5)
    padded = np.concatenate([np.zeros((SR // 2, 1)), data, np.zeros((SR, 1))])
    trimmed, _ = P.op_trim_silence((padded, sr), keep_ms=10)
    assert abs(len(trimmed) - (len(data) + 2 * 480)) <= 2
    assert len(P.op_cut((data, sr), 100, 300)[0]) == int(0.2 * SR)
    assert np.max(np.abs(P.op_gain((data, sr), -6.0206)[0])) == pytest.approx(0.25, rel=1e-3)
    faded, _ = P.op_fade((data, sr), in_ms=20, out_ms=20)
    assert faded[0, 0] == 0 and faded[-1, 0] == 0 and np.max(np.abs(faded)) == pytest.approx(0.5, rel=1e-3)
    with pytest.raises(InvalidInputError):
        P.op_trim_silence((np.zeros((100, 1)), sr))
    with pytest.raises(InvalidInputError):
        P.op_cut((data, sr), 400, 900)


def test_normalize_targets_loudness_but_respects_the_ceiling():
    data, sr = tone(2.0, amp=0.05)
    out, _ = P.op_normalize((data, sr), mode="lufs", target=-16.0, ceiling_db=-1.0)
    assert integrated_loudness(out, sr) == pytest.approx(-16.0, abs=0.05)
    loud, _ = P.op_normalize((data, sr), mode="lufs", target=0.0, ceiling_db=-1.0)
    assert 20 * np.log10(np.max(np.abs(loud))) == pytest.approx(-1.0, abs=0.01)  # capped by the ceiling
    peak, _ = P.op_normalize((data, sr), mode="peak", target=-3.0)
    assert 20 * np.log10(np.max(np.abs(peak))) == pytest.approx(-3.0, abs=0.01)


def test_channels_resample_and_loop():
    stereo, sr = tone(0.2, channels=2)
    assert P.op_channels((stereo, sr), 1)[0].shape[1] == 1
    assert P.op_channels((stereo[:, :1], sr), 2)[0].shape[1] == 2
    resampled, rate = P.op_resample((stereo, sr), 24000)
    assert rate == 24000 and len(resampled) == len(stereo) // 2

    t = np.arange(SR) / SR
    clicky = (0.5 * np.sin(2 * np.pi * 110.25 * t))[:, None]  # ends mid-cycle: a click when looped
    assert loop_seam(clicky, SR)["loop_click"]
    looped, _ = P.op_loop((clicky, SR), crossfade_ms=60)
    assert len(looped) == SR - int(0.06 * SR)
    assert not loop_seam(looped, SR)["loop_click"]


def test_validate_ops_and_labels():
    ops = P.validate_ops(P.expand_preset("web-sfx"))
    assert P.ops_label(ops) == "trim_mono_m16lufs_fade_ogg"
    for bad in ([{"op": "reverse"}], [{"op": "gain", "dbs": 1}], [{"op": "format"}, {"op": "gain", "db": 1}],
                [{"op": "format", "format": "aac"}]):  # fmt: skip
        with pytest.raises(InvalidInputError):
            P.validate_ops(bad)


def test_process_audio_asset(lib, tmp_path):
    raw = tmp_path / "boop.wav"
    data, sr = tone(0.3, amp=0.05, channels=2, sr=44100)
    soundfile.write(str(raw), np.concatenate([np.zeros((22050, 2)), data, np.zeros((22050, 2))]), sr)
    asset_id = lib.import_paths([raw], ImportOptions()).items[0].asset_id

    result = P.process_audio_asset(lib, asset_id, P.expand_preset("web-sfx"), make_primary=True)
    asset = lib.get_asset(asset_id)
    variant = next(v for v in asset.variants if v.id == result.variant_id)
    assert asset.path.endswith(".ogg") and variant.file.channels == 1
    assert variant.file.duration_ms < 400  # silence trimmed
    assert variant.params["loudness_lufs"] == pytest.approx(-16.0, abs=0.1)
    decoded, _ = soundfile.read(asset.path)
    assert integrated_loudness(decoded, 44100) == pytest.approx(-16.0, abs=0.5)
    assert P.process_audio_asset(lib, asset_id, P.expand_preset("web-sfx")).reused
    qc = analyze(decoded, 44100)
    assert qc["ok"]
