"""Traceur de courbes : cartésiennes, polaires et paramétriques, analyse symbolique."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core import expr, numeric
from ..core.colors import mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..ui.widgets import (LabeledScale, Section, ToolTip, button_row, check, info_label,
                          side_layout, themed_canvas)

KINDS = {"cart": "y = f(x)", "polar": "r = f(θ)", "param": "x(t) ; y(t)"}
EXAMPLES = [
    ("cart", "sin(x) * exp(-x^2 / 10) * 3"),
    ("cart", "x^3 / 6 - x"),
    ("cart", "tan(x)"),
    ("polar", "2 * sin(4θ)"),
    ("param", "3 cos(3t) ; 2 sin(2t)"),
    ("cart", "a * sin(x / a)"),
]


class Curve:
    def __init__(self, kind: str, source: str, color: int, visible: bool = True) -> None:
        self.kind = kind
        self.source = source
        self.color = color
        self.visible = visible
        self.fns: List[expr.CompiledExpr] = []
        self.derivative: Optional[expr.CompiledExpr] = None
        self.derivative_text = ""
        self.error = ""
        self.compile()

    def compile(self) -> None:
        self.fns, self.derivative, self.error, self.derivative_text = [], None, "", ""
        src = self.source.strip()
        if not src:
            self.error = "expression vide"
            return
        try:
            if self.kind == "cart":
                for prefix in ("y=", "f(x)=", "y =", "f(x) ="):
                    if src.replace(" ", "").startswith(prefix.replace(" ", "")):
                        src = src.split("=", 1)[1]
                        break
                f = expr.compile_expr(src, ("x", "a"))
                self.fns = [f]
                try:
                    d = expr.simplify(expr.derive(f.node, "x"))
                    self.derivative = expr.CompiledExpr(d, ("x", "a"))
                    self.derivative_text = str(d)
                except expr.ExprError as e:
                    self.derivative_text = f"(non dérivable symboliquement : {e})"
            elif self.kind == "polar":
                src = src.split("=", 1)[1] if src.startswith("r=") or src.startswith("r =") else src
                self.fns = [expr.compile_expr(src, ("t", "a"))]
            else:
                parts = src.split(";")
                if len(parts) != 2:
                    raise expr.ExprError("séparez x(t) et y(t) par « ; »")
                self.fns = [expr.compile_expr(p, ("t", "a")) for p in parts]
        except expr.ExprError as e:
            self.error = str(e)
            self.fns = []

    @property
    def ok(self) -> bool:
        return bool(self.fns) and not self.error


class CurveRow(ttk.Frame):
    def __init__(self, parent: tk.Misc, module: "PlotterModule", curve: Curve) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.module = module
        self.curve = curve
        top = ttk.Frame(self, style="Panel.TFrame")
        top.pack(fill="x")
        self.swatch = themed_canvas(top, role="surface", width=14, height=14, cursor="hand2")
        self.swatch.pack(side="left", padx=(0, 6))
        self.swatch.bind("<Button-1>", lambda e: self._toggle())
        ToolTip(self.swatch, "Afficher / masquer")
        self.kind_var = tk.StringVar(value=KINDS[curve.kind])
        kind = ttk.Combobox(top, textvariable=self.kind_var, values=list(KINDS.values()),
                            state="readonly", width=9)
        kind.pack(side="left")
        kind.bind("<<ComboboxSelected>>", lambda e: self._kind_changed())
        ttk.Button(top, text="✕", style="Icon.TButton", width=2,
                   command=lambda: module.remove_curve(self)).pack(side="right")
        self.var = tk.StringVar(value=curve.source)
        self.entry = ttk.Entry(self, textvariable=self.var, font="StudioMono")
        self.entry.pack(fill="x", pady=(3, 0))
        self.err = ttk.Label(self, text="", style="Error.TLabel", wraplength=260,
                             font="StudioSmall")
        self.err.pack(anchor="w")
        self._after: Optional[str] = None
        self.var.trace_add("write", lambda *_: self._schedule())
        self.entry.bind("<FocusIn>", lambda e: module.select(self))
        self.refresh()

    def _toggle(self) -> None:
        self.curve.visible = not self.curve.visible
        self.refresh()
        self.module.redraw()

    def _kind_changed(self) -> None:
        label = self.kind_var.get()
        self.curve.kind = next(k for k, v in KINDS.items() if v == label)
        self.curve.compile()
        self.refresh()
        self.module.redraw()

    def _schedule(self) -> None:
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(250, self._apply)

    def _apply(self) -> None:
        self._after = None
        self.curve.source = self.var.get()
        self.curve.compile()
        self.refresh()
        self.module.select(self)
        self.module.redraw()

    def refresh(self) -> None:
        t = self.module.theme
        c = self.swatch
        c.configure(background=t.surface)
        c.delete("all")
        color = t.color(self.curve.color)
        if self.curve.visible:
            c.create_oval(1, 1, 13, 13, fill=color, outline=color)
        else:
            c.create_oval(1, 1, 13, 13, fill="", outline=color, width=2)
        self.err.configure(text=self.curve.error)


class PlotterModule(StudioModule):
    id = "plotter"
    title = "Traceur de courbes"
    icon = "ƒ"
    category = "Mathématiques"
    description = "Courbes cartésiennes, polaires et paramétriques ; dérivées symboliques, " \
                  "racines, extrema, intersections, intégrales et tangente au curseur."
    order = 11
    keywords = ("graphique", "fonction", "dérivée", "intégrale", "courbe")

    def build(self, frame: ttk.Frame) -> None:
        self.view = [-10.0, 10.0, -6.0, 6.0]
        self.curves: List[Curve] = []
        self.rows: List[CurveRow] = []
        self.selected: Optional[CurveRow] = None
        self.hover: Optional[Tuple[int, int]] = None
        self._drag: Optional[Tuple[int, int, List[float]]] = None
        self.param_a = 1.0
        self.a_dir = 1

        main, panel = side_layout(frame, 310, "Traceur",
                                  "Glisser : déplacer · molette : zoom (Ctrl : axe x, Maj : axe y)")
        self.canvas = themed_canvas(main, role="canvas", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: (setattr(self, "hover", None),
                                               self.canvas.delete("hover")))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", lambda e: self._wheel(e, 1 if e.delta > 0 else -1))
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.canvas.bind("<Double-Button-1>", lambda e: self.reset_view())

        s = Section(panel, "Fonctions")
        s.pack(fill="x")
        self.list_frame = ttk.Frame(s.body, style="Panel.TFrame")
        self.list_frame.pack(fill="x")
        button_row(s.body, [("+ Ajouter", lambda: self.add_curve("cart", "")),
                            ("Exemples", self._examples_menu)], accent_first=True)

        s = Section(panel, "Paramètre a")
        s.pack(fill="x")
        self.a_scale = LabeledScale(s.body, "a (utilisable dans les formules)", 0.1, 5,
                                    1.0, fmt="{:.2f}", command=self._on_a)
        self.a_scale.pack(fill="x")
        self.animate_a = check(s.body, "Animer a", False, self._toggle_anim)
        self.anim_loop = self.loop(self._anim_tick, fps=30, name="plotter.a")

        s = Section(panel, "Analyse")
        s.pack(fill="x")
        self.show_deriv = check(s.body, "Dérivée de la fonction sélectionnée", True,
                                lambda v: self.redraw())
        self.show_roots = check(s.body, "Racines", True, lambda v: self.redraw())
        self.show_ext = check(s.body, "Extrema locaux", True, lambda v: self.redraw())
        self.show_inter = check(s.body, "Intersections", True, lambda v: self.redraw())
        self.show_tangent = check(s.body, "Tangente au curseur", True, lambda v: self.redraw())
        self.deriv_lbl = info_label(s.body, "")
        self.deriv_lbl.configure(wraplength=280)

        s = Section(panel, "Intégrale de la sélection")
        s.pack(fill="x")
        row = ttk.Frame(s.body, style="Panel.TFrame")
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="de", style="Secondary.TLabel").pack(side="left")
        self.int_a = tk.StringVar(value="-2")
        self.int_b = tk.StringVar(value="2")
        ttk.Entry(row, textvariable=self.int_a, width=8).pack(side="left", padx=4)
        ttk.Label(row, text="à", style="Secondary.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.int_b, width=8).pack(side="left", padx=4)
        self.show_int = check(s.body, "Afficher l'aire", True, lambda v: self.redraw())
        for v in (self.int_a, self.int_b):
            v.trace_add("write", lambda *_: self.redraw())
        self.int_lbl = info_label(s.body, "")

        s = Section(panel, "Vue")
        s.pack(fill="x")
        button_row(s.body, [("Réinitialiser", self.reset_view), ("Repère orthonormé", self.equal_axes)])
        self.points_lbl = info_label(panel, "", "Muted.TLabel")
        self.points_lbl.configure(wraplength=280)

        for kind, src in EXAMPLES[:3]:
            self.add_curve(kind, src, redraw=False)

    # ------------------------------------------------------------------
    # Courbes
    # ------------------------------------------------------------------
    def _next_color(self) -> int:
        used = {c.color for c in self.curves}
        return next(i for i in range(64) if i not in used) % 8

    def add_curve(self, kind: str, source: str, visible: bool = True, color: Optional[int] = None,
                  redraw: bool = True) -> CurveRow:
        curve = Curve(kind, source, self._next_color() if color is None else color, visible)
        self.curves.append(curve)
        row = CurveRow(self.list_frame, self, curve)
        row.pack(fill="x", pady=(0, 8))
        self.rows.append(row)
        if not source:
            row.entry.focus_set()
        self.select(row)
        if redraw:
            self.redraw()
        return row

    def remove_curve(self, row: CurveRow) -> None:
        self.curves.remove(row.curve)
        self.rows.remove(row)
        row.destroy()
        if self.selected is row:
            self.selected = self.rows[0] if self.rows else None
        self.redraw()

    def select(self, row: CurveRow) -> None:
        self.selected = row
        c = row.curve
        if c.kind == "cart" and c.ok:
            self.deriv_lbl.configure(text=f"f'(x) = {c.derivative_text}")
        else:
            self.deriv_lbl.configure(text="(dérivée : fonctions cartésiennes uniquement)")

    def _examples_menu(self) -> None:
        menu = tk.Menu(self.frame, tearoff=False)
        for kind, src in EXAMPLES + [("cart", "floor(x) + frac(x)^2"),
                                     ("cart", "sqrt(9 - x^2)"), ("cart", "x > 0"),
                                     ("polar", "θ / 4"), ("param", "sin(3t) * 4 ; sin(4t) * 4"),
                                     ("cart", "gamma(x)"), ("cart", "sinc(3x) * 4")]:
            menu.add_command(label=f"{KINDS[kind]}   {src}",
                             command=lambda k=kind, s=src: self.add_curve(k, s))
        self.app.themes.recolor_tree(menu)
        menu.tk_popup(self.frame.winfo_pointerx(), self.frame.winfo_pointery())

    # ------------------------------------------------------------------
    # Repère
    # ------------------------------------------------------------------
    def _size(self) -> Tuple[int, int]:
        return max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())

    def to_screen(self, x: float, y: float) -> Tuple[float, float]:
        w, h = self._size()
        x0, x1, y0, y1 = self.view
        return (x - x0) / (x1 - x0) * w, h - (y - y0) / (y1 - y0) * h

    def to_world(self, sx: float, sy: float) -> Tuple[float, float]:
        w, h = self._size()
        x0, x1, y0, y1 = self.view
        return x0 + sx / w * (x1 - x0), y0 + (h - sy) / h * (y1 - y0)

    def reset_view(self) -> None:
        self.view = [-10.0, 10.0, -6.0, 6.0]
        self.equal_axes()

    def equal_axes(self) -> None:
        w, h = self._size()
        x0, x1, y0, y1 = self.view
        cy = (y0 + y1) / 2
        half = (x1 - x0) / w * h / 2
        self.view = [x0, x1, cy - half, cy + half]
        self.redraw()

    def _press(self, e: tk.Event) -> None:
        self._drag = (e.x, e.y, list(self.view))

    def _drag_move(self, e: tk.Event) -> None:
        if not self._drag:
            return
        sx, sy, v = self._drag
        w, h = self._size()
        dx = (e.x - sx) / w * (v[1] - v[0])
        dy = (e.y - sy) / h * (v[3] - v[2])
        self.view = [v[0] - dx, v[1] - dx, v[2] + dy, v[3] + dy]
        self.redraw()

    def _wheel(self, e: tk.Event, direction: int) -> None:
        f = 0.85 if direction > 0 else 1 / 0.85
        wx, wy = self.to_world(e.x, e.y)
        x0, x1, y0, y1 = self.view
        zoom_x = not (e.state & 0x1)       # Maj : axe y seul
        zoom_y = not (e.state & 0x4)       # Ctrl : axe x seul
        if zoom_x:
            x0, x1 = wx + (x0 - wx) * f, wx + (x1 - wx) * f
        if zoom_y:
            y0, y1 = wy + (y0 - wy) * f, wy + (y1 - wy) * f
        if x1 - x0 > 1e-9 and y1 - y0 > 1e-9 and x1 - x0 < 1e9:
            self.view = [x0, x1, y0, y1]
        self.redraw()

    def _motion(self, e: tk.Event) -> None:
        self.hover = (e.x, e.y)
        x, y = self.to_world(e.x, e.y)
        self.app.set_status(f"x = {numeric.format_value(x)}   y = {numeric.format_value(y)}")
        # Seul le calque de survol est redessiné (l'analyse reste en cache)
        self.canvas.delete("hover")
        self._draw_hover([cv for cv in self.curves if cv.ok and cv.visible and cv.kind == "cart"])

    # ------------------------------------------------------------------
    # Paramètre animé
    # ------------------------------------------------------------------
    def _on_a(self, v: float) -> None:
        self.param_a = v
        self.redraw()

    def _toggle_anim(self, on: bool) -> None:
        if on:
            self.anim_loop.start()
        else:
            self.anim_loop.stop()

    def _anim_tick(self, dt: float) -> None:
        a = self.param_a + self.a_dir * dt * 0.8
        if a > 5 or a < 0.1:
            self.a_dir *= -1
            a = max(0.1, min(5, a))
        self.param_a = a
        self.a_scale.set(a)
        self.redraw()

    # ------------------------------------------------------------------
    # Dessin
    # ------------------------------------------------------------------
    def _cart_segments(self, f: expr.CompiledExpr, step: int = 2) -> List[List[float]]:
        w, h = self._size()
        x0, x1, y0, y1 = self.view
        a = self.param_a
        segs: List[List[float]] = []
        cur: List[float] = []
        prev_y: Optional[float] = None
        span = y1 - y0
        for sx in range(0, w + step, step):
            x = x0 + sx / w * (x1 - x0)
            y = f(x, a)
            if not math.isfinite(y) or abs(y) > 1e9:
                if len(cur) >= 4:
                    segs.append(cur)
                cur, prev_y = [], None
                continue
            if prev_y is not None and abs(y - prev_y) > span * 2:
                # saut vertical : asymptote probable, on coupe le tracé
                if len(cur) >= 4:
                    segs.append(cur)
                cur = []
            sy = h - (y - y0) / span * h
            cur += [sx, max(-5000, min(5000, sy))]
            prev_y = y
        if len(cur) >= 4:
            segs.append(cur)
        return segs

    def _param_points(self, curve: Curve) -> List[List[float]]:
        a = self.param_a
        n = 1400
        segs: List[List[float]] = []
        cur: List[float] = []
        lo, hi = (0.0, math.tau * 2) if curve.kind == "polar" else (0.0, math.tau)
        for i in range(n + 1):
            t = lo + (hi - lo) * i / n
            if curve.kind == "polar":
                r = curve.fns[0](t, a)
                x, y = r * math.cos(t), r * math.sin(t)
            else:
                x, y = curve.fns[0](t, a), curve.fns[1](t, a)
            if not (math.isfinite(x) and math.isfinite(y)):
                if len(cur) >= 4:
                    segs.append(cur)
                cur = []
                continue
            sx, sy = self.to_screen(x, y)
            cur += [max(-5000, min(5000, sx)), max(-5000, min(5000, sy))]
        if len(cur) >= 4:
            segs.append(cur)
        return segs

    def redraw(self) -> None:
        c = self.canvas
        t = self.theme
        c.configure(background=t.canvas)
        c.delete("all")
        w, h = self._size()
        if w < 20:
            return
        x0, x1, y0, y1 = self.view
        # Quadrillage
        xstep, xticks = numeric.nice_ticks(x0, x1, max(4, w // 90))
        ystep, yticks = numeric.nice_ticks(y0, y1, max(4, h // 70))
        for xv in xticks:
            sx, _ = self.to_screen(xv, 0)
            c.create_line(sx, 0, sx, h, fill=t.grid)
        for yv in yticks:
            _, sy = self.to_screen(0, yv)
            c.create_line(0, sy, w, sy, fill=t.grid)
        ax_x, ax_y = self.to_screen(0, 0)
        ax_x = max(0, min(w - 1, ax_x))
        ax_y = max(0, min(h - 1, ax_y))
        c.create_line(0, ax_y, w, ax_y, fill=t.axis, width=1.5)
        c.create_line(ax_x, 0, ax_x, h, fill=t.axis, width=1.5)
        for xv in xticks:
            if xv == 0:
                continue
            sx, _ = self.to_screen(xv, 0)
            c.create_text(sx, min(h - 10, ax_y + 12), text=numeric.format_tick(xv, xstep),
                          fill=t.muted, font="StudioSmall")
        for yv in yticks:
            if yv == 0:
                continue
            _, sy = self.to_screen(0, yv)
            anchor = "w" if ax_x < 40 else "e"
            c.create_text(ax_x + (6 if anchor == "w" else -6), sy,
                          text=numeric.format_tick(yv, ystep), fill=t.muted, anchor=anchor,
                          font="StudioSmall")

        sel = self.selected.curve if self.selected else None
        notes: List[str] = []
        # Aire sous la courbe sélectionnée
        if sel and sel.ok and sel.kind == "cart" and self.show_int.get():
            self._draw_integral(sel, notes)
        cart = [cv for cv in self.curves if cv.ok and cv.visible and cv.kind == "cart"]
        for cv in self.curves:
            if not (cv.ok and cv.visible):
                continue
            color = t.color(cv.color)
            width = 3 if cv is sel else 2
            segs = self._cart_segments(cv.fns[0]) if cv.kind == "cart" else self._param_points(cv)
            for seg in segs:
                c.create_line(*seg, fill=color, width=width, capstyle="round", joinstyle="round")
            if cv is sel and cv.kind == "cart" and cv.derivative and self.show_deriv.get():
                for seg in self._cart_segments(cv.derivative, 3):
                    c.create_line(*seg, fill=color, width=1.5, dash=(6, 4))
        a = self.param_a
        if self.show_roots.get():
            for cv in cart:
                for r in numeric.find_roots(lambda x: cv.fns[0](x, a), x0, x1, 600)[:40]:
                    self._marker(r, 0.0, t.color(cv.color), "○")
                    notes.append(f"racine x = {numeric.format_value(r)}")
        if self.show_ext.get():
            for cv in cart:
                for xe, ye, kind in numeric.find_extrema(lambda x: cv.fns[0](x, a), x0, x1, 500)[:30]:
                    if y0 <= ye <= y1:
                        self._marker(xe, ye, t.color(cv.color), "▲" if kind == "max" else "▼")
                        notes.append(f"{kind} ({numeric.format_value(xe)}, {numeric.format_value(ye)})")
        if self.show_inter.get():
            for i in range(len(cart)):
                for j in range(i + 1, len(cart)):
                    f, g = cart[i].fns[0], cart[j].fns[0]
                    for xr in numeric.find_roots(lambda x: f(x, a) - g(x, a), x0, x1, 600)[:20]:
                        yr = f(xr, a)
                        if y0 <= yr <= y1:
                            self._marker(xr, yr, t.fg, "◆")
                            notes.append(f"∩ ({numeric.format_value(xr)}, {numeric.format_value(yr)})")
        self._draw_hover(cart)
        self.points_lbl.configure(text="\n".join(notes[:14]) + ("\n…" if len(notes) > 14 else ""))

    def _marker(self, x: float, y: float, color: str, symbol: str) -> None:
        sx, sy = self.to_screen(x, y)
        t = self.theme
        if symbol == "○":
            self.canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, outline=color, width=2,
                                    fill=t.canvas)
        elif symbol == "◆":
            self.canvas.create_polygon(sx, sy - 6, sx + 6, sy, sx, sy + 6, sx - 6, sy,
                                       fill=color, outline=t.canvas, width=2)
        else:
            d = -1 if symbol == "▲" else 1
            self.canvas.create_polygon(sx - 6, sy + 5 * -d, sx + 6, sy + 5 * -d, sx, sy + 7 * d,
                                       fill=color, outline=t.canvas, width=2)

    def _draw_integral(self, cv: Curve, notes: List[str]) -> None:
        try:
            a_lim = expr.evaluate(self.int_a.get())
            b_lim = expr.evaluate(self.int_b.get())
            a_lim, b_lim = float(a_lim), float(b_lim)
        except (expr.ExprError, TypeError, ValueError):
            self.int_lbl.configure(text="bornes invalides")
            return
        a = self.param_a
        f = cv.fns[0]
        value = numeric.simpson(lambda x: f(x, a), a_lim, b_lim, 2000)
        self.int_lbl.configure(text=f"∫ f(x) dx = {numeric.format_value(value, 8)}")
        lo, hi = sorted((a_lim, b_lim))
        n = 160
        pts: List[float] = []
        _, zero = self.to_screen(0, 0)
        for i in range(n + 1):
            x = lo + (hi - lo) * i / n
            y = f(x, a)
            if not math.isfinite(y):
                y = 0.0
            sx, sy = self.to_screen(x, y)
            pts += [sx, max(-5000, min(5000, sy))]
        sx_lo, _ = self.to_screen(lo, 0)
        sx_hi, _ = self.to_screen(hi, 0)
        poly = [sx_lo, zero] + pts + [sx_hi, zero]
        color = self.theme.color(cv.color)
        self.canvas.create_polygon(*poly, fill=mix(self.theme.canvas, color, 0.25), outline="")
        notes.append(f"aire [{numeric.format_value(lo)}, {numeric.format_value(hi)}] = "
                     f"{numeric.format_value(value)}")

    def _draw_hover(self, cart: List[Curve]) -> None:
        if not self.hover:
            return
        c = self.canvas
        t = self.theme
        w, h = self._size()
        hx, _ = self.hover
        x, _ = self.to_world(hx, 0)
        a = self.param_a
        c.create_line(hx, 0, hx, h, fill=t.muted, dash=(2, 4), tags="hover")
        lines = [f"x = {numeric.format_value(x)}"]
        for cv in cart:
            y = cv.fns[0](x, a)
            if not math.isfinite(y):
                continue
            sx, sy = self.to_screen(x, y)
            color = t.color(cv.color)
            if self.show_tangent.get() and self.selected and cv is self.selected.curve \
                    and cv.derivative:
                slope = cv.derivative(x, a)
                if math.isfinite(slope):
                    x0, x1, _, _ = self.view
                    span = (x1 - x0) * 0.18
                    p0 = self.to_screen(x - span, y - slope * span)
                    p1 = self.to_screen(x + span, y + slope * span)
                    c.create_line(*p0, *p1, fill=t.fg2, width=1.5, tags="hover")
                    lines.append(f"pente = {numeric.format_value(slope)}")
            c.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill=color, outline=t.canvas, width=2,
                          tags="hover")
            lines.append(f"{cv.source[:22]} = {numeric.format_value(y)}")
        text = "\n".join(lines)
        tx = hx + 14 if hx < w * 0.7 else hx - 14
        item = c.create_text(tx, 14, text=text, anchor="nw" if hx < w * 0.7 else "ne",
                             fill=t.fg, font="StudioSmall", justify="left", tags="hover")
        bb = c.bbox(item)
        if bb:
            rect = c.create_rectangle(bb[0] - 8, bb[1] - 6, bb[2] + 8, bb[3] + 6, fill=t.surface2,
                                      outline=t.border, tags="hover")
            c.tag_lower(rect, item)

    # ------------------------------------------------------------------
    def on_theme(self, theme) -> None:
        for row in self.rows:
            row.refresh()
        self.redraw()

    def commands(self) -> List[Command]:
        return [Command("plotter.add", "Ajouter une fonction", lambda: self.add_curve("cart", ""),
                        icon="+"),
                Command("plotter.reset", "Réinitialiser la vue", self.reset_view, icon="⟲"),
                Command("plotter.ortho", "Repère orthonormé", self.equal_axes, icon="⊞")]

    def get_state(self) -> Dict[str, Any]:
        return {"curves": [(c.kind, c.source, c.visible, c.color) for c in self.curves],
                "view": self.view, "a": self.param_a,
                "integral": [self.int_a.get(), self.int_b.get()]}

    def set_state(self, state: Dict[str, Any]) -> None:
        curves = state.get("curves")
        if curves:
            for row in list(self.rows):
                row.destroy()
            self.rows.clear()
            self.curves.clear()
            for kind, src, vis, color in curves:
                if kind in KINDS:
                    self.add_curve(kind, src, vis, color, redraw=False)
        view = state.get("view")
        if isinstance(view, list) and len(view) == 4:
            self.view = [float(v) for v in view]
        self.param_a = float(state.get("a", 1.0))
        self.a_scale.set(self.param_a)
        ia = state.get("integral")
        if ia and len(ia) == 2:
            self.int_a.set(ia[0])
            self.int_b.set(ia[1])
        self.redraw()

    def info(self) -> Dict[str, Any]:
        return {"courbes": len(self.curves)}

    def demo(self) -> None:
        kind, src = EXAMPLES[(len(self.curves)) % len(EXAMPLES)]
        self.add_curve(kind, src)
