"""Tableur : grille virtuelle sur canevas, formules réactives, annulation, graphiques."""

from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core import numeric
from ..core.colors import mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import spreadsheet as ss
from ..engines.spreadsheet import Pos, Sheet, col_name, parse_ref, ref_name
from ..ui.widgets import Segmented, ToolTip, themed_canvas

ROW_H = 26
HEAD_W = 52
DEFAULT_COL_W = 104


class SpreadsheetModule(StudioModule):
    id = "spreadsheet"
    title = "Tableur"
    icon = "▦"
    category = "Données"
    description = "Formules réactives (SOMME, SI, NB.SI, RECHERCHEV…), graphe de dépendances, " \
                  "détection des cycles, copier-coller intelligent, graphiques."
    order = 20
    keywords = ("excel", "calc", "feuille", "formule", "csv")

    def build(self, frame: ttk.Frame) -> None:
        self.sheet = Sheet(rows=500, cols=52)
        self.sheet.load_dict(ss.DEMO)
        self.col_w: Dict[int, int] = {0: 150, 4: 90}
        self.top = 0
        self.left = 0
        self.anchor: Pos = (0, 0)
        self.cursor: Pos = (0, 0)
        self.undo_stack: List[Dict[Pos, Tuple[str, str]]] = []
        self.redo_stack: List[Dict[Pos, Tuple[str, str]]] = []
        self.clip: Optional[Tuple[List[List[str]], Pos]] = None
        self.editor: Optional[tk.Entry] = None
        self.edit_pos: Optional[Pos] = None
        self._resize: Optional[Tuple[int, int, int]] = None
        self._dragging = False
        self.path: Optional[str] = None

        # Barre d'outils
        bar = ttk.Frame(frame, style="Toolbar.TFrame", padding=(10, 8))
        bar.pack(fill="x")
        self.chart_mode = Segmented(bar, [("none", "Aucun"), ("bar", "Barres"),
                                          ("line", "Courbes")], "line", lambda v: self._chart_toggle())
        self.chart_mode.pack(side="right")
        ttk.Label(bar, text="Graphique", style="Muted.TLabel").pack(side="right", padx=8)
        for text, cmd, tip in (("Nouveau", self.new, "Feuille vide"),
                               ("Ouvrir…", self.open_file, "CSV ou JSON"),
                               ("Enregistrer…", self.save_file, "JSON (formules) ou CSV"),
                               ("Exporter valeurs…", self.export_values, "CSV des valeurs calculées")):
            b = ttk.Button(bar, text=text, style="Ghost.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            ToolTip(b, tip)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        for text, cmd, tip in (("↶", self.undo, "Annuler (Ctrl+Z)"),
                               ("↷", self.redo, "Rétablir (Ctrl+Y)"),
                               ("⇩ Remplir", self.fill_down, "Recopier vers le bas (Ctrl+D)"),
                               ("Σ", self.auto_sum, "Somme automatique sous la sélection")):
            b = ttk.Button(bar, text=text, style="Ghost.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            ToolTip(b, tip)

        fbar = ttk.Frame(frame, style="Toolbar.TFrame", padding=(10, 0, 10, 8))
        fbar.pack(fill="x")
        self.name_lbl = ttk.Label(fbar, text="A1", style="Mono.TLabel", width=8, anchor="center")
        self.name_lbl.pack(side="left")
        ttk.Label(fbar, text="ƒx", style="Accent.TLabel").pack(side="left", padx=(4, 8))
        self.formula = tk.StringVar()
        self.fentry = ttk.Entry(fbar, textvariable=self.formula, font="StudioMono")
        self.fentry.pack(side="left", fill="x", expand=True)
        self.fentry.bind("<Return>", lambda e: self._commit_formula())
        self.fentry.bind("<KP_Enter>", lambda e: self._commit_formula())
        self.fentry.bind("<Escape>", lambda e: (self._sync_formula(), self.canvas.focus_set()))

        body = ttk.Frame(frame, style="Module.TFrame")
        body.pack(fill="both", expand=True)
        self.chart_frame = ttk.Frame(body, style="Panel.TFrame", width=340)
        self.chart = themed_canvas(self.chart_frame, role="canvas", width=340)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        grid_wrap = ttk.Frame(body, style="Module.TFrame")
        grid_wrap.pack(side="left", fill="both", expand=True)
        self.vbar = ttk.Scrollbar(grid_wrap, orient="vertical", command=self._yview)
        self.hbar = ttk.Scrollbar(grid_wrap, orient="horizontal", command=self._xview)
        self.vbar.pack(side="right", fill="y")
        self.hbar.pack(side="bottom", fill="x")
        self.canvas = themed_canvas(grid_wrap, role="canvas", takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self._chart_toggle()

        self.stats = ttk.Label(frame, text="", style="Status.Muted.TLabel", padding=(10, 3))
        self.stats.pack(fill="x", side="bottom")

        c = self.canvas
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Double-Button-1>", lambda e: self.begin_edit())
        c.bind("<Motion>", self._hover_cursor)
        c.bind("<MouseWheel>", lambda e: self._scroll(-3 if e.delta > 0 else 3, e.state & 0x1))
        c.bind("<Button-4>", lambda e: self._scroll(-3, e.state & 0x1))
        c.bind("<Button-5>", lambda e: self._scroll(3, e.state & 0x1))
        c.bind("<Key>", self._key)
        for seq, fn in (("<Control-c>", self.copy), ("<Control-x>", self.cut),
                        ("<Control-v>", self.paste), ("<Control-z>", self.undo),
                        ("<Control-y>", self.redo), ("<Control-d>", self.fill_down),
                        ("<Control-a>", self.select_all)):
            c.bind(seq, lambda e, f=fn: (f(), "break")[1])
        self._sync_formula()

    # ------------------------------------------------------------------
    # Géométrie
    # ------------------------------------------------------------------
    def cw(self, col: int) -> int:
        return self.col_w.get(col, DEFAULT_COL_W)

    def _visible(self) -> Tuple[List[Tuple[int, int, int]], List[Tuple[int, int]]]:
        """Colonnes visibles (index, x, largeur) et lignes visibles (index, y)."""
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        cols, x = [], HEAD_W
        c = self.left
        while x < w and c < self.sheet.cols:
            cols.append((c, x, self.cw(c)))
            x += self.cw(c)
            c += 1
        rows, y = [], ROW_H
        r = self.top
        while y < h and r < self.sheet.rows:
            rows.append((r, y))
            y += ROW_H
            r += 1
        return cols, rows

    def cell_rect(self, pos: Pos) -> Optional[Tuple[int, int, int, int]]:
        r, c = pos
        if r < self.top or c < self.left:
            return None
        x = HEAD_W + sum(self.cw(i) for i in range(self.left, c))
        y = ROW_H + (r - self.top) * ROW_H
        return x, y, x + self.cw(c), y + ROW_H

    def cell_at(self, x: int, y: int) -> Optional[Pos]:
        if x < HEAD_W or y < ROW_H:
            return None
        r = self.top + (y - ROW_H) // ROW_H
        cx, c = HEAD_W, self.left
        while c < self.sheet.cols:
            if cx + self.cw(c) > x:
                return min(r, self.sheet.rows - 1), c
            cx += self.cw(c)
            c += 1
        return None

    def selection(self) -> Tuple[int, int, int, int]:
        (r0, c0), (r1, c1) = self.anchor, self.cursor
        return min(r0, r1), min(c0, c1), max(r0, r1), max(c0, c1)

    # ------------------------------------------------------------------
    # Dessin
    # ------------------------------------------------------------------
    def redraw(self) -> None:
        c = self.canvas
        t = self.theme
        c.configure(background=t.canvas)
        c.delete("grid")
        w, h = c.winfo_width(), c.winfo_height()
        cols, rows = self._visible()
        r0, c0, r1, c1 = self.selection()
        head_bg = t.surface
        sel_fill = mix(t.canvas, t.accent, 0.14)
        # en-têtes
        c.create_rectangle(0, 0, w, ROW_H, fill=head_bg, outline="", tags="grid")
        c.create_rectangle(0, 0, HEAD_W, h, fill=head_bg, outline="", tags="grid")
        for col, x, cw in cols:
            active = c0 <= col <= c1
            if active:
                c.create_rectangle(x, 0, x + cw, ROW_H, fill=mix(head_bg, t.accent, 0.25),
                                   outline="", tags="grid")
            c.create_text(x + cw / 2, ROW_H / 2, text=col_name(col),
                          fill=t.fg if active else t.muted, font="StudioBold" if active else "StudioSmall",
                          tags="grid")
            c.create_line(x + cw, 0, x + cw, h, fill=t.grid, tags="grid")
        for row, y in rows:
            active = r0 <= row <= r1
            if active:
                c.create_rectangle(0, y, HEAD_W, y + ROW_H, fill=mix(head_bg, t.accent, 0.25),
                                   outline="", tags="grid")
            c.create_text(HEAD_W / 2, y + ROW_H / 2, text=str(row + 1),
                          fill=t.fg if active else t.muted, font="StudioSmall", tags="grid")
            c.create_line(0, y + ROW_H, w, y + ROW_H, fill=t.grid, tags="grid")
        # sélection
        sel = self._range_rect(r0, c0, r1, c1)
        if sel:
            c.create_rectangle(*sel, fill=sel_fill, outline="", tags="grid")
        # références de la formule active (encadrés colorés façon Excel)
        cell = self.sheet.cell(self.cursor)
        if cell and cell.node is not None:
            self._draw_refs(cell.node)
        # cellules
        for row, y in rows:
            for col, x, cw in cols:
                cl = self.sheet.cell((row, col))
                if cl is None:
                    continue
                v = cl.value
                text = cl.display
                if cl.error:
                    color, anchor, tx = t.error, "w", x + 6
                elif isinstance(v, bool):
                    color, anchor, tx = t.fg, "center", x + cw / 2
                elif isinstance(v, float):
                    color, anchor, tx = t.fg, "e", x + cw - 6
                else:
                    color, anchor, tx = t.fg, "w", x + 6
                if cl.is_formula and not cl.error:
                    color = t.fg
                font = "StudioBold" if row == 0 else "StudioBody"
                # troncature visuelle
                max_chars = max(1, int(cw / 7.2))
                if len(text) > max_chars:
                    text = text[:max_chars - 1] + "…"
                c.create_text(tx, y + ROW_H / 2, text=text, anchor=anchor, fill=color, font=font,
                              tags="grid")
                if cl.is_formula:
                    c.create_polygon(x + cw - 6, y + 1, x + cw - 1, y + 1, x + cw - 1, y + 6,
                                     fill=t.accent, outline="", tags="grid")
        # curseur
        rect = self.cell_rect(self.cursor)
        if rect:
            c.create_rectangle(rect[0], rect[1], rect[2], rect[3], outline=t.accent, width=2,
                               tags="grid")
            c.create_rectangle(rect[2] - 3, rect[3] - 3, rect[2] + 3, rect[3] + 3, fill=t.accent,
                               outline=t.canvas, tags="grid")
        c.create_rectangle(0, 0, HEAD_W, ROW_H, fill=head_bg, outline="", tags="grid")
        c.create_line(HEAD_W, 0, HEAD_W, h, fill=t.border, tags="grid")
        c.create_line(0, ROW_H, w, ROW_H, fill=t.border, tags="grid")
        if self.editor is not None:
            c.tag_raise("editor")
        self._update_scrollbars()
        self._update_stats()

    def _range_rect(self, r0: int, c0: int, r1: int, c1: int) -> Optional[Tuple[int, int, int, int]]:
        a = self.cell_rect((max(r0, self.top), max(c0, self.left)))
        b = self.cell_rect((max(r1, self.top), max(c1, self.left)))
        if not a or not b:
            return None
        return a[0], a[1], b[2], b[3]

    def _draw_refs(self, node: Any) -> None:
        from ..core.expr import Bin, Call, Neg, Range, Var

        groups: List[Tuple[int, int, int, int]] = []

        def walk(n: Any) -> None:
            if isinstance(n, Var):
                p = parse_ref(n.name)
                if p:
                    groups.append((p[0], p[1], p[0], p[1]))
            elif isinstance(n, Range):
                a, b = parse_ref(n.start), parse_ref(n.end)
                if a and b:
                    groups.append((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]),
                                   max(a[1], b[1])))
            elif isinstance(n, Neg):
                walk(n.arg)
            elif isinstance(n, Bin):
                walk(n.left)
                walk(n.right)
            elif isinstance(n, Call):
                for a in n.args:
                    walk(a)

        walk(node)
        t = self.theme
        for i, (r0, c0, r1, c1) in enumerate(groups[:8]):
            rect = self._range_rect(r0, c0, r1, c1)
            if rect and r1 >= self.top and c1 >= self.left:
                color = t.color(i)
                self.canvas.create_rectangle(rect[0] + 1, rect[1] + 1, rect[2] - 1, rect[3] - 1,
                                             outline=color, width=2, dash=(4, 2), tags="grid")

    def _update_scrollbars(self) -> None:
        cols, rows = self._visible()
        n_rows, n_cols = self.sheet.rows, self.sheet.cols
        self.vbar.set(self.top / n_rows, min(1.0, (self.top + len(rows)) / n_rows))
        self.hbar.set(self.left / n_cols, min(1.0, (self.left + len(cols)) / n_cols))

    def _yview(self, *args: Any) -> None:
        self._view(args, vertical=True)

    def _xview(self, *args: Any) -> None:
        self._view(args, vertical=False)

    def _view(self, args: Tuple[Any, ...], vertical: bool) -> None:
        total = self.sheet.rows if vertical else self.sheet.cols
        cur = self.top if vertical else self.left
        if args[0] == "moveto":
            new = int(float(args[1]) * total)
        else:
            n = int(args[1])
            new = cur + n * (10 if args[2] == "pages" else 1)
        new = max(0, min(total - 1, new))
        if vertical:
            self.top = new
        else:
            self.left = new
        self.redraw()
        self._place_editor()

    def _scroll(self, n: int, horizontal: int = 0) -> None:
        if horizontal:
            self.left = max(0, min(self.sheet.cols - 1, self.left + (1 if n > 0 else -1)))
        else:
            self.top = max(0, min(self.sheet.rows - 1, self.top + n))
        self.redraw()
        self._place_editor()

    def _ensure_visible(self, pos: Pos) -> None:
        r, c = pos
        cols, rows = self._visible()
        if r < self.top:
            self.top = r
        elif rows and r > rows[-1][0] - 1:
            self.top = max(0, r - len(rows) + 2)
        if c < self.left:
            self.left = c
        elif cols and c > cols[-1][0] - 1 and len(cols) > 1:
            self.left = max(0, self.left + (c - cols[-1][0] + 1))

    # ------------------------------------------------------------------
    # Souris / clavier
    # ------------------------------------------------------------------
    def _border_at(self, x: int, y: int) -> Optional[int]:
        if y > ROW_H:
            return None
        for col, cx, cw in self._visible()[0]:
            if abs(x - (cx + cw)) <= 4:
                return col
        return None

    def _hover_cursor(self, e: tk.Event) -> None:
        self.canvas.configure(cursor="sb_h_double_arrow" if self._border_at(e.x, e.y) is not None
                              else "")

    def _press(self, e: tk.Event) -> None:
        self.canvas.focus_set()
        self.commit_edit()
        col = self._border_at(e.x, e.y)
        if col is not None:
            self._resize = (col, e.x, self.cw(col))
            return
        if e.y < ROW_H and e.x > HEAD_W:  # sélection de colonne
            pos = self.cell_at(e.x, ROW_H + 1)
            if pos:
                self.anchor, self.cursor = (0, pos[1]), (self.sheet.rows - 1, pos[1])
                self.redraw()
            return
        if e.x < HEAD_W and e.y > ROW_H:  # sélection de ligne
            pos = self.cell_at(HEAD_W + 1, e.y)
            if pos:
                self.anchor, self.cursor = (pos[0], 0), (pos[0], self.sheet.cols - 1)
                self.redraw()
            return
        pos = self.cell_at(e.x, e.y)
        if pos is None:
            return
        if e.state & 0x1:
            self.cursor = pos
        else:
            self.anchor = self.cursor = pos
        self._dragging = True
        self._sync_formula()
        self.redraw()

    def _drag(self, e: tk.Event) -> None:
        if self._resize:
            col, x0, w0 = self._resize
            self.col_w[col] = max(30, w0 + e.x - x0)
            self.redraw()
            return
        if self._dragging:
            pos = self.cell_at(max(HEAD_W + 1, e.x), max(ROW_H + 1, e.y))
            if pos and pos != self.cursor:
                self.cursor = pos
                self.redraw()

    def _release(self, _e: tk.Event) -> None:
        self._resize = None
        self._dragging = False
        self.draw_chart()

    def _move(self, dr: int, dc: int, extend: bool = False) -> None:
        r = max(0, min(self.sheet.rows - 1, self.cursor[0] + dr))
        c = max(0, min(self.sheet.cols - 1, self.cursor[1] + dc))
        self.cursor = (r, c)
        if not extend:
            self.anchor = self.cursor
        self._ensure_visible(self.cursor)
        self._sync_formula()
        self.redraw()
        self.draw_chart()

    def _key(self, e: tk.Event) -> Optional[str]:
        shift = bool(e.state & 0x1)
        ctrl = bool(e.state & 0x4)
        moves = {"Up": (-1, 0), "Down": (1, 0), "Left": (0, -1), "Right": (0, 1),
                 "Prior": (-20, 0), "Next": (20, 0)}
        if e.keysym in moves:
            dr, dc = moves[e.keysym]
            if ctrl:
                dr, dc = dr * 1000, dc * 1000
            self._move(dr, dc, shift)
            return "break"
        if e.keysym in ("Return", "KP_Enter"):
            self._move(-1 if shift else 1, 0)
            return "break"
        if e.keysym in ("Tab", "ISO_Left_Tab"):
            self._move(0, -1 if shift else 1)
            return "break"
        if e.keysym == "Home":
            self.cursor = (0, 0) if ctrl else (self.cursor[0], 0)
            self.anchor = self.cursor
            self._ensure_visible(self.cursor)
            self._sync_formula()
            self.redraw()
            return "break"
        if e.keysym in ("Delete", "BackSpace"):
            self.clear_selection()
            return "break"
        if e.keysym == "F2":
            self.begin_edit()
            return "break"
        if e.char and e.char.isprintable() and not ctrl:
            self.begin_edit(e.char)
            return "break"
        return None

    # ------------------------------------------------------------------
    # Édition
    # ------------------------------------------------------------------
    def begin_edit(self, initial: Optional[str] = None) -> None:
        self.commit_edit()
        pos = self.cursor
        self._ensure_visible(pos)
        self.redraw()
        t = self.theme
        self.edit_pos = pos
        self.editor = tk.Entry(self.canvas, font="StudioMono", relief="flat", borderwidth=0,
                               background=t.surface2, foreground=t.fg, insertbackground=t.fg,
                               highlightthickness=2, highlightcolor=t.accent,
                               highlightbackground=t.accent)
        self.editor._studio_role = "skip"  # type: ignore[attr-defined]
        self.editor.insert(0, self.sheet.raw(pos) if initial is None else initial)
        self.editor.bind("<Return>", lambda e: (self.commit_edit(), self._move(1, 0), "break")[2])
        self.editor.bind("<KP_Enter>", lambda e: (self.commit_edit(), self._move(1, 0), "break")[2])
        self.editor.bind("<Tab>", lambda e: (self.commit_edit(), self._move(0, 1), "break")[2])
        self.editor.bind("<Escape>", lambda e: (self.cancel_edit(), "break")[1])
        self.editor.bind("<KeyRelease>", lambda e: self.formula.set(self.editor.get())
                         if self.editor else None)
        self._place_editor()
        self.editor.focus_set()
        self.editor.icursor("end")

    def _place_editor(self) -> None:
        if self.editor is None or self.edit_pos is None:
            return
        rect = self.cell_rect(self.edit_pos)
        self.canvas.delete("editor")
        if rect is None:
            return
        x0, y0, x1, y1 = rect
        self.canvas.create_window(x0, y0, window=self.editor, anchor="nw",
                                  width=max(x1 - x0, 160), height=y1 - y0, tags="editor")

    def commit_edit(self) -> None:
        if self.editor is None or self.edit_pos is None:
            return
        value = self.editor.get()
        pos = self.edit_pos
        self.cancel_edit()
        self.apply({pos: value})

    def cancel_edit(self) -> None:
        if self.editor is not None:
            self.canvas.delete("editor")
            self.editor.destroy()
        self.editor = None
        self.edit_pos = None
        self.canvas.focus_set()
        self._sync_formula()

    def _commit_formula(self) -> None:
        self.apply({self.cursor: self.formula.get()})
        self.canvas.focus_set()

    def _sync_formula(self) -> None:
        self.name_lbl.configure(text=ref_name(self.cursor))
        self.formula.set(self.sheet.raw(self.cursor))

    def apply(self, changes: Dict[Pos, str], record: bool = True) -> None:
        diff = {p: (self.sheet.raw(p), v) for p, v in changes.items() if self.sheet.raw(p) != v}
        if not diff:
            return
        self.sheet.set_many({p: new for p, (_, new) in diff.items()})
        if record:
            self.undo_stack.append(diff)
            del self.undo_stack[:-200]
            self.redo_stack.clear()
        self._sync_formula()
        self.redraw()
        self.draw_chart()

    def clear_selection(self) -> None:
        r0, c0, r1, c1 = self.selection()
        changes = {p: "" for p in list(self.sheet.cells)
                   if r0 <= p[0] <= r1 and c0 <= p[1] <= c1}
        self.apply(changes)

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self) -> None:
        if not self.undo_stack:
            return
        diff = self.undo_stack.pop()
        self.sheet.set_many({p: old for p, (old, _) in diff.items()})
        self.redo_stack.append(diff)
        self._sync_formula()
        self.redraw()
        self.draw_chart()
        self.status(f"Annulé ({len(diff)} cellule(s))")

    def redo(self) -> None:
        if not self.redo_stack:
            return
        diff = self.redo_stack.pop()
        self.sheet.set_many({p: new for p, (_, new) in diff.items()})
        self.undo_stack.append(diff)
        self._sync_formula()
        self.redraw()
        self.draw_chart()

    def copy(self) -> None:
        r0, c0, r1, c1 = self.selection()
        block = self.sheet.copy_block(r0, c0, r1, c1)
        self.clip = (block, (r0, c0))
        self.frame.clipboard_clear()
        self.frame.clipboard_append("\n".join("\t".join(row) for row in block))
        self.status(f"{(r1 - r0 + 1) * (c1 - c0 + 1)} cellule(s) copiée(s)")

    def cut(self) -> None:
        self.copy()
        self.clear_selection()

    def paste(self) -> None:
        try:
            text = self.frame.clipboard_get()
        except tk.TclError:
            text = ""
        if self.clip and text == "\n".join("\t".join(r) for r in self.clip[0]):
            block, src = self.clip
        else:
            block = [line.split("\t") for line in text.rstrip("\n").split("\n")] if text else []
            src = None
        if not block:
            return
        changes = self.sheet.paste_block(block, self.cursor[0], self.cursor[1], src)
        self.apply(changes)
        rows, cols = len(block), max(len(r) for r in block)
        self.anchor = self.cursor
        self.cursor = (min(self.sheet.rows - 1, self.cursor[0] + rows - 1),
                       min(self.sheet.cols - 1, self.cursor[1] + cols - 1))
        self.redraw()

    def fill_down(self) -> None:
        r0, c0, r1, c1 = self.selection()
        if r1 <= r0:
            return
        changes = {}
        for c in range(c0, c1 + 1):
            raw = self.sheet.raw((r0, c))
            for r in range(r0 + 1, r1 + 1):
                changes[(r, c)] = ss.shift_formula(raw, r - r0, 0)
        self.apply(changes)

    def auto_sum(self) -> None:
        r0, c0, r1, c1 = self.selection()
        target = (min(self.sheet.rows - 1, r1 + 1), c0)
        a, b = ref_name((r0, c0)), ref_name((r1, c0))
        self.apply({target: f"=SOMME({a}:{b})"})
        self.anchor = self.cursor = target
        self._sync_formula()
        self.redraw()

    def select_all(self) -> None:
        self.anchor = (0, 0)
        self.cursor = (self.sheet.rows - 1, self.sheet.cols - 1)
        self.redraw()

    # ------------------------------------------------------------------
    # Statistiques et graphique
    # ------------------------------------------------------------------
    def _selection_values(self) -> List[float]:
        r0, c0, r1, c1 = self.selection()
        out = []
        for (r, c), cell in self.sheet.cells.items():
            if r0 <= r <= r1 and c0 <= c <= c1 and isinstance(cell.value, float) \
                    and not isinstance(cell.value, bool) and not cell.error:
                out.append(cell.value)
        return out

    def _update_stats(self) -> None:
        vals = self._selection_values()
        r0, c0, r1, c1 = self.selection()
        n_cells = (r1 - r0 + 1) * (c1 - c0 + 1)
        text = f"{ref_name((r0, c0))}:{ref_name((r1, c1))}  ·  {n_cells} cellule(s)"
        if vals:
            text += (f"  ·  Somme {numeric.format_value(sum(vals), 10)}  ·  Moyenne "
                     f"{numeric.format_value(sum(vals) / len(vals), 8)}  ·  Min "
                     f"{numeric.format_value(min(vals))}  ·  Max {numeric.format_value(max(vals))}"
                     f"  ·  Nb {len(vals)}")
        text += f"   |   {len(self.sheet.cells)} cellules remplies · " \
                f"{self.sheet.recalc_count} recalculs"
        self.stats.configure(text=text)

    def _chart_toggle(self) -> None:
        if self.chart_mode.get() == "none":
            self.chart_frame.pack_forget()
        else:
            self.chart_frame.pack(side="right", fill="y")
            self.draw_chart()

    def _chart_data(self) -> Tuple[List[str], List[Tuple[str, List[float]]]]:
        r0, c0, r1, c1 = self.selection()
        if r1 - r0 > 400:
            r1 = r0 + 400
        if c1 - c0 > 8:
            c1 = c0 + 8
        series: List[Tuple[str, List[float]]] = []
        labels: List[str] = []
        first_row_header = any(isinstance(self.sheet.value((r0, c)), str) for c in range(c0, c1 + 1))
        start = r0 + 1 if first_row_header else r0
        label_col = None
        if c1 > c0 and all(not isinstance(self.sheet.value((r, c0)), float)
                           for r in range(start, r1 + 1) if self.sheet.value((r, c0)) is not None):
            label_col = c0
        for r in range(start, r1 + 1):
            labels.append(str(self.sheet.display((r, label_col))) if label_col is not None
                          else str(r + 1))
        for c in range(c0, c1 + 1):
            if c == label_col:
                continue
            vals = []
            for r in range(start, r1 + 1):
                v = self.sheet.value((r, c))
                vals.append(float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
                            else math.nan)
            if any(math.isfinite(v) for v in vals):
                name = str(self.sheet.display((r0, c))) if first_row_header else col_name(c)
                series.append((name, vals))
        return labels, series

    def draw_chart(self) -> None:
        if self.chart_mode.get() == "none" or not self.chart.winfo_ismapped():
            return
        c = self.chart
        t = self.theme
        c.configure(background=t.canvas)
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        labels, series = self._chart_data()
        c.create_text(16, 16, text="Graphique de la sélection", anchor="w", fill=t.fg,
                      font="StudioBold")
        if not series:
            c.create_text(w / 2, h / 2, text="Sélectionnez des cellules\nnumériques",
                          fill=t.muted, font="StudioBody", justify="center")
            return
        series = series[:8]
        left, right, top, bottom = 50, 16, 64 if len(series) > 1 else 44, 34
        pw, ph = w - left - right, min(h - top - bottom, 360)
        vals = [v for _, s in series for v in s if math.isfinite(v)]
        lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
        if hi - lo < 1e-12:
            hi = lo + 1
        step, ticks = numeric.nice_ticks(lo, hi, 6)
        lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])

        def ymap(v: float) -> float:
            return top + ph - (v - lo) / (hi - lo) * ph

        for tv in ticks:
            y = ymap(tv)
            c.create_line(left, y, left + pw, y, fill=t.grid)
            c.create_text(left - 6, y, text=numeric.format_tick(tv, step), anchor="e",
                          fill=t.muted, font="StudioSmall")
        c.create_line(left, ymap(0), left + pw, ymap(0), fill=t.axis)
        n = max(1, len(labels))
        if len(series) > 1:
            x = left
            for i, (name, _) in enumerate(series):
                color = t.color(i)
                c.create_rectangle(x, 38, x + 10, 48, fill=color, outline="")
                c.create_text(x + 14, 43, text=name[:12], anchor="w", fill=t.fg2,
                              font="StudioSmall")
                x += 24 + min(12, len(name)) * 7
        mode = self.chart_mode.get()
        if mode == "bar":
            group = pw / n
            bw = max(2.0, (group - 4) / len(series) - 2)
            for i in range(n):
                for k, (_, vals) in enumerate(series):
                    v = vals[i]
                    if not math.isfinite(v):
                        continue
                    x0 = left + i * group + 2 + k * (bw + 2)
                    y0, y1 = sorted((ymap(0), ymap(v)))
                    c.create_rectangle(x0, y0, x0 + bw, y1, fill=t.color(k), outline="")
        else:
            for k, (_, vals) in enumerate(series):
                pts: List[float] = []
                for i, v in enumerate(vals):
                    if math.isfinite(v):
                        pts += [left + (i + 0.5) * pw / n, ymap(v)]
                if len(pts) >= 4:
                    c.create_line(*pts, fill=t.color(k), width=2)
                if len(pts) // 2 <= 30:
                    for j in range(0, len(pts), 2):
                        c.create_oval(pts[j] - 3, pts[j + 1] - 3, pts[j] + 3, pts[j + 1] + 3,
                                      fill=t.color(k), outline=t.canvas)
        every = max(1, n // 8)
        for i in range(0, n, every):
            c.create_text(left + (i + 0.5) * pw / n, top + ph + 14, text=labels[i][:8],
                          fill=t.muted, font="StudioSmall")

    # ------------------------------------------------------------------
    # Fichiers
    # ------------------------------------------------------------------
    def new(self) -> None:
        old = {p: c.raw for p, c in self.sheet.cells.items()}
        self.apply({p: "" for p in old})
        self.path = None
        self.status("Nouvelle feuille (Ctrl+Z pour annuler)")

    def open_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.frame, filetypes=[
            ("Feuilles", "*.json *.csv *.tsv *.txt"), ("Tous les fichiers", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
            tmp = Sheet(self.sheet.rows, self.sheet.cols)
            if path.lower().endswith(".json"):
                tmp.load_json(text)
            else:
                tmp.load_csv(text)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            self.notify(f"Ouverture impossible : {exc}", "error")
            return
        changes = {p: "" for p in self.sheet.cells}
        changes.update({p: c.raw for p, c in tmp.cells.items()})
        self.apply(changes)
        self.path = path
        self.notify(f"{os.path.basename(path)} ouvert ({len(tmp.cells)} cellules)", "success")

    def save_file(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".json",
                                            filetypes=[("JSON (formules)", "*.json"),
                                                       ("CSV (formules)", "*.csv")],
                                            initialfile=os.path.basename(self.path or "feuille.json"))
        if not path:
            return
        data = self.sheet.to_csv() if path.lower().endswith(".csv") else self.sheet.to_json()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data)
        self.path = path
        self.notify("Feuille enregistrée", "success")

    def export_values(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            with open(path, "w", encoding="utf-8-sig") as fh:
                fh.write(self.sheet.to_csv(values=True))
            self.notify("Valeurs exportées", "success")

    # ------------------------------------------------------------------
    def on_activate(self) -> None:
        self.canvas.focus_set()

    def on_theme(self, theme) -> None:
        self.redraw()
        self.draw_chart()

    def commands(self) -> List[Command]:
        return [Command("sheet.save", "Enregistrer la feuille", self.save_file, shortcut="Ctrl+S",
                        icon="⬇"),
                Command("sheet.open", "Ouvrir une feuille", self.open_file, shortcut="Ctrl+O",
                        icon="⇧"),
                Command("sheet.fill", "Recopier vers le bas", self.fill_down, icon="⇩"),
                Command("sheet.sum", "Somme automatique", self.auto_sum, icon="Σ"),
                Command("sheet.demo", "Recharger la feuille d'exemple", self._reload_demo, icon="↺")]

    def _reload_demo(self) -> None:
        changes = {p: "" for p in self.sheet.cells}
        for ref, raw in ss.DEMO.items():
            pos = parse_ref(ref)
            if pos:
                changes[pos] = raw
        self.apply(changes)

    def get_state(self) -> Dict[str, Any]:
        return {"cells": self.sheet.to_dict(), "col_w": {str(k): v for k, v in self.col_w.items()},
                "cursor": list(self.cursor), "chart": self.chart_mode.get()}

    def set_state(self, state: Dict[str, Any]) -> None:
        cells = state.get("cells")
        if isinstance(cells, dict):
            self.sheet.load_dict(cells)
        self.col_w = {int(k): int(v) for k, v in state.get("col_w", {}).items()}
        cur = state.get("cursor")
        if cur and len(cur) == 2:
            self.anchor = self.cursor = (int(cur[0]), int(cur[1]))
        if state.get("chart") in ("none", "bar", "line"):
            self.chart_mode.set(state["chart"])
            self._chart_toggle()
        self._sync_formula()
        self.redraw()

    def info(self) -> Dict[str, Any]:
        return {"cellules": len(self.sheet.cells), "recalculs": self.sheet.recalc_count}

    def demo(self) -> None:
        self.anchor, self.cursor = (0, 6), (21, 8)  # G1:I22 — sin(x) et x²/10
        self.chart_mode.set("line")
        self._chart_toggle()
        self.redraw()
        self.draw_chart()
