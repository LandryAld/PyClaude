"""Registre de commandes : une seule source pour menus, raccourcis et palette.

Chaque raccourci est lié une seule fois (``bind_all``) ; à l'appui, le
répartiteur choisit la première commande dont la condition ``when`` est
vraie. Ainsi ``Ctrl+S`` peut signifier « enregistrer le fichier » dans
l'éditeur et « enregistrer la feuille » dans le tableur.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_MODS = {"ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift",
         "cmd": "Command", "meta": "Meta"}
_KEYS = {
    "tab": "Tab", "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "space": "space", "del": "Delete", "delete": "Delete", "backspace": "BackSpace",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right", "home": "Home",
    "end": "End", "pgup": "Prior", "pgdn": "Next", "=": "equal", "+": "plus",
    "-": "minus", "`": "grave", ",": "comma", ".": "period", "/": "slash",
    ";": "semicolon", "[": "bracketleft", "]": "bracketright", "\\": "backslash",
    "'": "apostrophe",
}
_ALIASES = {"equal": ["plus", "KP_Add"], "minus": ["KP_Subtract"], "Return": ["KP_Enter"]}


def shortcut_sequences(shortcut: str, windowing: str = "x11") -> List[str]:
    """Convertit « Ctrl+Shift+P » en séquences d'événements Tk."""
    parts = [p.strip() for p in shortcut.split("+")]
    if shortcut.endswith("++"):
        parts = parts[:-2] + ["+"]
    *mods_raw, key = parts
    mods = []
    for m in mods_raw:
        mapped = _MODS.get(m.lower())
        if mapped is None:
            raise ValueError(f"modificateur inconnu : {m}")
        mods.append(mapped)
    if len(key) == 1 and key.isalpha():
        keysyms = [key.upper() if "Shift" in mods else key.lower()]
    elif len(key) == 1 and key.isdigit():
        keysyms = ["Key-" + key]
    elif key.upper().startswith("F") and key[1:].isdigit():
        keysyms = [key.upper()]
    else:
        base = _KEYS.get(key.lower(), key)
        keysyms = [base] + _ALIASES.get(base, [])
    seqs = ["<" + "-".join(mods + [k]) + ">" for k in keysyms]
    if keysyms[0] == "Tab" and "Shift" in mods and windowing == "x11":
        others = [m for m in mods if m != "Shift"]
        seqs.append("<" + "-".join(others + ["ISO_Left_Tab"]) + ">")
    return seqs


@dataclass
class Command:
    id: str
    title: str
    callback: Callable[[], Any]
    category: str = ""
    shortcut: Optional[str] = None
    when: Optional[Callable[[], bool]] = None
    icon: str = ""
    description: str = ""
    palette: bool = True
    owner: Optional[str] = None
    last_used: float = field(default=0.0, compare=False)

    def available(self) -> bool:
        if self.when is None:
            return True
        try:
            return bool(self.when())
        except Exception:
            return False

    @property
    def label(self) -> str:
        return f"{self.category} : {self.title}" if self.category else self.title


class CommandRegistry:
    def __init__(self, root=None, bus=None) -> None:
        self.root = root
        self.bus = bus
        self._commands: Dict[str, Command] = {}
        self._bound: Dict[str, List[str]] = {}  # séquence Tk -> ids
        self.history: List[str] = []

    def register(self, command: Command) -> Command:
        if command.id in self._commands:
            self.unregister(command.id)
        self._commands[command.id] = command
        if command.shortcut and self.root is not None:
            self._bind(command)
        return command

    def add(self, id: str, title: str, callback: Callable[[], Any], **kw: Any) -> Command:
        return self.register(Command(id, title, callback, **kw))

    def unregister(self, command_id: str) -> None:
        cmd = self._commands.pop(command_id, None)
        if cmd is None:
            return
        for seq, ids in list(self._bound.items()):
            if command_id in ids:
                ids.remove(command_id)

    def unregister_owner(self, owner: str) -> None:
        for cid in [c.id for c in self._commands.values() if c.owner == owner]:
            self.unregister(cid)

    def get(self, command_id: str) -> Optional[Command]:
        return self._commands.get(command_id)

    def all(self) -> List[Command]:
        return list(self._commands.values())

    def available(self) -> List[Command]:
        return [c for c in self._commands.values() if c.available()]

    def execute(self, command_id: str) -> bool:
        cmd = self._commands.get(command_id)
        if cmd is None or not cmd.available():
            return False
        cmd.last_used = time.time()
        self.history.append(command_id)
        del self.history[:-50]
        try:
            cmd.callback()
        except Exception:
            log.exception("La commande « %s » a échoué", cmd.title)
            if self.bus is not None:
                self.bus.publish("notify", message=f"La commande « {cmd.title} » a échoué",
                                 kind="error")
            return False
        if self.bus is not None:
            self.bus.publish("command.executed", id=command_id, title=cmd.title)
        return True

    # -- raccourcis ----------------------------------------------------
    def _windowing(self) -> str:
        try:
            return str(self.root.tk.call("tk", "windowingsystem"))
        except Exception:
            return "x11"

    def _bind(self, command: Command) -> None:
        try:
            seqs = shortcut_sequences(command.shortcut or "", self._windowing())
        except ValueError as exc:
            log.warning("Raccourci invalide pour %s : %s", command.id, exc)
            return
        for seq in seqs:
            if seq not in self._bound:
                try:
                    self.root.bind_all(seq, lambda e, s=seq: self._dispatch(s, e), add="+")
                except Exception:
                    continue  # touche inconnue sur cette plateforme
                self._bound[seq] = []
            self._bound[seq].append(command.id)

    def _dispatch(self, seq: str, event=None) -> Optional[str]:
        for cid in reversed(self._bound.get(seq, [])):
            cmd = self._commands.get(cid)
            if cmd is not None and cmd.available():
                self.execute(cid)
                return "break"
        return None

    def shortcut_for(self, command_id: str) -> str:
        cmd = self._commands.get(command_id)
        return cmd.shortcut or "" if cmd else ""
