"""L-systèmes : éditeur de grammaires, tortue graphique, croissance animée, export SVG."""

from __future__ import annotations

import random
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core.colors import gradient_hex, mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import lsystem
from ..engines.lsystem import LSystem
from ..ui.widgets import (LabeledScale, Section, Segmented, button_row, check, combo, info_label,
                          side_layout, themed_canvas)


def format_rules(rules: Dict[str, Any]) -> str:
    lines = []
    for k, v in rules.items():
        if isinstance(v, str):
            lines.append(f"{k} = {v}")
        else:
            lines.append(f"{k} = " + " | ".join(f"{p:g}:{r}" for p, r in v))
    return "\n".join(lines)


def parse_rules(text: str) -> Dict[str, Any]:
    rules: Dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"règle sans « = » : {line}")
        key, rhs = (part.strip() for part in line.split("=", 1))
        if len(key) != 1:
            raise ValueError(f"le symbole à réécrire doit être un seul caractère : {key!r}")
        if "|" in rhs or ":" in rhs:
            options = []
            for opt in rhs.split("|"):
                p, _, r = opt.partition(":")
                options.append((float(p), r.strip()))
            total = sum(p for p, _ in options) or 1.0
            rules[key] = [(p / total, r) for p, r in options]
        else:
            rules[key] = rhs.replace(" ", "")
    return rules


