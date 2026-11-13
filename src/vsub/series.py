"""Hồ sơ phim: nhận chuỗi phim theo tên tệp và nhớ những gì Claude đã học qua từng tập.

- Tên chuỗi = tên tệp bỏ số tập, nhãn bản phát hành (1080p, WEB-DL, x264, v2, mã số dài…)
  và đuôi ngôn ngữ. Chỉ các tệp cùng tên chuỗi, số tập nối tiếp mới là phim nhiều tập;
  phim lẻ nằm chung thư mục có hồ sơ riêng.
- Tên tệp chỉ có số tập (ep.1…, Tập 2, 第3集) thì chuỗi gắn với thư mục chứa nó
  (bỏ qua thư mục mùa kiểu "Season 2").
- Hồ sơ là tệp JSON cạnh video (ho-so-phim.<tên chuỗi>.json): phần người dùng điền
  (nội dung phim, link, tệp đã dịch, xưng hô, tên riêng) và phần Claude học được theo
  từng ngôn ngữ đích. Mỗi nguồn (link, bản dịch một tập) chỉ đọc một lần rồi ghi vào hồ sơ
  ("nạp dồn"); tệp bị sửa sau lần nạp thì tự đọc lại.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Callable

from .formats import parse_srt
from .translate import MODEL_TAG_RE as _MODEL_TAG
from .translate import Cancelled, ClaudeError, ClaudeRunner, absorb_source, join_lines, parse_glossary_text

PROFILE_PREFIX = "ho-so-phim"
SUB_EXTS = (".srt", ".vtt")

_LANG_SUFFIX = re.compile(
    r"\.(?:vi|vie|vn|en|eng|zh|zho|chi|chs|cht|sc|tc|zh-cn|zh-tw|zh-hans|zh-hant|ko|kor|ja|jpn|th|tha)$",
    re.I,
)
# Từ chỗ gặp một nhãn này trở về sau là thông tin bản phát hành, không phải tên phim.
_RELEASE = re.compile(
    r"(?<![^\W_])(?:\d{3,4}p|[48]k|uhd|hdr(?:10)?|x26[45]|h\.?26[45]|hevc|avc|av1|10bit|web[-. ]?dl"
    r"|webrip|blu-?ray|bdrip|brrip|hdtv|dvdrip|hdrip|remux|e?ac3|aac(?:\d\.\d)?|ddp?\d\.\d|atmos"
    r"|vietsub|engsub|amzn|dsnp|hmax|v\d{1,2}|360|480|540|576|720|1080|1440|2160|\d{5,})(?![^\W_])",
    re.I,
)
_EPISODE = (  # (mẫu, có số mùa không)
    (re.compile(r"s(\d{1,2})[ ._-]*e(\d{1,4})(?!\d)"), True),
    (re.compile(r"(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)"), True),
    (re.compile(r"(?<![^\W\d_])(?:episode|ep|tập|tap|e)[ ._-]*(\d{1,4})(?!\d)"), False),
    (re.compile(r"第\s*(\d{1,4})\s*[集话話]"), False),
)
_SEASON_IN_DIR = re.compile(r"(?:season|mùa|phần)[ ._-]*(\d{1,2})(?!\d)|^s(\d{1,2})$", re.I)
_SEASON_DIR = re.compile(r"^(?:season|mùa|phần|s)[ ._-]*\d{1,2}(?:\s*[\[(][^\])]*[\])])?$", re.I)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _plain(s: str) -> str:
    """Bỏ dấu: tập -> tap (để "Tập 1" và "Tap 2" cùng một chuỗi)."""
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------ tên tệp -> chuỗi, số tập

@dataclass(frozen=True)
class NameInfo:
    title: str                         # tên chuỗi, "" khi tên tệp chỉ có số tập
    marker: str                        # chữ đánh dấu tập khi không có tên (ep, tap, 第集…)
    episode: tuple[int, int] | None    # (mùa, tập); mùa 0 = không rõ


def parse_name(path: str) -> NameInfo:
    p = _nfc(str(path))
    name = os.path.splitext(os.path.basename(p))[0]
    if _LANG_SUFFIX.search(name):
        while _LANG_SUFFIX.search(name):
            name = _LANG_SUFFIX.sub("", name)
        name = _MODEL_TAG.sub("", name)  # phim.claude-sonnet.vi -> phim
    name = re.sub(r"[\[{][^\]}]*[\]}]", " ", name.lower())
    name = re.sub(r"\((?!(?:19|20)\d\d\))[^)]*\)", " ", name)  # bỏ ngoặc tròn, trừ năm
    season, ep, marker = 0, None, ""
    for pat, has_season in _EPISODE:
        m = pat.search(name)
        if m:
            season, ep = (int(m[1]), int(m[2])) if has_season else (0, int(m[1]))
            if not has_season:
                marker = _plain(re.sub(r"[\d\s._-]", "", m[0]))
            name = f"{name[:m.start()]} {name[m.end():]}"
            break
    m = _RELEASE.search(name)
    if m:
        name = name[: m.start()]
    words = [w for w in re.split(r"[\s._\-–—,()]+", name) if w]
    if ep is None and words and re.fullmatch(r"\d{1,3}", words[-1]):
        ep = int(words.pop())
    if not season:
        for d in reversed(re.split(r"[\\/]", p)[:-1]):  # cả "/" lẫn "\\" (Windows)
            m = _SEASON_IN_DIR.search(d.lower())
            if m:
                season = int(m[1] or m[2])
                break
    title = " ".join(words)
    return NameInfo(title, "" if title else marker, (season, ep) if ep is not None else None)


def season_folder(path: str) -> str:
    """Thư mục đại diện cho chuỗi: thư mục chứa tệp, bỏ qua các tầng thư mục mùa."""
    d = os.path.dirname(os.path.abspath(path))
    while _SEASON_DIR.match(_nfc(os.path.basename(d))) and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    return d


@dataclass(frozen=True)
class SeriesKey:
    title: str
    marker: str = ""
    folder: str = ""   # chỉ dùng khi không có tên chuỗi

    def label(self) -> str:
        if self.title:
            return f"“{self.title}”"
        return f"thư mục “{os.path.basename(self.folder) or self.folder}”" + (f" ({self.marker})" if self.marker else "")

    def profile_name(self) -> str:
        part = self.title or self.marker
        return f"{PROFILE_PREFIX}.{part}.json" if part else f"{PROFILE_PREFIX}.json"


def series_key(path: str) -> SeriesKey:
    info = parse_name(path)
    if info.title:
        return SeriesKey(info.title)
    return SeriesKey("", info.marker, season_folder(path))


def episode_of(path: str) -> tuple[int, int] | None:
    return parse_name(path).episode


def episode_label(ep) -> str:
    return "" if not ep else (f"S{ep[0]}E{ep[1]}" if ep[0] else str(ep[1]))


def _season_fill(eps) -> Callable:
    """Chuỗi có chia mùa thì tệp không nằm trong mùa nào (mùa 0) là mùa 1."""
    fill = 1 if any(e and e[0] for e in eps) else 0
    return lambda e: (e[0] or fill, e[1]) if e else None


def natural_key(text: str) -> list:
    """Khóa sắp xếp theo tên mà số so như số: ep.2 đứng trước ep.10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", _nfc(text).lower())]


