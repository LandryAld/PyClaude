"""Images sans Pillow : encodeurs PPM/PNG, recoloration rapide par tables.

L'astuce centrale : une image « indexée » (un octet par pixel) se recolore en
trois appels ``bytes.translate`` exécutés en C, ce qui permet le cyclage de
palette en temps réel sans numpy.
"""

from __future__ import annotations

import struct
import zlib
from typing import Sequence, Tuple

from .colors import RGB, channel_tables


def ppm(width: int, height: int, rgb: bytes) -> bytes:
    if len(rgb) != width * height * 3:
        raise ValueError("taille de tampon RGB incohérente")
    return b"P6 %d %d 255\n" % (width, height) + bytes(rgb)


def png(width: int, height: int, rgb: bytes, level: int = 6) -> bytes:
    """Encode un tampon RGB 8 bits en PNG (filtre 0, compression zlib)."""
    if len(rgb) != width * height * 3:
        raise ValueError("taille de tampon RGB incohérente")
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b""))


def decode_png_rgb(data: bytes) -> Tuple[int, int, bytes]:
    """Décode un PNG RGB 8 bits non entrelacé (filtres 0 à 4) — pour les tests et l'import."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("signature PNG invalide")
    pos, width, height, idat = 8, 0, 0, b""
    color_type = 2
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        if zlib.crc32(tag + body) & 0xFFFFFFFF != crc:
            raise ValueError(f"CRC invalide pour {tag!r}")
        if tag == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or color_type not in (2, 6) or interlace:
                raise ValueError("seuls les PNG RGB/RGBA 8 bits non entrelacés sont pris en charge")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + length
    bpp = 3 if color_type == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * bpp
    out = bytearray()
    prev = bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        prev = line
        if bpp == 4:
            del line[3::4]
        out += line
    return width, height, bytes(out)


def index_to_rgb(index: bytes, tables: Tuple[bytes, bytes, bytes]) -> bytes:
    """Convertit une image indexée (1 octet/pixel) en RGB via 3 ``translate``."""
    r_t, g_t, b_t = tables
    out = bytearray(len(index) * 3)
    out[0::3] = index.translate(r_t)
    out[1::3] = index.translate(g_t)
    out[2::3] = index.translate(b_t)
    return bytes(out)


def lut_to_tables(lut: Sequence[RGB]) -> Tuple[bytes, bytes, bytes]:
    return channel_tables(lut)


def upscale(width: int, height: int, rgb: bytes, k: int) -> Tuple[int, int, bytes]:
    """Agrandissement « plus proche voisin » d'un facteur entier ``k``."""
    if k <= 1:
        return width, height, rgb
    stride = width * 3
    rows = []
    for y in range(height):
        row = rgb[y * stride:(y + 1) * stride]
        wide = b"".join(row[i:i + 3] * k for i in range(0, stride, 3))
        rows.append(wide * k)
    return width * k, height * k, b"".join(rows)


def solid(width: int, height: int, color: RGB) -> bytes:
    return bytes(color) * (width * height)


def photo_image(master, width: int, height: int, rgb: bytes, zoom: int = 1):
    """Crée un ``tk.PhotoImage`` depuis un tampon RGB (via le format PPM natif de Tk)."""
    import tkinter as tk  # import local : ce module reste utilisable sans Tk

    img = tk.PhotoImage(master=master, data=ppm(width, height, rgb), format="PPM")
    if zoom > 1:
        img = img.zoom(zoom)
    return img
