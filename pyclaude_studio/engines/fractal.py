"""Moteur de fractales à temps d'échappement.

Trois moteurs interchangeables produisant la même sortie — une image
**indexée** (un octet par pixel ; 255 = intérieur de l'ensemble) :

* ``numpy``   : vectorisé, avec compaction des points actifs à chaque itération ;
* ``process`` : Python pur réparti sur tous les cœurs (``ProcessPoolExecutor``) ;
* ``thread``  : Python pur dans un seul thread (repli universel).

Le coloriage se fait ensuite par tables (voir :mod:`core.imaging`), ce qui
permet de changer de palette ou de faire cycler les couleurs sans recalculer.
"""

from __future__ import annotations

import math
import os
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..core.colors import RGB, colormap_lut, hex_to_rgb, mix

INSIDE = 255
KINDS: Dict[str, str] = {
    "mandelbrot": "Mandelbrot",
    "julia": "Julia",
    "burning_ship": "Burning Ship",
    "tricorn": "Tricorne",
    "multibrot3": "Multibrot z³+c",
    "newton": "Newton z³−1",
}
_LOG2 = math.log(2.0)
_LOG3 = math.log(3.0)
_BAILOUT = 256.0 * 256.0


@dataclass(frozen=True)
class FractalParams:
    kind: str = "mandelbrot"
    cx: float = -0.6
    cy: float = 0.0
    span: float = 3.4          # largeur de la vue dans le plan complexe
    max_iter: int = 200
    density: float = 3.0       # vitesse de défilement de la palette
    jr: float = -0.8           # paramètre c des ensembles de Julia
    ji: float = 0.156

    def scale(self, width: int) -> float:
        return self.span / max(1, width)

    def to_complex(self, px: float, py: float, width: int, height: int) -> Tuple[float, float]:
        s = self.scale(width)
        return self.cx + (px - width / 2) * s, self.cy - (py - height / 2) * s

    def zoomed(self, factor: float, px: float, py: float, width: int, height: int) -> "FractalParams":
        """Zoom d'un facteur ``factor`` (<1 = rapprocher) en gardant le point visé fixe."""
        zr, zi = self.to_complex(px, py, width, height)
        span = self.span * factor
        s = span / max(1, width)
        cx = zr - (px - width / 2) * s
        cy = zi + (py - height / 2) * s
        return replace(self, cx=cx, cy=cy, span=span)

    def framed(self, x0: float, y0: float, x1: float, y1: float, width: int,
               height: int) -> "FractalParams":
        """Vue cadrant le rectangle écran (x0, y0)-(x1, y1)."""
        a = self.to_complex(min(x0, x1), min(y0, y1), width, height)
        b = self.to_complex(max(x0, x1), max(y0, y1), width, height)
        span_x = abs(b[0] - a[0])
        span_y = abs(b[1] - a[1]) * width / max(1, height)
        return replace(self, cx=(a[0] + b[0]) / 2, cy=(a[1] + b[1]) / 2,
                       span=max(span_x, span_y, 1e-15))

    @property
    def zoom_level(self) -> float:
        return 3.4 / self.span


PRESETS: List[Tuple[str, FractalParams]] = [
    ("Vue d'ensemble", FractalParams()),
    ("Vallée des hippocampes", FractalParams(cx=-0.743643887, cy=0.131825904, span=0.0035,
                                             max_iter=600, density=2.0)),
    ("Spirale double", FractalParams(cx=-0.7746806106, cy=0.1374168856, span=0.0004,
                                     max_iter=900, density=1.5)),
    ("Éclair", FractalParams(cx=-1.315180982097868, cy=0.073481649996795, span=0.0001,
                             max_iter=1200, density=1.2)),
    ("Mini-Mandelbrot", FractalParams(cx=-1.7685736562, cy=0.0009484, span=0.00012,
                                      max_iter=1000, density=1.5)),
    ("Vallée des éléphants", FractalParams(cx=0.2827, cy=0.0101, span=0.028,
                                           max_iter=500, density=2.5)),
    ("Julia : dendrite", FractalParams(kind="julia", cx=0.0, cy=0.0, span=3.2, jr=0.0, ji=1.0,
                                       max_iter=300)),
    ("Julia : lapin de Douady", FractalParams(kind="julia", cx=0.0, cy=0.0, span=3.2,
                                              jr=-0.123, ji=0.745, max_iter=300)),
    ("Julia : galaxie", FractalParams(kind="julia", cx=0.0, cy=0.0, span=3.0, jr=-0.8,
                                      ji=0.156, max_iter=400)),
    ("Julia : dragon de San Marco", FractalParams(kind="julia", cx=0.0, cy=0.0, span=3.4,
                                                  jr=-0.75, ji=0.0, max_iter=300)),
    ("Burning Ship : l'armada", FractalParams(kind="burning_ship", cx=-1.762, cy=0.028,
                                              span=0.09, max_iter=400, density=3)),
    ("Tricorne", FractalParams(kind="tricorn", cx=-0.3, cy=0.0, span=4.0, max_iter=200)),
    ("Multibrot z³+c", FractalParams(kind="multibrot3", cx=0.0, cy=0.0, span=3.2,
                                     max_iter=200)),
    ("Newton z³−1", FractalParams(kind="newton", cx=0.0, cy=0.0, span=3.0, max_iter=60,
                                  density=4.0)),
]


