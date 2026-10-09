"""Dessin vectoriel : formes, texte, symétrie kaléidoscopique, calques ordonnés,
annuler/rétablir par commandes, export SVG / PostScript, sauvegarde JSON."""

from __future__ import annotations

import json
import math
import os
import tkinter as tk
from dataclasses import asdict, dataclass
from tkinter import filedialog, simpledialog, ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core.colors import mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..ui.widgets import (ColorButton, LabeledScale, Section, ToolTip, button_row, check,
                          info_label, side_layout, themed_canvas)

TOOLS = [("select", "⬚", "Sélection et déplacement"), ("pen", "✎", "Crayon (lissé)"),
         ("line", "╱", "Ligne"), ("rect", "▭", "Rectangle"), ("oval", "◯", "Ellipse"),
         ("poly", "⬠", "Polygone (double-clic pour terminer)"), ("text", "T", "Texte"),
         ("eraser", "⌫", "Gomme (supprime la forme touchée)")]


@dataclass
class Shape:
    kind: str
    coords: List[float]
    stroke: str = "#7c6cff"
    fill: str = ""
    width: float = 3.0
    text: str = ""
    dash: bool = False
    size: int = 18
    uid: int = 0

    def moved(self, dx: float, dy: float) -> List[float]:
        return [v + (dx if i % 2 == 0 else dy) for i, v in enumerate(self.coords)]


