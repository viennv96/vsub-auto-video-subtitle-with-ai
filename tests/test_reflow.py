from vsub.models import Segment, Word
from vsub.reflow import ReflowOptions, reflow


def words_from(text, start=0.0, step=0.3):
    out, t = [], start
    for tok in text.split():
        out.append(Word(t, t + step * 0.9, " " + tok))
        t += step
    return out


def seg(text, start=0.0, step=0.3):
    ws = words_from(text, start, step)
    return Segment(ws[0].start, ws[-1].end, " " + text, ws)


def test_splits_on_sentence_end():
    cues = reflow([seg("Anh đi đâu đấy? Em về nhà.")], "vi")
    assert [c.text for c in cues] == ["Anh đi đâu đấy?", "Em về nhà."]


def test_line_length_and_two_lines():
    text = ("Hôm nay trời mưa rất to nên chúng ta phải ở nhà cả ngày, "
            "không đi đâu được hết, buồn ơi là buồn")
    cues = reflow([seg(text, step=0.12)], "vi", ReflowOptions(max_duration=10))
    for c in cues:
        assert len(c.lines) <= 2
        assert all(len(line) <= 42 for line in c.lines), c.lines


def test_prefers_comma_split_when_too_long():
    text = "Tôi biết anh đã cố gắng rất nhiều suốt mấy năm qua, nhưng chuyện này không thể tiếp tục như vậy mãi được nữa đâu anh"
    cues = reflow([seg(text, step=0.1)], "vi", ReflowOptions(max_duration=20))
    assert cues[0].text.replace("\n", " ").endswith("qua,")


def test_pause_splits():
    s1 = seg("xin chào", 0.0)
    s2 = seg("tạm biệt", 3.0)
    cues = reflow([s1, s2], "vi")
    assert len(cues) == 2


def test_min_duration_and_no_overlap():
    s1 = seg("Ừ.", 1.0, step=0.2)
    s2 = seg("Được.", 1.5, step=0.2)
    cues = reflow([s1, s2], "vi")
    assert cues[0].end <= cues[1].start
    assert all(c.end > c.start for c in cues)


def test_min_duration_extends_short_cue():
    cues = reflow([seg("Ừ.", 1.0, step=0.2)], "vi")
    assert cues[0].end - cues[0].start >= 1.0


def test_cjk_uses_short_lines():
    chars = list("我今天很高兴见到你们大家我们一起去吃饭吧然后再去看电影好不好")
    ws = [Word(i * 0.15, i * 0.15 + 0.14, ch) for i, ch in enumerate(chars)]
    cues = reflow([Segment(0, ws[-1].end, "".join(chars), ws)], "zh", ReflowOptions(max_duration=20))
    for c in cues:
        assert all(len(line) <= 16 for line in c.lines), c.lines
        assert " " not in c.text


def test_segment_without_words_is_synthesized():
    s = Segment(2.0, 5.0, " Không có timestamp từng từ.", [])
    cues = reflow([s], "vi")
    assert cues and abs(cues[0].start - 2.0) < 1e-6
    assert cues[0].text == "Không có timestamp từng từ."


def test_long_first_word_is_trimmed():
    ws = [Word(0.0, 8.0, " Này"), Word(8.0, 8.3, " anh!")]
    cues = reflow([Segment(0, 8.3, " Này anh!", ws)], "vi")
    assert cues[0].start >= 5.9


def test_breaks_at_whisper_segment_boundary_without_punctuation():
    a_text = "整形じゃねえわ"
    a = Segment(0.0, 1.2, a_text, [Word(i * 0.17, i * 0.17 + 0.16, ch) for i, ch in enumerate(a_text)])
    t0 = 1.2 + 0.15
    b_text = "兄貴は姉貴になるってことでオッケー"
    b = Segment(t0, t0 + 2, b_text, [Word(t0 + i * 0.1, t0 + i * 0.1 + 0.09, ch) for i, ch in enumerate(b_text)])
    cues = reflow([a, b], "ja")
    assert [c.text.replace("\n", "") for c in cues] == [a_text, b_text]


def test_fit_reading_time_extends_into_gap_only():
    from vsub.models import Cue
    from vsub.reflow import MAX_EXTEND, cue_cps, fit_reading_time

    long = "x" * 34  # 34 ký tự / 17 cps = cần 2 giây
    cues = [
        Cue(0.0, 1.0, [long]),          # còn trống tới 5.0 -> kéo tới 2.0
        Cue(5.0, 6.0, [long]),          # câu sau sát nút ở 6.5 -> chỉ tới 6.42
        Cue(6.5, 7.0, ["x" * 100]),     # cần 5.9 giây, cuối phim: tối đa +MAX_EXTEND
        Cue(20.0, 21.0, ["ngắn"]),      # đọc kịp: giữ nguyên
    ]
    fit_reading_time(cues, 17)
    assert abs(cues[0].end - 2.0) < 1e-9 and cue_cps(cues[0]) <= 17
    assert abs(cues[1].end - 6.42) < 1e-9
    assert abs(cues[2].end - (7.0 + MAX_EXTEND)) < 1e-9
    assert cues[3].end == 21.0
    assert [c.start for c in cues] == [0.0, 5.0, 6.5, 20.0]


def test_cue_chars_counts_spaces_and_skips_tags():
    from vsub.models import Cue
    from vsub.reflow import cue_chars, reading_cps

    assert cue_chars(Cue(0, 1, ["{\\an8}Xin chào", "anh."])) == len("Xin chào anh.")
    assert reading_cps("vi") == 17 and reading_cps("ja") == 4 and reading_cps(None) == 17
