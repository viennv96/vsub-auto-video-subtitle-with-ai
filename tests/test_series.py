import json
import stat
import sys

import pytest

from vsub import series
from vsub.formats import to_srt
from vsub.models import Cue
from vsub.series import (
    Profile, describe_group, episode_of, html_to_text, open_profile, order_files, parse_name,
    pending_sources, prepare, remember, series_key,
)
from vsub.translate import ClaudeRunner, translate_cues

# Claude giả: ghi lại từng lần gọi (loại + dữ liệu stdin) vào FAKE_LOG
FAKE = r'''#!{py}
import json, os, sys
sys.stdin.reconfigure(encoding="utf-8"); sys.stdout.reconfigure(encoding="utf-8")  # như claude thật
args = sys.argv[1:]
system = args[args.index("--system-prompt") + 1]
data = json.loads(sys.stdin.read())
kind = "absorb" if "reference material" in system else ("prep" if "terminology" in system else "translate")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"kind": kind, "data": data}}, ensure_ascii=False) + "\n")
if kind == "absorb" and "page_text" in data:
    res = {{"terms": [{{"src": "田中", "dst": "Tanaka-san"}}], "pronouns": [], "notes": "office drama"}}
elif kind == "absorb":
    res = {{"terms": [{{"src": "佐藤", "dst": "Sato"}}, {{"src": "", "dst": "Giám đốc Kim"}}],
           "pronouns": [{{"speaker": "Tanaka", "listener": "Sato", "self": "tôi", "address": "chị"}}],
           "summary": "Tập trước", "ending": "Hai người làm lành"}}
elif kind == "prep":
    res = {{"terms": [{{"src": "田中", "dst": "Điền Trung"}}, {{"src": "鈴木", "dst": "Suzuki"}}],
           "pronouns": [{{"speaker": "Tanaka", "listener": "Sato", "self": "anh", "address": "em", "changed": True}},
                        {{"speaker": "Sato", "listener": "Tanaka", "self": "em", "address": "anh"}}],
           "notes": "romance", "summary": "Tập này", "ending": "Cuối tập này"}}
else:
    res = {{str(l["id"]): "VI: " + l["text"] for l in data["lines"]}}
print(json.dumps({{"type": "result", "is_error": False, "result": json.dumps(res, ensure_ascii=False), "total_cost_usd": 0.01}}))
'''


@pytest.fixture
def claude(tmp_path, monkeypatch):
    exe = tmp_path / "claude.py"  # .py: ClaudeRunner chạy bằng Python hiện tại (cả trên Windows)
    exe.write_text(FAKE.format(py=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))

    def calls(kind=None):
        if not log.exists():
            return []
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
        return [r["data"] for r in rows if kind is None or r["kind"] == kind]

    return ClaudeRunner(exe=str(exe)), calls


# ------------------------------------------------------------------ tên tệp

@pytest.mark.parametrize("name, title, marker, ep", [
    # tên thật của người dùng: số tập + phiên bản + mã thời gian + độ phân giải không có "p"
    ("ep.1.v1.1677999252.720 [ep.1.v1.1677999252.720].mp4", "", "ep", (0, 1)),
    ("ep.3.v3.1678024619.720 [ep.3.v3.1678024619.720].vi.srt", "", "ep", (0, 3)),
    ("Phim.Hay.S01E01.1080p.WEB-DL.x264-RARBG.mkv", "phim hay", "", (1, 1)),
    ("Phim Hay - Tap 2 [720p].mp4", "phim hay", "", (0, 2)),
    ("Dune (2021).mkv", "dune 2021", "", None),
    ("Dune (1984).mkv", "dune 1984", "", None),
    ("The Batman.mp4", "the batman", "", None),
    ("斗破苍穹 第02集.mp4", "斗破苍穹", "", (0, 2)),
    ("Tiêu Viêm tập 6.mp4", "tiêu viêm", "", (0, 6)),
    ("Tập 05 - Tiêu Viêm.mp4", "tiêu viêm", "", (0, 5)),
    ("Toy Story 3.mp4", "toy story", "", (0, 3)),
    ("Movie.en.srt", "movie", "", None),
])
def test_parse_name(name, title, marker, ep):
    info = parse_name(f"/phim/{name}")
    assert (info.title, info.marker, info.episode) == (title, marker, ep)


