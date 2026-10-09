"""Automates cellulaires « Life-like » et « Generations » sur grille creuse.

* Règles en notation B/S (``B3/S23``) et Generations (``B2/S/C3`` : cerveau de Brian).
* Grille torique ou bornée ; seules les cellules vivantes sont stockées.
* Lecture/écriture du format RLE standard (motifs de la communauté Life).
* Âge des cellules (coloration thermique) et historique de population.
"""

from __future__ import annotations

import random
import re
from collections import Counter, deque
from dataclasses import dataclass
from typing import Deque, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

Cell = Tuple[int, int]
_OFFSETS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


@dataclass(frozen=True)
class Rule:
    birth: FrozenSet[int]
    survive: FrozenSet[int]
    states: int = 2  # > 2 : règle « Generations » (cellules mourantes)
    name: str = ""

    @classmethod
    def parse(cls, text: str, name: str = "") -> "Rule":
        t = text.strip().upper().replace(" ", "")
        m = re.fullmatch(r"B(\d*)/S(\d*)(?:/C?(\d+))?", t)
        if not m:
            m2 = re.fullmatch(r"(\d*)/(\d*)(?:/(\d+))?", t)  # notation S/B historique
            if not m2:
                raise ValueError(f"règle invalide : {text!r} (exemple : B3/S23)")
            survive, birth, states = m2.group(1), m2.group(2), m2.group(3)
        else:
            birth, survive, states = m.group(1), m.group(2), m.group(3)
        n = int(states) if states else 2
        if n < 2:
            raise ValueError("au moins deux états sont nécessaires")
        return cls(frozenset(int(c) for c in birth), frozenset(int(c) for c in survive), n,
                   name or t)

    def notation(self) -> str:
        b = "".join(str(i) for i in sorted(self.birth))
        s = "".join(str(i) for i in sorted(self.survive))
        return f"B{b}/S{s}" + (f"/C{self.states}" if self.states > 2 else "")


RULES: Dict[str, str] = {
    "Jeu de la vie (Conway)": "B3/S23",
    "HighLife (réplicateurs)": "B36/S23",
    "Day & Night": "B3678/S34678",
    "Seeds (explosif)": "B2/S",
    "Vie sans mort": "B3/S012345678",
    "Labyrinthe": "B3/S12345",
    "Réplicateur": "B1357/S1357",
    "Diamoeba": "B35678/S5678",
    "2x2": "B36/S125",
    "Cerveau de Brian": "B2/S/C3",
    "Star Wars": "B2/S345/C4",
    "Feux de forêt": "B2/S2345/C8",
}

PATTERNS: Dict[str, str] = {
    "Planeur": "bo$2bo$3o!",
    "Vaisseau léger (LWSS)": "bo2bo$o4b$o3bo$4o!",
    "Pulsar": "2b3o3b3o2b2$o4bobo4bo$o4bobo4bo$o4bobo4bo$2b3o3b3o2b2$2b3o3b3o2b$o4bobo4bo$"
              "o4bobo4bo$o4bobo4bo2$2b3o3b3o!",
    "Pentadécathlon": "2bo4bo2b$2ob4ob2o$2bo4bo!",
    "Canon à planeurs de Gosper": "24bo$22bobo$12b2o6b2o12b2o$11bo3bo4b2o12b2o$2o8bo5bo3b2o$"
                                  "2o8bo3bob2o4bobo$10bo5bo7bo$11bo3bo$12b2o!",
    "R-pentomino": "b2o$2o$bo!",
    "Gland (acorn)": "bo5b$3bo3b$2o2b3o!",
    "Diehard": "6bob$2o6b$bo3b3o!",
    "Clignotant": "3o!",
    "Galaxie de Kok": "6ob2o$6ob2o$7b2o$2o5b2o$2o5b2o$2o5b2o$2o7b$2ob6o$2ob6o!",
    "Moteur de croissance infinie": "6bob$4bob2o$4bobob$4bo3b$2bo5b$obo!",
}


def parse_rle(text: str) -> Tuple[Set[Cell], Optional[str]]:
    """Analyse un motif RLE ; renvoie (cellules, règle éventuelle)."""
    rule = None
    body_lines = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("x"):
            m = re.search(r"rule\s*=\s*([^,\s]+)", line, re.I)
            if m:
                rule = m.group(1)
            continue
        body_lines.append(line)
    body = "".join(body_lines)
    cells: Set[Cell] = set()
    x = y = 0
    count = ""
    for ch in body:
        if ch.isdigit():
            count += ch
            continue
        n = int(count) if count else 1
        count = ""
        if ch == "b" or ch == ".":
            x += n
        elif ch == "o" or ch.isalpha():
            for i in range(n):
                cells.add((x + i, y))
            x += n
        elif ch == "$":
            y += n
            x = 0
        elif ch == "!":
            break
    return cells, rule


def to_rle(cells: Iterable[Cell], rule: str = "B3/S23") -> str:
    cells = set(cells)
    if not cells:
        return f"x = 0, y = 0, rule = {rule}\n!"
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    rows = []
    for y in range(y0, y1 + 1):
        runs: List[Tuple[int, str]] = []
        for x in range(x0, x1 + 1):
            ch = "o" if (x, y) in cells else "b"
            if runs and runs[-1][1] == ch:
                runs[-1] = (runs[-1][0] + 1, ch)
            else:
                runs.append((1, ch))
        if runs and runs[-1][1] == "b":
            runs.pop()
        rows.append("".join((str(n) if n > 1 else "") + ch for n, ch in runs))
    # fusion des lignes vides consécutives ($$ -> 2$)
    body = ""
    empty = 0
    for i, r in enumerate(rows):
        if i:
            if r == "":
                empty += 1
                continue
            body += (str(empty + 1) if empty else "") + "$"
            empty = 0
        body += r
    body += "!"
    lines = [body[i:i + 70] for i in range(0, len(body), 70)]
    return f"x = {x1 - x0 + 1}, y = {y1 - y0 + 1}, rule = {rule}\n" + "\n".join(lines)


