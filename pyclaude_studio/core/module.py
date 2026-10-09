"""Architecture de plugins : classe de base des modules, boucles d'animation, registre.

Un module est une classe dérivée de :class:`StudioModule` déclarant ``id``,
``title``, ``icon``… Le registre découvre automatiquement les modules du
paquet ``pyclaude_studio.modules`` ainsi que les plugins utilisateur déposés
dans ``<dossier de configuration>/plugins/*.py``. Un module qui échoue à
l'import est isolé et signalé sans empêcher l'application de démarrer.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Type

if TYPE_CHECKING:  # pragma: no cover
    from ..app import StudioApp
    from .commands import Command
    from .theme import Theme

log = logging.getLogger(__name__)


class Loop:
    """Boucle d'animation basée sur ``after`` avec mesure du FPS réel."""

    def __init__(self, widget: tk.Misc, callback: Callable[[float], Any], fps: float = 60,
                 name: str = "") -> None:
        self.widget = widget
        self.callback = callback
        self.fps = fps
        self.name = name
        self.running = False
        self.measured_fps = 0.0
        self.frame_ms = 0.0
        self._after: Optional[str] = None
        self._last = 0.0

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._last = time.perf_counter()
        self._schedule(1)

    def stop(self) -> None:
        self.running = False
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def toggle(self) -> bool:
        if self.running:
            self.stop()
        else:
            self.start()
        return self.running

    def _schedule(self, delay: int) -> None:
        try:
            self._after = self.widget.after(max(1, delay), self._tick)
        except tk.TclError:
            self.running = False

    def _tick(self) -> None:
        self._after = None
        if not self.running:
            return
        now = time.perf_counter()
        dt = min(0.1, now - self._last)
        self._last = now
        if dt > 0:
            inst = 1.0 / dt
            self.measured_fps = inst if not self.measured_fps else \
                self.measured_fps * 0.9 + inst * 0.1
        try:
            self.callback(dt)
        except Exception:
            log.exception("Boucle « %s » arrêtée après une erreur", self.name)
            self.running = False
            return
        spent = time.perf_counter() - now
        self.frame_ms = self.frame_ms * 0.9 + spent * 100.0 if self.frame_ms else spent * 1000
        if self.running:
            self._schedule(int(1000.0 / self.fps - spent * 1000))


