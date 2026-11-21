#!/usr/bin/env python3
"""Passport of the Republic of Nordhavn -- a country that isn't there.

Generates one open passport spread (data page + visa page) and writes it to
/output/entry.png (PNG, 1024x724, well under 1 MiB).

Runs on stdlib only: the grading sandbox is python:3.13-slim with no network
and no PIL, so all drawing is done on a raw RGB bytearray, text comes from
glyph bitmaps baked into font.bin (DejaVu, rendered at build time), and the
portrait comes from portrait.bin (raw RGB, zlib-compressed). No randomness,
no clock: two runs produce byte-identical output.
"""

import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("ENTRY_OUTPUT", "/output/entry.png")

W, H = 1024, 724

# ---------------------------------------------------------------- fonts ----
# font.bin: zlib stream of: magic "NFNT1", u16 nfonts, then per font:
#   u16 namelen, name, u16 size, u16 nglyphs, then per glyph:
#   u32 codepoint, u8 advance, u8 w, u8 h, i8 bearing_x, i8 bearing_y,
#   w*h bytes of grayscale coverage.
class Font:
    def __init__(self, name, size, glyphs):
        self.name = name
        self.size = size
        self.glyphs = glyphs  # cp -> (adv, w, h, bx, by, bytes)

    def width(self, text, tracking=0):
        total = 0
        for ch in text:
            g = self.glyphs.get(ord(ch))
            if g is None:
                continue
            total += g[0] + tracking
        return total - tracking if text else 0


def load_fonts():
    raw = zlib.decompress(open(os.path.join(HERE, "font.bin"), "rb").read())
    assert raw[:5] == b"NFNT1", "bad font.bin"
    pos = 5
    nfonts, = struct.unpack_from(">H", raw, pos)
    pos += 2
    fonts = {}
    for _ in range(nfonts):
        (nl,) = struct.unpack_from(">H", raw, pos)
        pos += 2
        name = raw[pos:pos + nl].decode()
        pos += nl
        (size,) = struct.unpack_from(">H", raw, pos)
        pos += 2
        (ng,) = struct.unpack_from(">H", raw, pos)
        pos += 2
        glyphs = {}
        for _ in range(ng):
            cp, adv, w, h, bx, by = struct.unpack_from(">IBBBbb", raw, pos)
            pos += 9
            px = raw[pos:pos + w * h]
            pos += w * h
            glyphs[cp] = (adv, w, h, bx, by, px)
        fonts[name] = Font(name, size, glyphs)
    return fonts


def load_portrait():
    raw = zlib.decompress(open(os.path.join(HERE, "portrait.bin"), "rb").read())
    w, h = struct.unpack_from(">HH", raw, 0)
    return w, h, raw[4:]


