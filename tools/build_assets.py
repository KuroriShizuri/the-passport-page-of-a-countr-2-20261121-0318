#!/usr/bin/env python3
"""Build-time asset generator for the Nordhavn passport entry.

Renders DejaVu glyph bitmaps and the portrait into compact binary blobs
(font.bin, portrait.bin) that main.py (stdlib only, no PIL in the sandbox)
can decode with zlib + struct.

Run once here on the dev VM; the .bin files ship in the repo.
"""
import struct
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

WORK = Path(__file__).resolve().parent
DJV = "/usr/share/fonts/truetype/dejavu"

# (font key, ttf path, pixel size)
FONTS = [
    ("serif_bold_34", f"{DJV}/DejaVuSerif-Bold.ttf", 34),
    ("serif_bold_26", f"{DJV}/DejaVuSerif-Bold.ttf", 26),
    ("serif_bold_19", f"{DJV}/DejaVuSerif-Bold.ttf", 19),
    ("sans_bold_16", f"{DJV}/DejaVuSans-Bold.ttf", 16),
    ("sans_bold_13", f"{DJV}/DejaVuSans-Bold.ttf", 13),
    ("sans_bold_11", f"{DJV}/DejaVuSans-Bold.ttf", 11),
    ("sans_15", f"{DJV}/DejaVuSans.ttf", 15),
    ("sans_13", f"{DJV}/DejaVuSans.ttf", 13),
    ("sans_11", f"{DJV}/DejaVuSans.ttf", 11),
    ("mono_15", f"{DJV}/DejaVuSansMono.ttf", 15),
    ("mono_13", f"{DJV}/DejaVuSansMono.ttf", 13),
]

# ASCII printable + the Nordic letters and punctuation the page uses.
CODEPOINTS = list(range(32, 127)) + [183, 198, 230, 216, 248, 197, 229, 201, 233, 8212, 8226]


def build_font_blob() -> bytes:
    out = bytearray()
    out += b"NFNT1"
    out += struct.pack(">H", len(FONTS))
    for key, path, size in FONTS:
        font = ImageFont.truetype(path, size)
        ascent, _descent = font.getmetrics()  # baseline sits `ascent` below the ascender top
        kb = key.encode()
        out += struct.pack(">H", len(kb)) + kb
        out += struct.pack(">H", size)
        glyphs = []
        for cp in CODEPOINTS:
            ch = chr(cp)
            # measure
            bbox = font.getbbox(ch)
            if bbox is None:
                continue
            left, top, right, bottom = bbox
            w, h = right - left, bottom - top
            if w <= 0 or h <= 0:
                # space etc: zero bitmap, keep advance
                adv = font.getlength(ch)
                glyphs.append((cp, int(round(adv)), 0, 0, 0, 0, b""))
                continue
            img = Image.new("L", (w + 2, h + 2), 0)
            d = ImageDraw.Draw(img)
            d.text((1 - left, 1 - top), ch, font=font, fill=255)
            px = img.tobytes()
            adv = font.getlength(ch)
            # bitmap row 0 sits `1 - top + ascent` px above the baseline:
            # the draw offset puts the bbox top at row 1, and the baseline
            # is `ascent` below the ascender line.
            glyphs.append((cp, int(round(adv)), w + 2, h + 2, left - 1,
                           top - 1 - ascent, px))
        out += struct.pack(">H", len(glyphs))
        for cp, adv, w, h, bx, by, px in glyphs:
            out += struct.pack(">IBBBbb", cp, adv, w, h, bx, by)
            out += px
    return zlib.compress(bytes(out), 9)


def build_portrait() -> bytes:
    src = Image.open(WORK / "portrait-source.webp").convert("RGB")
    W, H = src.size
    # Passport crop: head centred, shoulders at the bottom. Face centre ~ (0.50W, 0.36H).
    cw = int(W * 0.60)
    ch = int(cw * 232 / 176)
    cx, cy = W // 2, int(H * 0.335)
    x0 = max(0, cx - cw // 2)
    y0 = max(0, cy - ch // 2)
    y0 = min(y0, H - ch)
    x0 = min(x0, W - cw)
    crop = src.crop((x0, y0, x0 + cw, y0 + ch)).resize((176, 232), Image.LANCZOS)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
    raw = crop.tobytes()  # 176*232*3 RGB
    blob = struct.pack(">HH", 176, 232) + raw
    return zlib.compress(blob, 9)


if __name__ == "__main__":
    fb = build_font_blob()
    (WORK.parent / "font.bin").write_bytes(fb)
    print("font.bin:", len(fb), "bytes")
    pb = build_portrait()
    (WORK.parent / "portrait.bin").write_bytes(pb)
    print("portrait.bin:", len(pb), "bytes")
