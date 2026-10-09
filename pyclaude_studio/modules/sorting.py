"""Course d'algorithmes de tri : jusqu'à quatre algorithmes animés sur les mêmes données."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Set

from ..core.colors import mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import sorting
from ..ui.widgets import LabeledScale, Section, button_row, combo, side_layout, themed_canvas


class Lane:
    """Un couloir de course : un algorithme, ses données, son canevas."""

    def __init__(self, module: "SortingModule", parent: tk.Misc, algo: str) -> None:
        self.module = module
        self.frame = ttk.Frame(parent, style="Module.TFrame")
        head = ttk.Frame(self.frame, style="Module.TFrame")
        head.pack(fill="x", padx=8, pady=(6, 2))
        self.algo_cb = combo(head, list(sorting.ALGORITHMS), algo, lambda v: module.reset(),
                             width=22)
        self.algo_cb.pack(side="left")
        self.cplx = ttk.Label(head, text="", style="Muted.TLabel")
        self.cplx.pack(side="left", padx=8)
        self.rank = ttk.Label(head, text="", style="Accent.TLabel")
        self.rank.pack(side="right")
        self.stats = ttk.Label(head, text="", style="Mono.TLabel")
        self.stats.pack(side="right", padx=8)
        self.canvas = themed_canvas(self.frame, role="canvas")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.canvas.bind("<Configure>", lambda e: self.redraw_all())
        self.data: List[int] = []
        self.gen: Optional[Any] = None
        self.bars: List[int] = []
        self.highlight: Dict[int, str] = {}
        self.done: Set[int] = set()
        self.comparisons = self.writes = 0
        self.finished = False
        self.finish_step = 0

    @property
    def algo(self) -> str:
        return self.algo_cb.var.get()  # type: ignore[attr-defined]

    def reset(self, data: List[int]) -> None:
        self.data = list(data)
        self.gen = sorting.ALGORITHMS[self.algo](self.data)
        self.highlight.clear()
        self.done.clear()
        self.comparisons = self.writes = 0
        self.finished = False
        self.finish_step = 0
        self.rank.configure(text="")
        self.cplx.configure(text=sorting.COMPLEXITY.get(self.algo, ""))
        self.redraw_all()

    def redraw_all(self) -> None:
        c = self.canvas
        t = self.module.theme
        c.configure(background=t.canvas)
        c.delete("all")
        self.bars = []
        n = len(self.data)
        if not n:
            return
        w, h = c.winfo_width(), c.winfo_height()
        bw = w / n
        top = max(self.data)
        for i, v in enumerate(self.data):
            x0 = i * bw
            y0 = h - (v / top) * (h - 6)
            self.bars.append(c.create_rectangle(x0, y0, x0 + max(1, bw - (1 if bw > 3 else 0)), h,
                                                fill=self._color(i), outline=""))
        self._update_stats()

    def _color(self, i: int) -> str:
        t = self.module.theme
        if i in self.highlight:
            return self.highlight[i]
        if self.finished or i in self.done:
            return mix(t.canvas, t.color(2), 0.85)
        return mix(t.canvas, t.fg2, 0.55)

    def _update_bar(self, i: int) -> None:
        if i >= len(self.bars):
            return
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        n = len(self.data)
        bw = w / n
        top = max(self.data) or 1
        x0 = i * bw
        y0 = h - (self.data[i] / top) * (h - 6)
        c.coords(self.bars[i], x0, y0, x0 + max(1, bw - (1 if bw > 3 else 0)), h)
        c.itemconfigure(self.bars[i], fill=self._color(i))

    def step(self, ops: int) -> int:
        """Exécute jusqu'à ``ops`` opérations ; renvoie le nombre réellement effectué."""
        if self.finished or self.gen is None:
            return 0
        t = self.module.theme
        old = set(self.highlight)
        self.highlight.clear()
        touched = set(old)
        count = 0
        for _ in range(ops):
            try:
                kind, i, j = next(self.gen)
            except StopIteration:
                self.finished = True
                touched.update(range(len(self.data)))
                break
            count += 1
            if kind == "cmp":
                self.comparisons += 1
                self.highlight[i] = t.accent
                if j >= 0:
                    self.highlight[j] = t.accent
            elif kind == "swap":
                self.writes += 2
                self.highlight[i] = t.color(1)
                self.highlight[j] = t.color(1)
            elif kind == "set":
                self.writes += 1
                self.highlight[i] = t.color(1)
            elif kind == "pivot":
                self.highlight[i] = t.color(3)
            elif kind == "done":
                self.done.add(i)
            touched.update(k for k in (i, j) if k >= 0)
        if self.finished:
            self.highlight.clear()
        touched.update(self.highlight)
        for i in touched:
            self._update_bar(i)
        self._update_stats()
        return count

    def _update_stats(self) -> None:
        self.stats.configure(text=f"{self.comparisons:>6} comp. {self.writes:>6} écr.")