def test_season_folder_and_untitled_key():
    assert episode_of("/b/Season 2/Tap01.mp4") == (2, 1)
    assert series_key("/b/Season 2/Tap01.mp4") == series_key("/b/Tap02.mp4")  # bỏ qua thư mục mùa
    assert series_key("/b/Tập 1.mp4") == series_key("/b/Tap 2.mp4")         # có dấu / không dấu
    assert series_key("/b/Tap01.mp4") != series_key("/c/Tap01.mp4")          # bộ khác thư mục
    assert series_key("/b/ep.1.mp4") != series_key("/b/Tap 1.mp4")           # khác cách đặt tên


def test_order_files_natural_and_grouped():
    files = ["/x/ep.10.mp4", "/x/ep.2.mp4", "/x/Avatar.2009.mp4", "/x/ep.1.mp4"]
    assert order_files(files) == [["/x/Avatar.2009.mp4"], ["/x/ep.1.mp4", "/x/ep.2.mp4", "/x/ep.10.mp4"]]
    # tệp ngoài thư mục mùa là mùa 1, đứng trước mùa 2
    assert order_files(["/b/Season 2/Tap01.mp4", "/b/Tap02.mp4", "/b/Tap01.mp4"]) == [
        ["/b/Tap01.mp4", "/b/Tap02.mp4", "/b/Season 2/Tap01.mp4"]]
    # tên đặt không đều vẫn theo số tập
    assert order_files(["/p/Phim Hay - Tap 2 [720p].mp4", "/p/Phim.Hay.S01E01.mkv"]) == [
        ["/p/Phim.Hay.S01E01.mkv", "/p/Phim Hay - Tap 2 [720p].mp4"]]


def test_describe_group():
    assert describe_group(["/x/Avatar.2009.mp4"]) == ("Phim lẻ “avatar 2009”", [])
    label, warns = describe_group(["/x/ep.1.mp4", "/x/ep.2.mp4"])
    assert label == "Chuỗi thư mục “x” (ep) — 2 tập" and warns == []
    _, warns = describe_group(["/x/ep.1.mp4", "/x/ep.2.mp4", "/x/ep.30.mp4"])
    assert any("rời rạc" in w for w in warns)


# ------------------------------------------------------------------ hồ sơ

def test_merge_keeps_names_stable_unless_authoritative(tmp_path):
    p = Profile(str(tmp_path / "ho-so-phim.json"))
    p.merge("vi", {"terms": [{"src": "田中", "dst": "Tanaka"}]})
    p.merge("vi", {"terms": [{"src": "田中", "dst": "Điền Trung"}]})
    assert p.bucket("vi")["glossary"]["田中"] == "Tanaka"
    p.merge("vi", {"terms": [{"src": "田中", "dst": "Tanaka-san"}]}, authoritative=True)
    assert p.bucket("vi")["glossary"]["田中"] == "Tanaka-san"
    p.notes_text = "田中 = Tanaka-kun"  # người dùng ghi thì luôn thắng
    assert p.story("vi")["glossary"]["田中"] == "Tanaka-kun"


def test_pronoun_change_applies_from_its_episode(tmp_path):
    p = Profile(str(tmp_path / "ho-so-phim.json"))
    pair = {"speaker": "A", "listener": "B"}
    p.merge("vi", {"pronouns": [{**pair, "self": "tôi", "address": "cô"}]}, (0, 1))
    p.merge("vi", {"pronouns": [{**pair, "self": "anh", "address": "em"}]}, (0, 3))  # không "changed" -> bỏ
    p.merge("vi", {"pronouns": [{**pair, "self": "anh", "address": "em", "changed": True}]}, (0, 5))
    assert p.story("vi", (0, 4))["pronouns"][0]["self"] == "tôi"
    assert p.story("vi", (0, 6))["pronouns"][0]["self"] == "anh"


def test_profile_roundtrip_and_naming(tmp_path):
    video = tmp_path / "Avatar.2009.1080p.mkv"
    prof = open_profile(str(video))
    assert prof.path == str(tmp_path / "ho-so-phim.avatar 2009.json") and prof.title == "avatar 2009"
    prof.summary, prof.links = "Phim về Pandora", ["https://example.com"]
    prof.save()
    again = open_profile(str(tmp_path / "Avatar.2009.mkv"))
    assert again.summary == "Phim về Pandora" and again.links == ["https://example.com"]


