# vsub: tự động làm phụ đề, dịch và thuyết minh phim

<p align="center"><img src="docs/assets/logo-512.png" width="96" alt="vsub"></p>

Nhận dạng thoại bằng Whisper, cắt câu theo chuẩn phụ đề phim, xuất `.srt`/`.vtt`, dịch bằng Claude
(giữ tên riêng và xưng hô), thuyết minh tiếng Việt bằng VieNeu-TTS. Trên Mac chip M, app chạy bằng
**mlx-whisper** (dùng GPU). Trên máy khác thì tự chuyển sang **faster-whisper**.

**Trang giới thiệu:** https://viennv96.github.io/vsub-auto-video-subtitle-with-ai/

## Tải bản cài sẵn

| Máy | Tải về | Cách cài |
|---|---|---|
| macOS 11+ (chip M và Intel) | [vsub-mac-universal.zip](https://github.com/viennv96/vsub-auto-video-subtitle-with-ai/releases/latest/download/vsub-mac-universal.zip) | Giải nén, kéo **Vsub.app** vào Applications, mở app |
| Windows 10/11 64-bit | [vsub-windows-amd64.zip](https://github.com/viennv96/vsub-auto-video-subtitle-with-ai/releases/latest/download/vsub-windows-amd64.zip) | Giải nén ra một thư mục cố định, chạy **Vsub.cmd** |

Gói tải về chỉ chứa mã nguồn và script. Lần mở đầu, app dùng [uv](https://docs.astral.sh/uv/) cài Python và
thư viện đúng cho máy (cần mạng, vài phút, khoảng 1,3 GB): Mac chip M cài mlx-whisper (GPU), Mac Intel và Windows
cài faster-whisper (CPU). Windows còn tự tải FFmpeg và tạo shortcut **vsub** trên Desktop, Start Menu.

- macOS chặn vì app chưa được Apple ký: **Cài đặt hệ thống → Quyền riêng tư & Bảo mật → Vẫn mở**, hoặc
  `xattr -dr com.apple.quarantine /Applications/Vsub.app`.
- Windows SmartScreen cảnh báo: **More info → Run anyway**.

### Cập nhật

Mở app là app kiểm tra bản mới trên GitHub Releases (tối đa 6 giờ một lần; tắt được trong **Giới thiệu**).
Có bản mới thì hiện **banner** ở đầu cửa sổ:
- **Cập nhật ngay**: app tự tải gói mới, thay bản cũ (bản cũ để ở thư mục `backup/`), rồi mở lại.
  Chạy từ mã nguồn (git clone) thì app chạy `git pull` + `uv sync`.
- **Tải thủ công**: mở trang phát hành để tự tải, giải nén đè lên bản cũ.
- **Có gì mới?**: xem ghi chú của bản phát hành.

Muốn kiểm tra ngay: bấm **Kiểm tra cập nhật** ở cuối cửa sổ (hoặc trong **Giới thiệu**).

## Cài từ mã nguồn (macOS)

Cần [Homebrew](https://brew.sh). Mở Terminal và chạy:

```bash
git clone https://github.com/viennv96/vsub-auto-video-subtitle-with-ai.git
cd vsub-auto-video-subtitle-with-ai
bash setup.command
```

Script tự cài ffmpeg, uv và thư viện Python, rồi build **`Vsub.app`** ngay trên máy (có icon, nhận file kéo thả lên icon, chỉ một icon trên Dock). Nếu app không mở được thì chạy `bash setup.command --applet` để dùng kiểu cũ. Lần chạy nhận dạng
đầu tiên tải model từ Hugging Face (large-v3-turbo khoảng 1,6 GB).

### Kiểm tra và cài các thành phần

| Thành phần | Cần để | Ghi chú |
|---|---|---|
| FFmpeg | bắt buộc | `setup.command` tự cài qua Homebrew |
| Whisper (mlx-whisper / faster-whisper) | bắt buộc | `setup.command` tự cài |
| Model Whisper | bắt buộc | tự tải lần chạy đầu (large-v3-turbo 1,6 GB, large-v3 3,1 GB) |
| Model tách giọng (Kim_Vocal_2) | tách lời khỏi nhạc nền | tự tải lần đầu bật tách giọng (67 MB), xem mục [Tách lời khỏi nhạc nền](#tách-lời-khỏi-nhạc-nền) |
| Claude CLI (đã đăng nhập) | dịch | cài bằng `curl -fsSL https://claude.ai/install.sh \| bash`, rồi `claude auth login` |
| Antigravity CLI `agy` (tuỳ chọn) | dịch bằng Gemini thay cho Claude | cài bằng `curl -fsSL https://antigravity.google/cli/install.sh \| bash`, rồi chạy `agy` một lần để đăng nhập. Xem mục [Dịch bằng Gemini](#dịch-bằng-gemini-antigravity-cli) |
| VieNeu-TTS | thuyết minh | venv riêng, xem mục [Thuyết minh](#thuyết-minh-bằng-vieneu-tts) |

Tab **Thư viện** trong app cho biết đang có gì, thiếu gì: ✅ đủ, ❌ thiếu thứ bắt buộc, ⚠️ thiếu thứ tuỳ chọn.
Dòng nào thiếu thì có nút **Cài đặt** / **Đăng nhập** / **Tải trước**. App hỏi xác nhận, ghi rõ các lệnh sẽ chạy,
rồi mở Terminal chạy các lệnh đó (bạn thấy từng lệnh, nhập mật khẩu hay đăng nhập được). Cài xong quay lại cửa sổ
vsub, app tự kiểm tra lại. Link **Hướng dẫn** hiện lệnh để tự cài tay (chép được).

App cũng cảnh báo đúng lúc:
- Mở app mà thiếu FFmpeg / Whisper: hỏi có muốn cài ngay không.
- Bấm **Tạo phụ đề** có dịch mà trình dịch đang chọn (Claude hoặc Antigravity CLI) chưa cài hoặc chưa đăng
  nhập: chọn cài / đăng nhập ngay, hoặc chạy tiếp không dịch.
- Bật thuyết minh mà chưa có VieNeu: chọn cài ngay, hoặc chạy tiếp không thuyết minh.
- Model chưa tải: báo trước lần chạy này sẽ tải bao nhiêu GB.

Dòng lệnh: `uv run vsub doctor` in trạng thái và hướng dẫn. `uv run vsub doctor --install` cài các thứ còn
thiếu (hỏi trước khi mở Terminal), hoặc ghi tên cụ thể: `--install claude vieneu`. Antigravity CLI chỉ cài
khi ghi tên: `--install agy`.

## Dùng bằng giao diện

Double-click **`Vsub.app`** trong thư mục này (hoặc chạy `uv run vsub-gui`):

1. **Kéo thả video hoặc cả thư mục** vào cửa sổ app, hoặc thả lên icon `Vsub.app` trong Finder/Dock. Cũng có thể bấm **Chọn video…**. Danh sách tự sắp theo tên: các tập
   cùng phim đứng liền nhau, đúng thứ tự tập (`ep.2` đứng trước `ep.10`)
2. Chọn ngôn ngữ trong phim (để "Tự nhận diện" cũng được)
3. Chọn **Dịch sang** (mặc định Tiếng Việt) hoặc "Không dịch", và **Trình dịch**: Claude (mặc định) hoặc
   Gemini (Antigravity CLI), kèm model
4. Nếu muốn, mở tab **Thông tin phim** và điền cho từng phim (xem mục
   [Hồ sơ phim](#hồ-sơ-phim-phim-nhiều-tập)): nội dung phim, link liên quan, tệp đã dịch,
   xưng hô, tên riêng
5. Bấm **▶ Tạo phụ đề**. Kết quả nằm cùng thư mục với video:
   - `tên-video.srt`: phụ đề theo ngôn ngữ gốc
   - `tên-video.claude-sonnet.vi.srt`: bản dịch, tên có kèm model đã dịch (IINA, VLC, Infuse tự nhận cả
     hai file; mã ngôn ngữ `vi` để cuối nên vẫn hiện là Tiếng Việt)
6. Muốn dịch lại bằng model khác: đổi **Trình dịch** / model rồi bấm **🌐 Chỉ dịch**. App dịch lại
   `tên-video.srt` đã có, không nhận dạng lại, và ghi ra file mới theo tên model, ví dụ
   `tên-video.gemini-3.8-flash-high.vi.srt`, nên các bản dịch cũ vẫn còn để so sánh.

Log ghi ở `~/Library/Logs/vsub.log`. Có thể kéo `Vsub.app` vào Dock hoặc Applications;
nhưng đừng di chuyển hay đổi tên thư mục project (app gọi vào đó).

## Dùng bằng dòng lệnh

```bash
# Tạo phim.srt cạnh video (tự nhận ngôn ngữ)
uv run vsub "phim.mp4"

# Chỉ định ngôn ngữ (chính xác hơn, khỏi bước đoán)
uv run vsub "phim.mp4" --lang ja

# Bật / tắt tách lời khỏi nhạc nền (mặc định: bật trên Mac chip M, tắt trên máy khác)
uv run vsub "phim.mp4" --lang ja --separate
uv run vsub "phim.mp4" --lang ja --no-separate

# Gắn phụ đề mềm (bật/tắt được trong player) -> phim.sub.mp4
uv run vsub "phim.mp4" --lang ja --embed

# In cứng phụ đề lên hình -> phim.hardsub.mp4
# (video đã có phụ đề cũ ở dưới thì thêm --position top)
uv run vsub "phim.mp4" --lang ja --burn --position top

# Dịch sang tiếng Anh bằng tính năng sẵn của Whisper (nên dùng model large-v3)
uv run vsub "phim.mp4" --translate --model large-v3

# Chia dòng lại mà không nhận dạng lại (vài giây)
uv run vsub reflow "phim.transcript.json" --max-chars 36

# Chỉ gắn file phụ đề có sẵn
uv run vsub embed "phim.mp4" "phim.srt" --lang vi --burn
```

Gợi ý tên nhân vật hoặc thuật ngữ cho model: `--prompt "Tanaka, Sato, công ty Nexus"`.

## Claude đọc thêm (mặc định tắt)

Trong tab **Cài đặt**, mục "Claude đọc thêm" có 4 tuỳ chọn, mặc định đều tắt. App nhớ lựa chọn cho lần sau:

| Tuỳ chọn | CLI |
|---|---|
| Đọc các link trong tab "Thông tin phim" | `--read-links` |
| Đọc lại phụ đề đã dịch của các tập khác | `--read-prev` |
| Dùng hồ sơ phim (tên riêng, xưng hô, tóm tắt tập trước Claude đã học) | `--use-profile` |
| Ghi những gì học được vào hồ sơ phim | `--learn` |

Tắt hết thì mỗi tập được dịch độc lập. Nội dung phim, xưng hô, tên riêng bạn tự điền luôn được dùng.

## Chạy nhiều cửa sổ cùng lúc

Bấm **Cửa sổ mới** (⌘N), hoặc thả video vào một cửa sổ đang bận rồi chọn "mở cửa sổ mới".
Mỗi cửa sổ là một tiến trình riêng với model riêng, macOS tự chia GPU. Một video chỉ được
một cửa sổ xử lý. Hồ sơ phim có khoá file và được gộp khi ghi, nên nhiều cửa sổ dịch các tập
của cùng một phim không làm mất tên riêng hay xưng hô của nhau. Trước khi dịch, mỗi cửa sổ
nạp lại những gì cửa sổ khác vừa học.

## Né phụ đề có sẵn trong video

Nhiều phim đã có phụ đề in cứng ở dưới (ví dụ phụ đề tiếng Anh). Ở chế độ mặc định
`--position auto` (trong giao diện là "Tự động"), app lấy khoảng 24 khung hình ở các đoạn có thoại,
chạy bộ dò chữ (RapidOCR, model ONNX có sẵn trong gói) và tìm dòng chữ **căn giữa, thấp,
lặp lại ở cùng một dải**. Nếu thấy ở phía dưới, file `.srt` sẽ có tag `{\an8}`, nên player
(IINA, VLC, mpv, Infuse, Plex…) hiện phụ đề mới ở **phía trên**. Bản dịch cũng vậy.
Muốn cố định thì dùng `--position top` hoặc `--position bottom`.

## Tách lời khỏi nhạc nền

Trước khi nhận dạng, app tách lời thoại khỏi nhạc nền và tiếng động bằng model MDX-Net **Kim_Vocal_2**
(ONNX, chạy bằng onnxruntime có sẵn trong gói, không cần cài thêm thư viện). Sau đó VAD và Whisper đều
nghe bản chỉ còn giọng. Kết quả đo trên một tập phim Nhật 43 phút, lấy 603 câu phụ đề Anh in sẵn làm đáp án:

| | Audio gốc | Tách giọng |
|---|---|---|
| Câu có phụ đề | 568 (94,2%) | 575 (95,4%) |
| Câu thừa (không khớp câu nào, thường là chữ bịa) | 11 | 2 |
| Độ đúng của chữ (Claude chấm mù, so với phụ đề Anh) | 87,3% | 88,0% |
| Thời gian trên M1 Max | 18 phút | 2,3 phút tách + 7,6 phút nhận dạng |

Nhanh hơn vì VAD không còn coi đoạn nhạc là "có thể có thoại" nên Whisper nghe ít hơn hẳn (phim ít nhạc
thì lợi ít hơn). Mac chip M chạy model bằng GPU qua CoreML (~0,15 giây cho mỗi 6 giây audio) nên mặc định
**bật**. Máy khác chạy bằng CPU, chậm hơn khoảng 17 lần (tập 45 phút có thể thêm 30 phút trở lên), nên mặc
định **tắt**. Bật/tắt trong tab **Cài đặt** hoặc `--separate` / `--no-separate`.

Model tải về `~/Library/Application Support/vsub/models/` (Windows: `%LOCALAPPDATA%\vsub\models`), có kiểm
SHA-256. Audio được đọc theo khối 2 phút nên phim dài cũng không tốn nhiều RAM.

## Chia câu

- Tiếng Nhật và tiếng Trung: Whisper được mồi bằng một câu có dấu câu, nên tự chép ra 。、？!
  Nhờ vậy app biết chỗ hết câu.
- Ngắt khi hết câu, khi người nói nghỉ trên 0,5 giây, hoặc ở ranh giới đoạn của Whisper.
- Khi dịch, Claude được phép **tách một câu chứa lời của hai người** (hỏi và đáp liền nhau)
  thành hai câu phụ đề. Thời gian được chia theo timestamp của từng từ.

## Dịch bằng Claude CLI

Cần [Claude Code](https://www.npmjs.com/package/@anthropic-ai/claude-code) đã đăng nhập.
Chạy `claude` một lần trong Terminal để đăng nhập. App gọi `claude -p` với các tool đã tắt.

```bash
uv run vsub "phim.mp4" --lang ja --to vi                 # nhận dạng + dịch
uv run vsub translate "phim.srt" --from ja --to vi       # chỉ dịch file .srt có sẵn -> phim.claude-sonnet.vi.srt
uv run vsub translate "phim.srt" --to vi --claude-model opus   # dịch lại bằng model khác -> phim.claude-opus.vi.srt
uv run vsub translate "phim.srt" --to vi --glossary ten-rieng.txt --notes "phim học đường"
uv run vsub translate "ep.3.srt" --to vi --link "https://ja.wikipedia.org/wiki/..."   # thêm link vào hồ sơ phim
```

Cách dịch giữ nguyên tên riêng và cách xưng hô:
1. Claude đọc cả tập cùng hồ sơ phim, lập **bảng tên riêng** (người, địa danh, công ty…) với
   cách viết thống nhất, và **bảng xưng hô** theo từng cặp nhân vật (A nói với B: A tự xưng gì,
   gọi B là gì). Tên Nhật dùng romaji kèm hậu tố (Tanaka-san), tên Hàn theo Revised Romanization,
   tên Trung sang tiếng Việt dùng âm Hán-Việt. Tên và xưng hô bạn tự ghi được ưu tiên.
2. Dịch theo lô khoảng 50 câu, mỗi lô có vài câu trước và sau làm ngữ cảnh, chạy 3 lô
   song song. Với mỗi câu, Claude xác định ai đang nói với ai rồi dùng đúng cặp xưng hô, giữ
   nguyên cặp đó suốt một cảnh. Câu nào Claude trả thiếu sẽ được hỏi lại. Vẫn thiếu thì giữ câu gốc.
3. Ngắt dòng lại theo độ dài câu dịch.
4. **Tốc độ đọc**: mỗi câu gửi kèm số ký tự người xem đọc kịp trong lúc câu hiện (tiếng Việt 17 ký tự/giây,
   tính cả dấu cách và dấu câu, theo hướng dẫn phụ đề của Netflix). Claude dịch gọn khi cần nhưng ưu tiên giữ
   ý. Dịch xong, câu đọc không kịp được hiện lâu thêm tối đa 1 giây nếu còn khoảng trống (không đè câu sau,
   giờ bắt đầu giữ nguyên). Câu vẫn quá nhanh thì Claude rút gọn thêm một lượt.
   Đo trên một tập 43 phút: câu nhanh hơn 17 ký tự/giây giảm từ 20% xuống 6%, nhanh hơn 20 ký tự/giây
   từ 11% xuống 2%. Chấm mù độ giữ nghĩa ở những câu từng quá nhanh: 1,91 → 1,89 (thang 0–2).
   Đổi giới hạn bằng `--max-cps 20`, tắt bằng `--max-cps 0`.

File tên riêng (`--glossary`): mỗi dòng `tên gốc = cách viết`, dòng bắt đầu `#` là ghi chú.
Chọn model bằng `--claude-model sonnet|opus|haiku` (mặc định sonnet).

## Dịch bằng Gemini (Antigravity CLI)

Thay cho Claude, có thể dịch bằng Gemini qua [Antigravity CLI](https://antigravity.google/docs/cli) (`agy`),
dùng tài khoản Google của bạn (gói miễn phí, Google AI Pro hoặc Ultra). Quy trình dịch giống hệt: bảng tên
riêng, xưng hô, lô dịch, tốc độ đọc, hồ sơ phim.

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash   # Windows: irm https://antigravity.google/cli/install.ps1 | iex
agy                                                           # đăng nhập một lần, xong gõ /exit
uv run vsub "phim.mp4" --to vi --engine gemini
uv run vsub translate "phim.srt" --to vi --engine gemini --gemini-model gemini-3.8-flash-high
```

Trong app: tab **Cài đặt** → **Trình dịch** → "Gemini (Antigravity CLI)". Tab **Thư viện** có nút cài và
đăng nhập.

Cần biết:
- Gemini CLI cũ không còn chạy với tài khoản miễn phí và gói Google AI Pro/Ultra từ 18/06/2026. Google
  chuyển sang Antigravity CLI, nên app dùng `agy`.
- Quota tính theo tài khoản Google: gói miễn phí giới hạn theo tuần, Pro/Ultra làm mới mỗi 5 giờ. Một tập phim
  tốn khoảng 15–20 lượt gọi (1 lượt đọc cả tập, các lô dịch, lượt rút gọn). Gói miễn phí có thể hết lượt
  giữa chừng; khi đó app báo lỗi quota, câu chưa dịch giữ nguyên gốc.
- Model: để "mặc định" thì dùng model đang chọn trong `agy`. Xem tên model bằng `agy models` (agy 1.5:
  `gemini-3.8-flash-high|medium|low`, `gemini-4-argon-high|medium|low`, …). Gói miễn phí chỉ có Gemini Flash.
- `agy` không có tuỳ chọn thay system prompt hay tắt tool như `claude -p`. App ghép luật dịch vào đầu tin
  nhắn, chạy `agy` trong một thư mục trống, và dặn không dùng tool. Ở chế độ headless, `agy` tự từ chối mọi
  thao tác cần xin phép. File `~/.gemini/GEMINI.md` (nếu bạn có) vẫn được `agy` nạp.
- `agy` không báo giá tiền, nên log không in chi phí.

## Thuyết minh bằng VieNeu-TTS

App dùng [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS) (chạy trên máy) để đọc bản dịch tiếng Việt
đè lên phim, kiểu thuyết minh truyền hình. Âm gốc vẫn nghe được nhưng được giảm xuống mỗi khi có lời đọc.

VieNeu cài ở venv nào cũng được. App tự tìm venv do app cài (tab **Thư viện → VieNeu-TTS → Cài đặt**) hoặc venv có lệnh `vieneu-web` / `vieneu-stream`.
Nếu không thấy, đặt `VSUB_VIENEU_PYTHON=/đường/dẫn/venv/bin/python`. Có thư mục `models/` cạnh venv
(bản chép thật của `vieneu-v3-turbo`, `moss-codec`) thì app dùng thư mục đó, hoặc chỉ định bằng `VSUB_VIENEU_MODELS`.

Thuyết minh là **tuỳ chọn, mặc định tắt**. Mỗi lần mở app đều tắt, phải tự bật mới chạy.

**Trong giao diện**, tab **Cài đặt**, mục **Thuyết minh**:
- Tích "Bật thuyết minh bằng VieNeu", chọn **giọng đọc** (mặc định Trúc Ly) và **âm gốc khi đọc**
  (mặc định Tự động). App nhớ giọng và mức giảm cho lần sau. Thuyết minh chạy sau bước dịch, chỉ khi
  dịch sang Tiếng Việt.
- Khi bật thì hiện thêm nút **🔊 Chỉ thuyết minh**: làm thuyết minh từ bản dịch tiếng Việt mới nhất
  (`tên-video.….vi.srt`), không nhận dạng hay dịch lại.
- Dòng lệnh cũng vậy: chỉ thuyết minh khi có `--dub` hoặc dùng lệnh `vsub dub`.

Kết quả là `tên-video.thuyetminh.mp4`: hình chép nguyên (không encode lại), track âm 1 là **thuyết minh**
(mặc định), track 2 là **âm gốc**.

Các bước:
1. VieNeu đọc từng câu. Câu đã đọc được lưu cache (`~/Library/Caches/vsub/tts`), nên sửa một câu trong
   `.vi.srt` rồi chạy lại thì chỉ đọc lại câu đó.
2. Cắt khoảng lặng đầu/cuối, đặt mỗi câu đúng lúc phụ đề hiện. Câu dài hơn chỗ trống (tới câu sau) thì
   đọc nhanh lên, tối đa 1,35 lần. Đang trễ so với phụ đề thì nhanh tới 1,6 lần để đuổi kịp.
3. **Kiểm tra âm trước khi trộn**: đo mức thoại gốc để đặt mức giọng đọc (độ to tổng thể gần như phim gốc),
   rồi đo nền (nhạc, tiếng động, lời gốc) trong lúc đọc từng câu. Âm gốc giảm vừa đủ để nền thấp hơn giọng
   đọc 10 dB, trong khoảng 6–24 dB: cảnh nhạc to giảm nhiều, cảnh yên giảm ít.
4. Âm gốc giảm dần trước câu và tăng lại dần sau câu, không giật. Các câu sát nhau thì giữ mức giảm luôn.
5. **Kiểm tra bản trộn**: log ghi giọng đọc cao hơn nền bao nhiêu dB, đỉnh âm bao nhiêu, có phải nén
   chỗ nào không.

Thấy âm gốc vẫn lấn giọng đọc thì chọn mức cố định "Giảm nhiều" hoặc "Rất nhiều".
Một tập 45 phút (~600 câu) mất khoảng 5 phút trên M1 Max (VieNeu chạy CPU, Whisper rảnh GPU).

```bash
uv run vsub "phim.mp4" --lang ja --to vi --dub             # nhận dạng + dịch + thuyết minh
uv run vsub dub "phim.mp4"                                 # thuyết minh từ bản phim.….vi.srt mới nhất
uv run vsub dub "phim.mp4" --voice "Thiện Minh" --duck-db 15
```

## Hồ sơ phim, phim nhiều tập

Mỗi phim có một **hồ sơ** (`ho-so-phim.<tên phim>.json`, nằm cạnh video). Claude đọc hồ sơ trước
khi dịch, và ghi lại vào đó những gì học được sau mỗi tập. Nhờ vậy các tập gọi tên và xưng hô
giống nhau.

**Nhận phim theo tên tệp, không theo thư mục.** App bỏ số tập, độ phân giải, mã nén, phiên bản
(`v2`), mã số dài và đuôi ngôn ngữ để lấy **tên phim**. Chỉ những video **cùng tên phim, có số tập
nối tiếp nhau** mới được coi là một bộ nhiều tập. Phim lẻ nằm chung thư mục có hồ sơ riêng, không
dùng bản dịch của nhau. Tên tệp chỉ có số tập (`ep.1.v1.1677999252.720.mp4`, `Tập 2.mp4`) thì bộ
phim gắn với thư mục chứa nó; thư mục mùa kiểu `Season 2` được bỏ qua.

Tab **Thông tin phim** (ô **Phim** để chọn phim khi danh sách có nhiều phim):

| Ô | Dùng để |
|---|---|
| Nội dung phim | tóm tắt, bối cảnh, quan hệ các nhân vật |
| Link liên quan | trang phim, trang nhân vật… mỗi dòng một link. App tự đọc nội dung trang (trang cần đăng nhập hay chạy JavaScript thì không đọc được, app sẽ báo) |
| Tệp đã dịch | bản dịch ở chỗ khác hoặc của nhóm khác, phần trước… Bản dịch các tập khác của cùng phim nằm cạnh video được **tự tìm**, không cần thêm |
| Xưng hô | mỗi dòng một chiều, ví dụ `Tanaka → Sato: tôi / chị` |
| Tên riêng / ghi chú | `田中 = Tanaka; phim công sở, xưng hô lịch sự` |

**Nạp dồn:** mỗi link và mỗi bản dịch chỉ được đọc **một lần**. Những gì đọc được (tên riêng, cặp
xưng hô, tóm tắt tập, tình hình cuối tập) được ghi vào hồ sơ; lần sau app dùng hồ sơ chứ không đọc
lại. Chọn nhiều tập rồi bấm chạy thì tập sau tự dùng những gì học được ở tập trước. Bản dịch nào bị
sửa tay sau lần đọc thì lần sau được đọc lại, và chỗ sửa của bạn được ưu tiên. Tên đã chốt ở các tập
trước được giữ nguyên. Xưng hô chỉ đổi khi phim cho thấy quan hệ thay đổi, và áp từ tập đó trở đi.

Dòng lệnh cũng dùng hồ sơ: `--link` và `--ref` (lặp được) thêm link hoặc tệp đã dịch vào hồ sơ,
`--no-profile` để dịch mà không dùng hồ sơ.

## Cách hoạt động

1. **Tách audio** bằng ffmpeg: mono 16 kHz, có lọc nhẹ highpass/lowpass. File bị mất gói audio giữa phim
   (có tập hụt 6 khúc, mỗi khúc 8 giây) được lấp bằng khoảng lặng để phụ đề sau chỗ hụt không bị sớm dần.
   Nếu bật tách giọng, audio đi qua model Kim_Vocal_2 trước (44,1 kHz stereo), chỉ giữ lời thoại rồi mới
   hạ về mono 16 kHz.
2. **VAD** (Silero): tìm các đoạn có thoại. Silero hay bỏ sót lời nói lẫn nhạc nền hoặc giọng
   qua điện thoại, nên khoảng trống nào audio vẫn to (trên −40 dB) cũng được đưa vào Whisper.
   Chỉ những đoạn im lặng thật mới bị bỏ. Câu nhận ra trong vùng "VAD không chắc" bị lọc gắt hơn
   để tránh chữ bịa ở đoạn chỉ có nhạc. Sau đó gom thành chunk ≤ 28 giây.
3. **Whisper** nhận dạng từng chunk, lấy timestamp từng từ, tắt
   `condition_on_previous_text` để tránh lặp vòng. Nếu không có `--lang`, app chốt
   ngôn ngữ sau khoảng 45 giây thoại đầu, rồi chạy lại những chunk đầu bị nhận sai.
4. **Lọc câu bịa**: "Hãy subscribe kênh…", "Thanks for watching", câu lặp liên tục,
   câu model không tự tin.
5. **Chia dòng**: tối đa 2 dòng × 42 ký tự (CJK 16), ngắt ở cuối câu, khi người nói dừng,
   hoặc sau dấu phẩy. Mỗi cue hiện ít nhất 1 giây và không chồng lên cue sau.
6. Kết quả thô lưu ở `*.transcript.json` để chỉnh định dạng lại mà không chạy model.

## Model

| `--model` | Ghi chú |
|---|---|
| `large-v3-turbo` (mặc định) | Nhanh, chính xác tốt. Không nên dùng cho `--translate` |
| `large-v3` | Chính xác nhất, chậm hơn khoảng 2-3 lần |
| `medium`, `small` | Nhẹ, dùng để thử nhanh |

Có thể truyền thẳng repo Hugging Face, ví dụ `--model mlx-community/whisper-large-v3-mlx`.

## Phát hành

Đẩy tag `vX.Y.Z` (khớp `version` trong `pyproject.toml` và `src/vsub/__init__.py`), GitHub Actions sẽ chạy test
trên macOS + Windows, đóng gói `vsub-mac-universal.zip` và `vsub-windows-amd64.zip`, thử cài như người dùng rồi tạo
GitHub Release. Landing page nằm trong `docs/` (GitHub Pages).

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Đóng gói thử trên máy: `bash scripts/build_mac.sh` (Mac), `pwsh scripts/build_windows.ps1` (Windows).

## Phát triển

```bash
uv sync --extra dev
uv run pytest
```

---

Phát triển bởi [viennv.dev](https://viennv.dev). Trong app: footer ở đáy cửa sổ và nút **Giới thiệu** (hoặc menu vsub → About).