def default_for(kind: str) -> FractalParams:
    for _, p in PRESETS:
        if p.kind == kind:
            return p
    return FractalParams(kind=kind)


# ---------------------------------------------------------------------------
# Python pur (fonctions de niveau module : sérialisables pour le multiprocessus)
# ---------------------------------------------------------------------------

def _smooth_index(it: int, mag2: float, density: float, log_power: float) -> int:
    nu = it + 1 - math.log(math.log(math.sqrt(mag2))) / log_power
    return int(nu * density) % 255


def render_rows_py(p: FractalParams, width: int, height: int, y0: int, y1: int) -> bytes:
    out = bytearray(width * (y1 - y0))
    s = p.scale(width)
    xs = [p.cx + (px - width / 2) * s for px in range(width)]  # même formule que numpy
    max_iter = p.max_iter
    density = p.density
    kind = p.kind
    k = 0
    for py in range(y0, y1):
        ci = p.cy - (py - height / 2) * s
        if kind == "mandelbrot":
            ci2 = ci * ci
            for px in range(width):
                cr = xs[px]
                # Cardioïde principale et bulbe de période 2 : intérieur garanti
                q = (cr - 0.25) ** 2 + ci2
                if q * (q + (cr - 0.25)) <= 0.25 * ci2 or (cr + 1) ** 2 + ci2 <= 0.0625:
                    out[k] = INSIDE
                    k += 1
                    continue
                zr = zi = zr2 = zi2 = 0.0
                it = 0
                while it < max_iter and zr2 + zi2 <= _BAILOUT:
                    zi = 2.0 * zr * zi + ci
                    zr = zr2 - zi2 + cr
                    zr2 = zr * zr
                    zi2 = zi * zi
                    it += 1
                out[k] = INSIDE if it >= max_iter else _smooth_index(it, zr2 + zi2, density, _LOG2)
                k += 1
        elif kind == "julia":
            jr, ji = p.jr, p.ji
            for px in range(width):
                zr = xs[px]
                zi = ci
                zr2, zi2 = zr * zr, zi * zi
                it = 0
                while it < max_iter and zr2 + zi2 <= _BAILOUT:
                    zi = 2.0 * zr * zi + ji
                    zr = zr2 - zi2 + jr
                    zr2 = zr * zr
                    zi2 = zi * zi
                    it += 1
                out[k] = INSIDE if it >= max_iter else _smooth_index(it, zr2 + zi2, density, _LOG2)
                k += 1
        elif kind == "burning_ship":
            cis = -ci  # convention : le « navire » apparaît à l'endroit
            for px in range(width):
                cr = xs[px]
                zr = zi = zr2 = zi2 = 0.0
                it = 0
                while it < max_iter and zr2 + zi2 <= _BAILOUT:
                    zi = abs(2.0 * zr * zi) + cis
                    zr = zr2 - zi2 + cr
                    zr2 = zr * zr
                    zi2 = zi * zi
                    it += 1
                out[k] = INSIDE if it >= max_iter else _smooth_index(it, zr2 + zi2, density, _LOG2)
                k += 1
        elif kind == "tricorn":
            for px in range(width):
                cr = xs[px]
                zr = zi = zr2 = zi2 = 0.0
                it = 0
                while it < max_iter and zr2 + zi2 <= _BAILOUT:
                    zi = -2.0 * zr * zi + ci
                    zr = zr2 - zi2 + cr
                    zr2 = zr * zr
                    zi2 = zi * zi
                    it += 1
                out[k] = INSIDE if it >= max_iter else _smooth_index(it, zr2 + zi2, density, _LOG2)
                k += 1
        elif kind == "multibrot3":
            for px in range(width):
                cr = xs[px]
                zr = zi = 0.0
                it = 0
                mag2 = 0.0
                while it < max_iter and mag2 <= _BAILOUT:
                    zr2, zi2 = zr * zr, zi * zi
                    zr, zi = zr * (zr2 - 3 * zi2) + cr, zi * (3 * zr2 - zi2) + ci
                    mag2 = zr * zr + zi * zi
                    it += 1
                out[k] = INSIDE if it >= max_iter else _smooth_index(it, mag2, density, _LOG3)
                k += 1
        elif kind == "newton":
            roots = ((1.0, 0.0), (-0.5, math.sqrt(3) / 2), (-0.5, -math.sqrt(3) / 2))
            for px in range(width):
                zr = xs[px]
                zi = ci
                idx = INSIDE
                for it in range(max_iter):
                    # z <- z - (z^3 - 1) / (3 z^2)
                    zr2, zi2 = zr * zr, zi * zi
                    # numérateur z^3 - 1 et dénominateur 3 z^2
                    nr = zr * (zr2 - 3 * zi2) - 1.0
                    ni = zi * (3 * zr2 - zi2)
                    dr, di = 3 * (zr2 - zi2), 6 * zr * zi
                    d = dr * dr + di * di
                    if d == 0:
                        break
                    zr -= (nr * dr + ni * di) / d
                    zi -= (ni * dr - nr * di) / d
                    for r, (rr, ri) in enumerate(roots):
                        if (zr - rr) ** 2 + (zi - ri) ** 2 < 1e-6:
                            idx = r * 85 + min(84, int(it * density))
                            break
                    if idx != INSIDE:
                        break
                out[k] = idx
                k += 1
        else:
            raise ValueError(f"fractale inconnue : {kind}")
    return bytes(out)


