"""Thèmes : palettes complètes, polices nommées et habillage ttk.

Un :class:`Theme` décrit toutes les couleurs de l'interface (surfaces, encres,
accent, séries de graphiques, coloration syntaxique). :class:`ThemeManager`
applique le thème au style ttk, recolore les widgets Tk classiques déjà créés
et publie ``theme.changed`` pour que les modules redessinent leurs canevas.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field, replace
from tkinter import ttk
from typing import Dict, List, Optional

from .colors import (SERIES_DARK, SERIES_LIGHT, STATUS, best_text_color, darken, is_dark,
                     lighten, mix)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    bg: str          # arrière-plan de la fenêtre
    surface: str     # panneaux
    surface2: str    # champs, cartes
    border: str
    fg: str          # encre principale
    fg2: str         # encre secondaire
    muted: str       # encre discrète (axes, légendes)
    accent: str
    canvas: str      # surface des graphiques
    grid: str        # quadrillage (filet)
    axis: str        # ligne de base
    series: List[str] = field(default_factory=list)
    syntax: Dict[str, str] = field(default_factory=dict)

    @property
    def accent_fg(self) -> str:
        return best_text_color(self.accent)

    @property
    def hover(self) -> str:
        return mix(self.surface2, self.fg, 0.08)

    @property
    def pressed(self) -> str:
        return mix(self.surface2, self.fg, 0.16)

    @property
    def select_bg(self) -> str:
        return mix(self.surface2, self.accent, 0.35)

    @property
    def accent_soft(self) -> str:
        return mix(self.surface, self.accent, 0.18)

    @property
    def sidebar(self) -> str:
        return mix(self.bg, self.surface, 0.5)

    @property
    def success(self) -> str:
        return STATUS["good"]

    @property
    def warning(self) -> str:
        return STATUS["warning"]

    @property
    def error(self) -> str:
        return STATUS["critical"]

    @property
    def info(self) -> str:
        return self.accent

    def color(self, i: int) -> str:
        return self.series[i % len(self.series)]


def _syntax(dark: bool, accent: str) -> Dict[str, str]:
    if dark:
        return {"keyword": "#c792ea", "string": "#c3e88d", "comment": "#7f8696",
                "number": "#f78c6c", "builtin": "#82aaff", "def": "#ffcb6b",
                "decorator": "#89ddff", "self": "#f07178", "op": "#89ddff",
                "error": "#ff5370", "match": mix("#000000", accent, 0.45)}
    return {"keyword": "#7c3aed", "string": "#2f7d32", "comment": "#8a8a80",
            "number": "#c2410c", "builtin": "#1d4ed8", "def": "#a16207",
            "decorator": "#0e7490", "self": "#be123c", "op": "#0e7490",
            "error": "#dc2626", "match": mix("#ffffff", accent, 0.35)}


def _make(name: str, dark: bool, **colors: str) -> Theme:
    series = list(SERIES_DARK if dark else SERIES_LIGHT)
    return Theme(name=name, dark=dark, series=series,
                 syntax=_syntax(dark, colors["accent"]), **colors)


THEMES: Dict[str, Theme] = {t.name: t for t in [
    _make("Nuit", True, bg="#111114", surface="#18181c", surface2="#212127",
          border="#2e2e36", fg="#ececf1", fg2="#b6b6c2", muted="#83838f",
          accent="#7c6cff", canvas="#141417", grid="#26262c", axis="#3a3a42"),
    _make("Clair", False, bg="#f1f1ee", surface="#f9f9f7", surface2="#ffffff",
          border="#dcdbd5", fg="#0b0b0b", fg2="#52514e", muted="#898781",
          accent="#2a78d6", canvas="#fcfcfb", grid="#e8e7e1", axis="#c3c2b7"),
    _make("Océan", True, bg="#0b1220", surface="#101a2c", surface2="#16233a",
          border="#22344f", fg="#e6eef9", fg2="#a9bad3", muted="#7287a5",
          accent="#38bdf8", canvas="#0d1626", grid="#1a2840", axis="#2b3f5e"),
    _make("Néon", True, bg="#0b0418", surface="#120826", surface2="#1b0d38",
          border="#2e1760", fg="#f5f3ff", fg2="#c4b5fd", muted="#8b7fb8",
          accent="#ff2a6d", canvas="#0e0620", grid="#20103d", axis="#3a1f6b"),
    _make("Forêt", True, bg="#0e1511", surface="#131d17", surface2="#1a271f",
          border="#283a2e", fg="#e7f0ea", fg2="#b0c4b6", muted="#7d9585",
          accent="#3ddc84", canvas="#101914", grid="#1d2b22", axis="#2f4537"),
    _make("Sable", False, bg="#eee8d5", surface="#f7f1df", surface2="#fdf6e3",
          border="#d9d0b4", fg="#073642", fg2="#3d5a63", muted="#839496",
          accent="#268bd2", canvas="#fdf6e3", grid="#ebe3c9", axis="#cfc5a6"),
]}

_SANS = ["Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Cantarell", "Ubuntu",
         "Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial", "Helvetica"]
_MONO = ["Cascadia Code", "JetBrains Mono", "Fira Code", "Consolas", "SF Mono", "Menlo",
         "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New", "Courier"]

FONT_SIZES = {"StudioBody": 10, "StudioSmall": 9, "StudioBold": 10, "StudioHeading": 11,
              "StudioTitle": 15, "StudioDisplay": 24, "StudioMono": 10, "StudioIcon": 13,
              "StudioIconLarge": 22}


def with_accent(theme: Theme, accent: Optional[str]) -> Theme:
    if not accent:
        return theme
    return replace(theme, accent=accent, syntax=_syntax(theme.dark, accent))


class ThemeManager:
    def __init__(self, root: tk.Misc, bus=None, settings=None) -> None:
        self.root = root
        self.bus = bus
        self.settings = settings
        self.style = ttk.Style(root)
        self.theme: Theme = THEMES["Nuit"]
        self.scale = 1.0
        self.fonts: Dict[str, tkfont.Font] = {}
        families = set(tkfont.families(root))
        self.sans = next((f for f in _SANS if f in families), "TkDefaultFont")
        self.mono = next((f for f in _MONO if f in families), "TkFixedFont")
        self._create_fonts()

    # -- polices --------------------------------------------------------
    def _create_fonts(self) -> None:
        for name, size in FONT_SIZES.items():
            family = self.mono if name == "StudioMono" else self.sans
            weight = "bold" if name in ("StudioBold", "StudioHeading", "StudioTitle",
                                        "StudioDisplay") else "normal"
            try:
                font = tkfont.Font(self.root, name=name, family=family, size=size,
                                   weight=weight, exists=False)
            except tk.TclError:
                font = tkfont.Font(self.root, name=name, exists=True)
                font.configure(family=family, size=size, weight=weight)
            self.fonts[name] = font
        self.set_scale(self.scale)

    def set_scale(self, scale: float) -> None:
        self.scale = max(0.7, min(2.0, scale))
        for name, base in FONT_SIZES.items():
            self.fonts[name].configure(size=max(6, int(round(base * self.scale))))
        for name, base in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10),
                           ("TkHeadingFont", 10), ("TkTooltipFont", 9)):
            try:
                f = tkfont.nametofont(name)
                f.configure(family=self.sans, size=max(6, int(round(base * self.scale))))
            except tk.TclError:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(
                family=self.mono, size=max(6, int(round(10 * self.scale))))
        except tk.TclError:
            pass
        self.style.configure("Treeview", rowheight=int(24 * self.scale))
        if self.bus is not None:
            self.bus.publish("theme.fonts", scale=self.scale)

    def font(self, name: str) -> tkfont.Font:
        return self.fonts[name]

    # -- application ---------------------------------------------------
    @property
    def names(self) -> List[str]:
        return list(THEMES)

    def apply(self, name: Optional[str] = None, accent: Optional[str] = None) -> Theme:
        base = THEMES.get(name or self.theme.name) or THEMES["Nuit"]
        if accent is None and self.settings is not None:
            accent = self.settings.get("ui.accent")
        self.theme = with_accent(base, accent)
        self._configure_style(self.theme)
        self._configure_options(self.theme)
        self.recolor_tree(self.root)
        if self.bus is not None:
            self.bus.publish("theme.changed", theme=self.theme)
        return self.theme

    def cycle(self, step: int = 1) -> Theme:
        names = self.names
        i = names.index(self.theme.name) if self.theme.name in names else 0
        return self.apply(names[(i + step) % len(names)])

    def _configure_options(self, t: Theme) -> None:
        r = self.root
        r.option_add("*TCombobox*Listbox.background", t.surface2)
        r.option_add("*TCombobox*Listbox.foreground", t.fg)
        r.option_add("*TCombobox*Listbox.selectBackground", t.accent)
        r.option_add("*TCombobox*Listbox.selectForeground", t.accent_fg)
        r.option_add("*TCombobox*Listbox.font", "StudioBody")
        r.option_add("*Menu.background", t.surface2)
        r.option_add("*Menu.foreground", t.fg)
        r.option_add("*Menu.activeBackground", t.accent)
        r.option_add("*Menu.activeForeground", t.accent_fg)
        r.option_add("*Menu.relief", "flat")
        r.option_add("*Menu.font", "StudioBody")
        r.option_add("*Text.background", t.surface2)
        r.option_add("*Text.foreground", t.fg)
        r.option_add("*Listbox.background", t.surface2)
        r.option_add("*Listbox.foreground", t.fg)
        r.option_add("*Canvas.highlightThickness", 0)
        try:
            r.configure(background=t.bg)
        except tk.TclError:
            pass

    def recolor_tree(self, widget: tk.Misc) -> None:
        """Recolore les widgets Tk classiques (non ttk) déjà construits."""
        t = self.theme
        stack = [widget]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            role = getattr(w, "_studio_role", None)
            if role == "skip":
                continue
            try:
                cls = w.winfo_class()
                if cls == "Menu":
                    w.configure(background=t.surface2, foreground=t.fg,
                                activebackground=t.accent, activeforeground=t.accent_fg,
                                selectcolor=t.accent, disabledforeground=t.muted,
                                borderwidth=0, relief="flat")
                elif cls in ("Text", "Listbox"):
                    w.configure(background=t.surface2 if role != "code" else t.canvas,
                                foreground=t.fg, selectbackground=t.select_bg,
                                selectforeground=t.fg, highlightthickness=0,
                                borderwidth=0)
                    if cls == "Text":
                        w.configure(insertbackground=t.fg, inactiveselectbackground=t.select_bg)
                elif cls == "Canvas":
                    bg = {"canvas": t.canvas, "surface": t.surface, "surface2": t.surface2,
                          "bg": t.bg, "sidebar": t.sidebar}.get(role or "surface", t.surface)
                    w.configure(background=bg, highlightthickness=0, borderwidth=0)
                elif cls in ("Frame", "Toplevel", "Tk"):
                    bg = {"surface2": t.surface2, "bg": t.bg, "canvas": t.canvas,
                          "border": t.border, "sidebar": t.sidebar}.get(role or "", t.surface)
                    w.configure(background=bg)
                elif cls == "Label":
                    bg = {"surface2": t.surface2, "bg": t.bg,
                          "sidebar": t.sidebar}.get(role or "", t.surface)
                    w.configure(background=bg, foreground=t.fg)
                elif cls == "TCombobox":
                    try:
                        popdown = w.tk.call("ttk::combobox::PopdownWindow", w)
                        w.tk.call(f"{popdown}.f.l", "configure", "-background", t.surface2,
                                  "-foreground", t.fg, "-selectbackground", t.accent,
                                  "-selectforeground", t.accent_fg)
                    except tk.TclError:
                        pass
            except tk.TclError:
                continue

    def _configure_style(self, t: Theme) -> None:
        s = self.style
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        sel = t.select_bg
        s.configure(".", background=t.surface, foreground=t.fg, fieldbackground=t.surface2,
                    bordercolor=t.border, darkcolor=t.surface, lightcolor=t.surface,
                    troughcolor=t.bg, focuscolor=t.accent, selectbackground=sel,
                    selectforeground=t.fg, insertcolor=t.fg, font="StudioBody",
                    relief="flat", borderwidth=1, arrowcolor=t.fg2)
        s.map(".", foreground=[("disabled", t.muted)])

        # Cadres
        s.configure("TFrame", background=t.surface)
        s.configure("App.TFrame", background=t.bg)
        s.configure("Module.TFrame", background=t.surface)
        s.configure("Card.TFrame", background=t.surface2, bordercolor=t.border,
                    relief="solid", borderwidth=1)
        s.configure("Flat.TFrame", background=t.surface2)
        s.configure("Sidebar.TFrame", background=t.sidebar)
        s.configure("Header.TFrame", background=t.bg)
        s.configure("Status.TFrame", background=t.bg)
        s.configure("Toolbar.TFrame", background=t.surface)
        s.configure("Panel.TFrame", background=t.surface)
        s.configure("Overlay.TFrame", background=t.surface2, bordercolor=t.accent,
                    relief="solid", borderwidth=1)

        # Étiquettes
        s.configure("TLabel", background=t.surface, foreground=t.fg)
        for prefix, bg in (("", t.surface), ("Card.", t.surface2), ("Sidebar.", t.sidebar),
                           ("Header.", t.bg), ("Status.", t.bg), ("Overlay.", t.surface2)):
            s.configure(f"{prefix}TLabel", background=bg, foreground=t.fg)
            s.configure(f"{prefix}Muted.TLabel", background=bg, foreground=t.muted)
            s.configure(f"{prefix}Secondary.TLabel", background=bg, foreground=t.fg2)
            s.configure(f"{prefix}Title.TLabel", background=bg, foreground=t.fg,
                        font="StudioTitle")
            s.configure(f"{prefix}Heading.TLabel", background=bg, foreground=t.fg,
                        font="StudioHeading")
            s.configure(f"{prefix}Display.TLabel", background=bg, foreground=t.fg,
                        font="StudioDisplay")
            s.configure(f"{prefix}Accent.TLabel", background=bg, foreground=t.accent,
                        font="StudioBold")
            s.configure(f"{prefix}Icon.TLabel", background=bg, foreground=t.accent,
                        font="StudioIconLarge")
            s.configure(f"{prefix}Small.TLabel", background=bg, foreground=t.muted,
                        font="StudioSmall")
            s.configure(f"{prefix}Mono.TLabel", background=bg, foreground=t.fg2,
                        font="StudioMono")
        s.configure("Badge.TLabel", background=t.accent_soft, foreground=t.accent,
                    font="StudioSmall", padding=(6, 1))
        s.configure("Error.TLabel", background=t.surface, foreground=t.error)
        s.configure("Success.TLabel", background=t.surface, foreground=t.success)

        # Boutons
        s.configure("TButton", background=t.surface2, foreground=t.fg, bordercolor=t.border,
                    lightcolor=t.surface2, darkcolor=t.surface2, padding=(10, 5),
                    focusthickness=0, anchor="center")
        s.map("TButton",
              background=[("disabled", t.surface), ("pressed", t.pressed), ("active", t.hover)],
              bordercolor=[("focus", t.accent), ("active", mix(t.border, t.accent, 0.5))],
              lightcolor=[("pressed", t.pressed), ("active", t.hover)],
              darkcolor=[("pressed", t.pressed), ("active", t.hover)])
        acc_hover = lighten(t.accent, 0.12) if t.dark else darken(t.accent, 0.1)
        s.configure("Accent.TButton", background=t.accent, foreground=t.accent_fg,
                    bordercolor=t.accent, lightcolor=t.accent, darkcolor=t.accent,
                    font="StudioBold")
        s.map("Accent.TButton",
              background=[("disabled", t.surface2), ("pressed", darken(t.accent, 0.15)),
                          ("active", acc_hover)],
              lightcolor=[("active", acc_hover)], darkcolor=[("active", acc_hover)],
              bordercolor=[("active", acc_hover)],
              foreground=[("disabled", t.muted)])
        s.configure("Danger.TButton", background=t.surface2, foreground=t.error,
                    bordercolor=mix(t.border, t.error, 0.5))
        s.map("Danger.TButton", background=[("active", mix(t.surface2, t.error, 0.15))])
        for name, bg in (("Ghost.TButton", t.surface), ("Header.TButton", t.bg),
                         ("Sidebar.TButton", t.sidebar), ("Card.TButton", t.surface2)):
            s.configure(name, background=bg, foreground=t.fg2, bordercolor=bg,
                        lightcolor=bg, darkcolor=bg, padding=(8, 4))
            s.map(name, background=[("pressed", t.pressed), ("active", t.hover)],
                  foreground=[("active", t.fg)],
                  bordercolor=[("active", t.hover)], lightcolor=[("active", t.hover)],
                  darkcolor=[("active", t.hover)])
        s.configure("Icon.TButton", background=t.surface, foreground=t.fg2,
                    bordercolor=t.surface, lightcolor=t.surface, darkcolor=t.surface,
                    padding=(6, 2), font="StudioIcon")
        s.map("Icon.TButton", background=[("active", t.hover)], foreground=[("active", t.fg)],
              bordercolor=[("active", t.hover)], lightcolor=[("active", t.hover)],
              darkcolor=[("active", t.hover)])

        # Bascules (barres segmentées)
        s.configure("Toolbutton", background=t.surface2, foreground=t.fg2,
                    bordercolor=t.border, lightcolor=t.surface2, darkcolor=t.surface2,
                    padding=(10, 4), anchor="center")
        s.map("Toolbutton",
              background=[("selected", t.accent), ("active", t.hover)],
              foreground=[("selected", t.accent_fg), ("active", t.fg)],
              lightcolor=[("selected", t.accent)], darkcolor=[("selected", t.accent)],
              bordercolor=[("selected", t.accent)])

        # Cases et boutons radio
        for name in ("TCheckbutton", "TRadiobutton"):
            s.configure(name, background=t.surface, foreground=t.fg,
                        indicatorbackground=t.surface2, indicatorforeground=t.accent_fg,
                        indicatorcolor=t.surface2, bordercolor=t.border,
                        focusthickness=0, padding=(2, 3))
            s.map(name,
                  indicatorbackground=[("selected", t.accent), ("active", t.hover)],
                  indicatorcolor=[("selected", t.accent), ("active", t.hover)],
                  background=[("active", t.surface)])
        s.configure("Card.TCheckbutton", background=t.surface2)

        # Champs
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            s.configure(name, fieldbackground=t.surface2, foreground=t.fg,
                        bordercolor=t.border, lightcolor=t.surface2, darkcolor=t.surface2,
                        insertcolor=t.fg, padding=5, arrowcolor=t.fg2,
                        background=t.surface2, selectbackground=sel, selectforeground=t.fg)
            s.map(name,
                  bordercolor=[("focus", t.accent)],
                  lightcolor=[("focus", t.accent)],
                  fieldbackground=[("readonly", t.surface2), ("disabled", t.surface)],
                  foreground=[("disabled", t.muted)],
                  background=[("active", t.hover)],
                  selectbackground=[("readonly", t.surface2)],
                  selectforeground=[("readonly", t.fg)])
        s.configure("Search.TEntry", fieldbackground=t.surface, padding=(8, 6))
        s.configure("Formula.TEntry", font="StudioMono")

        # Défilement fin, sans flèches
        for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
            s.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {
                "sticky": sticky, "children": [(f"{orient}.Scrollbar.thumb",
                                                {"expand": "1", "sticky": "nswe"})]})])
            s.configure(f"{orient}.TScrollbar", troughcolor=t.surface, background=t.border,
                        bordercolor=t.surface, lightcolor=t.border, darkcolor=t.border,
                        gripcount=0, arrowsize=10, width=10)
            s.map(f"{orient}.TScrollbar",
                  background=[("active", t.muted), ("pressed", t.fg2)],
                  lightcolor=[("active", t.muted)], darkcolor=[("active", t.muted)])

        # Curseurs
        for orient in ("Horizontal", "Vertical"):
            s.configure(f"{orient}.TScale", background=t.accent, troughcolor=t.surface2,
                        bordercolor=t.border, lightcolor=t.accent, darkcolor=t.accent,
                        gripcount=0, sliderlength=14, sliderthickness=14)
            s.map(f"{orient}.TScale", background=[("active", lighten(t.accent, 0.15))])

        # Barres de progression
        for orient in ("Horizontal", "Vertical"):
            s.configure(f"{orient}.TProgressbar", troughcolor=t.surface2, background=t.accent,
                        bordercolor=t.surface2, lightcolor=t.accent, darkcolor=t.accent,
                        thickness=6)

        # Onglets
        s.configure("TNotebook", background=t.surface, bordercolor=t.border,
                    tabmargins=(4, 4, 4, 0), lightcolor=t.surface, darkcolor=t.surface)
        s.configure("TNotebook.Tab", background=t.surface, foreground=t.muted,
                    padding=(12, 5), bordercolor=t.surface, lightcolor=t.surface,
                    darkcolor=t.surface, focuscolor=t.surface)
        s.map("TNotebook.Tab",
              background=[("selected", t.surface2), ("active", t.hover)],
              foreground=[("selected", t.fg), ("active", t.fg)],
              lightcolor=[("selected", t.accent)],
              bordercolor=[("selected", t.border)],
              expand=[("selected", (0, 2, 0, 0))])

        # Arbres / tableaux
        s.configure("Treeview", background=t.surface2, fieldbackground=t.surface2,
                    foreground=t.fg, bordercolor=t.border, lightcolor=t.surface2,
                    darkcolor=t.surface2, rowheight=int(24 * self.scale))
        s.map("Treeview", background=[("selected", sel)], foreground=[("selected", t.fg)])
        s.configure("Treeview.Heading", background=t.surface, foreground=t.fg2,
                    bordercolor=t.border, lightcolor=t.surface, darkcolor=t.surface,
                    relief="flat", font="StudioBold", padding=(6, 4))
        s.map("Treeview.Heading", background=[("active", t.hover)])

        # Divers
        s.configure("TLabelframe", background=t.surface, bordercolor=t.border,
                    lightcolor=t.surface, darkcolor=t.surface)
        s.configure("TLabelframe.Label", background=t.surface, foreground=t.fg2,
                    font="StudioBold")
        s.configure("TPanedwindow", background=t.bg)
        s.configure("Sash", sashthickness=5, gripcount=0, background=t.bg,
                    lightcolor=t.bg, bordercolor=t.bg)
        s.configure("TSeparator", background=t.border)
        s.configure("TMenubutton", background=t.surface2, foreground=t.fg,
                    bordercolor=t.border, lightcolor=t.surface2, darkcolor=t.surface2,
                    arrowcolor=t.fg2, padding=(8, 4))
        s.map("TMenubutton", background=[("active", t.hover)])
        s.configure("TSizegrip", background=t.bg)


def windows_dpi_awareness() -> None:
    """Active la netteté HiDPI sous Windows (sans effet ailleurs)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[name-defined]
        except Exception:
            pass


__all__ = ["Theme", "THEMES", "ThemeManager", "with_accent", "windows_dpi_awareness",
           "is_dark"]
