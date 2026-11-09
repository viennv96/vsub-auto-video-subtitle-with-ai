"""Tách giọng nói khỏi nhạc nền, tiếng động trước khi nhận dạng (model MDX-Net Kim_Vocal_2, ONNX).

Lý do: cảnh có nhạc to hoặc ồn thì VAD bỏ sót lời, Whisper nghe nhầm hoặc bỏ cả đoạn.
Nghe trên bản chỉ còn giọng thì đỡ hơn.

Cách chạy (theo MDX-Net của UVR / python-audio-separator, viết lại bằng numpy để khỏi cần torch):
- audio 44,1 kHz stereo -> cửa sổ 261 120 mẫu (~5,9 giây), hai cửa sổ liền nhau chồng 25%
- STFT (n_fft 7680, hop 1024) -> model đoán phổ của giọng -> iSTFT -> cộng chồng có trọng số Hann
- đọc ffmpeg theo khối 2 phút (mỗi bên đệm 6 giây) nên phim dài cũng không tốn nhiều RAM
- giọng tách ra: trộn về mono, hạ xuống 16 kHz, cùng bộ lọc như audio.load_audio

Model ~67 MB, tải lần đầu về thư mục dữ liệu của app, kiểm SHA-256.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE, require_ffmpeg
from .paths import data_dir

MODEL_NAME = "Kim_Vocal_2.onnx"
MODEL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx"
MODEL_SHA256 = "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b"
MODEL_SIZE = 66_759_214

SR = 44_100
N_FFT = 7680
HOP = 1024
DIM_F = 3072
DIM_T = 256
CHUNK = HOP * (DIM_T - 1)  # 261 120 mẫu
TRIM = N_FFT // 2
OVERLAP = 0.25
BLOCK_S = 120.0
MARGIN_S = 6.0  # > độ dài một cửa sổ, để mẫu nào giữ lại cũng được model nhìn đủ ngữ cảnh

Progress = Callable[[float, float], None]  # (giây đã xử lý, tổng giây)


class SeparationError(RuntimeError):
    pass


def model_path() -> str:
    return os.path.join(data_dir(), "models", MODEL_NAME)


def model_downloaded() -> bool:
    p = model_path()
    return os.path.isfile(p) and os.path.getsize(p) == MODEL_SIZE


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ensure_model(status: Callable[[str], None] | None = None) -> str:
    """Tải model nếu chưa có (ghi ra file tạm, đúng SHA-256 mới đổi tên)."""
    path = model_path()
    if model_downloaded():
        return path
    say = status or (lambda _m: None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.part"
    say(f"• Đang tải model tách giọng ({MODEL_SIZE / 1e6:.0f} MB, chỉ lần đầu)...")
    from . import __version__

    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": f"vsub/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310 — github.com
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                f.write(block)
        if _sha256(tmp) != MODEL_SHA256:
            raise SeparationError("Model tách giọng tải về bị hỏng (sai SHA-256), hãy thử lại")
        os.replace(tmp, path)
    except OSError as e:
        raise SeparationError(f"Không tải được model tách giọng: {e}") from e
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def _providers() -> list:
    """Mac: CoreML dạng MLProgram chạy trên GPU (~0,15 s/cửa sổ trên M1 Max, CPU ~2,4 s).
    Dạng NeuralNetwork mặc định của CoreML thì không nhanh hơn CPU. Có CUDA/DirectML thì dùng."""
    import onnxruntime as ort

    avail = ort.get_available_providers()
    out: list = []
    if "CoreMLExecutionProvider" in avail:
        out.append(("CoreMLExecutionProvider", {"ModelFormat": "MLProgram", "MLComputeUnits": "CPUAndGPU"}))
    out += [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in avail]
    return out + ["CPUExecutionProvider"]


_WIN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)  # Hann tuần hoàn


def stft(x: np.ndarray) -> np.ndarray:
    """[C, N] -> phổ [C, N_FFT//2+1, khung], giống torch.stft(center=True, pad reflect)."""
    xp = np.pad(x, ((0, 0), (N_FFT // 2, N_FFT // 2)), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(xp, N_FFT, axis=-1)[:, ::HOP]
    return np.fft.rfft(frames * _WIN, axis=-1).transpose(0, 2, 1)


def istft(spec: np.ndarray) -> np.ndarray:
    """Ngược lại của stft(): [C, F, T] -> [C, HOP*(T-1)]."""
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1).astype(np.float32) * _WIN
    c, t, _ = frames.shape
    n = N_FFT + HOP * (t - 1)
    out = np.zeros((c, n), np.float32)
    wsum = np.zeros(n, np.float32)
    w2 = _WIN * _WIN
    for k in range(t):
        out[:, k * HOP:k * HOP + N_FFT] += frames[:, k]
        wsum[k * HOP:k * HOP + N_FFT] += w2
    out /= np.where(wsum > 1e-8, wsum, 1.0)
    return out[:, N_FFT // 2:N_FFT // 2 + HOP * (t - 1)]


class VocalSeparator:
    def __init__(self, path: str | None = None):
        try:
            import onnxruntime as ort
        except ImportError as e:  # pragma: no cover - onnxruntime đi kèm rapidocr
            raise SeparationError("Thiếu onnxruntime") from e
        path = path or ensure_model()
        try:
            self.session = ort.InferenceSession(path, providers=_providers())
        except Exception as e:  # noqa: BLE001 — CoreML/GPU không dựng được model thì chạy CPU
            print(f"! Tách giọng chuyển sang CPU ({e})", file=sys.stderr)
            self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def _run(self, part: np.ndarray) -> np.ndarray:
        """Một cửa sổ [2, CHUNK] -> giọng [2, CHUNK]."""
        spec = stft(part)[:, :DIM_F]  # [2, F, T] phức
        x = np.stack([spec[0].real, spec[0].imag, spec[1].real, spec[1].imag]).astype(np.float32)
        x[:, :3] = 0  # bỏ 3 bin thấp nhất như UVR
        y = self.session.run(None, {self.input: x[None]})[0][0]
        full = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
        full[0, :DIM_F] = y[0] + 1j * y[1]
        full[1, :DIM_F] = y[2] + 1j * y[3]
        return istft(full)

    def demix(self, mix: np.ndarray) -> np.ndarray:
        """[2, N] stereo 44,1 kHz -> giọng [2, N]."""
        n = mix.shape[1]
        gen = CHUNK - 2 * TRIM
        pad = gen + TRIM - n % gen
        mixture = np.concatenate([np.zeros((2, TRIM), np.float32), mix, np.zeros((2, pad), np.float32)], axis=1)
        total = mixture.shape[1]
        step = int((1 - OVERLAP) * CHUNK)
        result = np.zeros((2, total), np.float32)
        divider = np.zeros(total, np.float32)
        for i in range(0, total, step):
            end = min(i + CHUNK, total)
            part = mixture[:, i:end]
            if end - i < CHUNK:
                part = np.pad(part, ((0, 0), (0, CHUNK - (end - i))))
            w = np.hanning(end - i).astype(np.float32)
            result[:, i:end] += self._run(part)[:, :end - i] * w
            divider[i:end] += w
        result /= np.where(divider > 1e-8, divider, 1.0)
        return result[:, TRIM:TRIM + n]


def _probe_seconds(path: str) -> float:
    try:
        from .audio import probe_duration

        return probe_duration(path)
    except Exception:  # noqa: BLE001
        return 0.0


def load_vocals(
    path: str,
    audio_track: int = 0,
    progress: Progress | None = None,
    status: Callable[[str], None] | None = None,
    separator: VocalSeparator | None = None,
) -> np.ndarray:
    """Giống audio.load_audio nhưng chỉ còn giọng nói: float32 mono 16 kHz, cùng trục thời gian với video."""
    require_ffmpeg()
    sep = separator or VocalSeparator(ensure_model(status))
    total_s = _probe_seconds(path)
    # stderr ghi ra file tạm: file hỏng làm ffmpeg in lỗi liên tục, để trong pipe không ai đọc thì sẽ kẹt
    dec_err, enc_err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    dec = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", f"0:a:{audio_track}", "-vn",
         "-af", "aresample=async=1:first_pts=0", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
        stdout=subprocess.PIPE, stderr=dec_err,
    )
    # bản giọng 44,1 kHz mono -> ffmpeg hạ 16 kHz + lọc như load_audio; đọc ở luồng riêng cho khỏi kẹt pipe
    enc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
         "-af", "highpass=f=80,lowpass=f=8000", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=enc_err,
    )
    pieces: list[bytes] = []
    reader = threading.Thread(target=lambda: pieces.append(enc.stdout.read()), daemon=True)
    reader.start()

    block, margin = int(BLOCK_S * SR), int(MARGIN_S * SR)
    buf = np.zeros((2, 0), np.float32)
    buf_start = 0  # chỉ số mẫu (toàn phim) của buf[:, 0]
    pos = 0  # mẫu đầu tiên chưa xuất
    eof = False
    try:
        while True:
            while not eof and buf_start + buf.shape[1] < pos + block + margin:
                raw = dec.stdout.read(int(10 * SR) * 8)
                if not raw:
                    eof = True
                    break
                raw = raw[: len(raw) // 8 * 8]
                buf = np.concatenate([buf, np.frombuffer(raw, np.float32).reshape(-1, 2).T], axis=1)
            avail = buf_start + buf.shape[1]
            if pos >= avail:
                break
            b1 = min(pos + block, avail)
            lo, hi = max(0, pos - margin), min(avail, b1 + margin)
            voc = sep.demix(np.ascontiguousarray(buf[:, lo - buf_start:hi - buf_start]))
            # (L+R)·0,707: đúng như ffmpeg "-ac 1" trong load_audio, để mức âm (và các ngưỡng dB) khớp
            mono = voc[:, pos - lo:b1 - lo].sum(axis=0) * np.float32(np.sqrt(0.5))
            enc.stdin.write(mono.astype(np.float32).tobytes())
            pos = b1
            if progress:
                progress(pos / SR, total_s or pos / SR)
            drop = pos - margin - buf_start
            if drop > 0:
                buf, buf_start = buf[:, drop:], buf_start + drop
        dec.wait()
        enc.stdin.close()
        reader.join()
        enc.wait()
    except BaseException:
        for p in (dec, enc):
            p.kill()
        raise

    def err_text(f) -> str:
        f.seek(0)
        return f.read()[-2000:].decode(errors="replace")

    try:
        if dec.returncode != 0:
            raise SeparationError(f"ffmpeg không đọc được audio: {err_text(dec_err)}")
        if enc.returncode != 0:
            raise SeparationError(f"ffmpeg lỗi khi hạ tần số mẫu: {err_text(enc_err)}")
    finally:
        dec_err.close()
        enc_err.close()
    return np.frombuffer(b"".join(pieces), dtype=np.float32).copy()

