"""Recherche de chemin et génération de labyrinthes, sous forme de générateurs animables.

Chaque algorithme est un générateur qui émet des événements
``("open", cellule)``, ``("close", cellule)`` puis ``("path", [cellules])``
(ou ``("fail", None)``). L'interface les consomme au rythme voulu, ce qui
permet d'animer pas à pas sans thread ni état global.
"""

from __future__ import annotations

import heapq
import itertools
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Generator, Iterator, List, Optional, Set, Tuple

Cell = Tuple[int, int]
Event = Tuple[str, object]
SQRT2 = math.sqrt(2)


@dataclass
class Grid:
    width: int
    height: int
    walls: Set[Cell] = field(default_factory=set)
    weights: Dict[Cell, float] = field(default_factory=dict)  # coût > 1 (boue)
    diagonal: bool = False
    start: Cell = (2, 2)
    goal: Cell = (10, 10)

    def inside(self, c: Cell) -> bool:
        return 0 <= c[0] < self.width and 0 <= c[1] < self.height

    def passable(self, c: Cell) -> bool:
        return self.inside(c) and c not in self.walls

    def cost(self, c: Cell) -> float:
        return self.weights.get(c, 1.0)

    def neighbors(self, c: Cell) -> Iterator[Tuple[Cell, float]]:
        x, y = c
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if self.passable(n):
                yield n, self.cost(n)
        if self.diagonal:
            for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                n = (x + dx, y + dy)
                # Pas de coupe de coin : les deux voisins orthogonaux doivent être libres
                if (self.passable(n) and self.passable((x + dx, y))
                        and self.passable((x, y + dy))):
                    yield n, self.cost(n) * SQRT2

    def clear(self) -> None:
        self.walls.clear()
        self.weights.clear()


# -- heuristiques ------------------------------------------------------------

def manhattan(a: Cell, b: Cell) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def euclidean(a: Cell, b: Cell) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def octile(a: Cell, b: Cell) -> float:
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return max(dx, dy) + (SQRT2 - 1) * min(dx, dy)


def chebyshev(a: Cell, b: Cell) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


HEURISTICS: Dict[str, Callable[[Cell, Cell], float]] = {
    "Manhattan": manhattan, "Euclidienne": euclidean, "Octile": octile,
    "Tchebychev": chebyshev,
}


def _reconstruct(came: Dict[Cell, Optional[Cell]], end: Cell) -> List[Cell]:
    path = [end]
    while came.get(path[-1]) is not None:
        path.append(came[path[-1]])  # type: ignore[arg-type]
    path.reverse()
    return path


def path_cost(grid: Grid, path: List[Cell]) -> float:
    total = 0.0
    for a, b in zip(path, path[1:]):
        step = SQRT2 if a[0] != b[0] and a[1] != b[1] else 1.0
        total += grid.cost(b) * step
    return total


# -- algorithmes -------------------------------------------------------------

def bfs(grid: Grid, **_: object) -> Generator[Event, None, None]:
    start, goal = grid.start, grid.goal
    came: Dict[Cell, Optional[Cell]] = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        yield "close", cur
        if cur == goal:
            yield "path", _reconstruct(came, cur)
            return
        for n, _c in grid.neighbors(cur):
            if n not in came:
                came[n] = cur
                q.append(n)
                yield "open", n
    yield "fail", None


def dfs(grid: Grid, **_: object) -> Generator[Event, None, None]:
    start, goal = grid.start, grid.goal
    came: Dict[Cell, Optional[Cell]] = {start: None}
    stack = [start]
    seen = {start}
    while stack:
        cur = stack.pop()
        yield "close", cur
        if cur == goal:
            yield "path", _reconstruct(came, cur)
            return
        for n, _c in reversed(list(grid.neighbors(cur))):
            if n not in seen:
                seen.add(n)
                came[n] = cur
                stack.append(n)
                yield "open", n
    yield "fail", None


def _best_first(grid: Grid, g_weight: float, h_weight: float,
                heuristic: Callable[[Cell, Cell], float]) -> Generator[Event, None, None]:
    start, goal = grid.start, grid.goal
    counter = itertools.count()
    g: Dict[Cell, float] = {start: 0.0}
    came: Dict[Cell, Optional[Cell]] = {start: None}
    h0 = heuristic(start, goal)
    heap = [(h_weight * h0, h0, next(counter), start)]
    closed: Set[Cell] = set()
    while heap:
        _f, _h, _i, cur = heapq.heappop(heap)
        if cur in closed:
            continue
        closed.add(cur)
        yield "close", cur
        if cur == goal:
            yield "path", _reconstruct(came, cur)
            return
        for n, cost in grid.neighbors(cur):
            if n in closed:
                continue
            ng = g[cur] + cost
            if ng < g.get(n, math.inf):
                g[n] = ng
                came[n] = cur
                h = heuristic(n, goal)
                heapq.heappush(heap, (g_weight * ng + h_weight * h, h, next(counter), n))
                yield "open", n
    yield "fail", None


