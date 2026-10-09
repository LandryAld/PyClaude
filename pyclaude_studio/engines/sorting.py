"""Algorithmes de tri instrumentés pour la visualisation.

Chaque algorithme trie la liste **en place** et émet des événements :
``("cmp", i, j)`` comparaison, ``("swap", i, j)`` échange, ``("set", i, v)``
écriture, ``("pivot", i, -1)`` mise en évidence, ``("done", i, -1)`` position finale.
"""

from __future__ import annotations

import random
from typing import Callable, Dict, Generator, List, Tuple

Op = Tuple[str, int, int]
SortGen = Generator[Op, None, None]


def bubble(a: List[int]) -> SortGen:
    n = len(a)
    for end in range(n - 1, 0, -1):
        swapped = False
        for i in range(end):
            yield "cmp", i, i + 1
            if a[i] > a[i + 1]:
                a[i], a[i + 1] = a[i + 1], a[i]
                yield "swap", i, i + 1
                swapped = True
        yield "done", end, -1
        if not swapped:
            break


def cocktail(a: List[int]) -> SortGen:
    lo, hi = 0, len(a) - 1
    while lo < hi:
        swapped = False
        for i in range(lo, hi):
            yield "cmp", i, i + 1
            if a[i] > a[i + 1]:
                a[i], a[i + 1] = a[i + 1], a[i]
                yield "swap", i, i + 1
                swapped = True
        yield "done", hi, -1
        hi -= 1
        for i in range(hi, lo, -1):
            yield "cmp", i - 1, i
            if a[i - 1] > a[i]:
                a[i - 1], a[i] = a[i], a[i - 1]
                yield "swap", i - 1, i
                swapped = True
        yield "done", lo, -1
        lo += 1
        if not swapped:
            break


def insertion(a: List[int]) -> SortGen:
    for i in range(1, len(a)):
        j = i
        while j > 0:
            yield "cmp", j - 1, j
            if a[j - 1] <= a[j]:
                break
            a[j - 1], a[j] = a[j], a[j - 1]
            yield "swap", j - 1, j
            j -= 1


def binary_insertion(a: List[int]) -> SortGen:
    for i in range(1, len(a)):
        v = a[i]
        lo, hi = 0, i
        while lo < hi:
            mid = (lo + hi) // 2
            yield "cmp", mid, i
            if a[mid] <= v:
                lo = mid + 1
            else:
                hi = mid
        for j in range(i, lo, -1):
            a[j] = a[j - 1]
            yield "set", j, a[j]
        a[lo] = v
        yield "set", lo, v


def selection(a: List[int]) -> SortGen:
    n = len(a)
    for i in range(n - 1):
        m = i
        yield "pivot", i, -1
        for j in range(i + 1, n):
            yield "cmp", m, j
            if a[j] < a[m]:
                m = j
        if m != i:
            a[i], a[m] = a[m], a[i]
            yield "swap", i, m
        yield "done", i, -1


def shell(a: List[int]) -> SortGen:
    gaps = [701, 301, 132, 57, 23, 10, 4, 1]  # suite de Ciura
    n = len(a)
    for gap in gaps:
        if gap >= n:
            continue
        for i in range(gap, n):
            j = i
            while j >= gap:
                yield "cmp", j - gap, j
                if a[j - gap] <= a[j]:
                    break
                a[j - gap], a[j] = a[j], a[j - gap]
                yield "swap", j - gap, j
                j -= gap


def comb(a: List[int]) -> SortGen:
    gap = len(a)
    done = False
    while not done:
        gap = max(1, int(gap / 1.3))
        done = gap == 1
        for i in range(len(a) - gap):
            yield "cmp", i, i + gap
            if a[i] > a[i + gap]:
                a[i], a[i + gap] = a[i + gap], a[i]
                yield "swap", i, i + gap
                done = False


def gnome(a: List[int]) -> SortGen:
    i = 0
    while i < len(a):
        if i == 0:
            i += 1
            continue
        yield "cmp", i - 1, i
        if a[i - 1] <= a[i]:
            i += 1
        else:
            a[i - 1], a[i] = a[i], a[i - 1]
            yield "swap", i - 1, i
            i -= 1


def odd_even(a: List[int]) -> SortGen:
    n = len(a)
    done = False
    while not done:
        done = True
        for start in (1, 0):
            for i in range(start, n - 1, 2):
                yield "cmp", i, i + 1
                if a[i] > a[i + 1]:
                    a[i], a[i + 1] = a[i + 1], a[i]
                    yield "swap", i, i + 1
                    done = False


