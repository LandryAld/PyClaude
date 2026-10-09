"""Palette de commandes (Ctrl+Maj+P) : recherche floue, navigation clavier,
mise en évidence des caractères appariés et priorité aux commandes récentes."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, List, Optional, Tuple

from ..core import fuzzy
from ..core.commands import Command
from .widgets import theme_of

if TYPE_CHECKING:  # pragma: no cover
    from ..app import StudioApp


class CommandPalette:
    WIDTH = 620
    ROWS = 12

    def __init__(self, app: "StudioApp") -> None:
        self.app = app
        self.root = app.root
        self.frame: Optional[tk.Frame] = None
        self.results: List[Tuple[int, Command, List[int]]] = []
        self.index = 0

    @property
    def is_open(self) -> bool:
        return self.frame is not None

    def toggle(self) -> None:
        if self.is_open:
            self.close()
        else:
            self.open()

    def open(self, initial: str = "") -> None:
        if self.is_open:
            self.entry.focus_set()
            return
        t = theme_of(self.root)
        self._previous_focus = self.root.focus_get()
        self.frame = tk.Frame(self.root, background=t.accent, padx=1, pady=1)
        self.frame._studio_role = "skip"  # type: ignore[attr-defined]
        inner = tk.Frame(self.frame, background=t.surface2)
        inner.pack(fill="both", expand=True)
        head = tk.Frame(inner, background=t.surface2)
        head.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(head, text="⌘", background=t.surface2, foreground=t.accent,
                 font="StudioHeading").pack(side="left", padx=(0, 8))
        self.var = tk.StringVar(value=initial)
        self.entry = tk.Entry(head, textvariable=self.var, background=t.surface2,
                              foreground=t.fg, insertbackground=t.fg, relief="flat",
                              font="StudioHeading", highlightthickness=0, borderwidth=0)
        self.entry.pack(side="left", fill="x", expand=True)
        self.count = tk.Label(head, text="", background=t.surface2, foreground=t.muted,
                              font="StudioSmall")
        self.count.pack(side="right")
        tk.Frame(inner, background=t.border, height=1).pack(fill="x")
        self.text = tk.Text(inner, height=self.ROWS, background=t.surface2, foreground=t.fg,
                            relief="flat", highlightthickness=0, borderwidth=0,
                            font="StudioBody", cursor="hand2", wrap="none", padx=8, pady=6,
                            spacing1=3, spacing3=3)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("match", foreground=t.accent, font="StudioBold")
        self.text.tag_configure("cat", foreground=t.muted)
        self.text.tag_configure("key", foreground=t.muted, font="StudioSmall")
        self.text.tag_configure("sel", background=t.select_bg)
        self.text.tag_configure("selrow", background=t.select_bg)
        self.text.tag_configure("empty", foreground=t.muted)
        foot = tk.Label(inner, text="↑↓ naviguer   ⏎ exécuter   Échap fermer",
                        background=t.surface2, foreground=t.muted, font="StudioSmall",
                        anchor="w", padx=12, pady=6)
        foot.pack(fill="x")
        self.frame.place(relx=0.5, y=70, anchor="n", width=self.WIDTH)
        self.frame.lift()
        self.var.trace_add("write", lambda *_: self._refresh())
        self.entry.bind("<Escape>", lambda e: self.close())
        self.entry.bind("<Return>", lambda e: self._run())
        self.entry.bind("<KP_Enter>", lambda e: self._run())
        self.entry.bind("<Down>", lambda e: self._move(1))
        self.entry.bind("<Up>", lambda e: self._move(-1))
        self.entry.bind("<Next>", lambda e: self._move(self.ROWS))
        self.entry.bind("<Prior>", lambda e: self._move(-self.ROWS))
        self.entry.bind("<Tab>", lambda e: self._move(1))
        self.text.bind("<Motion>", self._hover)
        self.text.bind("<Button-1>", self._click)
        self.text.bind("<MouseWheel>", lambda e: self._move(-1 if e.delta > 0 else 1))
        self.text.bind("<Button-4>", lambda e: self._move(-1))
        self.text.bind("<Button-5>", lambda e: self._move(1))
        self.entry.focus_set()
        # Un clic en dehors de la palette la referme
        self.root.bind_all("<ButtonPress-1>", self._outside_click, add="+")
        self._refresh()

    def _outside_click(self, e: tk.Event) -> None:
        if self.frame is None:
            return
        if not str(e.widget).startswith(str(self.frame)):
            self.close()

    def close(self) -> None:
        if self.frame is None:
            return
        self.root.unbind_all("<ButtonPress-1>")
        self.frame.destroy()
        self.frame = None
        try:
            if self._previous_focus is not None:
                self._previous_focus.focus_set()
        except (tk.TclError, AttributeError):
            pass

    # -- résultats ------------------------------------------------------
    def _candidates(self) -> List[Command]:
        return [c for c in self.app.commands.available() if c.palette]

    def _refresh(self) -> None:
        query = self.var.get().strip()
        cmds = self._candidates()
        if query:
            ranked = fuzzy.rank(query, cmds, key=lambda c: c.label, limit=60)
            now = time.time()
            # Bonus discret aux commandes utilisées récemment
            ranked = sorted(ranked, key=lambda r: -(r[0] + (300 if now - r[1].last_used < 600 else 0)))
        else:
            recent = sorted([c for c in cmds if c.last_used], key=lambda c: -c.last_used)[:5]
            rest = sorted([c for c in cmds if c not in recent], key=lambda c: c.label)
            ranked = [(0, c, []) for c in recent + rest]
        self.results = ranked
        self.index = 0
        self.count.configure(text=f"{len(ranked)} résultat{'s' if len(ranked) > 1 else ''}")
        self._render()

    def _render(self) -> None:
        txt = self.text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        if not self.results:
            txt.insert("end", "  Aucune commande ne correspond.", "empty")
        top = max(0, min(self.index - self.ROWS + 2, len(self.results) - self.ROWS))
        top = max(0, top)
        self._top = top
        for row, (score, cmd, idx) in enumerate(self.results[top:top + self.ROWS]):
            line = row + 1
            label = cmd.label
            # Le libellé « Catégorie : Titre » est rendu avec les correspondances en gras
            cat_len = len(cmd.category) + 3 if cmd.category else 0
            icon = (cmd.icon or "•") + "  "
            txt.insert("end", icon)
            for j, ch in enumerate(label):
                tags = []
                if j < cat_len:
                    tags.append("cat")
                if j in idx:
                    tags.append("match")
                txt.insert("end", ch, tuple(tags))
            if cmd.shortcut:
                txt.insert("end", "    " + cmd.shortcut.replace("Shift", "Maj"), "key")
            txt.insert("end", "\n")
            if top + row == self.index:
                txt.tag_add("selrow", f"{line}.0", f"{line}.end+1c")
        txt.tag_raise("match")
        txt.configure(state="disabled")

    def _move(self, step: int) -> str:
        if self.results:
            self.index = max(0, min(len(self.results) - 1, self.index + step))
            self._render()
        return "break"

    def _row_at(self, y: int) -> Optional[int]:
        index = self.text.index(f"@0,{y}")
        line = int(index.split(".")[0]) - 1
        i = getattr(self, "_top", 0) + line
        return i if 0 <= i < len(self.results) else None

    def _hover(self, e: tk.Event) -> None:
        i = self._row_at(e.y)
        if i is not None and i != self.index:
            self.index = i
            self._render()

    def _click(self, e: tk.Event) -> str:
        i = self._row_at(e.y)
        if i is not None:
            self.index = i
            self._run()
        return "break"

    def _run(self) -> str:
        if not self.results:
            return "break"
        cmd = self.results[self.index][1]
        self.close()
        self.root.after(10, lambda: self.app.commands.execute(cmd.id))
        return "break"
