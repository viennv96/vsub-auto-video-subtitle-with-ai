"""Ghép các bước: audio -> VAD -> Whisper từng chunk -> lọc -> chia dòng."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from typing import Callable

from .audio import SAMPLE_RATE, load_audio
from .backends import PUNCT_PROMPTS, Backend, TranscribeOptions
from .filters import clean_segments
from .models import Cue, Segment, Word
from .reflow import ReflowOptions, reflow
from .vad import Chunk, fill_loud_gaps, find_holes, group_regions, slice_audio, speech_regions

# Khoảng trống giữa các đoạn VAD mà audio to hơn mức này vẫn được đưa vào Whisper
LOUD_GAP_DB = -40.0

import re

# Đoán ngôn ngữ: lấy tối đa chừng này đoạn thoại rải khắp video (bỏ phần mở đầu)
LANG_SAMPLES = 8
LANG_SKIP_INTRO_S = 120.0
# Sau chừng này giây thoại thì kiểm tra chữ viết có khớp ngôn ngữ đã chốt không
EARLY_CHECK_S = 180.0

_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
_HAN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def script_language(text: str) -> str | None:
    """Đoán ngôn ngữ CJK theo chữ viết: có kana -> ja, Hangul -> ko, chỉ Hán tự -> zh.

    Dùng để bắt lỗi Whisper chốt nhầm ngôn ngữ (ví dụ phim Nhật bị nhận là tiếng Hàn:
    chữ ra vẫn là tiếng Nhật nhưng timestamp từng từ hỏng hết)."""
    kana, hangul, han = len(_KANA.findall(text)), len(_HANGUL.findall(text)), len(_HAN.findall(text))
    cjk = kana + hangul + han
    if cjk < 20:
        return None
    if hangul > 0.5 * cjk:
        return "ko"
    if kana >= 0.08 * cjk and kana > 3 * hangul:
        return "ja"
    if han > 0.9 * cjk:
        return "zh"
    return None


def _same_lang(a: str | None, b: str | None) -> bool:
    norm = {"yue": "zh"}
    return norm.get(a, a) == norm.get(b, b)


@dataclass
class Transcript:
    video: str
    backend: str
    model: str
    task: str
    language: str | None
    duration: float
    chunks: list[tuple[float, float]] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    requested_language: str | None = None
    elapsed: float = 0.0
    hardsub: dict | None = None  # kết quả kiểm tra phụ đề in sẵn
    uncertain: list[tuple[float, float]] = field(default_factory=list)  # vùng VAD bỏ nhưng audio to
    separated: bool = False  # nhận dạng trên bản đã tách giọng (separate.py)

    def save(self, path: str) -> None:
        d = asdict(self)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)

    @staticmethod
    def load(path: str) -> "Transcript":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d["segments"] = [Segment.from_dict(s) for s in d.get("segments", [])]
        d["chunks"] = [tuple(c) for c in d.get("chunks", [])]
        d["uncertain"] = [tuple(c) for c in d.get("uncertain", [])]
        return Transcript(**d)


def _offset(segs: list[Segment], chunk: Chunk) -> list[Segment]:
    out = []
    for s in segs:
        start = min(s.start + chunk.start, chunk.end)
        end = min(s.end + chunk.start, chunk.end)
        words = [
            Word(min(w.start + chunk.start, chunk.end), min(w.end + chunk.start, chunk.end), w.text, w.probability)
            for w in s.words
        ]
        out.append(replace(s, start=start, end=end, words=words))
    return out


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def default_progress(i: int, n: int, chunk: Chunk, segs: list[Segment], eta: float | None) -> None:
    text = " ".join(s.text.strip() for s in segs)[:70]
    eta_s = f" | còn ~{_fmt(eta)}" if eta is not None else ""
    print(f"[{i + 1:>4}/{n}] {_fmt(chunk.start)} {text}{eta_s}", file=sys.stderr, flush=True)


def default_status(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def transcribe_video(
    video: str,
    backend: Backend,
    opts: TranscribeOptions,
    progress: Callable | None = default_progress,
    audio=None,
    status: Callable[[str], None] | None = default_status,
    separate: bool = False,
    separate_progress: Callable[[float, float], None] | None = None,
) -> Transcript:
    """progress(i, n, chunk, segments, eta) được gọi sau mỗi chunk; nó có thể
    raise exception để dừng giữa chừng (giao diện dùng để làm nút Dừng).

    separate=True: tách giọng khỏi nhạc nền / tiếng động trước (separate.py), rồi VAD và Whisper
    đều chạy trên bản chỉ còn giọng. separate_progress(giây đã tách, tổng giây) cũng raise được để dừng."""
    say = status or (lambda _m: None)
    t0 = time.time()
    if audio is None and separate:
        from .separate import load_vocals

        say("• Đang tách giọng khỏi nhạc nền...")
        audio = load_vocals(video, progress=separate_progress, status=say)
    elif audio is None:
        say("• Đang tách audio...")
        audio = load_audio(video)
    total = len(audio) / SAMPLE_RATE

    say("• Đang tìm các đoạn có thoại (VAD)...")
    vad = speech_regions(audio)
    # VAD hay bỏ sót lời nói lẫn nhạc nền -> khoảng trống nào audio còn to cũng đưa vào Whisper,
    # nhưng làm chunk RIÊNG, ngắn: gộp nhạc với thoại thì Whisper hay bỏ cả cửa sổ 30s
    _, uncertain = fill_loud_gaps(vad, audio, total, loud_db=LOUD_GAP_DB)
    chunks = plan_chunks(vad, uncertain, total)
    speech = sum(c.duration for c in chunks)
    say(f"  {len(chunks)} đoạn, {_fmt(speech)} audio cần nghe / {_fmt(total)} video"
        f" (gồm {_fmt(sum(e - s for s, e in uncertain))} VAD không chắc)")

    auto = opts.language is None
    lang = opts.language
    if auto and chunks:
        lang = detect_language(audio, chunks, backend, opts, total, say)
    results: list[list[Segment]] = [[] for _ in chunks]

    def transcribe_chunk(ch: Chunk, prompt: str | None, depth: int = 0) -> list[Segment]:
        r = backend.transcribe(slice_audio(audio, ch), replace(opts, language=lang, initial_prompt=prompt))
        segs = _offset(r.segments, ch)
        if any(is_loop(s) for s in segs):
            if depth < 2 and ch.duration > 4.0:
                # lặp vòng ("あ、あ、あ…") nuốt mất lời thật -> chia đôi, nghe lại không dùng câu mồi
                mid = (ch.start + ch.end) / 2
                return (transcribe_chunk(Chunk(ch.start, mid), None, depth + 1)
                        + transcribe_chunk(Chunk(mid, ch.end), None, depth + 1))
            segs = [s for s in segs if not is_loop(s)]
        return segs

    def run(i: int) -> None:
        prompt = opts.initial_prompt or (PUNCT_PROMPTS.get(lang) if lang else None)
        results[i] = transcribe_chunk(chunks[i], prompt)

    def text_so_far(upto: int) -> str:
        return "".join(s.text for segs in results[: upto + 1] for s in segs)

    say(f"• Đang nhận dạng giọng nói ({backend.name}, {backend.model})...")
    relocked = False
    while True:
        t_start, done_speech, restart = time.time(), 0.0, False
        for i, ch in enumerate(chunks):
            run(i)
            done_speech += ch.duration
            if progress:
                rate = (time.time() - t_start) / max(done_speech, 1e-6)
                progress(i, len(chunks), ch, results[i], rate * (speech - done_speech))
            # Kiểm tra sớm: chữ viết ra có khớp ngôn ngữ đã chốt không
            at_check = done_speech >= EARLY_CHECK_S and done_speech - ch.duration < EARLY_CHECK_S
            if at_check or i == len(chunks) - 1:
                seen = script_language(text_so_far(i))
                if seen and not _same_lang(seen, lang):
                    if auto and not relocked:
                        say(f"  ! Chữ nhận được là {seen} nhưng đã chốt {lang} → chạy lại với {seen}")
                        lang, relocked, restart = seen, True, True
                        break
                    say(f"  ! Cảnh báo: chữ nhận được giống {seen} nhưng đang dùng {lang}"
                        " — nếu phụ đề thiếu/lệch giờ, hãy chọn đúng ngôn ngữ của phim")
        if not restart:
            break

    segments = [s for segs in results for s in segs]

    # Lượt nghe lại: chỗ có tiếng (VAD hoặc audio to) mà chưa có chữ nào
    words = [(w.start, w.end) for s in segments for w in s.words] or [(s.start, s.end) for s in segments]
    holes = find_holes(audio, words, vad, total, loud_db=LOUD_GAP_DB)
    if holes:
        say(f"• Nghe lại {len(holes)} đoạn có tiếng nhưng chưa có chữ ({_fmt(sum(e - s for s, e in holes))})...")
        added = 0
        for hs, he in holes:
            ch = Chunk(max(0.0, hs - 0.2), min(total, he + 0.2))
            prompt = opts.initial_prompt or (PUNCT_PROMPTS.get(lang) if lang else None)
            for s in transcribe_chunk(ch, prompt):
                if not _duplicate(s, segments):
                    segments.append(s)
                    added += 1
        segments.sort(key=lambda s: s.start)
        say(f"  → thêm được {added} câu")
    return Transcript(
        video=video,
        backend=backend.name,
        model=backend.model,
        task=opts.task,
        language=lang,
        duration=total,
        chunks=[(c.start, c.end) for c in chunks],
        segments=segments,
        uncertain=uncertain,
        requested_language=opts.language,
        elapsed=time.time() - t0,
        separated=separate,
    )


def is_loop(seg: Segment) -> bool:
    """Whisper lặp vòng: chữ lặp lại dày đặc (nén được nhiều) hoặc quá nhiều chữ/giây."""
    n = len(seg.text.strip())
    return seg.compression_ratio > 2.4 or (n > 30 and n / max(seg.end - seg.start, 0.1) > 40)


def _norm_text(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s.lower())


def _duplicate(seg: Segment, segments: list[Segment], window: float = 8.0) -> bool:
    """Câu nghe lại trùng nội dung với câu đã có ở gần đó (lệch giờ chút ít) -> bỏ."""
    t = _norm_text(seg.text)
    if len(t) < 2:
        return True
    near = "".join(_norm_text(s.text) for s in segments if abs(s.start - seg.start) < window)
    return t in near


def plan_chunks(vad: list[tuple[float, float]], loud_gaps: list[tuple[float, float]],
                total: float, max_gap_chunk: float = 12.0) -> list[Chunk]:
    """Chunk thoại gom từ VAD như cũ; vùng 'VAD không chắc' cắt thành chunk riêng ≤ 12s."""
    chunks = group_regions(vad, total_s=total)
    for s, e in loud_gaps:
        n = max(1, int((e - s) // max_gap_chunk) + (1 if (e - s) % max_gap_chunk > 1.0 else 0))
        step = (e - s) / n
        chunks += [Chunk(s + k * step, s + (k + 1) * step) for k in range(n)]
    chunks.sort(key=lambda c: c.start)
    out: list[Chunk] = []
    for c in chunks:  # cắt chồng lấn do phần đệm của chunk thoại
        if out and c.start < out[-1].end:
            c.start = out[-1].end
        if c.end - c.start >= 0.3:
            out.append(c)
    return out


def detect_language(audio, chunks: list[Chunk], backend: Backend, opts: TranscribeOptions,
                    total: float, say: Callable[[str], None]) -> str | None:
    """Đoán ngôn ngữ từ nhiều đoạn thoại rải khắp video.

    Bỏ qua 2 phút đầu (đoạn tóm tắt tập trước / bài hát mở đầu hay làm Whisper đoán nhầm,
    ví dụ tiếng hét "Akira!" bị nhận là tiếng Hàn), bỏ đoạn quá ngắn, rồi bỏ phiếu theo độ dài.
    Chữ viết của kết quả (kana / Hangul / Hán tự) cũng được tính phiếu."""
    cands = [c for c in chunks if c.duration >= 4.0]
    if total > 5 * LANG_SKIP_INTRO_S:
        later = [c for c in cands if c.start >= LANG_SKIP_INTRO_S]
        if len(later) >= 3:
            cands = later
    cands = cands or chunks
    step = max(1, len(cands) // LANG_SAMPLES)
    picks = cands[step // 2 :: step][:LANG_SAMPLES] or cands[:1]
    say(f"• Đang đoán ngôn ngữ từ {len(picks)} đoạn thoại...")
    votes: Counter = Counter()
    for c in picks:
        r = backend.transcribe(slice_audio(audio, c), replace(opts, language=None))
        if r.language:
            votes[r.language] += c.duration
        seen = script_language("".join(s.text for s in r.segments))
        if seen:
            votes[seen] += 1.5 * c.duration
    lang = votes.most_common(1)[0][0] if votes else None
    if lang:
        detail = ", ".join(f"{k} {v:.0f}" for k, v in votes.most_common(3))
        say(f"  → Ngôn ngữ: {lang} ({detail})")
    return lang


def check_hardsubs(t: Transcript, video: str | None = None):
    """Kiểm tra phụ đề in sẵn ở các đoạn có thoại; lưu kết quả vào transcript."""
    from .hardsub import detect_hardsubs

    info = detect_hardsubs(video or t.video, [(a + b) / 2 for a, b in t.chunks])
    t.hardsub = {
        "bottom_ratio": info.bottom_ratio, "top_ratio": info.top_ratio,
        "frames": info.frames, "suggest": info.suggest_position(), "error": info.error,
    }
    return info


def resolve_position(choice: str, t: Transcript | None) -> str:
    if choice in ("top", "bottom"):
        return choice
    if t is not None and t.hardsub and not t.hardsub.get("error"):
        return t.hardsub.get("suggest", "bottom")
    return "bottom"


def make_cues(t: Transcript, ropts: ReflowOptions | None = None, clean: bool = True) -> list[Cue]:
    segs = clean_segments(t.segments, uncertain=t.uncertain) if clean else t.segments
    # translate luôn ra tiếng Anh
    lang = "en" if t.task == "translate" else t.language
    return reflow(segs, lang, ropts)