class LifeGrid:
    def __init__(self, width: int = 160, height: int = 100, rule: str = "B3/S23",
                 wrap: bool = True) -> None:
        self.width = width
        self.height = height
        self.wrap = wrap
        self.rule = Rule.parse(rule)
        self.alive: Set[Cell] = set()
        self.dying: Dict[Cell, int] = {}  # règles Generations : état 2..C-1
        self.age: Dict[Cell, int] = {}
        self.generation = 0
        self.history: Deque[int] = deque(maxlen=400)
        self.births = 0
        self.deaths = 0

    # -- édition ---------------------------------------------------------
    def set_rule(self, rule: str) -> None:
        self.rule = Rule.parse(rule)
        if self.rule.states == 2:
            self.dying.clear()

    def _norm(self, x: int, y: int) -> Optional[Cell]:
        if self.wrap:
            return x % self.width, y % self.height
        if 0 <= x < self.width and 0 <= y < self.height:
            return x, y
        return None

    def set(self, x: int, y: int, alive: bool = True) -> None:
        c = self._norm(x, y)
        if c is None:
            return
        self.dying.pop(c, None)
        if alive:
            if c not in self.alive:
                self.alive.add(c)
                self.age[c] = 0
        else:
            self.alive.discard(c)
            self.age.pop(c, None)

    def toggle(self, x: int, y: int) -> bool:
        c = self._norm(x, y)
        if c is None:
            return False
        self.set(c[0], c[1], c not in self.alive)
        return c in self.alive

    def clear(self) -> None:
        self.alive.clear()
        self.dying.clear()
        self.age.clear()
        self.generation = 0
        self.history.clear()

    def randomize(self, density: float = 0.25, seed: Optional[int] = None) -> None:
        rng = random.Random(seed)
        self.clear()
        for y in range(self.height):
            for x in range(self.width):
                if rng.random() < density:
                    self.alive.add((x, y))
                    self.age[(x, y)] = 0
        self.history.append(len(self.alive))

    def place(self, cells: Iterable[Cell], ox: int, oy: int, center: bool = True) -> None:
        cells = list(cells)
        if center and cells:
            w = max(c[0] for c in cells) + 1
            h = max(c[1] for c in cells) + 1
            ox -= w // 2
            oy -= h // 2
        for x, y in cells:
            self.set(ox + x, oy + y, True)

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.alive = {c for c in self.alive if c[0] < width and c[1] < height}
        self.dying = {c: s for c, s in self.dying.items() if c[0] < width and c[1] < height}
        self.age = {c: a for c, a in self.age.items() if c in self.alive}

    # -- évolution -------------------------------------------------------
    def step(self) -> None:
        counts: Counter = Counter()
        w, h, wrap = self.width, self.height, self.wrap
        for (x, y) in self.alive:
            for dx, dy in _OFFSETS:
                nx, ny = x + dx, y + dy
                if wrap:
                    counts[(nx % w, ny % h)] += 1
                elif 0 <= nx < w and 0 <= ny < h:
                    counts[(nx, ny)] += 1
        rule = self.rule
        new_alive: Set[Cell] = set()
        births = 0
        for cell, n in counts.items():
            if cell in self.alive:
                if n in rule.survive:
                    new_alive.add(cell)
            elif n in rule.birth and cell not in self.dying:
                new_alive.add(cell)
                births += 1
        for cell in self.alive:
            if cell not in new_alive and counts.get(cell, 0) in rule.survive:
                new_alive.add(cell)  # n = 0 (S0) : cellule isolée qui survit
        died = self.alive - new_alive
        if rule.states > 2:
            new_dying: Dict[Cell, int] = {}
            for cell, state in self.dying.items():
                if state + 1 < rule.states:
                    new_dying[cell] = state + 1
            for cell in died:
                if rule.states > 2:
                    new_dying[cell] = 2 if rule.states > 2 else 0
            self.dying = {c: s for c, s in new_dying.items() if s < rule.states}
        self.age = {c: (self.age.get(c, -1) + 1) for c in new_alive}
        self.births = births
        self.deaths = len(died)
        self.alive = new_alive
        self.generation += 1
        self.history.append(len(new_alive))

    def run(self, n: int) -> None:
        for _ in range(n):
            self.step()

    @property
    def population(self) -> int:
        return len(self.alive)

    def bounding_box(self) -> Optional[Tuple[int, int, int, int]]:
        if not self.alive:
            return None
        xs = [c[0] for c in self.alive]
        ys = [c[1] for c in self.alive]
        return min(xs), min(ys), max(xs), max(ys)

    def normalized(self) -> FrozenSet[Cell]:
        """Forme canonique translatée à l'origine (comparaison de motifs)."""
        bb = self.bounding_box()
        if bb is None:
            return frozenset()
        return frozenset((x - bb[0], y - bb[1]) for x, y in self.alive)

    def render_index(self, max_age: int = 40) -> bytes:
        """Image indexée (1 octet/cellule) : 0 = vide, 1..max_age+1 = âge, 200+ = mourante."""
        buf = bytearray(self.width * self.height)
        w = self.width
        for (x, y), a in self.age.items():
            buf[y * w + x] = 1 + min(a, max_age)
        for (x, y), s in self.dying.items():
            buf[y * w + x] = 200 + min(s, 50)
        return bytes(buf)