def merge(a: List[int]) -> SortGen:
    def rec(lo: int, hi: int) -> SortGen:
        if hi - lo <= 1:
            return
        mid = (lo + hi) // 2
        yield from rec(lo, mid)
        yield from rec(mid, hi)
        left, right = a[lo:mid], a[mid:hi]
        i = j = 0
        k = lo
        while i < len(left) and j < len(right):
            yield "cmp", lo + i, mid + j
            if left[i] <= right[j]:
                a[k] = left[i]
                i += 1
            else:
                a[k] = right[j]
                j += 1
            yield "set", k, a[k]
            k += 1
        for v in left[i:] + right[j:]:
            a[k] = v
            yield "set", k, v
            k += 1

    yield from rec(0, len(a))


def merge_bottom_up(a: List[int]) -> SortGen:
    n = len(a)
    width = 1
    while width < n:
        for lo in range(0, n, 2 * width):
            mid, hi = min(lo + width, n), min(lo + 2 * width, n)
            left, right = a[lo:mid], a[mid:hi]
            i = j = 0
            k = lo
            while i < len(left) and j < len(right):
                yield "cmp", lo + i, mid + j
                if left[i] <= right[j]:
                    a[k] = left[i]
                    i += 1
                else:
                    a[k] = right[j]
                    j += 1
                yield "set", k, a[k]
                k += 1
            for v in left[i:] + right[j:]:
                a[k] = v
                yield "set", k, v
                k += 1
        width *= 2


def quick(a: List[int]) -> SortGen:
    """Tri rapide (Lomuto) avec médiane de trois et pile explicite."""
    stack = [(0, len(a) - 1)]
    while stack:
        lo, hi = stack.pop()
        if lo >= hi:
            if lo == hi:
                yield "done", lo, -1
            continue
        mid = (lo + hi) // 2
        # médiane de trois placée en a[hi]
        for i, j in ((lo, mid), (lo, hi), (mid, hi)):
            yield "cmp", i, j
            if a[i] > a[j]:
                a[i], a[j] = a[j], a[i]
                yield "swap", i, j
        a[mid], a[hi] = a[hi], a[mid]
        yield "swap", mid, hi
        pivot = a[hi]
        yield "pivot", hi, -1
        i = lo
        for j in range(lo, hi):
            yield "cmp", j, hi
            if a[j] < pivot:
                if i != j:
                    a[i], a[j] = a[j], a[i]
                    yield "swap", i, j
                i += 1
        a[i], a[hi] = a[hi], a[i]
        yield "swap", i, hi
        yield "done", i, -1
        stack.append((i + 1, hi))
        stack.append((lo, i - 1))


