"""Réseau de neurones multicouche écrit de zéro (Python pur).

* Couches denses, activations tanh / ReLU / sigmoïde / sinus / linéaire.
* Sortie sigmoïde + entropie croisée binaire ; régularisation L2.
* Rétropropagation exacte, optimiseurs SGD avec moment et **Adam**.
* Jeux de données 2D (cercles, XOR, spirales, gaussiennes, lunes, damier)
  et caractéristiques dérivées (x², y², xy, sin x, sin y) façon « TensorFlow Playground ».
"""

from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Sample = Tuple[float, float, int]

ACTIVATIONS: Dict[str, Tuple[Callable[[float], float], Callable[[float, float], float]]] = {
    # nom : (f(z), f'(z) exprimée avec (z, a=f(z)))
    "tanh": (math.tanh, lambda z, a: 1.0 - a * a),
    "ReLU": (lambda z: z if z > 0 else 0.0, lambda z, a: 1.0 if z > 0 else 0.0),
    "Leaky ReLU": (lambda z: z if z > 0 else 0.01 * z, lambda z, a: 1.0 if z > 0 else 0.01),
    "sigmoïde": (lambda z: 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z)))),
                 lambda z, a: a * (1.0 - a)),
    "sinus": (math.sin, lambda z, a: math.cos(z)),
    "linéaire": (lambda z: z, lambda z, a: 1.0),
}

