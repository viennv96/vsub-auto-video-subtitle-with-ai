"""Vẽ icon Font Awesome (Free, Solid) thành PNG cho giao diện Tk: src/vsub/assets/fa-*.png.

Tk không dùng được font icon trực tiếp, nên vẽ sẵn ra ảnh. Mỗi icon có một màu nhìn rõ trên cả
nền sáng lẫn tối, kèm bản xám (-off) cho lúc nút bị khoá.

  curl -fsSL -o /tmp/fa-solid-900.ttf \\
    https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/webfonts/fa-solid-900.ttf
  uv run python scripts/make_fa_icons.py /tmp/fa-solid-900.ttf

Icon: Font Awesome Free 6.7.2 — https://fontawesome.com, giấy phép CC BY 4.0.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "vsub", "assets")
GREY = "#8E8E93"

# tên: (mã glyph, màu, cỡ px, có bản xám không)
ICONS = {
    "play": (0xF04B, "#34C759", 14, True),           # Tạo phụ đề
    "language": (0xF1AB, "#0A84FF", 16, True),       # Chỉ dịch
    "volume": (0xF028, "#FF9F0A", 16, True),         # Chỉ thuyết minh
    "stop": (0xF04D, "#FF453A", 13, True),           # Dừng
    "folder": (0xF07C, "#0A84FF", 16, True),         # Mở thư mục
    "ok": (0xF058, "#34C759", 18, False),            # thư viện: đã có
    "missing": (0xF057, "#FF453A", 18, False),       # thư viện: thiếu, bắt buộc
    "warn": (0xF071, "#FF9F0A", 18, False),          # thư viện: thiếu, tuỳ chọn / tab cảnh báo
    "warn-sm": (0xF071, "#FF9F0A", 14, False),
}
SCALE = 8  # vẽ to rồi thu nhỏ cho mịn


def render(font_path: str, code: int, color: str, size: int) -> Image.Image:
    big = size * SCALE
    font = ImageFont.truetype(font_path, big)
    ch = chr(code)
    x0, y0, x1, y1 = font.getbbox(ch)
    w, h = x1 - x0, y1 - y0
    k = min(big / w, big / h)
    if k < 1:  # glyph rộng hơn ô: vẽ nhỏ lại cho vừa
        font = ImageFont.truetype(font_path, int(big * k))
        x0, y0, x1, y1 = font.getbbox(ch)
        w, h = x1 - x0, y1 - y0
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(img).text(((big - w) / 2 - x0, (big - h) / 2 - y0), ch, font=font, fill=color)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    font_path = sys.argv[1]
    for name, (code, color, size, off) in ICONS.items():
        render(font_path, code, color, size).save(os.path.join(OUT, f"fa-{name}.png"), optimize=True)
        if off:
            render(font_path, code, GREY, size).save(os.path.join(OUT, f"fa-{name}-off.png"), optimize=True)
    print("đã ghi", len(ICONS), "icon vào", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
