import json
import os
import stat
import sys
import textwrap

import pytest

from vsub.formats import parse_srt, to_srt
from vsub.models import Cue, Word
from vsub.translate import split_cue
from vsub.translate import (
    ClaudeError, ClaudeRunner, extract_json, join_lines, parse_glossary_text, translate_cues,
    wrap_text,
)

FAKE = r'''#!{py}
import json, os, sys
sys.stdin.reconfigure(encoding="utf-8"); sys.stdout.reconfigure(encoding="utf-8")  # như claude thật
args = sys.argv[1:]
system = args[args.index("--system-prompt") + 1]
assert args[args.index("--tools") + 1] == ""
assert "-p" in args and "--no-session-persistence" in args
data = sys.stdin.read()
state = os.environ["FAKE_STATE"]
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "nologin":
    print(json.dumps({{"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}}))
    sys.exit(0)
if "terminology" in system:
    res = "```json\n" + json.dumps({{"terms": [{{"src": "田中", "dst": "Tanaka", "type": "person"}}], "notes": "office drama"}}) + "\n```"
elif "subtitle editor" in system:  # lượt rút gọn câu đọc không kịp
    req = json.loads(data)
    open(state + ".shorten", "w", encoding="utf-8").write(data)
    # chế độ thường: Claude "không rút gọn được" (trả lại y nguyên) -> app giữ bản cũ
    res = json.dumps({{str(l["id"]): "Ngắn." if mode == "long" else l["text"] for l in req["lines"]}},
                     ensure_ascii=False)
else:
    req = json.loads(data)
    assert req["glossary"]["田中"] == "Tanaka"
    if mode == "long":
        open(state + ".lines", "w", encoding="utf-8").write(json.dumps(req["lines"], ensure_ascii=False))
        print(json.dumps({{"type": "result", "is_error": False, "total_cost_usd": 0.01, "result": json.dumps(
            {{str(l["id"]): "Một câu dịch rất dài dòng không đọc kịp trong một giây" for l in req["lines"]}},
            ensure_ascii=False)}}))
        sys.exit(0)
    first = not os.path.exists(state)
    open(state, "a").write("x")
    out = {{}}
    for i, line in enumerate(req["lines"]):
        if first and i == 0 and mode != "split":
            continue  # lần đầu cố tình thiếu 1 câu để thử hỏi lại
        if mode == "split" and "|" in line["text"]:
            a, b = line["text"].split("|")
            out[str(line["id"])] = [[a, "VI: " + a], ["|" + b, "VI: " + b]]
        else:
            out[str(line["id"])] = "VI: " + line["text"].replace("田中", "Tanaka")
    res = "Đây là bản dịch:\n" + json.dumps(out, ensure_ascii=False)
print(json.dumps({{"type": "result", "is_error": False, "result": res, "total_cost_usd": 0.01}}))
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    exe = tmp_path / "claude.py"  # .py: ClaudeRunner chạy bằng Python hiện tại (cả trên Windows)
    exe.write_text(FAKE.format(py=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "state"))
    return str(exe)


def test_translate_with_retry_and_glossary(fake_claude):
    cues = [Cue(i, i + 1, [f"田中さん、こんにちは {i}"]) for i in range(7)]
    res = translate_cues(
        cues, "vi", "ja", ClaudeRunner(exe=fake_claude),
        user_glossary={"佐藤": "Sato-sensei"}, batch_size=3, workers=2,
    )
    assert res.missing == 0
    assert [c.start for c in res.cues] == [c.start for c in cues]
    assert all(c.text.startswith("VI: Tanaka") for c in res.cues)
    assert res.glossary == {"田中": "Tanaka", "佐藤": "Sato-sensei"}
    assert res.cost_usd > 0


def test_not_logged_in_raises(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "nologin")
    with pytest.raises(ClaudeError, match="login"):
        translate_cues([Cue(0, 1, ["a"])], "vi", runner=ClaudeRunner(exe=fake_claude))


def test_extract_json_variants():
    assert extract_json('```json\n{"1": "a"}\n```') == {"1": "a"}
    assert extract_json('Kết quả: {"2": "b"} xong') == {"2": "b"}
    with pytest.raises(ValueError):
        extract_json("không có json")


def test_parse_glossary_text():
    g = parse_glossary_text("# ghi chú\n田中 = Tanaka\n東京=Tokyo; 佐藤 = Sato\nlinh tinh")
    assert g == {"田中": "Tanaka", "東京": "Tokyo", "佐藤": "Sato"}


def test_wrap_text():
    lines = wrap_text("Tanaka-san ơi, hôm nay anh có rảnh không, đi ăn trưa với em nhé", "vi")
    assert len(lines) == 2 and all(len(l) <= 42 for l in lines)
    assert wrap_text("Ngắn thôi", "vi") == ["Ngắn thôi"]


def test_join_lines_cjk():
    assert join_lines(["一つ屋根の下 中睦", "まじく暮らす女と女"]) == "一つ屋根の下 中睦まじく暮らす女と女"
    assert join_lines(["Hello there,", "friend"]) == "Hello there, friend"
    assert join_lines(["新作が出るたびに sns で話", "題になる"]) == "新作が出るたびに sns で話題になる"


def _char_words(text, start, step):
    return [Word(start + i * step, start + (i + 1) * step - 0.01, ch) for i, ch in enumerate(text)]


def test_split_cue_uses_word_times():
    a, b = "整形じゃねえわ", "兄貴は姉貴になるってことでオッケー?"
    words = _char_words(a, 10.0, 0.1) + _char_words(b, 10.0 + len(a) * 0.1 + 0.2, 0.1)
    cue = Cue(10.0, words[-1].end, [a + b], words=words)
    parts = split_cue(cue, a + b, [(a, "Không phải phẫu thuật đâu."), (b, "Vậy là anh thành chị à?")])
    assert len(parts) == 2
    (s1, e1, t1), (s2, e2, t2) = parts
    assert s1 == 10.0 and e2 == cue.end
    # mốc tách nằm trong khoảng nghỉ giữa わ và 兄
    assert words[len(a) - 1].end - 0.05 <= e1 < s2 <= words[len(a)].start + 0.01
    assert t2.startswith("Vậy")


def test_split_cue_without_words_is_proportional():
    cue = Cue(0.0, 4.0, ["xxxx yyyy"])
    (s1, e1, _), (s2, e2, _) = split_cue(cue, "xxxx yyyy", [("xxxx", "A"), (" yyyy", "B")])
    assert abs(s2 - 2.0) < 1e-6 and e1 < s2 and e2 == 4.0


def test_split_cue_merges_tiny_part():
    t = "a " + "b" * 31
    parts = split_cue(Cue(0.0, 3.0, [t]), t, [("a", "A."), (" " + "b" * 31, "B.")])
    assert len(parts) == 1 and parts[0][2] == "A. B."


def test_translate_splits_merged_line(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "split")
    cues = [Cue(0, 2, ["一つ目"]), Cue(3, 7, ["質問ですか|はいそうです"]), Cue(8, 9, ["終わり"])]
    res = translate_cues(cues, "vi", "ja", ClaudeRunner(exe=fake_claude), batch_size=10)
    assert res.splits == 1 and len(res.cues) == 4
    assert res.cues[1].text == "VI: 質問ですか" and res.cues[2].text == "VI: はいそうです"
    assert res.cues[1].end < res.cues[2].start and res.cues[2].end == 7


def test_reading_speed_budget_and_shorten_pass(fake_claude, monkeypatch, tmp_path):
    """Mỗi câu gửi kèm max_chars; câu dịch vẫn quá nhanh thì được kéo dài vào khoảng trống,
    còn không đủ thì Claude rút gọn."""
    monkeypatch.setenv("FAKE_MODE", "long")
    cues = [Cue(0.0, 1.0, ["田中さん、行くよ"]), Cue(1.1, 2.1, ["待って"]), Cue(10.0, 13.0, ["終わり"])]
    res = translate_cues(cues, "vi", "ja", ClaudeRunner(exe=fake_claude), batch_size=10)
    sent = json.loads((tmp_path / "state.lines").read_text(encoding="utf-8"))
    # hiện được: câu 0 tới 1.02 (trước câu sau) -> 17 ký tự; câu 1 tới 3.1 (+1 giây) -> 34; câu 2 tới 14 -> 68
    assert [l["max_chars"] for l in sent] == [17, 34, 68]
    assert res.max_cps == 17
    # câu 0, 1 sát nhau, không kéo dài được -> rút gọn; câu 2 đủ thời gian -> giữ nguyên
    assert res.cues[0].text == "Ngắn." and res.cues[1].text == "Ngắn."
    assert res.cues[2].text.startswith("Một câu dịch")
    assert res.shortened == 2 and res.too_fast == 0
    asked = json.loads((tmp_path / "state.shorten").read_text(encoding="utf-8"))
    assert [l["id"] for l in asked["lines"]] == [0, 1] and asked["lines"][0]["original"] == "田中さん、行くよ"


def test_reading_speed_check_can_be_disabled(fake_claude, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MODE", "long")
    cues = [Cue(0.0, 1.0, ["田中さん"]), Cue(1.1, 2.1, ["待って"])]
    res = translate_cues(cues, "vi", "ja", ClaudeRunner(exe=fake_claude), max_cps=0)
    sent = json.loads((tmp_path / "state.lines").read_text(encoding="utf-8"))
    assert all("max_chars" not in l for l in sent)
    assert not (tmp_path / "state.shorten").exists() and res.shortened == 0
    assert [c.end for c in res.cues] == [1.0, 2.1]


# ------------------------------------------------------------------ Gemini qua Antigravity CLI (agy)

FAKE_AGY = r'''#!{py}
# agy giả: đọc 1 tin nhắn stream-json, tách luật / dữ liệu rồi nhờ claude giả trả lời
import json, os, subprocess, sys
sys.stdin.reconfigure(encoding="utf-8"); sys.stdout.reconfigure(encoding="utf-8")
args = sys.argv[1:]
assert args[args.index("--input-format") + 1] == "stream-json"
assert args[args.index("--output-format") + 1] == "stream-json"
assert "-p" not in args  # chế độ stream-json bỏ qua prompt trên dòng lệnh
open(os.environ["FAKE_STATE"] + ".agy", "a", encoding="utf-8").write(json.dumps(args) + "\n")
ev = json.loads(sys.stdin.readline())
assert ev["event"] == "user"
msg = ev["message"]["content"]
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "noauth":
    print("Error: authentication required", file=sys.stderr)
    sys.exit(1)
