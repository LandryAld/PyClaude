"""Automates cellulaires : jeu de la vie et variantes, motifs RLE, coloration par âge."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core import imaging
from ..core.colors import gradient, hex_to_rgb, mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import life
from ..ui.widgets import (LabeledScale, LineChart, Section, Segmented, button_row, check, combo,
                          info_label, side_layout, themed_canvas)


class LifeModule(StudioModule):
    id = "life"
    title = "Automates cellulaires"
    icon = "▩"
    category = "Simulation"
    description = "Jeu de la vie de Conway et 11 autres règles (HighLife, Day & Night, cerveau " \
                  "de Brian…), motifs célèbres, import/export RLE, coloration par âge."
    order = 30
    keywords = ("conway", "game of life", "automate", "rle")

    def build(self, frame: ttk.Frame) -> None:
        self.cell = 7
        self.grid = life.LifeGrid(120, 80)
        self.tool = "draw"
        self.pattern_name = "Planeur"
        self.pattern_cells: Set[Tuple[int, int]] = set()
        self.color_age = True
        self.gridlines = True
        self.speed = 15.0
        self._acc = 0.0
        self.photo: Optional[tk.PhotoImage] = None
        self._cache_key: Any = None
        self._cell_px: List[bytes] = []
        self._painting: Optional[bool] = None

        main, panel = side_layout(frame, 300, "Automates cellulaires",
                                  "Clic gauche : dessiner · clic droit : effacer")
        self.canvas = themed_canvas(main, role="canvas", cursor="pencil")
        self.canvas.pack(fill="both", expand=True)
        self.img_item = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<ButtonPress-1>", lambda e: self._paint(e, True, press=True))
        self.canvas.bind("<B1-Motion>", lambda e: self._paint(e, True))
        self.canvas.bind("<space>", lambda e: self.toggle())
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set(), add="+")
        self.canvas.bind("<ButtonPress-3>", lambda e: self._paint(e, False))
        self.canvas.bind("<B3-Motion>", lambda e: self._paint(e, False))
        self.canvas.bind("<Motion>", self._ghost)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("ghost"))

        s = Section(panel, "Simulation")
        s.pack(fill="x")
        self.play_btns = button_row(s.body, [("▶ Lancer", self.toggle), ("Pas", self.step_once),
                                             ("Effacer", self.clear)], accent_first=True)
        LabeledScale(s.body, "Générations / s", 1, 120, self.speed, integer=True,
                     command=lambda v: setattr(self, "speed", v)).pack(fill="x", pady=(6, 0))
        self.density = LabeledScale(s.body, "Densité aléatoire", 0.05, 0.8, 0.28, fmt="{:.0%}")
        self.density.pack(fill="x")
        button_row(s.body, [("Remplissage aléatoire", self.randomize)])

        s = Section(panel, "Règle")
        s.pack(fill="x")
        self.rule_cb = combo(s.body, list(life.RULES), "Jeu de la vie (Conway)", self._on_rule)
        self.rule_cb.pack(fill="x", pady=3)
        row = ttk.Frame(s.body, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        self.rule_var = tk.StringVar(value="B3/S23")
        ent = ttk.Entry(row, textvariable=self.rule_var, width=14, font="StudioMono")
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self._custom_rule())
        ttk.Button(row, text="Appliquer", command=self._custom_rule).pack(side="right", padx=(6, 0))
        self.wrap_var = check(s.body, "Monde torique (bords reliés)", True, self._on_wrap)

        s = Section(panel, "Outils & motifs")
        s.pack(fill="x")
        self.tool_seg = Segmented(s.body, [("draw", "Crayon"), ("pattern", "Motif")], "draw",
                                  self._on_tool)
        self.tool_seg.pack(fill="x", pady=3)
        self.pattern_cb = combo(s.body, list(life.PATTERNS), self.pattern_name, self._on_pattern)
        self.pattern_cb.pack(fill="x", pady=3)
        button_row(s.body, [("Importer RLE…", self.import_rle), ("Exporter RLE…", self.export_rle)])
        self.size_scale = LabeledScale(s.body, "Taille des cellules (px)", 2, 20, self.cell,
                                       integer=True, command=self._on_cell)
        self.size_scale.pack(fill="x", pady=(6, 0))
        check(s.body, "Coloration selon l'âge", True, self._on_age)
        check(s.body, "Quadrillage", True, self._on_lines)

        s = Section(panel, "Statistiques")
        s.pack(fill="x")
        self.stats = info_label(s.body, "")
        self.chart = LineChart(s.body, capacity=200, title="Population", height=130, y_min=0)
        self.chart.add_series("Population", 0)
        self.chart.pack(fill="x", pady=(4, 0))

        self.sim = self.loop(self._tick, fps=60, name="life.sim")
        self._on_pattern(self.pattern_name)

    # ------------------------------------------------------------------
    def _resize(self, _e: Any = None) -> None:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        cols, rows = max(10, w // self.cell), max(10, h // self.cell)
        first = not self.grid.alive and self.grid.generation == 0
        if (cols, rows) != (self.grid.width, self.grid.height):
            self.grid.resize(cols, rows)
        if first and not getattr(self, "_seeded", False):
            self._seeded = True
            gun, _ = life.parse_rle(life.PATTERNS["Canon à planeurs de Gosper"])
            self.grid.place(gun, 22, 14)
            acorn, _ = life.parse_rle(life.PATTERNS["Gland (acorn)"])
            self.grid.place(acorn, cols * 2 // 3, rows * 2 // 3)
        self.render()

    def _palette(self) -> List[bytes]:
        t = self.theme
        key = (t.name, t.accent, self.color_age, self.gridlines, self.cell)
        if key == self._cache_key:
            return self._cell_px
        self._cache_key = key
        bg = hex_to_rgb(t.canvas)
        line = hex_to_rgb(mix(t.canvas, t.fg, 0.07))
        ages = gradient([(0.0, mix(t.accent, "#ffffff", 0.35)), (0.15, t.accent),
                         (0.5, t.color(4)), (1.0, t.color(3))], 41)
        pal: List[Tuple[int, int, int]] = [bg] * 256
        for i in range(1, 42):
            pal[i] = ages[i - 1] if self.color_age else hex_to_rgb(t.accent)
        for i in range(200, 256):
            pal[i] = hex_to_rgb(mix(t.canvas, t.color(1), max(0.15, 0.75 - (i - 200) * 0.2)))
        k = self.cell
        lines = self.gridlines and k >= 5
        if lines:
            self._cell_px = [bytes(c) * (k - 1) + bytes(line) for c in pal]
            self._line_px = bytes(line)
        else:
            self._cell_px = [bytes(c) * k for c in pal]
            self._line_px = b""
        return self._cell_px

    def render(self) -> None:
        g = self.grid
        k = self.cell
        cell_px = self._palette()
        idx = g.render_index()
        w, h = g.width, g.height
        lines = bool(self._line_px)
        line_row = self._line_px * (w * k)
        rows = []
        for y in range(h):
            row = b"".join([cell_px[v] for v in idx[y * w:(y + 1) * w]])
            rows.append(row * (k - 1) + line_row if lines else row * k)
        rgb = b"".join(rows)
        try:
            self.photo = imaging.photo_image(self.canvas, w * k, h * k, rgb)
        except tk.TclError:
            return
        self.canvas.itemconfigure(self.img_item, image=self.photo)
        self.canvas.tag_lower(self.img_item)
        self._update_stats()

    def _update_stats(self) -> None:
        g = self.grid
        self.stats.configure(text=f"Génération  {g.generation}\nPopulation  {g.population}\n"
                                  f"Naissances  {g.births}   Morts {g.deaths}\n"
                                  f"Règle       {g.rule.notation()}\n"
                                  f"Grille      {g.width}×{g.height}")

    # ------------------------------------------------------------------
    def toggle(self) -> None:
        running = self.sim.toggle()
        self.play_btns[0].configure(text="⏸ Pause" if running else "▶ Lancer")

    def step_once(self) -> None:
        self.grid.step()
        self.chart.push("Population", self.grid.population)
        self.chart.redraw()
        self.render()

    def _tick(self, dt: float) -> None:
        self._acc += dt * self.speed
        steps = int(self._acc)
        if steps <= 0:
            return
        self._acc -= steps
        for _ in range(min(steps, 8)):
            self.grid.step()
            self.chart.push("Population", self.grid.population)
        self.render()
        if self.grid.generation % 5 == 0:
            self.chart.redraw()

    def clear(self) -> None:
        self.grid.clear()
        self.chart.clear()
        self.render()

    def randomize(self) -> None:
        self.grid.randomize(self.density.get())
        self.chart.clear()
        self.render()

    def _on_rule(self, name: str) -> None:
        self.rule_var.set(life.RULES[name])
        self._custom_rule()

    def _custom_rule(self) -> None:
        try:
            self.grid.set_rule(self.rule_var.get())
        except ValueError as e:
            self.notify(str(e), "warning")
            return
        self.status(f"Règle {self.grid.rule.notation()} appliquée")
        self.render()

    def _on_wrap(self, v: bool) -> None:
        self.grid.wrap = v

    def _on_tool(self, v: str) -> None:
        self.tool = v
        self.canvas.configure(cursor="pencil" if v == "draw" else "plus")

    def _on_pattern(self, name: str) -> None:
        self.pattern_name = name
        self.pattern_cells, _ = life.parse_rle(life.PATTERNS[name])
        self.tool_seg.set("pattern")
        self._on_tool("pattern")

    def _on_cell(self, v: float) -> None:
        self.cell = int(v)
        self._cache_key = None
        self._resize()

    def _on_age(self, v: bool) -> None:
        self.color_age = v
        self.render()

    def _on_lines(self, v: bool) -> None:
        self.gridlines = v
        self.render()

    def _cell_at(self, e: tk.Event) -> Tuple[int, int]:
        return e.x // self.cell, e.y // self.cell

    def _paint(self, e: tk.Event, alive: bool, press: bool = False) -> None:
        x, y = self._cell_at(e)
        if self.tool == "pattern" and alive:
            if press:
                self.grid.place(self.pattern_cells, x, y)
                self.render()
            return
        self.grid.set(x, y, alive)
        self.render()

    def _ghost(self, e: tk.Event) -> None:
        self.canvas.delete("ghost")
        if self.tool != "pattern" or not self.pattern_cells:
            return
        x, y = self._cell_at(e)
        k = self.cell
        w = max(c[0] for c in self.pattern_cells) + 1
        h = max(c[1] for c in self.pattern_cells) + 1
        color = self.theme.color(2)
        for cx, cy in list(self.pattern_cells)[:600]:
            px, py = (x - w // 2 + cx) * k, (y - h // 2 + cy) * k
            self.canvas.create_rectangle(px, py, px + k - 1, py + k - 1, outline=color,
                                         tags="ghost")

    # ------------------------------------------------------------------
    def import_rle(self) -> None:
        path = filedialog.askopenfilename(parent=self.frame, filetypes=[("Motifs RLE", "*.rle"),
                                                                        ("Tous", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                cells, rule = life.parse_rle(fh.read())
        except (OSError, ValueError) as exc:
            self.notify(f"Import impossible : {exc}", "error")
            return
        if rule:
            self.rule_var.set(rule)
            self._custom_rule()
        self.grid.clear()
        self.grid.place(cells, self.grid.width // 2, self.grid.height // 2)
        self.render()
        self.notify(f"{os.path.basename(path)} : {len(cells)} cellules", "success")

    def export_rle(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".rle",
                                            filetypes=[("Motifs RLE", "*.rle")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("#C Exporté par PyClaude Studio\n" +
                         life.to_rle(self.grid.alive, self.grid.rule.notation()))
            self.notify("Motif exporté", "success")

    # ------------------------------------------------------------------
    def on_theme(self, theme) -> None:
        self._cache_key = None
        self.render()
        self.chart.redraw()

    def commands(self) -> List[Command]:
        return [Command("life.toggle", "Lancer / mettre en pause (Espace sur la grille)",
                        self.toggle, icon="▶"),
                Command("life.step", "Génération suivante", self.step_once, icon="⏭"),
                Command("life.random", "Remplissage aléatoire", self.randomize, icon="⁂"),
                Command("life.clear", "Effacer la grille", self.clear, icon="✕")]

    def get_state(self) -> Dict[str, Any]:
        return {"rle": life.to_rle(self.grid.alive, self.grid.rule.notation()),
                "cell": self.cell, "wrap": self.grid.wrap}

    def set_state(self, state: Dict[str, Any]) -> None:
        self._seeded = True
        rle = state.get("rle")
        if rle:
            cells, rule = life.parse_rle(rle)
            if rule:
                try:
                    self.grid.set_rule(rule)
                    self.rule_var.set(rule)
                except ValueError:
                    pass
            self.grid.place(cells, 2, 2, center=False)
        self.cell = int(state.get("cell", self.cell))
        self.size_scale.set(self.cell)
        self.grid.wrap = bool(state.get("wrap", True))
        self.wrap_var.set(self.grid.wrap)

    def info(self) -> Dict[str, Any]:
        return {"génération": self.grid.generation, "population": self.grid.population}

    def demo(self) -> None:
        if not self.sim.running:
            self.toggle()
