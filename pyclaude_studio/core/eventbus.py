"""Bus d'événements publication/abonnement.

* Les sujets sont des chaînes hiérarchiques (``module.opened``, ``task.progress``…)
  et les abonnements acceptent des motifs glob (``task.*``, ``*``).
* :meth:`EventBus.publish` est synchrone sur le thread principal ; depuis un
  autre thread, l'événement est mis en file et délivré par :meth:`EventBus.pump`
  (appelé périodiquement par la boucle Tk via :meth:`EventBus.attach`).
* Une exception levée par un abonné est journalisée sans interrompre la
  distribution aux autres abonnés.
"""

from __future__ import annotations

import fnmatch
import logging
import queue
import threading
import time
from collections import Counter, deque
from typing import Any, Callable, Deque, Dict, List

log = logging.getLogger(__name__)

_MAX_DEPTH = 32


class Event:
    """Événement immuable transporté par le bus."""

    __slots__ = ("topic", "data", "timestamp")

    def __init__(self, topic: str, data: Dict[str, Any], timestamp: float) -> None:
        self.topic = topic
        self.data = data
        self.timestamp = timestamp

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"Event({self.topic!r}, {self.data!r})"


Callback = Callable[[Event], Any]


class Subscription:
    """Poignée d'abonnement ; :meth:`cancel` désabonne."""

    __slots__ = ("_bus", "pattern", "callback", "priority", "once", "active", "_glob")

    def __init__(self, bus: "EventBus", pattern: str, callback: Callback,
                 priority: int, once: bool) -> None:
        self._bus = bus
        self.pattern = pattern
        self.callback = callback
        self.priority = priority
        self.once = once
        self.active = True
        self._glob = any(ch in pattern for ch in "*?[")

    def matches(self, topic: str) -> bool:
        if self._glob:
            return fnmatch.fnmatchcase(topic, self.pattern)
        return topic == self.pattern

    def cancel(self) -> None:
        if self.active:
            self._bus.unsubscribe(self)


class EventBus:
    def __init__(self, history: int = 400) -> None:
        self._subs: List[Subscription] = []
        self._lock = threading.RLock()
        self._pending: "queue.SimpleQueue[Event]" = queue.SimpleQueue()
        self._main_thread = threading.get_ident()
        self._depth = 0
        self._after_id = None
        self.history: Deque[Event] = deque(maxlen=history)
        self.counts: Counter = Counter()
        self.total = 0

    # -- abonnements -------------------------------------------------------
    def subscribe(self, pattern: str, callback: Callback, *, priority: int = 0,
                  once: bool = False) -> Subscription:
        sub = Subscription(self, pattern, callback, priority, once)
        with self._lock:
            self._subs.append(sub)
            self._subs.sort(key=lambda s: -s.priority)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            sub.active = False
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    # -- publication -------------------------------------------------------
    def publish(self, topic: str, **data: Any) -> Event:
        """Publie immédiatement (ou met en file si appelé hors thread principal)."""
        if threading.get_ident() != self._main_thread:
            return self.post(topic, **data)
        event = Event(topic, data, time.time())
        self._dispatch(event)
        return event

    def post(self, topic: str, **data: Any) -> Event:
        """Publication différée, sûre depuis n'importe quel thread."""
        event = Event(topic, data, time.time())
        self._pending.put(event)
        return event

    def pump(self, budget: int = 1000) -> int:
        """Délivre les événements en attente ; renvoie le nombre traité."""
        delivered = 0
        while delivered < budget:
            try:
                event = self._pending.get_nowait()
            except queue.Empty:
                break
            self._dispatch(event)
            delivered += 1
        return delivered

    def attach(self, widget, interval_ms: int = 25) -> None:
        """Fait pomper la file par la boucle d'événements Tk."""

        def tick() -> None:
            try:
                self.pump()
            finally:
                try:
                    self._after_id = widget.after(interval_ms, tick)
                except Exception:  # fenêtre détruite
                    self._after_id = None

        tick()

    def _dispatch(self, event: Event) -> None:
        self.history.append(event)
        self.counts[event.topic] += 1
        self.total += 1
        if self._depth >= _MAX_DEPTH:
            log.error("Récursion d'événements trop profonde sur « %s »", event.topic)
            return
        with self._lock:
            targets = [s for s in self._subs if s.active and s.matches(event.topic)]
        self._depth += 1
        try:
            for sub in targets:
                if not sub.active:
                    continue
                if sub.once:
                    self.unsubscribe(sub)
                try:
                    sub.callback(event)
                except Exception:
                    log.exception("Erreur dans un abonné à « %s »", event.topic)
        finally:
            self._depth -= 1