class PaintModule(StudioModule):
    id = "paint"
    title = "Atelier de dessin"
    icon = "✎"
    category = "Graphisme"
    description = "Dessin vectoriel : crayon lissé, formes, texte, symétrie kaléidoscopique, " \
                  "sélection, annulation illimitée, export SVG et PostScript."
    order = 52
    keywords = ("paint", "dessin", "vectoriel", "svg", "kaléidoscope")

    def build(self, frame: ttk.Frame) -> None:
        self.shapes: List[Shape] = []
        self.items: Dict[int, int] = {}  # uid -> identifiant canevas
        self.undo_stack: List[Tuple[str, Any]] = []
        self.redo_stack: List[Tuple[str, Any]] = []
        self.next_uid = 1
        self.tool = "pen"
        self.stroke = self.theme.accent
        self.fill = ""
        self.width = 3.0
        self.dash = False
        self.symmetry = 1
        self.mirror = False
        self.grid_snap = False
        self.selected: Optional[Shape] = None
        self._start: Optional[Tuple[float, float]] = None
        self._points: List[float] = []
        self._preview: List[int] = []
        self._poly: List[float] = []
        self._move_origin: Optional[Tuple[float, float, List[float]]] = None

        main, panel = side_layout(frame, 280, "Atelier de dessin",
                                  "Suppr : effacer la sélection · Ctrl+Z / Ctrl+Y")
        toolbar = ttk.Frame(main, style="Toolbar.TFrame", padding=(8, 6))
        toolbar.pack(fill="x")
        self.tool_var = tk.StringVar(value=self.tool)
        for key, icon, tip in TOOLS:
            rb = ttk.Radiobutton(toolbar, text=icon, value=key, variable=self.tool_var,
                                 style="Toolbutton", width=3, command=self._on_tool)
            rb.pack(side="left", padx=1)
            ToolTip(rb, tip)
        self.canvas = themed_canvas(main, role="canvas", cursor="pencil")
        self.canvas.pack(fill="both", expand=True)
        c = self.canvas
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._motion)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Double-Button-1>", self._double)
        c.bind("<Motion>", self._hover)
        c.bind("<Delete>", lambda e: self.delete_selected())
        c.bind("<BackSpace>", lambda e: self.delete_selected())
        c.bind("<Escape>", lambda e: self._cancel_poly())
        c.bind("<Enter>", lambda e: c.focus_set())
        c.bind("<Configure>", lambda e: self._draw_guides())

        s = Section(panel, "Trait et remplissage")
        s.pack(fill="x")
        row = ttk.Frame(s.body, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Trait", style="Secondary.TLabel").pack(side="left")
        self.stroke_btn = ColorButton(row, self.stroke, command=self._on_stroke)
        self.stroke_btn.pack(side="left", padx=8)
        ttk.Label(row, text="Fond", style="Secondary.TLabel").pack(side="left", padx=(12, 0))
        self.fill_btn = ColorButton(row, mix(self.theme.accent, self.theme.canvas, 0.6),
                                    command=self._on_fill)
        self.fill_btn.pack(side="left", padx=8)
        self.fill_var = check(s.body, "Remplir les formes fermées", False,
                              lambda v: self._on_fill(self.fill_btn.color) if v else
                              setattr(self, "fill", ""))
        palette = ttk.Frame(s.body, style="Panel.TFrame")
        palette.pack(fill="x", pady=4)
        for i in range(8):
            sw = themed_canvas(palette, role="surface", width=24, height=24, cursor="hand2")
            sw.grid(row=0, column=i, padx=1)
            sw.bind("<Button-1>", lambda e, k=i: self._on_stroke(self.theme.color(k), sync=True))
            self._swatch(sw, i)
        self.palette_frame = palette
        LabeledScale(s.body, "Épaisseur", 1, 24, self.width, fmt="{:.0f} px",
                     command=lambda v: setattr(self, "width", v)).pack(fill="x", pady=(4, 0))
        check(s.body, "Pointillés", False, lambda v: setattr(self, "dash", v))

        s = Section(panel, "Symétrie (crayon)")
        s.pack(fill="x")
        self.sym_scale = LabeledScale(s.body, "Axes de rotation", 1, 16, 1, integer=True,
                                      command=self._on_symmetry)
        self.sym_scale.pack(fill="x")
        self.mirror_var = check(s.body, "Miroir (kaléidoscope)", False,
                                lambda v: (setattr(self, "mirror", v), self._draw_guides()))
        check(s.body, "Aimanter à la grille (20 px)", False,
              lambda v: (setattr(self, "grid_snap", v), self._draw_guides()))

        s = Section(panel, "Objet sélectionné")
        s.pack(fill="x")
        button_row(s.body, [("Premier plan", lambda: self._restack(True)),
                            ("Arrière-plan", lambda: self._restack(False))])
        button_row(s.body, [("Dupliquer", self.duplicate), ("Supprimer", self.delete_selected)])
        self.sel_lbl = info_label(s.body, "", "Muted.TLabel")

        s = Section(panel, "Fichier")
        s.pack(fill="x")
        button_row(s.body, [("Ouvrir…", self.load), ("Enregistrer…", self.save)])
        button_row(s.body, [("Export SVG…", self.export_svg), ("PostScript…", self.export_ps)])
        button_row(s.body, [("Tout effacer", self.clear)])
        self.stats = info_label(panel, "", "Muted.TLabel")
        self.stats.pack_configure(padx=12)
        self.after(200, self._demo_drawing)

    # ------------------------------------------------------------------
    # Modèle → canevas
    # ------------------------------------------------------------------
    def _swatch(self, canvas: tk.Canvas, i: int) -> None:
        canvas.delete("all")
        canvas.configure(background=self.theme.surface)
        canvas.create_oval(3, 3, 21, 21, fill=self.theme.color(i), outline="")

    def _create_item(self, s: Shape) -> int:
        c = self.canvas
        kw: Dict[str, Any] = {"width": s.width}
        if s.dash:
            kw["dash"] = (int(s.width * 2) + 2, int(s.width * 2) + 2)
        if s.kind in ("pen", "line"):
            return c.create_line(*s.coords, fill=s.stroke, capstyle="round", joinstyle="round",
                                 smooth=s.kind == "pen", splinesteps=12, **kw)
        if s.kind == "rect":
            return c.create_rectangle(*s.coords, outline=s.stroke, fill=s.fill, **kw)
        if s.kind == "oval":
            return c.create_oval(*s.coords, outline=s.stroke, fill=s.fill, **kw)
        if s.kind == "poly":
            return c.create_polygon(*s.coords, outline=s.stroke, fill=s.fill, joinstyle="round",
                                    **kw)
        return c.create_text(*s.coords[:2], text=s.text, fill=s.stroke, anchor="nw",
                             font=("TkDefaultFont", s.size, "bold"))

    def _add(self, s: Shape, index: Optional[int] = None) -> None:
        if not s.uid:
            s.uid = self.next_uid
            self.next_uid += 1
        else:
            self.next_uid = max(self.next_uid, s.uid + 1)
        if index is None:
            self.shapes.append(s)
        else:
            self.shapes.insert(index, s)
        self.items[s.uid] = self._create_item(s)
        if index is not None:
            self._sync_stacking()

    def _remove(self, s: Shape) -> int:
        idx = self.shapes.index(s)
        self.shapes.pop(idx)
        self.canvas.delete(self.items.pop(s.uid))
        if self.selected is s:
            self.select(None)
        return idx

    def _sync_stacking(self) -> None:
        for s in self.shapes:
            self.canvas.tag_raise(self.items[s.uid])
        self.canvas.tag_raise("selection")

    def _set_coords(self, s: Shape, coords: List[float]) -> None:
        s.coords = coords
        self.canvas.coords(self.items[s.uid], *coords)

    # ------------------------------------------------------------------
    # Annuler / rétablir (motif « commande »)
    # ------------------------------------------------------------------
    def _do(self, action: Tuple[str, Any], record: bool = True) -> None:
        kind, data = action
        if kind == "add":
            for s in data:
                self._add(s)
        elif kind == "remove":
            for s, _idx in data:
                self._remove(s)
        elif kind == "move":
            s, old, new = data
            self._set_coords(s, new)
        elif kind == "restack":
            s, old_idx, new_idx = data
            self.shapes.remove(s)
            self.shapes.insert(new_idx, s)
            self._sync_stacking()
        if record:
            self.undo_stack.append(action)
            del self.undo_stack[:-300]
            self.redo_stack.clear()
        self._update_stats()

    def _inverse(self, action: Tuple[str, Any]) -> Tuple[str, Any]:
        kind, data = action
        if kind == "add":
            return "remove", [(s, None) for s in data]
        if kind == "remove":
            return "readd", data
        if kind == "move":
            s, old, new = data
            return "move", (s, new, old)
        s, old_idx, new_idx = data
        return "restack", (s, new_idx, old_idx)

    def _apply_inverse(self, action: Tuple[str, Any]) -> None:
        inv = self._inverse(action)
        if inv[0] == "readd":
            for s, idx in sorted(inv[1], key=lambda p: p[1]):
                self._add(s, idx)
        else:
            self._do(inv, record=False)
        self._update_stats()

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self) -> None:
        if self.undo_stack:
            action = self.undo_stack.pop()
            self._apply_inverse(action)
            self.redo_stack.append(action)

    def redo(self) -> None:
        if self.redo_stack:
            action = self.redo_stack.pop()
            if action[0] == "remove":
                action = ("remove", [(s, self.shapes.index(s)) for s, _ in action[1]
                                     if s in self.shapes])
            self._do(action, record=False)
            self.undo_stack.append(action)

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _on_tool(self) -> None:
        self.tool = self.tool_var.get()
        self._cancel_poly()
        cursors = {"select": "arrow", "pen": "pencil", "eraser": "X_cursor", "text": "xterm"}
        self.canvas.configure(cursor=cursors.get(self.tool, "crosshair"))
        if self.tool != "select":
            self.select(None)

    def _on_stroke(self, color: str, sync: bool = False) -> None:
        self.stroke = color
        if sync:
            self.stroke_btn.set(color)
        if self.selected is not None and self.selected.kind != "text":
            self.selected.stroke = color
            item = self.items[self.selected.uid]
            opt = "fill" if self.selected.kind in ("pen", "line") else "outline"
            self.canvas.itemconfigure(item, **{opt: color})
        elif self.selected is not None:
            self.selected.stroke = color
            self.canvas.itemconfigure(self.items[self.selected.uid], fill=color)

    def _on_fill(self, color: str) -> None:
        self.fill = color if self.fill_var.get() else ""

    def _on_symmetry(self, v: float) -> None:
        self.symmetry = int(v)
        self._draw_guides()

    def _snap(self, x: float, y: float) -> Tuple[float, float]:
        if self.grid_snap:
            return round(x / 20) * 20, round(y / 20) * 20
        return x, y

    def _draw_guides(self) -> None:
        c = self.canvas
        t = self.theme
        c.delete("guide")
        w, h = c.winfo_width(), c.winfo_height()
        if self.grid_snap:
            for x in range(0, w, 20):
                c.create_line(x, 0, x, h, fill=mix(t.canvas, t.fg, 0.05), tags="guide")
            for y in range(0, h, 20):
                c.create_line(0, y, w, y, fill=mix(t.canvas, t.fg, 0.05), tags="guide")
        if self.symmetry > 1 or self.mirror:
            cx, cy = w / 2, h / 2
            r = math.hypot(w, h)
            n = max(1, self.symmetry)
            for k in range(n * (2 if self.mirror else 1)):
                a = math.pi * k / n if self.mirror else math.tau * k / n
                c.create_line(cx, cy, cx + r * math.cos(a), cy + r * math.sin(a),
                              fill=mix(t.canvas, t.accent, 0.25), dash=(3, 5), tags="guide")
        c.tag_lower("guide")

    def _symmetric(self, pts: List[float]) -> List[List[float]]:
        """Copies de la polyligne par rotation (et miroir) autour du centre du canevas."""
        if self.symmetry <= 1 and not self.mirror:
            return [pts]
        cx, cy = self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2
        out = []
        n = max(1, self.symmetry)
        for k in range(n):
            a = math.tau * k / n
            ca, sa = math.cos(a), math.sin(a)
            for mirror in ((False, True) if self.mirror else (False,)):
                copy = []
                for i in range(0, len(pts), 2):
                    x, y = pts[i] - cx, pts[i + 1] - cy
                    if mirror:
                        y = -y
                    copy += [cx + x * ca - y * sa, cy + x * sa + y * ca]
                out.append(copy)
        return out

    def _new_shape(self, kind: str, coords: List[float]) -> Shape:
        closed = kind in ("rect", "oval", "poly")
        return Shape(kind, coords, self.stroke, self.fill if closed else "", self.width,
                     dash=self.dash)

    def _press(self, e: tk.Event) -> None:
        x, y = self._snap(e.x, e.y)
        self._start = (x, y)
        if self.tool == "select":
            hit = self._shape_at(e.x, e.y)
            self.select(hit)
            if hit:
                self._move_origin = (x, y, list(hit.coords))
        elif self.tool == "pen":
            self._points = [x, y]
        elif self.tool == "eraser":
            self._erase_at(e.x, e.y)
        elif self.tool == "poly":
            self._poly += [x, y]
            self._preview_poly(x, y)
        elif self.tool == "text":
            text = simpledialog.askstring("Texte", "Texte à insérer :", parent=self.frame)
            if text:
                s = Shape("text", [x, y], self.stroke, text=text, size=int(10 + self.width * 2))
                self._do(("add", [s]))

    def _motion(self, e: tk.Event) -> None:
        if self._start is None:
            return
        x, y = self._snap(e.x, e.y)
        x0, y0 = self._start
        c = self.canvas
        for item in self._preview:
            c.delete(item)
        self._preview = []
        if self.tool == "select" and self.selected and self._move_origin:
            ox, oy, orig = self._move_origin
            moved = Shape(self.selected.kind, orig).moved(x - ox, y - oy)
            self._set_coords(self.selected, moved)
            self._draw_selection()
        elif self.tool == "pen":
            if (x - self._points[-2]) ** 2 + (y - self._points[-1]) ** 2 > 4:
                self._points += [x, y]
            for pts in self._symmetric(self._points):
                if len(pts) >= 4:
                    self._preview.append(c.create_line(*pts, fill=self.stroke, width=self.width,
                                                       capstyle="round", smooth=True))
        elif self.tool in ("line", "rect", "oval"):
            fn = {"line": c.create_line, "rect": c.create_rectangle, "oval": c.create_oval}[self.tool]
            kw: Dict[str, Any] = {"width": self.width}
            if self.tool == "line":
                kw["fill"] = self.stroke
            else:
                kw["outline"] = self.stroke
                kw["fill"] = self.fill
            if self.tool != "line" and (e.state & 0x1):  # Maj : carré / cercle
                d = max(abs(x - x0), abs(y - y0))
                x, y = x0 + math.copysign(d, x - x0), y0 + math.copysign(d, y - y0)
            self._preview.append(fn(x0, y0, x, y, **kw))
        elif self.tool == "eraser":
            self._erase_at(e.x, e.y)

    def _release(self, e: tk.Event) -> None:
        if self._start is None:
            return
        x, y = self._snap(e.x, e.y)
        x0, y0 = self._start
        self._start = None
        for item in self._preview:
            self.canvas.delete(item)
        self._preview = []
        if self.tool == "select" and self.selected and self._move_origin:
            orig = self._move_origin[2]
            if orig != self.selected.coords:
                new = list(self.selected.coords)
                self.selected.coords = orig
                self._do(("move", (self.selected, orig, new)))
            self._move_origin = None
        elif self.tool == "pen" and len(self._points) >= 4:
            shapes = [self._new_shape("pen", pts) for pts in self._symmetric(self._points)]
            self._do(("add", shapes))
        elif self.tool in ("line", "rect", "oval") and (abs(x - x0) > 2 or abs(y - y0) > 2):
            if self.tool != "line" and (e.state & 0x1):
                d = max(abs(x - x0), abs(y - y0))
                x, y = x0 + math.copysign(d, x - x0), y0 + math.copysign(d, y - y0)
            self._do(("add", [self._new_shape(self.tool, [x0, y0, x, y])]))
        self._points = []

    def _double(self, _e: tk.Event) -> None:
        if self.tool == "poly" and len(self._poly) >= 6:
            self._do(("add", [self._new_shape("poly", self._poly[:-2] if len(self._poly) > 6
                                              else self._poly)]))
        self._cancel_poly()

    def _preview_poly(self, x: float, y: float) -> None:
        c = self.canvas
        c.delete("polyprev")
        pts = self._poly + [x, y]
        if len(pts) >= 4:
            c.create_line(*pts, fill=self.stroke, width=self.width, dash=(4, 3), tags="polyprev")

    def _hover(self, e: tk.Event) -> None:
        if self.tool == "poly" and self._poly:
            self._preview_poly(*self._snap(e.x, e.y))

    def _cancel_poly(self) -> None:
        self._poly = []
        self.canvas.delete("polyprev")

    def _shape_at(self, x: float, y: float) -> Optional[Shape]:
        found = self.canvas.find_overlapping(x - 3, y - 3, x + 3, y + 3)
        by_item = {item: s for s in self.shapes for item in (self.items[s.uid],)}
        for item in reversed(found):
            if item in by_item:
                return by_item[item]
        return None

    def _erase_at(self, x: float, y: float) -> None:
        s = self._shape_at(x, y)
        if s is not None:
            self._do(("remove", [(s, self.shapes.index(s))]))

    # ------------------------------------------------------------------
    # Sélection
    # ------------------------------------------------------------------
    def select(self, shape: Optional[Shape]) -> None:
        self.selected = shape
        self._draw_selection()
        if shape:
            self.sel_lbl.configure(text=f"{shape.kind} · {len(shape.coords) // 2} point(s)")
        else:
            self.sel_lbl.configure(text="aucun")

    def _draw_selection(self) -> None:
        c = self.canvas
        c.delete("selection")
        if not self.selected:
            return
        bb = c.bbox(self.items[self.selected.uid])
        if bb:
            c.create_rectangle(bb[0] - 4, bb[1] - 4, bb[2] + 4, bb[3] + 4,
                               outline=self.theme.accent, dash=(4, 3), tags="selection")

    def delete_selected(self) -> None:
        if self.selected:
            s = self.selected
            self._do(("remove", [(s, self.shapes.index(s))]))

    def duplicate(self) -> None:
        if self.selected:
            s = self.selected
            copy = Shape(**{**asdict(s), "uid": 0, "coords": s.moved(20, 20)})
            self._do(("add", [copy]))
            self.select(copy)

    def _restack(self, front: bool) -> None:
        if not self.selected:
            return
        old = self.shapes.index(self.selected)
        new = len(self.shapes) - 1 if front else 0
        if old != new:
            self._do(("restack", (self.selected, old, new)))

    def clear(self) -> None:
        if self.shapes:
            self._do(("remove", [(s, i) for i, s in enumerate(self.shapes)][::-1]))

    def _update_stats(self) -> None:
        pts = sum(len(s.coords) // 2 for s in self.shapes)
        self.stats.configure(text=f"{len(self.shapes)} formes · {pts} points\n"
                                  f"{len(self.undo_stack)} action(s) annulable(s)")

    # ------------------------------------------------------------------
    # Fichiers
    # ------------------------------------------------------------------
    def to_svg(self) -> str:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
               f'viewBox="0 0 {w} {h}">',
               f'<rect width="100%" height="100%" fill="{self.theme.canvas}"/>']
        for s in self.shapes:
            dash = f' stroke-dasharray="{s.width * 2 + 2:.0f}"' if s.dash else ""
            common = (f'stroke="{s.stroke}" stroke-width="{s.width:.1f}" '
                      f'stroke-linecap="round" stroke-linejoin="round"{dash}')
            fill = s.fill or "none"
            c = s.coords
            if s.kind in ("pen", "line"):
                pts = " ".join(f"{c[i]:.1f},{c[i + 1]:.1f}" for i in range(0, len(c), 2))
                out.append(f'<polyline points="{pts}" fill="none" {common}/>')
            elif s.kind == "rect":
                x, y = min(c[0], c[2]), min(c[1], c[3])
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{abs(c[2] - c[0]):.1f}" '
                           f'height="{abs(c[3] - c[1]):.1f}" fill="{fill}" {common}/>')
            elif s.kind == "oval":
                out.append(f'<ellipse cx="{(c[0] + c[2]) / 2:.1f}" cy="{(c[1] + c[3]) / 2:.1f}" '
                           f'rx="{abs(c[2] - c[0]) / 2:.1f}" ry="{abs(c[3] - c[1]) / 2:.1f}" '
                           f'fill="{fill}" {common}/>')
            elif s.kind == "poly":
                pts = " ".join(f"{c[i]:.1f},{c[i + 1]:.1f}" for i in range(0, len(c), 2))
                out.append(f'<polygon points="{pts}" fill="{fill}" {common}/>')
            else:
                text = (s.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                out.append(f'<text x="{c[0]:.1f}" y="{c[1] + s.size:.1f}" fill="{s.stroke}" '
                           f'font-size="{s.size}" font-weight="bold" '
                           f'font-family="sans-serif">{text}</text>')
        out.append("</svg>")
        return "\n".join(out)

    def export_svg(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".svg",
                                            filetypes=[("Image SVG", "*.svg")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.to_svg())
            self.notify("Dessin exporté en SVG", "success")

    def export_ps(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".ps",
                                            filetypes=[("PostScript", "*.ps *.eps")])
        if path:
            self.canvas.delete("selection")
            self.canvas.postscript(file=path, colormode="color")
            self._draw_selection()
            self.notify("Dessin exporté en PostScript", "success")

    def save(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".json",
                                            filetypes=[("Dessin PyClaude", "*.json")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([asdict(s) for s in self.shapes], fh, indent=1)
            self.notify("Dessin enregistré", "success")

    def load(self) -> None:
        path = filedialog.askopenfilename(parent=self.frame, filetypes=[("Dessin PyClaude", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                shapes = [Shape(**d) for d in json.load(fh)]
        except (OSError, ValueError, TypeError) as exc:
            self.notify(f"Dessin illisible : {exc}", "error")
            return
        self.clear()
        for s in shapes:
            s.uid = 0
        self._do(("add", shapes))
        self.notify(f"{os.path.basename(path)} : {len(shapes)} formes", "success")

    def _demo_drawing(self) -> None:
        """Petite composition d'accueil (rosace kaléidoscopique)."""
        if self.shapes or getattr(self, "_restored", False):
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 100:
            self.after(200, self._demo_drawing)
            return
        cx, cy = w / 2, h / 2
        shapes = []
        for k in range(12):
            a = math.tau * k / 12
            pts = []
            for i in range(30):
                r = 30 + i * 7
                b = a + math.sin(i / 5) * 0.5
                pts += [cx + r * math.cos(b), cy + r * math.sin(b)]
            shapes.append(Shape("pen", pts, self.theme.color(k % 4), width=2.5))
        shapes.append(Shape("oval", [cx - 26, cy - 26, cx + 26, cy + 26], self.theme.accent,
                            mix(self.theme.accent, self.theme.canvas, 0.5), 3))
        for s in shapes:
            self._add(s)
        self._update_stats()

    # ------------------------------------------------------------------
    def on_activate(self) -> None:
        self.canvas.focus_set()

    def on_theme(self, theme) -> None:
        for i, sw in enumerate(self.palette_frame.winfo_children()):
            self._swatch(sw, i)  # type: ignore[arg-type]
        self._draw_guides()

    def commands(self) -> List[Command]:
        cmds = [Command("paint.svg", "Exporter le dessin en SVG", self.export_svg, icon="⇩"),
                Command("paint.clear", "Effacer le dessin", self.clear, icon="✕"),
                Command("paint.dup", "Dupliquer la sélection", self.duplicate, icon="⧉")]
        for key, icon, tip in TOOLS:
            cmds.append(Command(f"paint.tool.{key}", f"Outil : {tip}",
                                lambda k=key: (self.tool_var.set(k), self._on_tool()), icon=icon))
        return cmds

    def get_state(self) -> Dict[str, Any]:
        return {"shapes": [asdict(s) for s in self.shapes[-400:]]}

    def set_state(self, state: Dict[str, Any]) -> None:
        shapes = state.get("shapes")
        if not isinstance(shapes, list):
            return
        try:
            loaded = [Shape(**d) for d in shapes]
        except TypeError:
            return
        self._restored = True
        for s in loaded:
            self._add(s)
        self._update_stats()

    def info(self) -> Dict[str, Any]:
        return {"formes": len(self.shapes)}

    def demo(self) -> None:
        self.symmetry = 8
        self.mirror = True
        self.sym_scale.set(8)
        self.mirror_var.set(True)
        self._draw_guides()
