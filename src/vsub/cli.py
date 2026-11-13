"""Giao diện dòng lệnh.

  vsub phim.mp4                         # tạo phim.srt cạnh video
  vsub phim.mp4 --lang vi --embed       # + xuất phim.sub.mp4 có phụ đề mềm
  vsub phim.mp4 --burn                  # + in cứng phụ đề lên hình
  vsub reflow phim.transcript.json      # chia dòng lại, không cần nhận dạng lại
  vsub embed phim.mp4 phim.claude-sonnet.vi.srt  # chỉ gắn phụ đề có sẵn
  vsub phim.mp4 --to vi                 # + dịch sang tiếng Việt bằng Claude -> phim.claude-sonnet.vi.srt
  vsub translate phim.srt --to vi --claude-model opus  # chỉ dịch lại phim.srt có sẵn -> phim.claude-opus.vi.srt
  vsub phim.mp4 --to vi --engine gemini # dịch bằng Gemini qua Antigravity CLI (agy) thay cho Claude
  vsub phim.mp4 --to vi --dub           # + thuyết minh bằng VieNeu -> phim.thuyetminh.mp4
  vsub dub phim.mp4                     # thuyết minh từ bản phim.*.vi.srt mới nhất
  vsub doctor                           # kiểm tra FFmpeg, Whisper, Claude, VieNeu; --install để cài
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__

COMMANDS = {"run", "reflow", "embed", "translate", "dub", "doctor"}


def _add_reflow_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("định dạng phụ đề")
    g.add_argument("--max-chars", type=int, default=42, help="ký tự tối đa mỗi dòng (mặc định 42)")
    g.add_argument("--max-lines", type=int, default=2, help="số dòng tối đa mỗi cue (mặc định 2)")
    g.add_argument("--max-duration", type=float, default=6.0, help="giây tối đa mỗi cue (mặc định 6)")
    g.add_argument("--keep-hallucinations", action="store_true", help="không lọc câu Whisper hay bịa")


def _add_position_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--position", default="auto", choices=["auto", "bottom", "top"],
        help="vị trí phụ đề; auto = kiểm tra video, có phụ đề in sẵn ở dưới thì đặt lên trên",
    )


def _reflow_opts(a):
    from .reflow import ReflowOptions

    return ReflowOptions(max_chars=a.max_chars, max_lines=a.max_lines, max_duration=a.max_duration)


def _add_translate_args(p: argparse.ArgumentParser, flag: str = "--to") -> None:
    g = p.add_argument_group("dịch bằng Claude CLI hoặc Gemini (Antigravity CLI)")
    g.add_argument(flag, dest="to", help="dịch phụ đề sang ngôn ngữ này (vi, en, zh, ko...)")
    g.add_argument("--engine", choices=["claude", "gemini"], default="claude",
                   help="trình dịch: claude (Claude Code, mặc định) hoặc gemini (Antigravity CLI — agy)")
    g.add_argument("--glossary", help="file tên riêng, mỗi dòng: 田中 = Tanaka")
    g.add_argument("--notes", help="ghi chú cho Claude: bối cảnh phim, cách xưng hô...")
    g.add_argument("--claude-model", default="sonnet", help="model Claude (sonnet, opus, haiku...)")
    g.add_argument("--gemini-model", default="",
                   help="model cho --engine gemini, ví dụ gemini-3.8-flash-high (xem: agy models);"
                        " bỏ trống = model đang chọn trong agy")
    g.add_argument("--link", action="append", default=[],
                   help="link về phim (trang phim, nhân vật…) — ghi vào hồ sơ phim, đọc một lần; lặp được")
    g.add_argument("--ref", action="append", default=[],
                   help="tệp phụ đề đã dịch để tham khảo (nhóm khác, phần trước…) — ghi vào hồ sơ; lặp được")
    g.add_argument("--read-links", action="store_true", help="đọc các link trong hồ sơ phim (mặc định tắt)")
    g.add_argument("--read-prev", action="store_true",
                   help="đọc lại phụ đề đã dịch của các tập khác cùng phim (mặc định tắt)")
    g.add_argument("--use-profile", action="store_true",
                   help="dùng tên riêng / xưng hô / tóm tắt các tập trước Claude đã học (mặc định tắt)")
    g.add_argument("--learn", action="store_true",
                   help="ghi những gì học được ở tập này vào hồ sơ phim (mặc định tắt)")
    g.add_argument("--no-profile", action="store_true",
                   help="không đụng tới hồ sơ phim (kể cả nội dung / xưng hô bạn tự điền)")
    g.add_argument("--max-cps", type=float,
                   help="tốc độ đọc tối đa của bản dịch, ký tự/giây (mặc định theo ngôn ngữ: tiếng Việt 17;"
                        " 0 = không kiểm)")


def _do_translate(srt: str, source: str | None, a, cues=None, position: str | None = None) -> str:
    from .formats import parse_srt, srt_position, write_subs
    from .translate import make_runner, model_tag, parse_glossary_text, translate_cues, translated_path

    with open(srt, encoding="utf-8") as f:
        raw = f.read()
    position = position or srt_position(raw)
    cues = cues or parse_srt(raw)
    glossary = {}
    if a.glossary:
        with open(a.glossary, encoding="utf-8") as f:
            glossary = parse_glossary_text(f.read())
    tr_model = a.gemini_model if a.engine == "gemini" else a.claude_model
    out = translated_path(srt, a.to, model_tag(a.engine, tr_model))
    runner = make_runner(a.engine, tr_model)
    say = lambda m: print("• " + m, file=sys.stderr, flush=True)  # noqa: E731
    profile = story = episode = None
    if not a.no_profile:
        from .series import episode_of, open_profile, prepare, series_key

        profile = open_profile(srt)
        profile.links += [l for l in a.link if l not in profile.links]
        profile.ref_files += [profile.rel(r) for r in a.ref if profile.rel(r) not in profile.ref_files]
        episode = episode_of(srt)
        say(f"Hồ sơ phim: {profile.path}")
        kinds = tuple(k for k, on in (("link", a.read_links or bool(a.link)),
                                      ("translation", a.read_prev or bool(a.ref))) if on)
        if kinds:
            prepare(profile, series_key(srt), a.to, source, runner, out, status=say, kinds=kinds)
        story = profile.story(a.to, episode, learned=a.use_profile)
    res = translate_cues(
        cues, a.to, source, runner, glossary, a.notes,
        progress=lambda d, n: print(f"  lô {d}/{n}", file=sys.stderr, flush=True),
        status=say, story=story, max_cps=a.max_cps,
    )
    write_subs(res.cues, out, position)
    if profile and a.learn:
        from .series import remember

        remember(profile, a.to, episode, res, out)
    miss = f", {res.missing} câu giữ nguyên gốc" if res.missing else ""
    spl = f", tách {res.splits} câu gộp" if res.splits else ""
    cost = f", ~${res.cost_usd:.2f}" if res.cost_usd else ""
    print(f"✔ Bản dịch: {out} ({len(res.cues)} câu{miss}{spl}{cost})")
    return out


def _add_embed_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("gắn vào video")
    g.add_argument("--embed", action="store_true", help="xuất video có phụ đề mềm (bật/tắt được)")
    g.add_argument("--burn", action="store_true", help="xuất video có phụ đề in cứng lên hình")
    g.add_argument("--video-out", help="đường dẫn video xuất ra")
    g.add_argument("--font-size", type=int, default=18, help="cỡ chữ phụ đề cứng (mặc định 18)")
    g.add_argument("--margin", type=int, default=26, help="khoảng cách tới mép (tăng lên để né phụ đề cũ)")


def _do_embed(video, subs, lang, a, position: str = "bottom") -> None:
    from .mux import burn_in, embed_soft, make_style

    stem, ext = os.path.splitext(video)
    if a.embed:
        out = a.video_out or f"{stem}.sub{ext if ext.lower() in ('.mp4', '.mkv', '.mov', '.m4v') else '.mkv'}"
        embed_soft(video, subs, out, language=lang)
        print(f"✔ Video phụ đề mềm: {out}")
    if a.burn:
        out = a.video_out if (a.video_out and not a.embed) else f"{stem}.hardsub.mp4"
        burn_in(video, subs, out, make_style(position, a.font_size, a.margin))
        print(f"✔ Video phụ đề cứng: {out}")


def _add_dub_args(p: argparse.ArgumentParser, flag: bool = True) -> None:
    from .dub import DEFAULT_VOICE

    g = p.add_argument_group("thuyết minh bằng VieNeu-TTS")
    if flag:
        g.add_argument("--dub", action="store_true",
                       help="thuyết minh bản dịch tiếng Việt bằng VieNeu -> phim.thuyetminh.mp4")
    g.add_argument("--voice", default=DEFAULT_VOICE, help=f"giọng VieNeu (mặc định {DEFAULT_VOICE})")
    g.add_argument("--duck-db", type=float,
                   help="giảm âm gốc cố định N dB khi có lời đọc (mặc định: tự đo theo từng câu)")
    g.add_argument("--audio-track", type=int, default=0, help="track âm thanh gốc (mặc định 0)")


def _do_dub(video: str, subs: str, a, out: str | None = None) -> str:
    from .dub import Cancelled, DubError, voiceover
    from .formats import parse_srt

    with open(subs, encoding="utf-8") as f:
        cues = parse_srt(f.read())
    say = lambda m: print("• " + m, file=sys.stderr, flush=True)  # noqa: E731
    shown: dict[str, int] = {}

    def progress(stage: str, i: int, n: int) -> None:
        label = {"tts": "VieNeu đọc", "prep": "chuẩn bị", "mix": "trộn"}[stage]
        pct = int(100 * i / max(n, 1))
        if pct // 10 != shown.get(stage, -1):
            shown[stage] = pct // 10
            print(f"  {label}: {pct}%", file=sys.stderr, flush=True)

    try:
        r = voiceover(video, cues, out=out, voice=a.voice, duck_db=a.duck_db, audio_track=a.audio_track,
                      status=say, progress=progress)
    except Cancelled:
        raise KeyboardInterrupt from None
    except DubError as e:
        raise RuntimeError(str(e)) from e
    say(r.describe())
    for w in r.warnings:
        print("! " + w, file=sys.stderr)
    print(f"✔ Thuyết minh: {r.out} ({r.lines} câu, {r.elapsed / 60:.1f} phút)")
    return r.out


def cmd_run(a) -> None:
    from .audio import require_ffmpeg
    from .backends import TranscribeOptions, is_apple_silicon, make_backend
    from .formats import write_subs
    from .pipeline import Transcript, check_hardsubs, make_cues, resolve_position, transcribe_video

    require_ffmpeg()
    video = os.path.abspath(a.video)
    if not os.path.exists(video):
        sys.exit(f"Không thấy file: {video}")
    stem = os.path.splitext(video)[0]
    cache = a.transcript or f"{stem}.transcript.json"
    task = "translate" if a.translate else "transcribe"

    t = None
    separate = is_apple_silicon() if a.separate is None else a.separate
    if os.path.exists(cache) and not a.force:
        old = Transcript.load(cache)
        # chỉ nhận dạng lại vì tách giọng khi người dùng ghi rõ --separate / --no-separate
        same_sep = a.separate is None or old.separated == a.separate
        if old.model == a.model and old.task == task and same_sep and a.lang in (None, old.language):
            print(f"• Dùng lại bản nhận dạng đã lưu: {cache} (thêm --force để chạy lại)", file=sys.stderr)
            t = old
    if t is None:
        if a.translate and "turbo" in a.model:
            print("! Model turbo dịch kém, nên dùng --model large-v3 khi --translate", file=sys.stderr)
        backend = make_backend(a.backend, a.model)
        shown = [-1]

        def sep_progress(done: float, total: float) -> None:
            pct = int(100 * done / total) if total else 0
            if pct // 10 != shown[0]:
                shown[0] = pct // 10
                print(f"  tách giọng: {pct}%", file=sys.stderr, flush=True)

        t = transcribe_video(video, backend, TranscribeOptions(a.lang, task, a.prompt),
                             separate=separate, separate_progress=sep_progress)
        t.save(cache)
        print(f"• Đã lưu bản nhận dạng: {cache} ({t.elapsed / 60:.1f} phút)", file=sys.stderr)

    if a.position == "auto" and t.hardsub is None:
        print("• Kiểm tra phụ đề in sẵn trong video...", file=sys.stderr, flush=True)
        info = check_hardsubs(t, video)
        print("  " + info.describe(), file=sys.stderr)
        t.save(cache)
    position = resolve_position(a.position, t)
    if position == "top":
        print("  → Đặt phụ đề mới ở phía trên", file=sys.stderr)

    cues = make_cues(t, _reflow_opts(a), clean=not a.keep_hallucinations)
    lang = "en" if task == "translate" else t.language
    out = a.output or f"{stem}.{a.format}"
    write_subs(cues, out, position)
    print(f"✔ Phụ đề: {out} ({len(cues)} câu)")
    if a.to and a.to != lang:
        out = _do_translate(out, lang, a, cues=cues, position=position)
        lang = a.to
    _do_embed(video, out, lang, a, position)
    if a.dub:
        if lang == "vi":
            _do_dub(video, out, a)
        else:
            print("! --dub cần phụ đề tiếng Việt: thêm --to vi", file=sys.stderr)


def cmd_reflow(a) -> None:
    from .formats import write_subs
    from .pipeline import Transcript, make_cues, resolve_position

    t = Transcript.load(a.transcript)
    cues = make_cues(t, _reflow_opts(a), clean=not a.keep_hallucinations)
    base = a.transcript.removesuffix(".transcript.json")
    out = a.output or f"{base}.{a.format}"
    write_subs(cues, out, resolve_position(a.position, t))
    print(f"✔ Phụ đề: {out} ({len(cues)} câu)")


def cmd_translate(a) -> None:
    from .translate import ClaudeError

    if not a.to:
        sys.exit("Cần chọn ngôn ngữ đích, ví dụ: vsub translate phim.srt --to vi")
    try:
        _do_translate(a.srt, a.source, a)
    except ClaudeError as e:
        raise RuntimeError(str(e)) from e


def cmd_embed(a) -> None:
    if not (a.embed or a.burn):
        a.embed = True
    position = a.position
    if position == "auto":
        from .formats import parse_srt
        from .hardsub import detect_hardsubs

        with open(a.subs, encoding="utf-8") as f:
            times = [(c.start + c.end) / 2 for c in parse_srt(f.read())]
        info = detect_hardsubs(a.video, times)
        print("• " + info.describe(), file=sys.stderr)
        position = info.suggest_position()
    _do_embed(os.path.abspath(a.video), a.subs, a.lang, a, position)


def cmd_dub(a) -> None:
    video = os.path.abspath(a.video)
    if not os.path.exists(video):
        sys.exit(f"Không thấy file: {video}")
    from .translate import latest_translation

    subs = a.subs or latest_translation(os.path.splitext(video)[0] + ".srt", "vi")
    if not subs or not os.path.exists(subs):
        sys.exit(f"Không thấy phụ đề tiếng Việt: {subs or os.path.splitext(video)[0] + '.*.vi.srt'}"
                 f" (tạo bằng: vsub \"{a.video}\" --to vi)")
    if not a.subs:
        print(f"• Dùng bản dịch: {subs}", file=sys.stderr)
    _do_dub(video, subs, a, out=a.output)


def cmd_doctor(a) -> None:
    from .deps import check_all, install_plan, run_in_terminal

    deps = check_all(a.model)
    for d in deps:
        icon = "✔" if d.ok else ("✘" if d.required else "!")
        print(f"{icon} {d.name:<22} {d.need:<15} {d.detail}")
    missing = [d for d in deps if not d.ok and d.action]
    if not missing:
        print("\nĐủ cả, app chạy được hết các tính năng.")
        return
    print()
    for d in missing:
        print(f"— {d.name} ({d.need.lower()}): {d.purpose}\n  " + d.guide.replace("\n", "\n  "))
    # agy (Gemini) là trình dịch thay thế: chỉ cài khi gọi tên (--install agy)
    keys = [d.key for d in missing if d.key in (a.install or [d.key for d in missing if d.key != "agy"])]
    if a.install is not None and keys:
        summary, script = install_plan(keys, a.model)
        print(f"\nSẽ chạy:\n{summary}")
        if input("Mở Terminal để cài? [y/N] ").strip().lower() in ("y", "yes", "c", "co", "có"):
            print("Script:", run_in_terminal("-".join(keys), script))
    else:
        print("\nĐể app cài giúp: vsub doctor --install  (hoặc --install claude vieneu …)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vsub", description="Tự động làm phụ đề cho phim bằng Whisper.")
    p.add_argument("--version", action="version", version=f"vsub {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="nhận dạng giọng nói và tạo phụ đề (mặc định)")
    r.add_argument("video")
    r.add_argument("-o", "--output", help="file phụ đề xuất ra (.srt hoặc .vtt)")
    r.add_argument("-l", "--lang", help="mã ngôn ngữ của phim: vi, en, zh, ko, ja... (bỏ trống = tự nhận)")
    r.add_argument("-m", "--model", default="large-v3-turbo", help="tiny/base/small/medium/large-v3/large-v3-turbo hoặc repo HF")
    r.add_argument("--backend", default="auto", choices=["auto", "mlx", "faster-whisper"])
    r.add_argument("--translate", action="store_true", help="dịch sang tiếng Anh (tính năng sẵn của Whisper)")
    r.add_argument("--prompt", help="gợi ý cho model: tên nhân vật, thuật ngữ, văn phong")
    r.add_argument("--separate", action=argparse.BooleanOptionalAction, default=None,
                   help="tách giọng khỏi nhạc nền trước khi nhận dạng (model 67 MB). Mặc định bật trên Mac chip M"
                        " (~2-3 phút/tập, bù lại Whisper nghe ít hơn), tắt trên máy chỉ có CPU (chậm hơn nhiều)")
    r.add_argument("-f", "--format", default="srt", choices=["srt", "vtt"])
    r.add_argument("--transcript", help="nơi lưu bản nhận dạng thô (.transcript.json)")
    r.add_argument("--force", action="store_true", help="nhận dạng lại dù đã có bản lưu")
    _add_reflow_args(r)
    _add_position_arg(r)
    _add_translate_args(r)
    _add_embed_args(r)
    _add_dub_args(r)
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("translate", help="dịch file .srt có sẵn bằng Claude CLI hoặc Gemini (agy)")
    t.add_argument("srt")
    t.add_argument("--from", dest="source", help="ngôn ngữ gốc (không bắt buộc)")
    _add_translate_args(t)
    t.set_defaults(func=cmd_translate)

    f = sub.add_parser("reflow", help="chia dòng lại từ .transcript.json (nhanh, không chạy model)")
    f.add_argument("transcript")
    f.add_argument("-o", "--output")
    f.add_argument("-f", "--format", default="srt", choices=["srt", "vtt"])
    _add_reflow_args(f)
    _add_position_arg(f)
    f.set_defaults(func=cmd_reflow)

    e = sub.add_parser("embed", help="gắn file phụ đề có sẵn vào video")
    e.add_argument("video")
    e.add_argument("subs")
    e.add_argument("-l", "--lang")
    _add_position_arg(e)
    _add_embed_args(e)
    e.set_defaults(func=cmd_embed)

    d = sub.add_parser("dub", help="thuyết minh bằng VieNeu-TTS từ phụ đề tiếng Việt có sẵn")
    d.add_argument("video")
    d.add_argument("subs", nargs="?", help="phụ đề tiếng Việt (mặc định: bản <tên video>.*.vi.srt mới nhất)")
    d.add_argument("-o", "--output", help="video xuất ra (mặc định: <tên video>.thuyetminh.mp4)")
    _add_dub_args(d, flag=False)
    d.set_defaults(func=cmd_dub)

    k = sub.add_parser("doctor", help="kiểm tra / cài các thành phần cần (FFmpeg, Whisper, Claude, VieNeu)")
    k.add_argument("-m", "--model", default="large-v3-turbo", help="model Whisper sẽ dùng (để kiểm tra đã tải chưa)")
    k.add_argument("--install", nargs="*", choices=["ffmpeg", "whisper", "model", "sepmodel", "claude", "agy", "vieneu"],
                   help="cài các thành phần còn thiếu (mở Terminal, hỏi trước); không ghi tên = cài hết")
    k.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "run")
    from .paths import fix_path, utf8_stdio

    utf8_stdio()
    fix_path()
    a = build_parser().parse_args(argv)
    try:
        a.func(a)
    except KeyboardInterrupt:
        sys.exit("\nĐã dừng.")
    except RuntimeError as e:  # gồm cả ClaudeError
        sys.exit(f"Lỗi: {e}")


if __name__ == "__main__":
    main()
