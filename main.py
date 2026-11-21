#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Passport of the Republic of Nordhavn -- a country that isn't there.

Generates one open passport spread (data page + visa page) and writes it to
/output/entry.png (PNG, 1024x724, well under 1 MiB).

Runs on stdlib only: the grading sandbox is python:3.13-slim with no network
and no PIL, so all drawing is done on a raw RGB bytearray, text comes from
glyph bitmaps baked into font.bin (DejaVu, rendered at build time), and the
portrait comes from portrait.bin (raw RGB, zlib-compressed). No randomness,
no clock: two runs produce byte-identical output.

Layout in one sentence: a 1024x724 desk-coloured spread holds two facing
492x704 pages -- the data page on the left (arms, holder fields, portrait,
signature, machine-readable zone) and the visa page on the right (stamps,
a fee seal, a visa sticker, a seafarer's endorsement).
"""

import math
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("ENTRY_OUTPUT", "/output/entry.png")

# Spread dimensions (pixels) and the per-page frame they contain.
W, H = 1024, 724
PAGE_WIDTH, PAGE_HEIGHT = 492, 704
PAGE_TOP = 10
LEFT_PAGE_X = 12
RIGHT_PAGE_X = 520

# ------------------------------------------------------------- fonts ------
# font.bin: zlib stream of: magic "NFNT1", u16 nfonts, then per font:
#   u16 namelen, name, u16 size, u16 nglyphs, then per glyph:
#   u32 codepoint, u8 advance, u8 w, u8 h, i8 bearing_x, i8 bearing_y,
#   w*h bytes of grayscale coverage.
class Font:
    """One bitmap font: glyphs keyed by codepoint.

    A glyph tuple is (advance, width, height, bearing_x, bearing_y,
    coverage_bytes), where coverage is a row-major w*h grayscale map.
    """

    def __init__(self, name, size, glyphs):
        self.name = name
        self.size = size
        self.glyphs = glyphs  # cp -> (adv, w, h, bx, by, bytes)

    def width(self, text, tracking=0):
        """Advance width of text in pixels, including inter-glyph tracking."""
        running_width = 0
        for ch in text:
            glyph = self.glyphs.get(ord(ch))
            if glyph is None:
                continue
            running_width += glyph[0] + tracking
        return running_width - tracking if text else 0


def load_fonts():
    """Parse font.bin into a dict of Font objects keyed by font name."""
    raw = zlib.decompress(open(os.path.join(HERE, "font.bin"), "rb").read())
    assert raw[:5] == b"NFNT1", "bad font.bin"
    offset = 5
    nfonts, = struct.unpack_from(">H", raw, offset)
    offset += 2
    fonts = {}
    for _ in range(nfonts):
        (name_len,) = struct.unpack_from(">H", raw, offset)
        offset += 2
        name = raw[offset:offset + name_len].decode()
        offset += name_len
        (size,) = struct.unpack_from(">H", raw, offset)
        offset += 2
        (glyph_count,) = struct.unpack_from(">H", raw, offset)
        offset += 2
        glyphs = {}
        for _ in range(glyph_count):
            cp, adv, w, h, bx, by = struct.unpack_from(">IBBBbb", raw, offset)
            offset += 9
            pixels = raw[offset:offset + w * h]
            offset += w * h
            glyphs[cp] = (adv, w, h, bx, by, pixels)
        fonts[name] = Font(name, size, glyphs)
    return fonts


def load_portrait():
    """Parse portrait.bin: a u16 width/height header plus raw RGB bytes."""
    data = zlib.decompress(open(os.path.join(HERE, "portrait.bin"), "rb").read())
    width, height = struct.unpack_from(">HH", data, 0)
    return width, height, data[4:]


# ------------------------------------------------------------ canvas ------
class Canvas:
    """A raw RGB pixel buffer with primitive drawing operations.

    Pixels live in a flat bytearray (row-major, 3 bytes per pixel). All
    drawing goes through blend(), which clips to the canvas bounds, so
    callers never need their own bounds checks. A canvas may carry an
    optional .alpha bytearray mask (set by rotated()); blit_canvas()
    honours it when pasting one canvas onto another.
    """

    def __init__(self, w, h, color=(255, 255, 255)):
        self.w = w
        self.h = h
        self.px = bytearray(bytes(color) * (w * h))

    def _idx(self, x, y):
        return (y * self.w + x) * 3

    def set(self, x, y, color):
        """Set one pixel, silently ignoring out-of-bounds coordinates."""
        if 0 <= x < self.w and 0 <= y < self.h:
            index = self._idx(x, y)
            self.px[index:index + 3] = bytes(color)

    def get(self, x, y):
        """Read one pixel; white outside the canvas."""
        if 0 <= x < self.w and 0 <= y < self.h:
            index = self._idx(x, y)
            return tuple(self.px[index:index + 3])
        return (255, 255, 255)

    def blend(self, x, y, color, alpha):
        """Alpha-blend color onto pixel (alpha 0..255)."""
        if alpha <= 0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        if alpha >= 255:
            self.set(x, y, color)
            return
        index = self._idx(x, y)
        remaining = 255 - alpha
        self.px[index] = (self.px[index] * remaining + color[0] * alpha) // 255
        self.px[index + 1] = (self.px[index + 1] * remaining + color[1] * alpha) // 255
        self.px[index + 2] = (self.px[index + 2] * remaining + color[2] * alpha) // 255

    def fill_rect(self, x, y, w, h, color, alpha=255):
        """Fill a rectangle, clipped to the canvas."""
        clip_left = max(0, x)
        clip_top = max(0, y)
        clip_right = min(self.w, x + w)
        clip_bottom = min(self.h, y + h)
        if clip_right <= clip_left or clip_bottom <= clip_top:
            return
        if alpha >= 255:
            row_bytes = bytes(color) * (clip_right - clip_left)
            for row_y in range(clip_top, clip_bottom):
                index = self._idx(clip_left, row_y)
                self.px[index:index + len(row_bytes)] = row_bytes
        else:
            for row_y in range(clip_top, clip_bottom):
                for col_x in range(clip_left, clip_right):
                    self.blend(col_x, row_y, color, alpha)

    def hline(self, x, y, w, color, alpha=255):
        """One-pixel-high horizontal line."""
        self.fill_rect(x, y, w, 1, color, alpha)

    def vline(self, x, y, h, color, alpha=255):
        """One-pixel-wide vertical line."""
        self.fill_rect(x, y, 1, h, color, alpha)

    def rect(self, x, y, w, h, color, t=1, alpha=255):
        """Hollow rectangle outline of thickness t."""
        self.fill_rect(x, y, w, t, color, alpha)
        self.fill_rect(x, y + h - t, w, t, color, alpha)
        self.fill_rect(x, y, t, h, color, alpha)
        self.fill_rect(x + w - t, y, t, h, color, alpha)

    def line(self, x0, y0, x1, y1, color, alpha=255):
        """Bresenham line between two points."""
        delta_x = abs(x1 - x0)
        delta_y = -abs(y1 - y0)
        step_x = 1 if x0 < x1 else -1
        step_y = 1 if y0 < y1 else -1
        error = delta_x + delta_y
        while True:
            self.blend(x0, y0, color, alpha)
            if x0 == x1 and y0 == y1:
                break
            error2 = 2 * error
            if error2 >= delta_y:
                error += delta_y
                x0 += step_x
            if error2 <= delta_x:
                error += delta_x
                y0 += step_y

    def poly(self, pts, color, alpha=255):
        """Filled polygon (scanline fill)."""
        if len(pts) < 3:
            return
        y_values = [p[1] for p in pts]
        for y in range(max(0, min(y_values)), min(self.h, max(y_values) + 1)):
            crossings = []
            point_count = len(pts)
            for edge_index in range(point_count):
                ax, ay = pts[edge_index]
                bx, by = pts[(edge_index + 1) % point_count]
                if (ay <= y < by) or (by <= y < ay):
                    crossings.append(ax + (y - ay) * (bx - ax) / (by - ay))
            crossings.sort()
            for pair_index in range(0, len(crossings) - 1, 2):
                x_start, x_end = int(math.ceil(crossings[pair_index])), int(math.floor(crossings[pair_index + 1]))
                for fill_x in range(x_start, x_end + 1):
                    self.blend(fill_x, y, color, alpha)

    def ellipse(self, cx, cy, rx, ry, color, alpha=255, fill=True, t=1):
        """Filled ellipse, or a thin outline when fill is False."""
        if fill:
            for y in range(cy - ry, cy + ry + 1):
                if y < 0 or y >= self.h:
                    continue
                normalized_y = (y - cy) / ry
                half_width = int(rx * math.sqrt(max(0.0, 1 - normalized_y * normalized_y)))
                for x in range(cx - half_width, cx + half_width + 1):
                    self.blend(x, y, color, alpha)
        else:
            steps = max(24, int(2 * math.pi * max(rx, ry)))
            for step_index in range(steps):
                angle = 2 * math.pi * step_index / steps
                point_x = int(cx + rx * math.cos(angle))
                point_y = int(cy + ry * math.sin(angle))
                for thickness_row in range(t):
                    self.blend(point_x, point_y + thickness_row, color, alpha)

    def circle(self, cx, cy, r, color, alpha=255, fill=True, t=1):
        """Circle: an ellipse with equal radii."""
        self.ellipse(cx, cy, r, r, color, alpha, fill, t)

    # ------------------------------------------------------------ text ----
    def draw_text(self, font, x, y, text, color, tracking=0, alpha=255):
        """Draw text with baseline at y; returns x after the last glyph."""
        pen_x = x
        for ch in text:
            glyph = font.glyphs.get(ord(ch))
            if glyph is None:
                pen_x += font.size // 2
                continue
            advance, glyph_w, glyph_h, bearing_x, bearing_y, pixels = glyph
            glyph_x = pen_x + bearing_x
            glyph_y = y + bearing_y
            for row_index in range(glyph_h):
                pixel_y = glyph_y + row_index
                if pixel_y < 0 or pixel_y >= self.h:
                    continue
                row_offset = row_index * glyph_w
                for col_index in range(glyph_w):
                    coverage = pixels[row_offset + col_index]
                    if coverage:
                        self.blend(glyph_x + col_index, pixel_y, color, coverage * alpha // 255)
            pen_x += advance + tracking
        return pen_x

    def text_centered(self, font, cx, y, text, color, tracking=0, alpha=255):
        """Draw text centred on cx with baseline at y."""
        text_width = font.width(text, tracking)
        return self.draw_text(font, cx - text_width // 2, y, text, color, tracking, alpha)

    def blit_rgb(self, x, y, w, h, rgb, alpha=255, scale=1):
        """Blit raw RGB bytes (w*h*3) at (x,y); nearest-neighbour scale."""
        for row_y in range(h):
            for col_x in range(w):
                source_index = (row_y * w + col_x) * 3
                pixel = (rgb[source_index], rgb[source_index + 1], rgb[source_index + 2])
                if scale == 1:
                    self.blend(x + col_x, y + row_y, pixel, alpha)
                else:
                    for sub_y in range(scale):
                        for sub_x in range(scale):
                            self.blend(x + col_x * scale + sub_x, y + row_y * scale + sub_y, pixel, alpha)

    def rotated(self, angle_deg):
        """Return a new canvas with this one rotated by angle (nearest)."""
        rad = math.radians(angle_deg)
        cosine, sine = math.cos(rad), math.sin(rad)
        w, h = self.w, self.h
        new_width = int(abs(w * cosine) + abs(h * sine)) + 2
        new_height = int(abs(w * sine) + abs(h * cosine)) + 2
        out = Canvas(new_width, new_height, (255, 255, 255))
        alpha_mask = bytearray(new_width * new_height)
        source_cx, source_cy = w / 2, h / 2
        dest_cx, dest_cy = new_width / 2, new_height / 2
        for yy in range(new_height):
            for xx in range(new_width):
                delta_x = xx - dest_cx
                delta_y = yy - dest_cy
                source_x = int(source_cx + delta_x * cosine + delta_y * sine)
                source_y = int(source_cy - delta_x * sine + delta_y * cosine)
                if 0 <= source_x < w and 0 <= source_y < h:
                    source_index = (source_y * w + source_x) * 3
                    dest_index = (yy * new_width + xx) * 3
                    out.px[dest_index:dest_index + 3] = self.px[source_index:source_index + 3]
                    alpha_mask[yy * new_width + xx] = 255
        out.alpha = alpha_mask
        return out

    def blit_canvas(self, src, x, y, alpha=255):
        """Blit another canvas, honouring its .alpha mask if present."""
        alpha_mask = getattr(src, "alpha", None)
        for row_y in range(src.h):
            for col_x in range(src.w):
                if alpha_mask is not None and not alpha_mask[row_y * src.w + col_x]:
                    continue
                source_index = (row_y * src.w + col_x) * 3
                self.blend(x + col_x, y + row_y,
                           (src.px[source_index], src.px[source_index + 1], src.px[source_index + 2]), alpha)


# ------------------------------------------------------------ PNG out ------
def write_png(path, canvas):
    """Encode a canvas as a PNG file; returns the file size in bytes.

    Each scanline is written with filter 1 (Sub) or filter 2 (Up),
    whichever yields the smaller sum of absolute signed bytes; the
    filtered stream is compressed with zlib level 9.
    """
    width, height = canvas.w, canvas.h
    pixels = canvas.px
    raw = bytearray()
    previous_row = bytearray(width * 3)
    for row_y in range(height):
        scanline = pixels[row_y * width * 3:(row_y + 1) * width * 3]
        if row_y == 0:
            raw.append(0)
            raw += scanline
            previous_row = bytearray(scanline)
            continue
        # choose filter 1 (Sub) or 2 (Up) by sum of absolute signed bytes
        sub_row = bytearray(width * 3)
        up_row = bytearray(width * 3)
        sub_cost = 0
        up_cost = 0
        for byte_index in range(width * 3):
            byte_value = scanline[byte_index]
            left_byte = scanline[byte_index - 3] if byte_index >= 3 else 0
            sub_diff = (byte_value - left_byte) & 0xFF
            sub_row[byte_index] = sub_diff
            sub_cost += sub_diff if sub_diff < 128 else 256 - sub_diff
            up_diff = (byte_value - previous_row[byte_index]) & 0xFF
            up_row[byte_index] = up_diff
            up_cost += up_diff if up_diff < 128 else 256 - up_diff
        if sub_cost <= up_cost:
            raw.append(1)
            raw += sub_row
        else:
            raw.append(2)
            raw += up_row
        previous_row = bytearray(scanline)
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(typ, data):
        chunk_bytes = struct.pack(">I", len(data)) + typ + data
        return chunk_bytes + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr_data)
                 + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(png_bytes)
    return len(png_bytes)

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
# Extra named colours used by the coat of arms, stamps and page furniture.
TOWER_WHITE = (240, 238, 230)
LAMP_GOLD = (255, 214, 120)
WAVE_BLUE = (150, 190, 215)
SEAL_BROWN = (90, 66, 20)
FEE_INK = (122, 92, 30)
VISA_GREEN = (0, 90, 60)
SPINE_SHADE = (90, 80, 60)
GUILLOCHE = (120, 130, 150)
SIGNATURE_INK = (30, 40, 90)

FONTS = None
PW, PH, PORTRAIT = None, None, None

MRZ_LINE_LENGTH = 44


def mrz_lines():
    """Build the two 44-character machine-readable zone lines.

    Line 1 holds the holder's name; line 2 packs document number, birth
    date, sex, expiry date and personal number, each followed by its
    ICAO check digit, plus one final check digit over the whole line.
    """
    first_line = "P<NRDMARISDOTTIR<<ELENA<SOFIA" + "<" * 15
    document_number = "NRD482916"
    birth_date, expiry_date = "910314", "330501"
    personal_number = "<" * 14
    line_body = (document_number + mrz_check(document_number) + "NRD"
                 + birth_date + mrz_check(birth_date) + "F"
                 + expiry_date + mrz_check(expiry_date) + personal_number
                 + mrz_check(personal_number))
    second_line = line_body + mrz_check(line_body)
    assert len(first_line) == MRZ_LINE_LENGTH and len(second_line) == MRZ_LINE_LENGTH, (len(first_line), len(second_line))
    return first_line, second_line


def mrz_check(s):
    """ICAO MRZ check digit: weighted sum mod 10 with weights 7, 3, 1."""
    char_values = {}
    for i in range(10):
        char_values[str(i)] = i
    for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        char_values[c] = 10 + i
    char_values["<"] = 0
    weights = (7, 3, 1)
    return str(sum(char_values[c] * weights[i % 3] for i, c in enumerate(s)) % 10)


def circular_text(canvas, font, cx, cy, r, text, color, start_deg=270,
                  direction=1):
    """Draw text along a circle, glyphs upright relative to the radius."""
    text_width = font.width(text)
    span_degrees = math.degrees(text_width / r)
    start_angle = math.radians(start_deg - direction * span_degrees / 2)
    advance_so_far = 0
    for ch in text:
        glyph = font.glyphs.get(ord(ch))
        if glyph is None:
            continue
        advance, glyph_w, glyph_h, bearing_x, bearing_y, pixels = glyph
        angle = start_angle + direction * math.radians(math.degrees((advance_so_far + advance / 2) / r))
        rotation_degrees = math.degrees(angle) + 90
        glyph_cv, _ = glyph_canvas(font, ch)
        if glyph_cv is not None:
            rotated_glyph = glyph_cv.rotated(rotation_degrees)
            paste_x = int(cx + r * math.cos(angle) - rotated_glyph.w / 2)
            paste_y = int(cy + r * math.sin(angle) - rotated_glyph.h / 2)
            for row_y in range(rotated_glyph.h):
                for col_x in range(rotated_glyph.w):
                    if rotated_glyph.alpha is not None and not rotated_glyph.alpha[row_y * rotated_glyph.w + col_x]:
                        continue
                    source_index = (row_y * rotated_glyph.w + col_x) * 3
                    darkness = 255 - min(rotated_glyph.px[source_index], rotated_glyph.px[source_index + 1], rotated_glyph.px[source_index + 2])
                    if darkness:
                        canvas.blend(paste_x + col_x, paste_y + row_y, color, darkness)
        advance_so_far += advance


def glyph_canvas(font, ch):
    """Render one glyph onto its own small canvas (black on white)."""
    glyph = font.glyphs.get(ord(ch))
    if glyph is None:
        return None, 0
    advance, glyph_w, glyph_h, bearing_x, bearing_y, pixels = glyph
    glyph_cv = Canvas(max(1, glyph_w), max(1, glyph_h), (255, 255, 255))
    for row_index in range(glyph_h):
        for col_index in range(glyph_w):
            coverage = pixels[row_index * glyph_w + col_index]
            if coverage:
                glyph_cv.blend(col_index, row_index, (0, 0, 0), coverage)
    return glyph_cv, advance


# -------------------------------------------------------------- arms -------
def draw_arms(canvas, cx, top):
    """Coat of arms: navy shield, gold border, lighthouse, waves, stars."""
    # shield outline (gold) then inner (navy)
    shield_outer = [(cx - 31, top), (cx + 31, top), (cx + 31, top + 44),
                    (cx, top + 68), (cx - 31, top + 44)]
    shield_inner = [(cx - 27, top + 3), (cx + 27, top + 3), (cx + 27, top + 42),
                    (cx, top + 63), (cx - 27, top + 42)]
    canvas.poly(shield_outer, GOLD)
    canvas.poly(shield_inner, NAVY)
    # lighthouse tower (white) with lamp
    canvas.poly([(cx - 7, top + 52), (cx + 7, top + 52), (cx + 4, top + 20),
                 (cx - 4, top + 20)], TOWER_WHITE)
    canvas.fill_rect(cx - 6, top + 14, 12, 6, TOWER_WHITE)
    canvas.fill_rect(cx - 3, top + 10, 6, 4, LAMP_GOLD)
    # light rays
    canvas.poly([(cx - 3, top + 12), (cx - 26, top + 2), (cx - 26, top + 8)],
                LAMP_GOLD, alpha=150)
    canvas.poly([(cx + 3, top + 12), (cx + 26, top + 2), (cx + 26, top + 8)],
                LAMP_GOLD, alpha=150)
    # waves
    for wave_index, wave_y in enumerate((top + 54, top + 59, top + 64)):
        previous_point = None
        for sample_x in range(cx - 24, cx + 25, 3):
            sample_y = wave_y + int(2 * math.sin((sample_x - cx + wave_index * 7) / 6.0))
            if previous_point is not None:
                canvas.line(previous_point[0], previous_point[1], sample_x, sample_y, WAVE_BLUE)
            previous_point = (sample_x, sample_y)
    # stars
    for star_x, star_y in ((cx - 17, top + 12), (cx + 17, top + 12)):
        canvas.poly([(star_x, star_y - 5), (star_x + 2, star_y - 2), (star_x + 5, star_y),
                     (star_x + 2, star_y + 2), (star_x, star_y + 5), (star_x - 2, star_y + 2),
                     (star_x - 5, star_y), (star_x - 2, star_y - 2)], GOLD)


# ------------------------------------------------------------- stamps ------
def make_stamp(w, h, ink, title, big, lines, plane=False):
    """Build a rectangular entry/departure stamp on its own canvas.

    Double border, optional paper-plane glyph, an optional title and big
    word, then one centred line per entry in lines.
    """
    stamp_w, stamp_h = w, h
    stamp_ink = ink
    stamp = Canvas(stamp_w, stamp_h, (255, 255, 255))
    stamp.rect(3, 3, stamp_w - 6, stamp_h - 6, stamp_ink, t=2)
    stamp.rect(8, 8, stamp_w - 16, stamp_h - 16, stamp_ink, t=1)
    cursor_y = 15
    if plane:
        # paper-plane glyph
        stamp.poly([(stamp_w // 2 - 9, cursor_y + 2), (stamp_w // 2 + 11, cursor_y + 6),
                    (stamp_w // 2 - 9, cursor_y + 10), (stamp_w // 2 - 4, cursor_y + 6)], stamp_ink)
        cursor_y += 13
    if title:
        stamp.text_centered(FONTS["sans_bold_11"], stamp_w // 2, cursor_y + 9, title, stamp_ink,
                            tracking=1)
        cursor_y += 13
    if big:
        stamp.text_centered(FONTS["sans_bold_16"], stamp_w // 2, cursor_y + 13, big, stamp_ink,
                            tracking=2)
        cursor_y += 20
    for line_text in lines:
        stamp.text_centered(FONTS["sans_13"], stamp_w // 2, cursor_y + 11, line_text, stamp_ink)
        cursor_y += 15
    return stamp


def blit_stamp(canvas, stamp, x, y, angle, alpha=210):
    """Paste a stamp rotated by angle, transferring only its dark ink."""
    rotated_stamp = stamp.rotated(angle)
    alpha_mask = rotated_stamp.alpha
    for row_y in range(rotated_stamp.h):
        for col_x in range(rotated_stamp.w):
            if alpha_mask is not None and not alpha_mask[row_y * rotated_stamp.w + col_x]:
                continue
            source_index = (row_y * rotated_stamp.w + col_x) * 3
            darkness = 255 - min(rotated_stamp.px[source_index], rotated_stamp.px[source_index + 1], rotated_stamp.px[source_index + 2])
            if darkness:
                canvas.blend(x + col_x, y + row_y,
                             (rotated_stamp.px[source_index], rotated_stamp.px[source_index + 1], rotated_stamp.px[source_index + 2]),
                             darkness * alpha // 255)


def draw_fee_stamp(canvas, cx, cy, r):
    """Circular 'fee paid' harbourmaster seal with text on two arcs."""
    stamp_ink = FEE_INK
    canvas.circle(cx, cy, r, stamp_ink, fill=False, t=2)
    canvas.circle(cx, cy, r - 5, stamp_ink, fill=False, t=1)
    # NOTE: the top arc must stay short. "HAVNVIK HARBOURMASTER" is 194 px
    # wide, a 252-degree span at this radius: it wrapped around the sides
    # and struck through "02 MAY 2023". "HAVNVIK" spans ~83 degrees.
    circular_text(canvas, FONTS["sans_bold_11"], cx, cy, r - 12,
                  "HAVNVIK", stamp_ink, start_deg=270)
    circular_text(canvas, FONTS["sans_bold_11"], cx, cy, r - 12,
                  "FEE PAID", stamp_ink, start_deg=90, direction=-1)
    canvas.text_centered(FONTS["sans_bold_16"], cx, cy - 2, "120 KR", stamp_ink)
    canvas.text_centered(FONTS["sans_13"], cx, cy + 16, "02 MAY 2023", stamp_ink)


def draw_visa_sticker(canvas, x, y, w, h, angle):
    """Green-header visa sticker, pasted opaquely after rotation."""
    pos_x, pos_y = x, y
    sticker_w, sticker_h = w, h
    sticker = Canvas(sticker_w, sticker_h, (255, 255, 255))
    sticker.fill_rect(0, 0, sticker_w, 30, VISA_GREEN)
    sticker.text_centered(FONTS["sans_bold_13"], sticker_w // 2, 21, "VISA", (255, 255, 255),
                          tracking=4)
    sticker.rect(0, 0, sticker_w, sticker_h, VISA_GREEN, t=2)
    sticker.text_centered(FONTS["sans_bold_16"], sticker_w // 2, 52, "AUSTERLAND", INK)
    sticker.text_centered(FONTS["sans_13"], sticker_w // 2, 72, "SINGLE ENTRY — 30 DAYS", INK)
    sticker.text_centered(FONTS["sans_13"], sticker_w // 2, 90, "No. AV-2210-884", INK)
    # NOTE: do NOT use blit_stamp here. It transfers only dark pixels, so
    # the white VISA text on the green header came out as a faint ghost
    # outline (only anti-aliased edges survived). A visa sticker is a solid
    # printed label, so paste the whole rotated sticker opaquely.
    rotated_sticker = sticker.rotated(angle)
    canvas.blit_canvas(rotated_sticker, pos_x, pos_y)


# -------------------------------------------------------------- pages ------
def page_frame(canvas, x0, y0, w, h):
    """Page furniture: shadow, paper fill, spine shading, gold rule,
    faint diagonal guilloche."""
    page_x, page_y, page_w, page_h = x0, y0, w, h
    canvas.fill_rect(page_x + 4, page_y + 4, page_w, page_h, SHADOW)
    canvas.fill_rect(page_x, page_y, page_w, page_h, PAGE)
    # spine shading
    for band_index in range(7):
        shade_alpha = 4 + band_index * 3
        if page_x == LEFT_PAGE_X:  # left page: shade its right edge
            canvas.fill_rect(page_x + page_w - 8 - band_index * 2, page_y, 2, page_h, SPINE_SHADE, alpha=shade_alpha)
        else:  # right page: shade its left edge
            canvas.fill_rect(page_x + band_index * 2, page_y, 2, page_h, SPINE_SHADE, alpha=shade_alpha)
    canvas.rect(page_x + 11, page_y + 11, page_w - 22, page_h - 22, GOLD_SOFT, t=1, alpha=170)
    # faint diagonal guilloche
    for diagonal_offset in range(-page_h, page_w, 18):
        canvas.line(page_x + diagonal_offset, page_y + page_h - 12, page_x + diagonal_offset + page_h, page_y + 12, GUILLOCHE,
                    alpha=9)


def field(canvas, x, y, label, value, value_font="sans_15"):
    """One labelled data field: small navy label, value 19 px below."""
    pos_x, pos_y = x, y
    canvas.draw_text(FONTS["sans_bold_11"], pos_x, pos_y, label, NAVY_SOFT, tracking=1)
    canvas.draw_text(FONTS[value_font], pos_x, pos_y + 19, value, INK)


def draw_data_page(canvas):
    """Left page: arms, title, holder fields, portrait, seal, signature,
    machine-readable zone."""
    page_x, page_y, page_w, page_h = LEFT_PAGE_X, PAGE_TOP, PAGE_WIDTH, PAGE_HEIGHT
    page_frame(canvas, page_x, page_y, page_w, page_h)
    center_x = page_x + page_w // 2  # 258
    draw_arms(canvas, center_x, 28)
    canvas.text_centered(FONTS["serif_bold_26"], center_x, 140, "REPUBLIC OF NORDHAVN",
                         NAVY)
    canvas.text_centered(FONTS["sans_bold_11"], center_x, 160, "REPUBLIKKEN NORDHAVN",
                         NAVY_SOFT, tracking=3)
    canvas.hline(center_x - 150, 172, 300, GOLD, alpha=220)
    canvas.hline(center_x - 150, 175, 300, GOLD, alpha=220)
    canvas.text_centered(FONTS["serif_bold_19"], center_x, 200, "PASSPORT", INK,
                         tracking=6)
    canvas.text_centered(FONTS["sans_bold_11"], center_x, 218,
                         "EX MARE  ·  PERDURAMUS", NAVY_SOFT, tracking=2)

    # type / code / number row
    field(canvas, 30, 246, "TYPE / TYPE", "P", value_font="sans_bold_13")
    field(canvas, 130, 246, "CODE / KODE", "NRD", value_font="sans_bold_13")
    field(canvas, 250, 246, "PASSPORT NO. / PASSNR.", "NRD 482 916",
          value_font="sans_bold_13")

    # portrait
    canvas.fill_rect(28, 270, 180, 236, (255, 255, 255))
    canvas.blit_rgb(30, 272, PW, PH, PORTRAIT)
    canvas.rect(28, 270, 180, 236, NAVY, t=2)
    # gold seal overlapping the photo corner
    seal_x, seal_y, seal_radius = 196, 494, 25
    canvas.circle(seal_x, seal_y, seal_radius, GOLD, fill=True)
    canvas.circle(seal_x, seal_y, seal_radius - 3, GOLD_SOFT, fill=False, t=1)
    circular_text(canvas, FONTS["sans_bold_11"], seal_x, seal_y, seal_radius - 8,
                  "NORDHAVN", SEAL_BROWN, start_deg=270)
    canvas.poly([(seal_x, seal_y - 7), (seal_x + 2, seal_y - 2), (seal_x + 7, seal_y), (seal_x + 2, seal_y + 2),
                 (seal_x, seal_y + 7), (seal_x - 2, seal_y + 2), (seal_x - 7, seal_y), (seal_x - 2, seal_y - 2)],
                SEAL_BROWN)

    # personal fields, right of the photo
    field_x = 228
    field(canvas, field_x, 290, "SURNAME / ETTERNAVN", "MARISDOTTIR")
    field(canvas, field_x, 330, "GIVEN NAMES / FORNAVN", "ELENA SOFIA")
    field(canvas, field_x, 370, "NATIONALITY / NASJONALITET", "NORDHAVNISK")
    field(canvas, field_x, 410, "BORN / FØDT", "14 MAR 1991")
    field(canvas, 340, 410, "SEX / KJØNN", "F")
    field(canvas, field_x, 450, "PLACE OF BIRTH / FØDESTED", "HAVNVIK, NORDHAVN")

    # issue / expiry / authority row
    field(canvas, 30, 530, "ISSUED / UTSTEDT", "02 MAY 2023")
    field(canvas, 180, 530, "EXPIRES / UTLØPER", "01 MAY 2033")
    canvas.draw_text(FONTS["sans_bold_11"], 350, 530, "AUTHORITY", NAVY_SOFT,
                     tracking=1)
    canvas.draw_text(FONTS["sans_15"], 350, 549, "HAVNVIK", INK)
    canvas.draw_text(FONTS["sans_11"], 350, 567, "PASSPORT OFFICE", INK)

    # holder's signature
    canvas.draw_text(FONTS["sans_bold_11"], 30, 596,
                     "HOLDER'S SIGNATURE / INNEHAVERENS UNDERSKRIFT", NAVY_SOFT,
                     tracking=1)
    previous_point = None
    for sample_x in range(36, 288, 3):
        sample_y = 614 + int(6 * math.sin(sample_x / 11.0) + 3 * math.sin(sample_x / 4.3))
        if previous_point is not None:
            canvas.line(previous_point[0], previous_point[1], sample_x, sample_y, SIGNATURE_INK)
            canvas.line(previous_point[0], previous_point[1] + 1, sample_x, sample_y + 1, SIGNATURE_INK)
        previous_point = (sample_x, sample_y)
    canvas.circle(118, 610, 7, SIGNATURE_INK, fill=False, t=1)
    canvas.line(36, 626, 288, 622, SIGNATURE_INK, alpha=180)

    # machine-readable zone
    canvas.fill_rect(24, 640, 456, 66, MRZ_BG)
    canvas.hline(24, 640, 456, RULE)
    mrz_first, mrz_second = mrz_lines()
    canvas.draw_text(FONTS["mono_15"], 30, 664, mrz_first, INK)
    canvas.draw_text(FONTS["mono_15"], 30, 688, mrz_second, INK)


def draw_visa_page(canvas):
    """Right page: observations box, entry/departure stamps, fee seal,
    visa sticker, seafarer's endorsement."""
    page_x, page_y, page_w, page_h = RIGHT_PAGE_X, PAGE_TOP, PAGE_WIDTH, PAGE_HEIGHT
    page_frame(canvas, page_x, page_y, page_w, page_h)
    canvas.draw_text(FONTS["serif_bold_19"], 538, 48, "VISAS", INK, tracking=4)
    canvas.draw_text(FONTS["sans_bold_11"], 940, 48, "VISUM", NAVY_SOFT,
                     tracking=2)
    canvas.hline(538, 58, 456, GOLD, alpha=220)
    canvas.draw_text(FONTS["sans_11"], 538, 700, "PAGE 12  ·  SIDE 12",
                     NAVY_SOFT, tracking=1)

    # observations box
    canvas.rect(538, 72, 456, 98, NAVY, t=1)
    canvas.fill_rect(538, 72, 456, 20, NAVY)
    canvas.draw_text(FONTS["sans_bold_11"], 546, 86, "OBSERVATIONS / MERKNADER",
                     (255, 255, 255), tracking=1)
    canvas.draw_text(FONTS["sans_13"], 546, 114,
                     "Holder is a registered crew member of FV SKARVEN.",
                     INK)
    canvas.draw_text(FONTS["sans_13"], 546, 132,
                     "Innehaveren er registrert mannskap på FV SKARVEN.",
                     INK)
    canvas.draw_text(FONTS["sans_11"], 546, 154,
                     "This passport remains the property of the Republic of Nordhavn.",
                     NAVY_SOFT)

    # stamps, two columns (all four entry/departure stamps share one size)
    STAMP_W, STAMP_H = 218, 108
    iceland_stamp = make_stamp(STAMP_W, STAMP_H, (31, 78, 156), "ICELAND — KEFLAVIK",
                               "ENTRY", ["12 JUN 2024", "No. 4417"], plane=True)
    blit_stamp(canvas, iceland_stamp, 538, 190, -7)
    draw_fee_stamp(canvas, 875, 244, 56)

    norway_stamp = make_stamp(STAMP_W, STAMP_H, (36, 52, 77), "NORWAY — BERGEN",
                              "DEPARTURE", ["28 JUN 2024", "No. 0912"], plane=True)
    blit_stamp(canvas, norway_stamp, 538, 320, 6)
    uk_stamp = make_stamp(STAMP_W, STAMP_H, (126, 42, 78), "UNITED KINGDOM",
                          "ENTRY", ["LONDON HEATHROW", "03 SEP 2024"], plane=True)
    blit_stamp(canvas, uk_stamp, 766, 320, -4)

    faroe_stamp = make_stamp(STAMP_W, STAMP_H, (46, 107, 52), "FAROE ISLANDS",
                             "DEPARTURE", ["TORSHAVN", "17 MAR 2025"], plane=True)
    blit_stamp(canvas, faroe_stamp, 538, 450, 8)
    draw_visa_sticker(canvas, 766, 450, 218, 108, -3)

    endorsement_stamp = make_stamp(446, 92, (165, 42, 42), None, None, [], plane=False)
    endorsement_stamp.text_centered(FONTS["sans_bold_13"], 223, 30,
                                    "SEAFARER'S ENDORSEMENT", (165, 42, 42), tracking=1)
    endorsement_stamp.text_centered(FONTS["sans_13"], 223, 52, "SJØMANNS PÅTEGNING",
                                    (165, 42, 42))
    endorsement_stamp.text_centered(FONTS["sans_13"], 223, 72,
                                    "FV SKARVEN — CREW  ·  HAVNVIK 02 MAY 2023",
                                    (165, 42, 42))
    blit_stamp(canvas, endorsement_stamp, 538, 584, -2)


# ------------------------------------------------------------------ main ----
def main():
    """Load assets, draw both pages onto the desk spread, write the PNG."""
    global FONTS, PW, PH, PORTRAIT
    FONTS = load_fonts()
    PW, PH, PORTRAIT = load_portrait()
    canvas = Canvas(W, H, DESK)
    draw_data_page(canvas)
    draw_visa_page(canvas)
    png_size = write_png(OUT, canvas)
    print(f"wrote {OUT}: {W}x{H}, {png_size} bytes")
    assert W <= 1024 and H <= 1024, "over pixel cap"
    assert png_size <= 1048576, "over 1 MiB cap"


if __name__ == "__main__":
    main()