FEATURES: Dict[str, Callable[[float, float], float]] = {
    "x": lambda x, y: x,
    "y": lambda x, y: y,
    "x²": lambda x, y: x * x,
    "y²": lambda x, y: y * y,
    "x·y": lambda x, y: x * y,
    "sin x": lambda x, y: math.sin(x * 2),
    "sin y": lambda x, y: math.sin(y * 2),
}


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class MLP:
    def __init__(self, sizes: Sequence[int], activation: str = "tanh",
                 seed: Optional[int] = None) -> None:
        if len(sizes) < 2:
            raise ValueError("il faut au moins une couche d'entrée et une de sortie")
        self.sizes = list(sizes)
        self.activation = activation
        self.f, self.df = ACTIVATIONS[activation]
        rng = random.Random(seed)
        self.W: List[List[List[float]]] = []
        self.b: List[List[float]] = []
        for n_in, n_out in zip(sizes, sizes[1:]):
            # Initialisation de Xavier/He selon l'activation
            scale = math.sqrt((2.0 if "ReLU" in activation else 1.0) / n_in)
            self.W.append([[rng.gauss(0, scale) for _ in range(n_in)] for _ in range(n_out)])
            self.b.append([0.0] * n_out)
        self._init_optimizer()
        self.steps = 0

    def _init_optimizer(self) -> None:
        self.mW = [[[0.0] * len(r) for r in layer] for layer in self.W]
        self.vW = [[[0.0] * len(r) for r in layer] for layer in self.W]
        self.mb = [[0.0] * len(layer) for layer in self.b]
        self.vb = [[0.0] * len(layer) for layer in self.b]
        self.t = 0

    @property
    def parameter_count(self) -> int:
        return sum(len(l) * len(l[0]) for l in self.W) + sum(len(b) for b in self.b)

    # -- propagation ----------------------------------------------------------
    def forward(self, x: Sequence[float]) -> Tuple[List[List[float]], List[List[float]]]:
        """Renvoie (pré-activations, activations) de chaque couche ; la sortie est sigmoïde."""
        acts = [list(x)]
        zs: List[List[float]] = []
        last = len(self.W) - 1
        f = self.f
        for li, (W, b) in enumerate(zip(self.W, self.b)):
            a_prev = acts[-1]
            z = [sum(w * a for w, a in zip(row, a_prev)) + bias for row, bias in zip(W, b)]
            zs.append(z)
            acts.append([_sigmoid(v) for v in z] if li == last else [f(v) for v in z])
        return zs, acts

    def predict(self, x: Sequence[float]) -> float:
        a = list(x)
        last = len(self.W) - 1
        f = self.f
        for li, (W, b) in enumerate(zip(self.W, self.b)):
            z = [sum(w * v for w, v in zip(row, a)) + bias for row, bias in zip(W, b)]
            a = [_sigmoid(v) for v in z] if li == last else [f(v) for v in z]
        return a[0]

    def train_batch(self, X: Sequence[Sequence[float]], Y: Sequence[int], lr: float,
                    optimizer: str = "Adam", l2: float = 0.0, momentum: float = 0.9
                    ) -> float:
        """Une étape de descente de gradient sur un mini-lot ; renvoie la perte moyenne."""
        gW = [[[0.0] * len(r) for r in layer] for layer in self.W]
        gb = [[0.0] * len(layer) for layer in self.b]
        loss = 0.0
        n = len(X)
        df = self.df
        for x, y in zip(X, Y):
            zs, acts = self.forward(x)
            p = acts[-1][0]
            p = min(1 - 1e-12, max(1e-12, p))
            loss -= y * math.log(p) + (1 - y) * math.log(1 - p)
            delta = [acts[-1][0] - y]  # sigmoïde + entropie croisée : gradient simplifié
            for li in range(len(self.W) - 1, -1, -1):
                a_prev = acts[li]
                gWl, gbl = gW[li], gb[li]
                for j, d in enumerate(delta):
                    row = gWl[j]
                    for k, a in enumerate(a_prev):
                        row[k] += d * a
                    gbl[j] += d
                if li:
                    W = self.W[li]
                    z_prev, a_prev_act = zs[li - 1], acts[li]
                    delta = [sum(W[j][k] * delta[j] for j in range(len(delta)))
                             * df(z_prev[k], a_prev_act[k]) for k in range(len(a_prev))]
        inv = 1.0 / max(1, n)
        if l2:
            loss += 0.5 * l2 * sum(w * w for layer in self.W for row in layer for w in row)
        self._apply(gW, gb, inv, lr, optimizer, l2, momentum)
        self.steps += 1
        return loss * inv

    def _apply(self, gW, gb, inv: float, lr: float, optimizer: str, l2: float,
               momentum: float) -> None:
        if optimizer == "Adam":
            self.t += 1
            b1, b2, eps = 0.9, 0.999, 1e-8
            c1 = 1 - b1 ** self.t
            c2 = 1 - b2 ** self.t
            for li, layer in enumerate(self.W):
                for j, row in enumerate(layer):
                    mrow, vrow, grow = self.mW[li][j], self.vW[li][j], gW[li][j]
                    for k in range(len(row)):
                        g = grow[k] * inv + l2 * row[k]
                        mrow[k] = b1 * mrow[k] + (1 - b1) * g
                        vrow[k] = b2 * vrow[k] + (1 - b2) * g * g
                        row[k] -= lr * (mrow[k] / c1) / (math.sqrt(vrow[k] / c2) + eps)
                    g = gb[li][j] * inv
                    self.mb[li][j] = b1 * self.mb[li][j] + (1 - b1) * g
                    self.vb[li][j] = b2 * self.vb[li][j] + (1 - b2) * g * g
                    self.b[li][j] -= lr * (self.mb[li][j] / c1) / (math.sqrt(self.vb[li][j] / c2) + eps)
        else:  # SGD avec moment
            for li, layer in enumerate(self.W):
                for j, row in enumerate(layer):
                    mrow, grow = self.mW[li][j], gW[li][j]
                    for k in range(len(row)):
                        mrow[k] = momentum * mrow[k] - lr * (grow[k] * inv + l2 * row[k])
                        row[k] += mrow[k]
                    self.mb[li][j] = momentum * self.mb[li][j] - lr * gb[li][j] * inv
                    self.b[li][j] += self.mb[li][j]

    def loss(self, X: Sequence[Sequence[float]], Y: Sequence[int]) -> float:
        total = 0.0
        for x, y in zip(X, Y):
            p = min(1 - 1e-12, max(1e-12, self.predict(x)))
            total -= y * math.log(p) + (1 - y) * math.log(1 - p)
        return total / max(1, len(X))

    def accuracy(self, X: Sequence[Sequence[float]], Y: Sequence[int]) -> float:
        if not X:
            return 0.0
        return sum(1 for x, y in zip(X, Y) if (self.predict(x) >= 0.5) == (y == 1)) / len(X)

    def numeric_gradient_check(self, x: Sequence[float], y: int, eps: float = 1e-5) -> float:
        """Écart relatif max entre gradient analytique et différences finies (test)."""
        gW = [[[0.0] * len(r) for r in layer] for layer in self.W]
        zs, acts = self.forward(x)
        delta = [acts[-1][0] - y]
        for li in range(len(self.W) - 1, -1, -1):
            for j, d in enumerate(delta):
                for k, a in enumerate(acts[li]):
                    gW[li][j][k] += d * a
            if li:
                W = self.W[li]
                delta = [sum(W[j][k] * delta[j] for j in range(len(delta)))
                         * self.df(zs[li - 1][k], acts[li][k]) for k in range(len(acts[li]))]
        worst = 0.0
        for li, layer in enumerate(self.W):
            for j, row in enumerate(layer):
                for k in range(len(row)):
                    old = row[k]
                    row[k] = old + eps
                    lp = self.loss([x], [y])
                    row[k] = old - eps
                    lm = self.loss([x], [y])
                    row[k] = old
                    num = (lp - lm) / (2 * eps)
                    ana = gW[li][j][k]
                    denom = max(1e-8, abs(num) + abs(ana))
                    worst = max(worst, abs(num - ana) / denom)
        return worst