def _chunk_job(args: Tuple[FractalParams, int, int, int, int]) -> Tuple[int, bytes]:
    p, width, height, y0, y1 = args
    return y0, render_rows_py(p, width, height, y0, y1)


# ---------------------------------------------------------------------------
# numpy
# ---------------------------------------------------------------------------

def numpy_available() -> bool:
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def render_rows_numpy(p: FractalParams, width: int, height: int, y0: int, y1: int) -> bytes:
    import numpy as np

    s = p.scale(width)
    xs = p.cx + (np.arange(width) - width / 2) * s
    ys = p.cy - (np.arange(y0, y1) - height / 2) * s
    c = (xs[None, :] + 1j * ys[:, None]).ravel()
    n = c.size
    out = np.full(n, INSIDE, dtype=np.uint8)
    idx = np.arange(n)
    with np.errstate(all="ignore"):
        if p.kind == "newton":
            z = c.copy()
            roots = np.array([1, complex(-0.5, math.sqrt(3) / 2), complex(-0.5, -math.sqrt(3) / 2)])
            for it in range(p.max_iter):
                z = z - (z ** 3 - 1) / (3 * z ** 2)
                done = np.zeros(z.size, dtype=bool)
                for r, root in enumerate(roots):
                    hit = np.abs(z - root) ** 2 < 1e-6
                    if hit.any():
                        out[idx[hit]] = r * 85 + min(84, int(it * p.density))
                        done |= hit
                keep = ~done & np.isfinite(z)
                z, idx = z[keep], idx[keep]
                if idx.size == 0:
                    break
            return out.tobytes()
        # Parties réelle et imaginaire séparées : mêmes opérations flottantes, dans le
        # même ordre, que le moteur Python (aucune fusion multiplication-addition).
        if p.kind == "julia":
            zr, zi = c.real.copy(), c.imag.copy()
            cr: object = p.jr
            ci: object = p.ji
        else:
            zr = np.zeros(n)
            zi = np.zeros(n)
            cr, ci = c.real.copy(), c.imag.copy()
            if p.kind == "burning_ship":
                ci = -ci
        log_power = _LOG3 if p.kind == "multibrot3" else _LOG2
        kind = p.kind
        for it in range(p.max_iter):
            if kind == "multibrot3":
                zr2, zi2 = zr * zr, zi * zi
                zr, zi = zr * (zr2 - 3 * zi2) + cr, zi * (3 * zr2 - zi2) + ci
            else:
                zr2, zi2 = zr * zr, zi * zi
                if kind == "burning_ship":
                    zi = np.abs(2.0 * zr * zi) + ci
                elif kind == "tricorn":
                    zi = -2.0 * zr * zi + ci
                else:
                    zi = 2.0 * zr * zi + ci
                zr = zr2 - zi2 + cr
            mag2 = zr * zr + zi * zi
            esc = mag2 > _BAILOUT
            if esc.any():
                nu = it + 2 - np.log(np.log(np.sqrt(mag2[esc]))) / log_power
                out[idx[esc]] = (nu * p.density).astype(np.int64) % 255
                keep = ~esc
                zr, zi, idx = zr[keep], zi[keep], idx[keep]
                if not isinstance(cr, float):
                    cr, ci = cr[keep], ci[keep]  # type: ignore[index]
            if idx.size == 0:
                break
    return out.tobytes()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_POOL: Optional[ProcessPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> ProcessPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2)))
        return _POOL


