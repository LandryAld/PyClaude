"""Recherche approximative (« fuzzy ») pour la palette de commandes.

Programmation dynamique sur la meilleure correspondance en sous-séquence :
bonus pour les caractères consécutifs et les débuts de mots, pénalité pour les
trous. Insensible à la casse et aux accents (« theme » trouve « Thème »).
"""

from __future__ import annotations

import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")

_BONUS_FIRST = 12
_BONUS_WORD = 10
_BONUS_CONSEC = 14
_BONUS_CASE = 1
_PENALTY_GAP = 1
_SEPARATORS = set(" _-/.:>()[]")


def fold(text: str) -> str:
    """Minuscules sans accents, longueur préservée caractère par caractère."""
    out = []
    for ch in text:
        decomposed = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        out.append((base[:1] or ch).lower())
    return "".join(out)


def _word_starts(text: str) -> List[bool]:
    starts = []
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else " "
        starts.append(i == 0 or prev in _SEPARATORS
                      or (prev.islower() and ch.isupper()))
    return starts


def match(pattern: str, text: str) -> Optional[Tuple[int, List[int]]]:
    """Renvoie ``(score, indices)`` ou ``None`` si ``pattern`` n'est pas une sous-séquence."""
    if not pattern:
        return 0, []
    p = fold(pattern.replace(" ", ""))
    t = fold(text)
    n, m = len(t), len(p)
    if m == 0:
        return 0, []
    if m > n:
        return None
    # Pré-filtre rapide
    pos = 0
    for ch in p:
        pos = t.find(ch, pos)
        if pos < 0:
            return None
        pos += 1

    starts = _word_starts(text)
    NEG = -10 ** 9
    # score[i][j] : meilleur score avec p[:i+1] apparié et p[i] sur t[j]
    score = [[NEG] * n for _ in range(m)]
    back = [[-1] * n for _ in range(m)]
    for j in range(n):
        if t[j] == p[0]:
            s = 0
            if j == 0:
                s += _BONUS_FIRST
            if starts[j]:
                s += _BONUS_WORD
            if pattern[:1] == text[j]:
                s += _BONUS_CASE
            score[0][j] = s - j * _PENALTY_GAP * 0.5
    for i in range(1, m):
        best_prev, best_k = NEG, -1
        row_prev = score[i - 1]
        for j in range(i, n):
            k = j - 1
            if row_prev[k] > NEG:
                # Pénalité de trou proportionnelle à la distance
                cand = row_prev[k] + k * _PENALTY_GAP
                if cand > best_prev:
                    best_prev, best_k = cand, k
            if t[j] != p[i]:
                continue
            options = []
            if row_prev[j - 1] > NEG:
                options.append((row_prev[j - 1] + _BONUS_CONSEC, j - 1))
            if best_k >= 0:
                options.append((best_prev - (j - 1) * _PENALTY_GAP - _PENALTY_GAP, best_k))
            if not options:
                continue
            s, k = max(options)
            if starts[j]:
                s += _BONUS_WORD
            score[i][j] = s
            back[i][j] = k
    last = score[m - 1]
    j = max(range(n), key=lambda x: last[x])
    if last[j] <= NEG:
        return None
    total = last[j]
    indices = [j]
    for i in range(m - 1, 0, -1):
        j = back[i][j]
        indices.append(j)
    indices.reverse()
    total -= (n - m) * 0.1  # léger avantage aux textes courts
    return int(total * 10), indices


def rank(pattern: str, items: Iterable[T], key=lambda x: str(x),
         limit: int = 50) -> List[Tuple[int, T, List[int]]]:
    results = []
    for item in items:
        res = match(pattern, key(item))
        if res is not None:
            results.append((res[0], item, res[1]))
    results.sort(key=lambda r: -r[0])
    return results[:limit]


def highlight_segments(text: str, indices: Sequence[int]) -> List[Tuple[str, bool]]:
    """Découpe ``text`` en segments (texte, apparié?) pour l'affichage."""
    marks = set(indices)
    segs: List[Tuple[str, bool]] = []
    for i, ch in enumerate(text):
        flag = i in marks
        if segs and segs[-1][1] == flag:
            segs[-1] = (segs[-1][0] + ch, flag)
        else:
            segs.append((ch, flag))
    return segs