class SortingModule(StudioModule):
    id = "sorting"
    title = "Course de tris"
    icon = "⇅"
    category = "Algorithmes"
    description = "17 algorithmes de tri instrumentés (rapide, fusion, tas, Shell, radix, " \
                  "TimSort…) en course côte à côte sur les mêmes données."
    order = 41
    keywords = ("sort", "quicksort", "algorithme", "visualisation")

    def build(self, frame: ttk.Frame) -> None:
        self.n = 120
        self.dist = "Aléatoire"
        self.seed = 1
        self.finish_order: List[Lane] = []
        main, panel = side_layout(frame, 280, "Course de tris",
                                  "Mêmes données, même vitesse : qui finit premier ?")
        self.lanes_frame = ttk.Frame(main, style="Module.TFrame")
        self.lanes_frame.pack(fill="both", expand=True)
        self.lanes: List[Lane] = []

        s = Section(panel, "Course")
        s.pack(fill="x")
        self.play = button_row(s.body, [("▶ Départ", self.toggle), ("Pas", self.step_once)],
                               accent_first=True)
        button_row(s.body, [("Mélanger", self.shuffle), ("Réinitialiser", self.reset)])
        self.lane_count = LabeledScale(s.body, "Nombre de couloirs", 1, 4, 3, integer=True,
                                       command=lambda v: self._set_lanes(int(v)))
        self.lane_count.pack(fill="x", pady=(6, 0))
        self.speed = LabeledScale(s.body, "Opérations par image", 1, 500, 8, integer=True,
                                  log=True)
        self.speed.pack(fill="x")

        s = Section(panel, "Données")
        s.pack(fill="x")
        self.size = LabeledScale(s.body, "Nombre d'éléments", 8, 400, self.n, integer=True,
                                 command=lambda v: self._resize_data(int(v)))
        self.size.pack(fill="x")
        self.dist_cb = combo(s.body, sorting.DISTRIBUTIONS, self.dist, self._on_dist)
        self.dist_cb.pack(fill="x", pady=3)

        s = Section(panel, "Légende")
        s.pack(fill="x")
        self.legend = themed_canvas(s.body, role="surface", height=96)
        self.legend.pack(fill="x")
        self.result = ttk.Label(panel, text="", style="Secondary.TLabel", wraplength=250,
                                justify="left")
        self.result.pack(anchor="w", padx=12, pady=8)

        self.anim = self.loop(self._tick, fps=60, name="sorting.race")
        self._set_lanes(3, ["Tri rapide", "Tri fusion", "Insertion"])
        self._draw_legend()

    def _set_lanes(self, count: int, algos: Optional[List[str]] = None) -> None:
        defaults = algos or [l.algo for l in self.lanes] + ["Tri par tas", "Tri de Shell",
                                                             "Bulles", "Radix LSD (base 4)"]
        for lane in self.lanes:
            lane.frame.destroy()
        self.lanes = []
        for i in range(count):
            lane = Lane(self, self.lanes_frame, defaults[i % len(defaults)])
            lane.frame.pack(fill="both", expand=True)
            self.lanes.append(lane)
        self.reset()

    def data(self) -> List[int]:
        return sorting.make_data(self.n, self.dist, self.seed)

    def reset(self) -> None:
        self.anim.stop() if hasattr(self, "anim") else None
        if hasattr(self, "play"):
            self.play[0].configure(text="▶ Départ")
        d = self.data()
        self.finish_order = []
        for lane in self.lanes:
            lane.reset(d)
        if hasattr(self, "result"):
            self.result.configure(text="")

    def shuffle(self) -> None:
        self.seed += 1
        self.reset()

    def _resize_data(self, n: int) -> None:
        self.n = n
        self.reset()

    def _on_dist(self, v: str) -> None:
        self.dist = v
        self.reset()

    def toggle(self) -> None:
        if all(l.finished for l in self.lanes):
            self.reset()
        running = self.anim.toggle()
        self.play[0].configure(text="⏸ Pause" if running else "▶ Reprendre")

    def step_once(self) -> None:
        for lane in self.lanes:
            lane.step(1)
        self._check_finish()

    def _tick(self, _dt: float) -> None:
        ops = int(self.speed.get())
        for lane in self.lanes:
            lane.step(ops)
        self._check_finish()

    def _check_finish(self) -> None:
        for lane in self.lanes:
            if lane.finished and lane not in self.finish_order:
                self.finish_order.append(lane)
                place = len(self.finish_order)
                lane.rank.configure(text=["1er ★", "2e", "3e", "4e"][place - 1])
        if all(l.finished for l in self.lanes):
            self.anim.stop()
            self.play[0].configure(text="▶ Départ")
            lines = [f"{i + 1}. {l.algo} — {l.comparisons} comparaisons, {l.writes} écritures"
                     for i, l in enumerate(self.finish_order)]
            self.result.configure(text="Arrivée :\n" + "\n".join(lines))

    def _draw_legend(self) -> None:
        c = self.legend
        t = self.theme
        c.configure(background=t.surface)
        c.delete("all")
        items = [(t.accent, "comparaison"), (t.color(1), "échange / écriture"),
                 (t.color(3), "pivot / lecture"), (mix(t.canvas, t.color(2), 0.85), "en place")]
        for i, (color, label) in enumerate(items):
            y = 12 + i * 22
            c.create_rectangle(4, y - 6, 18, y + 6, fill=color, outline="")
            c.create_text(26, y, text=label, anchor="w", fill=t.fg2, font="StudioSmall")

    def on_theme(self, theme) -> None:
        for lane in self.lanes:
            lane.redraw_all()
        self._draw_legend()

    def commands(self) -> List[Command]:
        return [Command("sort.go", "Départ / pause de la course", self.toggle, icon="▶"),
                Command("sort.shuffle", "Nouvelles données", self.shuffle, icon="⁂")]

    def get_state(self) -> Dict[str, Any]:
        return {"algos": [l.algo for l in self.lanes], "n": self.n, "dist": self.dist}

    def set_state(self, state: Dict[str, Any]) -> None:
        self.n = int(state.get("n", self.n))
        self.size.set(self.n)
        if state.get("dist") in sorting.DISTRIBUTIONS:
            self.dist = state["dist"]
            self.dist_cb.var.set(self.dist)  # type: ignore[attr-defined]
        algos = [a for a in state.get("algos", []) if a in sorting.ALGORITHMS]
        if algos:
            self.lane_count.set(len(algos))
            self._set_lanes(len(algos), algos)

    def info(self) -> Dict[str, Any]:
        return {"couloirs": len(self.lanes), "éléments": self.n}

    def demo(self) -> None:
        if not self.anim.running:
            self.toggle()
