"""Moteurs physiques 2D.

* :class:`ClothWorld` — intégration de Verlet, contraintes de distance relaxées
  (Gauss-Seidel), déchirure au-delà d'un seuil d'étirement, vent turbulent.
* :class:`GravityWorld` — problème à N corps par l'algorithme de **Barnes-Hut**
  (arbre quaternaire, critère d'ouverture θ), intégrateur saute-mouton
  symplectique, fusion inélastique des corps qui se touchent.
* :class:`BallWorld` — billes rigides, détection large par **hachage spatial**,
  résolution par impulsions avec coefficient de restitution.
"""

from __future__ import annotations

import math
import random
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Tissu (Verlet)
# ---------------------------------------------------------------------------

class Point:
    __slots__ = ("x", "y", "px", "py", "pinned", "pin_x", "pin_y")

    def __init__(self, x: float, y: float, pinned: bool = False) -> None:
        self.x = self.px = self.pin_x = x
        self.y = self.py = self.pin_y = y
        self.pinned = pinned


class Stick:
    __slots__ = ("a", "b", "length", "broken", "tension")

    def __init__(self, a: Point, b: Point, length: Optional[float] = None) -> None:
        self.a, self.b = a, b
        self.length = length if length is not None else math.hypot(a.x - b.x, a.y - b.y)
        self.broken = False
        self.tension = 0.0


class ClothWorld:
    def __init__(self, width: float = 900, height: float = 600) -> None:
        self.width, self.height = width, height
        self.points: List[Point] = []
        self.sticks: List[Stick] = []
        self.gravity = 900.0
        self.damping = 0.995
        self.iterations = 5
        self.tear = 3.2          # facteur d'étirement provoquant la rupture (0 = jamais)
        self.wind = 0.0
        self.time = 0.0
        self.grabbed: Optional[Point] = None
        self.grab_target = (0.0, 0.0)

    def make_cloth(self, cols: int = 38, rows: int = 24, spacing: float = 16.0,
                   pin_every: int = 4) -> None:
        self.points.clear()
        self.sticks.clear()
        ox = (self.width - (cols - 1) * spacing) / 2
        oy = 40.0
        grid: List[List[Point]] = []
        for r in range(rows):
            row = []
            for c in range(cols):
                pinned = r == 0 and (c % pin_every == 0 or c == cols - 1)
                p = Point(ox + c * spacing, oy + r * spacing, pinned)
                row.append(p)
                self.points.append(p)
                if c:
                    self.sticks.append(Stick(row[c - 1], p))
                if r:
                    self.sticks.append(Stick(grid[r - 1][c], p))
            grid.append(row)

    def make_curtain(self) -> None:
        self.make_cloth(cols=46, rows=30, spacing=13.0, pin_every=1)

    def make_ropes(self, count: int = 7, segments: int = 22) -> None:
        self.points.clear()
        self.sticks.clear()
        for i in range(count):
            x = self.width * (i + 1) / (count + 1)
            prev = Point(x, 30, pinned=True)
            self.points.append(prev)
            for s in range(1, segments):
                p = Point(x + s * 3 * math.sin(i), 30 + s * 14)
                self.points.append(p)
                self.sticks.append(Stick(prev, p))
                prev = p

    def step(self, dt: float) -> None:
        dt = min(dt, 1 / 30)
        self.time += dt
        g = self.gravity * dt * dt
        gust = self.wind * (0.6 + 0.4 * math.sin(self.time * 1.7) * math.sin(self.time * 0.63))
        w = gust * dt * dt
        damping = self.damping
        for p in self.points:
            if p.pinned:
                p.x, p.y = p.pin_x, p.pin_y
                continue
            vx = (p.x - p.px) * damping
            vy = (p.y - p.py) * damping
            p.px, p.py = p.x, p.y
            p.x += vx + w * (1.0 + 0.3 * math.sin(p.y * 0.05 + self.time * 3))
            p.y += vy + g
        if self.grabbed is not None:
            gx, gy = self.grab_target
            self.grabbed.x += (gx - self.grabbed.x) * 0.6
            self.grabbed.y += (gy - self.grabbed.y) * 0.6
        tear = self.tear
        for _ in range(self.iterations):
            for s in self.sticks:
                if s.broken:
                    continue
                a, b = s.a, s.b
                dx, dy = b.x - a.x, b.y - a.y
                dist = math.sqrt(dx * dx + dy * dy) or 1e-9
                if tear and dist > s.length * tear:
                    s.broken = True
                    continue
                diff = (dist - s.length) / dist * 0.5
                s.tension = dist / s.length - 1.0
                ox, oy = dx * diff, dy * diff
                if not a.pinned:
                    a.x += ox
                    a.y += oy
                if not b.pinned:
                    b.x -= ox
                    b.y -= oy
            self._bounds()
        self.sticks = [s for s in self.sticks if not s.broken]

    def _bounds(self) -> None:
        w, h = self.width, self.height
        for p in self.points:
            if p.x < 0:
                p.x = 0
            elif p.x > w:
                p.x = w
            if p.y > h:
                p.y = h
                p.px = p.x - (p.x - p.px) * 0.5  # frottement au sol
            elif p.y < 0:
                p.y = 0

    def nearest(self, x: float, y: float, radius: float = 30) -> Optional[Point]:
        best, best_d = None, radius * radius
        for p in self.points:
            d = (p.x - x) ** 2 + (p.y - y) ** 2
            if d < best_d:
                best, best_d = p, d
        return best

    def cut(self, x: float, y: float, radius: float = 10) -> int:
        """Coupe les liens passant à moins de ``radius`` du point (distance point-segment)."""
        n = 0
        r2 = radius * radius
        for s in self.sticks:
            ax, ay, bx, by = s.a.x, s.a.y, s.b.x, s.b.y
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy or 1e-9
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
            cx, cy = ax + t * dx, ay + t * dy
            if (x - cx) ** 2 + (y - cy) ** 2 < r2:
                s.broken = True
                n += 1
        self.sticks = [s for s in self.sticks if not s.broken]
        return n


