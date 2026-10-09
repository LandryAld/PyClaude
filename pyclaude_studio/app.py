"""Application principale : assemble les services du noyau et la coquille graphique."""

from __future__ import annotations

import gc
import json
import logging
import sys
import time
import tkinter as tk
import traceback
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional

from . import APP_NAME, __version__
from .core import expr, numeric
from .core.commands import CommandRegistry
from .core.eventbus import EventBus
from .core.module import ModuleRegistry, StudioModule
from .core.settings import Settings, config_dir
from .core.tasks import TaskManager
from .core.theme import THEMES, Theme, ThemeManager, windows_dpi_awareness
from .ui.console import BottomPanel, QueueLogHandler
from .ui.dialogs import AboutDialog, ErrorDialog, SettingsDialog, ShortcutsDialog
from .ui.palette import CommandPalette
from .ui.shell import Header, Sidebar, Splash, StatusBar
from .ui.tabbar import TabBar
from .ui.toast import ToastManager

log = logging.getLogger("studio")


class StudioApp:
    def __init__(self, root: Optional[tk.Tk] = None, *, theme: Optional[str] = None,
                 splash: Optional[bool] = None, restore: Optional[bool] = None,
                 plugins: bool = True, settings: Optional[Settings] = None) -> None:
        windows_dpi_awareness()
        self.root = root or tk.Tk(className="PyClaudeStudio")
        self.root._studio_app = self  # type: ignore[attr-defined]
        self.root.title(APP_NAME)
        self.root.minsize(980, 620)
        self.settings = settings or Settings.load()
        self.started = time.time()

        # Journalisation vers le panneau « Journal »
        self.log_handler = QueueLogHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_handler)
        root_logger.setLevel(logging.INFO)

        self.bus = EventBus()
        self.bus.attach(self.root)
        self.tasks = TaskManager(self.bus)
        self.tasks.attach(self.root)
        self.themes = ThemeManager(self.root, self.bus, self.settings)
        self.themes.set_scale(float(self.settings.get("ui.font_scale") or 1.0))
        self.themes.apply(theme or self.settings.get("ui.theme"))

        show_splash = self.settings.get("ui.splash") if splash is None else splash
        self._splash: Optional[Splash] = None
        if show_splash:
            self.root.withdraw()
            try:
                self._splash = Splash(self.root, self.theme)
            except tk.TclError:
                self._splash = None
        self._splash_step(0.15, "Découverte des modules…")

        self.commands = CommandRegistry(self.root, self.bus)
        self.registry = ModuleRegistry().discover(
            plugin_dir=(config_dir() / "plugins") if plugins else None)
        self.instances: Dict[str, StudioModule] = {}
        self.active_id: Optional[str] = None
        self.closed_history: List[str] = []
        self._demo_after: Optional[str] = None

        self._splash_step(0.45, "Construction de l'interface…")
        self._build_ui()
        self._splash_step(0.65, "Enregistrement des commandes…")
        self._register_commands()
        self._build_menu()

        self.bus.subscribe("notify", lambda e: self.notify(e.get("message", ""),
                                                           e.get("kind", "info")))
        self.bus.subscribe("theme.changed", self._on_theme)
        self.bus.subscribe("task.failed", lambda e: self.notify(
            f"Tâche « {e['task'].name} » en échec : {e['task'].error}", "error"))
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.report_callback_exception = self._report_exception  # type: ignore[assignment]

        self._splash_step(0.85, "Restauration de la session…")
        self._restore_window()
        do_restore = self.settings.get("session.restore") if restore is None else restore
        self._restore_session(bool(do_restore))
        self._splash_step(1.0, "Prêt")
        if self._splash is not None:
            self._splash.close()
            self._splash = None
            self.root.deiconify()
        self.root.after(60_000, self._autosave)
        n_mod = len(self.registry.specs)
        log.info("%s %s démarré (%d modules, %d commandes)", APP_NAME, __version__, n_mod,
                 len(self.commands.all()))
        for failure in self.registry.failures:
            self.notify(f"Module en erreur : {failure.origin}", "error")

    # ------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------
    @property
    def theme(self) -> Theme:
        return self.themes.theme

    @property
    def active_module(self) -> Optional[StudioModule]:
        return self.instances.get(self.active_id) if self.active_id else None

    def _splash_step(self, progress: float, text: str) -> None:
        if self._splash is not None:
            try:
                self._splash.step(progress, text)
            except tk.TclError:
                self._splash = None

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = self.root
        self.shell = ttk.Frame(root, style="App.TFrame")
        self.shell.pack(fill="both", expand=True)
        self.header = Header(self.shell, self)
        self.header.pack(fill="x")
        self.statusbar = StatusBar(self.shell, self)
        self.statusbar.pack(fill="x", side="bottom")
        self.body = ttk.Panedwindow(self.shell, orient="horizontal")
        self.body.pack(fill="both", expand=True)
        self.sidebar = Sidebar(self.body, self)
        self.sidebar.canvas.configure(width=200)
        self.right = ttk.Panedwindow(self.body, orient="vertical")
        if self.settings.get("ui.sidebar"):
            self.body.add(self.sidebar, weight=0)
        self.body.add(self.right, weight=1)
        self.workspace = ttk.Frame(self.right, style="App.TFrame")
        self.tabbar = TabBar(self.workspace, on_select=self.activate, on_close=self.close_module,
                             on_reorder=self._on_reorder, on_context=self._tab_menu)
        self.tabbar.pack(fill="x")
        self.host = ttk.Frame(self.workspace, style="Module.TFrame")
        self.host.pack(fill="both", expand=True)
        self.empty = ttk.Frame(self.host, style="Module.TFrame")
        ttk.Label(self.empty, text="◈", style="Icon.TLabel").pack(pady=(140, 6))
        ttk.Label(self.empty, text="Aucun module ouvert", style="Title.TLabel").pack()
        ttk.Label(self.empty, text="Choisissez un module dans la barre latérale ou ouvrez la "
                  "palette de commandes (Ctrl+Maj+P).", style="Muted.TLabel").pack(pady=6)
        ttk.Button(self.empty, text="Ouvrir l'accueil", style="Accent.TButton",
                   command=lambda: self.open_module("home")).pack(pady=10)
        self.right.add(self.workspace, weight=1)
        namespace = {"app": self, "bus": self.bus, "settings": self.settings,
                     "tasks": self.tasks, "theme": self.theme, "expr": expr,
                     "numeric": numeric, "tk": tk}
        self.bottom = BottomPanel(self.right, self, self.log_handler, namespace)
        if self.settings.get("ui.bottom_panel"):
            self.right.add(self.bottom, weight=0)
        self.toasts = ToastManager(root, enabled=lambda: bool(self.settings.get("ui.toasts")))
        self.palette = CommandPalette(self)
        root.after(120, self._apply_sashes)

    def _apply_sashes(self) -> None:
        try:
            self.root.update_idletasks()
            if self._sidebar_visible():
                self.body.sashpos(0, int(self.settings.get("ui.sidebar_width") or 230))
            if self._bottom_visible():
                total = self.right.winfo_height()
                h = int(self.settings.get("ui.bottom_height") or 230)
                self.right.sashpos(0, max(200, total - h))
        except tk.TclError:
            pass

    def _sidebar_visible(self) -> bool:
        return str(self.sidebar) in self.body.panes()

    def _bottom_visible(self) -> bool:
        return str(self.bottom) in self.right.panes()

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        t = self.theme
        menubar = tk.Menu(self.root, tearoff=False)

        def add(menu: tk.Menu, cid: str, label: Optional[str] = None) -> None:
            cmd = self.commands.get(cid)
            if cmd is None:
                return
            menu.add_command(label=label or cmd.title,
                             accelerator=(cmd.shortcut or "").replace("Shift", "Maj"),
                             command=lambda: self.commands.execute(cid))

        m_file = tk.Menu(menubar, tearoff=False)
        for cid in ("session.save", "session.export", "session.import"):
            add(m_file, cid)
        m_file.add_separator()
        add(m_file, "app.settings")
        m_file.add_separator()
        add(m_file, "app.quit")
        menubar.add_cascade(label="Fichier", menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=False)
        add(m_edit, "edit.undo")
        add(m_edit, "edit.redo")
        menubar.add_cascade(label="Édition", menu=m_edit)

        m_view = tk.Menu(menubar, tearoff=False)
        for cid in ("app.palette", "view.sidebar", "view.bottom", "view.console", "view.log",
                    "view.fullscreen"):
            add(m_view, cid)
        m_view.add_separator()
        for cid in ("view.zoom_in", "view.zoom_out", "view.zoom_reset"):
            add(m_view, cid)
        m_view.add_separator()
        m_theme = tk.Menu(m_view, tearoff=False)
        self._theme_var = tk.StringVar(value=self.theme.name)
        for name in THEMES:
            m_theme.add_radiobutton(label=name, value=name, variable=self._theme_var,
                                    command=lambda n=name: self.set_theme(n))
        m_view.add_cascade(label="Thème", menu=m_theme)
        add(m_view, "theme.next")
        menubar.add_cascade(label="Affichage", menu=m_view)

        m_mod = tk.Menu(menubar, tearoff=False)
        categories: Dict[str, tk.Menu] = {}
        for spec in self.registry.ordered():
            cls = spec.cls
            sub = categories.get(cls.category)
            if sub is None:
                sub = tk.Menu(m_mod, tearoff=False)
                categories[cls.category] = sub
                m_mod.add_cascade(label=cls.category, menu=sub)
            add(sub, f"module.open.{cls.id}", f"{cls.icon}  {cls.title}")
        m_mod.add_separator()
        for cid in ("tab.next", "tab.prev", "tab.close", "tab.close_others", "tab.reopen"):
            add(m_mod, cid)
        menubar.add_cascade(label="Modules", menu=m_mod)

        m_tools = tk.Menu(menubar, tearoff=False)
        for cid in ("tools.demo", "tools.selftest", "tools.gc", "tools.events"):
            add(m_tools, cid)
        menubar.add_cascade(label="Outils", menu=m_tools)

        m_help = tk.Menu(menubar, tearoff=False)
        for cid in ("app.shortcuts", "app.about"):
            add(m_help, cid)
        menubar.add_cascade(label="Aide", menu=m_help)
        self.root.configure(menu=menubar)
        self.menubar = menubar
        self.themes.recolor_tree(self.root)
        del t

    # ------------------------------------------------------------------
    # Commandes globales
    # ------------------------------------------------------------------
    def _register_commands(self) -> None:
        C = self.commands.add
        C("app.palette", "Palette de commandes", self.palette.toggle, category="Affichage",
          shortcut="Ctrl+Shift+P", icon="⌘", palette=False)
        C("app.palette.f1", "Palette de commandes", self.palette.toggle, category="Affichage",
          shortcut="F1", palette=False)
        C("app.settings", "Réglages…", lambda: SettingsDialog(self), category="Application",
          shortcut="Ctrl+,", icon="⚙")
        C("app.shortcuts", "Raccourcis clavier", lambda: ShortcutsDialog(self),
          category="Aide", icon="⌨")
        C("app.about", "À propos de PyClaude Studio", lambda: AboutDialog(self),
          category="Aide", icon="ℹ")
        C("app.quit", "Quitter", self.quit, category="Application", shortcut="Ctrl+Q", icon="⏻")
        C("session.save", "Enregistrer la session", self.save_session, category="Session",
          shortcut="Ctrl+Alt+S", icon="⬇")
        C("session.export", "Exporter la session…", self.export_session, category="Session",
          icon="⇪")
        C("session.import", "Importer une session…", self.import_session, category="Session",
          icon="⇩")
        C("edit.undo", "Annuler", lambda: self.active_module.undo(),  # type: ignore[union-attr]
          category="Édition", shortcut="Ctrl+Z", icon="↶",
          when=lambda: bool(self.active_module and self.active_module.can_undo()))
        C("edit.redo", "Rétablir", lambda: self.active_module.redo(),  # type: ignore[union-attr]
          category="Édition", shortcut="Ctrl+Y", icon="↷",
          when=lambda: bool(self.active_module and self.active_module.can_redo()))
        C("edit.redo2", "Rétablir", lambda: self.active_module.redo(),  # type: ignore[union-attr]
          category="Édition", shortcut="Ctrl+Shift+Z", palette=False,
          when=lambda: bool(self.active_module and self.active_module.can_redo()))
        C("view.sidebar", "Afficher/masquer la barre latérale", self.toggle_sidebar,
          category="Affichage", shortcut="Ctrl+Shift+B", icon="◧")
        C("view.bottom", "Afficher/masquer le panneau inférieur", self.toggle_bottom_panel,
          category="Affichage", shortcut="Ctrl+J", icon="▤")
        C("view.console", "Console Python", lambda: self.show_bottom("console"),
          category="Affichage", shortcut="Ctrl+Shift+C", icon="❯")
        C("view.log", "Journal", lambda: self.show_bottom("log"), category="Affichage",
          icon="☰")
        C("tools.events", "Moniteur d'événements", lambda: self.show_bottom("events"),
          category="Outils", icon="⚡")
        C("view.tasks", "Tâches d'arrière-plan", lambda: self.show_bottom("tasks"),
          category="Affichage", icon="⟳")
        C("view.fullscreen", "Plein écran", self.toggle_fullscreen, category="Affichage",
          shortcut="F11", icon="⛶")
        C("view.zoom_in", "Agrandir le texte", lambda: self.set_font_scale(
            self.themes.scale + 0.1), category="Affichage", shortcut="Ctrl+=", icon="+")
        C("view.zoom_out", "Réduire le texte", lambda: self.set_font_scale(
            self.themes.scale - 0.1), category="Affichage", shortcut="Ctrl+-", icon="−")
        C("view.zoom_reset", "Taille de texte par défaut", lambda: self.set_font_scale(1.0),
          category="Affichage", shortcut="Ctrl+0", icon="◎")
        C("theme.next", "Thème suivant", lambda: self.set_theme(self.themes.cycle().name),
          category="Thème", shortcut="Ctrl+Shift+L", icon="◐")
        for name in THEMES:
            C(f"theme.set.{name}", f"Appliquer le thème « {name} »",
              lambda n=name: self.set_theme(n), category="Thème", icon="◐")
        C("tab.next", "Onglet suivant", lambda: self._cycle_tab(1), category="Onglets",
          shortcut="Ctrl+Tab", icon="→")
        C("tab.prev", "Onglet précédent", lambda: self._cycle_tab(-1), category="Onglets",
          shortcut="Ctrl+Shift+Tab", icon="←")
        C("tab.close", "Fermer l'onglet", lambda: self.active_id and self.close_module(
            self.active_id), category="Onglets", shortcut="Ctrl+W", icon="✕")
        C("tab.close_others", "Fermer les autres onglets", self.close_others,
          category="Onglets", icon="✕")
        C("tab.reopen", "Rouvrir l'onglet fermé", self.reopen_closed, category="Onglets",
          shortcut="Ctrl+Shift+T", icon="↺")
        C("tools.demo", "Visite guidée automatique des modules", self.toggle_demo,
          category="Outils", icon="▶")
        C("tools.selftest", "Autotest : ouvrir et vérifier tous les modules",
          lambda: self.run_selftest(), category="Outils", icon="✓")
        C("tools.gc", "Forcer le ramasse-miettes", self._collect_garbage, category="Outils",
          icon="♻")
        for i, spec in enumerate(self.registry.ordered()):
            cls = spec.cls
            C(f"module.open.{cls.id}", f"Ouvrir « {cls.title} »",
              lambda m=cls.id: self.open_module(m), category="Modules", icon=cls.icon,
              shortcut=f"Ctrl+{i}" if 1 <= i <= 9 else None, description=cls.description)

    # ------------------------------------------------------------------
    # Gestion des modules
    # ------------------------------------------------------------------
    def open_module(self, module_id: str, activate: bool = True) -> Optional[StudioModule]:
        if module_id in self.instances:
            if activate:
                self.activate(module_id)
            return self.instances[module_id]
        spec = self.registry.get(module_id)
        if spec is None or spec.cls is None:
            self.notify(f"Module inconnu : {module_id}", "error")
            return None
        t0 = time.perf_counter()
        try:
            inst = spec.cls(self, self.host)
        except Exception:
            trace = traceback.format_exc()
            log.error("Impossible d'ouvrir « %s » :\n%s", module_id, trace)
            self.notify(f"Le module « {spec.cls.title} » n'a pas pu s'ouvrir", "error")
            ErrorDialog(self, f"Erreur à l'ouverture de « {spec.cls.title} »", trace)
            return None
        self.instances[module_id] = inst
        state = self.settings.module_state(module_id)
        if state:
            try:
                inst.set_state(state)
            except Exception:
                log.exception("État illisible pour %s (ignoré)", module_id)
        for cmd in inst.commands():
            cmd.owner = module_id
            if cmd.when is None:
                cmd.when = lambda m=module_id: self.active_id == m
            if not cmd.category:
                cmd.category = spec.cls.title
            self.commands.register(cmd)
        self.tabbar.add(module_id, spec.cls.icon, spec.cls.title)
        log.info("Module « %s » chargé en %.0f ms", spec.cls.title,
                 (time.perf_counter() - t0) * 1000)
        self.bus.publish("module.opened", id=module_id)
        if activate:
            self.activate(module_id)
        self.sidebar.redraw()
        return inst

    def activate(self, module_id: str) -> None:
        if module_id == self.active_id or module_id not in self.instances:
            return
        prev = self.active_module
        if prev is not None:
            try:
                prev._deactivate()
            except Exception:
                log.exception("Erreur en masquant %s", prev.id)
            prev.frame.pack_forget()
        self.empty.pack_forget()
        inst = self.instances[module_id]
        inst.frame.pack(fill="both", expand=True)
        self.active_id = module_id
        try:
            inst._activate()
        except Exception:
            log.exception("Erreur en activant %s", module_id)
        cls = type(inst)
        self.tabbar.select(module_id)
        self.sidebar.redraw()
        self.header.set_crumb(f"{cls.category}  ›  {cls.title}")
        self.root.title(f"{cls.title} — {APP_NAME}")
        self.bus.publish("module.activated", id=module_id)

    def close_module(self, module_id: str) -> None:
        inst = self.instances.get(module_id)
        if inst is None:
            return
        try:
            self.settings.set_module_state(module_id, inst.get_state())
        except Exception:
            log.exception("Sauvegarde d'état impossible pour %s", module_id)
        neighbor = self.tabbar.neighbor(module_id, -1)
        if self.active_id == module_id:
            try:
                inst._deactivate()
            except Exception:
                log.exception("Erreur en masquant %s", module_id)
            self.active_id = None
        self.commands.unregister_owner(module_id)
        del self.instances[module_id]
        try:
            inst._destroy()
        except Exception:
            log.exception("Erreur en détruisant %s", module_id)
        self.tabbar.remove(module_id)
        self.closed_history.append(module_id)
        self.bus.publish("module.closed", id=module_id)
        if self.active_id is None:
            if neighbor and neighbor in self.instances:
                self.activate(neighbor)
            elif self.instances:
                self.activate(next(iter(self.instances)))
            else:
                self.empty.pack(fill="both", expand=True)
                self.header.set_crumb("")
                self.root.title(APP_NAME)
        self.sidebar.redraw()

    def close_others(self) -> None:
        for mid in list(self.instances):
            if mid != self.active_id:
                self.close_module(mid)

    def reopen_closed(self) -> None:
        while self.closed_history:
            mid = self.closed_history.pop()
            if mid not in self.instances:
                self.open_module(mid)
                return
        self.notify("Aucun onglet fermé récemment")

    def _cycle_tab(self, step: int) -> None:
        if self.active_id:
            nxt = self.tabbar.neighbor(self.active_id, step)
            if nxt:
                self.activate(nxt)

    def _on_reorder(self, order: List[str]) -> None:
        self.instances = {mid: self.instances[mid] for mid in order if mid in self.instances}

    def _tab_menu(self, module_id: str, event: tk.Event) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Fermer", command=lambda: self.close_module(module_id))
        menu.add_command(label="Fermer les autres", command=lambda: (
            self.activate(module_id), self.close_others()))
        menu.add_command(label="Recharger le module", command=lambda: self.reload_module(module_id))
        menu.add_separator()
        menu.add_command(label="Réinitialiser l'état du module",
                         command=lambda: self.reset_module(module_id))
        self.themes.recolor_tree(menu)
        menu.tk_popup(event.x_root, event.y_root)

    def reload_module(self, module_id: str) -> None:
        was_active = self.active_id == module_id
        self.close_module(module_id)
        self.open_module(module_id, activate=was_active)
        self.notify("Module rechargé", "success")

    def reset_module(self, module_id: str) -> None:
        self.close_module(module_id)
        self.settings.set_module_state(module_id, {})
        self.open_module(module_id)
        self.notify("État du module réinitialisé", "success")

    # ------------------------------------------------------------------
    # Affichage
    # ------------------------------------------------------------------
    def notify(self, message: str, kind: str = "info") -> None:
        if not message:
            return
        try:
            self.toasts.show(message, kind)
        except tk.TclError:
            pass
        level = {"error": logging.ERROR, "warning": logging.WARNING}.get(kind, logging.INFO)
        log.log(level, message)

    def set_status(self, message: str) -> None:
        self.statusbar.set_message(message)

    def set_theme(self, name: str) -> None:
        self.settings.set("ui.theme", name)
        self.themes.apply(name)
        if hasattr(self, "_theme_var"):
            self._theme_var.set(name)

    def set_accent(self, color: Optional[str]) -> None:
        self.settings.set("ui.accent", color)
        self.themes.apply(self.theme.name, accent=color)

    def set_font_scale(self, scale: float) -> None:
        scale = max(0.7, min(2.0, round(scale, 2)))
        self.settings.set("ui.font_scale", scale)
        self.themes.set_scale(scale)
        self.tabbar.redraw()
        self.sidebar.redraw()
        self.set_status(f"Taille du texte : {scale:.0%}")

    def _on_theme(self, event: Any) -> None:
        theme = event["theme"]
        self.tabbar.redraw()
        self.sidebar.redraw()
        self.bottom.apply_theme()
        for inst in list(self.instances.values()):
            try:
                inst.on_theme(theme)
            except Exception:
                log.exception("Erreur de thème dans %s", inst.id)

    def toggle_sidebar(self) -> None:
        if self._sidebar_visible():
            self.settings.set("ui.sidebar_width", self.body.sashpos(0))
            self.body.forget(self.sidebar)
            self.settings.set("ui.sidebar", False)
        else:
            self.body.insert(0, self.sidebar, weight=0)
            self.settings.set("ui.sidebar", True)
            self.root.after(20, self._apply_sashes)

    def toggle_bottom_panel(self) -> None:
        if self._bottom_visible():
            try:
                self.settings.set("ui.bottom_height",
                                  self.right.winfo_height() - self.right.sashpos(0))
            except tk.TclError:
                pass
            self.right.forget(self.bottom)
            self.settings.set("ui.bottom_panel", False)
        else:
            self.right.add(self.bottom, weight=0)
            self.settings.set("ui.bottom_panel", True)
            self.root.after(20, self._apply_sashes)

    def show_bottom(self, tab: str) -> None:
        if not self._bottom_visible():
            self.toggle_bottom_panel()
        self.bottom.show(tab)

    def toggle_fullscreen(self) -> None:
        current = bool(self.root.attributes("-fullscreen"))
        self.root.attributes("-fullscreen", not current)

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    def _restore_window(self) -> None:
        geom = self.settings.get("window.geometry")
        try:
            if geom:
                self.root.geometry(geom)
            if self.settings.get("window.zoomed"):
                if sys.platform == "win32":
                    self.root.state("zoomed")
                else:
                    self.root.attributes("-zoomed", True)
        except tk.TclError:
            pass

    def _restore_session(self, restore: bool) -> None:
        ids = self.settings.get("session.open") if restore else ["home"]
        ids = [i for i in (ids or []) if i in self.registry.specs] or \
            ([s.id for s in self.registry.ordered()][:1])
        for mid in ids:
            self.open_module(mid, activate=False)
        active = self.settings.get("session.active") if restore else None
        target = active if active in self.instances else (ids[0] if ids else None)
        if target and target in self.instances:
            self.activate(target)
        elif not self.instances:
            self.empty.pack(fill="both", expand=True)

    def save_session(self, quiet: bool = False) -> None:
        for mid, inst in self.instances.items():
            try:
                self.settings.set_module_state(mid, inst.get_state())
            except Exception:
                log.exception("État non sauvegardé pour %s", mid)
        self.settings.set("session.open", list(self.instances))
        self.settings.set("session.active", self.active_id)
        try:
            zoomed = (self.root.state() == "zoomed") or bool(self.root.attributes("-zoomed"))
        except tk.TclError:
            zoomed = False
        self.settings.set("window.zoomed", zoomed)
        if not zoomed and not self.root.attributes("-fullscreen"):
            self.settings.set("window.geometry", self.root.geometry())
        if self._sidebar_visible():
            try:
                self.settings.set("ui.sidebar_width", self.body.sashpos(0))
            except tk.TclError:
                pass
        if self.settings.save() and not quiet:
            self.notify("Session enregistrée", "success")

    def export_session(self) -> None:
        self.save_session(quiet=True)
        path = filedialog.asksaveasfilename(parent=self.root, title="Exporter la session",
                                            defaultextension=".studio.json",
                                            filetypes=[("Session PyClaude Studio", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.settings.export_session(), fh, ensure_ascii=False, indent=2,
                      default=str)
        self.notify("Session exportée", "success")

    def import_session(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Importer une session",
                                          filetypes=[("Session PyClaude Studio", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            modules = data.get("modules", {})
        except (OSError, ValueError, AttributeError) as exc:
            self.notify(f"Session illisible : {exc}", "error")
            return
        for mid in list(self.instances):
            self.close_module(mid)
        for mid, state in modules.items():
            self.settings.set_module_state(mid, state)
        for mid in data.get("values", {}).get("session.open", []) or ["home"]:
            if mid in self.registry.specs:
                self.open_module(mid)
        self.notify("Session importée", "success")

    def _autosave(self) -> None:
        try:
            if self.settings.dirty:
                self.settings.save()
        finally:
            self.root.after(60_000, self._autosave)

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def toggle_demo(self) -> None:
        if self._demo_after:
            self.root.after_cancel(self._demo_after)
            self._demo_after = None
            self.notify("Visite guidée arrêtée")
            return
        order = [s.id for s in self.registry.ordered()]
        self.notify("Visite guidée : un module toutes les 6 secondes (relancez la commande "
                    "pour arrêter)")

        def step(i: int = 0) -> None:
            self.open_module(order[i % len(order)])
            mod = self.active_module
            if mod is not None and hasattr(mod, "demo"):
                try:
                    mod.demo()  # type: ignore[attr-defined]
                except Exception:
                    log.exception("Démo en erreur pour %s", mod.id)
            self._demo_after = self.root.after(6000, lambda: step(i + 1))

        step()

    def run_selftest(self, on_finish: Optional[Callable[[bool, List[str]], None]] = None,
                     dwell_ms: int = 400) -> None:
        """Ouvre chaque module, l'active, exécute sa démo, puis vérifie l'absence d'erreur."""
        order = [s.id for s in self.registry.ordered()]
        report: List[str] = []
        failures: List[str] = []
        errors_before = [0]

        class _Counter(logging.Handler):
            def __init__(self) -> None:
                super().__init__(logging.ERROR)
                self.count = 0

            def emit(self, record: logging.LogRecord) -> None:
                self.count += 1

        counter = _Counter()
        logging.getLogger().addHandler(counter)

        def step(i: int) -> None:
            if i >= len(order):
                logging.getLogger().removeHandler(counter)
                ok = not failures and not self.registry.failures
                summary = f"Autotest : {len(order) - len(failures)}/{len(order)} modules OK"
                report.append(summary)
                log.info("\n".join(report))
                self.notify(summary, "success" if ok else "error")
                if on_finish:
                    on_finish(ok, report)
                return
            mid = order[i]
            errors_before[0] = counter.count
            t0 = time.perf_counter()
            inst = self.open_module(mid)
            if inst is not None and hasattr(inst, "demo"):
                try:
                    inst.demo()  # type: ignore[attr-defined]
                except Exception:
                    log.exception("Démo en erreur pour %s", mid)

            def check() -> None:
                dt = (time.perf_counter() - t0) * 1000
                errs = counter.count - errors_before[0]
                if inst is None or errs:
                    failures.append(mid)
                    report.append(f"✖ {mid:<12} {errs} erreur(s)")
                else:
                    report.append(f"✔ {mid:<12} {dt:6.0f} ms")
                step(i + 1)

            self.root.after(dwell_ms, check)

        step(0)

    def _collect_garbage(self) -> None:
        n = gc.collect()
        self.notify(f"Ramasse-miettes : {n} objets libérés", "success")

    def _report_exception(self, exc_type, exc, tb) -> None:
        trace = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Exception non gérée :\n%s", trace)
        try:
            self.notify(f"Erreur : {exc_type.__name__}: {exc}", "error")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        try:
            self.save_session(quiet=True)
        except Exception:
            log.exception("Erreur pendant la sauvegarde de session")
        for inst in list(self.instances.values()):
            try:
                inst._destroy()
            except Exception:
                pass
        self.instances.clear()
        self.tasks.shutdown()
        try:
            from .engines import fractal

            fractal.shutdown_pool()
        except Exception:
            pass
        logging.getLogger().removeHandler(self.log_handler)
        try:
            self.root.destroy()
        except tk.TclError:
            pass