def dijkstra(grid: Grid, **_: object) -> Generator[Event, None, None]:
    return _best_first(grid, 1.0, 0.0, manhattan)


def astar(grid: Grid, heuristic: str = "Manhattan", weight: float = 1.0,
          **_: object) -> Generator[Event, None, None]:
    h = HEURISTICS.get(heuristic, manhattan)
    if grid.diagonal and heuristic == "Manhattan":
        h = octile  # Manhattan surestime avec diagonales : on reste admissible
    return _best_first(grid, 1.0, weight, h)


def greedy(grid: Grid, heuristic: str = "Manhattan", **_: object) -> Generator[Event, None, None]:
    return _best_first(grid, 0.0, 1.0, HEURISTICS.get(heuristic, manhattan))


def bidirectional_bfs(grid: Grid, **_: object) -> Generator[Event, None, None]:
    start, goal = grid.start, grid.goal
    if start == goal:
        yield "path", [start]
        return
    came_a: Dict[Cell, Optional[Cell]] = {start: None}
    came_b: Dict[Cell, Optional[Cell]] = {goal: None}
    qa, qb = deque([start]), deque([goal])
    while qa and qb:
        for q, came, other in ((qa, came_a, came_b), (qb, came_b, came_a)):
            for _ in range(len(q)):
                cur = q.popleft()
                yield "close", cur
                for n, _c in grid.neighbors(cur):
                    if n in came:
                        continue
                    came[n] = cur
                    if n in other:
                        pa = _reconstruct(came_a, n)
                        pb = _reconstruct(came_b, n)
                        yield "path", pa + list(reversed(pb[:-1]))
                        return
                    q.append(n)
                    yield "open", n
    yield "fail", None


ALGORITHMS: Dict[str, Callable[..., Generator[Event, None, None]]] = {
    "A*": astar,
    "Dijkstra": dijkstra,
    "Glouton (best-first)": greedy,
    "Largeur (BFS)": bfs,
    "BFS bidirectionnel": bidirectional_bfs,
    "Profondeur (DFS)": dfs,
}
OPTIMAL = {"A*", "Dijkstra"}  # (BFS est optimal seulement sans poids ni diagonales)


def solve(grid: Grid, algorithm: str, **kw: object) -> Tuple[Optional[List[Cell]], int]:
    """Exécute un algorithme jusqu'au bout ; renvoie (chemin, nœuds explorés)."""
    explored = 0
    for kind, data in ALGORITHMS[algorithm](grid, **kw):
        if kind == "close":
            explored += 1
        elif kind == "path":
            return data, explored  # type: ignore[return-value]
        elif kind == "fail":
            return None, explored
    return None, explored


# -- labyrinthes ---------------------------------------------------------------
# Les labyrinthes « parfaits » utilisent les cellules impaires comme salles et
# les cellules paires comme murs potentiels. Les générateurs émettent
# ("carve", cellule) ou ("wall", cellule).

def _rooms(grid: Grid) -> List[Cell]:
    return [(x, y) for y in range(1, grid.height - 1, 2) for x in range(1, grid.width - 1, 2)]


def _fill(grid: Grid) -> None:
    grid.weights.clear()
    grid.walls = {(x, y) for y in range(grid.height) for x in range(grid.width)}


def maze_backtracker(grid: Grid, rng: random.Random) -> Generator[Event, None, None]:
    _fill(grid)
    start = (1, 1)
    grid.walls.discard(start)
    yield "carve", start
    stack = [start]
    visited = {start}
    while stack:
        x, y = stack[-1]
        options = [(x + dx, y + dy, x + dx // 2, y + dy // 2)
                   for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2))
                   if 0 < x + dx < grid.width - 1 and 0 < y + dy < grid.height - 1
                   and (x + dx, y + dy) not in visited]
        if not options:
            stack.pop()
            continue
        nx, ny, wx, wy = rng.choice(options)
        for c in ((wx, wy), (nx, ny)):
            grid.walls.discard(c)
            yield "carve", c
        visited.add((nx, ny))
        stack.append((nx, ny))