# ---------------------------------------------------------------------------
# Gravitation (Barnes-Hut)
# ---------------------------------------------------------------------------

class Body:
    __slots__ = ("x", "y", "vx", "vy", "ax", "ay", "m", "r", "trail", "alive", "hue")

    def __init__(self, x: float, y: float, vx: float, vy: float, m: float,
                 hue: float = 0.0) -> None:
        self.x, self.y, self.vx, self.vy, self.m = x, y, vx, vy, m
        self.ax = self.ay = 0.0
        self.r = max(1.5, m ** (1 / 3) * 0.9)
        self.trail: Deque[Tuple[float, float]] = deque(maxlen=24)
        self.alive = True
        self.hue = hue


class QuadNode:
    __slots__ = ("cx", "cy", "half", "mass", "mx", "my", "body", "children")

    def __init__(self, cx: float, cy: float, half: float) -> None:
        self.cx, self.cy, self.half = cx, cy, half
        self.mass = 0.0
        self.mx = self.my = 0.0
        self.body: Optional[Body] = None
        self.children: Optional[List["QuadNode"]] = None

    def _child_for(self, x: float, y: float) -> "QuadNode":
        assert self.children is not None
        return self.children[(1 if x >= self.cx else 0) + (2 if y >= self.cy else 0)]

    def insert(self, b: Body, depth: int = 0) -> None:
        # Centre de masse incrémental
        total = self.mass + b.m
        self.mx = (self.mx * self.mass + b.x * b.m) / total
        self.my = (self.my * self.mass + b.y * b.m) / total
        self.mass = total
        if self.children is None:
            if self.body is None:
                self.body = b
                return
            if depth > 40:  # corps quasi confondus : on agrège
                return
            h = self.half / 2
            self.children = [QuadNode(self.cx - h, self.cy - h, h), QuadNode(self.cx + h, self.cy - h, h),
                             QuadNode(self.cx - h, self.cy + h, h), QuadNode(self.cx + h, self.cy + h, h)]
            old = self.body
            self.body = None
            self._child_for(old.x, old.y).insert(old, depth + 1)
        self._child_for(b.x, b.y).insert(b, depth + 1)

    def count(self) -> int:
        if self.children is None:
            return 1
        return 1 + sum(c.count() for c in self.children)


