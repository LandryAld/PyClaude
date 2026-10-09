"""Gestionnaire de tâches d'arrière-plan.

Les fonctions s'exécutent dans un pool de threads ; elles reçoivent un
:class:`TaskContext` pour signaler leur progression, publier des résultats
partiels et vérifier l'annulation. Tout retour vers l'interface passe par une
file pompée sur le thread principal : aucun appel Tk depuis un thread annexe.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class Cancelled(Exception):
    """Levée par :meth:`TaskContext.check` lorsqu'une tâche est annulée."""


class Task:
    _ids = itertools.count(1)

    def __init__(self, name: str, owner: Optional[str] = None) -> None:
        self.id = next(Task._ids)
        self.name = name
        self.owner = owner
        self.status = "en attente"
        self.progress = 0.0
        self.message = ""
        self.result: Any = None
        self.error: Optional[str] = None
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self._cancel = threading.Event()
        self.on_done: Optional[Callable[[Any], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_progress: Optional[Callable[[float, str], None]] = None
        self.on_partial: Optional[Callable[[Any], None]] = None

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def active(self) -> bool:
        return self.status in ("en attente", "en cours")

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.finished or time.time()) - self.started

    def cancel(self) -> None:
        self._cancel.set()


class TaskContext:
    """Interface offerte à la fonction exécutée en arrière-plan."""

    def __init__(self, task: Task, outbox: "queue.SimpleQueue") -> None:
        self._task = task
        self._outbox = outbox
        self._last_progress = 0.0

    @property
    def cancelled(self) -> bool:
        return self._task.cancelled

    def check(self) -> None:
        if self._task.cancelled:
            raise Cancelled()

    def progress(self, fraction: float, message: str = "") -> None:
        now = time.perf_counter()
        if fraction < 1.0 and now - self._last_progress < 0.05:
            return  # limitation du débit des messages
        self._last_progress = now
        self._outbox.put((self._task, "progress", (max(0.0, min(1.0, fraction)), message)))

    def partial(self, data: Any) -> None:
        self._outbox.put((self._task, "partial", data))


class TaskManager:
    def __init__(self, bus=None, max_workers: int = 4) -> None:
        self.bus = bus
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix="studio-task")
        self._outbox: "queue.SimpleQueue" = queue.SimpleQueue()
        self.tasks: List[Task] = []
        self._closed = False

    # -- soumission ----------------------------------------------------
    def submit(self, name: str, fn: Callable[..., Any], *args: Any,
               owner: Optional[str] = None,
               on_done: Optional[Callable[[Any], None]] = None,
               on_error: Optional[Callable[[str], None]] = None,
               on_progress: Optional[Callable[[float, str], None]] = None,
               on_partial: Optional[Callable[[Any], None]] = None,
               **kwargs: Any) -> Task:
        """Exécute ``fn(ctx, *args, **kwargs)`` en arrière-plan."""
        task = Task(name, owner)
        task.on_done, task.on_error = on_done, on_error
        task.on_progress, task.on_partial = on_progress, on_partial
        self.tasks.append(task)
        if len(self.tasks) > 200:
            self.tasks = [t for t in self.tasks if t.active] + \
                [t for t in self.tasks if not t.active][-100:]
        if self._closed:
            task.status = "annulée"
            return task
        ctx = TaskContext(task, self._outbox)

        def runner() -> None:
            if task.cancelled:
                self._outbox.put((task, "cancelled", None))
                return
            self._outbox.put((task, "started", None))
            try:
                result = fn(ctx, *args, **kwargs)
            except Cancelled:
                self._outbox.put((task, "cancelled", None))
            except Exception as exc:  # erreur rapportée à l'interface
                self._outbox.put((task, "error", (f"{type(exc).__name__}: {exc}",
                                                  traceback.format_exc())))
            else:
                if task.cancelled:
                    self._outbox.put((task, "cancelled", None))
                else:
                    self._outbox.put((task, "done", result))

        self._executor.submit(runner)
        self._emit("task.submitted", task)
        return task

    def cancel_owner(self, owner: str) -> None:
        for task in self.tasks:
            if task.owner == owner and task.active:
                task.cancel()

    @property
    def active(self) -> List[Task]:
        return [t for t in self.tasks if t.active]

    # -- pompe (thread principal) --------------------------------------
    def pump(self, budget: int = 500) -> int:
        count = 0
        while count < budget:
            try:
                task, kind, payload = self._outbox.get_nowait()
            except queue.Empty:
                break
            count += 1
            try:
                self._handle(task, kind, payload)
            except Exception:
                log.exception("Erreur dans un rappel de tâche « %s »", task.name)
        return count

    def _handle(self, task: Task, kind: str, payload: Any) -> None:
        if kind == "started":
            task.status = "en cours"
            task.started = time.time()
            self._emit("task.started", task)
        elif kind == "progress":
            task.progress, task.message = payload
            if task.on_progress and not task.cancelled:
                task.on_progress(*payload)
            self._emit("task.progress", task)
        elif kind == "partial":
            if task.on_partial and not task.cancelled:
                task.on_partial(payload)
        elif kind == "done":
            task.status = "terminée"
            task.progress = 1.0
            task.result = payload
            task.finished = time.time()
            if task.on_done:
                task.on_done(payload)
            self._emit("task.finished", task)
        elif kind == "error":
            summary, trace = payload
            task.status = "erreur"
            task.error = summary
            task.finished = time.time()
            log.error("Tâche « %s » en erreur : %s\n%s", task.name, summary, trace)
            if task.on_error:
                task.on_error(summary)
            self._emit("task.failed", task)
        elif kind == "cancelled":
            task.status = "annulée"
            task.finished = time.time()
            self._emit("task.cancelled", task)

    def attach(self, widget, interval_ms: int = 30) -> None:
        def tick() -> None:
            try:
                self.pump()
            finally:
                try:
                    widget.after(interval_ms, tick)
                except Exception:
                    pass

        tick()

    def _emit(self, topic: str, task: Task) -> None:
        if self.bus is not None:
            self.bus.publish(topic, task=task)

    def shutdown(self) -> None:
        self._closed = True
        for task in self.tasks:
            task.cancel()
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)  # type: ignore[call-arg]
        except TypeError:  # Python < 3.9
            self._executor.shutdown(wait=False)

    def snapshot(self) -> List[Dict[str, Any]]:
        return [{"id": t.id, "name": t.name, "status": t.status, "progress": t.progress,
                 "elapsed": t.elapsed, "message": t.message} for t in self.tasks]
