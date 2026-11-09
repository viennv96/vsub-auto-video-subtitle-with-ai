import os
import stat
import subprocess
import sys

import pytest

from vsub import deps

IS_WIN = sys.platform.startswith("win")


@pytest.mark.parametrize("keys", [["ffmpeg"], ["whisper"], ["model"], ["sepmodel"], ["claude"], ["vieneu"],
                                  ["ffmpeg", "whisper", "claude", "vieneu"]])
def test_install_scripts_are_valid(keys, tmp_path):
    summary, script = deps.install_plan(keys)
    assert summary.count("•") == len(keys)
    if IS_WIN:  # PowerShell: kiểm tra cú pháp bằng parser của chính PowerShell
        f = tmp_path / "s.ps1"
        f.write_text(script, encoding="utf-8-sig")
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{f}', [ref]$null, [ref]$e); if ($e) {{ $e | ForEach-Object {{ $_.Message }}; exit 1 }}")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", check], text=True, capture_output=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return
    assert script.startswith("#!/bin/bash") and "set -e" in script
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def test_vieneu_install_goes_to_own_venv_and_tests_a_voice():
    _, script = deps.install_plan("vieneu")
    assert deps.vieneu_home() in script
    assert "vieneu_worker.py" in script and "Xin ch\\u00e0o" in script  # JSON ASCII: qua pipe không hỏng dấu


def test_model_downloaded_reads_hf_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr("vsub.backends.is_apple_silicon", lambda: True)
    assert deps.model_downloaded("large-v3-turbo") is False
    snap = tmp_path / "models--mlx-community--whisper-large-v3-turbo" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")
    assert deps.model_downloaded("large-v3-turbo") is False      # chưa có trọng số
    (snap / "weights.safetensors").write_bytes(b"x")
    assert deps.model_downloaded("large-v3-turbo") is True
    d = deps.check_model("large-v3-turbo")
    assert d.ok and d.action is None


def _fake_claude(tmp_path, logged_in: bool):
    exe = tmp_path / "claude"
    exe.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "9.9.9 (Claude Code)"; exit 0; fi\n'
        f'if [ "$1" = "auth" ]; then echo \'{{"loggedIn": {"true" if logged_in else "false"}}}\'; '
        f'exit {0 if logged_in else 1}; fi\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


@pytest.mark.skipif(IS_WIN, reason="claude giả là script sh")
@pytest.mark.parametrize("logged_in", [True, False])
def test_check_claude_login_state(tmp_path, monkeypatch, logged_in):
    exe = _fake_claude(tmp_path, logged_in)
    monkeypatch.setattr("vsub.translate.find_claude", lambda: exe)
    d = deps.check_claude()
    assert d.ok is logged_in
    assert "9.9.9" in d.detail
    assert d.action == (None if logged_in else "Đăng nhập")


def test_check_claude_missing(monkeypatch):
    monkeypatch.setattr("vsub.translate.find_claude", lambda: None)
    d = deps.check_claude()
    assert not d.ok and d.action == "Cài đặt" and "claude.ai/install" in d.guide


def test_check_vieneu_missing(monkeypatch):
    monkeypatch.setattr("vsub.dub.find_vieneu_python", lambda refresh=False: None)
    d = deps.check_vieneu()
    assert not d.ok and not d.required and d.action == "Cài đặt"


def test_run_in_terminal_writes_script(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "cache_dir", lambda: str(tmp_path))
    calls = []
    monkeypatch.setattr(deps.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(deps.shutil, "which", lambda name: "/usr/bin/x-terminal-emulator")
    path = deps.run_in_terminal("claude", "echo hi")
    assert path.startswith(str(tmp_path)) and "echo hi" in open(path, encoding="utf-8-sig").read()
    assert path.endswith(".ps1" if IS_WIN else ".command")
    assert calls and path in calls[0]
    if not IS_WIN:
        assert os.access(path, os.X_OK)