def order_files(paths: list[str]) -> list[list[str]]:
    """Gom theo chuỗi; trong chuỗi xếp theo mùa, tập (tên tệp đặt không đều vẫn đúng thứ tự);
    các chuỗi xếp theo tên tệp đầu tiên."""
    groups: dict[SeriesKey, list[str]] = {}
    for p in paths:
        groups.setdefault(series_key(p), []).append(p)
    out = []
    for files in groups.values():
        fill = _season_fill([episode_of(f) for f in files])
        out.append(sorted(files, key=lambda f: (
            fill(episode_of(f)) is None, fill(episode_of(f)) or (0, 0), natural_key(os.path.basename(f)))))
    return sorted(out, key=lambda g: natural_key(os.path.basename(g[0])))


def describe_group(files: list[str]) -> tuple[str, list[str]]:
    """(nhãn, cảnh báo) cho một nhóm tệp cùng chuỗi."""
    key = series_key(files[0])
    fill = _season_fill([episode_of(f) for f in files])
    eps = [fill(episode_of(f)) for f in files]
    known = sorted({e for e in eps if e})
    warns = []
    if len(known) < 2:
        return f"Phim lẻ {key.label()}", warns
    for season in sorted({e[0] for e in known}):
        nums = [e[1] for e in known if e[0] == season]
        missing = [n for n in range(1, max(nums)) if n not in nums]
        if len(missing) > len(nums):
            warns.append(f"số tập rời rạc ({', '.join(map(str, nums))}), có thể không cùng một phim")
    dup = {e for e in eps if e and eps.count(e) > 1}
    if dup:
        warns.append("trùng tập " + ", ".join(episode_label(e) for e in sorted(dup)))
    if any(e is None for e in eps):
        warns.append("có tệp không đoán được số tập, xếp cuối")
    return f"Chuỗi {key.label()} — {len(files)} tập", warns


