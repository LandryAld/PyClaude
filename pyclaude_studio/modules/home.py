"""Accueil : présentation des modules sous forme de cartes interactives."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import List, Tuple

from ..core.colors import mix
from ..core.module import StudioModule
from ..engines.fractal import numpy_available
from ..ui.widgets import themed_canvas

TIPS = [
    "Ctrl+Maj+P ouvre la palette : tapez « fra » pour trouver les fractales, « thè » pour "
    "les thèmes — la recherche ignore accents et majuscules.",
    "Le panneau inférieur (Ctrl+J) contient une vraie console Python : essayez "
    "app.open_module('physics') ou expr.derive(expr.parse('x^x'), 'x').",
    "Glissez les onglets pour les réordonner ; clic du milieu pour fermer ; "
    "Ctrl+Maj+T rouvre le dernier onglet fermé.",
    "Les sessions (onglets, états de chaque module) sont restaurées au démarrage.",
    "Déposez un fichier .py dans le dossier « plugins » de la configuration pour ajouter "
    "votre propre module (voir examples/plugins).",
    "Outils › Visite guidée fait défiler automatiquement tous les modules en mode démo.",
]


class HomeModule(StudioModule):
    id = "home"
    title = "Accueil"
    icon = "⌂"
    category = "Studio"
    description = "Vue d'ensemble des modules et raccourcis essentiels."
    order = 0

    def build(self, frame: ttk.Frame) -> None:
        self.canvas = themed_canvas(frame, role="surface")
        self.canvas.pack(fill="both", expand=True)
        self.cards: List[Tuple[Tuple[float, float, float, float], str]] = []
        self.hover: str = ""
        self.tip = 0
        self.scroll = 0
        self.phase = 0.0
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(""))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", lambda e: self._scroll(-1 if e.delta > 0 else 1))
        self.canvas.bind("<Button-4>", lambda e: self._scroll(-1))
        self.canvas.bind("<Button-5>", lambda e: self._scroll(1))
        self.anim = self.loop(self._tick, fps=30, name="home.anim")
        self.anim.start()

    def _scroll(self, d: int) -> None:
        self.scroll = max(0, self.scroll + d * 50)
        self.redraw()

    def _tick(self, dt: float) -> None:
        if not self.app.settings.get("ui.animations"):
            return
        self.phase += dt
        self._draw_hero_art()

    def _set_hover(self, mid: str) -> None:
        if mid != self.hover:
            self.hover = mid
            self.canvas.configure(cursor="hand2" if mid else "")
            self.redraw()

    def _hit(self, x: float, y: float) -> str:
        for (x0, y0, x1, y1), mid in self.cards:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return mid
        return ""

    def _motion(self, e: tk.Event) -> None:
        self._set_hover(self._hit(e.x, e.y))

    def _click(self, e: tk.Event) -> None:
        mid = self._hit(e.x, e.y)
        if mid == "__tip__":
            self.tip = (self.tip + 1) % len(TIPS)
            self.redraw()
        elif mid:
            self.app.open_module(mid)

    def on_theme(self, theme) -> None:
        self.redraw()

    def on_activate(self) -> None:
        self.redraw()

    def _draw_hero_art(self) -> None:
        c = self.canvas
        t = self.theme
        c.delete("art")
        w = c.winfo_width()
        cx, cy = w - 150, 92 - self.scroll
        if cy < -80:
            return
        for k in range(3):
            pts = []
            for i in range(121):
                a = i / 120 * math.tau
                r = 46 + 14 * math.sin(3 * a + self.phase * (0.6 + k * 0.3) + k)
                pts += [cx + r * math.cos(a + k * 0.4), cy + r * 0.55 * math.sin(a + k * 0.4)]
            c.create_line(*pts, fill=mix(t.surface, t.color(k), 0.75), width=2, smooth=True,
                          tags="art")
        c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=t.accent, outline="", tags="art")

    def redraw(self) -> None:
        c = self.canvas
        t = self.theme
        c.configure(background=t.surface)
        c.delete("all")
        self.cards.clear()
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50:
            return
        pad = 32
        y = 30 - self.scroll
        c.create_text(pad, y, text="Bienvenue dans PyClaude Studio", anchor="nw", fill=t.fg,
                      font="StudioDisplay")
        specs = [s for s in self.app.registry.ordered() if s.id != self.id]
        np_text = "numpy détecté" if numpy_available() else "Python pur (numpy absent)"
        n = len(specs)
        sub = (f"{n} module{'s' if n > 1 else ''} · {len(self.app.commands.all())} commandes · "
               f"thème « {t.name} » · {np_text}")
        c.create_text(pad, y + 48, text=sub, anchor="nw", fill=t.muted, font="StudioBody")
        c.create_text(pad, y + 74, text="Un atelier scientifique et créatif : choisissez un "
                      "module ci-dessous ou ouvrez la palette avec Ctrl+Maj+P.", anchor="nw",
                      fill=t.fg2, font="StudioBody", width=w - 2 * pad - 260)
        self._draw_hero_art()
        y += 130
        cols = max(1, int((w - 2 * pad + 16) // 300))
        card_w = (w - 2 * pad - (cols - 1) * 16) / cols
        card_h = 132
        for i, spec in enumerate(specs):
            cls = spec.cls
            col, row = i % cols, i // cols
            x0 = pad + col * (card_w + 16)
            y0 = y + row * (card_h + 16)
            x1, y1 = x0 + card_w, y0 + card_h
            hovered = self.hover == cls.id
            opened = cls.id in self.app.instances
            fill = mix(t.surface2, t.accent, 0.08) if hovered else t.surface2
            outline = t.accent if hovered else t.border
            self._round_rect(x0, y0, x1, y1, 12, fill=fill, outline=outline)
            c.create_text(x0 + 18, y0 + 26, text=cls.icon, anchor="w", fill=t.accent,
                          font="StudioIconLarge")
            c.create_text(x0 + 56, y0 + 20, text=cls.title, anchor="w", fill=t.fg,
                          font="StudioHeading")
            c.create_text(x0 + 56, y0 + 40, text=cls.category, anchor="w", fill=t.muted,
                          font="StudioSmall")
            c.create_text(x0 + 18, y0 + 60, text=cls.description, anchor="nw", fill=t.fg2,
                          font="StudioSmall", width=card_w - 36)
            hint = f"Ctrl+{i + 1}" if i < 9 else ""
            if opened:
                hint = ("● ouvert   " + hint).strip()
            c.create_text(x1 - 14, y1 - 14, text=hint, anchor="se", fill=t.muted,
                          font="StudioSmall")
            self.cards.append(((x0, y0, x1, y1), cls.id))
        rows = (len(specs) + cols - 1) // cols
        y += rows * (card_h + 16) + 10
        # Astuce
        x0, x1 = pad, w - pad
        self._round_rect(x0, y, x1, y + 64, 12, fill=t.accent_soft, outline="")
        c.create_text(x0 + 18, y + 32, text="✦", anchor="w", fill=t.accent,
                      font="StudioIconLarge")
        c.create_text(x0 + 52, y + 32, text=TIPS[self.tip], anchor="w", fill=t.fg,
                      font="StudioBody", width=x1 - x0 - 160)
        c.create_text(x1 - 16, y + 32, text="astuce suivante ›", anchor="e", fill=t.accent,
                      font="StudioSmall")
        self.cards.append(((x0, y, x1, y + 64), "__tip__"))
        content_h = y + 64 + self.scroll + 30
        if content_h < h:
            self.scroll = 0
        else:
            self.scroll = min(self.scroll, int(content_h - h))

    def _round_rect(self, x0: float, y0: float, x1: float, y1: float, r: float, **kw) -> int:
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(*pts, smooth=True, **kw)

    def demo(self) -> None:
        self.tip = (self.tip + 1) % len(TIPS)
        self.redraw()
