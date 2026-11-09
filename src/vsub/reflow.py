"""Chia lời thoại (có timestamp từng từ) thành các dòng phụ đề dễ đọc.

Quy tắc kiểu phụ đề phim:
- tối đa `max_lines` dòng, mỗi dòng ≤ `max_chars` ký tự (CJK dùng giới hạn ngắn hơn)
- ngắt khi hết câu, khi người nói dừng lâu, hoặc khi quá dài / quá lâu
- khi phải ngắt giữa câu thì ưu tiên ngắt sau dấu phẩy
- mỗi cue hiển thị ít nhất `min_duration` giây, không chồng lên cue sau
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .models import Cue, Segment, Word

SENT_END = (".", "?", "!", "…", "。", "？", "！")
CLAUSE_END = (",", ";", ":", "，", "、", "；", "：", " -", "—")
CLOSERS = "\"'”’»)]}」』"
# Ngôn ngữ không dùng khoảng trắng giữa các từ
CJK_LANGS = {"zh", "ja", "yue", "th", "lo", "my", "km"}


@dataclass
class ReflowOptions:
    max_chars: int = 42
    max_chars_cjk: int = 16
    max_lines: int = 2
    max_duration: float = 6.0
    min_duration: float = 1.0
    pause_split: float = 0.5
    seg_gap: float = 0.12  # ranh giới segment Whisper + nghỉ ≥ chừng này -> ngắt
    seg_min_dur: float = 1.5  # hoặc cue đã dài ≥ chừng này giây thì ngắt ở ranh giới segment
    min_gap: float = 0.08
    linger: float = 0.3


def _tail(text: str) -> str:
    return text.strip().rstrip(CLOSERS)


def ends_sentence(w: Word) -> bool:
    return _tail(w.text).endswith(SENT_END)


def ends_clause(w: Word) -> bool:
    return _tail(w.text).endswith(CLAUSE_END)


def join_words(words: list[Word]) -> str:
    return re.sub(r"\s+", " ", "".join(w.text for w in words)).strip()


def _synth_words(seg: Segment, cjk: bool) -> list[Word]:
    """Segment không có timestamp từng từ: chia thời gian theo độ dài chữ."""
    text = seg.text.strip()
    if not text:
        return []
    tokens = list(text) if cjk else [" " + t for t in text.split()]
    total = sum(len(t.strip()) or 1 for t in tokens)
    dur = max(seg.end - seg.start, 0.01)
    out, t = [], seg.start
    for tok in tokens:
        d = dur * (len(tok.strip()) or 1) / total
        out.append(Word(t, t + d, tok))
        t += d
    return out


def _sanitize(words: list[Word], max_word_s: float = 2.0) -> list[Word]:
    """Whisper đôi khi kéo dài từ đầu câu qua cả đoạn im lặng — cắt bớt lại."""
    out = []
    for w in words:
        if not w.text.strip():
            continue
        if w.end - w.start > max_word_s:
            w = replace(w, start=w.end - max_word_s)
        out.append(w)
    return out


def collect_words(segments: list[Segment], cjk: bool) -> tuple[list[Word], set[int]]:
    """Trả về danh sách từ và tập id() của các từ kết thúc một segment Whisper."""
    words: list[Word] = []
    seg_ends: set[int] = set()
    for seg in segments:
        ws = _sanitize(seg.words if seg.words else _synth_words(seg, cjk))
        if ws:
            words.extend(ws)
            seg_ends.add(id(ws[-1]))
    return words, seg_ends


def _best_clause_split(cur: list[Word], cap: int) -> int | None:
    """Vị trí ngắt đẹp nhất (sau dấu phẩy) mà phần đầu không quá ngắn."""
    best = None
    for k in range(1, len(cur)):
        if ends_clause(cur[k - 1]) and len(join_words(cur[:k])) >= 0.4 * cap:
            best = k
    return best


def group_words(
    words: list[Word], line_limit: int, opts: ReflowOptions, seg_ends: set[int] | None = None
) -> list[list[Word]]:
    cap = line_limit * opts.max_lines
    seg_ends = seg_ends or set()
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            prev = cur[-1]
            gap = w.start - prev.end
            seg_break = id(prev) in seg_ends and (
                gap >= opts.seg_gap or prev.end - cur[0].start >= opts.seg_min_dur
            )
            natural = gap > opts.pause_split or ends_sentence(prev) or seg_break
            too_long = len(join_words(cur + [w])) > cap
            too_slow = w.end - cur[0].start > opts.max_duration
            if natural:
                groups.append(cur)
                cur = []
            elif too_long or too_slow:
                k = _best_clause_split(cur, cap)
                if k is not None:
                    groups.append(cur[:k])
                    cur = cur[k:]
                    if len(join_words(cur + [w])) > cap or w.end - cur[0].start > opts.max_duration:
                        groups.append(cur)
                        cur = []
                else:
                    groups.append(cur)
                    cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def split_lines(words: list[Word], line_limit: int, max_lines: int = 2) -> list[str]:
    text = join_words(words)
    if len(text) <= line_limit or len(words) < 2 or max_lines < 2:
        return [text]
    best, best_score = None, None
    for k in range(1, len(words)):
        a, b = join_words(words[:k]), join_words(words[k:])
        score = max(len(a), len(b))
        if len(a) > line_limit or len(b) > line_limit:
            score += 1000
        if ends_sentence(words[k - 1]) or ends_clause(words[k - 1]):
            score -= 6
        if best_score is None or score < best_score:
            best, best_score = (a, b), score
    return list(best)


def fix_timing(cues: list[Cue], opts: ReflowOptions) -> list[Cue]:
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else float("inf")
        target = max(c.end + opts.linger, c.start + opts.min_duration)
        c.end = max(min(target, nxt - opts.min_gap), c.end if c.end < nxt else c.start + 0.3)
    for prev, c in zip(cues, cues[1:]):
        if c.start < prev.end + opts.min_gap:
            c.start = prev.end + opts.min_gap
            if c.end <= c.start + 0.3:
                c.end = c.start + 0.3
    for c in cues:  # chốt chặn cuối: không có cue thời lượng âm/0
        if c.end <= c.start:
            c.end = c.start + 0.5
    return cues


# Tốc độ đọc tối đa cho phim người lớn (ký tự/giây, tính cả dấu cách và dấu câu), theo hướng dẫn
# phụ đề của Netflix từng ngôn ngữ. Ngôn ngữ không có trong bảng dùng DEFAULT_CPS.
READING_CPS = {"vi": 17, "en": 20, "ja": 4, "zh": 9, "zh-tw": 9, "yue": 9, "ko": 12, "th": 15}
DEFAULT_CPS = 17
MAX_EXTEND = 1.0      # cho câu hiện thêm tối đa chừng này giây sau giờ kết thúc cũ
MAX_CUE_S = 7.0       # một câu không hiện quá chừng này giây


def reading_cps(language: str | None) -> float:
    return READING_CPS.get((language or "").lower(), DEFAULT_CPS)


def cue_chars(cue: Cue) -> int:
    """Số ký tự người xem phải đọc: các dòng nối bằng một dấu cách, bỏ tag kiểu {\\an8}."""
    return len(re.sub(r"\{\\[^}]*\}", "", " ".join(l.strip() for l in cue.lines)).strip())


def cue_cps(cue: Cue) -> float:
    return cue_chars(cue) / max(cue.end - cue.start, 0.01)


def display_limit(cues: list[Cue], i: int, min_gap: float = 0.08) -> float:
    """Giờ muộn nhất câu thứ i được hiện tới: trước câu sau, ≤ MAX_EXTEND giây sau giờ kết thúc cũ,
    tổng cộng ≤ MAX_CUE_S (nhưng không ngắn hơn giờ kết thúc hiện có)."""
    c = cues[i]
    nxt = cues[i + 1].start - min_gap if i + 1 < len(cues) else float("inf")
    return max(c.end, min(nxt, c.end + MAX_EXTEND, c.start + MAX_CUE_S))


def fit_reading_time(cues: list[Cue], max_cps: float, min_gap: float = 0.08) -> list[Cue]:
    """Câu nào đọc không kịp thì cho hiện lâu hơn, lấn vào khoảng trống phía sau (không đè câu sau).
    Giờ bắt đầu giữ nguyên để chữ vẫn hiện đúng lúc nhân vật nói."""
    if max_cps <= 0:
        return cues
    for i, c in enumerate(cues):
        need = c.start + cue_chars(c) / max_cps + 1e-3  # +1 ms cho khỏi sai số làm tròn
        if need > c.end:
            c.end = min(need, display_limit(cues, i, min_gap))
    return cues


def reflow(segments: list[Segment], language: str | None, opts: ReflowOptions | None = None) -> list[Cue]:
    opts = opts or ReflowOptions()
    cjk = (language or "") in CJK_LANGS
    line_limit = opts.max_chars_cjk if cjk else opts.max_chars
    words, seg_ends = collect_words(segments, cjk)
    cues = [
        Cue(min(w.start for w in g), max(w.end for w in g), split_lines(g, line_limit, opts.max_lines), words=g)
        for g in group_words(words, line_limit, opts, seg_ends)
        if join_words(g)
    ]
    return fix_timing(cues, opts)
