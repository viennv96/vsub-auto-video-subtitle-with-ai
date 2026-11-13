"""Kiểm tra và cài các thành phần app cần: FFmpeg, Whisper (thư viện + model), Claude CLI, VieNeu-TTS.

Kiểm tra chạy nhanh (không nạp model). Lệnh cài được viết thành một script .command rồi mở bằng
Terminal: người dùng thấy rõ từng lệnh, nhập mật khẩu / đăng nhập được khi cần. Xong thì quay lại app,
app tự kiểm tra lại.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .paths import IS_MAC, IS_WIN, cache_dir, data_dir, extra_paths, venv_python

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cỡ tải lần đầu (để báo trước cho người dùng)
MODEL_SIZES = {"large-v3-turbo": "1,6 GB", "large-v3": "3,1 GB", "medium": "1,5 GB", "small": "0,5 GB",
               "base": "0,15 GB", "tiny": "0,08 GB"}


@dataclass
class Dep:
    key: str            # ffmpeg | whisper | model | sepmodel | claude | vieneu
    name: str
    need: str           # "Bắt buộc" | "Để dịch" | "Để thuyết minh" ...
    purpose: str
    ok: bool
    detail: str         # đang có gì / thiếu gì
    action: str | None  # nhãn nút sửa: "Cài đặt", "Đăng nhập", "Tải trước"
    guide: str          # hướng dẫn làm tay
    required: bool = False


def _run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def find_brew() -> str | None:
    return shutil.which("brew") or next(
        (p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.isfile(p)), None)


def find_uv() -> str | None:
    home = os.path.expanduser("~")
    exe = "uv.exe" if IS_WIN else "uv"
    return shutil.which("uv") or next(
        (p for p in ("/opt/homebrew/bin/uv", os.path.join(home, ".local", "bin", exe),
                     os.path.join(home, ".cargo", "bin", exe)) if os.path.isfile(p)), None)


def vieneu_home() -> str:
    """Nơi app tự cài VieNeu (venv + models/ cạnh nhau)."""
    return os.path.join(data_dir(), "vieneu")


# ------------------------------------------------------------------ kiểm tra

def check_ffmpeg() -> Dep:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    ok = bool(ff and fp)
    detail = "Chưa có ffmpeg / ffprobe"
    if ok:
        r = _run([ff, "-version"])
        first = (r.stdout.splitlines() or [""])[0] if r else ""
        detail = first.split(" Copyright")[0].replace("ffmpeg version", "Phiên bản").strip() or ff
    guide = ("Tải bản build sẵn (ffmpeg-master-latest-win64-gpl.zip) ở\n"
             "  https://github.com/BtbN/FFmpeg-Builds/releases\n"
             f"giải nén, chép thư mục bin vào {os.path.join(data_dir(), 'ffmpeg')} (hoặc thêm vào PATH)."
             if IS_WIN else "Cài Homebrew (https://brew.sh) rồi chạy trong Terminal:\n  brew install ffmpeg")
    return Dep("ffmpeg", "FFmpeg", "Bắt buộc", "Tách audio, ghép phụ đề và giọng đọc vào video", ok, detail,
               None if ok else "Cài đặt", guide, required=True)


def _whisper_lib() -> str:
    from .backends import is_apple_silicon

    return "mlx-whisper" if is_apple_silicon() else "faster-whisper"


def check_whisper() -> Dep:
    lib = _whisper_lib()
    ok = importlib.util.find_spec(lib.replace("-", "_")) is not None
    detail = f"{lib} đã cài" if ok else f"Chưa cài {lib}"
    if ok:
        try:
            from importlib.metadata import version

            detail = f"{lib} {version(lib)}"
        except Exception:  # noqa: BLE001
            pass
    return Dep("whisper", "Whisper", "Bắt buộc", "Nhận dạng giọng nói" + (" (GPU Apple Silicon)" if lib == "mlx-whisper" else ""),
               ok, detail, None if ok else "Cài đặt",
               f"Trong thư mục app chạy: bash setup.command\n(hoặc: uv pip install {lib})", required=True)


def _hf_hub_dir() -> str:
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    return os.path.join(home, "hub")


def model_repo(model: str) -> str | None:
    from .backends import MLX_MODELS, is_apple_silicon

    if not is_apple_silicon():
        return None  # faster-whisper tự tải theo tên, không kiểm tra trước
    return MLX_MODELS.get(model, model if "/" in model else None)


def model_downloaded(model: str) -> bool | None:
    """True/False nếu kiểm tra được (model MLX trên Hugging Face), None nếu không kiểm tra được."""
    repo = model_repo(model)
    if not repo:
        return None
    if os.path.isdir(repo):  # đường dẫn thư mục model có sẵn
        return True
    base = os.path.join(_hf_hub_dir(), "models--" + repo.replace("/", "--"), "snapshots")
    for snap in glob.glob(os.path.join(base, "*")):
        has_cfg = os.path.exists(os.path.join(snap, "config.json"))
        has_w = any(os.path.exists(p) for p in glob.glob(os.path.join(snap, "*.safetensors"))
                    + glob.glob(os.path.join(snap, "*.npz")))
        if has_cfg and has_w:
            return True
    return False


def check_model(model: str) -> Dep:
    got = model_downloaded(model)
    size = MODEL_SIZES.get(model, "")
    if got is None:
        return Dep("model", f"Model {model}", "Bắt buộc", "Model nhận dạng", True,
                   "Tự tải khi chạy lần đầu", None, "")
    return Dep("model", f"Model {model}", "Tự tải lần đầu", "Model nhận dạng giọng nói", got,
               "Đã tải về máy" if got else f"Chưa tải — lần chạy đầu sẽ tải {size} (mất vài phút)",
               None if got else "Tải trước",
               "Không cần làm gì: bấm Tạo phụ đề, app tự tải. Bấm “Tải trước” để tải ngay bây giờ.")


def check_sep_model() -> Dep:
    from .separate import MODEL_SIZE, model_downloaded as sep_downloaded

    got = sep_downloaded()
    return Dep("sepmodel", "Model tách giọng", "Tự tải lần đầu", "Tách giọng khỏi nhạc nền trước khi nhận dạng",
               got, "Đã tải về máy" if got else
               f"Chưa tải — lần đầu bật “Tách giọng” sẽ tải {MODEL_SIZE / 1e6:.0f} MB",
               None if got else "Tải trước",
               "Không cần làm gì: bật “Tách giọng khỏi nhạc nền” rồi bấm Tạo phụ đề, app tự tải. "
               "Bấm “Tải trước” để tải ngay bây giờ.")


def claude_status() -> tuple[str | None, bool | None, str]:
    """(đường dẫn claude, đã đăng nhập?, phiên bản). Đăng nhập = None nếu không hỏi được."""
    from .translate import find_claude

    exe = find_claude()
    if not exe:
        return None, None, ""
    r = _run([exe, "--version"], 10)
    ver = r.stdout.strip().split(" ")[0] if r and r.returncode == 0 else ""
    r = _run([exe, "auth", "status"], 15)
    logged = None
    if r is not None:
        try:
            logged = bool(json.loads(r.stdout).get("loggedIn"))
        except ValueError:
            logged = False if r.returncode else None
    return exe, logged, ver


def check_claude() -> Dep:
    exe, logged, ver = claude_status()
    guide = ("Cài Claude Code (https://claude.com/claude-code), chạy trong "
             + ("PowerShell:\n  irm https://claude.ai/install.ps1 | iex\n" if IS_WIN
                else "Terminal:\n  curl -fsSL https://claude.ai/install.sh | bash\n")
             + "rồi đăng nhập:\n  claude auth login")
    if not exe:
        return Dep("claude", "Claude CLI", "Để dịch", "Dịch phụ đề, giữ tên riêng và xưng hô", False,
                   "Chưa cài Claude Code", "Cài đặt", guide)
    if logged is False:
        return Dep("claude", "Claude CLI", "Để dịch", "Dịch phụ đề, giữ tên riêng và xưng hô", False,
                   f"Đã cài {ver}, nhưng chưa đăng nhập", "Đăng nhập", guide)
    return Dep("claude", "Claude CLI", "Để dịch", "Dịch phụ đề, giữ tên riêng và xưng hô", True,
               f"{ver} — đã đăng nhập" if logged else f"{ver} (không hỏi được trạng thái đăng nhập)", None, guide)


AGY_PURPOSE = "Dịch bằng Gemini thay cho Claude (tài khoản Google)"


def check_agy() -> Dep:
    """Antigravity CLI (agy) — trình dịch thay thế. Không hỏi được trạng thái đăng nhập (token nằm
    trong keychain của máy), nên chỉ kiểm đã cài chưa."""
    from .translate import find_agy

    exe = find_agy()
    guide = ("Cài Antigravity CLI (https://antigravity.google/docs/cli), chạy trong "
             + ("PowerShell:\n  irm https://antigravity.google/cli/install.ps1 | iex\n" if IS_WIN
                else "Terminal:\n  curl -fsSL https://antigravity.google/cli/install.sh | bash\n")
             + "rồi chạy agy một lần để đăng nhập tài khoản Google (gõ /exit để thoát).\n"
             "Chọn “Gemini” ở mục Trình dịch trong tab Cài đặt.")
    if not exe:
        return Dep("agy", "Antigravity CLI", "Tuỳ chọn", AGY_PURPOSE, False,
                   "Chưa cài — chỉ cần khi dịch bằng Gemini", "Cài đặt", guide)
    r = _run([exe, "--version"], 10)
    ver = (r.stdout.strip().split() or [""])[-1] if r and r.returncode == 0 else ""
    return Dep("agy", "Antigravity CLI", "Tuỳ chọn", AGY_PURPOSE, True,
               (f"{ver} — " if ver else "") + "đã cài (đăng nhập một lần bằng lệnh agy)", "Đăng nhập", guide)


def check_vieneu() -> Dep:
    from .dub import find_vieneu_python

    py = find_vieneu_python(refresh=True)
    venv = os.path.join(vieneu_home(), "venv")
    guide = ("Cài VieNeu-TTS vào một venv riêng:\n"
             f"  uv venv --python 3.12 \"{venv}\"\n"
             f"  uv pip install --python \"{venv_python(venv)}\" vieneu\n"
             "Hoặc trỏ app tới venv có sẵn: biến môi trường VSUB_VIENEU_PYTHON = python của venv đó")
    if py:
        return Dep("vieneu", "VieNeu-TTS", "Để thuyết minh", "Đọc bản dịch tiếng Việt (thuyết minh)", True,
                   py.replace(os.path.expanduser("~"), "~"), None, guide)
    return Dep("vieneu", "VieNeu-TTS", "Để thuyết minh", "Đọc bản dịch tiếng Việt (thuyết minh)", False,
               "Chưa cài — chỉ cần khi bật thuyết minh", "Cài đặt", guide)


def check_all(model: str = "large-v3-turbo") -> list[Dep]:
    return [check_ffmpeg(), check_whisper(), check_model(model), check_sep_model(), check_claude(), check_agy(),
            check_vieneu()]


# ------------------------------------------------------------------ cài đặt
#
# Mỗi bước là ("say", chữ) | ("run", lệnh) | ("raw", lệnh không in ra). Lệnh viết sẵn cho shell của máy:
# bash (macOS / Linux) hoặc PowerShell (Windows).

FFMPEG_WIN_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"


def _q(s: str) -> str:
    return _psq(s) if IS_WIN else shlex.quote(s)


def _psq(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _call(exe: str, *args: str) -> str:
    cmd = " ".join(_q(a) for a in args)
    return f"& {_psq(exe)} {cmd}" if IS_WIN else f"{_q(exe)} {cmd}"


def _uv_steps() -> tuple[str, list[tuple[str, str]]]:
    """(đường dẫn uv, các bước cài uv nếu máy chưa có)."""
    uv = find_uv()
    if uv:
        return uv, []
    home = os.path.expanduser("~")
    if IS_WIN:
        return (os.path.join(home, ".local", "bin", "uv.exe"),
                [("say", "Cài uv (trình cài thư viện Python)…"),
                 ("run", "irm https://astral.sh/uv/install.ps1 | iex")])
    return (os.path.join(home, ".local", "bin", "uv"),
            [("say", "Cài uv (trình cài thư viện Python)…"),
             ("run", "curl -LsSf https://astral.sh/uv/install.sh | sh")])


def _plan(key: str, model: str) -> tuple[str, list[tuple[str, str]]]:
    """(mô tả ngắn, các bước) để cài một thành phần."""
    py = sys.executable
    steps: list[tuple[str, str]] = []
    if key == "ffmpeg":
        if IS_WIN:
            dst = os.path.join(data_dir(), "ffmpeg")
            steps += [("say", "Tải FFmpeg (~90 MB)…"),
                      ("raw", "$z = Join-Path $env:TEMP 'vsub-ffmpeg.zip'; $x = Join-Path $env:TEMP 'vsub-ffmpeg'"),
                      ("run", f"Invoke-WebRequest -UseBasicParsing {_psq(FFMPEG_WIN_URL)} -OutFile $z"),
                      ("raw", "if (Test-Path $x) { Remove-Item $x -Recurse -Force }"),
                      ("run", "Expand-Archive $z -DestinationPath $x -Force"),
                      ("raw", "$b = (Get-ChildItem $x -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName"),
                      ("raw", f"New-Item -ItemType Directory -Force {_psq(os.path.join(dst, 'bin'))} | Out-Null"),
                      ("run", f"Copy-Item (Join-Path $b '*') {_psq(os.path.join(dst, 'bin'))} -Force")]
            summary = f"Tải FFmpeg bản build sẵn (BtbN) vào {dst}"
        else:
            brew = find_brew()
            if not brew:
                steps += [("say", "Cài Homebrew (sẽ hỏi mật khẩu máy)…"),
                          ("run", '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'),
                          ("raw", 'eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"')]
            steps.append(("run", f"{_q(brew) if brew else 'brew'} install ffmpeg"))
            summary = ("Cài Homebrew, rồi " if not brew else "") + "brew install ffmpeg"
    elif key == "whisper":
        lib = _whisper_lib()
        uv, pre = _uv_steps()
        steps += pre + [("run", _call(uv, "pip", "install", "--python", py, lib))]
        summary = f"uv pip install {lib} (vào môi trường của app)"
    elif key == "model":
        repo = model_repo(model) or model
        code = f"from huggingface_hub import snapshot_download; print(snapshot_download({repo!r}))"
        steps.append(("run", _call(py, "-c", code)))
        summary = f"Tải model {repo} ({MODEL_SIZES.get(model, '')}) từ Hugging Face"
    elif key == "sepmodel":
        from .separate import MODEL_SIZE, MODEL_URL

        steps.append(("run", _call(py, "-c", "from vsub.separate import ensure_model; print(ensure_model(print))")))
        summary = f"Tải model tách giọng Kim_Vocal_2 ({MODEL_SIZE / 1e6:.0f} MB) từ {MODEL_URL.split('/releases')[0]}"
    elif key == "claude":
        from .translate import find_claude

        exe = find_claude()
        if not exe:
            home = os.path.expanduser("~")
            if IS_WIN:
                steps.append(("run", "irm https://claude.ai/install.ps1 | iex"))
                exe_after = os.path.join(home, ".local", "bin", "claude.exe")
            else:
                steps += [("run", "curl -fsSL https://claude.ai/install.sh | bash")]
                exe_after = os.path.join(home, ".local", "bin", "claude")
        steps += [("say", "Đăng nhập tài khoản Claude (trình duyệt sẽ mở)…"),
                  ("run", _call(exe or exe_after, "auth", "login"))]
        summary = ("Cài Claude Code bằng bộ cài chính thức (claude.ai), rồi " if not exe else "") + "claude auth login"
    elif key == "agy":
        from .translate import find_agy

        exe = find_agy()
        if not exe:
            home = os.path.expanduser("~")
            if IS_WIN:
                steps.append(("run", "irm https://antigravity.google/cli/install.ps1 | iex"))
                local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
                exe_after = os.path.join(local, "agy", "bin", "agy.exe")
            else:
                steps.append(("run", "curl -fsSL https://antigravity.google/cli/install.sh | bash"))
                exe_after = os.path.join(home, ".local", "bin", "agy")
        steps += [("say", "Đăng nhập tài khoản Google trong agy (trình duyệt sẽ mở). Xong gõ /exit để thoát."),
                  ("run", _call(exe or exe_after))]
        summary = ("Cài Antigravity CLI bằng bộ cài chính thức (antigravity.google), rồi " if not exe else "") \
            + "đăng nhập bằng lệnh agy"
    elif key == "vieneu":
        from .dub import WORKER

        home = vieneu_home()
        venv = os.path.join(home, "venv")
        vpy = venv_python(venv)
        uv, pre = _uv_steps()
        steps += pre + [("run", _call(uv, "venv", "--python", "3.12", "--allow-existing", venv)),
                        ("run", _call(uv, "pip", "install", "--python", vpy, "vieneu"))]
        test = os.path.join(home, "thu-giong.wav")
        # JSON toàn ASCII (\uXXXX) để đi qua pipe của mọi shell không hỏng dấu tiếng Việt
        job = json.dumps({"voice": "Trúc Ly", "items": [{"text": "Xin chào, đây là giọng thuyết minh thử.",
                                                          "out": test}]})
        steps += [("say", "Tải model VieNeu (~600 MB) và đọc thử một câu…")]
        if IS_WIN:
            steps += [("run", f"{_psq(job)} | & {_psq(vpy)} -I {_psq(WORKER)}"),
                      ("raw", f"if (Test-Path {_psq(test)}) {{ (New-Object Media.SoundPlayer {_psq(test)}).PlaySync() }}")]
        else:
            steps += [("run", f"echo {_q(job)} | {_q(vpy)} -I {_q(WORKER)}"),
                      ("raw", f"[ -f {_q(test)} ] && afplay {_q(test)} || true" if IS_MAC else "true")]
        summary = f"Tạo venv riêng ở {home.replace(os.path.expanduser('~'), '~')}, cài vieneu, tải model, đọc thử"
    else:
        raise ValueError(key)
    return summary, steps


def _render_bash(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
    out = ["#!/bin/bash", "set -e",
           f'export PATH="{":".join(extra_paths())}:$PATH"',
           'echo "vsub — cài đặt thành phần còn thiếu"']
    for i, (summary, steps) in enumerate(sections, 1):
        out += ["echo", f"echo {shlex.quote(f'=== {i}/{len(sections)}: {summary} ===')}"]
        for kind, text in steps:
            if kind == "say":
                out.append(f"echo {shlex.quote(text)}")
            elif kind == "run":
                out += [f"echo {shlex.quote('$ ' + text)}", text]
            else:
                out.append(text)
    out += ["echo", 'echo "✔ Xong. Quay lại cửa sổ vsub — app sẽ tự kiểm tra lại."']
    return "\n".join(out)


def _render_ps(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
    out = ["$ErrorActionPreference = 'Stop'", "$ProgressPreference = 'SilentlyContinue'",
           "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
           f"$env:Path = {_psq(';'.join(extra_paths()))} + ';' + $env:Path",
           "Write-Host 'vsub — cài đặt thành phần còn thiếu'", "try {"]
    for i, (summary, steps) in enumerate(sections, 1):
        out += ["  Write-Host ''", f"  Write-Host {_psq(f'=== {i}/{len(sections)}: {summary} ===')} -ForegroundColor Cyan"]
        for kind, text in steps:
            if kind == "say":
                out.append(f"  Write-Host {_psq(text)}")
            elif kind == "run":
                out += [f"  Write-Host {_psq('> ' + text)} -ForegroundColor DarkGray", f"  {text}",
                        "  if ($LASTEXITCODE) { throw \"Lệnh lỗi (mã $LASTEXITCODE)\" }"]
            else:
                out.append(f"  {text}")
    out += ["  Write-Host ''", "  Write-Host '✔ Xong. Quay lại cửa sổ vsub — app sẽ tự kiểm tra lại.' -ForegroundColor Green",
            "} catch {", "  Write-Host ''", "  Write-Host \"✘ Lỗi: $_\" -ForegroundColor Red", "}"]
    return "\r\n".join(out)


def install_plan(keys: list[str] | str, model: str = "large-v3-turbo") -> tuple[str, str]:
    """(mô tả các bước sẽ chạy — để người dùng xác nhận, nội dung script). Nhiều thành phần
    thì gộp vào một script, chạy lần lượt. Script là bash (macOS/Linux) hoặc PowerShell (Windows)."""
    keys = [keys] if isinstance(keys, str) else list(keys)
    sections = [_plan(k, model) for k in keys]
    script = _render_ps(sections) if IS_WIN else _render_bash(sections)
    return "\n".join(f"• {summary}" for summary, _ in sections), script


def run_in_terminal(key: str, script: str) -> str:
    """Ghi script ra file và mở trong cửa sổ dòng lệnh (Terminal / PowerShell). Trả về đường dẫn script."""
    base = cache_dir()
    os.makedirs(base, exist_ok=True)
    if IS_WIN:
        path = os.path.join(base, f"cai-dat-{key}.ps1")
        with open(path, "w", encoding="utf-8-sig") as f:  # PowerShell 5 cần BOM để đọc đúng tiếng Việt
            f.write(script + "\r\n")
        subprocess.Popen(["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
        return path
    path = os.path.join(base, f"cai-dat-{key}.command")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script + "\n")
    os.chmod(path, 0o755)
    if IS_MAC:
        subprocess.Popen(["open", "-a", "Terminal", path])
    else:
        term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal")
        if not term:
            raise RuntimeError(f"Không mở được terminal — hãy tự chạy: bash {path}")
        subprocess.Popen([term, "--", "bash", path])
    return path