class LSystemModule(StudioModule):
    id = "lsystem"
    title = "L-systèmes"
    icon = "❦"
    category = "Graphisme"
    description = "Grammaires de Lindenmayer éditables (déterministes ou stochastiques) : " \
                  "plantes, flocons, courbes de Hilbert et du dragon ; croissance animée, SVG."
    order = 51
    keywords = ("fractale", "plante", "tortue", "grammaire", "koch", "dragon")

    def build(self, frame: ttk.Frame) -> None:
        self.system = lsystem.PRESETS[0]
        self.iterations = self.system.iterations
        self.angle = self.system.angle
        self.jitter = 0.0
        self.seed = 7
        self.color_mode = "depth"
        self.width_px = 1.4
        self.segments: List[Tuple[float, float, float, float, int, float]] = []
        self.polylines: List[Tuple[List[float], str, float]] = []
        self.drawn = 0
        self.length = 0

        main, panel = side_layout(frame, 300, "L-systèmes",
                                  "Modifiez l'axiome et les règles, puis « Appliquer ».")
        self.canvas = themed_canvas(main, role="canvas")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.regenerate(animate=False))

        s = Section(panel, "Grammaire")
        s.pack(fill="x")
        self.preset_cb = combo(s.body, [p.name for p in lsystem.PRESETS], self.system.name,
                               self._on_preset)
        self.preset_cb.pack(fill="x", pady=3)
        self.desc = ttk.Label(s.body, text="", style="Muted.TLabel", wraplength=260, justify="left")
        self.desc.pack(anchor="w")
        ttk.Label(s.body, text="Axiome", style="Secondary.TLabel").pack(anchor="w", pady=(6, 0))
        self.axiom_var = tk.StringVar()
        ttk.Entry(s.body, textvariable=self.axiom_var, font="StudioMono").pack(fill="x", pady=2)
        ttk.Label(s.body, text="Règles (X = …, ou p:… | p:… stochastique)",
                  style="Secondary.TLabel").pack(anchor="w", pady=(6, 0))
        self.rules_text = tk.Text(s.body, height=4, font="StudioMono", wrap="none",
                                  relief="flat", padx=6, pady=4)
        self.rules_text.pack(fill="x", pady=2)
        button_row(s.body, [("Appliquer", self.apply_grammar)], accent_first=True)

        s = Section(panel, "Paramètres")
        s.pack(fill="x")
        self.iter_scale = LabeledScale(s.body, "Itérations", 1, self.system.max_iterations,
                                       self.iterations, integer=True, command=self._on_iter)
        self.iter_scale.pack(fill="x")
        self.angle_scale = LabeledScale(s.body, "Angle", 1, 180, self.angle, fmt="{:.1f}°",
                                        command=self._on_angle)
        self.angle_scale.pack(fill="x")
        LabeledScale(s.body, "Variation aléatoire de l'angle", 0, 15, 0, fmt="{:.1f}°",
                     command=self._on_jitter).pack(fill="x")
        LabeledScale(s.body, "Épaisseur du trait", 0.5, 4, self.width_px, fmt="{:.1f}",
                     command=self._on_width).pack(fill="x")
        self.color_seg = Segmented(s.body, [("depth", "Profondeur"), ("order", "Ordre"),
                                            ("mono", "Uni")], "depth", self._on_color)
        self.color_seg.pack(fill="x", pady=3)
        self.grow = check(s.body, "Croissance animée", True)
        button_row(s.body, [("Nouvelle graine", self.reseed), ("Exporter SVG…", self.export_svg)])
        self.stats = info_label(panel, "", "Mono.TLabel")
        self.stats.pack_configure(padx=12)

        self.anim = self.loop(self._grow_tick, fps=60, name="lsystem.grow")
        self._load_preset(self.system)

    # ------------------------------------------------------------------
    def _load_preset(self, system: LSystem) -> None:
        self.system = system
        self.iterations = system.iterations
        self.angle = system.angle
        self.axiom_var.set(system.axiom)
        self.rules_text.delete("1.0", "end")
        self.rules_text.insert("1.0", format_rules(system.rules))
        self.desc.configure(text=system.description)
        self.iter_scale.scale.configure(to=system.max_iterations)
        self.iter_scale.set(self.iterations)
        self.angle_scale.set(self.angle)
        self.regenerate()

    def _on_preset(self, name: str) -> None:
        self._load_preset(next(p for p in lsystem.PRESETS if p.name == name))

    def apply_grammar(self) -> None:
        try:
            rules = parse_rules(self.rules_text.get("1.0", "end"))
        except ValueError as e:
            self.notify(str(e), "warning")
            return
        axiom = self.axiom_var.get().strip() or "F"
        self.system = LSystem("Personnalisé", axiom, rules, self.angle, self.iterations,
                              start_angle=self.system.start_angle,
                              max_iterations=self.system.max_iterations)
        self.regenerate()

    def _on_iter(self, v: float) -> None:
        if int(v) != self.iterations:
            self.iterations = int(v)
            self.regenerate()

    def _on_angle(self, v: float) -> None:
        self.angle = v
        self.regenerate(animate=False)

    def _on_jitter(self, v: float) -> None:
        self.jitter = v
        self.regenerate(animate=False)

    def _on_width(self, v: float) -> None:
        self.width_px = v
        self.regenerate(animate=False)

    def _on_color(self, v: str) -> None:
        self.color_mode = v
        self.regenerate(animate=False)

    def reseed(self) -> None:
        self.seed = random.randrange(1_000_000)
        self.regenerate()

    # ------------------------------------------------------------------
    def regenerate(self, animate: bool = True) -> None:
        try:
            s = self.system.expand(self.iterations, seed=self.seed)
        except ValueError as e:
            self.notify(str(e), "warning")
            return
        s = lsystem.normalize_drawing(s, self.system)
        self.length = len(s)
        self.segments = self.system.interpret(s, 10.0, self.angle,
                                              jitter=self.jitter * 0.0174533, seed=self.seed)
        self._build_polylines()
        self.canvas.delete("all")
        self.canvas.configure(background=self.theme.canvas)
        self.drawn = 0
        if animate and self.grow.get() and len(self.polylines) > 1:
            self.anim.start()
        else:
            self._draw_upto(len(self.polylines))
        self.stats.configure(text=f"symboles  {self.length:,}\nsegments  {len(self.segments):,}\n"
                                  f"tracés    {len(self.polylines):,}".replace(",", " "))

    def _build_polylines(self) -> None:
        """Fusionne les segments contigus de même couleur en polylignes (bien plus rapide)."""
        t = self.theme
        segs = self.segments
        self.polylines = []
        if not segs:
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 80 or h < 80:  # canevas pas encore dimensionné
            return
        x0, y0, x1, y1 = lsystem.bounds(segs)
        margin = 30
        scale = min((w - 2 * margin) / max(1e-9, x1 - x0), (h - 2 * margin) / max(1e-9, y1 - y0))
        ox = margin + ((w - 2 * margin) - (x1 - x0) * scale) / 2 - x0 * scale
        oy = margin + ((h - 2 * margin) - (y1 - y0) * scale) / 2 - y0 * scale
        max_depth = max(s[4] for s in segs) or 1
        buckets = 32
        if self.color_mode == "mono":
            palette = [t.accent] * buckets
        else:
            palette = gradient_hex([(0.0, mix(t.color(5), t.canvas, 0.1)), (0.45, t.color(2)),
                                    (0.75, t.accent), (1.0, t.color(4))], buckets)
        n = len(segs)
        cur: List[float] = []
        cur_key: Optional[Tuple[int, float]] = None
        last_end: Optional[Tuple[float, float]] = None
        for i, (ax, ay, bx, by, depth, width) in enumerate(segs):
            if self.color_mode == "order":
                bucket = min(buckets - 1, i * buckets // n)
            else:
                bucket = min(buckets - 1, depth * (buckets - 1) // max_depth)
            key = (bucket, round(width, 3))
            sx, sy = ox + ax * scale, oy + ay * scale
            ex, ey = ox + bx * scale, oy + by * scale
            if key == cur_key and last_end is not None and \
                    abs(last_end[0] - sx) < 0.01 and abs(last_end[1] - sy) < 0.01:
                cur += [ex, ey]
            else:
                if len(cur) >= 4 and cur_key is not None:
                    self.polylines.append((cur, palette[cur_key[0]], cur_key[1]))
                cur = [sx, sy, ex, ey]
                cur_key = key
            last_end = (ex, ey)
        if len(cur) >= 4 and cur_key is not None:
            self.polylines.append((cur, palette[cur_key[0]], cur_key[1]))

    def _draw_upto(self, n: int) -> None:
        c = self.canvas
        for pts, color, width in self.polylines[self.drawn:n]:
            c.create_line(*pts, fill=color, width=max(0.5, self.width_px * width),
                          capstyle="round", joinstyle="round")
        self.drawn = n

    def _grow_tick(self, _dt: float) -> None:
        total = len(self.polylines)
        step = max(1, total // 90)
        self._draw_upto(min(total, self.drawn + step))
        if self.drawn >= total:
            self.anim.stop()

    def export_svg(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".svg",
                                            filetypes=[("Image SVG", "*.svg")])
        if not path:
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
               f'viewBox="0 0 {w} {h}">',
               f'<rect width="100%" height="100%" fill="{self.theme.canvas}"/>']
        for pts, color, width in self.polylines:
            coords = " ".join(f"{pts[i]:.2f},{pts[i + 1]:.2f}" for i in range(0, len(pts), 2))
            out.append(f'<polyline points="{coords}" fill="none" stroke="{color}" '
                       f'stroke-width="{self.width_px * width:.2f}" stroke-linecap="round" '
                       f'stroke-linejoin="round"/>')
        out.append("</svg>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out))
        self.notify("SVG enregistré", "success")

    # ------------------------------------------------------------------
    def on_theme(self, theme) -> None:
        self.regenerate(animate=False)

    def commands(self) -> List[Command]:
        return [Command("lsystem.seed", "Nouvelle graine aléatoire", self.reseed, icon="⁂"),
                Command("lsystem.svg", "Exporter le L-système en SVG", self.export_svg, icon="⇩")]

    def get_state(self) -> Dict[str, Any]:
        return {"preset": self.system.name, "iterations": self.iterations, "angle": self.angle,
                "axiom": self.axiom_var.get(), "rules": self.rules_text.get("1.0", "end-1c"),
                "seed": self.seed}

    def set_state(self, state: Dict[str, Any]) -> None:
        name = state.get("preset")
        preset = next((p for p in lsystem.PRESETS if p.name == name), None)
        if preset:
            self.preset_cb.var.set(name)  # type: ignore[attr-defined]
            self._load_preset(preset)
        self.seed = int(state.get("seed", self.seed))
        if name == "Personnalisé":
            self.axiom_var.set(state.get("axiom", "F"))
            self.rules_text.delete("1.0", "end")
            self.rules_text.insert("1.0", state.get("rules", ""))
            self.apply_grammar()
        it = state.get("iterations")
        if isinstance(it, int):
            self.iter_scale.set(it)
            self.iterations = it
        ang = state.get("angle")
        if isinstance(ang, (int, float)):
            self.angle_scale.set(ang)
            self.angle = float(ang)
        self.regenerate(animate=False)

    def info(self) -> Dict[str, Any]:
        return {"segments": len(self.segments)}

    def demo(self) -> None:
        names = [p.name for p in lsystem.PRESETS]
        cur = self.preset_cb.var.get()  # type: ignore[attr-defined]
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.preset_cb.var.set(nxt)  # type: ignore[attr-defined]
        self._on_preset(nxt)
