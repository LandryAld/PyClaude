"""Panneau inférieur : journal, console Python interactive, tâches et moniteur d'événements."""

from __future__ import annotations

import code
import contextlib
import io
import logging
import queue
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .widgets import theme_of

if TYPE_CHECKING:  # pragma: no cover
    from ..app import StudioApp


class QueueLogHandler(logging.Handler):
    """Gestionnaire de journalisation sûr entre threads (file vers l'interface)."""

    def __init__(self) -> None:
        super().__init__()
        self.queue: "queue.SimpleQueue[logging.LogRecord]" = queue.SimpleQueue()
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s %(name)s — %(message)s",
                                            "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.queue.put(record)


def _text_widget(parent: tk.Misc, **kw: Any) -> tk.Text:
    kw.setdefault("wrap", "word")
    t = tk.Text(parent, relief="flat", borderwidth=0, highlightthickness=0, font="StudioMono",
                padx=10, pady=6, **kw)
    t._studio_role = "code"  # type: ignore[attr-defined]
    return t


class LogView(ttk.Frame):
    LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

    def __init__(self, parent: tk.Misc, handler: QueueLogHandler) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.handler = handler
        self.records: List[logging.LogRecord] = []
        bar = ttk.Frame(self, style="Panel.TFrame")
        bar.pack(fill="x", padx=6, pady=4)
        ttk.Label(bar, text="Niveau", style="Muted.TLabel").pack(side="left", padx=(4, 4))
        self.level = tk.StringVar(value="INFO")
        cb = ttk.Combobox(bar, textvariable=self.level, values=self.LEVELS, width=10,
                          state="readonly")
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
        ttk.Label(bar, text="Filtre", style="Muted.TLabel").pack(side="left", padx=(12, 4))
        self.filter = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.filter, width=24)
        ent.pack(side="left")
        self.filter.trace_add("write", lambda *_: self.rebuild())
        ttk.Button(bar, text="Effacer", style="Ghost.TButton",
                   command=self.clear).pack(side="right")
        ttk.Button(bar, text="Copier", style="Ghost.TButton",
                   command=self.copy).pack(side="right")
        body = ttk.Frame(self, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        self.text = _text_widget(body, height=8, state="disabled")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.apply_theme()

    def apply_theme(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        self.text.tag_configure("DEBUG", foreground=t.muted)
        self.text.tag_configure("INFO", foreground=t.fg2)
        self.text.tag_configure("WARNING", foreground=t.warning)
        self.text.tag_configure("ERROR", foreground=t.error)
        self.text.tag_configure("CRITICAL", foreground=t.error)

    def _visible(self, rec: logging.LogRecord) -> bool:
        if rec.levelno < logging.getLevelName(self.level.get()):
            return False
        f = self.filter.get().lower()
        return not f or f in rec.getMessage().lower() or f in rec.name.lower()

    def poll(self) -> int:
        new = []
        while True:
            try:
                new.append(self.handler.queue.get_nowait())
            except queue.Empty:
                break
        if not new:
            return 0
        self.records.extend(new)
        if len(self.records) > 3000:
            self.records = self.records[-2000:]
        self._append([r for r in new if self._visible(r)])
        return len(new)

    def _append(self, records: List[logging.LogRecord]) -> None:
        if not records:
            return
        at_end = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        for rec in records:
            try:
                line = self.handler.format(rec)
            except Exception:
                line = rec.getMessage()
            self.text.insert("end", line + "\n", rec.levelname)
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > 2500:
            self.text.delete("1.0", f"{lines - 2000}.0")
        self.text.configure(state="disabled")
        if at_end:
            self.text.see("end")

    def rebuild(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._append([r for r in self.records if self._visible(r)])

    def clear(self) -> None:
        self.records.clear()
        self.rebuild()

    def copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end-1c"))


class PythonConsole(ttk.Frame):
    """Console Python intégrée ayant accès à l'objet ``app``.

    Historique (↑/↓), saisie multiligne via ``code.compile_command``,
    redirection de stdout/stderr pendant l'exécution.
    """

    PS1, PS2 = ">>> ", "... "

    def __init__(self, parent: tk.Misc, namespace: Dict[str, Any]) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.interp = code.InteractiveInterpreter(namespace)
        self.buffer: List[str] = []
        self.history: List[str] = []
        self.hpos = 0
        self.text = _text_widget(self, height=8, undo=False)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.bind("<Return>", self._enter)
        self.text.bind("<KP_Enter>", self._enter)
        self.text.bind("<Up>", lambda e: self._hist(-1))
        self.text.bind("<Down>", lambda e: self._hist(1))
        self.text.bind("<BackSpace>", self._backspace)
        self.text.bind("<Home>", self._home)
        self.text.bind("<Key>", self._key)
        self.text.bind("<Tab>", self._tab)
        self.apply_theme()
        self._write("Console Python de PyClaude Studio — objets disponibles : app, bus, "
                    "settings, tasks, theme, expr, numeric.\n"
                    "Exemple : app.open_module('fractal')   |   expr.compile_expr('sin(x)^2')(1)\n",
                    "info")
        self._prompt()

    def apply_theme(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        self.text.tag_configure("prompt", foreground=t.accent)
        self.text.tag_configure("out", foreground=t.fg2)
        self.text.tag_configure("err", foreground=t.error)
        self.text.tag_configure("info", foreground=t.muted)

    def _write(self, s: str, tag: str = "out") -> None:
        self.text.insert("end", s, tag)
        self.text.see("end")

    def _prompt(self) -> None:
        self._write(self.PS2 if self.buffer else self.PS1, "prompt")
        self.text.mark_set("input", "end-1c")
        self.text.mark_gravity("input", "left")
        self.text.mark_set("insert", "end-1c")

    def _current(self) -> str:
        return self.text.get("input", "end-1c")

    def _set_current(self, s: str) -> None:
        self.text.delete("input", "end-1c")
        self.text.insert("end", s)
        self.text.mark_set("insert", "end-1c")

    def _key(self, e: tk.Event) -> Optional[str]:
        if e.state & 0x4 and e.keysym.lower() in ("c", "a"):
            return None
        if self.text.compare("insert", "<", "input") and e.char:
            self.text.mark_set("insert", "end-1c")
        return None

    def _backspace(self, _e: tk.Event) -> Optional[str]:
        if self.text.tag_ranges("sel"):
            return None
        if self.text.compare("insert", "<=", "input"):
            return "break"
        return None

    def _home(self, _e: tk.Event) -> str:
        self.text.mark_set("insert", "input")
        return "break"

    def _tab(self, _e: tk.Event) -> str:
        self.text.insert("insert", "    ")
        return "break"

    def _hist(self, step: int) -> str:
        if not self.history:
            return "break"
        self.hpos = max(0, min(len(self.history), self.hpos + step))
        self._set_current(self.history[self.hpos] if self.hpos < len(self.history) else "")
        return "break"

    def _enter(self, _e: tk.Event) -> str:
        line = self._current()
        self._write("\n")
        if line.strip():
            self.history.append(line)
        self.hpos = len(self.history)
        self.buffer.append(line)
        source = "\n".join(self.buffer)
        try:
            compiled = code.compile_command(source, "<console>", "single")
        except (SyntaxError, OverflowError, ValueError) as exc:
            self.buffer = []
            self._write(f"{type(exc).__name__}: {exc}\n", "err")
            self._prompt()
            return "break"
        if compiled is None:
            self._prompt()  # instruction incomplète : ligne de continuation
            return "break"
        self.buffer = []
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.interp.runcode(compiled)
        if out.getvalue():
            self._write(out.getvalue(), "out")
        if err.getvalue():
            self._write(err.getvalue(), "err")
        self._prompt()
        return "break"

    def run_source(self, source: str) -> None:
        """Exécute du code comme s'il avait été tapé (utilisé par les tests)."""
        self._set_current(source)
        self._enter(None)  # type: ignore[arg-type]


class TaskView(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "StudioApp") -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.app = app
        cols = ("name", "status", "progress", "elapsed", "message")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=6)
        for col, label, width in (("name", "Tâche", 220), ("status", "État", 90),
                                  ("progress", "Progression", 100), ("elapsed", "Durée", 80),
                                  ("message", "Détail", 300)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        side = ttk.Frame(self, style="Panel.TFrame")
        side.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(side, text="Annuler", command=self._cancel).pack(fill="x", pady=2)
        ttk.Button(side, text="Tout annuler", style="Danger.TButton",
                   command=self._cancel_all).pack(fill="x", pady=2)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

    def refresh(self) -> None:
        tasks = self.app.tasks.tasks[-80:]
        existing = set(self.tree.get_children())
        for task in tasks:
            iid = str(task.id)
            values = (task.name, task.status, f"{task.progress * 100:.0f} %",
                      f"{task.elapsed:.1f} s", task.error or task.message)
            if iid in existing:
                self.tree.item(iid, values=values)
                existing.discard(iid)
            else:
                self.tree.insert("", 0, iid=iid, values=values)
        for iid in existing:
            self.tree.delete(iid)

    def _cancel(self) -> None:
        for iid in self.tree.selection():
            for task in self.app.tasks.tasks:
                if str(task.id) == iid:
                    task.cancel()

    def _cancel_all(self) -> None:
        for task in self.app.tasks.active:
            task.cancel()


class EventMonitor(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "StudioApp") -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.app = app
        self.paused = tk.BooleanVar(value=False)
        bar = ttk.Frame(self, style="Panel.TFrame")
        bar.pack(fill="x", padx=6, pady=4)
        ttk.Label(bar, text="Motif", style="Muted.TLabel").pack(side="left", padx=4)
        self.pattern = tk.StringVar(value="*")
        ttk.Entry(bar, textvariable=self.pattern, width=20).pack(side="left")
        ttk.Checkbutton(bar, text="Pause", variable=self.paused).pack(side="left", padx=10)
        self.stats = ttk.Label(bar, text="", style="Muted.TLabel")
        self.stats.pack(side="right", padx=6)
        body = ttk.Frame(self, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        self.text = _text_widget(body, height=8, state="disabled", wrap="none")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self._seen = 0
        self.apply_theme()

    def apply_theme(self) -> None:
        t = theme_of(self)
        if t is None:
            return
        self.text.tag_configure("time", foreground=t.muted)
        self.text.tag_configure("topic", foreground=t.accent)
        self.text.tag_configure("data", foreground=t.fg2)

    def refresh(self) -> None:
        bus = self.app.bus
        self.stats.configure(text=f"{bus.total} événements · {bus.subscriber_count} abonnés")
        if self.paused.get():
            self._seen = bus.total
            return
        new = bus.total - self._seen
        if new <= 0:
            return
        self._seen = bus.total
        import fnmatch

        pattern = self.pattern.get() or "*"
        events = list(bus.history)[-min(new, len(bus.history)):]
        self.text.configure(state="normal")
        for ev in events:
            if not fnmatch.fnmatchcase(ev.topic, pattern):
                continue
            stamp = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
            data = ", ".join(f"{k}={_short(v)}" for k, v in ev.data.items())
            self.text.insert("end", stamp + "  ", "time")
            self.text.insert("end", f"{ev.topic:<22}", "topic")
            self.text.insert("end", "  " + data + "\n", "data")
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > 1500:
            self.text.delete("1.0", f"{lines - 1000}.0")
        self.text.configure(state="disabled")
        self.text.see("end")


def _short(v: Any) -> str:
    if hasattr(v, "name") and hasattr(v, "status"):
        return f"<Tâche {v.name} {v.status}>"
    if hasattr(v, "name") and hasattr(v, "accent"):
        return f"<Thème {v.name}>"
    s = repr(v)
    return s if len(s) < 60 else s[:57] + "…"


class BottomPanel(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "StudioApp", log_handler: QueueLogHandler,
                 namespace: Dict[str, Any]) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.app = app
        head = ttk.Frame(self, style="Panel.TFrame")
        head.pack(fill="x")
        self.notebook = ttk.Notebook(self)
        self.log = LogView(self.notebook, log_handler)
        self.console = PythonConsole(self.notebook, namespace)
        self.tasks = TaskView(self.notebook, app)
        self.events = EventMonitor(self.notebook, app)
        self.notebook.add(self.log, text="  Journal  ")
        self.notebook.add(self.console, text="  Console Python  ")
        self.notebook.add(self.tasks, text="  Tâches  ")
        self.notebook.add(self.events, text="  Événements  ")
        self.notebook.pack(fill="both", expand=True)
        close = ttk.Button(self, text="✕", style="Icon.TButton", command=app.toggle_bottom_panel)
        close.place(relx=1.0, x=-6, y=4, anchor="ne")
        self._tick()

    def show(self, name: str) -> None:
        tab = {"log": self.log, "console": self.console, "tasks": self.tasks,
               "events": self.events}.get(name)
        if tab is not None:
            self.notebook.select(tab)
            if tab is self.console:
                self.console.text.focus_set()

    def apply_theme(self) -> None:
        for w in (self.log, self.console, self.events):
            w.apply_theme()

    def _tick(self) -> None:
        try:
            self.log.poll()
            if self.winfo_ismapped():
                current = self.notebook.select()
                if current == str(self.tasks):
                    self.tasks.refresh()
                elif current == str(self.events):
                    self.events.refresh()
        finally:
            self.after(250, self._tick)