def maze_prim(grid: Grid, rng: random.Random) -> Generator[Event, None, None]:
    _fill(grid)
    start = (1, 1)
    grid.walls.discard(start)
    yield "carve", start
    in_maze = {start}
    frontier: List[Tuple[Cell, Cell]] = []

    def add_frontier(c: Cell) -> None:
        x, y = c
        for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            n = (x + dx, y + dy)
            if 0 < n[0] < grid.width - 1 and 0 < n[1] < grid.height - 1 and n not in in_maze:
                frontier.append((n, (x + dx // 2, y + dy // 2)))

    add_frontier(start)
    while frontier:
        i = rng.randrange(len(frontier))
        frontier[i], frontier[-1] = frontier[-1], frontier[i]
        cell, wall = frontier.pop()
        if cell in in_maze:
            continue
        in_maze.add(cell)
        for c in (wall, cell):
            grid.walls.discard(c)
            yield "carve", c
        add_frontier(cell)


def maze_kruskal(grid: Grid, rng: random.Random) -> Generator[Event, None, None]:
    _fill(grid)
    rooms = _rooms(grid)
    parent = {c: c for c in rooms}

    def find(c: Cell) -> Cell:
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    edges = []
    for x, y in rooms:
        if (x + 2, y) in parent:
            edges.append(((x, y), (x + 2, y), (x + 1, y)))
        if (x, y + 2) in parent:
            edges.append(((x, y), (x, y + 2), (x, y + 1)))
    rng.shuffle(edges)
    for c in rooms:
        grid.walls.discard(c)
        yield "carve", c
    for a, b, wall in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            grid.walls.discard(wall)
            yield "carve", wall


def maze_division(grid: Grid, rng: random.Random) -> Generator[Event, None, None]:
    """Division récursive : on part d'une salle vide et on ajoute des murs."""
    grid.walls = set()
    grid.weights.clear()
    w, h = grid.width, grid.height
    for x in range(w):
        for y in (0, h - 1):
            grid.walls.add((x, y))
            yield "wall", (x, y)
    for y in range(h):
        for x in (0, w - 1):
            grid.walls.add((x, y))
            yield "wall", (x, y)
    stack = [(1, 1, w - 2, h - 2)]
    while stack:
        x0, y0, x1, y1 = stack.pop()
        width, height = x1 - x0 + 1, y1 - y0 + 1
        if width < 3 or height < 3:
            continue
        horizontal = height > width if width != height else rng.random() < 0.5
        if horizontal:
            ys = [y for y in range(y0 + 1, y1, 2)]
            if not ys:
                continue
            wy = rng.choice(ys)
            gaps = [x for x in range(x0, x1 + 1, 2)]
            gx = rng.choice(gaps)
            for x in range(x0, x1 + 1):
                if x != gx:
                    grid.walls.add((x, wy))
                    yield "wall", (x, wy)
            stack += [(x0, y0, x1, wy - 1), (x0, wy + 1, x1, y1)]
        else:
            xs = [x for x in range(x0 + 1, x1, 2)]
            if not xs:
                continue
            wx = rng.choice(xs)
            gy = rng.choice([y for y in range(y0, y1 + 1, 2)])
            for y in range(y0, y1 + 1):
                if y != gy:
                    grid.walls.add((wx, y))
                    yield "wall", (wx, y)
            stack += [(x0, y0, wx - 1, y1), (wx + 1, y0, x1, y1)]


def maze_scatter(grid: Grid, rng: random.Random, density: float = 0.28
                 ) -> Generator[Event, None, None]:
    grid.walls = set()
    grid.weights.clear()
    for y in range(grid.height):
        for x in range(grid.width):
            r = rng.random()
            if r < density:
                grid.walls.add((x, y))
                yield "wall", (x, y)
            elif r < density + 0.12:
                grid.weights[(x, y)] = 5.0
                yield "weight", (x, y)


def maze_braid(grid: Grid, rng: random.Random) -> Generator[Event, None, None]:
    """Labyrinthe tressé : un labyrinthe parfait dont on supprime des culs-de-sac (boucles)."""
    yield from maze_backtracker(grid, rng)
    for x, y in _rooms(grid):
        exits = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                 if (x + dx, y + dy) not in grid.walls]
        if len(exits) == 1 and rng.random() < 0.6:
            options = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                       if (x + dx, y + dy) in grid.walls
                       and 0 < x + 2 * dx < grid.width - 1 and 0 < y + 2 * dy < grid.height - 1]
            if options:
                c = rng.choice(options)
                grid.walls.discard(c)
                yield "carve", c


MAZES: Dict[str, Callable[..., Generator[Event, None, None]]] = {
    "Exploration récursive": maze_backtracker,
    "Prim aléatoire": maze_prim,
    "Kruskal (union-find)": maze_kruskal,
    "Division récursive": maze_division,
    "Labyrinthe tressé (boucles)": maze_braid,
    "Obstacles et boue aléatoires": maze_scatter,
}