def heap(a: List[int]) -> SortGen:
    n = len(a)

    def sift(start: int, end: int) -> SortGen:
        root = start
        while 2 * root + 1 <= end:
            child = 2 * root + 1
            swap = root
            yield "cmp", swap, child
            if a[swap] < a[child]:
                swap = child
            if child + 1 <= end:
                yield "cmp", swap, child + 1
                if a[swap] < a[child + 1]:
                    swap = child + 1
            if swap == root:
                return
            a[root], a[swap] = a[swap], a[root]
            yield "swap", root, swap
            root = swap

    for start in range((n - 2) // 2, -1, -1):
        yield from sift(start, n - 1)
    for end in range(n - 1, 0, -1):
        a[0], a[end] = a[end], a[0]
        yield "swap", 0, end
        yield "done", end, -1
        yield from sift(0, end - 1)


def radix_lsd(a: List[int]) -> SortGen:
    if not a:
        return
    base = 4
    m = max(a)
    exp = 1
    while m // exp > 0:
        buckets: List[List[int]] = [[] for _ in range(base)]
        for i, v in enumerate(a):
            yield "pivot", i, -1
            buckets[(v // exp) % base].append(v)
        k = 0
        for b in buckets:
            for v in b:
                a[k] = v
                yield "set", k, v
                k += 1
        exp *= base


def cycle(a: List[int]) -> SortGen:
    n = len(a)
    for start in range(n - 1):
        item = a[start]
        pos = start
        for i in range(start + 1, n):
            yield "cmp", i, start
            if a[i] < item:
                pos += 1
        if pos == start:
            yield "done", start, -1
            continue
        while item == a[pos]:
            pos += 1
        a[pos], item = item, a[pos]
        yield "set", pos, a[pos]
        while pos != start:
            pos = start
            for i in range(start + 1, n):
                yield "cmp", i, start
                if a[i] < item:
                    pos += 1
            while item == a[pos]:
                pos += 1
            a[pos], item = item, a[pos]
            yield "set", pos, a[pos]
        yield "done", start, -1


def pancake(a: List[int]) -> SortGen:
    def flip(k: int) -> SortGen:
        i, j = 0, k
        while i < j:
            a[i], a[j] = a[j], a[i]
            yield "swap", i, j
            i += 1
            j -= 1

    for size in range(len(a), 1, -1):
        m = 0
        for i in range(1, size):
            yield "cmp", i, m
            if a[i] > a[m]:
                m = i
        if m != size - 1:
            if m:
                yield from flip(m)
            yield from flip(size - 1)
        yield "done", size - 1, -1


def timsort_simple(a: List[int]) -> SortGen:
    """TimSort simplifié : tri par insertion de « runs » de 16 puis fusions."""
    run = 16
    n = len(a)
    for lo in range(0, n, run):
        hi = min(lo + run, n)
        for i in range(lo + 1, hi):
            j = i
            while j > lo:
                yield "cmp", j - 1, j
                if a[j - 1] <= a[j]:
                    break
                a[j - 1], a[j] = a[j], a[j - 1]
                yield "swap", j - 1, j
                j -= 1
    width = run
    while width < n:
        for lo in range(0, n, 2 * width):
            mid, hi = min(lo + width, n), min(lo + 2 * width, n)
            if mid >= hi:
                continue
            yield "cmp", mid - 1, mid
            if a[mid - 1] <= a[mid]:
                continue  # runs déjà ordonnés : pas de fusion (galop simplifié)
            left, right = a[lo:mid], a[mid:hi]
            i = j = 0
            k = lo
            while i < len(left) and j < len(right):
                yield "cmp", lo + i, mid + j
                if left[i] <= right[j]:
                    a[k] = left[i]
                    i += 1
                else:
                    a[k] = right[j]
                    j += 1
                yield "set", k, a[k]
                k += 1
            for v in left[i:] + right[j:]:
                a[k] = v
                yield "set", k, v
                k += 1
        width *= 2


ALGORITHMS: Dict[str, Callable[[List[int]], SortGen]] = {
    "Tri rapide": quick,
    "Tri fusion": merge,
    "Fusion ascendante": merge_bottom_up,
    "TimSort simplifié": timsort_simple,
    "Tri par tas": heap,
    "Tri de Shell": shell,
    "Tri à peigne": comb,
    "Radix LSD (base 4)": radix_lsd,
    "Insertion": insertion,
    "Insertion dichotomique": binary_insertion,
    "Sélection": selection,
    "Bulles": bubble,
    "Cocktail": cocktail,
    "Pair-impair": odd_even,
    "Gnome": gnome,
    "Cycle": cycle,
    "Crêpes": pancake,
}

COMPLEXITY = {
    "Tri rapide": "O(n log n) moyen", "Tri fusion": "O(n log n)",
    "Fusion ascendante": "O(n log n)", "TimSort simplifié": "O(n log n)",
    "Tri par tas": "O(n log n)", "Tri de Shell": "≈ O(n^1.3)", "Tri à peigne": "≈ O(n log n)",
    "Radix LSD (base 4)": "O(n·k)", "Insertion": "O(n²)", "Insertion dichotomique": "O(n²)",
    "Sélection": "O(n²)", "Bulles": "O(n²)", "Cocktail": "O(n²)", "Pair-impair": "O(n²)",
    "Gnome": "O(n²)", "Cycle": "O(n²)", "Crêpes": "O(n²)",
}


def make_data(n: int, kind: str, seed: int = 0) -> List[int]:
    rng = random.Random(seed)
    values = list(range(1, n + 1))
    if kind == "Aléatoire":
        rng.shuffle(values)
    elif kind == "Presque trié":
        for _ in range(max(1, n // 20)):
            i, j = rng.randrange(n), rng.randrange(n)
            values[i], values[j] = values[j], values[i]
    elif kind == "Inversé":
        values.reverse()
    elif kind == "Peu de valeurs":
        values = [rng.choice([n // 5, 2 * n // 5, 3 * n // 5, 4 * n // 5, n]) for _ in range(n)]
    elif kind == "Montagne":
        half = values[::2] + values[1::2][::-1]
        values = half
    return values


DISTRIBUTIONS = ["Aléatoire", "Presque trié", "Inversé", "Peu de valeurs", "Montagne"]
