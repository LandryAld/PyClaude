"""Systèmes de Lindenmayer : réécriture (déterministe ou stochastique) et tortue graphique.

Symboles interprétés : ``F``/``G`` avancer en traçant, ``f`` avancer sans tracer,
``+``/``-`` tourner, ``|`` demi-tour, ``[``/``]`` empiler/dépiler l'état,
``!`` amincir le trait, ``>``/``<`` changer la longueur. Les autres lettres
(X, Y…) ne servent qu'à la réécriture.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

Rule = Union[str, List[Tuple[float, str]]]  # déterministe ou liste (probabilité, remplacement)
Segment = Tuple[float, float, float, float, int, float]  # x0, y0, x1, y1, profondeur, largeur


@dataclass
class LSystem:
    name: str
    axiom: str
    rules: Dict[str, Rule]
    angle: float
    iterations: int
    start_angle: float = -90.0  # vers le haut
    max_iterations: int = 10
    length_factor: float = 0.62
    description: str = ""

    def expand(self, iterations: Optional[int] = None, seed: Optional[int] = None,
               limit: int = 600_000) -> str:
        rng = random.Random(seed)
        s = self.axiom
        for _ in range(self.iterations if iterations is None else iterations):
            out = []
            size = 0
            for ch in s:
                rule = self.rules.get(ch)
                if rule is None:
                    rep = ch
                elif isinstance(rule, str):
                    rep = rule
                else:
                    r = rng.random()
                    acc = 0.0
                    rep = rule[-1][1]
                    for p, candidate in rule:
                        acc += p
                        if r <= acc:
                            rep = candidate
                            break
                out.append(rep)
                size += len(rep)
                if size > limit:
                    raise ValueError(f"chaîne trop longue (> {limit} symboles) : réduisez les "
                                     "itérations")
            s = "".join(out)
        return s

    def interpret(self, s: str, step: float = 10.0, angle: Optional[float] = None,
                  jitter: float = 0.0, seed: Optional[int] = None) -> List[Segment]:
        rng = random.Random(seed)
        delta = math.radians(self.angle if angle is None else angle)
        x = y = 0.0
        heading = math.radians(self.start_angle)
        width = 1.0
        length = step
        depth = 0
        stack: List[Tuple[float, float, float, float, float, int]] = []
        segs: List[Segment] = []
        for ch in s:
            if ch in "FG":
                nx, ny = x + length * math.cos(heading), y + length * math.sin(heading)
                segs.append((x, y, nx, ny, depth, width))
                x, y = nx, ny
            elif ch == "f":
                x, y = x + length * math.cos(heading), y + length * math.sin(heading)
            elif ch == "+":
                heading += delta + (rng.gauss(0, jitter) if jitter else 0.0)
            elif ch == "-":
                heading -= delta + (rng.gauss(0, jitter) if jitter else 0.0)
            elif ch == "|":
                heading += math.pi
            elif ch == "[":
                stack.append((x, y, heading, width, length, depth))
                depth += 1
            elif ch == "]":
                if stack:
                    x, y, heading, width, length, depth = stack.pop()
            elif ch == "!":
                width *= 0.7
            elif ch == ">":
                length *= self.length_factor
            elif ch == "<":
                length /= self.length_factor
        return segs


PRESETS: List[LSystem] = [
    LSystem("Plante fractale", "X", {"X": "F+[[X]-X]-F[-FX]+X", "F": "FF"}, 25, 6,
            description="Plante de Barnsley (Prusinkiewicz & Lindenmayer)."),
    LSystem("Arbre stochastique", "F",
            {"F": [(0.34, "F[+F]F[-F]F"), (0.33, "F[+F]F"), (0.33, "F[-F]F")]}, 25.7, 5,
            description="Chaque F choisit aléatoirement sa règle : chaque graine donne un arbre."),
    LSystem("Buisson", "F", {"F": "FF+[+F-F-F]-[-F+F+F]"}, 22.5, 4, max_iterations=5),
    LSystem("Flocon de Koch", "F--F--F", {"F": "F+F--F+F"}, 60, 4, start_angle=0,
            max_iterations=7),
    LSystem("Triangle de Sierpiński", "F-G-G", {"F": "F-G+F+G-F", "G": "GG"}, 120, 6,
            start_angle=0, max_iterations=8),
    LSystem("Courbe du dragon", "FX", {"X": "X+YF+", "Y": "-FX-Y"}, 90, 12, start_angle=0,
            max_iterations=16),
    LSystem("Courbe de Hilbert", "A", {"A": "+BF-AFA-FB+", "B": "-AF+BFB+FA-"}, 90, 6,
            start_angle=0, max_iterations=8),
    LSystem("Courbe de Gosper", "A", {"A": "A-B--B+A++AA+B-", "B": "+A-BB--B-A++A+B"}, 60, 4,
            start_angle=0, max_iterations=5),
    LSystem("Courbe C de Lévy", "F", {"F": "+F--F+"}, 45, 12, start_angle=0,
            max_iterations=16),
    LSystem("Île de Koch quadratique", "F+F+F+F", {"F": "F+F-F-FF+F+F-F"}, 90, 3,
            start_angle=0, max_iterations=4),
    LSystem("Algue", "F", {"F": "F[+F]F[-F][F]"}, 20, 5, max_iterations=6),
]

def normalize_drawing(s: str, system: LSystem) -> str:
    """Pour les systèmes où A/B tracent (Gosper), on les convertit en F."""
    if system.name == "Courbe de Gosper":
        return s.replace("A", "F").replace("B", "F")
    return s


def bounds(segs: Sequence[Segment]) -> Tuple[float, float, float, float]:
    if not segs:
        return 0.0, 0.0, 1.0, 1.0
    xs = [s[0] for s in segs] + [s[2] for s in segs]
    ys = [s[1] for s in segs] + [s[3] for s in segs]
    return min(xs), min(ys), max(xs), max(ys)