def test_html_to_text():
    html = ("<html><head><title>x</title><script>var a=1</script></head><body><h1>Tên</h1>"
            "<p>Đoạn&nbsp;một</p><style>.a{}</style><div>Hai</div></body></html>")
    assert html_to_text(html) == "Tên\nĐoạn một\nHai"


# ------------------------------------------------------------------ nạp dồn + dịch nhiều tập

def _srt(path, texts):
    path.write_text(to_srt([Cue(i * 2, i * 2 + 1, [t]) for i, t in enumerate(texts)]), encoding="utf-8")


def test_multi_episode_flow(tmp_path, claude, monkeypatch):
    runner, calls = claude
    monkeypatch.setattr(series, "fetch_page_text", lambda url: "Trang giới thiệu phim " * 20)
    ep1, ep2, ep3 = (tmp_path / f"ep.{n}.v1.16779{n}9252.720.srt" for n in (1, 2, 3))
    _srt(ep1, ["田中さん", "佐藤さん"])
    _srt(tmp_path / "ep.1.v1.1677919252.720.vi.srt", ["Tanaka-san", "Chị Sato"])
    _srt(tmp_path / "Avatar.2009.vi.srt", ["Phim khác, không được nạp"])
    _srt(ep2, ["田中、鈴木です"])
    _srt(ep3, ["三話"])

    prof = open_profile(str(ep2))
    assert prof.path == str(tmp_path / "ho-so-phim.ep.json")
    prof.links = ["https://example.com/phim"]
    key = series_key(str(ep2))
    out2 = str(tmp_path / "ep.2.v1.1677929252.720.vi.srt")

    # 1) nạp link + bản dịch tập 1 (phim khác cùng thư mục bị loại)
    assert prepare(prof, key, "vi", "ja", runner, out2) == 2
    absorbed = calls("absorb")
    assert len(absorbed) == 2 and not any("không được nạp" in json.dumps(c, ensure_ascii=False) for c in absorbed)
    g = prof.bucket("vi")
    assert g["glossary"] == {"田中": "Tanaka-san", "佐藤": "Sato"} and g["names"] == ["Giám đốc Kim"]
    assert prepare(prof, key, "vi", "ja", runner, out2) == 0  # đã nạp -> không đọc lại

    # 2) dịch tập 2: tên đã chốt giữ nguyên, xưng hô đổi trong tập này được áp
    story = prof.story("vi", episode_of(str(ep2)))
    assert story["previous_episodes"][0]["ending"] == "Hai người làm lành"
    res = translate_cues([Cue(0, 1, ["田中、鈴木です"])], "vi", "ja", runner, story=story)
    req = calls("translate")[-1]
    assert req["glossary"]["田中"] == "Tanaka-san" and req["glossary"]["鈴木"] == "Suzuki"
    assert {"speaker": "Tanaka", "listener": "Sato", "self": "anh", "address": "em", "note": ""} in req["pronouns"]
    assert req["story"]["previous_episodes"][0]["episode"] == "1"
    (tmp_path / "ep.2.v1.1677929252.720.vi.srt").write_text(to_srt(res.cues), encoding="utf-8")
    remember(prof, "vi", episode_of(str(ep2)), res, out2)

    # 3) sang tập 3: bản dịch tập 2 vừa ghi đã nằm trong hồ sơ -> không gọi nạp nữa
    n_absorb = len(calls("absorb"))
    assert prepare(Profile.load(prof.path), key, "vi", "ja", runner, str(ep3).replace(".srt", ".vi.srt")) == 0
    assert len(calls("absorb")) == n_absorb
    saved = Profile.load(prof.path)

    def tanaka_to_sato(ep):
        return next(p for p in saved.story("vi", ep)["pronouns"] if p["speaker"] == "Tanaka")["self"]

    assert tanaka_to_sato((0, 3)) == "anh"
    assert tanaka_to_sato((0, 1)) == "tôi"  # tập 1 vẫn là cách xưng cũ
    assert "2" in saved.bucket("vi")["episodes"]

    # 4) người dùng sửa tay bản dịch tập 1 -> tự đọc lại đúng tệp đó (tập 2 vẫn đã nạp)
    _srt(tmp_path / "ep.1.v1.1677919252.720.vi.srt", ["Tanaka-san", "Sato-senpai"])
    pend = pending_sources(saved, key, "vi")
    assert [s.name for s in pend] == ["ep.1.v1.1677919252.720.vi.srt"]