def build_tree(bodies: List[Body]) -> Optional[QuadNode]:
    if not bodies:
        return None
    xs = [b.x for b in bodies]
    ys = [b.y for b in bodies]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 1.0
    root = QuadNode(cx, cy, half)
    for b in bodies:
        root.insert(b)
    return root


def tree_acceleration(root: QuadNode, b: Body, theta: float, G: float, soft2: float
                      ) -> Tuple[float, float, int]:
    """Accélération sur ``b`` ; renvoie aussi le nombre d'interactions calculées."""
    ax = ay = 0.0
    interactions = 0
    stack = [root]
    theta2 = theta * theta
    while stack:
        node = stack.pop()
        if node.mass == 0 or node.body is b:
            continue
        dx, dy = node.mx - b.x, node.my - b.y
        d2 = dx * dx + dy * dy
        size = node.half * 2
        if node.children is None or size * size < theta2 * d2:
            inv = G * node.mass / ((d2 + soft2) * math.sqrt(d2 + soft2))
            ax += dx * inv
            ay += dy * inv
            interactions += 1
        else:
            stack.extend(node.children)
    return ax, ay, interactions


def direct_acceleration(bodies: List[Body], b: Body, G: float, soft2: float) -> Tuple[float, float]:
    ax = ay = 0.0
    for o in bodies:
        if o is b:
            continue
        dx, dy = o.x - b.x, o.y - b.y
        d2 = dx * dx + dy * dy + soft2
        inv = G * o.m / (d2 * math.sqrt(d2))
        ax += dx * inv
        ay += dy * inv
    return ax, ay


