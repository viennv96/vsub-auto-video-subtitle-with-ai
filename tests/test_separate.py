import shutil
import subprocess

import numpy as np
import pytest

from vsub import separate as S
from vsub.audio import SAMPLE_RATE, load_audio


class IdentitySession:
    """Model giả: trả lại đúng phổ đầu vào -> demix phải dựng lại gần đúng tín hiệu gốc."""

    def run(self, _names, feeds):
        return [next(iter(feeds.values()))]


def identity_separator() -> S.VocalSeparator:
    sep = S.VocalSeparator.__new__(S.VocalSeparator)
    sep.session, sep.input = IdentitySession(), "input"
    return sep


def test_stft_roundtrip():
    x = np.random.default_rng(0).standard_normal((2, S.CHUNK)).astype(np.float32)
    spec = S.stft(x)
    assert spec.shape == (2, S.N_FFT // 2 + 1, S.DIM_T)
    assert np.abs(S.istft(spec) - x).max() < 1e-4


def test_demix_identity_reconstructs_signal():
    t = np.arange(int(14 * S.SR)) / S.SR  # dài hơn 2 cửa sổ -> có đoạn chồng nhau
    x = np.stack([np.sin(2 * np.pi * 440 * t), 0.5 * np.sin(2 * np.pi * 1000 * t)]).astype(np.float32) * 0.3
    y = identity_separator().demix(x)
    assert y.shape == x.shape
    mid = slice(S.SR, -S.SR)
    assert np.abs(y[:, mid] - x[:, mid]).max() < 1e-3


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
def test_load_vocals_streams_in_blocks_and_keeps_timeline(tmp_path, monkeypatch):
    """Đọc nhiều khối (khối 1 giây, đệm 0,3 giây) vẫn ra đúng độ dài và đúng trục thời gian,
    kể cả khi file bị hụt gói audio giữa chừng (như load_audio)."""
    monkeypatch.setattr(S, "BLOCK_S", 1.0)
    monkeypatch.setattr(S, "MARGIN_S", 0.3)
    f = tmp_path / "gap.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3:sample_rate=48000",
         "-af", "asetpts='if(gte(T,1),PTS+2/TB,PTS)'", "-c:a", "aac", str(f)],
        check=True,
    )
    seen = []
    v = S.load_vocals(str(f), separator=identity_separator(), progress=lambda d, n: seen.append(d))
    ref = load_audio(str(f))
    assert abs(len(v) - len(ref)) <= SAMPLE_RATE // 100
    assert len(seen) >= 4 and seen == sorted(seen)

    def rms(a, s, e):
        return float(np.sqrt(np.mean(a[int(s * SAMPLE_RATE):int(e * SAMPLE_RATE)] ** 2)))

    assert rms(v, 0.2, 0.8) > 0.05 and rms(v, 3.2, 4.8) > 0.05
    assert rms(v, 1.2, 2.8) < 1e-3
    assert abs(rms(v, 3.2, 4.8) - rms(ref, 3.2, 4.8)) < 0.1 * rms(ref, 3.2, 4.8)


def test_model_downloaded_checks_size(tmp_path, monkeypatch):
    monkeypatch.setenv("VSUB_HOME", str(tmp_path))
    assert not S.model_downloaded()
    p = tmp_path / "models" / S.MODEL_NAME
    p.parent.mkdir()
    p.write_bytes(b"x" * 10)
    assert not S.model_downloaded()  # file dở dang / hỏng
