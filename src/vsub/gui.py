"""Giao diện: chọn video -> bấm "Tạo phụ đề" -> ra <tên video>.srt cùng thư mục.

Tab "Thông tin phim" sửa hồ sơ của từng phim (series.py): danh sách video được gom theo
chuỗi phim, mỗi chuỗi / phim lẻ một hồ sơ riêng, Claude đọc trước khi dịch."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .backends import is_apple_silicon

SITE_URL = "https://viennv.dev"
SITE_TAGLINE = "MMO VPS — máy chủ ảo, máy chủ vật lý, proxy tĩnh và máy chủ phát trực tiếp"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LINK_COLOR = "#3b82f6"
WARN_COLOR = "#f59e0b"
BANNER_BG = "#1d4ed8"
HAND = "pointinghand" if sys.platform == "darwin" else "hand2"
ABOUT_TEXT = (
    "Tự động làm phụ đề cho phim, chạy ngay trên máy:\n"
    "•  Nhận dạng giọng nói bằng Whisper (GPU Apple Silicon)\n"
    "•  Dịch bằng Claude hoặc Gemini, giữ tên riêng và xưng hô thống nhất giữa các tập\n"
    "•  Tự né phụ đề in sẵn trong video\n"
    "•  Thuyết minh tiếng Việt bằng VieNeu-TTS"
)
CREDITS = ("Dùng: OpenAI Whisper (mlx-whisper, faster-whisper), Silero VAD, RapidOCR, "
           "Claude Code, Antigravity CLI, VieNeu-TTS, FFmpeg. Icon: Font Awesome Free (CC BY 4.0).")


def open_site() -> None:
    webbrowser.open(SITE_URL)

LANGS = [
    ("Tự nhận diện", None),
    ("Tiếng Nhật (ja)", "ja"),
    ("Tiếng Việt (vi)", "vi"),
    ("Tiếng Anh (en)", "en"),
    ("Tiếng Trung (zh)", "zh"),
    ("Tiếng Hàn (ko)", "ko"),
    ("Tiếng Thái (th)", "th"),
    ("Tiếng Pháp (fr)", "fr"),
    ("Tiếng Tây Ban Nha (es)", "es"),
]
MODELS = [
    ("large-v3-turbo — nhanh, chính xác tốt", "large-v3-turbo"),
    ("large-v3 — chính xác nhất, chậm hơn", "large-v3"),
    ("medium — nhẹ hơn", "medium"),
    ("small — thử nhanh", "small"),
]
TARGETS = [
    ("Không dịch", None),
    ("Tiếng Việt", "vi"),
    ("English", "en"),
    ("中文 (giản thể)", "zh"),
    ("日本語", "ja"),
    ("한국어", "ko"),
]
CLAUDE_MODELS = ["sonnet", "opus", "haiku"]
# trình dịch: (tên hiện, khoá engine, khoá thành phần ở tab Thư viện)
ENGINES = [("Claude (Claude Code)", "claude", "claude"), ("Gemini (Antigravity CLI)", "gemini", "agy")]
DEFAULT_MODEL = "mặc định"  # Gemini: dùng model đang chọn trong agy
ENGINE_MODELS = {
    "claude": CLAUDE_MODELS,
    # tên theo `agy models` (agy 1.5): gói miễn phí chỉ có Flash, Argon cần Google AI Pro/Ultra
    "gemini": [DEFAULT_MODEL, "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-4-argon-medium",
               "gemini-4-argon-high"],
}
POSITIONS = [
    ("Tự động (né phụ đề có sẵn trong video)", "auto"),
    ("Luôn ở dưới", "bottom"),
    ("Luôn ở trên", "top"),
]
DUCKS = [
    ("Tự động — đo nền từng câu rồi giảm vừa đủ", None),
    ("Giảm ít (6 dB)", 6.0),
    ("Giảm vừa (10 dB)", 10.0),
    ("Giảm nhiều (15 dB)", 15.0),
    ("Giảm rất nhiều (20 dB)", 20.0),
]
FILE_TYPES = [
    ("Video", "*.mp4 *.mkv *.mov *.avi *.m4v *.webm *.ts *.flv *.wmv"),
    ("Audio", "*.mp3 *.m4a *.wav *.flac *.aac *.ogg"),
    ("Tất cả", "*.*"),
]


class Cancelled(Exception):
    pass


VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".ts", ".flv", ".wmv",
              ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg"}
DROP_HINT = "⇣  Kéo thả video (hoặc cả thư mục) vào đây"


def expand_inputs(paths: list[str]) -> list[str]:
    """File video giữ nguyên; thư mục thì lấy các video bên trong (cả thư mục con 1 cấp)."""
    out = []
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                if root.count(os.sep) - p.count(os.sep) >= 1:
                    dirs[:] = []
                out += [os.path.join(root, n) for n in sorted(names)
                        if not n.startswith(".") and os.path.splitext(n)[1].lower() in VIDEO_EXTS]
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in VIDEO_EXTS:
            out.append(p)
    return out


# Những việc Claude làm thêm khi dịch — mặc định TẮT (mỗi tập dịch độc lập, nhanh và rẻ hơn)
SEPARATE_HINT = (
    "Bắt thêm lời lẫn trong nhạc, bớt chữ bịa. Lần đầu tải model 67 MB; tách ~2–3 phút mỗi tập 45 phút (GPU)."
    if is_apple_silicon() else
    "Bắt thêm lời lẫn trong nhạc, bớt chữ bịa. Lần đầu tải model 67 MB. Máy này tách bằng CPU nên chậm:"
    " tập 45 phút có thể thêm 30 phút trở lên."
)

READ_OPTIONS = [
    ("read_links", "Đọc các link trong tab “Thông tin phim”"),
    ("read_prev", "Đọc lại phụ đề đã dịch của các tập khác (cùng thư mục / “Tệp đã dịch”)"),
    ("use_profile", "Dùng hồ sơ phim: tên riêng, xưng hô, tóm tắt các tập trước đã học được"),
    ("learn", "Ghi những gì học được ở tập này vào hồ sơ phim (cho các tập sau)"),
]


def _settings_path() -> str:
    return os.path.join(_cache_dir(), "settings.json")


def load_settings() -> dict:
    import json

    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    import json

    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _cache_dir() -> str:
    from .paths import cache_dir

    return cache_dir()


def _pid_alive(pid: int) -> bool:
    from .paths import pid_alive

    return pid_alive(pid)


def claim_video(path: str) -> str | None:
    """Đánh dấu video đang được cửa sổ này xử lý. Trả None nếu cửa sổ khác đang làm video đó."""
    import hashlib

    d = os.path.join(_cache_dir(), "locks")
    os.makedirs(d, exist_ok=True)
    lock = os.path.join(d, hashlib.sha1(os.path.abspath(path).encode()).hexdigest() + ".lock")
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lock
        except FileExistsError:
            try:
                pid = int(open(lock).read().strip() or 0)
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                return None
            try:  # khoá bỏ lại của cửa sổ đã tắt
                os.remove(lock)
            except OSError:
                pass
    return None


def release_video(lock: str | None) -> None:
    if lock:
        try:
            os.remove(lock)
        except OSError:
            pass


def open_new_window(files: list[str] | None = None):
    """Mở thêm một cửa sổ vsub = một tiến trình riêng (model + GPU riêng, chạy song song)."""
    app = os.environ.get("VSUB_APP")
    if sys.platform == "darwin" and app and os.path.isdir(app):
        # mở thêm một bản của Vsub.app -> cửa sổ mới cũng có icon Vsub trên Dock
        return subprocess.Popen(["open", "-n", "-a", app, "--args", *(files or [])])
    from .paths import IS_WIN, log_path

    log = log_path()
    os.makedirs(os.path.dirname(log), exist_ok=True)
    exe = sys.executable
    kw: dict = {"start_new_session": True}
    if IS_WIN:  # pythonw: không hiện cửa sổ console
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = w if os.path.exists(w) else exe
        kw = {"creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(log, "a", encoding="utf-8") as out:
        return subprocess.Popen([exe, "-m", "vsub.gui", *(files or [])], stdout=out, stderr=out,
                                stdin=subprocess.DEVNULL, **kw)


def srt_path(video: str) -> str:
    return os.path.splitext(video)[0] + ".srt"


def _text_get(w: tk.Text) -> str:
    return w.get("1.0", "end").strip()


def _text_set(w: tk.Text, value: str) -> None:
    w.delete("1.0", "end")
    w.insert("1.0", value)


def _fmt(sec: float) -> str:
    m, s = divmod(int(max(sec, 0)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fix_path_for_finder() -> None:
    """App mở từ Finder / Start Menu không có PATH của Homebrew, ffmpeg app tự cài… -> bổ sung."""
    from .paths import fix_path

    fix_path()


class App:
    def __init__(self, root: tk.Tk, files: list[str] | None = None):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.files: list[str] = []
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.last_out: str | None = None
        self.errors: list[str] = []
        self.groups: list[list[str]] = []   # video gom theo chuỗi phim, đúng thứ tự tập
        self.film_keys: list = []           # SeriesKey của từng nhóm
        self.profiles: dict = {}            # SeriesKey -> Profile
        self.cur_key = None                 # phim đang hiện ở tab "Thông tin phim"

        root.title("vsub — Làm phụ đề tự động")
        root.minsize(680, 700)
        frm = ttk.Frame(root, padding=16)
        frm.pack(fill="both", expand=True)

        # --- banner báo bản mới (ẩn cho tới khi có)
        self.banner = tk.Frame(frm, bg=BANNER_BG, padx=12, pady=8)
        self.banner_lbl = tk.Label(self.banner, bg=BANNER_BG, fg="white", anchor="w", justify="left")
        self.banner_lbl.pack(side="left", fill="x", expand=True)
        self.banner_btns: list = []
        self.release = None

        # --- chọn file
        row = ttk.Frame(frm)
        row.pack(fill="x")
        self._top_row = row
        ttk.Button(row, text="Chọn video…", command=self.pick).pack(side="left")
        ttk.Button(row, text="Xoá danh sách", command=self.clear).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Cửa sổ mới", command=lambda: open_new_window()).pack(side="right")
        for seq in ("<Command-n>", "<Control-n>"):
            root.bind_all(seq, lambda e: open_new_window())
        self.count_lbl = ttk.Label(row, text="Chưa chọn file nào")
        self.count_lbl.pack(side="left", padx=12)

        self.listbox = tk.Listbox(frm, height=5, activestyle="none")
        self.listbox.pack(fill="x", pady=(10, 12))
        self._setup_drop()

        # --- tuỳ chọn
        nb = ttk.Notebook(frm)
        nb.pack(fill="x")
        opts = ttk.Frame(nb, padding=(6, 8))
        nb.add(opts, text="Cài đặt")
        ttk.Label(opts, text="Ngôn ngữ trong phim:").grid(row=0, column=0, sticky="w", pady=3)
        self.lang = ttk.Combobox(opts, state="readonly", width=34, values=[n for n, _ in LANGS])
        self.lang.current(0)
        self.lang.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(opts, text="Model:").grid(row=1, column=0, sticky="w", pady=3)
        self.model = ttk.Combobox(opts, state="readonly", width=34, values=[n for n, _ in MODELS])
        self.model.current(0)
        self.model.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(opts, text="Vị trí phụ đề:").grid(row=2, column=0, sticky="w", pady=3)
        self.position = ttk.Combobox(opts, state="readonly", width=34, values=[n for n, _ in POSITIONS])
        self.position.current(0)
        self.position.grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(opts, text="Tách giọng:").grid(row=3, column=0, sticky="w", pady=3)
        # mặc định bật trên Mac chip M (GPU: nhanh, đo trên ep3 còn nhanh hơn vì Whisper nghe ít hơn)
        self.sep_var = tk.BooleanVar(value=bool(load_settings().get("separate", is_apple_silicon())))
        self.sep_var.trace_add("write", lambda *_: save_settings({**load_settings(), "separate": self.sep_var.get()}))
        ttk.Checkbutton(opts, text="Tách lời thoại khỏi nhạc nền, tiếng ồn trước khi nhận dạng",
                        variable=self.sep_var).grid(row=3, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Label(
            opts, foreground="gray", text=SEPARATE_HINT,
        ).grid(row=4, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Separator(opts).grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(opts, text="Dịch sang:").grid(row=7, column=0, sticky="w", pady=3)
        self.target = ttk.Combobox(opts, state="readonly", width=34, values=[n for n, _ in TARGETS])
        self.target.current(1)
        self.target.grid(row=7, column=1, sticky="w", padx=8)
        ttk.Label(opts, text="Trình dịch:").grid(row=8, column=0, sticky="w", pady=3)
        eng_row = ttk.Frame(opts)
        eng_row.grid(row=8, column=1, columnspan=2, sticky="w", padx=8)
        self.engine = ttk.Combobox(eng_row, state="readonly", width=34, values=[n for n, _, _ in ENGINES])
        keys = [k for _, k, _ in ENGINES]
        self.engine.current(keys.index(load_settings().get("engine")) if load_settings().get("engine") in keys else 0)
        self.engine.pack(side="left")
        self.claude_model = ttk.Combobox(eng_row, state="readonly", width=22)  # model của trình dịch đang chọn
        self.claude_model.pack(side="left", padx=(8, 0))
        self.engine.bind("<<ComboboxSelected>>", lambda _e: self.on_engine(save=True))
        self.claude_model.bind("<<ComboboxSelected>>", lambda _e: save_settings(
            {**load_settings(), f"model_{self.engine_key()}": self.claude_model.get()}))
        self.on_engine()
        ttk.Label(
            opts, foreground="gray",
            text="Nội dung phim, link, xưng hô, tên riêng: điền ở tab “Thông tin phim”.",
        ).grid(row=9, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Label(opts, text="Đọc thêm khi dịch:").grid(row=10, column=0, sticky="nw", pady=(8, 3))
        saved = load_settings().get("read_options", {})
        self.read_vars: dict[str, tk.BooleanVar] = {}
        self.read_checks = []
        for i, (key, label) in enumerate(READ_OPTIONS):
            var = tk.BooleanVar(value=bool(saved.get(key, False)))  # mặc định tắt
            var.trace_add("write", lambda *_: save_settings(
                {**load_settings(), "read_options": {k: v.get() for k, v in self.read_vars.items()}}))
            self.read_vars[key] = var
            cb = ttk.Checkbutton(opts, text=label, variable=var)
            cb.grid(row=10 + i, column=1, columnspan=2, sticky="w", padx=8, pady=(8 if i == 0 else 0, 0))
            self.read_checks.append(cb)
        ttk.Label(
            opts, foreground="gray",
            text="Tắt hết: mỗi tập dịch độc lập. Nội dung / xưng hô / tên riêng bạn tự điền luôn được dùng.",
        ).grid(row=10 + len(READ_OPTIONS), column=1, columnspan=2, sticky="w", padx=8)

        # --- thuyết minh bằng VieNeu-TTS
        from .dub import DEFAULT_VOICE, VOICES

        r0 = 11 + len(READ_OPTIONS)
        ttk.Separator(opts).grid(row=r0, column=0, columnspan=3, sticky="ew", pady=8)
        st = load_settings()
        ttk.Label(opts, text="Thuyết minh:").grid(row=r0 + 1, column=0, sticky="w", pady=3)
        # tuỳ chọn, mặc định TẮT mỗi lần mở app (không nhớ trạng thái bật); giọng + mức giảm thì nhớ
        self.dub_var = tk.BooleanVar(value=False)
        self.dub_check = ttk.Checkbutton(opts, text="Bật thuyết minh bằng VieNeu (→ <tên>.thuyetminh.mp4)",
                                         variable=self.dub_var)
        self.dub_check.grid(row=r0 + 1, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Label(opts, text="Giọng đọc:").grid(row=r0 + 2, column=0, sticky="w", pady=3)
        voice_ids = [v for _, v in VOICES]
        self.voice = ttk.Combobox(opts, state="readonly", width=34, values=[n for n, _ in VOICES])
        saved_voice = st.get("voice", DEFAULT_VOICE)
        self.voice.current(voice_ids.index(saved_voice) if saved_voice in voice_ids else 0)
        self.voice.grid(row=r0 + 2, column=1, sticky="w", padx=8)
        ttk.Label(opts, text="Âm gốc khi đọc:").grid(row=r0 + 3, column=0, sticky="w", pady=3)
        self.duck = ttk.Combobox(opts, state="readonly", width=34, values=[n for n, _ in DUCKS])
        duck_vals = [v for _, v in DUCKS]
        self.duck.current(duck_vals.index(st.get("duck")) if st.get("duck") in duck_vals else 0)
        self.duck.grid(row=r0 + 3, column=1, sticky="w", padx=8)
        ttk.Label(
            opts, foreground="gray",
            text="Mặc định tắt. Bật thì chạy sau bước dịch sang Tiếng Việt; app đo âm gốc trước rồi mới giảm.",
        ).grid(row=r0 + 4, column=1, columnspan=2, sticky="w", padx=8)

        def save_dub(*_):
            save_settings({**load_settings(), "voice": VOICES[self.voice.current()][1],
                           "duck": DUCKS[self.duck.current()][1]})

        self.voice.bind("<<ComboboxSelected>>", save_dub)
        self.duck.bind("<<ComboboxSelected>>", save_dub)
        self.dub_var.trace_add("write", lambda *_: self.sync_dub())

        # --- tab "Thông tin phim": mỗi chuỗi phim / phim lẻ một hồ sơ, Claude đọc trước khi dịch
        info = ttk.Frame(nb, padding=(6, 8))
        nb.add(info, text="Thông tin phim")
        info.columnconfigure(1, weight=1)

        def hint(row: int, text: str) -> None:
            ttk.Label(info, foreground="gray", text=text).grid(row=row, column=1, sticky="w", padx=8)

        def text_box(row: int, label: str, height: int) -> tk.Text:
            ttk.Label(info, text=label).grid(row=row, column=0, sticky="nw", pady=3)
            w = tk.Text(info, height=height, width=52, wrap="word", relief="solid", borderwidth=1)
            w.grid(row=row, column=1, sticky="ew", padx=8, pady=3)
            return w

        ttk.Label(info, text="Phim:").grid(row=0, column=0, sticky="w", pady=3)
        self.film = ttk.Combobox(info, state="readonly", width=52)
        self.film.grid(row=0, column=1, sticky="ew", padx=8)
        self.film.bind("<<ComboboxSelected>>", lambda _e: self.show_film(self.film.current()))
        self.film_lbl = ttk.Label(info, foreground="gray", text="Chọn video để điền thông tin cho từng phim.")
        self.film_lbl.grid(row=1, column=1, sticky="w", padx=8)
        self.summary = text_box(2, "Nội dung phim:", 3)
        self.links = text_box(3, "Link liên quan:", 2)
        hint(4, "Mỗi dòng một link (trang phim, trang nhân vật…). Mỗi link chỉ đọc một lần.")
        ttk.Label(info, text="Tệp đã dịch:").grid(row=5, column=0, sticky="nw", pady=3)
        refs = ttk.Frame(info)
        refs.grid(row=5, column=1, sticky="ew", padx=8, pady=3)
        self.refs = tk.Listbox(refs, height=2, activestyle="none")
        self.refs.pack(side="left", fill="x", expand=True)
        self.ref_add = ttk.Button(refs, text="Thêm…", command=self.add_ref)
        self.ref_add.pack(side="left", padx=(6, 0))
        self.ref_del = ttk.Button(refs, text="Bỏ", command=self.del_ref)
        self.ref_del.pack(side="left", padx=(4, 0))
        hint(6, "Bản dịch các tập khác của cùng phim nằm cạnh video được tự tìm, không cần thêm.")
        self.pronouns = text_box(7, "Xưng hô:", 3)
        hint(8, "Mỗi dòng một chiều. VD: Tanaka → Sato: tôi / chị")
        self.notes = text_box(9, "Tên riêng / ghi chú:", 2)
        hint(10, "VD: 田中 = Tanaka; 佐藤 = Sato; phim công sở, xưng hô lịch sự")
        self.info_inputs = (self.summary, self.links, self.pronouns, self.notes)

        # --- tab "Thư viện": kiểm tra / cài các thành phần app cần
        self.nb = nb
        self.deps_tab = ttk.Frame(nb, padding=(6, 8))
        nb.add(self.deps_tab, text="Thư viện")
        self.deps_tab.columnconfigure(0, weight=1)
        ttk.Label(
            self.deps_tab, foreground="gray", justify="left", wraplength=620,
            text="Những thứ app cần. Thiếu cái nào thì bấm nút bên cạnh: app mở Terminal và chạy lệnh cài "
                 "(bạn xem được từng lệnh). Cài xong quay lại cửa sổ này, app tự kiểm tra lại.",
        ).grid(row=0, column=0, sticky="w")
        self.deps_rows = ttk.Frame(self.deps_tab)
        self.deps_rows.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.deps_rows.columnconfigure(2, weight=1)
        dbar = ttk.Frame(self.deps_tab)
        dbar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(dbar, text="Kiểm tra lại", command=self.check_deps).pack(side="left")
        self.deps_lbl = ttk.Label(dbar, foreground="gray", text="Đang kiểm tra…")
        self.deps_lbl.pack(side="left", padx=10)
        self.deps: dict = {}
        self._deps_first = True
        self._deps_checking = False
        self._deps_at = 0.0
        self._awaiting: set[str] = set()     # thành phần vừa mở Terminal để cài
        self._model_warned: set[str] = set()
        self.model.bind("<<ComboboxSelected>>", lambda _e: self.check_deps(), add="+")
        root.bind("<FocusIn>", self._on_focus, add="+")

        # --- nút
        act = ttk.Frame(frm)
        act.pack(fill="x", pady=(12, 8))
        self.start_btn = self._button(act, "play", "Tạo phụ đề", self.start)
        self.start_btn.pack(side="left")
        self.tr_btn = self._button(act, "language", "Chỉ dịch", self.start_translate)
        self.tr_btn.pack(side="left", padx=(8, 0))
        self.stop_btn = self._button(act, "stop", "Dừng", self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        # chỉ hiện khi bật thuyết minh (xem sync_dub)
        self.dub_btn = self._button(act, "volume", "Chỉ thuyết minh", self.start_dub)
        self.open_btn = self._button(act, "folder", "Mở thư mục", self.reveal, state="disabled")
        self.open_btn.pack(side="right")

        self.sync_dub()

        # --- tiến độ
        self.stage = ttk.Label(frm, text="Sẵn sàng.")
        self.stage.pack(fill="x")
        self.bar = ttk.Progressbar(frm, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=6)

        # --- footer (pack trước khung log để luôn còn chỗ ở đáy cửa sổ)
        foot = ttk.Frame(frm)
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        logo = self._img("viennv-20.png")
        if logo:
            ttk.Label(foot, image=logo, cursor=HAND).pack(side="left", padx=(0, 6))
            foot.winfo_children()[-1].bind("<Button-1>", lambda _e: open_site())
        ttk.Label(foot, text=f"vsub {__version__}  ·  Phát triển bởi ", foreground="gray").pack(side="left")
        self._link(foot, "viennv.dev", open_site).pack(side="left")
        self._link(foot, "Giới thiệu", self.show_about).pack(side="right")
        ttk.Label(foot, text="  ·  ", foreground="gray").pack(side="right")
        self._link(foot, "Kiểm tra cập nhật", lambda: self.check_update(manual=True)).pack(side="right")
        try:  # mục "About" trong menu app của macOS
            root.createcommand("tkAboutDialog", self.show_about)
        except tk.TclError:
            pass

        logf = ttk.Frame(frm)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=10, wrap="word", state="disabled", relief="flat", borderwidth=1)
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        for f in files or []:
            self.add(f, refresh=False)
        self.refresh_files()
        root.after(100, self.poll)
        root.after(400, self.check_deps)
        root.after(2500, self.check_update)

    # ---------- kéo thả
    def _setup_drop(self) -> None:
        """Kéo thả file/thư mục vào cửa sổ (tkinterdnd2) và lên icon app trên Dock (macOS)."""
        try:  # thả lên icon trên Dock khi app đang chạy
            self.root.createcommand("::tk::mac::OpenDocument", lambda *paths: self.add_many(paths))
        except tk.TclError:
            pass
        if not hasattr(self.root, "drop_target_register"):
            return  # không có tkinterdnd2 -> chỉ dùng nút "Chọn video…"
        from tkinterdnd2 import DND_FILES

        self._lb_bg = self.listbox.cget("background")
        for w in (self.root, self.listbox):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", self.on_drop)
        self.listbox.dnd_bind("<<DropEnter>>", lambda e: (self.listbox.config(background="#dbeafe"), e.action)[1])
        self.listbox.dnd_bind("<<DropLeave>>", lambda e: (self.listbox.config(background=self._lb_bg), e.action)[1])

    def on_drop(self, event):
        self.listbox.config(background=getattr(self, "_lb_bg", self.listbox.cget("background")))
        self.add_many(self.root.tk.splitlist(event.data))
        return event.action

    def add_many(self, paths) -> None:
        found = expand_inputs(list(paths))
        if not found:
            messagebox.showwarning("Không có video", "Không thấy file video nào trong những gì vừa thả vào.")
            return
        if self.worker and self.worker.is_alive():
            if messagebox.askyesno(
                "Đang chạy", f"Cửa sổ này đang bận. Mở cửa sổ mới để làm {len(found)} video này song song?"
            ):
                open_new_window(found)
            return
        for f in found:
            self.add(f, refresh=False)
        self.refresh_files()
        self.write(f"＋ Thêm {len(found)} file")

    # ---------- danh sách file
    def add(self, path: str, refresh: bool = True) -> None:
        path = os.path.abspath(path)
        if os.path.isdir(path):
            for f in expand_inputs([path]):
                self.add(f, refresh=False)
        elif os.path.isfile(path) and path not in self.files:
            self.files.append(path)
        if refresh:
            self.refresh_files()

    def pick(self) -> None:
        start = os.path.dirname(self.files[-1]) if self.files else os.path.expanduser("~/Downloads")
        for p in filedialog.askopenfilenames(title="Chọn video", initialdir=start, filetypes=FILE_TYPES):
            self.add(p, refresh=False)
        self.refresh_files()

    def clear(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.files.clear()
        self.refresh_files()

    def refresh_files(self) -> None:
        """Sắp danh sách theo tên (gom theo chuỗi phim, đúng thứ tự tập) và dựng lại ô "Phim"."""
        from .series import describe_group, open_profile, order_files, series_key

        self.save_fields()  # giữ chữ đang gõ dở trước khi dựng lại các ô
        self.groups = order_files(self.files)
        self.files = [f for g in self.groups for f in g]
        self.listbox.delete(0, "end")
        for f in self.files:
            self.listbox.insert("end", os.path.basename(f))
        if not self.files:
            self.listbox.insert("end", DROP_HINT)
            self.listbox.itemconfig(0, foreground="gray")
        n = len(self.files)
        self.count_lbl.config(text=f"{n} file" if n else "Chưa chọn file nào")
        self.film_keys = [series_key(g[0]) for g in self.groups]
        for key, g in zip(self.film_keys, self.groups):
            if key not in self.profiles:
                try:
                    self.profiles[key] = open_profile(g[0])
                except (OSError, ValueError) as e:  # hồ sơ hỏng -> báo, dùng hồ sơ trống
                    self.write(f"✘ Không đọc được hồ sơ phim cạnh {os.path.basename(g[0])}: {e}")
                    from .series import Profile

                    self.profiles[key] = Profile(os.path.join(os.path.dirname(g[0]), key.profile_name()))
        self.film["values"] = [describe_group(g)[0] for g in self.groups]
        if self.cur_key in self.film_keys:
            self.film.current(self.film_keys.index(self.cur_key))
            self.fill_fields()
        else:
            self.show_film(0 if self.film_keys else None)

    # ---------- hồ sơ phim
    def target_lang(self) -> str:
        return TARGETS[self.target.current()][1] or "vi"

    def show_film(self, idx: int | None) -> None:
        key = self.film_keys[idx] if idx is not None and idx >= 0 else None
        if key == self.cur_key and key is not None:
            return
        self.save_fields()
        self.cur_key = key
        if key is None:
            self.film.set("")
        else:
            self.film.current(idx)
        self.fill_fields()

    def fill_fields(self) -> None:
        from .series import describe_group

        prof = self.profiles.get(self.cur_key) if self.cur_key else None
        _text_set(self.summary, prof.summary if prof else "")
        _text_set(self.links, "\n".join(prof.links) if prof else "")
        _text_set(self.pronouns, prof.pronouns_text if prof else "")
        _text_set(self.notes, prof.notes_text if prof else "")
        self.refs.delete(0, "end")
        for p in prof.ref_files if prof else []:
            self.refs.insert("end", p)
        if not prof:
            self.film_lbl.config(text="Chọn video để điền thông tin cho từng phim.")
            return
        warns = describe_group(self.groups[self.film_keys.index(self.cur_key)])[1]
        state = prof.stats(self.target_lang()) if os.path.exists(prof.path) else "chưa có, sẽ tạo khi dịch"
        self.film_lbl.config(text=f"Hồ sơ {os.path.basename(prof.path)}: {state}"
                                  + "".join(f"\n⚠ {w}" for w in warns))

    def save_fields(self) -> None:
        """Ghi các ô của tab "Thông tin phim" vào hồ sơ của phim đang hiện."""
        prof = self.profiles.get(self.cur_key) if self.cur_key else None
        if not prof:
            return
        prof.summary = _text_get(self.summary)
        prof.links = [l.strip() for l in _text_get(self.links).splitlines() if l.strip()]
        prof.pronouns_text = _text_get(self.pronouns)
        prof.notes_text = _text_get(self.notes)
        if prof.has_user_info() or os.path.exists(prof.path):
            try:
                prof.save()
            except OSError as e:
                self.write(f"✘ Không ghi được hồ sơ {prof.path}: {e}")

    def add_ref(self) -> None:
        prof = self.profiles.get(self.cur_key) if self.cur_key else None
        if not prof:
            return
        for p in filedialog.askopenfilenames(
            title="Chọn tệp phụ đề đã dịch", initialdir=prof.folder,
            filetypes=[("Phụ đề", "*.srt *.vtt"), ("Tất cả", "*.*")],
        ):
            if prof.rel(p) not in prof.ref_files:
                prof.ref_files.append(prof.rel(p))
                self.refs.insert("end", prof.rel(p))

    def del_ref(self) -> None:
        prof = self.profiles.get(self.cur_key) if self.cur_key else None
        if not prof:
            return
        for i in reversed(self.refs.curselection()):
            del prof.ref_files[i]
            self.refs.delete(i)

    # ---------- chạy
    def start(self) -> None:
        if not self.files:
            self.pick()
            if not self.files:
                return
        from .deps import check_vieneu

        if not self.ensure_required():
            return
        target = TARGETS[self.target.current()][1]
        engine = self.engine_key()
        if target:
            ok = self.ensure_translator(allow_skip=True)
            if ok is None:
                return
            if not ok:
                target = None
        dub = self.dub_settings() if self.dub_var.get() and target == "vi" else None
        if self.dub_var.get() and target != "vi":
            self.write("⚠ Thuyết minh chỉ chạy khi dịch sang Tiếng Việt — lần này bỏ qua thuyết minh.")
        if dub and not check_vieneu().ok:
            ans = messagebox.askyesnocancel(
                "Chưa có VieNeu-TTS",
                "Thuyết minh cần VieNeu-TTS mà máy chưa cài.\n\n"
                "Có: cài giúp ngay (mở Terminal; tải thư viện + model khoảng 1–2 GB).\n"
                "Không: chạy tiếp nhưng lần này không thuyết minh.\nHuỷ: thôi.",
            )
            if ans is None:
                return
            if ans:
                self.install(["vieneu"])
                return
            dub = None
            self.write("⚠ Lần này không thuyết minh (chưa có VieNeu-TTS).")
        if not self.confirm_model(MODELS[self.model.current()][1]):
            return
        exist = [f for f in self.files if os.path.exists(srt_path(f))]
        if exist and not messagebox.askyesno(
            "Ghi đè?", f"Đã có sẵn {len(exist)} file .srt cùng tên. Ghi đè lên?"
        ):
            return
        from .series import describe_group, series_key

        self.save_fields()
        lang = LANGS[self.lang.current()][1]
        model = MODELS[self.model.current()][1]
        flags = {k: v.get() for k, v in self.read_vars.items()}
        tr = (target, engine, self.translator_model(), flags, dub) if target else None
        pos = POSITIONS[self.position.current()][1]
        if tr:
            for g in self.groups:
                label, warns = describe_group(g)
                self.write(f"• {label}" + "".join(f"\n  ⚠ {w}" for w in warns))
        jobs = [(f, self.profiles[series_key(f)]) for f in self.files]
        self.cancel.clear()
        self.errors.clear()
        self.set_running(True)
        self.worker = threading.Thread(target=self.work, args=(jobs, lang, model, tr, pos, self.sep_var.get()),
                                       daemon=True)
        self.worker.start()

    def dub_settings(self) -> tuple[str, float | None]:
        from .dub import VOICES

        return VOICES[self.voice.current()][1], DUCKS[self.duck.current()][1]

    # ---------- thành phần cần cài (tab "Thư viện")
    def ensure_required(self) -> bool:
        """FFmpeg + thư viện Whisper: thiếu thì đề nghị cài, không chạy tiếp."""
        from .deps import check_ffmpeg, check_whisper

        miss = [d for d in (check_ffmpeg(), check_whisper()) if not d.ok]
        if not miss:
            return True
        names = ", ".join(d.name for d in miss)
        if messagebox.askyesno("Thiếu thành phần bắt buộc",
                               f"Thiếu {names} — app chưa chạy được.\n\nCài giúp bạn ngay bây giờ?"):
            self.install([d.key for d in miss])
        return False

    def ensure_vieneu(self) -> bool:
        from .deps import check_vieneu

        if check_vieneu().ok:
            return True
        if messagebox.askyesno("Chưa có VieNeu-TTS",
                               "Thuyết minh cần VieNeu-TTS mà máy chưa cài.\n\n"
                               "Cài giúp bạn ngay? (mở Terminal; tải thư viện + model khoảng 1–2 GB)"):
            self.install(["vieneu"])
        return False

    def confirm_model(self, model: str) -> bool:
        """Model chưa tải -> báo trước lần chạy này sẽ tải bao nhiêu (mỗi model hỏi một lần)."""
        from .deps import MODEL_SIZES, model_downloaded

        if model in self._model_warned or model_downloaded(model) is not False:
            return True
        ok = messagebox.askokcancel(
            "Model chưa có trên máy",
            f"Model {model} chưa tải về. Lần chạy này app sẽ tải khoảng {MODEL_SIZES.get(model, '?')} "
            "trước khi nhận dạng (cần mạng, mất vài phút).\n\nTiếp tục?",
        )
        if ok:
            self._model_warned.add(model)
        return ok

    def install(self, keys: list[str]) -> bool:
        """Hỏi xác nhận rồi mở Terminal chạy lệnh cài. Trả True nếu đã mở."""
        from .deps import install_plan, run_in_terminal

        summary, script = install_plan(keys, MODELS[self.model.current()][1])
        if not messagebox.askyesno(
            "Cài đặt giúp?",
            f"App sẽ mở Terminal và chạy:\n\n{summary}\n\nCài xong quay lại cửa sổ vsub, app tự kiểm tra lại. "
            "Tiếp tục?",
        ):
            return False
        try:
            path = run_in_terminal("-".join(keys), script)
        except (OSError, RuntimeError) as e:
            messagebox.showerror("Không mở được Terminal", str(e))
            return False
        self.write(f"→ Đã mở Terminal để cài ({', '.join(keys)}). Script: {path}")
        self._awaiting |= set(keys)
        return True

    def check_deps(self) -> None:
        if self._deps_checking:
            return
        self._deps_checking = True
        self._deps_at = time.time()
        self.deps_lbl.config(text="Đang kiểm tra…")
        model = MODELS[self.model.current()][1]

        def run() -> None:
            from .deps import check_all

            try:
                res = check_all(model)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                res = []
            self.emit("deps", res)

        threading.Thread(target=run, daemon=True).start()

    def _on_focus(self, _e=None) -> None:
        # quay lại app sau khi cài trong Terminal -> kiểm tra lại (không quá 1 lần / 3 giây)
        if self._awaiting and time.time() - self._deps_at > 3:
            self.check_deps()

    def show_deps(self, deps: list) -> None:
        from tkinter import font as tkfont

        self._deps_checking = False
        old = self.deps
        self.deps = {d.key: d for d in deps}
        for w in self.deps_rows.winfo_children():
            w.destroy()
        if not hasattr(self, "_bold"):
            self._bold = tkfont.Font(**{**tkfont.nametofont("TkDefaultFont").actual(), "weight": "bold"})
        for i, d in enumerate(deps):
            r = 2 * i
            icon = self._img("fa-ok.png" if d.ok else ("fa-missing.png" if d.required else "fa-warn.png"))
            ttk.Label(self.deps_rows, image=icon).grid(row=r, column=0, rowspan=2, sticky="n", padx=(0, 8), pady=(8, 0))
            ttk.Label(self.deps_rows, text=d.name, font=self._bold).grid(row=r, column=1, sticky="w", pady=(8, 0))
            ttk.Label(self.deps_rows, text=f"{d.need} · {d.purpose}", foreground="gray").grid(
                row=r, column=2, sticky="w", padx=10, pady=(8, 0))
            ttk.Label(self.deps_rows, text=d.detail, foreground="gray" if d.ok else WARN_COLOR).grid(
                row=r + 1, column=1, columnspan=2, sticky="w")
            btns = ttk.Frame(self.deps_rows)
            btns.grid(row=r, column=3, rowspan=2, sticky="e", pady=(8, 0))
            if d.action:
                ttk.Button(btns, text=d.action, command=lambda k=d.key: self.install([k])).pack(side="left")
            if d.guide:
                self._link(btns, "Hướng dẫn", lambda d=d: self.show_guide(d)).pack(side="left", padx=(10, 0))
        missing = [d for d in deps if not d.ok and d.key not in ("model", "sepmodel")]  # 2 model này tự tải
        self.deps_lbl.config(text=f"Kiểm tra lúc {time.strftime('%H:%M')}"
                                  + (f" — thiếu {len(missing)} thành phần" if missing else " — đủ cả"))
        warn = any(not d.ok and (d.required or d.key == self.engine_dep()) for d in deps)
        self.nb.tab(self.deps_tab, text="Thư viện", image=self._img("fa-warn-sm.png") if warn else "",
                    compound="right")
        for k in list(self._awaiting):  # vừa cài xong?
            if k in self.deps and self.deps[k].ok:
                self._awaiting.discard(k)
                if k in old and not old[k].ok:
                    self.write(f"✔ {self.deps[k].name} đã sẵn sàng: {self.deps[k].detail}")
        if self._deps_first and deps:
            self._deps_first = False
            for d in missing:  # VieNeu chỉ cần khi bật thuyết minh (mặc định tắt) -> không nhắc lúc mở app
                if d.required or d.key == self.engine_dep():
                    self.write(f"⚠ {d.name} ({d.need.lower()}): {d.detail} — xem tab “Thư viện”")
            req = [d for d in deps if d.required and not d.ok]
            if req and not self.running and messagebox.askyesno(
                "Thiếu thành phần bắt buộc",
                f"Thiếu {', '.join(d.name for d in req)} — app chưa chạy được.\n\nCài giúp bạn ngay bây giờ?",
            ):
                self.install([d.key for d in req])

    def show_guide(self, d) -> None:
        """Hướng dẫn cài tay; chữ chọn / chép được."""
        w = tk.Toplevel(self.root)
        w.title(f"Cài {d.name}")
        w.transient(self.root)
        body = ttk.Frame(w, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"{d.name} — {d.purpose}", font=getattr(self, "_bold", None)).pack(anchor="w")
        ttk.Label(body, text=f"Hiện tại: {d.detail}", foreground="gray").pack(anchor="w", pady=(2, 8))
        txt = tk.Text(body, width=78, height=d.guide.count("\n") + 2, wrap="word", relief="solid", borderwidth=1)
        txt.insert("1.0", d.guide)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Đóng", command=w.destroy).pack(side="right")

        def copy() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(d.guide)

        ttk.Button(btns, text="Chép", command=copy).pack(side="right", padx=8)
        if d.action:
            ttk.Button(btns, text=f"{d.action} giúp tôi",
                       command=lambda: (w.destroy(), self.install([d.key]))).pack(side="left")
        w.bind("<Escape>", lambda _e: w.destroy())

    def ensure_translator(self, allow_skip: bool = False) -> bool | None:
        """Claude / agy (theo trình dịch đang chọn) chưa sẵn sàng -> đề nghị cài / đăng nhập.
        True: dịch được. False: lần này không dịch (chỉ khi allow_skip). None: thôi, không chạy."""
        from .deps import check_agy, check_claude

        dep_key = self.engine_dep()
        d = check_agy() if dep_key == "agy" else check_claude()
        if d.ok:
            return True
        msg = (f"{d.detail}. {d.name} cần để dịch phụ đề.\n\n"
               f"Có: {(d.action or 'cài đặt').lower()} ngay (mở Terminal).\n")
        if allow_skip:
            ans = messagebox.askyesnocancel(
                f"{d.name} chưa sẵn sàng", msg + "Không: vẫn tạo phụ đề nhưng lần này không dịch.\nHuỷ: thôi.")
        else:
            ans = messagebox.askyesno(f"{d.name} chưa sẵn sàng", msg + "Không: thôi.") or None
        if ans is None:
            return None
        if ans:
            self.install([dep_key])
            return None
        self.write(f"⚠ Lần này không dịch ({d.name} chưa sẵn sàng).")
        return False

    def translator_model(self) -> str:
        """Model của trình dịch đang chọn ("" = model mặc định của agy) — khác model Whisper."""
        m = self.claude_model.get()
        return "" if m == DEFAULT_MODEL else m

    def start_translate(self) -> None:
        """Chỉ dịch lại <tên>.srt đã nhận dạng (không chạy Whisper lại) bằng trình dịch / model đang chọn.
        Mỗi model ra một file riêng: <tên>.claude-opus.vi.srt, <tên>.gemini-….vi.srt…"""
        from .series import series_key
        from .translate import model_tag, translated_path

        if not self.files:
            self.pick()
            if not self.files:
                return
        target = TARGETS[self.target.current()][1]
        if not target:
            messagebox.showwarning("Chưa chọn ngôn ngữ dịch",
                                   "Chọn ngôn ngữ ở ô “Dịch sang” rồi bấm “Chỉ dịch”.")
            return
        ready = [f for f in self.files if os.path.exists(srt_path(f))]
        if not ready:
            messagebox.showwarning("Chưa có phụ đề gốc",
                                   "Chưa video nào có phụ đề gốc (<tên>.srt). Bấm “Tạo phụ đề” trước — "
                                   "lần sau đổi model thì chỉ cần “Chỉ dịch”.")
            return
        if not self.ensure_translator():
            return
        engine, tr_model = self.engine_key(), self.translator_model()
        tag = model_tag(engine, tr_model)
        exist = [f for f in ready if os.path.exists(translated_path(srt_path(f), target, tag))]
        if exist and not messagebox.askyesno(
            "Ghi đè?", f"Đã có {len(exist)} bản dịch bằng {tag}. Dịch lại và ghi đè?"
        ):
            return
        for f in self.files:
            if f not in ready:
                self.write(f"⏭ {os.path.basename(f)}: chưa có phụ đề gốc .srt — bỏ qua")
        self.save_fields()
        flags = {k: v.get() for k, v in self.read_vars.items()}
        jobs = [(f, self.profiles[series_key(f)]) for f in ready]
        self.cancel.clear()
        self.errors.clear()
        self.set_running(True)
        self.worker = threading.Thread(
            target=self.work_translate,
            args=(jobs, LANGS[self.lang.current()][1], (target, engine, tr_model, flags, None)), daemon=True)
        self.worker.start()

    def start_dub(self) -> None:
        """Thuyết minh từ bản <tên>.*.vi.srt mới nhất, không nhận dạng / dịch lại."""
        from .dub import output_path
        from .translate import latest_translation

        if not self.files:
            self.pick()
            if not self.files:
                return
        if not self.ensure_required():
            return
        ready = [f for f in self.files if latest_translation(srt_path(f), "vi")]
        missing = [f for f in self.files if f not in ready]
        if not ready:
            messagebox.showwarning("Chưa có bản dịch",
                                   "Chưa video nào có phụ đề tiếng Việt (<tên>.….vi.srt). Hãy tạo phụ đề và dịch "
                                   "sang Tiếng Việt trước, hoặc bật “Thuyết minh” rồi bấm “Tạo phụ đề”.")
            return
        if not self.ensure_vieneu():
            return
        exist = [f for f in ready if os.path.exists(output_path(f))]
        if exist and not messagebox.askyesno("Ghi đè?", f"Đã có {len(exist)} file thuyết minh. Làm lại và ghi đè?"):
            return
        for f in missing:
            self.write(f"⏭ {os.path.basename(f)}: chưa có .vi.srt — bỏ qua")
        voice, duck = self.dub_settings()
        self.cancel.clear()
        self.errors.clear()
        self.set_running(True)
        self.worker = threading.Thread(target=self.work_dub, args=(ready, voice, duck), daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.cancel.set()
        self.stage.config(text="Đang dừng sau đoạn hiện tại…")

    def engine_key(self) -> str:
        """Trình dịch đang chọn: "claude" hoặc "gemini"."""
        w = getattr(self, "engine", None)
        return ENGINES[w.current()][1] if w is not None and w.current() >= 0 else "claude"

    def engine_dep(self) -> str:
        """Khoá thành phần ở tab Thư viện của trình dịch đang chọn ("claude" / "agy")."""
        return {k: d for _, k, d in ENGINES}[self.engine_key()]

    def on_engine(self, save: bool = False) -> None:
        """Đổi trình dịch: nạp danh sách model của nó, chọn lại model đã dùng lần trước."""
        key = self.engine_key()
        models = ENGINE_MODELS[key]
        last = load_settings().get(f"model_{key}")
        self.claude_model.config(values=models)
        self.claude_model.current(models.index(last) if last in models else 0)
        if save:
            save_settings({**load_settings(), "engine": key})

    def sync_dub(self) -> None:
        """Thuyết minh tắt: khoá ô giọng / mức giảm và ẩn nút "Chỉ thuyết minh"."""
        on, busy = self.dub_var.get(), self.running
        for w in (self.voice, self.duck):
            w.config(state="readonly" if on and not busy else "disabled")
        self.dub_check.config(state="disabled" if busy else "normal")
        if on and not self.dub_btn.winfo_manager():
            self.dub_btn.pack(side="left", after=self.tr_btn, padx=(8, 0))
        elif not on and self.dub_btn.winfo_manager():
            self.dub_btn.pack_forget()
        self.dub_btn.config(state="disabled" if busy else "normal")

    def set_running(self, running: bool) -> None:
        self.running = running
        self.start_btn.config(state="disabled" if running else "normal")
        self.tr_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        for w in (self.lang, self.model, self.position, self.target, self.engine, self.claude_model, self.film):
            w.config(state="disabled" if running else "readonly")
        self.sync_dub()
        # hồ sơ đang được luồng nền ghi -> khoá các ô cho tới khi chạy xong
        for w in (*self.info_inputs, self.refs, self.ref_add, self.ref_del):
            w.config(state="disabled" if running else "normal")
        for cb in getattr(self, "read_checks", []):
            cb.config(state="disabled" if running else "normal")

    # ---------- luồng nền (không đụng vào Tk ở đây, chỉ đẩy sự kiện vào queue)
    def emit(self, *ev) -> None:
        self.q.put(ev)

    def work(self, jobs: list, lang, model: str, tr, pos: str = "auto", separate: bool = False) -> None:
        """jobs: [(đường dẫn video, hồ sơ phim)] theo đúng thứ tự tập."""
        import numpy as np

        from .backends import TranscribeOptions, make_backend
        from .formats import write_subs
        from .pipeline import check_hardsubs, make_cues, resolve_position, transcribe_video

        backend = None
        for idx, (path, profile) in enumerate(jobs):
            name = os.path.basename(path)
            self.emit("file", idx, len(jobs), name)
            lock = claim_video(path)
            if lock is None:
                self.emit("log", "  ⏭ Cửa sổ khác đang làm video này — bỏ qua")
                continue
            try:
                if backend is None:
                    self.emit("stage", "Đang nạp model… (lần đầu sẽ tải về, có thể mất vài phút)")
                    backend = make_backend("auto", model)
                    backend.transcribe(np.zeros(16000, dtype=np.float32), TranscribeOptions("en"))
                started = time.time()

                def progress(i, n, chunk, segs, eta, _name=name):
                    if self.cancel.is_set():
                        raise Cancelled()
                    text = " ".join(s.text.strip() for s in segs)
                    self.emit("progress", i + 1, n, chunk.start, text, eta)

                def sep_progress(done, total):
                    if self.cancel.is_set():
                        raise Cancelled()
                    self.emit("sprogress", done, total)

                use_lang = lang or (profile.source_lang or None)
                if not lang and use_lang:
                    self.emit("log", f"  Ngôn ngữ gốc theo hồ sơ phim: {use_lang}")
                t = transcribe_video(
                    path, backend, TranscribeOptions(use_lang, "transcribe", None),
                    progress=progress,
                    status=lambda m: (self.emit("stage", m.strip("• ").strip()),
                                      m.lstrip().startswith(("→", "!")) and self.emit("log", "  " + m.strip())),
                    separate=separate, separate_progress=sep_progress,
                )
                if t.language and t.language != profile.source_lang:
                    profile.source_lang = t.language
                    try:
                        profile.save()
                    except OSError:
                        pass
                if pos == "auto":
                    self.emit("stage", "Kiểm tra phụ đề in sẵn trong video…")
                    info = check_hardsubs(t, path)
                    self.emit("log", "  " + info.describe())
                position = resolve_position(pos, t)
                if position == "top":
                    self.emit("log", "  → Phụ đề mới sẽ hiện ở phía trên màn hình")
                self.save_transcript(t, path)
                cues = make_cues(t)
                out = srt_path(path)
                write_subs(cues, out, position)
                self.emit("done", name, out, len(cues), t.language, time.time() - started)
                if tr and tr[0] != t.language and cues:
                    target, engine, tr_model, flags, dub = tr
                    vi_srt = self.translate_file(path, cues, t.language, target, engine, tr_model, profile,
                                                 position=position, flags=flags)
                    if dub:
                        self.dub_file(path, vi_srt, *dub)
            except Cancelled:
                self.emit("cancelled")
                return
            except Exception as e:  # noqa: BLE001 — báo lỗi lên giao diện, chạy tiếp file sau
                traceback.print_exc()
                self.emit("error", name, str(e) or e.__class__.__name__)
            finally:
                release_video(lock)
        self.emit("finished")

    def work_dub(self, files: list[str], voice: str, duck: float | None) -> None:
        from .translate import latest_translation

        for idx, path in enumerate(files):
            name = os.path.basename(path)
            self.emit("file", idx, len(files), name)
            lock = claim_video(path)
            if lock is None:
                self.emit("log", "  ⏭ Cửa sổ khác đang làm video này — bỏ qua")
                continue
            try:
                subs = latest_translation(srt_path(path), "vi")
                if not subs:
                    self.emit("log", "  ⏭ Chưa có bản dịch tiếng Việt — bỏ qua")
                    continue
                self.emit("log", f"  Dùng bản dịch {os.path.basename(subs)}")
                self.dub_file(path, subs, voice, duck)
            except Cancelled:
                self.emit("cancelled")
                return
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.emit("error", name, str(e) or e.__class__.__name__)
            finally:
                release_video(lock)
        self.emit("finished")

    def work_translate(self, jobs: list, lang, tr) -> None:
        """Chỉ dịch: đọc <tên>.srt có sẵn rồi dịch, không nạp Whisper."""
        from .formats import parse_srt, srt_position

        target, engine, tr_model, flags, _dub = tr
        for idx, (path, profile) in enumerate(jobs):
            name = os.path.basename(path)
            self.emit("file", idx, len(jobs), name)
            lock = claim_video(path)
            if lock is None:
                self.emit("log", "  ⏭ Cửa sổ khác đang làm video này — bỏ qua")
                continue
            try:
                src = srt_path(path)
                with open(src, encoding="utf-8") as f:
                    raw = f.read()
                cues = parse_srt(raw)
                source = self.source_lang(path, profile, lang)
                self.emit("log", f"  Dùng phụ đề gốc có sẵn {os.path.basename(src)} — {len(cues)} câu"
                                 + (f", ngôn ngữ {source}" if source else ""))
                if not cues:
                    self.emit("log", "  ⏭ Phụ đề gốc trống — bỏ qua")
                elif source == target:
                    self.emit("log", f"  ⏭ Phụ đề gốc đã là {target} — bỏ qua")
                else:
                    self.translate_file(path, cues, source, target, engine, tr_model, profile,
                                        position=srt_position(raw), flags=flags)
            except Cancelled:
                self.emit("cancelled")
                return
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.emit("error", name, str(e) or e.__class__.__name__)
            finally:
                release_video(lock)
        self.emit("finished")

    @staticmethod
    def source_lang(path: str, profile, lang: str | None) -> str | None:
        """Ngôn ngữ của phụ đề gốc: ô “Ngôn ngữ gốc” > hồ sơ phim > bản nhận dạng đã lưu."""
        if lang or profile.source_lang:
            return lang or profile.source_lang
        from .pipeline import Transcript

        try:
            stem = os.path.splitext(os.path.basename(path))[0]
            return Transcript.load(os.path.join(_cache_dir(), stem + ".transcript.json")).language or None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def dub_file(self, path: str, subs: str, voice: str, duck: float | None) -> None:
        """Thuyết minh một video từ file phụ đề tiếng Việt."""
        from .dub import Cancelled as DCancelled
        from .dub import voiceover
        from .formats import parse_srt

        self.emit("dstart", voice)
        started = time.time()
        with open(subs, encoding="utf-8") as f:
            cues = parse_srt(f.read())
        labels = {"tts": "VieNeu đang đọc: câu", "prep": "Chuẩn bị giọng đọc: câu", "mix": "Đang trộn vào phim"}

        def progress(stage: str, i: int, n: int) -> None:
            self.emit("dprogress", labels[stage], stage, i, n)

        try:
            r = voiceover(path, cues, voice=voice, duck_db=duck, status=lambda m: self.emit("stage", m),
                          progress=progress, cancel=self.cancel)
        except DCancelled:
            raise Cancelled() from None
        self.emit("log", "  " + r.describe())
        for w in r.warnings:
            self.emit("log", "  ⚠ " + w)
        self.emit("ddone", r.out, r.lines, time.time() - started)

    @staticmethod
    def save_transcript(t, path: str) -> None:
        """Lưu bản nhận dạng thô vào thư mục cache (để chia dòng/dịch lại, gỡ lỗi)."""
        try:
            base = _cache_dir()
            os.makedirs(base, exist_ok=True)
            t.save(os.path.join(base, os.path.splitext(os.path.basename(path))[0] + ".transcript.json"))
        except OSError:
            pass

    def translate_file(self, path, cues, source, target, engine, tr_model, profile, position="bottom",
                       flags: dict | None = None) -> str:
        """Nạp dồn những gì hồ sơ chưa có (link, bản dịch các tập khác cùng phim), dịch, rồi ghi
        lại vào hồ sơ những gì học được ở tập này để tập sau dùng tiếp."""
        from .formats import write_subs
        from .series import episode_of, prepare, remember, series_key
        from .translate import Cancelled as TCancelled
        from .translate import make_runner, model_tag, parse_glossary_text, runner_name, translate_cues, translated_path

        flags = flags or {}
        runner = make_runner(engine, tr_model)
        self.emit("tstart", target, runner_name(runner))
        started = time.time()
        if flags.get("use_profile"):
            try:
                profile.reload()  # lấy thêm tên riêng / xưng hô mà cửa sổ khác vừa học
            except (OSError, ValueError):
                pass
        out = translated_path(srt_path(path), target, model_tag(engine, tr_model))
        episode = episode_of(path)
        say = lambda m: self.emit("stage", m)  # noqa: E731
        try:
            kinds = tuple(k for k, on in (("link", flags.get("read_links")),
                                          ("translation", flags.get("read_prev"))) if on)
            if kinds:
                prepare(profile, series_key(path), target, source, runner, out, status=say,
                        cancel=self.cancel, kinds=kinds)
            res = translate_cues(
                cues, target, source, runner,
                user_glossary=parse_glossary_text(profile.notes_text),
                notes=profile.notes_text or None,
                progress=lambda d, n: self.emit("tprogress", d, n),
                status=say,
                cancel=self.cancel,
                story=profile.story(target, episode, learned=bool(flags.get("use_profile"))),
            )
        except TCancelled:
            raise Cancelled()
        write_subs(res.cues, out, position)
        if flags.get("learn"):
            remember(profile, target, episode, res, out)
            self.emit("log", f"  Hồ sơ {os.path.basename(profile.path)}: {profile.stats(target)}")
        self.emit("tdone", out, len(res.cues), res.missing, res.cost_usd, time.time() - started, res.splits)
        return out

    # ---------- nhận sự kiện trên luồng giao diện
    def poll(self) -> None:
        try:
            while True:
                self.handle(*self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def handle(self, kind, *a) -> None:
        if kind == "file":
            idx, n, name = a
            self.bar.config(value=0)
            self.write(f"\n▶ ({idx + 1}/{n}) {name}")
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
        elif kind == "stage":
            self.stage.config(text=a[0])
            if not a[0].startswith("Đang nạp"):
                self.write("  " + a[0])
        elif kind == "progress":
            i, n, at, text, eta = a
            self.bar.config(value=100 * i / n)
            eta_s = f" — còn khoảng {_fmt(eta)}" if eta is not None and i > 3 else ""
            self.stage.config(text=f"Đang nhận dạng: đoạn {i}/{n}{eta_s}")
            if text:
                self.write(f"  [{_fmt(at)}] {text[:90]}")
        elif kind == "sprogress":
            done, total = a
            self.bar.config(value=100 * done / total if total else 0)
            self.stage.config(text=f"Đang tách giọng khỏi nhạc nền: {_fmt(done)} / {_fmt(total)}")
        elif kind == "done":
            name, out, n, lang, secs = a
            self.last_out = out
            self.bar.config(value=100)
            self.open_btn.config(state="normal")
            self.write(f"✔ Xong {os.path.basename(out)} — {n} câu, ngôn ngữ {lang}, {_fmt(secs)}")
        elif kind == "log":
            self.write(a[0])
        elif kind == "tstart":
            self.bar.config(value=0)
            self._tr_name = a[1]
            self.write(f"  → Dịch sang {a[0]} bằng {a[1]}")
        elif kind == "tprogress":
            d, n = a
            self.bar.config(value=100 * d / n)
            self.stage.config(text=f"{getattr(self, '_tr_name', 'Claude')} đang dịch: lô {d}/{n}")
        elif kind == "tdone":
            out, n, missing, cost, secs, splits = a
            self.last_out = out
            self.bar.config(value=100)
            self.open_btn.config(state="normal")
            miss = f", {missing} câu giữ nguyên gốc" if missing else ""
            miss += f", tách {splits} câu gộp" if splits else ""
            self.write(f"✔ Xong bản dịch {os.path.basename(out)} — {n} câu{miss}, {_fmt(secs)}"
                       + (f", ~${cost:.2f}" if cost else ""))
        elif kind == "ucheck":
            self.on_update_checked(*a)
        elif kind == "uprogress":
            d, n = a
            if n:
                self.bar.config(value=100 * d / n)
            self.stage.config(text=f"Đang tải bản mới: {d / 1e6:.1f}" + (f" / {n / 1e6:.1f} MB" if n else " MB"))
        elif kind == "udone":
            self.on_update_done(*a)
        elif kind == "deps":
            self.show_deps(a[0])
        elif kind == "dstart":
            self.bar.config(value=0)
            self.write(f"  → Thuyết minh bằng VieNeu, giọng {a[0]}")
        elif kind == "dprogress":
            label, stage, i, n = a
            self.bar.config(value=100 * i / max(n, 1))
            self.stage.config(text=f"{label} {i}/{n}" if stage != "mix" else f"{label}: {100 * i // max(n, 1)}%")
        elif kind == "ddone":
            out, n, secs = a
            self.last_out = out
            self.bar.config(value=100)
            self.open_btn.config(state="normal")
            self.write(f"✔ Xong thuyết minh {os.path.basename(out)} — {n} câu, {_fmt(secs)}")
        elif kind == "error":
            name, msg = a
            self.errors.append(name)
            self.write(f"✘ Lỗi với {name}: {msg}")
        elif kind == "cancelled":
            self.set_running(False)
            self.fill_fields()
            self.stage.config(text="Đã dừng.")
            self.write("■ Đã dừng.")
        elif kind == "finished":
            self.set_running(False)
            self.fill_fields()
            if self.errors:
                self.stage.config(text=f"Xong, có {len(self.errors)} file lỗi (xem log bên dưới).")
            else:
                self.stage.config(text="Xong! Các file .srt nằm cùng thư mục với video.")
                self.root.bell()

    def write(self, line: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------- ảnh, icon (Font Awesome vẽ sẵn: scripts/make_fa_icons.py)
    def _img(self, name: str) -> tk.PhotoImage | None:
        if not hasattr(self, "_imgs"):
            self._imgs: dict[str, tk.PhotoImage] = {}
        if name not in self._imgs:
            try:
                self._imgs[name] = tk.PhotoImage(master=self.root, file=os.path.join(ASSETS, name))
            except tk.TclError:
                return None
        return self._imgs[name]

    def _button(self, parent, icon: str, text: str, command, **kw) -> ttk.Button:
        """Nút có icon Font Awesome bên trái chữ; nút bị khoá thì icon xám."""
        on, off = self._img(f"fa-{icon}.png"), self._img(f"fa-{icon}-off.png")
        if on is None:
            return ttk.Button(parent, text=text, command=command, **kw)
        image = (on, "disabled", off) if off is not None else on
        return ttk.Button(parent, text=" " + text, image=image, compound="left", command=command, **kw)

    def _link(self, parent, text: str, action, bold: bool = False) -> ttk.Label:
        """Chữ bấm được (như link web): màu xanh, gạch chân khi rê chuột."""
        from tkinter import font as tkfont

        key = "_link_fonts_bold" if bold else "_link_fonts"
        if not hasattr(self, key):
            base = tkfont.nametofont("TkDefaultFont").actual()
            if bold:
                base["weight"] = "bold"
            setattr(self, key, (tkfont.Font(**base), tkfont.Font(**{**base, "underline": 1})))
        plain, under = getattr(self, key)
        lbl = ttk.Label(parent, text=text, foreground=LINK_COLOR, cursor=HAND, font=plain)
        lbl.bind("<Button-1>", lambda _e: action())
        lbl.bind("<Enter>", lambda _e: lbl.config(font=under))
        lbl.bind("<Leave>", lambda _e: lbl.config(font=plain))
        return lbl

    def show_about(self) -> None:
        from tkinter import font as tkfont

        old = getattr(self, "_about", None)
        if old is not None and old.winfo_exists():
            old.deiconify()
            old.lift()
            old.focus_force()
            return
        w = tk.Toplevel(self.root)
        self._about = w
        w.title("Giới thiệu vsub")
        w.resizable(False, False)
        w.transient(self.root)
        body = ttk.Frame(w, padding=(24, 20))
        body.pack(fill="both", expand=True)

        head = ttk.Frame(body)
        head.pack(fill="x")
        icon = self._img("vsub-64.png")
        if icon:
            ttk.Label(head, image=icon).pack(side="left", padx=(0, 14))
        names = ttk.Frame(head)
        names.pack(side="left")
        big = tkfont.nametofont("TkDefaultFont").actual()
        big.update(size=int(big["size"] * 1.8) or 22, weight="bold")
        self._about_font = tkfont.Font(**big)
        ttk.Label(names, text="vsub", font=self._about_font).pack(anchor="w")
        ttk.Label(names, text=f"Phiên bản {__version__}", foreground="gray").pack(anchor="w")

        ttk.Label(body, text=ABOUT_TEXT, justify="left", wraplength=460).pack(anchor="w", pady=(16, 0))

        ttk.Separator(body).pack(fill="x", pady=16)
        dev = ttk.Frame(body)
        dev.pack(fill="x")
        logo = self._img("viennv-64.png")
        if logo:
            lg = ttk.Label(dev, image=logo, cursor=HAND)
            lg.pack(side="left", padx=(0, 14))
            lg.bind("<Button-1>", lambda _e: open_site())
        info = ttk.Frame(dev)
        info.pack(side="left")
        ttk.Label(info, text="Phát triển bởi", foreground="gray").pack(anchor="w")
        self._link(info, "viennv.dev", open_site, bold=True).pack(anchor="w")
        ttk.Label(info, text=SITE_TAGLINE, foreground="gray", wraplength=360, justify="left").pack(anchor="w")

        ttk.Label(body, text=CREDITS, foreground="gray", justify="left", wraplength=460).pack(
            anchor="w", pady=(16, 0))
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(18, 0))
        upd = ttk.Frame(body)
        upd.pack(fill="x", pady=(16, 0))
        ttk.Button(upd, text="Kiểm tra cập nhật", command=lambda: self.check_update(manual=True)).pack(side="left")
        auto = tk.BooleanVar(value=load_settings().get("auto_update", True))
        auto.trace_add("write", lambda *_: save_settings({**load_settings(), "auto_update": auto.get()}))
        w._auto_var = auto  # giữ tham chiếu
        ttk.Checkbutton(upd, text="Tự kiểm tra bản mới khi mở app", variable=auto).pack(side="left", padx=12)

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(18, 0))
        ttk.Button(btns, text="Đóng", command=w.destroy).pack(side="right")
        ttk.Button(btns, text="Mở viennv.dev", command=open_site).pack(side="right", padx=8)
        ttk.Label(btns, text=f"© {time.localtime().tm_year} viennv.dev", foreground="gray").pack(side="left")
        w.bind("<Escape>", lambda _e: w.destroy())

        w.update_idletasks()  # đặt giữa cửa sổ chính
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + max(40, (self.root.winfo_height() - w.winfo_reqheight()) // 3)
        w.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        w.focus_force()

    # ---------- bản mới
    def check_update(self, manual: bool = False) -> None:
        """Tự động (lúc mở app, tối đa 6 giờ/lần, tắt được) hoặc khi người dùng bấm "Kiểm tra cập nhật"."""
        from .update import cached_check, is_newer

        st = load_settings()
        if not manual:
            if not st.get("auto_update", True):
                return
            should, latest = cached_check(st)
            if not should:  # dùng kết quả lần trước
                rel = st.get("update_release")
                if latest and rel and is_newer(latest):
                    self.show_banner(self._release_from(rel))
                return

        def run() -> None:
            from .update import UpdateError, fetch_latest

            try:
                self.emit("ucheck", fetch_latest(), manual, None)
            except UpdateError as e:
                self.emit("ucheck", None, manual, str(e))

        if manual:
            self.stage.config(text="Đang kiểm tra bản mới…")
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _release_from(d: dict):
        from .update import Release

        return Release(d["version"], d["tag"], d["url"], d.get("notes", ""), d.get("published", ""),
                       {k: tuple(v) for k, v in (d.get("assets") or {}).items()})

    def on_update_checked(self, rel, manual: bool, err: str | None) -> None:
        from dataclasses import asdict

        from .update import LATEST_URL, is_newer

        if manual:
            self.stage.config(text="Sẵn sàng." if not self.running else self.stage.cget("text"))
        if err:
            if manual and messagebox.askyesno("Không kiểm tra được",
                                              f"{err}.\n\nMở trang phát hành trên GitHub để xem?"):
                webbrowser.open(LATEST_URL)
            return
        save_settings({**load_settings(), "update_checked_at": time.time(), "update_latest": rel.version,
                       "update_release": asdict(rel)})
        if is_newer(rel.version):
            self.show_banner(rel)
            self.write(f"🔔 Có bản mới vsub {rel.version} (đang dùng {__version__})")
            if manual and messagebox.askyesno("Có bản mới", f"Có bản mới vsub {rel.version} "
                                              f"(đang dùng {__version__}).\n\nCập nhật ngay?"):
                self.do_update()
        elif manual:
            messagebox.showinfo("Đã là bản mới nhất", f"Bạn đang dùng bản mới nhất (vsub {__version__}).")

    def show_banner(self, rel, text: str | None = None) -> None:
        self.release = rel
        for b in self.banner_btns:
            b.destroy()
        self.banner_btns = []
        self.banner_lbl.config(text=text or f"🔔  Có bản mới vsub {rel.version}"
                               + (f" ({rel.published})" if rel.published else "") + f" — bạn đang dùng {__version__}.")

        def add(widget) -> None:
            widget.pack(side="right", padx=(8, 0))
            self.banner_btns.append(widget)

        close = tk.Label(self.banner, text="✕", bg=BANNER_BG, fg="white", cursor=HAND, padx=4)
        close.bind("<Button-1>", lambda _e: self.banner.pack_forget())
        add(close)
        if text is None:
            add(ttk.Button(self.banner, text="Cập nhật ngay", command=self.do_update))
            add(ttk.Button(self.banner, text="Có gì mới?", command=self.show_notes))
            from .update import LATEST_URL

            manual = tk.Label(self.banner, text="Tải thủ công", bg=BANNER_BG, fg="#bfdbfe", cursor=HAND)
            manual.bind("<Button-1>", lambda _e: webbrowser.open(LATEST_URL))
            add(manual)
        if not self.banner.winfo_manager():
            self.banner.pack(fill="x", pady=(0, 12), before=self._top_row)

    def show_notes(self) -> None:
        rel = self.release
        if not rel:
            return
        w = tk.Toplevel(self.root)
        w.title(f"vsub {rel.version} — có gì mới")
        w.transient(self.root)
        body = ttk.Frame(w, padding=16)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, width=80, height=22, wrap="word", relief="solid", borderwidth=1)
        txt.insert("1.0", rel.notes.strip() or "(Bản phát hành này không có ghi chú.)")
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Đóng", command=w.destroy).pack(side="right")
        ttk.Button(btns, text="Cập nhật ngay", command=lambda: (w.destroy(), self.do_update())).pack(side="right", padx=8)
        ttk.Button(btns, text="Mở trên GitHub", command=lambda: webbrowser.open(rel.url)).pack(side="left")
        w.bind("<Escape>", lambda _e: w.destroy())

    def do_update(self) -> None:
        """Cập nhật tự động; bản cài không tự cập nhật được thì hướng dẫn tải thủ công."""
        from .update import LATEST_URL, asset_for, describe_mode, update_mode

        rel = self.release
        if not rel:
            return
        if self.running:
            messagebox.showinfo("Đang bận", "App đang chạy việc khác. Đợi xong (hoặc bấm Dừng) rồi cập nhật.")
            return
        mode, path = update_mode()
        if mode == "manual" or (mode != "git" and not asset_for(rel, mode)):
            if messagebox.askyesno(
                "Cập nhật thủ công",
                f"Bản cài này không tự cập nhật được.\n\nMở trang tải vsub {rel.version}, tải file cho máy bạn "
                "(Mac: vsub-mac-universal.zip, Windows: vsub-windows-amd64.zip), giải nén đè lên bản cũ rồi mở lại.",
            ):
                webbrowser.open(LATEST_URL)
            return
        if not messagebox.askyesno(f"Cập nhật lên {rel.version}?",
                                   f"App sẽ {describe_mode(mode)}.\n\n{path}\n\nTiếp tục?"):
            return
        self.cancel.clear()
        self.set_running(True)
        for b in self.banner_btns:
            if isinstance(b, ttk.Button):
                b.config(state="disabled")
        self.write(f"\n⟳ Cập nhật lên vsub {rel.version} ({describe_mode(mode)})")

        def run() -> None:
            from .update import Cancelled as UCancelled
            from .update import apply_update

            try:
                used = apply_update(rel, status=lambda m: self.emit("log", "  " + m),
                                    progress=lambda d, n: self.emit("uprogress", d, n), cancel=self.cancel)
                self.emit("udone", used, None)
            except UCancelled:
                self.emit("udone", None, "Đã dừng cập nhật")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.emit("udone", None, str(e) or e.__class__.__name__)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def on_update_done(self, mode: str | None, err: str | None) -> None:
        from .update import LATEST_URL, relaunch

        self.set_running(False)
        rel = self.release
        if err:
            self.write(f"✘ Cập nhật không xong: {err}")
            self.stage.config(text="Cập nhật không xong.")
            if rel:
                self.show_banner(rel)
            if messagebox.askyesno("Cập nhật không xong", f"{err}\n\nMở trang tải để cập nhật thủ công?"):
                webbrowser.open(LATEST_URL)
            return
        self.bar.config(value=100)
        self.stage.config(text=f"Đã cập nhật lên {rel.version}.")
        self.write(f"✔ Đã cập nhật lên vsub {rel.version}")
        self.show_banner(rel, text=f"✔  Đã cập nhật lên vsub {rel.version} — mở lại app để dùng bản mới.")
        if messagebox.askyesno("Đã cập nhật", f"Đã cập nhật lên vsub {rel.version}. Mở lại app ngay?"):
            relaunch(mode)
            self.root.after(300, self.root.destroy)

    def reveal(self) -> None:
        if not self.last_out:
            return
        from .paths import reveal

        reveal(self.last_out)


def main(argv: list[str] | None = None) -> None:
    from .paths import utf8_stdio

    utf8_stdio()
    _fix_path_for_finder()
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()  # Tk có hỗ trợ kéo thả
    except Exception:  # noqa: BLE001 — thiếu thư viện / không nạp được tkdnd -> Tk thường
        root = tk.Tk()
    args = list(sys.argv[1:] if argv is None else argv)
    App(root, files=[a for a in args if not a.startswith("-psn")])  # macOS cũ chèn "-psn_0_…"
    off = 40 * (os.getpid() % 6)  # cửa sổ mới lệch chỗ để không chồng khít lên nhau
    root.geometry(f"+{80 + off}+{60 + off}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()


if __name__ == "__main__":
    main()
