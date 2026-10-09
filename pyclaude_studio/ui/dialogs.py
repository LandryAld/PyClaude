"""Boîtes de dialogue : réglages, raccourcis, à propos, détails d'erreur."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import APP_NAME, __version__
from ..core.settings import config_dir
from ..core.sysinfo import static_info
from .widgets import ColorButton, LabeledScale, theme_of

if TYPE_CHECKING:  # pragma: no cover
    from ..app import StudioApp


class _Dialog(tk.Toplevel):
    def __init__(self, app: "StudioApp", title: str, size: str = "640x520") -> None:
        super().__init__(app.root)
        self.app = app
        self.title(title)
        self.geometry(size)
        self.transient(app.root)
        t = theme_of(app.root)
        self.configure(background=t.surface)
        self.body = ttk.Frame(self, style="Panel.TFrame", padding=18)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._center)

    def _center(self) -> None:
        try:
            self.update_idletasks()
            r = self.app.root
            x = r.winfo_rootx() + (r.winfo_width() - self.winfo_width()) // 2
            y = r.winfo_rooty() + (r.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
            self.focus_set()
        except tk.TclError:
            pass


class SettingsDialog(_Dialog):
    def __init__(self, app: "StudioApp") -> None:
        super().__init__(app, "Réglages — " + APP_NAME, "680x560")
        s = app.settings
        ttk.Label(self.body, text="Réglages", style="Title.TLabel").pack(anchor="w")
        nb = ttk.Notebook(self.body)
        nb.pack(fill="both", expand=True, pady=(12, 0))

        # Apparence
        look = ttk.Frame(nb, style="Panel.TFrame", padding=16)
        nb.add(look, text="  Apparence  ")
        ttk.Label(look, text="Thème", style="Heading.TLabel").pack(anchor="w")
        grid = ttk.Frame(look, style="Panel.TFrame")
        grid.pack(fill="x", pady=(6, 14))
        from ..core.theme import THEMES

        for i, (name, theme) in enumerate(THEMES.items()):
            card = tk.Canvas(grid, width=120, height=70, highlightthickness=2,
                             highlightbackground=theme.border, cursor="hand2",
                             background=theme.bg)
            card._studio_role = "skip"  # type: ignore[attr-defined]
            card.grid(row=i // 3, column=i % 3, padx=6, pady=6)
            card.create_rectangle(8, 8, 40, 62, fill=theme.surface, outline="")
            card.create_rectangle(48, 8, 112, 30, fill=theme.surface2, outline="")
            card.create_rectangle(48, 38, 80, 48, fill=theme.accent, outline="")
            for k in range(4):
                card.create_rectangle(84 + k * 7, 38, 89 + k * 7, 48, fill=theme.series[k],
                                      outline="")
            card.create_text(60, 56, text=name, fill=theme.fg, font="StudioSmall", anchor="w")
            card.bind("<Button-1>", lambda e, n=name: app.set_theme(n))

        row = ttk.Frame(look, style="Panel.TFrame")
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="Couleur d'accent personnalisée", style="Secondary.TLabel").pack(
            side="left")
        ttk.Button(row, text="Réinitialiser", style="Ghost.TButton",
                   command=lambda: app.set_accent(None)).pack(side="right")
        ColorButton(row, s.get("ui.accent") or app.theme.accent,
                    command=lambda c: app.set_accent(c)).pack(side="right", padx=8)
        LabeledScale(look, "Taille du texte", 0.8, 1.6, float(s.get("ui.font_scale") or 1.0),
                     fmt="{:.0%}", command=lambda v: app.set_font_scale(round(v, 2))).pack(
            fill="x", pady=(10, 0))

        # Comportement
        beh = ttk.Frame(nb, style="Panel.TFrame", padding=16)
        nb.add(beh, text="  Comportement  ")
        for key, label in (("ui.splash", "Afficher l'écran de démarrage"),
                           ("ui.animations", "Animations de l'interface"),
                           ("ui.toasts", "Notifications (les erreurs restent affichées)"),
                           ("session.restore", "Restaurer les onglets au démarrage")):
            var = tk.BooleanVar(value=bool(s.get(key)))
            ttk.Checkbutton(beh, text=label, variable=var,
                            command=lambda k=key, v=var: s.set(k, v.get())).pack(anchor="w",
                                                                                 pady=3)
        ttk.Label(beh, text="Moteur de rendu des fractales", style="Heading.TLabel").pack(
            anchor="w", pady=(16, 4))
        eng = tk.StringVar(value=s.get("fractal.engine"))
        for value, label in (("auto", "Automatique (numpy si disponible, sinon multiprocessus)"),
                             ("numpy", "numpy (vectorisé)"),
                             ("process", "Multiprocessus (Python pur, tous les cœurs)"),
                             ("thread", "Thread unique (Python pur)")):
            ttk.Radiobutton(beh, text=label, value=value, variable=eng,
                            command=lambda: s.set("fractal.engine", eng.get())).pack(
                anchor="w", pady=2)
        LabeledScale(beh, "Images par seconde maximum", 15, 120, int(s.get("perf.max_fps")),
                     integer=True, command=lambda v: s.set("perf.max_fps", int(v))).pack(
            fill="x", pady=(16, 0))

        # Données
        data = ttk.Frame(nb, style="Panel.TFrame", padding=16)
        nb.add(data, text="  Données  ")
        ttk.Label(data, text="Dossier de configuration", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(data, text=str(config_dir()), style="Mono.TLabel").pack(anchor="w", pady=4)
        ttk.Label(data, text="Les plugins utilisateur (*.py) se déposent dans le sous-dossier "
                  "« plugins » puis sont chargés au prochain démarrage.",
                  style="Muted.TLabel", wraplength=560).pack(anchor="w", pady=(0, 12))
        ttk.Button(data, text="Enregistrer la session maintenant",
                   command=app.save_session).pack(anchor="w", pady=3)
        ttk.Button(data, text="Réinitialiser tous les réglages…", style="Danger.TButton",
                   command=self._reset).pack(anchor="w", pady=3)

        ttk.Button(self.body, text="Fermer", style="Accent.TButton",
                   command=self.destroy).pack(anchor="e", pady=(12, 0))

    def _reset(self) -> None:
        if messagebox.askyesno("Réinitialiser", "Rétablir tous les réglages par défaut ?\n"
                               "Les états des modules seront effacés.", parent=self):
            self.app.settings.reset()
            self.app.settings.save()
            self.app.set_theme(self.app.settings.get("ui.theme"))
            self.app.notify("Réglages réinitialisés", "success")


class ShortcutsDialog(_Dialog):
    def __init__(self, app: "StudioApp") -> None:
        super().__init__(app, "Raccourcis clavier", "640x560")
        ttk.Label(self.body, text="Raccourcis clavier", style="Title.TLabel").pack(anchor="w")
        tree = ttk.Treeview(self.body, columns=("key", "cat"), show="tree headings")
        tree.heading("#0", text="Commande")
        tree.heading("key", text="Raccourci")
        tree.heading("cat", text="Catégorie")
        tree.column("#0", width=300)
        tree.column("key", width=140)
        tree.column("cat", width=140)
        cmds = sorted((c for c in app.commands.all() if c.shortcut),
                      key=lambda c: (c.category, c.title))
        for c in cmds:
            tree.insert("", "end", text=c.title,
                        values=(c.shortcut.replace("Shift", "Maj"), c.category))
        tree.pack(fill="both", expand=True, pady=12)


class AboutDialog(_Dialog):
    def __init__(self, app: "StudioApp") -> None:
        super().__init__(app, "À propos", "560x480")
        ttk.Label(self.body, text="◈", style="Icon.TLabel").pack(anchor="w")
        ttk.Label(self.body, text=f"{APP_NAME} {__version__}", style="Title.TLabel").pack(
            anchor="w")
        ttk.Label(self.body, text="Un atelier scientifique et créatif entièrement écrit en "
                  "Python et Tkinter, sans dépendance obligatoire : moteur d'expressions "
                  "symboliques, tableur réactif, moteur 3D logiciel, physique de Verlet, "
                  "Barnes-Hut, réseau de neurones, fractales multiprocessus…",
                  style="Secondary.TLabel", wraplength=500, justify="left").pack(
            anchor="w", pady=(6, 14))
        info = dict(static_info())
        info["Tk"] = str(app.root.tk.call("info", "patchlevel"))
        info["Modules"] = str(len(app.registry.specs))
        info["Commandes"] = str(len(app.commands.all()))
        try:
            import numpy  # noqa: F401

            info["numpy"] = numpy.__version__
        except Exception:
            info["numpy"] = "absent (repli en Python pur)"
        for k, v in info.items():
            row = ttk.Frame(self.body, style="Panel.TFrame")
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=k, style="Muted.TLabel", width=14).pack(side="left")
            ttk.Label(row, text=v, style="Mono.TLabel").pack(side="left")


class ErrorDialog(_Dialog):
    def __init__(self, app: "StudioApp", title: str, details: str) -> None:
        super().__init__(app, title, "720x420")
        ttk.Label(self.body, text=title, style="Heading.TLabel").pack(anchor="w")
        txt = tk.Text(self.body, height=16, font="StudioMono", wrap="none")
        txt.insert("1.0", details)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, pady=10)
        app.themes.recolor_tree(self)
