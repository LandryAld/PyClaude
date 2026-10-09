"""Séries de Fourier d'un tracé fermé et épicycles.

FFT radix-2 itérative (Cooley-Tukey, permutation par inversion de bits)
avec repli sur une DFT directe pour les tailles quelconques, rééchantillonnage
d'un tracé à abscisse curviligne constante, tri des harmoniques par amplitude.
"""

from __future__ import annotations

import cmath
import math
from typing import List, Sequence, Tuple

Epicycle = Tuple[int, float, float]  # (fréquence, amplitude, phase)


def is_power_of_two(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def fft(x: Sequence[complex]) -> List[complex]:
    n = len(x)
    if not is_power_of_two(n):
        return dft(x)
    a = list(x)
    j = 0
    for i in range(1, n):  # permutation par inversion des bits
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:
        w_step = cmath.exp(-2j * math.pi / size)
        half = size // 2
        for start in range(0, n, size):
            w = 1 + 0j
            for k in range(half):
                u = a[start + k]
                v = a[start + k + half] * w
                a[start + k] = u + v
                a[start + k + half] = u - v
                w *= w_step
        size *= 2
    return a


def dft(x: Sequence[complex]) -> List[complex]:
    n = len(x)
    out = []
    for k in range(n):
        s = 0j
        for t, v in enumerate(x):
            s += v * cmath.exp(-2j * math.pi * k * t / n)
        out.append(s)
    return out


def ifft(X: Sequence[complex]) -> List[complex]:
    n = len(X)
    conj = fft([v.conjugate() for v in X])
    return [v.conjugate() / n for v in conj]


def resample(points: Sequence[Tuple[float, float]], n: int, closed: bool = True
             ) -> List[Tuple[float, float]]:
    """Rééchantillonne un tracé en ``n`` points régulièrement espacés le long de la courbe."""
    pts = list(points)
    if len(pts) < 2:
        return pts * n if pts else []
    if closed and pts[0] != pts[-1]:
        pts.append(pts[0])
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg) or 1.0
    out = []
    step = total / n
    i, acc = 0, 0.0
    for k in range(n):
        target = k * step
        while i < len(seg) - 1 and acc + seg[i] < target:
            acc += seg[i]
            i += 1
        t = (target - acc) / seg[i] if seg[i] else 0.0
        t = max(0.0, min(1.0, t))
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def epicycles(points: Sequence[Tuple[float, float]]) -> List[Epicycle]:
    """Décompose le tracé (nombre complexe par point) en épicycles triés par amplitude."""
    z = [complex(x, y) for x, y in points]
    n = len(z)
    X = fft(z)
    cycles = []
    for k, c in enumerate(X):
        freq = k if k <= n // 2 else k - n  # fréquences négatives : rotation inverse
        c = c / n
        cycles.append((freq, abs(c), cmath.phase(c)))
    cycles.sort(key=lambda e: -e[1])
    return cycles


def evaluate(cycles: Sequence[Epicycle], t: float, count: int) -> List[Tuple[float, float]]:
    """Positions successives des centres des cercles au temps ``t`` ∈ [0, 1)."""
    x = y = 0.0
    chain = [(0.0, 0.0)]
    for freq, amp, phase in cycles[:count]:
        a = math.tau * freq * t + phase
        x += amp * math.cos(a)
        y += amp * math.sin(a)
        chain.append((x, y))
    return chain


def reconstruction_error(points: Sequence[Tuple[float, float]], cycles: Sequence[Epicycle],
                         count: int) -> float:
    n = len(points)
    err = 0.0
    for i, (px, py) in enumerate(points):
        x, y = evaluate(cycles, i / n, count)[-1]
        err += math.hypot(px - x, py - y)
    return err / max(1, n)


# -- formes prédéfinies ---------------------------------------------------------

def shape(name: str, n: int = 512) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    if name == "Cœur":
        for i in range(n):
            t = math.tau * i / n
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            pts.append((x / 17, -y / 17))
    elif name == "Étoile":
        k = 5
        corners = []
        for i in range(2 * k):
            r = 1.0 if i % 2 == 0 else 0.42
            a = math.pi * i / k - math.pi / 2
            corners.append((r * math.cos(a), r * math.sin(a)))
        pts = resample(corners, n)
    elif name == "Lemniscate":
        for i in range(n):
            t = math.tau * i / n
            d = 1 + math.sin(t) ** 2
            pts.append((math.cos(t) / d, math.sin(t) * math.cos(t) / d))
    elif name == "Carré":
        pts = resample([(-1, -1), (1, -1), (1, 1), (-1, 1)], n)
    elif name == "Rosace":
        for i in range(n):
            t = math.tau * i / n
            r = math.cos(4 * t) * 0.6 + 0.4
            pts.append((r * math.cos(t), r * math.sin(t)))
    elif name == "Clé de sol":
        # Courbe paramétrique stylisée (spirale + hampe)
        raw = []
        for i in range(n):
            t = i / n
            if t < 0.7:
                u = t / 0.7 * 3.2 * math.pi
                r = 0.18 + u / (3.2 * math.pi) * 0.5
                raw.append((r * math.cos(u + math.pi), 0.25 + r * math.sin(u + math.pi) * 1.2))
            else:
                u = (t - 0.7) / 0.3
                raw.append((0.1 * math.sin(u * 6), 0.85 - u * 1.9))
        pts = resample(raw, n)
    else:  # Engrenage
        for i in range(n):
            t = math.tau * i / n
            r = 0.8 + 0.12 * (1 if math.sin(12 * t) > 0 else -1)
            pts.append((r * math.cos(t), r * math.sin(t)))
    return pts


SHAPES = ["Cœur", "Étoile", "Lemniscate", "Rosace", "Engrenage", "Carré", "Clé de sol"]
