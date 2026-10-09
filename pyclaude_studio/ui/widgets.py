"""Widgets réutilisables : infobulles, panneaux défilants, sections repliables,
curseurs étiquetés, sélecteurs de couleur, graphiques temps réel et jauges."""

from __future__ import annotations

import math
import time
import tkinter as tk
from collections import deque
from tkinter import colorchooser, ttk
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

from ..core.colors import mix
from ..core.numeric import format_tick, nice_ticks


def themed_canvas(parent: tk.Misc, role: str = "canvas", **kw: Any) -> tk.Canvas:
    """Canevas sans bordure, recoloré automatiquement selon son rôle au changement de thème."""
    kw.setdefault("highlightthickness", 0)
    kw.setdefault("borderwidth", 0)
    c = tk.Canvas(parent, **kw)
    c._studio_role = role  # type: ignore[attr-defined]
    return c


def theme_of(widget: tk.Misc):
    """Retrouve le thème courant via l'application attachée à la racine."""
    app = getattr(widget.winfo_toplevel(), "_studio_app", None)
    if app is None:
        root = widget._root()  # type: ignore[attr-defined]
        app = getattr(root, "_studio_app", None)
    return app.theme if app is not None else None


class ToolTip:
    """Infobulle retardée, positionnée près du pointeur."""

    def __init__(self, widget: tk.Misc, text: str | Callable[[], str], delay: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self) -> None:
        text = self.text() if callable(self.text) else self.text
        if not text:
            return
        t = theme_of(self.widget)
        bg = t.surface2 if t else "#222"
        fg = t.fg if t else "#eee"
        border = t.border if t else "#444"
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        try:
            tw.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(tw, background=border, padx=1, pady=1)
        frame.pack()
        tk.Label(frame, text=text, justify="left", background=bg, foreground=fg,
                 font="StudioSmall", padx=8, pady=4, wraplength=360).pack()
        x = self.widget.winfo_pointerx() + 14
        y = self.widget.winfo_pointery() + 18
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class ScrollFrame(ttk.Frame):
    """Cadre au contenu défilant verticalement (molette prise en charge)."""

    def __init__(self, parent: tk.Misc, style: str = "Panel.TFrame", **kw: Any) -> None:
        super().__init__(parent, style=style, **kw)
        self.canvas = themed_canvas(self, role="surface")
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style=style)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.bind("<Enter>", lambda e: self._bind_wheel(True), add="+")
        self.bind("<Leave>", self._on_leave, add="+")

    def _on_leave(self, _e=None) -> None:
        # <Leave> est aussi émis en entrant dans un enfant : on vérifie la position réelle.
        x, y = self.winfo_pointerxy()
        x0, y0 = self.winfo_rootx(), self.winfo_rooty()
        if x0 <= x < x0 + self.winfo_width() and y0 <= y < y0 + self.winfo_height():
            return
        self._bind_wheel(False)

    def _on_scroll(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self.vbar.pack_forget()
        elif not self.vbar.winfo_ismapped():
            self.vbar.pack(side="right", fill="y")

    def _on_inner(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)

    def _bind_wheel(self, on: bool) -> None:
        if on:
            self.canvas.bind_all("<MouseWheel>", self._wheel)
            self.canvas.bind_all("<Button-4>", self._wheel)
            self.canvas.bind_all("<Button-5>", self._wheel)
        else:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.unbind_all(seq)

    def _wheel(self, e) -> None:
        if not self.winfo_exists():
            return
        # Ignore la molette si le pointeur est sur un widget qui la gère lui-même
        target = self.winfo_containing(e.x_root, e.y_root)
        if target is not None and target.winfo_class() in ("Canvas", "Text", "Listbox",
                                                           "Treeview") \
                and target is not self.canvas:
            return
        if getattr(e, "num", None) == 4:
            delta = -1
        elif getattr(e, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if e.delta > 0 else 1
        if self.vbar.winfo_ismapped():
            self.canvas.yview_scroll(delta * 2, "units")


class Section(ttk.Frame):
    """Section repliable avec en-tête cliquable (▾ / ▸)."""

    def __init__(self, parent: tk.Misc, title: str, *, opened: bool = True,
                 style: str = "Panel.TFrame") -> None:
        super().__init__(parent, style=style)
        self._open = opened
        self.title = title
        self.header = ttk.Label(self, text=self._caption(), style="Accent.TLabel",
                                cursor="hand2")
        self.header.pack(fill="x", padx=12, pady=(12, 4))
        self.header.bind("<Button-1>", lambda e: self.toggle())
        self.body = ttk.Frame(self, style=style)
        if opened:
            self.body.pack(fill="x", padx=12, pady=(0, 6))

    def _caption(self) -> str:
        return f"{'▾' if self._open else '▸'}  {self.title.upper()}"

    def toggle(self) -> None:
        self._open = not self._open
        self.header.configure(text=self._caption())
        if self._open:
            self.body.pack(fill="x", padx=12, pady=(0, 6))
        else:
            self.body.pack_forget()


class LabeledScale(ttk.Frame):
    """Curseur avec libellé et valeur formatée, éventuellement entier."""

    def __init__(self, parent: tk.Misc, label: str, from_: float, to: float, value: float, *,
                 command: Optional[Callable[[float], Any]] = None, integer: bool = False,
                 fmt: str = "{:.2f}", log: bool = False, style: str = "Panel.TFrame") -> None:
        super().__init__(parent, style=style)
        self.integer = integer
        self.fmt = "{:d}" if integer and fmt == "{:.2f}" else fmt
        self.command = command
        self.log = log
        self.from_, self.to = from_, to
        top = ttk.Frame(self, style=style)
        top.pack(fill="x")
        ttk.Label(top, text=label, style="Secondary.TLabel").pack(side="left")
        self.value_label = ttk.Label(top, text="", style="Mono.TLabel")
        self.value_label.pack(side="right")
        self.var = tk.DoubleVar(value=self._to_slider(value))
        lo, hi = (math.log10(from_), math.log10(to)) if log else (from_, to)
        self.scale = ttk.Scale(self, from_=lo, to=hi, variable=self.var,
                               command=lambda _v: self._changed())
        self.scale.pack(fill="x", pady=(2, 6))
        self._refresh_label()

    def _to_slider(self, v: float) -> float:
        return math.log10(max(v, 1e-12)) if self.log else v

    def get(self) -> float:
        v = self.var.get()
        if self.log:
            v = 10 ** v
        return int(round(v)) if self.integer else v

    def set(self, value: float, fire: bool = False) -> None:
        self.var.set(self._to_slider(value))
        self._refresh_label()
        if fire and self.command:
            self.command(self.get())

    def _refresh_label(self) -> None:
        v = self.get()
        self.value_label.configure(text=self.fmt.format(int(v) if self.integer else v))

    def _changed(self) -> None:
        self._refresh_label()
        if self.command:
            self.command(self.get())


class ColorButton(ttk.Frame):
    """Pastille de couleur cliquable ouvrant le sélecteur système."""

    def __init__(self, parent: tk.Misc, color: str, command: Optional[Callable[[str], Any]] = None,
                 size: int = 22, style: str = "Panel.TFrame") -> None:
        super().__init__(parent, style=style)
        self.color = color
        self.command = command
        self.canvas = themed_canvas(self, role="surface", width=size, height=size,
                                    cursor="hand2")
        self.canvas.pack()
        self.size = size
        self.canvas.bind("<Button-1>", self._pick)
        self._draw()

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        s = self.size
        c.create_oval(2, 2, s - 2, s - 2, fill=self.color, outline=mix(self.color, "#000000", 0.3))

    def set(self, color: str) -> None:
        self.color = color
        self._draw()

    def _pick(self, _e=None) -> None:
        result = colorchooser.askcolor(color=self.color, parent=self)
        if result and result[1]:
            self.set(result[1])
            if self.command:
                self.command(self.color)


class Segmented(ttk.Frame):
    """Contrôle segmenté (boutons radio en style « Toolbutton »)."""

    def __init__(self, parent: tk.Misc, options: Sequence[Tuple[str, str]], value: str,
                 command: Optional[Callable[[str], Any]] = None,
                 style: str = "Panel.TFrame") -> None:
        super().__init__(parent, style=style)
        self.var = tk.StringVar(value=value)
        self.command = command
        for i, (key, label) in enumerate(options):
            rb = ttk.Radiobutton(self, text=label, value=key, variable=self.var,
                                 style="Toolbutton", command=self._changed)
            rb.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 2, 0))
            self.columnconfigure(i, weight=1)

    def _changed(self) -> None:
        if self.command:
            self.command(self.var.get())

    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value)


