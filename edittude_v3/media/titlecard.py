from __future__ import annotations

import struct
import zlib
from pathlib import Path

# 5x7 uppercase glyphs, bit rows. Enough for a title card without libfreetype.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "'": ("00100", "00100", "01000", "00000", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "/": ("00001", "00010", "00100", "01000", "10000", "00000", "00000"),
}


def write_title_png(
    path: Path,
    title: str,
    *,
    width: int,
    height: int,
    subtitle: str = "",
) -> Path:
    pixels = bytearray(width * height * 4)
    _fill_bar(pixels, width, height, y0=int(height * 0.70), y1=int(height * 0.94), rgba=b"\x00\x00\x00\x99")
    _blit_line(pixels, width, height, title.upper(), y_frac=0.78, scale=_scale(width, title, 0.70))
    if subtitle.strip():
        _blit_line(
            pixels,
            width,
            height,
            subtitle.upper(),
            y_frac=0.87,
            scale=max(2, _scale(width, subtitle, 0.42)),
        )
    _write_png(path, width, height, bytes(pixels))
    return path


def _fill_bar(pixels: bytearray, width: int, height: int, *, y0: int, y1: int, rgba: bytes) -> None:
    y0 = max(0, y0)
    y1 = min(height, y1)
    row = rgba * width
    for y in range(y0, y1):
        start = y * width * 4
        pixels[start : start + width * 4] = row


def _scale(width: int, text: str, fraction: float) -> int:
    glyph_w = 6  # 5 plus 1 gap
    raw = max(len(text), 1) * glyph_w
    return max(3, int((width * fraction) / raw))


def _blit_line(
    pixels: bytearray,
    width: int,
    height: int,
    text: str,
    *,
    y_frac: float,
    scale: int,
) -> None:
    glyph_w = 6
    glyph_h = 7
    text_w = len(text) * glyph_w * scale
    text_h = glyph_h * scale
    x0 = max(0, (width - text_w) // 2)
    y0 = max(0, int(height * y_frac) - text_h // 2)
    dots: list[tuple[int, int]] = []
    for index, char in enumerate(text):
        rows = _GLYPHS.get(char) or _GLYPHS[" "]
        origin_x = x0 + index * glyph_w * scale
        for gy, row in enumerate(rows):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue
                for oy in range(scale):
                    for ox in range(scale):
                        dots.append((origin_x + gx * scale + ox, y0 + gy * scale + oy))
    for x, y in dots:
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1)):
            xx, yy = x + dx, y + dy
            if 0 <= xx < width and 0 <= yy < height:
                i = (yy * width + xx) * 4
                pixels[i : i + 4] = b"\x00\x00\x00\xff"
    for x, y in dots:
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 4
            pixels[i : i + 4] = b"\xff\xff\xff\xff"


def _write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    )
