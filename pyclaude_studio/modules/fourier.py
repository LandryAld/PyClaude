"""Épicycles de Fourier : dessinez une forme, la FFT la reconstruit avec des cercles tournants."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core.colors import mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import fourier
from ..ui.widgets import (LabeledScale, Section, button_row, check, combo, info_label, side_layout,
                          themed_canvas)

N_POINTS = 512


class FourierModule(StudioModule):
    id = "fourier"
    title = "Épicycles de Fourier"
    icon = "∿"
    category = "Mathématiques"
    description = "Dessinez à main levée : une FFT décompose le tracé en cercles tournants qui " \
                  "le redessinent. Spectre, erreur de reconstruction, formes prédéfinies."
    order = 12
    keywords = ("fft", "transformée", "cercles", "signal", "dessin")

    def build(self, frame: ttk.Frame) -> None:
        self.points: List[Tuple[float, float]] = fourier.shape("Cœur", N_POINTS)
        self.cycles: List[fourier.Epicycle] = []
        self.count = 40
        self.speed = 0.08
        self.t = 0.0
        self.trail: List[float] = []
        self.drawing: Optional[List[Tuple[float, float]]] = None
        self._trail_item: Optional[int] = None

        main, panel = side_layout(frame, 290, "Épicycles de Fourier",
                                  "Dessinez une forme fermée avec la souris : elle sera "
                                  "décomposée en série de Fourier.")
        self.canvas = themed_canvas(main, role="canvas", cursor="pencil")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._reset_trail())
        self.canvas.bind("<ButtonPress-1>", self._start_draw)
        self.canvas.bind("<B1-Motion>", self._draw_move)
        self.canvas.bind("<ButtonRelease-1>", self._end_draw)

        s = Section(panel, "Forme")
        s.pack(fill="x")
        self.shape_cb = combo(s.body, fourier.SHAPES, "Cœur", self._on_shape)
        self.shape_cb.pack(fill="x", pady=3)
        self.count_scale = LabeledScale(s.body, "Nombre de cercles", 1, N_POINTS, self.count,
                                        integer=True, log=True, command=self._on_count)
        self.count_scale.pack(fill="x", pady=(6, 0))
        LabeledScale(s.body, "Vitesse", 0.01, 0.5, self.speed, fmt="{:.2f}",
                     command=lambda v: setattr(self, "speed", v)).pack(fill="x")
        self.show_circles = check(s.body, "Afficher les cercles", True)
        self.show_original = check(s.body, "Afficher la forme d'origine", True)
        self.play = button_row(s.body, [("⏸ Pause", self.toggle), ("↺ Recommencer", self._reset_trail)],
                               accent_first=True)

        s = Section(panel, "Spectre")
        s.pack(fill="x")
        self.spectrum = themed_canvas(s.body, role="surface", height=120)
        self.spectrum.pack(fill="x", pady=3)
        self.stats = info_label(s.body, "")

        self.anim = self.loop(self._tick, fps=60, name="fourier.anim")
        self.anim.start()
        self._compute()

    # ------------------------------------------------------------------
    def _compute(self) -> None:
        pts = fourier.resample(self.points, N_POINTS)
        self.points = pts
        self.cycles = fourier.epicycles(pts)
        self._reset_trail()
        self._update_stats()
        self._draw_spectrum()

    def _update_stats(self) -> None:
        err = fourier.reconstruction_error(self.points[::4], self.cycles, self.count)
        energy = sum(a * a for _, a, _ in self.cycles) or 1.0
        kept = sum(a * a for _, a, _ in self.cycles[:self.count]) / energy
        self.stats.configure(text=f"Points       {len(self.points)} (FFT radix 2)\n"
                                  f"Cercles      {self.count}\n"
                                  f"Énergie      {kept:.2%}\n"
                                  f"Erreur moy.  {err:.4f}")

    def _draw_spectrum(self) -> None:
        c = self.spectrum
        t = self.theme
        c.update_idletasks()
        c.delete("all")
        c.configure(background=t.surface)
        w, h = max(200, c.winfo_width()), c.winfo_height()
        by_freq = {f: a for f, a, _ in self.cycles}
        top = max(by_freq.values()) or 1.0
        span = 40
        bw = w / (2 * span + 1)
        for f in range(-span, span + 1):
            a = by_freq.get(f, 0.0)
            x = (f + span) * bw
            bh = (h - 18) * math.sqrt(a / top)
            kept = any(cf == f for cf, _, _ in self.cycles[:self.count])
            color = t.accent if kept else mix(t.surface, t.fg2, 0.35)
            c.create_rectangle(x + 1, h - 14 - bh, x + bw - 1, h - 14, fill=color, outline="")
        c.create_text(2, h - 2, text=f"−{span}", anchor="sw", fill=t.muted, font="StudioSmall")
        c.create_text(w / 2, h - 2, text="0", anchor="s", fill=t.muted, font="StudioSmall")
        c.create_text(w - 2, h - 2, text=f"+{span}", anchor="se", fill=t.muted, font="StudioSmall")

    def _on_shape(self, name: str) -> None:
        self.points = fourier.shape(name, N_POINTS)
        self._compute()

    def _on_count(self, v: float) -> None:
        self.count = int(v)
        self._reset_trail()
        self._update_stats()
        self._draw_spectrum()

    def toggle(self) -> None:
        running = self.anim.toggle()
        self.play[0].configure(text="⏸ Pause" if running else "▶ Lecture")

    # ------------------------------------------------------------------
    def _frame(self) -> Tuple[float, float, float]:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return w / 2, h / 2, min(w, h) * 0.38

    def _reset_trail(self) -> None:
        self.t = 0.0
        self.trail = []
        self.canvas.delete("all")
        self._trail_item = None
        self._draw_original()

    def _draw_original(self) -> None:
        c = self.canvas
        t = self.theme
        c.configure(background=t.canvas)
        c.delete("orig")
        if not self.show_original.get() or len(self.points) < 2:
            return
        cx, cy, s = self._frame()
        pts = [v for x, y in self.points[::2] for v in (cx + x * s, cy + y * s)]
        pts += pts[:2]
        c.create_line(*pts, fill=mix(t.canvas, t.fg, 0.15), width=2, tags="orig")

    def _tick(self, dt: float) -> None:
        c = self.canvas
        t = self.theme
        cx, cy, s = self._frame()
        self.t += dt * self.speed
        if self.t >= 1.0:
            self.t -= 1.0
            self.trail = []
        chain = fourier.evaluate(self.cycles, self.t, self.count)
        c.delete("dyn")
        if self.show_circles.get():
            circle_color = mix(t.canvas, t.fg2, 0.35)
            for i in range(min(len(chain) - 1, 120)):
                (x0, y0), (x1, y1) = chain[i], chain[i + 1]
                r = math.hypot(x1 - x0, y1 - y0) * s
                X0, Y0 = cx + x0 * s, cy + y0 * s
                if r > 1.5:
                    c.create_oval(X0 - r, Y0 - r, X0 + r, Y0 + r, outline=circle_color, tags="dyn")
            pts = [v for x, y in chain for v in (cx + x * s, cy + y * s)]
            if len(pts) >= 4:
                c.create_line(*pts, fill=mix(t.canvas, t.fg, 0.6), width=1, tags="dyn")
        tip = chain[-1]
        X, Y = cx + tip[0] * s, cy + tip[1] * s
        self.trail += [X, Y]
        if len(self.trail) >= 4:
            if self._trail_item is None or not c.find_withtag("trail"):
                self._trail_item = c.create_line(*self.trail, fill=t.accent, width=2.5,
                                                 capstyle="round", joinstyle="round", tags="trail")
            else:
                c.coords(self._trail_item, *self.trail)
        c.create_oval(X - 4, Y - 4, X + 4, Y + 4, fill=t.color(1), outline="", tags="dyn")
        if self.drawing:
            pts = [v for p in self.drawing for v in p]
            if len(pts) >= 4:
                c.create_line(*pts, fill=t.color(2), width=2, tags="dyn")

    # ------------------------------------------------------------------
    # Dessin libre
    # ------------------------------------------------------------------
    def _start_draw(self, e: tk.Event) -> None:
        self.drawing = [(e.x, e.y)]

    def _draw_move(self, e: tk.Event) -> None:
        if self.drawing is not None:
            last = self.drawing[-1]
            if (e.x - last[0]) ** 2 + (e.y - last[1]) ** 2 > 9:
                self.drawing.append((e.x, e.y))

    def _end_draw(self, _e: tk.Event) -> None:
        pts = self.drawing or []
        self.drawing = None
        if len(pts) < 8:
            return
        cx, cy, s = self._frame()
        self.points = [((x - cx) / s, (y - cy) / s) for x, y in pts]
        self.shape_cb.var.set("")  # type: ignore[attr-defined]
        self._compute()
        self.notify(f"Tracé décomposé en {len(self.cycles)} harmoniques", "success")

    # ------------------------------------------------------------------
    def on_theme(self, theme) -> None:
        self._reset_trail()
        self._draw_spectrum()

    def commands(self) -> List[Command]:
        return [Command("fourier.toggle", "Pause / lecture des épicycles", self.toggle, icon="⏯")]

    def get_state(self) -> Dict[str, Any]:
        return {"points": [(round(x, 4), round(y, 4)) for x, y in self.points[::2]],
                "count": self.count}

    def set_state(self, state: Dict[str, Any]) -> None:
        pts = state.get("points")
        if isinstance(pts, list) and len(pts) >= 8:
            self.points = [(float(x), float(y)) for x, y in pts]
        self.count = int(state.get("count", self.count))
        self.count_scale.set(self.count)
        self._compute()

    def info(self) -> Dict[str, Any]:
        return {"cercles": self.count}

    def demo(self) -> None:
        names = fourier.SHAPES
        cur = self.shape_cb.var.get()  # type: ignore[attr-defined]
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.shape_cb.var.set(nxt)  # type: ignore[attr-defined]
        self._on_shape(nxt)