def form_row(parent: tk.Misc, label: str, widget_factory: Callable[[tk.Misc], tk.Widget],
             style: str = "Panel.TFrame") -> tk.Widget:
    row = ttk.Frame(parent, style=style)
    row.pack(fill="x", pady=3)
    ttk.Label(row, text=label, style="Secondary.TLabel").pack(side="left")
    w = widget_factory(row)
    w.pack(side="right")
    return w


def button_row(parent: tk.Misc, buttons: Sequence[Tuple[str, Callable[[], Any]]],
               style: str = "Panel.TFrame", accent_first: bool = False) -> List[ttk.Button]:
    row = ttk.Frame(parent, style=style)
    row.pack(fill="x", pady=3)
    out = []
    for i, (label, cmd) in enumerate(buttons):
        b = ttk.Button(row, text=label, command=cmd,
                       style="Accent.TButton" if accent_first and i == 0 else "TButton")
        b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
        row.columnconfigure(i, weight=1)
        out.append(b)
    return out


class LineChart(tk.Canvas):
    """Graphique temps réel multi-séries : axes « jolis », légende, réticule au survol.

    Respecte les règles de lisibilité : traits fins de 2 px, quadrillage discret,
    couleurs catégorielles en ordre fixe, texte en encre neutre (jamais la
    couleur de la série), légende dès deux séries.
    """

    def __init__(self, parent: tk.Misc, *, capacity: int = 240, title: str = "",
                 unit: str = "", y_min: Optional[float] = None, y_max: Optional[float] = None,
                 height: int = 160, **kw: Any) -> None:
        kw.setdefault("highlightthickness", 0)
        super().__init__(parent, height=height, **kw)
        self._studio_role = "canvas"
        self.capacity = capacity
        self.title = title
        self.unit = unit
        self.y_min, self.y_max = y_min, y_max
        self.series: Dict[str, Deque[float]] = {}
        self.colors: Dict[str, str] = {}
        self.hover_x: Optional[int] = None
        self._dirty = True
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)

    def add_series(self, name: str, color_index: int) -> None:
        self.series[name] = deque(maxlen=self.capacity)
        self.colors[name] = str(color_index)

    def push(self, name: str, value: float) -> None:
        self.series[name].append(value)
        self._dirty = True

    def clear(self) -> None:
        for d in self.series.values():
            d.clear()
        self.redraw()

    def _motion(self, e) -> None:
        self.hover_x = e.x
        self.redraw()

    def _leave(self, _e) -> None:
        self.hover_x = None
        self.redraw()

    def redraw(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        self.configure(background=t.canvas)
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40 or h < 40:
            return
        left, right, top, bottom = 46, 12, 26 if self.title else 10, 22
        pw, ph = w - left - right, h - top - bottom
        if self.title:
            self.create_text(left, 6, text=self.title, anchor="nw", fill=t.fg,
                             font="StudioBold")
        values = [v for d in self.series.values() for v in d if math.isfinite(v)]
        lo = self.y_min if self.y_min is not None else (min(values) if values else 0.0)
        hi = self.y_max if self.y_max is not None else (max(values) if values else 1.0)
        if hi - lo < 1e-9:
            hi = lo + 1.0
        if self.y_max is None:
            hi += (hi - lo) * 0.08
        step, ticks = nice_ticks(lo, hi, 5)
        if ticks:
            lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])

        def ymap(v: float) -> float:
            return top + ph - (v - lo) / (hi - lo) * ph

        for tv in ticks:
            y = ymap(tv)
            self.create_line(left, y, left + pw, y, fill=t.grid)
            self.create_text(left - 6, y, text=format_tick(tv, step) + self.unit, anchor="e",
                             fill=t.muted, font="StudioSmall")
        self.create_line(left, top + ph, left + pw, top + ph, fill=t.axis)
        n = self.capacity
        dx = pw / max(1, n - 1)
        legend_x = w - right
        for idx, (name, data) in enumerate(self.series.items()):
            color = t.color(int(self.colors[name]))
            pts: List[float] = []
            offset = n - len(data)
            for i, v in enumerate(data):
                if math.isfinite(v):
                    pts += [left + (offset + i) * dx, ymap(v)]
            if len(pts) >= 4:
                self.create_line(*pts, fill=color, width=2, smooth=False, capstyle="round",
                                 joinstyle="round")
            if len(self.series) >= 2:
                label = name
                tw = len(label) * 7 + 22
                legend_x -= tw
                self.create_line(legend_x, 12, legend_x + 12, 12, fill=color, width=2)
                self.create_text(legend_x + 16, 12, text=label, anchor="w", fill=t.fg2,
                                 font="StudioSmall")
        if self.hover_x is not None and left <= self.hover_x <= left + pw:
            i = int(round((self.hover_x - left) / dx))
            x = left + i * dx
            self.create_line(x, top, x, top + ph, fill=t.muted, dash=(2, 3))
            lines = []
            for name, data in self.series.items():
                j = i - (n - len(data))
                if 0 <= j < len(data):
                    v = data[j]
                    lines.append(f"{name} : {v:.2f}{self.unit}")
                    self.create_oval(x - 4, ymap(v) - 4, x + 4, ymap(v) + 4,
                                     fill=t.color(int(self.colors[name])), outline=t.canvas,
                                     width=2)
            if lines:
                text = "\n".join(lines)
                tx = x + 10 if x < left + pw * 0.6 else x - 10
                anchor = "nw" if x < left + pw * 0.6 else "ne"
                item = self.create_text(tx, top + 6, text=text, anchor=anchor, fill=t.fg,
                                        font="StudioSmall", justify="left")
                bb = self.bbox(item)
                if bb:
                    rect = self.create_rectangle(bb[0] - 6, bb[1] - 4, bb[2] + 6, bb[3] + 4,
                                                 fill=t.surface2, outline=t.border)
                    self.tag_lower(rect, item)
        self._dirty = False


