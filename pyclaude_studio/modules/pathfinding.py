"""Recherche de chemin animée : A*, Dijkstra, BFS bidirectionnel… et générateurs de labyrinthes."""

from __future__ import annotations

import random
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Generator, List, Optional, Tuple

from ..core import imaging
from ..core.colors import hex_to_rgb, mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import pathfinding as pf
from ..ui.widgets import (LabeledScale, Section, Segmented, button_row, check, combo, info_label,
                          side_layout, themed_canvas)

EMPTY, WALL, MUD, OPEN, CLOSED = 0, 1, 2, 3, 4


class PathfindingModule(StudioModule):
    id = "pathfinding"
    title = "Recherche de chemin"
    icon = "⌖"
    category = "Algorithmes"
    description = "A*, Dijkstra, glouton, BFS, BFS bidirectionnel, DFS ; six générateurs de " \
                  "labyrinthes ; murs, boue et diagonales ; comparaison des algorithmes."
    order = 40
    keywords = ("a star", "dijkstra", "labyrinthe", "maze", "graphe")

    def build(self, frame: ttk.Frame) -> None:
        self.cell = 18
        self.grid = pf.Grid(41, 29, start=(3, 3), goal=(37, 25))
        self.marks: Dict[Tuple[int, int], int] = {}
        self.path: List[Tuple[int, int]] = []
        self.search: Optional[Generator] = None
        self.maze_gen: Optional[Generator] = None
        self.explored = 0
        self.t0 = 0.0
        self.tool = "wall"
        self._drag_marker: Optional[str] = None
        self._paint_mode: Optional[int] = None
        self.photo: Optional[tk.PhotoImage] = None
        self._palette_key: Any = None

        main, panel = side_layout(frame, 300, "Recherche de chemin",
                                  "Glissez ● départ et ⚑ arrivée · clic gauche : outil · "
                                  "clic droit : gomme")
        self.canvas = themed_canvas(main, role="canvas", cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.img_item = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", lambda e: self._erase(e))
        self.canvas.bind("<B3-Motion>", lambda e: self._erase(e))

        s = Section(panel, "Algorithme")
        s.pack(fill="x")
        self.algo_cb = combo(s.body, list(pf.ALGORITHMS), "A*")
        self.algo_cb.pack(fill="x", pady=3)
        ttk.Label(s.body, text="Heuristique (A*, glouton)", style="Secondary.TLabel").pack(
            anchor="w", pady=(4, 0))
        self.heur_cb = combo(s.body, list(pf.HEURISTICS), "Manhattan")
        self.heur_cb.pack(fill="x", pady=3)
        self.weight = LabeledScale(s.body, "Poids de l'heuristique (A* pondéré)", 1.0, 5.0, 1.0,
                                   fmt="{:.1f}")
        self.weight.pack(fill="x")
        self.diag = check(s.body, "Déplacements en diagonale", False, self._on_diag)
        self.speed = LabeledScale(s.body, "Vitesse (étapes par image)", 1, 400, 12, integer=True,
                                  log=True)
        self.speed.pack(fill="x")
        self.run_btns = button_row(s.body, [("▶ Chercher", self.run), ("Pas", self.step_once)],
                                   accent_first=True)
        button_row(s.body, [("Effacer la recherche", self.clear_search)])

        s = Section(panel, "Terrain")
        s.pack(fill="x")
        self.tool_seg = Segmented(s.body, [("wall", "Mur"), ("mud", "Boue ×5"), ("erase", "Gomme")],
                                  "wall", lambda v: setattr(self, "tool", v))
        self.tool_seg.pack(fill="x", pady=3)
        self.maze_cb = combo(s.body, list(pf.MAZES), "Exploration récursive")
        self.maze_cb.pack(fill="x", pady=3)
        button_row(s.body, [("Générer", self.generate_maze), ("Tout effacer", self.clear_all)])
        self.size_scale = LabeledScale(s.body, "Taille des cases", 8, 36, self.cell, integer=True,
                                       command=self._on_size)
        self.size_scale.pack(fill="x", pady=(6, 0))

        s = Section(panel, "Résultats")
        s.pack(fill="x")
        self.stats = info_label(s.body, "")
        button_row(s.body, [("Comparer tous les algorithmes", self.compare)])
        cols = ("algo", "explored", "length", "cost", "ms")
        self.table = ttk.Treeview(s.body, columns=cols, show="headings", height=7)
        for col, label, w in (("algo", "Algorithme", 110), ("explored", "Explorés", 60),
                              ("length", "Long.", 46), ("cost", "Coût", 50), ("ms", "ms", 40)):
            self.table.heading(col, text=label)
            self.table.column(col, width=w, anchor="w" if col == "algo" else "e")
        self.table.pack(fill="x", pady=4)

        self.anim = self.loop(self._tick, fps=60, name="pathfinding.anim")

    # ------------------------------------------------------------------
    # Grille et rendu
    # ------------------------------------------------------------------
    def _resize(self, _e: Any = None) -> None:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        cols = max(11, (w // self.cell) | 1)
        rows = max(11, (h // self.cell) | 1)
        if cols * self.cell > w:
            cols -= 2
        if rows * self.cell > h:
            rows -= 2
        if (cols, rows) != (self.grid.width, self.grid.height):
            g = self.grid
            g.width, g.height = cols, rows
            g.walls = {c for c in g.walls if c[0] < cols and c[1] < rows}
            g.weights = {c: v for c, v in g.weights.items() if c[0] < cols and c[1] < rows}
            g.start = (min(g.start[0], cols - 2), min(g.start[1], rows - 2))
            g.goal = (min(g.goal[0], cols - 2), min(g.goal[1], rows - 2))
            if not getattr(self, "_seeded", False):
                self._seeded = True
                g.start, g.goal = (1, rows // 2 | 1), (cols - 2, rows // 2 | 1)
                for y in range(3, rows - 3):
                    g.walls.add((cols // 3, y))
                for y in range(4, rows):
                    g.walls.add((2 * cols // 3, y - 3))
                for x in range(cols // 3 + 2, 2 * cols // 3 - 1):
                    for y in range(rows // 2 - 3, rows // 2 + 3):
                        g.weights[(x, y)] = 5.0
            g.walls.discard(g.start)
            g.walls.discard(g.goal)
            self.clear_search()
        self.render()

    def _cell_px(self) -> List[bytes]:
        t = self.theme
        key = (t.name, t.accent, self.cell)
        if key != self._palette_key:
            self._palette_key = key
            colors = {
                EMPTY: t.canvas,
                WALL: mix(t.canvas, t.fg2, 0.62),
                MUD: mix(t.canvas, t.color(3), 0.45),
                OPEN: mix(t.canvas, t.color(2), 0.55),
                CLOSED: mix(t.canvas, t.accent, 0.38),
            }
            line = bytes(hex_to_rgb(mix(t.canvas, t.fg, 0.06)))
            k = self.cell
            self._px = [bytes(hex_to_rgb(colors.get(i, t.canvas))) * (k - 1) + line
                        for i in range(8)]
            self._line_row = line
        return self._px

    def render(self) -> None:
        g = self.grid
        k = self.cell
        px = self._cell_px()
        line_row = self._line_row * (g.width * k)
        rows = []
        walls, weights, marks = g.walls, g.weights, self.marks
        for y in range(g.height):
            vals = []
            for x in range(g.width):
                c = (x, y)
                if c in walls:
                    vals.append(px[WALL])
                else:
                    m = marks.get(c)
                    if m is not None:
                        vals.append(px[m])
                    elif c in weights:
                        vals.append(px[MUD])
                    else:
                        vals.append(px[EMPTY])
            row = b"".join(vals)
            rows.append(row * (k - 1) + line_row)
        try:
            self.photo = imaging.photo_image(self.canvas, g.width * k, g.height * k, b"".join(rows))
        except tk.TclError:
            return
        self.canvas.itemconfigure(self.img_item, image=self.photo)
        self._draw_overlay()

    def _center(self, c: Tuple[int, int]) -> Tuple[float, float]:
        k = self.cell
        return c[0] * k + k / 2, c[1] * k + k / 2

    def _draw_overlay(self) -> None:
        cv = self.canvas
        t = self.theme
        cv.delete("overlay")
        k = self.cell
        # mud markers (petits points) pour distinguer sans couleur seule
        if k >= 12:
            for (x, y) in list(self.grid.weights)[:4000]:
                if (x, y) in self.grid.walls:
                    continue
                cx, cy = x * k + k / 2, y * k + k / 2
                cv.create_oval(cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5,
                               fill=mix(t.canvas, t.color(3), 0.9), outline="", tags="overlay")
        if len(self.path) >= 2:
            pts = [v for c in self.path for v in self._center(c)]
            cv.create_line(*pts, fill=t.canvas, width=max(4, k // 2.5) + 4, capstyle="round",
                           joinstyle="round", tags="overlay")
            cv.create_line(*pts, fill=t.color(1), width=max(3, k // 2.5), capstyle="round",
                           joinstyle="round", tags="overlay")
        r = k * 0.42
        sx, sy = self._center(self.grid.start)
        cv.create_oval(sx - r, sy - r, sx + r, sy + r, fill=t.color(0), outline=t.canvas,
                       width=2, tags=("overlay", "start"))
        gx, gy = self._center(self.grid.goal)
        cv.create_oval(gx - r, gy - r, gx + r, gy + r, fill=t.color(7), outline=t.canvas,
                       width=2, tags=("overlay", "goal"))
        cv.create_text(gx, gy, text="⚑", fill="#ffffff", font="StudioSmall", tags="overlay")

    # ------------------------------------------------------------------
    # Recherche
    # ------------------------------------------------------------------
    def _algo_kwargs(self) -> Dict[str, Any]:
        return {"heuristic": self.heur_cb.var.get(), "weight": self.weight.get()}  # type: ignore[attr-defined]

    def run(self) -> None:
        if self.anim.running and self.search is not None:
            self.anim.stop()
            self.run_btns[0].configure(text="▶ Reprendre")
            return
        if self.search is None:
            self.clear_search(render=False)
            name = self.algo_cb.var.get()  # type: ignore[attr-defined]
            self.search = pf.ALGORITHMS[name](self.grid, **self._algo_kwargs())
            self.explored = 0
            self.t0 = time.perf_counter()
        self.maze_gen = None
        self.anim.start()
        self.run_btns[0].configure(text="⏸ Pause")

    def step_once(self) -> None:
        if self.search is None:
            name = self.algo_cb.var.get()  # type: ignore[attr-defined]
            self.clear_search(render=False)
            self.search = pf.ALGORITHMS[name](self.grid, **self._algo_kwargs())
            self.t0 = time.perf_counter()
        self._advance(1)
        self.render()

    def _advance(self, n: int) -> bool:
        """Consomme ``n`` événements ; renvoie False quand la recherche est terminée."""
        if self.search is None:
            return False
        for _ in range(n):
            try:
                kind, data = next(self.search)
            except StopIteration:
                self.search = None
                return False
            if kind == "open":
                if data not in (self.grid.start, self.grid.goal):
                    self.marks.setdefault(data, OPEN)  # type: ignore[arg-type]
            elif kind == "close":
                self.explored += 1
                if data not in (self.grid.start, self.grid.goal):
                    self.marks[data] = CLOSED  # type: ignore[index]
            elif kind == "path":
                self.path = list(data)  # type: ignore[arg-type]
                self.search = None
                self._finish(True)
                return False
            elif kind == "fail":
                self.search = None
                self._finish(False)
                return False
        return True

    def _finish(self, found: bool) -> None:
        self.anim.stop()
        self.run_btns[0].configure(text="▶ Chercher")
        name = self.algo_cb.var.get()  # type: ignore[attr-defined]
        if found:
            cost = pf.path_cost(self.grid, self.path)
            self.stats.configure(text=f"{name}\nExplorés   {self.explored}\n"
                                      f"Longueur   {len(self.path) - 1}\nCoût       {cost:.1f}")
            self.status(f"Chemin trouvé : {len(self.path) - 1} pas, coût {cost:.1f}")
        else:
            self.stats.configure(text=f"{name}\nExplorés   {self.explored}\nAucun chemin !")
            self.notify("Aucun chemin possible", "warning")

    def _tick(self, _dt: float) -> None:
        if self.maze_gen is not None:
            for _ in range(max(4, int(self.speed.get()) * 3)):
                try:
                    next(self.maze_gen)
                except StopIteration:
                    self.maze_gen = None
                    g = self.grid
                    g.walls.discard(g.start)
                    g.walls.discard(g.goal)
                    self.anim.stop()
                    break
            self.render()
            return
        if not self._advance(int(self.speed.get())):
            self.anim.stop() if self.search is None else None
        self.render()

    def clear_search(self, render: bool = True) -> None:
        self.marks.clear()
        self.path = []
        self.search = None
        self.explored = 0
        if hasattr(self, "anim") and self.anim.running and self.maze_gen is None:
            self.anim.stop()
        if hasattr(self, "run_btns"):
            self.run_btns[0].configure(text="▶ Chercher")
        if render:
            self.render()

    def clear_all(self) -> None:
        self.maze_gen = None
        self.grid.clear()
        self.clear_search()

    def generate_maze(self) -> None:
        self.clear_search(render=False)
        g = self.grid
        name = self.maze_cb.var.get()  # type: ignore[attr-defined]
        # Départ et arrivée sur des « salles » impaires pour les labyrinthes parfaits
        g.start = (1, 1)
        g.goal = (g.width - 2 - (1 - g.width % 2), g.height - 2 - (1 - g.height % 2))
        self.maze_gen = pf.MAZES[name](g, random.Random())
        self.anim.start()

    def compare(self) -> None:
        self.table.delete(*self.table.get_children())
        for name in pf.ALGORITHMS:
            t0 = time.perf_counter()
            path, explored = pf.solve(self.grid, name, **self._algo_kwargs())
            ms = (time.perf_counter() - t0) * 1000
            if path:
                vals = (name, explored, len(path) - 1, f"{pf.path_cost(self.grid, path):.1f}",
                        f"{ms:.1f}")
            else:
                vals = (name, explored, "—", "—", f"{ms:.1f}")
            self.table.insert("", "end", values=vals)

    def _on_diag(self, v: bool) -> None:
        self.grid.diagonal = v
        self.clear_search()

    def _on_size(self, v: float) -> None:
        self.cell = int(v)
        self._palette_key = None
        self._resize()

    # ------------------------------------------------------------------
    # Souris
    # ------------------------------------------------------------------
    def _cell_at(self, e: tk.Event) -> Tuple[int, int]:
        return (max(0, min(self.grid.width - 1, e.x // self.cell)),
                max(0, min(self.grid.height - 1, e.y // self.cell)))

    def _press(self, e: tk.Event) -> None:
        c = self._cell_at(e)
        if c == self.grid.start:
            self._drag_marker = "start"
        elif c == self.grid.goal:
            self._drag_marker = "goal"
        else:
            self._drag_marker = None
            self._apply_tool(c)

    def _motion(self, e: tk.Event) -> None:
        c = self._cell_at(e)
        if self._drag_marker:
            if c in self.grid.walls or c in (self.grid.start, self.grid.goal):
                return
            setattr(self.grid, self._drag_marker, c)
            if self.path or self.marks:
                # recalcul instantané pendant le déplacement : effet « temps réel »
                self.clear_search(render=False)
                name = self.algo_cb.var.get()  # type: ignore[attr-defined]
                self.search = pf.ALGORITHMS[name](self.grid, **self._algo_kwargs())
                self._advance(10 ** 6)
            self.render()
        else:
            self._apply_tool(c)

    def _release(self, _e: tk.Event) -> None:
        self._drag_marker = None

    def _apply_tool(self, c: Tuple[int, int]) -> None:
        if c in (self.grid.start, self.grid.goal):
            return
        if self.tool == "wall":
            self.grid.walls.add(c)
            self.grid.weights.pop(c, None)
        elif self.tool == "mud":
            self.grid.walls.discard(c)
            self.grid.weights[c] = 5.0
        else:
            self.grid.walls.discard(c)
            self.grid.weights.pop(c, None)
        self.marks.pop(c, None)
        self.render()

    def _erase(self, e: tk.Event) -> None:
        c = self._cell_at(e)
        self.grid.walls.discard(c)
        self.grid.weights.pop(c, None)
        self.render()

    # ------------------------------------------------------------------
    def on_theme(self, theme) -> None:
        self._palette_key = None
        self.render()

    def commands(self) -> List[Command]:
        return [Command("path.run", "Lancer la recherche", self.run, icon="▶"),
                Command("path.maze", "Générer un labyrinthe", self.generate_maze, icon="▦"),
                Command("path.compare", "Comparer tous les algorithmes", self.compare, icon="≡"),
                Command("path.clear", "Tout effacer", self.clear_all, icon="✕")]

    def get_state(self) -> Dict[str, Any]:
        g = self.grid
        return {"walls": sorted(g.walls), "weights": sorted(g.weights), "start": g.start,
                "goal": g.goal, "cell": self.cell, "diagonal": g.diagonal}

    def set_state(self, state: Dict[str, Any]) -> None:
        g = self.grid
        try:
            g.walls = {tuple(c) for c in state.get("walls", [])}  # type: ignore[misc]
            g.weights = {tuple(c): 5.0 for c in state.get("weights", [])}  # type: ignore[misc]
            g.start = tuple(state.get("start", g.start))  # type: ignore[assignment]
            g.goal = tuple(state.get("goal", g.goal))  # type: ignore[assignment]
        except TypeError:
            return
        self._seeded = True
        self.cell = int(state.get("cell", self.cell))
        self.size_scale.set(self.cell)
        g.diagonal = bool(state.get("diagonal", False))
        self.diag.set(g.diagonal)

    def info(self) -> Dict[str, Any]:
        return {"explorés": self.explored, "chemin": max(0, len(self.path) - 1)}

    def demo(self) -> None:
        self.run()
