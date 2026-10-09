"""Éléments de la coquille : barre latérale, barre d'état, en-tête et écran de démarrage."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from ..core import fuzzy
from ..core.colors import mix
from ..core.sysinfo import CpuMeter, human_bytes, memory_rss
from .widgets import ToolTip, theme_of, themed_canvas

if TYPE_CHECKING:  # pragma: no cover
    from ..app import StudioApp


class Sidebar(ttk.Frame):
    """Navigation entre modules : recherche, catégories, état ouvert/actif."""

    ROW_H = 34

    def __init__(self, parent: tk.Misc, app: "StudioApp") -> None:
        super().__init__(parent, style="Sidebar.TFrame", width=230)
        self.app = app
        self.filter = tk.StringVar()
        search = ttk.Entry(self, textvariable=self.filter, style="Search.TEntry")
        search.pack(fill="x", padx=10, pady=(10, 6))
        self._placeholder(search, "Filtrer les modules…")
        self.filter.trace_add("write", lambda *_: self.redraw())
        self.canvas = themed_canvas(self, role="sidebar", cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.rows: List[Tuple[float, float, Optional[str]]] = []
        self.hover: Optional[str] = None
        self.scroll = 0
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", lambda e: self._wheel(-1 if e.delta > 0 else 1))
        self.canvas.bind("<Button-4>", lambda e: self._wheel(-1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(1))
        footer = ttk.Frame(self, style="Sidebar.TFrame")
        footer.pack(fill="x", side="bottom", pady=6)
        for icon, tip, cmd in (("⚙", "Réglages (Ctrl+,)", "app.settings"),
                               ("◐", "Thème suivant (Ctrl+Maj+T)", "theme.next"),
                               ("⌘", "Palette de commandes (Ctrl+Maj+P)", "app.palette"),
                               ("▤", "Panneau inférieur (Ctrl+J)", "view.bottom")):
            b = ttk.Button(footer, text=icon, style="Sidebar.TButton", width=3,
                           command=lambda c=cmd: app.commands.execute(c))
            b.pack(side="left", padx=(8 if icon == "⚙" else 0, 0))
            ToolTip(b, tip)

    def _placeholder(self, entry: ttk.Entry, text: str) -> None:
        def on_focus_in(_e: tk.Event) -> None:
            if entry.get() == text:
                entry.delete(0, "end")
                entry.configure(foreground="")

        def on_focus_out(_e: tk.Event) -> None:
            if not entry.get():
                self.filter.set("")
                entry.insert(0, text)

        entry.insert(0, text)
        self._ph_text = text
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

    def _query(self) -> str:
        q = self.filter.get()
        return "" if q == getattr(self, "_ph_text", None) else q.strip()

    def _set_hover(self, mid: Optional[str]) -> None:
        if mid != self.hover:
            self.hover = mid
            self.redraw()

    def _wheel(self, d: int) -> None:
        self.scroll = max(0, self.scroll + d * 40)
        self.redraw()

    def redraw(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        c = self.canvas
        c.configure(background=t.sidebar)
        c.delete("all")
        self.rows.clear()
        width = c.winfo_width()
        query = self._query()
        specs = self.app.registry.ordered()
        if query:
            ranked = fuzzy.rank(query, specs, key=lambda s: f"{s.cls.title} {s.cls.category} "
                                f"{' '.join(s.cls.keywords)}")
            groups: Dict[str, List] = {"Résultats": [r[1] for r in ranked]}
        else:
            groups = {}
            for spec in specs:
                groups.setdefault(spec.cls.category, []).append(spec)
        total_h = sum(28 + len(v) * self.ROW_H for v in groups.values()) + 20
        self.scroll = max(0, min(self.scroll, int(total_h - c.winfo_height())))
        y = 6 - self.scroll
        for category, items in groups.items():
            c.create_text(16, y + 14, text=category.upper(), anchor="w", fill=t.muted,
                          font="StudioSmall")
            y += 28
            for spec in items:
                cls = spec.cls
                mid = cls.id
                active = self.app.active_id == mid
                opened = mid in self.app.instances
                hovered = self.hover == mid
                y0, y1 = y, y + self.ROW_H
                if active:
                    c.create_rectangle(8, y0 + 2, width - 8, y1 - 2, fill=t.accent_soft,
                                       outline="")
                    c.create_rectangle(8, y0 + 8, 11, y1 - 8, fill=t.accent, outline="")
                elif hovered:
                    c.create_rectangle(8, y0 + 2, width - 8, y1 - 2,
                                       fill=mix(t.sidebar, t.fg, 0.06), outline="")
                fg = t.fg if active or hovered else t.fg2
                c.create_text(26, (y0 + y1) / 2, text=cls.icon, anchor="center",
                              fill=t.accent if active else t.muted, font="StudioIcon")
                c.create_text(44, (y0 + y1) / 2, text=cls.title, anchor="w", fill=fg,
                              font="StudioBold" if active else "StudioBody")
                if opened and not active:
                    c.create_oval(width - 22, (y0 + y1) / 2 - 3, width - 16, (y0 + y1) / 2 + 3,
                                  fill=t.accent, outline="")
                self.rows.append((y0, y1, mid))
                y = y1
            y += 6
        for spec in self.app.registry.failures[:3]:
            c.create_text(16, y + 12, text=f"⚠ {spec.origin.split('.')[-1]} (erreur)",
                          anchor="w", fill=t.error, font="StudioSmall")
            y += 22

    def _row(self, y: float) -> Optional[str]:
        for y0, y1, mid in self.rows:
            if y0 <= y < y1:
                return mid
        return None

    def _motion(self, e: tk.Event) -> None:
        self._set_hover(self._row(e.y))

    def _click(self, e: tk.Event) -> None:
        mid = self._row(e.y)
        if mid:
            self.app.open_module(mid)


class StatusBar(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "StudioApp") -> None:
        super().__init__(parent, style="Status.TFrame", padding=(10, 3))
        self.app = app
        self.message = ttk.Label(self, text="Prêt", style="Status.Muted.TLabel")
        self.message.pack(side="left")
        self.clock = ttk.Label(self, text="", style="Status.Muted.TLabel")
        self.clock.pack(side="right", padx=(12, 0))
        self.mem = ttk.Label(self, text="", style="Status.Muted.TLabel")
        self.mem.pack(side="right", padx=(12, 0))
        self.fps = ttk.Label(self, text="", style="Status.Muted.TLabel")
        self.fps.pack(side="right", padx=(12, 0))
        self.theme_lbl = ttk.Label(self, text="", style="Status.Muted.TLabel", cursor="hand2")
        self.theme_lbl.pack(side="right", padx=(12, 0))
        self.theme_lbl.bind("<Button-1>", lambda e: app.commands.execute("theme.next"))
        self.progress = ttk.Progressbar(self, length=140, mode="determinate", maximum=1.0)
        self.task_lbl = ttk.Label(self, text="", style="Status.Muted.TLabel", cursor="hand2")
        self.task_lbl.bind("<Button-1>", lambda e: app.show_bottom("tasks"))
        self._msg_after: Optional[str] = None
        self.cpu = CpuMeter()
        self._tick()

    def set_message(self, text: str, timeout: int = 6000) -> None:
        self.message.configure(text=text)
        if self._msg_after:
            self.after_cancel(self._msg_after)
        self._msg_after = self.after(timeout, lambda: self.message.configure(text="Prêt"))

    def _tick(self) -> None:
        try:
            self.clock.configure(text=time.strftime("%H:%M"))
            rss = memory_rss()
            self.mem.configure(text=f"Mémoire {human_bytes(rss)}")
            mod = self.app.active_module
            fps = mod.fps if mod else 0.0
            self.fps.configure(text=f"{fps:.0f} img/s" if fps else "")
            self.theme_lbl.configure(text=f"◐ {self.app.theme.name}")
            active = self.app.tasks.active
            if active:
                if not self.progress.winfo_ismapped():
                    self.task_lbl.pack(side="right", padx=(12, 4))
                    self.progress.pack(side="right")
                task = active[-1]
                self.progress.configure(value=task.progress)
                more = f" (+{len(active) - 1})" if len(active) > 1 else ""
                self.task_lbl.configure(text=f"⟳ {task.name}{more}")
            elif self.progress.winfo_ismapped():
                self.progress.pack_forget()
                self.task_lbl.pack_forget()
        finally:
            self.after(500, self._tick)


class Header(ttk.Frame):
    """En-tête : logo animé, fil d'Ariane du module, bouton de recherche."""

    def __init__(self, parent: tk.Misc, app: "StudioApp") -> None:
        super().__init__(parent, style="Header.TFrame", padding=(10, 6))
        self.app = app
        self.logo = themed_canvas(self, role="bg", width=30, height=30)
        self.logo.pack(side="left")
        ttk.Label(self, text="PyClaude Studio", style="Header.Heading.TLabel").pack(
            side="left", padx=(8, 0))
        self.crumb = ttk.Label(self, text="", style="Header.Muted.TLabel")
        self.crumb.pack(side="left", padx=(10, 0))
        search = ttk.Button(self, text="⌕  Rechercher une commande…        Ctrl+Maj+P",
                            style="Header.TButton", command=lambda: app.commands.execute("app.palette"))
        search.pack(side="right")
        self._phase = 0.0
        self._animate()

    def set_crumb(self, text: str) -> None:
        self.crumb.configure(text=f"›  {text}" if text else "")

    def _animate(self) -> None:
        t = theme_of(self)
        if t is not None:
            c = self.logo
            c.configure(background=t.bg)
            c.delete("all")
            cx, cy = 15, 15
            for i in range(6):
                a = self._phase + i * math.pi / 3
                r = 10
                x, y = cx + r * math.cos(a), cy + r * math.sin(a)
                size = 2.2 + 1.2 * (1 + math.sin(self._phase * 2 + i)) / 2
                c.create_oval(x - size, y - size, x + size, y + size,
                              fill=mix(t.accent, t.fg, i / 10), outline="")
            c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=t.accent, outline="")
            self._phase += 0.05 if self.app.settings.get("ui.animations") else 0.0
        self.after(50, self._animate)