if mode == "tool":
    print(json.dumps({{"event": "result", "result": {{"status": "SUCCESS", "response": "",
                     "denied_actions": [{{"action": "command", "display_name": "RunCommand"}}]}}}}))
    sys.exit(0)
if mode == "quota":
    print(json.dumps({{"event": "result", "result": {{"status": "ERROR", "response": "",
                     "error": "RESOURCE_EXHAUSTED: weekly limit reached"}}}}))
    sys.exit(1)
assert "Do not use any tools" in msg
system = msg.split("\n\nYou are used as a plain text-in/text-out function.")[0]
payload = msg.split("<input>\n", 1)[1].split("\n</input>", 1)[0]
p = subprocess.run([sys.executable, os.environ["FAKE_CLAUDE"], "-p", "--tools", "", "--no-session-persistence",
                    "--system-prompt", system, "instr"], input=payload, capture_output=True, text=True,
                   encoding="utf-8")
res = json.loads(p.stdout)
print(json.dumps({{"event": "message", "text": "..."}}))
print(json.dumps({{"event": "result", "result": {{"conversation_id": "c1", "status": "SUCCESS",
                 "response": res["result"], "usage": {{"total_tokens": 100}}}}}}, ensure_ascii=False))
'''


@pytest.fixture
def fake_agy(tmp_path, monkeypatch, fake_claude):
    exe = tmp_path / "agy.py"
    exe.write_text(FAKE_AGY.format(py=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_CLAUDE", fake_claude)
    return str(exe)


def test_translate_with_gemini_agy(fake_agy, tmp_path):
    from vsub.translate import AgyRunner

    cues = [Cue(i, i + 1, [f"田中さん、こんにちは {i}"]) for i in range(5)]
    runner = AgyRunner(exe=fake_agy, model="gemini-3.8-flash-high")
    msgs = []
    res = translate_cues(cues, "vi", "ja", runner, batch_size=3, status=msgs.append)
    assert res.missing == 0
    assert all(c.text.startswith("VI: Tanaka") for c in res.cues)
    assert res.glossary["田中"] == "Tanaka"
    assert res.cost_usd == 0 and runner.total_tokens > 0
    assert any(m.startswith("Gemini đang dịch") for m in msgs)
    calls = [json.loads(l) for l in (tmp_path / "state.agy").read_text(encoding="utf-8").splitlines()]
    assert all(c[c.index("--model") + 1] == "gemini-3.8-flash-high" for c in calls)


def test_agy_default_model_passes_no_flag(fake_agy, tmp_path):
    from vsub.translate import AgyRunner

    AgyRunner(exe=fake_agy).ask("Rules about terminology.", "Do it.", "{}")
    args = json.loads((tmp_path / "state.agy").read_text(encoding="utf-8").splitlines()[0])
    assert "--model" not in args


def test_agy_not_logged_in_stops(fake_agy, monkeypatch):
    from vsub.translate import AgyRunner

    monkeypatch.setenv("FAKE_MODE", "noauth")
    with pytest.raises(ClaudeError, match="login"):
        translate_cues([Cue(0, 1, ["a"])], "vi", runner=AgyRunner(exe=fake_agy))


def test_agy_quota_error_is_explained(fake_agy, monkeypatch):
    from vsub.translate import AgyRunner

    monkeypatch.setenv("FAKE_MODE", "quota")
    with pytest.raises(ClaudeError, match="quota"):
        AgyRunner(exe=fake_agy).ask("s", "i", "{}")


def test_make_runner(fake_claude, monkeypatch):
    from vsub.translate import AgyRunner, make_runner

    monkeypatch.setattr("vsub.translate.find_agy", lambda: None)
    with pytest.raises(ClaudeError, match="Antigravity"):
        make_runner("gemini")
    monkeypatch.setattr("vsub.translate.find_claude", lambda: fake_claude)
    r = make_runner("claude", "opus")
    assert isinstance(r, ClaudeRunner) and r.model == "opus"
    with pytest.raises(ValueError):
        make_runner("gpt")


def test_agy_tool_attempt_is_reported(fake_agy, monkeypatch):
    from vsub.translate import AgyRunner

    monkeypatch.setenv("FAKE_MODE", "tool")
    with pytest.raises(ClaudeError, match="RunCommand"):
        AgyRunner(exe=fake_agy).ask("s", "i", "{}")


@pytest.mark.parametrize("engine,model,tag", [
    ("claude", "sonnet", "claude-sonnet"),
    ("claude", None, "claude-sonnet"),
    ("claude", "claude-opus-5-5", "claude-opus-5-5"),
    ("gemini", "", "gemini"),
    ("gemini", "gemini-3.8-flash-high", "gemini-3.8-flash-high"),
    ("gemini", "Some Model/x", "gemini-some-model-x"),
])
def test_model_tag(engine, model, tag):
    from vsub.translate import model_tag

    assert model_tag(engine, model) == tag


def test_translated_path_puts_model_before_language():
    from vsub.translate import translated_path

    assert translated_path("/a/phim.srt", "vi") == "/a/phim.vi.srt"
    assert translated_path("/a/phim.srt", "vi", "claude-opus") == "/a/phim.claude-opus.vi.srt"


def test_latest_translation_picks_newest_of_any_model(tmp_path):
    import os

    from vsub.translate import latest_translation, translations_of

    src = tmp_path / "[Doraemon] phim.srt"   # ngoặc vuông: không được hiểu là mẫu glob
    src.write_text("", encoding="utf-8")
    old = tmp_path / "[Doraemon] phim.vi.srt"
    a = tmp_path / "[Doraemon] phim.claude-sonnet.vi.srt"
    b = tmp_path / "[Doraemon] phim.gemini-3.8-flash-high.vi.srt"
    for i, f in enumerate((old, a, b)):
        f.write_text("x", encoding="utf-8")
        os.utime(f, (1000 + i, 1000 + i))
    (tmp_path / "[Doraemon] phim.claude-sonnet.en.srt").write_text("x", encoding="utf-8")  # ngôn ngữ khác
    (tmp_path / "[Doraemon] phim 2.vi.srt").write_text("x", encoding="utf-8")              # phim khác
    (tmp_path / "[Doraemon] phim.ban-cu.vi.srt").write_text("x", encoding="utf-8")         # không phải tên model
    assert translations_of(str(src), "vi") == [str(b), str(a), str(old)]
    assert latest_translation(str(src), "vi") == str(b)
    os.utime(a, (2000, 2000))
    assert latest_translation(str(src), "vi") == str(a)
    assert latest_translation(str(src), "ko") is None