# --------------------------------------------------------------- canvas ----
class Canvas:
    def __init__(self, w, h, color=(255, 255, 255)):
        self.w = w
        self.h = h
        self.px = bytearray(bytes(color) * (w * h))

    def _idx(self, x, y):
        return (y * self.w + x) * 3

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = self._idx(x, y)
            self.px[i:i + 3] = bytes(color)

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = self._idx(x, y)
            return tuple(self.px[i:i + 3])
        return (255, 255, 255)

    def blend(self, x, y, color, alpha):
        """Alpha-blend color onto pixel (alpha 0..255)."""
        if alpha <= 0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        if alpha >= 255:
            self.set(x, y, color)
            return
        i = self._idx(x, y)
        inv = 255 - alpha
        self.px[i] = (self.px[i] * inv + color[0] * alpha) // 255
        self.px[i + 1] = (self.px[i + 1] * inv + color[1] * alpha) // 255
        self.px[i + 2] = (self.px[i + 2] * inv + color[2] * alpha) // 255

    def fill_rect(self, x, y, w, h, color, alpha=255):
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.w, x + w)
        y1 = min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        if alpha >= 255:
            row = bytes(color) * (x1 - x0)
            for yy in range(y0, y1):
                i = self._idx(x0, yy)
                self.px[i:i + len(row)] = row
        else:
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    self.blend(xx, yy, color, alpha)

    def hline(self, x, y, w, color, alpha=255):
        self.fill_rect(x, y, w, 1, color, alpha)

    def vline(self, x, y, h, color, alpha=255):
        self.fill_rect(x, y, 1, h, color, alpha)

    def rect(self, x, y, w, h, color, t=1, alpha=255):
        self.fill_rect(x, y, w, t, color, alpha)
        self.fill_rect(x, y + h - t, w, t, color, alpha)
        self.fill_rect(x, y, t, h, color, alpha)
        self.fill_rect(x + w - t, y, t, h, color, alpha)

    def line(self, x0, y0, x1, y1, color, alpha=255):
        """Bresenham line."""
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.blend(x0, y0, color, alpha)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def poly(self, pts, color, alpha=255):
        """Filled polygon (scanline)."""
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        for y in range(max(0, min(ys)), min(self.h, max(ys) + 1)):
            xs = []
            n = len(pts)
            for i in range(n):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % n]
                if (y0 <= y < y1) or (y1 <= y < y0):
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                xa, xb = int(math.ceil(xs[i])), int(math.floor(xs[i + 1]))
                for x in range(xa, xb + 1):
                    self.blend(x, y, color, alpha)

    def ellipse(self, cx, cy, rx, ry, color, alpha=255, fill=True, t=1):
        if fill:
            for y in range(cy - ry, cy + ry + 1):
                if y < 0 or y >= self.h:
                    continue
                dy = (y - cy) / ry
                dx = int(rx * math.sqrt(max(0.0, 1 - dy * dy)))
                for x in range(cx - dx, cx + dx + 1):
                    self.blend(x, y, color, alpha)
        else:
            steps = max(24, int(2 * math.pi * max(rx, ry)))
            for i in range(steps):
                a = 2 * math.pi * i / steps
                x = int(cx + rx * math.cos(a))
                y = int(cy + ry * math.sin(a))
                for ty in range(t):
                    self.blend(x, y + ty, color, alpha)

    def circle(self, cx, cy, r, color, alpha=255, fill=True, t=1):
        self.ellipse(cx, cy, r, r, color, alpha, fill, t)

    # ------------------------------------------------------------ text ----
    def draw_text(self, font, x, y, text, color, tracking=0, alpha=255):
        """Draw text with baseline at y; returns x after the last glyph."""
        pen = x
        for ch in text:
            g = font.glyphs.get(ord(ch))
            if g is None:
                pen += font.size // 2
                continue
            adv, w, h, bx, by, px = g
            gx = pen + bx
            gy = y + by
            for row in range(h):
                yy = gy + row
                if yy < 0 or yy >= self.h:
                    continue
                base = row * w
                for col in range(w):
                    cov = px[base + col]
                    if cov:
                        self.blend(gx + col, yy, color, cov * alpha // 255)
            pen += adv + tracking
        return pen

    def text_centered(self, font, cx, y, text, color, tracking=0, alpha=255):
        w = font.width(text, tracking)
        return self.draw_text(font, cx - w // 2, y, text, color, tracking, alpha)

    def blit_rgb(self, x, y, w, h, rgb, alpha=255, scale=1):
        """Blit raw RGB bytes (w*h*3) at (x,y); nearest-neighbour scale."""
        for yy in range(h):
            for xx in range(w):
                i = (yy * w + xx) * 3
                c = (rgb[i], rgb[i + 1], rgb[i + 2])
                if scale == 1:
                    self.blend(x + xx, y + yy, c, alpha)
                else:
                    for sy in range(scale):
                        for sx in range(scale):
                            self.blend(x + xx * scale + sx, y + yy * scale + sy, c, alpha)

    def rotated(self, angle_deg):
        """Return a new canvas with this one rotated by angle (nearest)."""
        rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        w, h = self.w, self.h
        nw = int(abs(w * cos_a) + abs(h * sin_a)) + 2
        nh = int(abs(w * sin_a) + abs(h * cos_a)) + 2
        out = Canvas(nw, nh, (255, 255, 255))
        out_alpha = bytearray(nw * nh)
        cx0, cy0 = w / 2, h / 2
        cx1, cy1 = nw / 2, nh / 2
        for yy in range(nh):
            for xx in range(nw):
                dx = xx - cx1
                dy = yy - cy1
                sx = int(cx0 + dx * cos_a + dy * sin_a)
                sy = int(cy0 - dx * sin_a + dy * cos_a)
                if 0 <= sx < w and 0 <= sy < h:
                    i = (sy * w + sx) * 3
                    j = (yy * nw + xx) * 3
                    out.px[j:j + 3] = self.px[i:i + 3]
                    out_alpha[yy * nw + xx] = 255
        out.alpha = out_alpha
        return out

    def blit_canvas(self, src, x, y, alpha=255):
        """Blit another canvas, honouring its .alpha mask if present."""
        mask = getattr(src, "alpha", None)
        for yy in range(src.h):
            for xx in range(src.w):
                if mask is not None and not mask[yy * src.w + xx]:
                    continue
                i = (yy * src.w + xx) * 3
                self.blend(x + xx, y + yy,
                           (src.px[i], src.px[i + 1], src.px[i + 2]), alpha)


# ------------------------------------------------------------ PNG out ------
def write_png(path, canvas):
    w, h = canvas.w, canvas.h
    px = canvas.px
    raw = bytearray()
    prev = bytearray(w * 3)
    for y in range(h):
        row = px[y * w * 3:(y + 1) * w * 3]
        if y == 0:
            raw.append(0)
            raw += row
            prev = bytearray(row)
            continue
        # choose filter 1 (Sub) or 2 (Up) by sum of absolute signed bytes
        sub = bytearray(w * 3)
        up = bytearray(w * 3)
        s_sub = 0
        s_up = 0
        for i in range(w * 3):
            b = row[i]
            left = row[i - 3] if i >= 3 else 0
            d1 = (b - left) & 0xFF
            sub[i] = d1
            s_sub += d1 if d1 < 128 else 256 - d1
            d2 = (b - prev[i]) & 0xFF
            up[i] = d2
            s_up += d2 if d2 < 128 else 256 - d2
        if s_sub <= s_up:
            raw.append(1)
            raw += sub
        else:
            raw.append(2)
            raw += up
        prev = bytearray(row)
    comp = zlib.compress(bytes(raw), 9)

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", comp) + chunk(b"IEND", b""))
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    return len(png)

# ------------------------------------------------------------------ style ----
INK = (26, 32, 44)
NAVY = (31, 58, 95)
NAVY_SOFT = (58, 74, 110)
GOLD = (176, 141, 63)
GOLD_SOFT = (201, 178, 120)
PAGE = (247, 242, 230)
DESK = (52, 55, 62)
SHADOW = (30, 31, 35)
MRZ_BG = (233, 230, 220)
RULE = (150, 140, 110)

FONTS = None
PW, PH, PORTRAIT = None, None, None


def mrz_check(s):
    vals = {}
    for i in range(10):
        vals[str(i)] = i
    for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        vals[c] = 10 + i
    vals["<"] = 0
    w = (7, 3, 1)
    return str(sum(vals[c] * w[i % 3] for i, c in enumerate(s)) % 10)


def mrz_lines():
    line1 = "P<NRDMARISDOTTIR<<ELENA<SOFIA" + "<" * 15
    doc = "NRD482916"
    dob, exp = "910314", "330501"
    pers = "<" * 14
    body = (doc + mrz_check(doc) + "NRD" + dob + mrz_check(dob) + "F"
            + exp + mrz_check(exp) + pers + mrz_check(pers))
    line2 = body + mrz_check(body)
    assert len(line1) == 44 and len(line2) == 44, (len(line1), len(line2))
    return line1, line2


def glyph_canvas(font, ch):
    g = font.glyphs.get(ord(ch))
    if g is None:
        return None, 0
    adv, w, h, bx, by, px = g
    cv = Canvas(max(1, w), max(1, h), (255, 255, 255))
    for row in range(h):
        for col in range(w):
            cov = px[row * w + col]
            if cov:
                cv.blend(col, row, (0, 0, 0), cov)
    return cv, adv


def circular_text(cv, font, cx, cy, r, text, color, start_deg=270,
                  direction=1):
    """Draw text along a circle, glyphs upright relative to the radius."""
    total = font.width(text)
    span = math.degrees(total / r)
    ang = math.radians(start_deg - direction * span / 2)
    pen = 0
    for ch in text:
        g = font.glyphs.get(ord(ch))
        if g is None:
            continue
        adv, w, h, bx, by, px = g
        a = ang + direction * math.radians(math.degrees((pen + adv / 2) / r))
        deg = math.degrees(a) + 90
        gc, _ = glyph_canvas(font, ch)
        if gc is not None:
            rc = gc.rotated(deg)
            px0 = int(cx + r * math.cos(a) - rc.w / 2)
            py0 = int(cy + r * math.sin(a) - rc.h / 2)
            for yy in range(rc.h):
                for xx in range(rc.w):
                    if rc.alpha is not None and not rc.alpha[yy * rc.w + xx]:
                        continue
                    i = (yy * rc.w + xx) * 3
                    dark = 255 - min(rc.px[i], rc.px[i + 1], rc.px[i + 2])
                    if dark:
                        cv.blend(px0 + xx, py0 + yy, color, dark)
        pen += adv


# -------------------------------------------------------------- arms -------
def draw_arms(cv, cx, top):
    """Coat of arms: navy shield, gold border, lighthouse, waves, stars."""
    # shield outline (gold) then inner (navy)
    outer = [(cx - 31, top), (cx + 31, top), (cx + 31, top + 44),
             (cx, top + 68), (cx - 31, top + 44)]
    inner = [(cx - 27, top + 3), (cx + 27, top + 3), (cx + 27, top + 42),
             (cx, top + 63), (cx - 27, top + 42)]
    cv.poly(outer, GOLD)
    cv.poly(inner, NAVY)
    # lighthouse tower (white) with lamp
    cv.poly([(cx - 7, top + 52), (cx + 7, top + 52), (cx + 4, top + 20),
             (cx - 4, top + 20)], (240, 238, 230))
    cv.fill_rect(cx - 6, top + 14, 12, 6, (240, 238, 230))
    cv.fill_rect(cx - 3, top + 10, 6, 4, (255, 214, 120))
    # light rays
    cv.poly([(cx - 3, top + 12), (cx - 26, top + 2), (cx - 26, top + 8)],
            (255, 214, 120), alpha=150)
    cv.poly([(cx + 3, top + 12), (cx + 26, top + 2), (cx + 26, top + 8)],
            (255, 214, 120), alpha=150)
    # waves
    for k, yy in enumerate((top + 54, top + 59, top + 64)):
        prev = None
        for x in range(cx - 24, cx + 25, 3):
            y = yy + int(2 * math.sin((x - cx + k * 7) / 6.0))
            if prev is not None:
                cv.line(prev[0], prev[1], x, y, (150, 190, 215))
            prev = (x, y)
    # stars
    for sx, sy in ((cx - 17, top + 12), (cx + 17, top + 12)):
        cv.poly([(sx, sy - 5), (sx + 2, sy - 2), (sx + 5, sy),
                 (sx + 2, sy + 2), (sx, sy + 5), (sx - 2, sy + 2),
                 (sx - 5, sy), (sx - 2, sy - 2)], GOLD)


# ------------------------------------------------------------- stamps ------
def make_stamp(w, h, ink, title, big, lines, plane=False):
    cv = Canvas(w, h, (255, 255, 255))
    cv.rect(3, 3, w - 6, h - 6, ink, t=2)
    cv.rect(8, 8, w - 16, h - 16, ink, t=1)
    y = 15
    if plane:
        # paper-plane glyph
        cv.poly([(w // 2 - 9, y + 2), (w // 2 + 11, y + 6),
                 (w // 2 - 9, y + 10), (w // 2 - 4, y + 6)], ink)
        y += 13
    if title:
        cv.text_centered(FONTS["sans_bold_11"], w // 2, y + 9, title, ink,
                         tracking=1)
        y += 13
    if big:
        cv.text_centered(FONTS["sans_bold_16"], w // 2, y + 13, big, ink,
                         tracking=2)
        y += 20
    for ln in lines:
        cv.text_centered(FONTS["sans_13"], w // 2, y + 11, ln, ink)
        y += 15
    return cv


def blit_stamp(cv, stamp, x, y, angle, alpha=210):
    rc = stamp.rotated(angle)
    mask = rc.alpha
    for yy in range(rc.h):
        for xx in range(rc.w):
            if mask is not None and not mask[yy * rc.w + xx]:
                continue
            i = (yy * rc.w + xx) * 3
            dark = 255 - min(rc.px[i], rc.px[i + 1], rc.px[i + 2])
            if dark:
                cv.blend(x + xx, y + yy,
                         (rc.px[i], rc.px[i + 1], rc.px[i + 2]),
                         dark * alpha // 255)


def draw_fee_stamp(cv, cx, cy, r):
    ink = (122, 92, 30)
    cv.circle(cx, cy, r, ink, fill=False, t=2)
    cv.circle(cx, cy, r - 5, ink, fill=False, t=1)
    circular_text(cv, FONTS["sans_bold_11"], cx, cy, r - 12,
                  "HAVNVIK HARBOURMASTER", ink, start_deg=270)
    circular_text(cv, FONTS["sans_bold_11"], cx, cy, r - 12,
                  "FEE PAID", ink, start_deg=90, direction=-1)
    cv.text_centered(FONTS["sans_bold_16"], cx, cy - 2, "120 KR", ink)
    cv.text_centered(FONTS["sans_13"], cx, cy + 16, "02 MAY 2023", ink)


def draw_visa_sticker(cv, x, y, w, h, angle):
    st = Canvas(w, h, (255, 255, 255))
    st.fill_rect(0, 0, w, 30, (0, 90, 60))
    st.text_centered(FONTS["sans_bold_13"], w // 2, 21, "VISA", (255, 255, 255),
                     tracking=4)
    st.rect(0, 0, w, h, (0, 90, 60), t=2)
    st.text_centered(FONTS["sans_bold_16"], w // 2, 52, "AUSTERLAND", INK)
    st.text_centered(FONTS["sans_13"], w // 2, 72, "SINGLE ENTRY — 30 DAYS", INK)
    st.text_centered(FONTS["sans_13"], w // 2, 90, "No. AV-2210-884", INK)
    blit_stamp(cv, st, x, y, angle, alpha=255)


# -------------------------------------------------------------- pages ------
def page_frame(cv, x0, y0, w, h):
    cv.fill_rect(x0 + 4, y0 + 4, w, h, SHADOW)
    cv.fill_rect(x0, y0, w, h, PAGE)
    # spine shading
    for i in range(7):
        a = 4 + i * 3
        if x0 == 12:  # left page: shade its right edge
            cv.fill_rect(x0 + w - 8 - i * 2, y0, 2, h, (90, 80, 60), alpha=a)
        else:  # right page: shade its left edge
            cv.fill_rect(x0 + i * 2, y0, 2, h, (90, 80, 60), alpha=a)
    cv.rect(x0 + 11, y0 + 11, w - 22, h - 22, GOLD_SOFT, t=1, alpha=170)
    # faint diagonal guilloche
    for d in range(-h, w, 18):
        cv.line(x0 + d, y0 + h - 12, x0 + d + h, y0 + 12, (120, 130, 150),
                alpha=9)


def field(cv, x, y, label, value, vfont="sans_15", label_w=None):
    cv.draw_text(FONTS["sans_bold_11"], x, y, label, NAVY_SOFT, tracking=1)
    cv.draw_text(FONTS[vfont], x, y + 19, value, INK)


def draw_data_page(cv):
    x0, y0, w, h = 12, 10, 492, 704
    page_frame(cv, x0, y0, w, h)
    cx = x0 + w // 2  # 258
    draw_arms(cv, cx, 28)
    cv.text_centered(FONTS["serif_bold_26"], cx, 140, "REPUBLIC OF NORDHAVN",
                     NAVY)
    cv.text_centered(FONTS["sans_bold_11"], cx, 160, "REPUBLIKKEN NORDHAVN",
                     NAVY_SOFT, tracking=3)
    cv.hline(cx - 150, 172, 300, GOLD, alpha=220)
    cv.hline(cx - 150, 175, 300, GOLD, alpha=220)
    cv.text_centered(FONTS["serif_bold_19"], cx, 200, "PASSPORT", INK,
                     tracking=6)
    cv.text_centered(FONTS["sans_bold_11"], cx, 218,
                     "EX MARE  ·  PERDURAMUS", NAVY_SOFT, tracking=2)

    # type / code / number row
    field(cv, 30, 246, "TYPE / TYPE", "P", vfont="sans_bold_13")
    field(cv, 130, 246, "CODE / KODE", "NRD", vfont="sans_bold_13")
    field(cv, 250, 246, "PASSPORT NO. / PASSNR.", "NRD 482 916",
          vfont="sans_bold_13")

    # portrait
    cv.fill_rect(28, 270, 180, 236, (255, 255, 255))
    cv.blit_rgb(30, 272, PW, PH, PORTRAIT)
    cv.rect(28, 270, 180, 236, NAVY, t=2)
    # gold seal overlapping the photo corner
    sx, sy, sr = 196, 494, 25
    cv.circle(sx, sy, sr, (176, 141, 63), fill=True)
    cv.circle(sx, sy, sr - 3, (201, 178, 120), fill=False, t=1)
    circular_text(cv, FONTS["sans_bold_11"], sx, sy, sr - 8,
                  "NORDHAVN", (90, 66, 20), start_deg=270)
    cv.poly([(sx, sy - 7), (sx + 2, sy - 2), (sx + 7, sy), (sx + 2, sy + 2),
             (sx, sy + 7), (sx - 2, sy + 2), (sx - 7, sy), (sx - 2, sy - 2)],
            (90, 66, 20))

    # personal fields, right of the photo
    fx = 228
    field(cv, fx, 290, "SURNAME / ETTERNAVN", "MARISDOTTIR")
    field(cv, fx, 330, "GIVEN NAMES / FORNAVN", "ELENA SOFIA")
    field(cv, fx, 370, "NATIONALITY / NASJONALITET", "NORDHAVNISK")
    field(cv, fx, 410, "BORN / FØDT", "14 MAR 1991")
    field(cv, 340, 410, "SEX / KJØNN", "F")
    field(cv, fx, 450, "PLACE OF BIRTH / FØDESTED", "HAVNVIK, NORDHAVN")

    # issue / expiry / authority row
    field(cv, 30, 530, "ISSUED / UTSTEDT", "02 MAY 2023")
    field(cv, 180, 530, "EXPIRES / UTLØPER", "01 MAY 2033")
    cv.draw_text(FONTS["sans_bold_11"], 350, 530, "AUTHORITY", NAVY_SOFT,
                 tracking=1)
    cv.draw_text(FONTS["sans_15"], 350, 549, "HAVNVIK", INK)
    cv.draw_text(FONTS["sans_11"], 350, 567, "PASSPORT OFFICE", INK)

    # holder's signature
    cv.draw_text(FONTS["sans_bold_11"], 30, 596,
                 "HOLDER'S SIGNATURE / INNEHAVERENS UNDERSKRIFT", NAVY_SOFT,
                 tracking=1)
    sig = (30, 40, 90)
    prev = None
    for x in range(36, 288, 3):
        y = 614 + int(6 * math.sin(x / 11.0) + 3 * math.sin(x / 4.3))
        if prev is not None:
            cv.line(prev[0], prev[1], x, y, sig)
            cv.line(prev[0], prev[1] + 1, x, y + 1, sig)
        prev = (x, y)
    cv.circle(118, 610, 7, sig, fill=False, t=1)
    cv.line(36, 626, 288, 622, sig, alpha=180)

    # machine-readable zone
    cv.fill_rect(24, 640, 456, 66, MRZ_BG)
    cv.hline(24, 640, 456, RULE)
    l1, l2 = mrz_lines()
    cv.draw_text(FONTS["mono_15"], 30, 664, l1, INK)
    cv.draw_text(FONTS["mono_15"], 30, 688, l2, INK)


def draw_visa_page(cv):
    x0, y0, w, h = 520, 10, 492, 704
    page_frame(cv, x0, y0, w, h)
    cv.draw_text(FONTS["serif_bold_19"], 538, 48, "VISAS", INK, tracking=4)
    cv.draw_text(FONTS["sans_bold_11"], 940, 48, "VISUM", NAVY_SOFT,
                 tracking=2)
    cv.hline(538, 58, 456, GOLD, alpha=220)
    cv.draw_text(FONTS["sans_11"], 538, 700, "PAGE 12  ·  SIDE 12",
                 NAVY_SOFT, tracking=1)

    # observations box
    cv.rect(538, 72, 456, 98, NAVY, t=1)
    cv.fill_rect(538, 72, 456, 20, NAVY)
    cv.draw_text(FONTS["sans_bold_11"], 546, 86, "OBSERVATIONS / MERKNADER",
                 (255, 255, 255), tracking=1)
    cv.draw_text(FONTS["sans_13"], 546, 114,
                 "Holder is a registered crew member of FV SKARVEN.",
                 INK)
    cv.draw_text(FONTS["sans_13"], 546, 132,
                 "Innehaveren er registrert mannskap på FV SKARVEN.",
                 INK)
    cv.draw_text(FONTS["sans_11"], 546, 154,
                 "This passport remains the property of the Republic of Nordhavn.",
                 NAVY_SOFT)

    # stamps, two columns
    iceland = make_stamp(218, 108, (31, 78, 156), "ICELAND — KEFLAVIK",
                         "ENTRY", ["12 JUN 2024", "No. 4417"], plane=True)
    blit_stamp(cv, iceland, 538, 190, -7)
    draw_fee_stamp(cv, 875, 244, 56)

    norway = make_stamp(218, 108, (36, 52, 77), "NORWAY — BERGEN",
                        "DEPARTURE", ["28 JUN 2024", "No. 0912"], plane=True)
    blit_stamp(cv, norway, 538, 320, 6)
    uk = make_stamp(218, 108, (126, 42, 78), "UNITED KINGDOM",
                    "ENTRY", ["LONDON HEATHROW", "03 SEP 2024"], plane=True)
    blit_stamp(cv, uk, 766, 320, -4)

    faroe = make_stamp(218, 108, (46, 107, 52), "FAROE ISLANDS",
                       "DEPARTURE", ["TORSHAVN", "17 MAR 2025"], plane=True)
    blit_stamp(cv, faroe, 538, 450, 8)
    draw_visa_sticker(cv, 766, 450, 218, 108, -3)

    end = make_stamp(446, 92, (165, 42, 42), None, None, [], plane=False)
    end.text_centered(FONTS["sans_bold_13"], 223, 30,
                      "SEAFARER'S ENDORSEMENT", (165, 42, 42), tracking=1)
    end.text_centered(FONTS["sans_13"], 223, 52, "SJØMANNS PÅTEGNING",
                      (165, 42, 42))
    end.text_centered(FONTS["sans_13"], 223, 72,
                      "FV SKARVEN — CREW  ·  HAVNVIK 02 MAY 2023",
                      (165, 42, 42))
    blit_stamp(cv, end, 538, 584, -2)


# ------------------------------------------------------------------ main ----
def main():
    global FONTS, PW, PH, PORTRAIT
    FONTS = load_fonts()
    PW, PH, PORTRAIT = load_portrait()
    cv = Canvas(W, H, DESK)
    draw_data_page(cv)
    draw_visa_page(cv)
    n = write_png(OUT, cv)
    print(f"wrote {OUT}: {W}x{H}, {n} bytes")
    assert W <= 1024 and H <= 1024, "over pixel cap"
    assert n <= 1048576, "over 1 MiB cap"


if __name__ == "__main__":
    main()
