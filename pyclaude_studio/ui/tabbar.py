"""Barre d'onglets dessinée sur canevas : survol, fermeture, glisser-déposer,
défilement horizontal à la molette et clic du milieu pour fermer."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Dict, List, Optional, Tuple

from ..core.colors import mix
from .widgets import theme_of


class TabBar(tk.Canvas):
    HEIGHT = 36
    PAD = 14
    CLOSE = 16
    MAX_W = 220

    def __init__(self, parent: tk.Misc, *, on_select: Callable[[str], None],
                 on_close: Callable[[str], None],
                 on_reorder: Optional[Callable[[List[str]], None]] = None,
                 on_context: Optional[Callable[[str, tk.Event], None]] = None) -> None:
        super().__init__(parent, height=self.HEIGHT, highlightthickness=0, borderwidth=0)
        self._studio_role = "bg"
        self.on_select = on_select
        self.on_close = on_close
        self.on_reorder = on_reorder
        self.on_context = on_context
        self.tabs: List[Tuple[str, str, str]] = []  # (id, icône, titre)
        self.active: Optional[str] = None
        self.hover: Optional[str] = None
        self.hover_close = False
        self.offset = 0
        self._geom: Dict[str, Tuple[float, float]] = {}
        self._drag: Optional[str] = None
        self._drag_x = 0
        self._drag_moved = False
        self.bind("<Configure>", lambda e: (self._scroll(0), self._ensure_visible(self.active),
                                            self.redraw()))
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag_motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<ButtonRelease-2>", self._middle)
        self.bind("<ButtonRelease-3>", self._context)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda e: self._scroll(-60))
        self.bind("<Button-5>", lambda e: self._scroll(60))

    # -- API -----------------------------------------------------------
    def add(self, tab_id: str, icon: str, title: str) -> None:
        if not any(t[0] == tab_id for t in self.tabs):
            self.tabs.append((tab_id, icon, title))
        self.redraw()

    def remove(self, tab_id: str) -> None:
        self.tabs = [t for t in self.tabs if t[0] != tab_id]
        if self.active == tab_id:
            self.active = None
        self.redraw()

    def select(self, tab_id: Optional[str]) -> None:
        self.active = tab_id
        self._ensure_visible(tab_id)
        self.redraw()

    def order(self) -> List[str]:
        return [t[0] for t in self.tabs]

    def neighbor(self, tab_id: str, step: int) -> Optional[str]:
        ids = self.order()
        if tab_id not in ids or len(ids) < 2:
            return None
        return ids[(ids.index(tab_id) + step) % len(ids)]

    # -- dessin --------------------------------------------------------
    def _tab_width(self, title: str) -> float:
        font = tkfont.nametofont("StudioBody")
        return min(self.MAX_W, font.measure(title) + self.PAD * 2 + 24 + self.CLOSE)

    def _ensure_visible(self, tab_id: Optional[str]) -> None:
        if tab_id is None:
            return
        view = self.winfo_width()
        if view <= 1:  # pas encore affiché
            return
        x = 8.0
        for tid, _icon, title in self.tabs:
            w = self._tab_width(title)
            if tid == tab_id:
                if x - self.offset < 0:
                    self.offset = int(x - 8)
                elif x + w - self.offset > view:
                    self.offset = int(x + w - view + 8)
                return
            x += w + 2

    def redraw(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        self.configure(background=t.bg)
        self.delete("all")
        h = self.HEIGHT
        x = 8.0 - self.offset
        self._geom.clear()
        self.create_line(0, h - 1, self.winfo_width(), h - 1, fill=t.border)
        for tid, icon, title in self.tabs:
            w = self._tab_width(title)
            self._geom[tid] = (x, x + w)
            active = tid == self.active
            hovered = tid == self.hover
            if active:
                fill = t.surface
            elif hovered:
                fill = mix(t.bg, t.surface, 0.6)
            else:
                fill = t.bg
            r = 8
            x0, x1, y0, y1 = x, x + w, 5, h
            self.create_polygon(x0, y1, x0, y0 + r, x0 + r, y0, x1 - r, y0, x1, y0 + r, x1, y1,
                                fill=fill, outline=t.border if active else fill,
                                smooth=False)
            if active:
                self.create_line(x0 + 1, y1 - 1, x1 - 1, y1 - 1, fill=t.surface, width=2)
                self.create_line(x0 + r, y0 + 1, x1 - r, y0 + 1, fill=t.accent, width=2)
            fg = t.fg if active else (t.fg2 if hovered else t.muted)
            self.create_text(x0 + self.PAD, (y0 + y1) / 2, text=icon, anchor="w",
                             fill=t.accent if active else fg, font="StudioIcon")
            font = tkfont.nametofont("StudioBody")
            avail = w - self.PAD * 2 - 24 - self.CLOSE
            label = title
            while font.measure(label) > avail and len(label) > 3:
                label = label[:-2]
            if label != title:
                label = label.rstrip() + "…"
            self.create_text(x0 + self.PAD + 22, (y0 + y1) / 2, text=label, anchor="w",
                             fill=fg, font="StudioBody")
            if active or hovered:
                cx, cy = x1 - self.PAD + 2, (y0 + y1) / 2
                if hovered and self.hover_close:
                    self.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=t.hover, outline="")
                self.create_text(cx, cy, text="×", fill=fg, font="StudioHeading")
            x += w + 2
        if not self.tabs:
            self.create_text(16, h / 2 + 2, text="Aucun onglet ouvert — Ctrl+Maj+P pour "
                             "la palette de commandes", anchor="w", fill=t.muted,
                             font="StudioSmall")

    # -- interaction ---------------------------------------------------
    def _hit(self, x: float) -> Tuple[Optional[str], bool]:
        for tid, (x0, x1) in self._geom.items():
            if x0 <= x <= x1:
                return tid, x >= x1 - self.PAD - 10
        return None, False

    def _motion(self, e: tk.Event) -> None:
        tid, close = self._hit(e.x)
        if (tid, close) != (self.hover, self.hover_close):
            self.hover, self.hover_close = tid, close
            self.redraw()

    def _leave(self, _e: tk.Event) -> None:
        self.hover, self.hover_close = None, False
        self.redraw()

    def _press(self, e: tk.Event) -> None:
        tid, close = self._hit(e.x)
        self._drag, self._drag_x, self._drag_moved = tid, e.x, False
        if tid and close:
            self._drag = None
            self.on_close(tid)
        elif tid:
            self.on_select(tid)

    def _drag_motion(self, e: tk.Event) -> None:
        if not self._drag or abs(e.x - self._drag_x) < 12:
            return
        self._drag_moved = True
        target, _ = self._hit(e.x)
        if target and target != self._drag:
            ids = self.order()
            i, j = ids.index(self._drag), ids.index(target)
            tab = self.tabs.pop(i)
            self.tabs.insert(j, tab)
            self._drag_x = e.x
            self.redraw()
            self.configure(cursor="fleur")

    def _release(self, _e: tk.Event) -> None:
        if self._drag and self._drag_moved and self.on_reorder:
            self.on_reorder(self.order())
        self._drag = None
        self.configure(cursor="")

    def _middle(self, e: tk.Event) -> None:
        tid, _ = self._hit(e.x)
        if tid:
            self.on_close(tid)

    def _context(self, e: tk.Event) -> None:
        tid, _ = self._hit(e.x)
        if tid and self.on_context:
            self.on_context(tid, e)

    def _wheel(self, e: tk.Event) -> None:
        self._scroll(-60 if e.delta > 0 else 60)

    def _scroll(self, dx: int) -> None:
        total = sum(self._tab_width(t[2]) + 2 for t in self.tabs) + 16
        max_off = max(0, int(total - self.winfo_width()))
        self.offset = max(0, min(max_off, self.offset + dx))
        self.redraw()