def test_cli_translate_uses_profile(tmp_path, claude, monkeypatch):
    from vsub import cli

    runner, calls = claude
    monkeypatch.setenv("VSUB_CLAUDE", runner.exe)
    _srt(tmp_path / "ep.1.srt", ["田中さん"])
    _srt(tmp_path / "ep.1.gemini-3.8-flash-high.vi.srt", ["Tanaka-san"])  # bản dịch tập trước, tên có model
    _srt(tmp_path / "ep.2.srt", ["鈴木です"])
    cli.main(["translate", str(tmp_path / "ep.2.srt"), "--from", "ja", "--to", "vi",
              "--read-prev", "--use-profile", "--learn"])
    assert (tmp_path / "ep.2.claude-sonnet.vi.srt").exists()
    prof = Profile.load(str(tmp_path / "ho-so-phim.ep.json"))
    assert sorted(prof.loaded["vi"]) == ["ep.1.gemini-3.8-flash-high.vi.srt", "ep.2.claude-sonnet.vi.srt"]
    assert len(calls("absorb")) == 1
    cli.main(["translate", str(tmp_path / "ep.2.srt"), "--to", "vi", "--no-profile"])
    assert len(calls("absorb")) == 1  # --no-profile: không nạp gì thêm



def test_cli_translate_defaults_do_not_touch_profile(tmp_path, claude, monkeypatch):
    """Mặc định (không bật tuỳ chọn nào): không đọc tập trước, không ghi gì Claude học vào hồ sơ."""
    from vsub import cli

    runner, calls = claude
    monkeypatch.setenv("VSUB_CLAUDE", runner.exe)
    _srt(tmp_path / "ep.1.srt", ["田中さん"])
    _srt(tmp_path / "ep.1.vi.srt", ["Tanaka-san"])
    _srt(tmp_path / "ep.2.srt", ["鈴木です"])
    cli.main(["translate", str(tmp_path / "ep.2.srt"), "--from", "ja", "--to", "vi"])
    assert (tmp_path / "ep.2.claude-sonnet.vi.srt").exists()
    assert len(calls("absorb")) == 0                      # không đọc lại phụ đề tập 1
    path = tmp_path / "ho-so-phim.ep.json"
    if path.exists():                                     # có thể chưa từng tạo
        prof = Profile.load(str(path))
        assert not prof.loaded.get("vi") and not prof.learned.get("vi", {}).get("glossary")


def test_story_without_learned_keeps_user_fields(tmp_path):
    p = Profile(str(tmp_path / "h.json"))
    p.summary = "Phim công sở"
    p.pronouns_text = "Akira → Sonoko: anh / em"
    p.notes_text = "小森 = Komori"
    p.bucket("vi")["glossary"]["沢"] = "Sawa"
    p.bucket("vi")["episodes"]["1"] = {"order": [1, 1], "summary": "x", "ending": "y"}
    s = p.story("vi", (1, 2), learned=False)
    assert s["summary"] == "Phim công sở" and s["user_pronouns"].startswith("Akira")
    assert s["glossary"] == {"小森": "Komori"}
    assert s["previous_episodes"] == [] and s["pronouns"] == []
    full = p.story("vi", (1, 2))
    assert full["glossary"]["沢"] == "Sawa" and full["previous_episodes"]


@pytest.mark.parametrize("name", ["ep.3.claude-sonnet.vi.srt", "ep.3.gemini-3.8-flash-high.vi.srt",
                                  "ep.3.gemini.vi.srt", "ep.3.vi.srt"])
def test_model_tag_in_translation_name_keeps_episode(tmp_path, name):
    from vsub.series import _original_of

    assert series_key(name) == series_key("ep.3.mkv")
    assert episode_of(name) == (0, 3)
    (tmp_path / "ep.3.srt").write_text("", encoding="utf-8")
    assert _original_of(str(tmp_path / name), "vi") == str(tmp_path / "ep.3.srt")


def test_model_word_in_title_without_language_is_kept():
    assert series_key("Project.Gemini.S01E02.mkv") != series_key("Project.S01E02.mkv")