class Gauge(tk.Canvas):
    """Jauge circulaire (arc de 270°) avec valeur centrale."""

    def __init__(self, parent: tk.Misc, *, label: str = "", maximum: float = 100.0,
                 unit: str = "%", size: int = 130, **kw: Any) -> None:
        kw.setdefault("highlightthickness", 0)
        super().__init__(parent, width=size, height=size, **kw)
        self._studio_role = "surface2"
        self.label = label
        self.maximum = maximum
        self.unit = unit
        self.value = 0.0
        self.shown = 0.0
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, value: float) -> None:
        self.value = value

    def animate(self) -> None:
        self.shown += (self.value - self.shown) * 0.25
        self.redraw()

    def redraw(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        self.configure(background=t.surface2)
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        s = min(w, h) - 16
        x0, y0 = (w - s) / 2, (h - s) / 2 + 4
        frac = max(0.0, min(1.0, self.shown / self.maximum if self.maximum else 0))
        self.create_arc(x0, y0, x0 + s, y0 + s, start=-45, extent=270, style="arc",
                        outline=t.grid, width=10)
        if frac > 0.002:
            color = t.accent if frac < 0.75 else (t.warning if frac < 0.9 else t.error)
            self.create_arc(x0, y0, x0 + s, y0 + s, start=225, extent=-270 * frac,
                            style="arc", outline=color, width=10)
        self.create_text(w / 2, h / 2, text=f"{self.shown:.0f}{self.unit}", fill=t.fg,
                         font="StudioTitle")
        self.create_text(w / 2, h / 2 + s * 0.28, text=self.label, fill=t.muted,
                         font="StudioSmall")


class Sparkline(tk.Canvas):
    """Petite courbe sans axes pour les tuiles de statistiques."""

    def __init__(self, parent: tk.Misc, capacity: int = 60, color_index: int = 0,
                 **kw: Any) -> None:
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("height", 28)
        super().__init__(parent, **kw)
        self._studio_role = "surface2"
        self.data: Deque[float] = deque(maxlen=capacity)
        self.color_index = color_index

    def push(self, v: float) -> None:
        self.data.append(v)

    def redraw(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        self.configure(background=t.surface2)
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if len(self.data) < 2 or w < 10:
            return
        lo, hi = min(self.data), max(self.data)
        if hi - lo < 1e-9:
            hi = lo + 1
        n = self.data.maxlen or len(self.data)
        dx = w / max(1, n - 1)
        off = n - len(self.data)
        pts = []
        for i, v in enumerate(self.data):
            pts += [(off + i) * dx, h - 3 - (v - lo) / (hi - lo) * (h - 6)]
        color = t.color(self.color_index)
        poly = pts + [pts[-2], h, pts[0], h]
        self.create_polygon(*poly, fill=mix(t.surface2, color, 0.18), outline="")
        self.create_line(*pts, fill=color, width=2)


class StatTile(ttk.Frame):
    """Tuile « chiffre clé » : titre, valeur, sous-texte et mini-courbe."""

    def __init__(self, parent: tk.Misc, title: str, color_index: int = 0) -> None:
        super().__init__(parent, style="Card.TFrame", padding=12)
        ttk.Label(self, text=title, style="Card.Muted.TLabel").pack(anchor="w")
        self.value = ttk.Label(self, text="—", style="Card.Title.TLabel")
        self.value.pack(anchor="w", pady=(2, 0))
        self.sub = ttk.Label(self, text="", style="Card.Small.TLabel")
        self.sub.pack(anchor="w")
        self.spark = Sparkline(self, color_index=color_index)
        self.spark.pack(fill="x", pady=(6, 0))

    def update_value(self, text: str, sub: str = "", spark: Optional[float] = None) -> None:
        self.value.configure(text=text)
        self.sub.configure(text=sub)
        if spark is not None:
            self.spark.push(spark)
            self.spark.redraw()


class FPSCounter:
    def __init__(self) -> None:
        self.times: Deque[float] = deque(maxlen=30)

    def tick(self) -> float:
        now = time.perf_counter()
        self.times.append(now)
        if len(self.times) < 2:
            return 0.0
        return (len(self.times) - 1) / (self.times[-1] - self.times[0])