# -- jeux de données ---------------------------------------------------------------

def make_dataset(kind: str, n: int = 240, noise: float = 0.1,
                 seed: Optional[int] = None) -> List[Sample]:
    rng = random.Random(seed)
    data: List[Sample] = []

    def jitter() -> float:
        return rng.gauss(0, noise)

    for i in range(n):
        if kind == "Cercles":
            label = i % 2
            r = rng.uniform(0, 0.45) if label else rng.uniform(0.65, 0.95)
            a = rng.uniform(0, math.tau)
            data.append((r * math.cos(a) + jitter(), r * math.sin(a) + jitter(), label))
        elif kind == "XOR":
            x, y = rng.uniform(-1, 1), rng.uniform(-1, 1)
            pad = 0.06
            x += pad if x > 0 else -pad
            y += pad if y > 0 else -pad
            data.append((x + jitter(), y + jitter(), 1 if x * y > 0 else 0))
        elif kind == "Spirales":
            label = i % 2
            t = (i // 2) / (n / 2) * 2.6 * math.pi
            r = t / (2.8 * math.pi)
            a = t + label * math.pi
            data.append((r * math.cos(a) + jitter() * 0.6, r * math.sin(a) + jitter() * 0.6, label))
        elif kind == "Gaussiennes":
            label = i % 2
            c = 0.45 if label else -0.45
            data.append((c + rng.gauss(0, 0.22 + noise), c + rng.gauss(0, 0.22 + noise), label))
        elif kind == "Lunes":
            label = i % 2
            a = rng.uniform(0, math.pi)
            if label:
                x, y = math.cos(a) * 0.6 - 0.3, math.sin(a) * 0.6 - 0.15
            else:
                x, y = 0.3 - math.cos(a) * 0.6, 0.15 - math.sin(a) * 0.6
            data.append((x + jitter(), y + jitter(), label))
        else:  # Damier
            x, y = rng.uniform(-1, 1), rng.uniform(-1, 1)
            label = (int((x + 1) * 2) + int((y + 1) * 2)) % 2
            data.append((x + jitter() * 0.3, y + jitter() * 0.3, label))
    return data


DATASETS = ["Cercles", "XOR", "Spirales", "Gaussiennes", "Lunes", "Damier"]


def featurize(x: float, y: float, features: Sequence[str]) -> List[float]:
    return [FEATURES[f](x, y) for f in features]


def split(data: List[Sample], ratio: float = 0.75, seed: int = 1
          ) -> Tuple[List[Sample], List[Sample]]:
    rng = random.Random(seed)
    items = list(data)
    rng.shuffle(items)
    k = int(len(items) * ratio)
    return items[:k], items[k:]