class GravityWorld:
    def __init__(self) -> None:
        self.bodies: List[Body] = []
        self.G = 60.0
        self.theta = 0.6
        self.softening = 4.0
        self.merge = True
        self.use_tree = True
        self.tree: Optional[QuadNode] = None
        self.interactions = 0
        self.time = 0.0

    def _accelerations(self) -> None:
        soft2 = self.softening ** 2
        self.interactions = 0
        if self.use_tree:
            self.tree = build_tree(self.bodies)
            if self.tree is None:
                return
            for b in self.bodies:
                b.ax, b.ay, n = tree_acceleration(self.tree, b, self.theta, self.G, soft2)
                self.interactions += n
        else:
            self.tree = None
            for b in self.bodies:
                b.ax, b.ay = direct_acceleration(self.bodies, b, self.G, soft2)
            n = len(self.bodies)
            self.interactions = n * (n - 1)

    def step(self, dt: float) -> None:
        """Saute-mouton « kick-drift-kick » : conservation d'énergie à long terme."""
        if not self.bodies:
            return
        if self.time == 0.0:
            self._accelerations()
        half = dt / 2
        for b in self.bodies:
            b.vx += b.ax * half
            b.vy += b.ay * half
            b.x += b.vx * dt
            b.y += b.vy * dt
        if self.merge:
            self._merge()
        self._accelerations()
        for b in self.bodies:
            b.vx += b.ax * half
            b.vy += b.ay * half
            b.trail.append((b.x, b.y))
        self.time += dt

    def _merge(self) -> None:
        """Fusion inélastique (conservation de la masse et de la quantité de mouvement)."""
        cell = 24.0
        grid: Dict[Tuple[int, int], List[Body]] = {}
        for b in self.bodies:
            grid.setdefault((int(b.x // cell), int(b.y // cell)), []).append(b)
        for b in self.bodies:
            if not b.alive:
                continue
            gx, gy = int(b.x // cell), int(b.y // cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for o in grid.get((gx + dx, gy + dy), ()):
                        if o is b or not o.alive:
                            continue
                        rr = (b.r + o.r) * 0.6
                        if (o.x - b.x) ** 2 + (o.y - b.y) ** 2 < rr * rr:
                            big, small = (b, o) if b.m >= o.m else (o, b)
                            m = big.m + small.m
                            big.vx = (big.vx * big.m + small.vx * small.m) / m
                            big.vy = (big.vy * big.m + small.vy * small.m) / m
                            big.x = (big.x * big.m + small.x * small.m) / m
                            big.y = (big.y * big.m + small.y * small.m) / m
                            big.m = m
                            big.r = max(1.5, m ** (1 / 3) * 0.9)
                            small.alive = False
        if any(not b.alive for b in self.bodies):
            self.bodies = [b for b in self.bodies if b.alive]

    def energy(self) -> Tuple[float, float]:
        """(cinétique, potentielle) — calcul direct O(n²) pour le diagnostic."""
        ke = sum(0.5 * b.m * (b.vx ** 2 + b.vy ** 2) for b in self.bodies)
        pe = 0.0
        soft2 = self.softening ** 2
        bs = self.bodies
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                d = math.sqrt((bs[i].x - bs[j].x) ** 2 + (bs[i].y - bs[j].y) ** 2 + soft2)
                pe -= self.G * bs[i].m * bs[j].m / d
        return ke, pe

    def momentum(self) -> Tuple[float, float]:
        return (sum(b.m * b.vx for b in self.bodies), sum(b.m * b.vy for b in self.bodies))

    # -- scénarios ------------------------------------------------------------
    def galaxy(self, cx: float, cy: float, n: int = 260, radius: float = 220,
               vx: float = 0.0, vy: float = 0.0, core: float = 3000.0, spin: int = 1,
               seed: Optional[int] = None, hue: float = 0.6) -> None:
        rng = random.Random(seed)
        center = Body(cx, cy, vx, vy, core, hue)
        self.bodies.append(center)
        for _ in range(n):
            r = radius * math.sqrt(rng.random()) * 0.95 + 12
            a = rng.random() * math.tau
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            # vitesse circulaire autour de la masse centrale (+ masse intérieure approx.)
            v = math.sqrt(self.G * (core + n * (r / radius) ** 2) / r)
            m = rng.uniform(0.5, 3.0)
            self.bodies.append(Body(x, y, vx - spin * v * math.sin(a), vy + spin * v * math.cos(a),
                                    m, hue + rng.uniform(-0.05, 0.05)))
        self.time = 0.0

    def preset(self, name: str, width: float, height: float, seed: int = 7) -> None:
        self.bodies = []
        self.time = 0.0
        cx, cy = width / 2, height / 2
        rng = random.Random(seed)
        if name == "Galaxie spirale":
            self.galaxy(cx, cy, n=320, radius=min(width, height) * 0.42, seed=seed)
        elif name == "Collision de galaxies":
            self.galaxy(cx - width * 0.22, cy - 40, n=180, radius=150, vx=14, vy=6, core=2200,
                        seed=seed, hue=0.58)
            self.galaxy(cx + width * 0.22, cy + 40, n=180, radius=150, vx=-14, vy=-6, core=2200,
                        spin=-1, seed=seed + 1, hue=0.02)
        elif name == "Système solaire":
            sun = Body(cx, cy, 0, 0, 8000, 0.12)
            self.bodies.append(sun)
            for i, r in enumerate((60, 95, 140, 200, 270, 330)):
                a = rng.random() * math.tau
                v = math.sqrt(self.G * sun.m / r)
                self.bodies.append(Body(cx + r * math.cos(a), cy + r * math.sin(a),
                                        -v * math.sin(a), v * math.cos(a),
                                        rng.uniform(3, 30), i / 6))
        elif name == "Étoile binaire":
            m = 3000
            d = 70
            v = math.sqrt(self.G * m / (4 * d))
            self.bodies += [Body(cx - d, cy, 0, -v, m, 0.08), Body(cx + d, cy, 0, v, m, 0.6)]
            for _ in range(150):
                r = rng.uniform(220, 320)
                a = rng.random() * math.tau
                vv = math.sqrt(self.G * 2 * m / r)
                self.bodies.append(Body(cx + r * math.cos(a), cy + r * math.sin(a),
                                        -vv * math.sin(a), vv * math.cos(a), 0.6,
                                        rng.uniform(0.4, 0.7)))
        else:  # « Nuage primordial »
            for _ in range(380):
                r = math.sqrt(rng.random()) * min(width, height) * 0.45
                a = rng.random() * math.tau
                self.bodies.append(Body(cx + r * math.cos(a), cy + r * math.sin(a),
                                        -math.sin(a) * r * 0.08, math.cos(a) * r * 0.08,
                                        rng.uniform(0.5, 6), rng.random()))


GRAVITY_PRESETS = ["Galaxie spirale", "Collision de galaxies", "Système solaire",
                   "Étoile binaire", "Nuage primordial"]


# ---------------------------------------------------------------------------
# Billes (hachage spatial + impulsions)
# ---------------------------------------------------------------------------

class Ball:
    __slots__ = ("x", "y", "vx", "vy", "r", "m", "hue")

    def __init__(self, x: float, y: float, vx: float, vy: float, r: float, hue: float) -> None:
        self.x, self.y, self.vx, self.vy, self.r = x, y, vx, vy, r
        self.m = r * r
        self.hue = hue


class BallWorld:
    def __init__(self, width: float = 900, height: float = 600) -> None:
        self.width, self.height = width, height
        self.balls: List[Ball] = []
        self.gravity = 600.0
        self.restitution = 0.85
        self.substeps = 4
        self.checks = 0
        self.collisions = 0
        self.rng = random.Random(3)

    def spawn(self, x: float, y: float, vx: float = 0.0, vy: float = 0.0,
              r: Optional[float] = None) -> Ball:
        b = Ball(x, y, vx, vy, r or self.rng.uniform(6, 16), self.rng.random())
        self.balls.append(b)
        return b

    def fill(self, n: int = 180) -> None:
        self.balls.clear()
        for _ in range(n):
            self.spawn(self.rng.uniform(20, self.width - 20), self.rng.uniform(20, self.height * 0.6),
                       self.rng.uniform(-150, 150), self.rng.uniform(-50, 50))

    def kinetic_energy(self) -> float:
        return sum(0.5 * b.m * (b.vx ** 2 + b.vy ** 2) for b in self.balls)

    def step(self, dt: float) -> None:
        dt = min(dt, 1 / 30)
        h = dt / self.substeps
        self.checks = self.collisions = 0
        for _ in range(self.substeps):
            for b in self.balls:
                b.vy += self.gravity * h
                b.x += b.vx * h
                b.y += b.vy * h
                if b.x < b.r:
                    b.x, b.vx = b.r, abs(b.vx) * self.restitution
                elif b.x > self.width - b.r:
                    b.x, b.vx = self.width - b.r, -abs(b.vx) * self.restitution
                if b.y < b.r:
                    b.y, b.vy = b.r, abs(b.vy) * self.restitution
                elif b.y > self.height - b.r:
                    b.y, b.vy = self.height - b.r, -abs(b.vy) * self.restitution
                    b.vx *= 0.995
            self._collide()

    def _collide(self) -> None:
        cell = 34.0
        grid: Dict[Tuple[int, int], List[Ball]] = {}
        for b in self.balls:
            grid.setdefault((int(b.x // cell), int(b.y // cell)), []).append(b)
        e = self.restitution
        for (gx, gy), items in grid.items():
            # On ne teste que les cellules « en avant » pour éviter les doublons
            neigh = list(items)
            for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 1)):
                neigh += grid.get((gx + dx, gy + dy), [])
            n_items = len(items)
            for i in range(n_items):
                a = items[i]
                for j in range(i + 1, len(neigh)):
                    b = neigh[j]
                    self.checks += 1
                    dx, dy = b.x - a.x, b.y - a.y
                    rr = a.r + b.r
                    d2 = dx * dx + dy * dy
                    if d2 >= rr * rr or d2 == 0:
                        continue
                    d = math.sqrt(d2)
                    nx, ny = dx / d, dy / d
                    # correction de position proportionnelle aux masses
                    overlap = rr - d
                    total = a.m + b.m
                    a.x -= nx * overlap * b.m / total
                    a.y -= ny * overlap * b.m / total
                    b.x += nx * overlap * a.m / total
                    b.y += ny * overlap * a.m / total
                    rel = (b.vx - a.vx) * nx + (b.vy - a.vy) * ny
                    if rel < 0:
                        j_imp = -(1 + e) * rel / (1 / a.m + 1 / b.m)
                        a.vx -= j_imp * nx / a.m
                        a.vy -= j_imp * ny / a.m
                        b.vx += j_imp * nx / b.m
                        b.vy += j_imp * ny / b.m
                        self.collisions += 1