# ------------------------------------------------------------------ hồ sơ

def _new_bucket() -> dict:
    return {"glossary": {}, "names": [], "pronouns": [], "notes": "", "episodes": {}}


@dataclass
class Profile:
    path: str
    title: str = ""
    marker: str = ""
    aliases: list[str] = field(default_factory=list)
    # người dùng điền
    summary: str = ""
    links: list[str] = field(default_factory=list)
    ref_files: list[str] = field(default_factory=list)
    pronouns_text: str = ""
    notes_text: str = ""
    # ngôn ngữ gốc của phim (lưu sau lần nhận dạng đầu, các tập sau khỏi phải đoán)
    source_lang: str = ""
    # Claude học được, theo ngôn ngữ đích: {"vi": {glossary, names, pronouns, notes, episodes}}
    learned: dict = field(default_factory=dict)
    # nguồn đã nạp, theo ngôn ngữ đích: {"vi": {"<link hoặc đường dẫn>": {hash, date, episode}}}
    loaded: dict = field(default_factory=dict)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @staticmethod
    def load(path: str) -> "Profile":
        prof = Profile(path)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for k in ("title", "marker", "aliases", "summary", "links", "ref_files", "pronouns_text",
                      "notes_text", "source_lang", "learned", "loaded"):
                if k in data:
                    setattr(prof, k, data[k])
        return prof

    @contextmanager
    def _file_lock(self):
        """Khoá liên tiến trình: nhiều cửa sổ vsub có thể cùng ghi hồ sơ của một phim."""
        try:
            import fcntl
        except ImportError:  # Windows: khoá 1 byte bằng msvcrt
            import msvcrt
            import time as _time

            with open(self.path + ".lock", "a+b") as lk:
                while True:
                    try:
                        lk.seek(0)
                        msvcrt.locking(lk.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        _time.sleep(0.1)
                try:
                    yield
                finally:
                    lk.seek(0)
                    msvcrt.locking(lk.fileno(), msvcrt.LK_UNLCK, 1)
            return
        with open(self.path + ".lock", "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    def absorb(self, other: "Profile") -> None:
        """Gộp những gì cửa sổ khác đã ghi vào hồ sơ trên đĩa (không đè cái mình đang có)."""
        for target, ob in (other.learned or {}).items():
            b = self.bucket(target)
            for k, v in (ob.get("glossary") or {}).items():
                b["glossary"].setdefault(k, v)
            for n in ob.get("names") or []:
                if n not in b["names"]:
                    b["names"].append(n)
            have = {json.dumps(p, sort_keys=True, ensure_ascii=False) for p in b["pronouns"]}
            for p in ob.get("pronouns") or []:
                if json.dumps(p, sort_keys=True, ensure_ascii=False) not in have:
                    b["pronouns"].append(p)
            on = str(ob.get("notes") or "")
            if on and on not in b["notes"]:
                b["notes"] = on if b["notes"] in on else (b["notes"] + " " + on).strip()[-2000:]
            for k, v in (ob.get("episodes") or {}).items():
                b["episodes"].setdefault(k, v)
        for target, items in (other.loaded or {}).items():
            mine = self.loaded.setdefault(target, {})
            for k, v in items.items():
                mine.setdefault(k, v)
        for k in ("title", "marker", "summary", "pronouns_text", "notes_text", "source_lang"):
            if not getattr(self, k) and getattr(other, k):
                setattr(self, k, getattr(other, k))
        for k in ("aliases", "links", "ref_files"):
            cur = getattr(self, k)
            cur += [x for x in getattr(other, k) or [] if x not in cur]

    def reload(self) -> None:
        """Nạp thêm những gì cửa sổ khác vừa học được (gọi trước khi dịch)."""
        if os.path.exists(self.path):
            with self._file_lock():
                self.absorb(Profile.load(self.path))

    def save(self) -> None:
        with self._file_lock():
            if os.path.exists(self.path):
                try:
                    self.absorb(Profile.load(self.path))
                except (OSError, ValueError):
                    pass
            data = {k: getattr(self, k) for k in ("title", "marker", "aliases", "summary", "links", "ref_files",
                                                   "pronouns_text", "notes_text", "source_lang", "learned",
                                                   "loaded")}
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    def has_user_info(self) -> bool:
        return any((self.summary.strip(), self.links, self.ref_files, self.pronouns_text.strip(),
                    self.notes_text.strip()))

    def bucket(self, target: str) -> dict:
        b = self.learned.setdefault(target, _new_bucket())
        for k, v in _new_bucket().items():
            b.setdefault(k, v)
        return b

    def rel(self, path: str) -> str:
        try:
            return os.path.relpath(os.path.abspath(path), self.folder)
        except ValueError:  # khác ổ đĩa
            return os.path.abspath(path)

    def abspath(self, path: str) -> str:
        return os.path.normpath(os.path.join(self.folder, os.path.expanduser(path)))

    # ---- nạp dồn
    def loaded_hash(self, target: str, key: str) -> str | None:
        return (self.loaded.get(target, {}).get(key) or {}).get("hash")

    def mark_loaded(self, target: str, key: str, digest: str, episode=None) -> None:
        self.loaded.setdefault(target, {})[key] = {
            "hash": digest, "date": date.today().isoformat(), "episode": episode_label(episode),
        }

    # ---- học thêm
    def merge(self, target: str, data: dict, episode=None, authoritative: bool = False) -> int:
        """Ghi kết quả Claude vào hồ sơ. `authoritative` (bản dịch đã có, có thể người sửa tay)
        được đè tên riêng và xưng hô cũ; còn lại chỉ thêm cái mới để các tập giữ nhất quán."""
        if not isinstance(data, dict):
            return 0
        b = self.bucket(target)
        added = 0
        for t in data.get("terms") or []:
            if not isinstance(t, dict):
                continue
            src, dst = str(t.get("src") or "").strip(), str(t.get("dst") or "").strip()
            if not dst:
                continue
            if src:
                if (authoritative or src not in b["glossary"]) and b["glossary"].get(src) != dst:
                    b["glossary"][src] = dst
                    added += 1
            elif dst not in b["names"]:
                b["names"].append(dst)
                added += 1
        order = list(episode) if episode else None
        for p in data.get("pronouns") or []:
            if not isinstance(p, dict) or not p.get("speaker") or not p.get("listener"):
                continue
            entry = {k: str(p.get(k) or "").strip() for k in ("speaker", "listener", "self", "address", "note")}
            if not (entry["self"] or entry["address"]):
                continue
            cur = _pick_pronoun(b["pronouns"], entry["speaker"], entry["listener"], episode)
            if cur and (cur["self"], cur["address"]) == (entry["self"], entry["address"]):
                continue
            if cur and not (authoritative or p.get("changed")):
                continue
            entry["from"] = order
            b["pronouns"].append(entry)
            added += 1
        notes = str(data.get("notes") or "").strip()
        if notes and notes not in b["notes"]:
            b["notes"] = (b["notes"] + " " + notes).strip()[-2000:]
        if episode and (data.get("summary") or data.get("ending")):
            b["episodes"][episode_label(episode)] = {
                "order": list(episode),
                "summary": str(data.get("summary") or "").strip(),
                "ending": str(data.get("ending") or "").strip(),
            }
        return added

    def story(self, target: str, episode=None, learned: bool = True) -> dict:
        """Những gì đã biết về phim, đưa cho Claude trước khi dịch tập `episode`.

        learned=False: chỉ dùng những gì người dùng tự điền (nội dung, xưng hô, tên riêng/ghi chú),
        bỏ phần Claude học được ở các tập trước (tên riêng, xưng hô, tóm tắt tập)."""
        if not learned:
            return {
                "summary": self.summary.strip(),
                "user_pronouns": self.pronouns_text.strip(),
                "glossary": parse_glossary_text(self.notes_text),
                "names_used": [], "pronouns": [], "notes": "", "previous_episodes": [],
            }
        b = self.bucket(target)
        glossary = dict(b["glossary"])
        glossary.update(parse_glossary_text(self.notes_text))
        pairs = {(p["speaker"], p["listener"]) for p in b["pronouns"]}
        pronouns = [_pick_pronoun(b["pronouns"], s, l, episode) for s, l in sorted(pairs)]
        eps = sorted(b["episodes"].values(), key=lambda e: tuple(e.get("order") or (0, 0)))
        if episode:
            eps = [e for e in eps if tuple(e.get("order") or (0, 0)) < tuple(episode)]
        return {
            "summary": self.summary.strip(),
            "user_pronouns": self.pronouns_text.strip(),
            "glossary": glossary,
            "names_used": list(b["names"]),
            "pronouns": [{k: p[k] for k in ("speaker", "listener", "self", "address", "note")} for p in pronouns],
            "notes": b["notes"],
            "previous_episodes": [
                {"episode": episode_label(e.get("order")), "summary": e.get("summary", ""), "ending": e.get("ending", "")}
                for e in eps[-5:]
            ],
        }

    def stats(self, target: str) -> str:
        b = {**_new_bucket(), **self.learned.get(target, {})}
        n = len(self.loaded.get(target, {}))
        pairs = len({(p["speaker"], p["listener"]) for p in b["pronouns"]})
        return f"đã nạp {n} nguồn · {len(b['glossary']) + len(b['names'])} tên riêng · {pairs} cặp xưng hô"


def _pick_pronoun(entries: list[dict], speaker: str, listener: str, episode) -> dict | None:
    """Cặp xưng hô áp cho tập `episode`: mục mới nhất bắt đầu từ tập đó trở về trước;
    không có thì lấy mục sớm nhất (cặp mới thấy ở tập sau vẫn hơn là không có gì)."""
    def order(e: dict) -> tuple:
        return tuple(e.get("from") or (0, 0))

    same = sorted((e for e in entries if e["speaker"] == speaker and e["listener"] == listener), key=order)
    if not same:
        return None
    if episode:
        before = [e for e in same if order(e) <= tuple(episode)]
        return before[-1] if before else same[0]
    return same[-1]


def _find_profile(path: str, key: SeriesKey) -> str | None:
    if not key.title:
        p = os.path.join(key.folder, key.profile_name())
        return p if os.path.exists(p) else None
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(4):
        try:
            names = sorted(n for n in os.listdir(d) if n.startswith(PROFILE_PREFIX) and n.endswith(".json"))
        except OSError:
            names = []
        for n in names:
            p = os.path.join(d, n)
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if key.title == data.get("title") or key.title in (data.get("aliases") or []):
                return p
        d = os.path.dirname(d)
    return None


def open_profile(path: str) -> Profile:
    """Hồ sơ của chuỗi chứa `path` (chưa có thì tạo mới trong bộ nhớ, lưu khi dùng tới)."""
    key = series_key(path)
    found = _find_profile(path, key)
    prof = Profile.load(found or os.path.join(season_folder(path), key.profile_name()))
    if not found:
        prof.title, prof.marker = key.title, key.marker
    return prof


# ------------------------------------------------------------------ nguồn để nạp

def file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


@dataclass
class Source:
    kind: str                              # "link" | "translation"
    key: str                               # link, hoặc đường dẫn tính từ thư mục hồ sơ
    digest: str
    path: str = ""                         # tệp bản dịch
    original: str = ""                     # tệp gốc cùng tên (nếu có)
    episode: tuple[int, int] | None = None

    @property
    def name(self) -> str:
        return os.path.basename(self.path) if self.path else self.key


def _scan_subs(folder: str, target: str, depth: int = 2) -> list[str]:
    out = []
    folder = os.path.normpath(folder)
    base = folder.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(folder):
        dirs[:] = [] if root.count(os.sep) - base >= depth else [d for d in dirs if not d.startswith(".")]
        out += [os.path.join(root, f) for f in files
                if os.path.splitext(f)[1].lower() in SUB_EXTS and os.path.splitext(f)[0].lower().endswith("." + target)]
    return out


def _original_of(translation: str, target: str) -> str:
    base, ext = os.path.splitext(translation)
    orig = _MODEL_TAG.sub("", base[: -len(target) - 1]) + ext
    return orig if os.path.exists(orig) else ""


def pending_sources(profile: Profile, key: SeriesKey, target: str, current_out: str | None = None) -> list[Source]:
    """Link và bản dịch chưa nạp (hoặc đã sửa sau lần nạp) của chuỗi `key`.

    Bản dịch được tìm tự động trong thư mục hồ sơ (chỉ tệp cùng chuỗi — phim khác nằm chung
    thư mục bị loại), cộng các tệp người dùng thêm ở mục "Tệp đã dịch"."""
    out: list[Source] = []
    for url in profile.links:
        url = url.strip()
        if url and not profile.loaded_hash(target, url):
            out.append(Source("link", url, hashlib.sha1(url.encode()).hexdigest()[:12]))
    skip = os.path.realpath(current_out) if current_out else None
    found = {os.path.realpath(p) for p in _scan_subs(profile.folder, target)
             if series_key(p) == key or (key.title and series_key(p).title in profile.aliases)}
    found |= {os.path.realpath(profile.abspath(p)) for p in profile.ref_files if os.path.isfile(profile.abspath(p))}
    found.discard(skip)
    translations = []
    for p in found:
        rel, digest = profile.rel(p), file_hash(p)
        if profile.loaded_hash(target, rel) != digest:
            translations.append(Source("translation", rel, digest, p, _original_of(p, target), episode_of(p)))
    fill = _season_fill([s.episode for s in translations])
    translations.sort(key=lambda s: (fill(s.episode) is None, fill(s.episode) or (0, 0), s.path))
    return out + translations


# ------------------------------------------------------------------ đọc link

class _TextOnly(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextOnly()
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[^\S\n]+", " ", text)  # mọi khoảng trắng trừ xuống dòng, kể cả &nbsp;
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def fetch_page_text(url: str, timeout: int = 20, limit: int = 40_000) -> str:
    if not re.match(r"https?://", url, re.I):
        raise ValueError("chỉ nhận link http(s)")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept-Language": "vi,en;q=0.8,zh;q=0.6,ja;q=0.5,ko;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if "accounts.google.com" in r.geturl():
            raise ValueError("trang cần đăng nhập")
        raw = r.read(3_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
        html = "html" in r.headers.get_content_type()
    text = raw.decode(charset, errors="replace")
    text = html_to_text(text) if html else text.strip()
    if len(text) < 200:
        raise ValueError("trang gần như không có chữ (có thể cần đăng nhập hoặc chạy JavaScript)")
    return text[:limit]


# ------------------------------------------------------------------ nạp trước khi dịch, nhớ sau khi dịch

def _timed_lines(path: str, limit: int = 100_000) -> list[list[str]]:
    with open(path, encoding="utf-8-sig") as f:
        cues = parse_srt(f.read())
    out, size = [], 0
    for c in cues:
        m, s = divmod(int(c.start), 60)
        text = join_lines(c.lines)
        size += len(text)
        if size > limit:
            break
        out.append([f"{m:02d}:{s:02d}", text])
    return out


def prepare(
    profile: Profile,
    key: SeriesKey,
    target: str,
    source: str | None,
    runner: ClaudeRunner,
    current_out: str | None = None,
    status: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    kinds: tuple[str, ...] = ("link", "translation"),
) -> int:
    """Nạp dồn các nguồn chưa nạp vào hồ sơ. Ghi hồ sơ sau từng nguồn, nên dừng giữa chừng
    thì lần sau không phải đọc lại nguồn đã nạp. Trả về số nguồn đã nạp.

    kinds: loại nguồn được đọc — "link" (link trong hồ sơ), "translation" (phụ đề đã dịch
    của các tập khác / tệp tham khảo)."""
    say = status or (lambda _m: None)
    done = 0
    if not kinds:
        return 0
    for s in pending_sources(profile, key, target, current_out):
        if s.kind not in kinds:
            continue
        if cancel and cancel.is_set():
            raise Cancelled()
        try:
            if s.kind == "link":
                say(f"Đọc link {s.key}…")
                payload = {"url": s.key, "page_text": fetch_page_text(s.key), "known": profile.story(target)}
            else:
                say(f"Nạp bản dịch {s.name}…")
                payload = {
                    "translated_lines": _timed_lines(s.path),
                    "source_lines": _timed_lines(s.original) if s.original else None,
                    "known": profile.story(target, s.episode),
                }
            data = absorb_source(runner, s.kind, payload, source, target)
        except ClaudeError as e:
            if "login" in str(e).lower():
                raise
            say(f"(không nạp được {s.name}: {e})")
            continue
        except (OSError, ValueError) as e:
            say(f"(không nạp được {s.name}: {e})")
            continue
        n = profile.merge(target, data, s.episode, authoritative=s.kind == "translation")
        profile.mark_loaded(target, s.key, s.digest, s.episode)
        profile.save()
        done += 1
        say(f"  + {n} mục mới từ {s.name}")
    return done


def remember(profile: Profile, target: str, episode, result, out_path: str) -> None:
    """Ghi những gì vừa học được ở tập này và đánh dấu bản dịch vừa ghi là đã nạp
    (người dùng sửa tay sau đó thì mã nội dung đổi, lần sau tự đọc lại)."""
    profile.merge(target, {
        "terms": [{"src": k, "dst": v} for k, v in result.glossary.items()],
        "pronouns": result.pronouns,
        "notes": result.notes,
        "summary": result.summary,
        "ending": result.ending,
    }, episode)
    profile.mark_loaded(target, profile.rel(out_path), file_hash(out_path), episode)
    profile.save()
