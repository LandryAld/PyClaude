"""Réglages persistants (JSON) avec valeurs par défaut et observateurs.

Les clés sont « pointées » (``ui.theme``) ; l'état propre à chaque module est
rangé à part dans ``modules``. L'écriture est atomique (fichier temporaire puis
``os.replace``) pour ne jamais corrompre le fichier en cas d'arrêt brutal.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "ui.theme": "Nuit",
    "ui.accent": None,
    "ui.font_scale": 1.0,
    "ui.sidebar": True,
    "ui.bottom_panel": False,
    "ui.bottom_height": 230,
    "ui.sidebar_width": 230,
    "ui.splash": True,
    "ui.animations": True,
    "ui.toasts": True,
    "window.geometry": "1440x900",
    "window.zoomed": False,
    "session.restore": True,
    "session.open": ["home"],
    "session.active": "home",
    "palette.recent": [],
    "fractal.engine": "auto",
    "perf.max_fps": 60,
    "editor.recent": [],
}

_MISSING = object()


def config_dir() -> Path:
    """Dossier de configuration de l'utilisateur, propre à chaque OS."""
    env = os.environ.get("PYCLAUDE_STUDIO_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / "PyClaudeStudio"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PyClaudeStudio"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "pyclaude-studio"


Observer = Callable[[str, Any], None]


class Settings:
    def __init__(self, path: Optional[Path] = None,
                 defaults: Optional[Dict[str, Any]] = None) -> None:
        self.path = Path(path) if path else config_dir() / "settings.json"
        self.defaults = dict(DEFAULTS if defaults is None else defaults)
        self._data: Dict[str, Any] = {}
        self._modules: Dict[str, Dict[str, Any]] = {}
        self._observers: List[Tuple[str, Observer]] = []
        self.dirty = False

    # -- chargement / sauvegarde ---------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        settings = cls(path)
        try:
            raw = json.loads(settings.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                settings._data = dict(raw.get("values", {}))
                settings._modules = dict(raw.get("modules", {}))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Réglages illisibles (%s) : valeurs par défaut utilisées", exc)
        return settings

    def save(self) -> bool:
        payload = {"version": 1, "values": self._data, "modules": self._modules}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json",
                                       dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, self.path)
            self.dirty = False
            return True
        except (OSError, TypeError, ValueError) as exc:
            log.error("Impossible d'enregistrer les réglages : %s", exc)
            return False

    def reset(self) -> None:
        keys = list(self._data)
        self._data.clear()
        self._modules.clear()
        self.dirty = True
        for key in keys:
            self._notify(key, self.get(key))

    # -- accès ---------------------------------------------------------
    def get(self, key: str, default: Any = _MISSING) -> Any:
        if key in self._data:
            return copy.deepcopy(self._data[key])
        if key in self.defaults:
            return copy.deepcopy(self.defaults[key])
        if default is _MISSING:
            return None
        return default

    def set(self, key: str, value: Any) -> None:
        if key in self._data and self._data[key] == value:
            return
        self._data[key] = copy.deepcopy(value)
        self.dirty = True
        self._notify(key, value)

    def __contains__(self, key: str) -> bool:
        return key in self._data or key in self.defaults

    def items(self):
        merged = dict(self.defaults)
        merged.update(self._data)
        return sorted(merged.items())

    def observe(self, prefix: str, callback: Observer) -> Callable[[], None]:
        entry = (prefix, callback)
        self._observers.append(entry)

        def unobserve() -> None:
            try:
                self._observers.remove(entry)
            except ValueError:
                pass

        return unobserve

    def _notify(self, key: str, value: Any) -> None:
        for prefix, callback in list(self._observers):
            if key == prefix or key.startswith(prefix.rstrip("*")):
                try:
                    callback(key, value)
                except Exception:
                    log.exception("Observateur de réglage en erreur (%s)", key)

    # -- états de modules ----------------------------------------------
    def module_state(self, module_id: str) -> Dict[str, Any]:
        return copy.deepcopy(self._modules.get(module_id, {}))

    def set_module_state(self, module_id: str, state: Dict[str, Any]) -> None:
        if state:
            self._modules[module_id] = copy.deepcopy(state)
        else:
            self._modules.pop(module_id, None)
        self.dirty = True

    def export_session(self) -> Dict[str, Any]:
        return {"values": copy.deepcopy(self._data), "modules": copy.deepcopy(self._modules)}
