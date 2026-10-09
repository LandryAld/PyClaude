"""Terrain de jeu de réseau de neurones : entraînement en direct, frontière de décision,
activation de chaque neurone, courbes de perte."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core import imaging
from ..core.colors import gradient, mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import neural
from ..ui.widgets import (LabeledScale, LineChart, Section, Segmented, button_row, combo,
                          info_label, side_layout, themed_canvas)

EXTENT = 1.25  # le plan affiché couvre [-EXTENT, EXTENT]²


class NeuralModule(StudioModule):
    id = "neural"
    title = "Réseau de neurones"
    icon = "⊛"
    category = "Intelligence artificielle"
    description = "Perceptron multicouche écrit de zéro : rétropropagation, Adam, L2, " \
                  "caractéristiques dérivées ; frontière de décision et neurones en direct."
    order = 60
    keywords = ("ia", "machine learning", "mlp", "deep learning", "classification")

    def build(self, frame: ttk.Frame) -> None:
        self.dataset = "Spirales"
        self.noise = 0.08
        self.features = ["x", "y", "x²", "y²", "sin x", "sin y"]
        self.hidden = [8, 6]
        self.activation = "tanh"
        self.lr = 0.03
        self.optimizer = "Adam"
        self.batch = 16
        self.l2 = 0.0001
        self.epoch = 0
        self.photo: Optional[tk.PhotoImage] = None
        self.neuron_photos: List[tk.PhotoImage] = []
        self._last_heat = 0.0
        self._lut_key: Any = None

        main, panel = side_layout(frame, 300, "Réseau de neurones",
                                  "Clic sur le plan : ajouter un point (Maj : classe orange)")
        top = ttk.Frame(main, style="Module.TFrame")
        top.pack(fill="both", expand=True)
        self.plane = themed_canvas(top, role="canvas", width=420, height=420, cursor="tcross")
        self.plane.pack(side="left", padx=(10, 6), pady=10)
        self.plane.bind("<Button-1>", self._add_point)
        self.net_canvas = themed_canvas(top, role="canvas")
        self.net_canvas.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)
        self.net_canvas.bind("<Configure>", lambda e: self.draw_network())
        self.chart = LineChart(main, capacity=300, title="Perte (entropie croisée)", height=170,
                               y_min=0)
        self.chart.add_series("Entraînement", 0)
        self.chart.add_series("Test", 1)
        self.chart.pack(fill="x", padx=10, pady=(0, 10))

        s = Section(panel, "Entraînement")
        s.pack(fill="x")
        self.play = button_row(s.body, [("▶ Entraîner", self.toggle), ("Pas", self.step_once),
                                        ("↺", self.reset)], accent_first=True)
        self.status_lbl = info_label(s.body, "")
        self.opt_seg = Segmented(s.body, [("Adam", "Adam"), ("SGD", "SGD + moment")], "Adam",
                                 lambda v: setattr(self, "optimizer", v))
        self.opt_seg.pack(fill="x", pady=3)
        LabeledScale(s.body, "Taux d'apprentissage", 0.0005, 0.5, self.lr, log=True,
                     fmt="{:.4f}", command=lambda v: setattr(self, "lr", v)).pack(fill="x")
        LabeledScale(s.body, "Taille des lots", 1, 64, self.batch, integer=True,
                     command=lambda v: setattr(self, "batch", int(v))).pack(fill="x")
        LabeledScale(s.body, "Régularisation L2", 0.000001, 0.05, self.l2, log=True,
                     fmt="{:.6f}", command=lambda v: setattr(self, "l2", v)).pack(fill="x")

        s = Section(panel, "Données")
        s.pack(fill="x")
        self.data_cb = combo(s.body, neural.DATASETS, self.dataset, self._on_dataset)
        self.data_cb.pack(fill="x", pady=3)
        LabeledScale(s.body, "Bruit", 0.0, 0.4, self.noise, fmt="{:.2f}",
                     command=lambda v: setattr(self, "noise", v)).pack(fill="x")
        button_row(s.body, [("Nouvelles données", self.new_data)])

        s = Section(panel, "Architecture")
        s.pack(fill="x")
        ttk.Label(s.body, text="Caractéristiques d'entrée", style="Secondary.TLabel").pack(anchor="w")
        grid = ttk.Frame(s.body, style="Panel.TFrame")
        grid.pack(fill="x", pady=3)
        self.feat_vars: Dict[str, tk.BooleanVar] = {}
        for i, name in enumerate(neural.FEATURES):
            var = tk.BooleanVar(value=name in self.features)
            ttk.Checkbutton(grid, text=name, variable=var, command=self._on_features).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 6))
            self.feat_vars[name] = var
        ttk.Label(s.body, text="Couches cachées (ex. « 8, 6 »)", style="Secondary.TLabel").pack(
            anchor="w", pady=(6, 0))
        self.layers_var = tk.StringVar(value=", ".join(map(str, self.hidden)))
        ent = ttk.Entry(s.body, textvariable=self.layers_var, font="StudioMono")
        ent.pack(fill="x", pady=3)
        ent.bind("<Return>", lambda e: self._on_layers())
        ent.bind("<FocusOut>", lambda e: self._on_layers())
        self.act_cb = combo(s.body, list(neural.ACTIVATIONS), self.activation, self._on_activation)
        self.act_cb.pack(fill="x", pady=3)
        self.arch_lbl = info_label(s.body, "", "Muted.TLabel")

        self.train_loop = self.loop(self._tick, fps=30, name="neural.train")
        self.new_data()

    # ------------------------------------------------------------------
    # Modèle et données
    # ------------------------------------------------------------------
    def new_data(self) -> None:
        data = neural.make_dataset(self.dataset, 260, self.noise, seed=int(time.time()) % 10000)
        self.train, self.test = neural.split(data, 0.75)
        self.reset()

    def reset(self) -> None:
        sizes = [len(self.features)] + self.hidden + [1]
        self.net = neural.MLP(sizes, self.activation, seed=int(time.time() * 1000) % 100000)
        self.epoch = 0
        self.chart.clear()
        self._prepare()
        self.arch_lbl.configure(text=f"{' → '.join(map(str, sizes))}\n"
                                     f"{self.net.parameter_count} paramètres")
        self.draw_all(force=True)

    def _prepare(self) -> None:
        f = self.features
        self.Xtr = [neural.featurize(x, y, f) for x, y, _ in self.train]
        self.Ytr = [l for _, _, l in self.train]
        self.Xte = [neural.featurize(x, y, f) for x, y, _ in self.test]
        self.Yte = [l for _, _, l in self.test]

    def _on_dataset(self, name: str) -> None:
        self.dataset = name
        self.new_data()

    def _on_features(self) -> None:
        feats = [n for n, v in self.feat_vars.items() if v.get()]
        if not feats:
            self.feat_vars["x"].set(True)
            feats = ["x"]
        self.features = feats
        self.reset()

    def _on_layers(self) -> None:
        try:
            layers = [int(p) for p in self.layers_var.get().replace(";", ",").split(",") if p.strip()]
            if any(n < 1 or n > 16 for n in layers) or len(layers) > 5:
                raise ValueError
        except ValueError:
            self.notify("Couches : de 0 à 5 couches de 1 à 16 neurones (ex. « 8, 6 »)", "warning")
            self.layers_var.set(", ".join(map(str, self.hidden)))
            return
        if layers != self.hidden:
            self.hidden = layers
            self.reset()

    def _on_activation(self, name: str) -> None:
        self.activation = name
        self.reset()

    def _add_point(self, e: tk.Event) -> None:
        size = self.plane.winfo_width()
        x = (e.x / size * 2 - 1) * EXTENT
        y = (1 - e.y / size * 2) * EXTENT
        label = 0 if e.state & 0x1 else 1
        self.train.append((x, y, label))
        self._prepare()
        self.draw_plane(force=True)

    # ------------------------------------------------------------------
    # Entraînement
    # ------------------------------------------------------------------
    def toggle(self) -> None:
        running = self.train_loop.toggle()
        self.play[0].configure(text="⏸ Pause" if running else "▶ Entraîner")

    def _epoch(self) -> float:
        import random

        idx = list(range(len(self.Xtr)))
        random.shuffle(idx)
        losses = []
        for i in range(0, len(idx), self.batch):
            b = idx[i:i + self.batch]
            losses.append(self.net.train_batch([self.Xtr[j] for j in b], [self.Ytr[j] for j in b],
                                               self.lr, self.optimizer, self.l2))
        self.epoch += 1
        return sum(losses) / max(1, len(losses))

    def step_once(self) -> None:
        self._epoch()
        self._record()
        self.draw_all(force=True)

    def _tick(self, _dt: float) -> None:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 0.022:
            self._epoch()
        self._record()
        self.draw_all()

    def _record(self) -> None:
        tr = self.net.loss(self.Xtr, self.Ytr)
        te = self.net.loss(self.Xte, self.Yte) if self.Xte else math.nan
        self.chart.push("Entraînement", tr)
        self.chart.push("Test", te)
        acc_tr = self.net.accuracy(self.Xtr, self.Ytr)
        acc_te = self.net.accuracy(self.Xte, self.Yte)
        self.status_lbl.configure(text=f"Époque        {self.epoch}\n"
                                       f"Perte  entr.  {tr:.4f}   test {te:.4f}\n"
                                       f"Précision     {acc_tr:.0%}   test {acc_te:.0%}")

    # ------------------------------------------------------------------
    # Dessin
    # ------------------------------------------------------------------
    def _lut(self) -> Tuple[bytes, bytes, bytes]:
        t = self.theme
        key = (t.name,)
        if key != self._lut_key:
            self._lut_key = key
            mid = "#f0efec" if not t.dark else "#383835"
            c0, c1 = t.color(1), t.color(0)  # orange (classe 0) ↔ bleu (classe 1)
            lut = gradient([(0.0, mix(c0, mid, 0.3)), (0.5, mid), (1.0, mix(c1, mid, 0.3))], 256)
            self._tables = imaging.lut_to_tables(lut)
        return self._tables

    def draw_all(self, force: bool = False) -> None:
        now = time.perf_counter()
        if force or now - self._last_heat > 0.25:
            self._last_heat = now
            self.draw_plane(force=True)
            self.draw_network()
            self.chart.redraw()

    def draw_plane(self, force: bool = False) -> None:
        c = self.plane
        t = self.theme
        size = int(c.winfo_width()) or 420
        G = 42
        zoom = max(1, size // G)
        feats = self.features
        pred = self.net.predict
        buf = bytearray(G * G)
        for gy in range(G):
            y = (1 - (gy + 0.5) / G * 2) * EXTENT
            for gx in range(G):
                x = ((gx + 0.5) / G * 2 - 1) * EXTENT
                p = pred(neural.featurize(x, y, feats))
                buf[gy * G + gx] = min(255, max(0, int(p * 255)))
        rgb = imaging.index_to_rgb(bytes(buf), self._lut())
        self.photo = imaging.photo_image(c, G, G, rgb, zoom=zoom)
        c.delete("all")
        c.configure(background=t.canvas)
        c.create_image(0, 0, image=self.photo, anchor="nw")
        span = G * zoom
        # contour de décision p = 0,5 (cases dont le voisin change de classe)
        for gy in range(G):
            for gx in range(G - 1):
                a, b = buf[gy * G + gx] >= 128, buf[gy * G + gx + 1] >= 128
                if a != b:
                    x = (gx + 1) * zoom
                    c.create_line(x, gy * zoom, x, (gy + 1) * zoom, fill=t.fg, width=1.5)
        for gy in range(G - 1):
            for gx in range(G):
                a, b = buf[gy * G + gx] >= 128, buf[(gy + 1) * G + gx] >= 128
                if a != b:
                    y = (gy + 1) * zoom
                    c.create_line(gx * zoom, y, (gx + 1) * zoom, y, fill=t.fg, width=1.5)

        def to_px(x: float, y: float) -> Tuple[float, float]:
            return (x / EXTENT + 1) / 2 * span, (1 - y / EXTENT) / 2 * span

        for data, filled in ((self.train, True), (self.test, False)):
            for x, y, label in data:
                px, py = to_px(x, y)
                color = t.color(0) if label else t.color(1)
                if filled:
                    c.create_oval(px - 4, py - 4, px + 4, py + 4, fill=color, outline=t.fg,
                                  width=1)
                else:
                    c.create_oval(px - 4, py - 4, px + 4, py + 4, fill=t.fg, outline=color,
                                  width=2)
        c.create_text(8, span - 8, text="● entraînement   ○ test", anchor="sw", fill=t.fg,
                      font="StudioSmall")

    def draw_network(self) -> None:
        c = self.net_canvas
        t = self.theme
        c.delete("all")
        c.configure(background=t.canvas)
        w, h = c.winfo_width(), c.winfo_height()
        if w < 100 or not hasattr(self, "net"):
            return
        sizes = self.net.sizes
        layers = len(sizes)
        cell = 34
        xs = [60 + i * (w - 120) / max(1, layers - 1) for i in range(layers)]

        def ys(n: int) -> List[float]:
            gap = min(cell + 14, (h - 60) / max(1, n))
            top = h / 2 - gap * (n - 1) / 2
            return [top + i * gap for i in range(n)]

        pos = [[(xs[i], y) for y in ys(n)] for i, n in enumerate(sizes)]
        # Arêtes : couleur = signe, épaisseur = |poids|
        for li, W in enumerate(self.net.W):
            for j, row in enumerate(W):
                for k, wv in enumerate(row):
                    (x0, y0), (x1, y1) = pos[li][k], pos[li + 1][j]
                    mag = min(1.0, abs(wv) / 2.0)
                    color = mix(t.canvas, t.color(0) if wv > 0 else t.color(1), 0.25 + 0.75 * mag)
                    c.create_line(x0 + cell / 2, y0, x1 - cell / 2, y1, fill=color,
                                  width=0.6 + 3.2 * mag)
        # Activation de chaque neurone sur le plan (mini-cartes)
        G = 17
        grids: List[List[List[float]]] = [[[] for _ in range(n)] for n in sizes]
        feats = self.features
        for gy in range(G):
            y = (1 - (gy + 0.5) / G * 2) * EXTENT
            for gx in range(G):
                x = ((gx + 0.5) / G * 2 - 1) * EXTENT
                _, acts = self.net.forward(neural.featurize(x, y, feats))
                for li, layer in enumerate(acts):
                    for ni, a in enumerate(layer):
                        grids[li][ni].append(a)
        self.neuron_photos = []
        tables = self._lut()
        for li, layer_pos in enumerate(pos):
            for ni, (x, y) in enumerate(layer_pos):
                if li == 0:
                    c.create_text(x - G - 6, y, text=feats[ni], anchor="e", fill=t.fg2,
                                  font="StudioSmall")
                vals = grids[li][ni]
                if li == layers - 1:
                    lo, hi = 0.0, 1.0
                else:
                    m = max(1e-6, max(abs(v) for v in vals))
                    lo, hi = -m, m
                idx = bytes(min(255, max(0, int((v - lo) / (hi - lo) * 255))) for v in vals)
                photo = imaging.photo_image(c, G, G, imaging.index_to_rgb(idx, tables), zoom=2)
                self.neuron_photos.append(photo)
                c.create_image(x, y, image=photo)
                c.create_rectangle(x - G, y - G, x + G, y + G, outline=t.border)
        for i, n in enumerate(sizes):
            label = "entrée" if i == 0 else ("sortie" if i == layers - 1 else f"cachée {i}")
            c.create_text(xs[i], 16, text=f"{label} ({n})", fill=t.muted, font="StudioSmall")

    # ------------------------------------------------------------------
    def on_theme(self, theme) -> None:
        self._lut_key = None
        self.draw_all(force=True)

    def commands(self) -> List[Command]:
        return [Command("neural.train", "Entraîner / pause", self.toggle, icon="▶"),
                Command("neural.reset", "Réinitialiser les poids", self.reset, icon="↺"),
                Command("neural.data", "Nouvelles données", self.new_data, icon="⁂")]

    def get_state(self) -> Dict[str, Any]:
        return {"dataset": self.dataset, "features": self.features, "hidden": self.hidden,
                "activation": self.activation, "lr": self.lr}

    def set_state(self, state: Dict[str, Any]) -> None:
        if state.get("dataset") in neural.DATASETS:
            self.dataset = state["dataset"]
            self.data_cb.var.set(self.dataset)  # type: ignore[attr-defined]
        feats = [f for f in state.get("features", []) if f in neural.FEATURES]
        if feats:
            self.features = feats
            for n, v in self.feat_vars.items():
                v.set(n in feats)
        hidden = state.get("hidden")
        if isinstance(hidden, list) and all(isinstance(n, int) and 1 <= n <= 16 for n in hidden):
            self.hidden = hidden[:5]
            self.layers_var.set(", ".join(map(str, self.hidden)))
        if state.get("activation") in neural.ACTIVATIONS:
            self.activation = state["activation"]
            self.act_cb.var.set(self.activation)  # type: ignore[attr-defined]
        self.new_data()

    def info(self) -> Dict[str, Any]:
        return {"époque": self.epoch, "paramètres": self.net.parameter_count}

    def demo(self) -> None:
        if not self.train_loop.running:
            self.toggle()