def shutdown_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.shutdown(wait=False, cancel_futures=True)  # type: ignore[call-arg]
            except TypeError:
                _POOL.shutdown(wait=False)
            _POOL = None


def resolve_engine(engine: str) -> str:
    if engine == "auto":
        return "numpy" if numpy_available() else "process"
    if engine == "numpy" and not numpy_available():
        return "process"
    return engine


def render(p: FractalParams, width: int, height: int, *, engine: str = "auto",
           on_chunk: Optional[Callable[[int, int, bytes], None]] = None,
           cancelled: Callable[[], bool] = lambda: False,
           chunk_rows: Optional[int] = None) -> Optional[bytes]:
    """Calcule l'image indexée complète ; ``on_chunk(y0, y1, data)`` reçoit chaque bande.

    Renvoie ``None`` si l'annulation a été demandée.
    """
    engine = resolve_engine(engine)
    rows = chunk_rows or (48 if engine == "numpy" else 8)
    bands = [(y, min(height, y + rows)) for y in range(0, height, rows)]
    image = bytearray(width * height)

    def store(y0: int, data: bytes) -> None:
        y1 = y0 + len(data) // width
        image[y0 * width:y1 * width] = data
        if on_chunk:
            on_chunk(y0, y1, data)

    if engine == "process":
        try:
            pool = get_pool()
            # Ordre entrelacé : l'image se dessine uniformément pendant le calcul
            order = bands[::2] + bands[1::2]
            pending: Dict[Future, int] = {pool.submit(_chunk_job, (p, width, height, a, b)): a
                                          for a, b in order}
            while pending:
                if cancelled():
                    for f in pending:
                        f.cancel()
                    return None
                done, _ = wait(list(pending), timeout=0.1, return_when=FIRST_COMPLETED)
                for f in done:
                    pending.pop(f)
                    y0, data = f.result()
                    store(y0, data)
            return bytes(image)
        except Exception:
            shutdown_pool()
            engine = "thread"  # repli robuste si le multiprocessus est indisponible
    fn = render_rows_numpy if engine == "numpy" else render_rows_py
    for a, b in bands:
        if cancelled():
            return None
        store(a, fn(p, width, height, a, b))
    return bytes(image)


def orbit(p: FractalParams, cr: float, ci: float, n: int = 120) -> List[Tuple[float, float]]:
    """Trajectoire de z sous l'itération, pour la visualisation interactive."""
    pts: List[Tuple[float, float]] = []
    if p.kind in ("julia", "newton"):
        zr, zi = cr, ci
    else:
        zr = zi = 0.0
    for _ in range(n):
        if p.kind == "julia":
            zr, zi = zr * zr - zi * zi + p.jr, 2 * zr * zi + p.ji
        elif p.kind == "burning_ship":
            zr, zi = zr * zr - zi * zi + cr, abs(2 * zr * zi) - ci
        elif p.kind == "tricorn":
            zr, zi = zr * zr - zi * zi + cr, -2 * zr * zi + ci
        elif p.kind == "multibrot3":
            zr, zi = zr ** 3 - 3 * zr * zi * zi + cr, 3 * zr * zr * zi - zi ** 3 + ci
        elif p.kind == "newton":
            z = complex(zr, zi)
            if z == 0:
                break
            z = z - (z ** 3 - 1) / (3 * z * z)
            zr, zi = z.real, z.imag
        else:
            zr, zi = zr * zr - zi * zi + cr, 2 * zr * zi + ci
        if zr * zr + zi * zi > 1e6 or not (math.isfinite(zr) and math.isfinite(zi)):
            break
        pts.append((zr, zi if p.kind != "burning_ship" else -zi))
    return pts


def build_lut(p: FractalParams, colormap: str, offset: int = 0,
              inside: str = "#000000") -> List[RGB]:
    """Table de 256 couleurs ; l'entrée 255 est la couleur de l'intérieur."""
    if p.kind == "newton":
        base = colormap_lut(colormap, 256)
        lut: List[RGB] = []
        for r in range(3):
            color = base[(r * 85 + offset) % 256]
            hexc = "#%02x%02x%02x" % color
            for j in range(85):
                t = j / 84
                lut.append(hex_to_rgb(mix(mix(hexc, "#ffffff", 0.25), "#000000", t * 0.85)))
        lut.append(hex_to_rgb(inside))
        return lut
    cyc = colormap_lut(colormap, 255)
    lut = [cyc[(i + offset) % 255] for i in range(255)]
    lut.append(hex_to_rgb(inside))
    return lut