class StudioModule:
    """Classe de base de tous les modules (onglets) du studio."""

    id: str = ""
    title: str = "Module"
    icon: str = "◆"
    category: str = "Divers"
    description: str = ""
    order: int = 100
    keywords: Tuple[str, ...] = ()

    def __init__(self, app: "StudioApp", parent: tk.Misc) -> None:
        self.app = app
        self.frame = ttk.Frame(parent, style="Module.TFrame")
        self.active = False
        self._loops: List[Loop] = []
        self._resume: List[Loop] = []
        self._subs: List[Any] = []
        self._after_ids: List[str] = []
        self.build(self.frame)

    # -- services pratiques --------------------------------------------
    @property
    def theme(self) -> "Theme":
        return self.app.theme

    def loop(self, callback: Callable[[float], Any], fps: float = 60, name: str = "") -> Loop:
        lp = Loop(self.frame, callback, fps, name or f"{self.id}.loop")
        self._loops.append(lp)
        return lp

    def subscribe(self, pattern: str, callback: Callable) -> Any:
        sub = self.app.bus.subscribe(pattern, callback)
        self._subs.append(sub)
        return sub

    def after(self, ms: int, fn: Callable[[], Any]) -> str:
        aid = self.frame.after(ms, fn)
        self._after_ids.append(aid)
        if len(self._after_ids) > 64:
            self._after_ids = self._after_ids[-32:]
        return aid

    def notify(self, message: str, kind: str = "info") -> None:
        self.app.notify(message, kind)

    def status(self, message: str) -> None:
        self.app.set_status(message)

    def run_task(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        kwargs.setdefault("owner", self.id)
        return self.app.tasks.submit(name, fn, *args, **kwargs)

    @property
    def fps(self) -> float:
        running = [lp for lp in self._loops if lp.running]
        return max((lp.measured_fps for lp in running), default=0.0)

    # -- cycle de vie (surchargé par les modules) -----------------------
    def build(self, frame: ttk.Frame) -> None:
        raise NotImplementedError

    def on_activate(self) -> None:
        """Le module devient visible."""

    def on_deactivate(self) -> None:
        """Le module est masqué : les boucles sont mises en pause automatiquement."""

    def on_theme(self, theme: "Theme") -> None:
        """Le thème a changé : redessiner les canevas personnalisés."""

    def commands(self) -> List["Command"]:
        return []

    def get_state(self) -> Dict[str, Any]:
        return {}

    def set_state(self, state: Dict[str, Any]) -> None:
        """Restaure l'état sauvegardé par :meth:`get_state`."""

    def can_undo(self) -> bool:
        return False

    def can_redo(self) -> bool:
        return False

    def undo(self) -> None:
        pass

    def redo(self) -> None:
        pass

    def info(self) -> Dict[str, Any]:
        """Statistiques affichées dans le tableau de bord."""
        return {}

    # -- appelés par l'application --------------------------------------
    def _activate(self) -> None:
        self.active = True
        for lp in self._resume:
            lp.start()
        self._resume = []
        self.on_activate()

    def _deactivate(self) -> None:
        self.active = False
        self._resume = [lp for lp in self._loops if lp.running]
        for lp in self._resume:
            lp.stop()
        self.on_deactivate()

    def _destroy(self) -> None:
        for lp in self._loops:
            lp.stop()
        for sub in self._subs:
            sub.cancel()
        for aid in self._after_ids:
            try:
                self.frame.after_cancel(aid)
            except tk.TclError:
                pass
        self.app.tasks.cancel_owner(self.id)
        self.frame.destroy()


class ModuleSpec:
    """Description d'un module découvert (chargé ou en erreur)."""

    def __init__(self, cls: Optional[Type[StudioModule]], origin: str,
                 error: Optional[str] = None) -> None:
        self.cls = cls
        self.origin = origin
        self.error = error

    @property
    def id(self) -> str:
        return self.cls.id if self.cls else self.origin

    def __repr__(self) -> str:  # pragma: no cover
        return f"ModuleSpec({self.id!r}, error={bool(self.error)})"


class ModuleRegistry:
    def __init__(self) -> None:
        self.specs: Dict[str, ModuleSpec] = {}
        self.failures: List[ModuleSpec] = []

    def discover(self, package: str = "pyclaude_studio.modules",
                 plugin_dir: Optional[Path] = None) -> "ModuleRegistry":
        try:
            pkg = importlib.import_module(package)
        except Exception:
            log.exception("Paquet de modules introuvable : %s", package)
            return self
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name.startswith("_"):
                continue
            self._load(f"{package}.{info.name}")
        if plugin_dir is not None and plugin_dir.is_dir():
            for path in sorted(plugin_dir.glob("*.py")):
                self._load_file(path)
        return self

    def _load(self, dotted: str) -> None:
        try:
            mod = importlib.import_module(dotted)
        except Exception:
            self._fail(dotted, traceback.format_exc())
            return
        self._collect(mod, dotted)

    def _load_file(self, path: Path) -> None:
        name = f"studio_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"chargement impossible : {path}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception:
            self._fail(str(path), traceback.format_exc())
            return
        self._collect(mod, str(path))

    def _collect(self, mod: Any, origin: str) -> None:
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if (issubclass(obj, StudioModule) and obj is not StudioModule and obj.id
                    and obj.__module__ == mod.__name__):
                if obj.id in self.specs:
                    log.warning("Module « %s » en double (%s) ignoré", obj.id, origin)
                    continue
                self.specs[obj.id] = ModuleSpec(obj, origin)

    def _fail(self, origin: str, trace: str) -> None:
        log.error("Échec du chargement de %s :\n%s", origin, trace)
        self.failures.append(ModuleSpec(None, origin, trace))

    def ordered(self) -> List[ModuleSpec]:
        return sorted(self.specs.values(), key=lambda s: (s.cls.order, s.cls.title))  # type: ignore[union-attr]

    def get(self, module_id: str) -> Optional[ModuleSpec]:
        return self.specs.get(module_id)

    def register(self, cls: Type[StudioModule], origin: str = "<api>") -> None:
        self.specs[cls.id] = ModuleSpec(cls, origin)