class Splash:
    """Écran de démarrage animé (fenêtre sans bordure) affichant les étapes."""

    def __init__(self, root: tk.Tk, theme) -> None:
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        w, h = 520, 300
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
        self.t = theme
        self.c = tk.Canvas(self.win, width=w, height=h, highlightthickness=0,
                           background=theme.bg)
        self.c._studio_role = "skip"  # type: ignore[attr-defined]
        self.c.pack(fill="both", expand=True)
        self.w, self.h = w, h
        self.progress = 0.0
        self.text = "Initialisation…"
        self.phase = 0.0
        self.draw()

    def step(self, progress: float, text: str) -> None:
        self.progress, self.text = progress, text
        self.phase += 0.35
        self.draw()
        self.win.update()

    def draw(self) -> None:
        t, c, w, h = self.t, self.c, self.w, self.h
        c.delete("all")
        c.create_rectangle(0, 0, w - 1, h - 1, outline=t.border)
        # Motif de fond : rosace de Lissajous
        pts = []
        for i in range(400):
            a = i / 400 * math.tau
            r = 70 + 18 * math.sin(5 * a + self.phase)
            pts += [w - 120 + r * math.cos(3 * a), 120 + r * math.sin(2 * a)]
        c.create_line(*pts, fill=mix(t.bg, t.accent, 0.45), width=1.5, smooth=True)
        c.create_text(36, 88, text="PyClaude", anchor="w", fill=t.fg, font=("StudioDisplay"))
        c.create_text(36, 124, text="Studio", anchor="w", fill=t.accent, font=("StudioDisplay"))
        c.create_text(38, 160, text="Atelier scientifique & créatif", anchor="w",
                      fill=t.fg2, font="StudioBody")
        c.create_rectangle(36, h - 58, w - 36, h - 54, fill=t.surface2, outline="")
        c.create_rectangle(36, h - 58, 36 + (w - 72) * self.progress, h - 54, fill=t.accent,
                           outline="")
        c.create_text(36, h - 36, text=self.text, anchor="w", fill=t.muted, font="StudioSmall")

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
