"""Analyse numérique : graduations « jolies », racines, intégrales, extrema."""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

Func = Callable[[float], float]


def nice_number(x: float, round_: bool) -> float:
    """Arrondit ``x`` à 1, 2, 5 × 10^n (algorithme de Heckbert)."""
    if x <= 0 or not math.isfinite(x):
        return 1.0
    exp = math.floor(math.log10(x))
    f = x / 10 ** exp
    if round_:
        nf = 1 if f < 1.5 else 2 if f < 3 else 5 if f < 7 else 10
    else:
        nf = 1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10
    return nf * 10 ** exp


def nice_ticks(lo: float, hi: float, max_ticks: int = 8) -> Tuple[float, List[float]]:
    """Renvoie (pas, graduations) couvrant [lo, hi]."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return 1.0, [lo]
    span = nice_number(hi - lo, False)
    step = nice_number(span / max(1, max_ticks - 1), True)
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    guard = 0
    while v <= hi + step * 1e-9 and guard < 1000:
        ticks.append(0.0 if abs(v) < step * 1e-9 else v)
        v += step
        guard += 1
    return step, ticks


def format_tick(v: float, step: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e6 or abs(v) < 1e-4:
        return f"{v:.2e}"
    decimals = max(0, -int(math.floor(math.log10(step)))) if step > 0 else 2
    text = f"{v:.{min(decimals, 10)}f}"
    return text


def format_value(v: float, digits: int = 6) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if math.isinf(v):
        return "∞" if v > 0 else "-∞"
    if v != 0 and (abs(v) >= 1e7 or abs(v) < 1e-4):
        return f"{v:.{digits - 2}e}"
    text = f"{v:.{digits}g}"
    return text


def _ok(y: float) -> bool:
    return y is not None and math.isfinite(y)


def bisect(f: Func, a: float, b: float, tol: float = 1e-12, max_iter: int = 200) -> Optional[float]:
    fa, fb = f(a), f(b)
    if not (_ok(fa) and _ok(fb)):
        return None
    if fa == 0:
        return a
    if fb == 0:
        return b
    if fa * fb > 0:
        return None
    for _ in range(max_iter):
        m = 0.5 * (a + b)
        fm = f(m)
        if not _ok(fm):
            return None
        if fm == 0 or (b - a) * 0.5 < tol:
            return m
        if fa * fm < 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
    return 0.5 * (a + b)


def newton(f: Func, df: Func, x0: float, tol: float = 1e-12, max_iter: int = 60) -> Optional[float]:
    x = x0
    for _ in range(max_iter):
        fx, dfx = f(x), df(x)
        if not (_ok(fx) and _ok(dfx)) or dfx == 0:
            return None
        step = fx / dfx
        x -= step
        if abs(step) < tol * max(1.0, abs(x)):
            return x
    return None


def find_roots(f: Func, a: float, b: float, samples: int = 1000) -> List[float]:
    """Racines de ``f`` sur [a, b] : changements de signe raffinés par dichotomie.

    Les sauts infinis (pôles comme tan) sont écartés en vérifiant |f| au point trouvé.
    """
    roots: List[float] = []
    if b <= a:
        return roots
    h = (b - a) / samples
    x0, y0 = a, f(a)
    span = 0.0
    ys = []
    for i in range(1, samples + 1):
        x1 = a + i * h
        y1 = f(x1)
        if _ok(y1):
            ys.append(abs(y1))
        if _ok(y0) and _ok(y1):
            if y0 == 0:
                roots.append(x0)
            elif y0 * y1 < 0:
                r = bisect(f, x0, x1)
                if r is not None:
                    roots.append(r)
        x0, y0 = x1, y1
    if ys:
        ys.sort()
        span = ys[len(ys) // 2] or 1.0
    clean: List[float] = []
    for r in roots:
        fr = f(r)
        if not _ok(fr) or abs(fr) > 1e-6 * max(1.0, span):
            continue  # pôle et non racine
        if clean and abs(r - clean[-1]) < h * 0.5:
            continue
        clean.append(r)
    return clean


def simpson(f: Func, a: float, b: float, n: int = 2000) -> float:
    """Intégrale de Simpson composite (les points non finis comptent pour 0)."""
    if n % 2:
        n += 1
    h = (b - a) / n
    total = 0.0
    for i in range(n + 1):
        y = f(a + i * h)
        if not _ok(y):
            y = 0.0
        w = 1 if i in (0, n) else (4 if i % 2 else 2)
        total += w * y
    return total * h / 3


def derivative(f: Func, x: float, h: float = 1e-5) -> float:
    return (f(x + h) - f(x - h)) / (2 * h)


def find_extrema(f: Func, a: float, b: float, samples: int = 800) -> List[Tuple[float, float, str]]:
    """Extrema locaux : racines de la dérivée numérique + classification."""
    def df(x: float) -> float:
        return derivative(f, x, (b - a) * 1e-7 + 1e-9)

    out: List[Tuple[float, float, str]] = []
    for x in find_roots(df, a, b, samples):
        y = f(x)
        if not _ok(y):
            continue
        d = (b - a) * 1e-4
        left, right = f(x - d), f(x + d)
        if not (_ok(left) and _ok(right)):
            continue
        if left > y and right > y:
            out.append((x, y, "min"))
        elif left < y and right < y:
            out.append((x, y, "max"))
    return out


def linspace(a: float, b: float, n: int) -> List[float]:
    if n <= 1:
        return [a]
    h = (b - a) / (n - 1)
    return [a + i * h for i in range(n)]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return math.nan
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if not n:
        return math.nan
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])
