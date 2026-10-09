"""Outils de couleur : conversions, mélanges, contrastes, dégradés et palettes."""

from __future__ import annotations

import colorsys
from typing import Dict, List, Sequence, Tuple

RGB = Tuple[int, int, int]


def hex_to_rgb(color: str) -> RGB:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError(f"couleur invalide : {color!r}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rgb_to_hex(rgb: Sequence[float]) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def mix(a: str, b: str, t: float) -> str:
    """Interpolation linéaire entre deux couleurs (t=0 → a, t=1 → b)."""
    t = max(0.0, min(1.0, t))
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def lighten(color: str, amount: float) -> str:
    return mix(color, "#ffffff", amount)


def darken(color: str, amount: float) -> str:
    return mix(color, "#000000", amount)


def _channel(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    r, g, b = hex_to_rgb(color)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def best_text_color(background: str, light: str = "#ffffff", dark: str = "#0b0b0b") -> str:
    return light if contrast_ratio(background, light) >= contrast_ratio(background, dark) else dark


def is_dark(color: str) -> bool:
    return luminance(color) < 0.2


def hsv_to_hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return rgb_to_hex((r * 255, g * 255, b * 255))


def hex_to_hsv(color: str) -> Tuple[float, float, float]:
    r, g, b = hex_to_rgb(color)
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)


def gradient(stops: Sequence[Tuple[float, str]], n: int) -> List[RGB]:
    """Échantillonne ``n`` couleurs le long d'un dégradé défini par des arrêts."""
    pts = sorted((max(0.0, min(1.0, p)), hex_to_rgb(c)) for p, c in stops)
    if pts[0][0] > 0:
        pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < 1:
        pts.append((1.0, pts[-1][1]))
    out: List[RGB] = []
    seg = 0
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        while seg < len(pts) - 2 and t > pts[seg + 1][0]:
            seg += 1
        (p0, c0), (p1, c1) = pts[seg], pts[seg + 1]
        u = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
        out.append(tuple(int(round(c0[k] + (c1[k] - c0[k]) * u)) for k in range(3)))  # type: ignore[misc]
    return out


def gradient_hex(stops: Sequence[Tuple[float, str]], n: int) -> List[str]:
    return [rgb_to_hex(c) for c in gradient(stops, n)]


def channel_tables(lut: Sequence[RGB]) -> Tuple[bytes, bytes, bytes]:
    """Tables de traduction (256 octets) par canal pour ``bytes.translate``."""
    if len(lut) != 256:
        raise ValueError("une table de couleurs doit contenir 256 entrées")
    return (bytes(c[0] for c in lut), bytes(c[1] for c in lut), bytes(c[2] for c in lut))


# Palettes artistiques (fractales, automates, rendus) -------------------------
COLORMAPS: Dict[str, List[Tuple[float, str]]] = {
    "Aurore": [(0.0, "#000764"), (0.16, "#206bcb"), (0.42, "#edffff"),
               (0.6425, "#ffaa00"), (0.8575, "#000200"), (1.0, "#000764")],
    "Feu": [(0.0, "#000000"), (0.25, "#7a0000"), (0.45, "#e33b00"), (0.65, "#ffb000"),
            (0.82, "#fff6c0"), (1.0, "#000000")],
    "Océan": [(0.0, "#020b1c"), (0.3, "#0b3d6e"), (0.55, "#1baf9a"), (0.75, "#e8fbff"),
              (1.0, "#020b1c")],
    "Néon": [(0.0, "#0d0221"), (0.25, "#7b2cbf"), (0.5, "#ff2a6d"), (0.75, "#05d9e8"),
             (1.0, "#0d0221")],
    "Forêt": [(0.0, "#05140b"), (0.3, "#1b5e20"), (0.6, "#9ccc65"), (0.8, "#fff8d6"),
              (1.0, "#05140b")],
    "Encre": [(0.0, "#000000"), (0.5, "#ffffff"), (1.0, "#000000")],
    "Améthyste": [(0.0, "#12002b"), (0.35, "#5a189a"), (0.6, "#e0aaff"), (0.8, "#fff0f6"),
                  (1.0, "#12002b")],
    "Prisme": [(i / 6, hsv_to_hex(i / 6, 0.85, 1.0)) for i in range(7)],
}

# Palette catégorielle validée (ordre fixe, jamais cyclé) : clair / sombre.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}


def colormap_lut(name: str, size: int = 256) -> List[RGB]:
    stops = COLORMAPS.get(name) or COLORMAPS["Aurore"]
    return gradient(stops, size)
