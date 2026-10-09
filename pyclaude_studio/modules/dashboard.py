"""Tableau de bord : métriques temps réel du processus et de l'application."""

from __future__ import annotations

import threading
import time
from tkinter import ttk
from typing import Optional

from ..core.module import StudioModule
from ..core.sysinfo import CpuMeter, human_bytes, load_average, memory_rss, static_info
from ..ui.widgets import Gauge, LineChart, StatTile


class DashboardModule(StudioModule):
    id = "dashboard"
    title = "Tableau de bord"
    icon = "▦"
    category = "Studio"
    description = "Processeur, mémoire, latence de la boucle Tk, trafic du bus d'événements, " \
                  "tâches et modules ouverts — en temps réel."
    order = 5
    keywords = ("monitoring", "performance", "cpu", "mémoire")

    def build(self, frame: ttk.Frame) -> None:
        self.cpu = CpuMeter()
        self.last_events = self.app.bus.total
        self.last_tasks = 0
        self.last_t = time.perf_counter()
        self.peak_mem = 1.0
        self.lag_ms = 0.0
        self._probe_sent: Optional[float] = None

        top = ttk.Frame(frame, style="Module.TFrame", padding=(16, 14, 16, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Tableau de bord", style="Title.TLabel").pack(side="left")
        self.uptime = ttk.Label(top, text="", style="Muted.TLabel")
        self.uptime.pack(side="right")

        tiles = ttk.Frame(frame, style="Module.TFrame", padding=(16, 10))
        tiles.pack(fill="x")
        self.t_cpu = StatTile(tiles, "Processeur", 0)
        self.t_mem = StatTile(tiles, "Mémoire résidente", 1)
        self.t_lag = StatTile(tiles, "Latence de la boucle Tk", 2)
        self.t_evt = StatTile(tiles, "Événements du bus", 3)
        self.t_task = StatTile(tiles, "Tâches", 4)
        self.t_thr = StatTile(tiles, "Threads actifs", 6)
        for i, tile in enumerate((self.t_cpu, self.t_mem, self.t_lag, self.t_evt, self.t_task,
                                  self.t_thr)):
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            tiles.columnconfigure(i, weight=1, uniform="tile")

        mid = ttk.Frame(frame, style="Module.TFrame", padding=(16, 4))
        mid.pack(fill="both", expand=True)
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)
        mid.rowconfigure(1, weight=1)
        self.c_cpu = LineChart(mid, title="Processeur (%)", unit="", y_min=0)
        self.c_cpu.add_series("CPU", 0)
        self.c_mem = LineChart(mid, title="Mémoire résidente (Mo)", unit="")
        self.c_mem.add_series("Mémoire", 1)
        self.c_lag = LineChart(mid, title="Latence de la boucle d'événements (ms)", y_min=0)
        self.c_lag.add_series("Latence", 2)
        self.c_evt = LineChart(mid, title="Débit (événements par seconde)", y_min=0)
        self.c_evt.add_series("Bus", 0)
        self.c_evt.add_series("Tâches", 1)
        for i, ch in enumerate((self.c_cpu, self.c_mem, self.c_lag, self.c_evt)):
            ch.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 == 0 else 6, 0),
                    pady=(0, 6))

        bottom = ttk.Frame(frame, style="Module.TFrame", padding=(16, 4, 16, 14))
        bottom.pack(fill="x")
        gauges = ttk.Frame(bottom, style="Card.TFrame", padding=8)
        gauges.pack(side="left", fill="y")
        self.g_cpu = Gauge(gauges, label="CPU", size=120)
        self.g_cpu.pack(side="left")
        self.g_mem = Gauge(gauges, label="Latence", maximum=50.0, unit=" ms", size=120)
        self.g_mem.pack(side="left")
        cols = ("module", "categorie", "fps", "etat")
        self.tree = ttk.Treeview(bottom, columns=cols, show="headings", height=5)
        for col, label, width in (("module", "Module ouvert", 200), ("categorie", "Catégorie", 140),
                                  ("fps", "Images/s", 90), ("etat", "Détails", 360)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        info = ttk.Frame(bottom, style="Card.TFrame", padding=10)
        info.pack(side="right", fill="y", padx=(10, 0))
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0))
        for k, v in list(static_info().items())[:4]:
            ttk.Label(info, text=k, style="Card.Muted.TLabel").pack(anchor="w")
            ttk.Label(info, text=v, style="Card.TLabel").pack(anchor="w", pady=(0, 4))

        self.sampler = self.loop(self._sample, fps=4, name="dashboard.sample")
        self.animator = self.loop(self._animate, fps=30, name="dashboard.gauges")
        self.sampler.start()
        self.animator.start()
        self._probe()

    def _probe(self) -> None:
        """Mesure la latence : écart entre le délai demandé à ``after`` et le délai réel."""
        sent = time.perf_counter()

        def back() -> None:
            self.lag_ms = max(0.0, (time.perf_counter() - sent) * 1000 - 50)
            if self.frame.winfo_exists():
                self.after(200, self._probe)

        self.after(50, back)

    def _sample(self, _dt: float) -> None:
        now = time.perf_counter()
        dt = max(1e-3, now - self.last_t)
        self.last_t = now
        cpu = self.cpu.sample()
        rss = memory_rss() or 0
        mem_mb = rss / 1048576
        self.peak_mem = max(self.peak_mem, mem_mb)
        bus = self.app.bus
        ev_rate = (bus.total - self.last_events) / dt
        self.last_events = bus.total
        task_events = sum(n for topic, n in bus.counts.items() if topic.startswith("task."))
        task_rate = (task_events - self.last_tasks) / dt
        self.last_tasks = task_events

        self.c_cpu.push("CPU", cpu)
        self.c_mem.push("Mémoire", mem_mb)
        self.c_lag.push("Latence", self.lag_ms)
        self.c_evt.push("Bus", ev_rate)
        self.c_evt.push("Tâches", task_rate)
        for ch in (self.c_cpu, self.c_mem, self.c_lag, self.c_evt):
            ch.redraw()
        self.g_cpu.set(min(100.0, cpu))
        self.g_mem.set(self.lag_ms)

        self.t_cpu.update_value(f"{cpu:.0f} %", f"charge système : {load_average() or 0:.2f}",
                                cpu)
        self.t_mem.update_value(human_bytes(rss), f"pic : {self.peak_mem:.0f} Mo", mem_mb)
        self.t_lag.update_value(f"{self.lag_ms:.1f} ms",
                                "fluide" if self.lag_ms < 20 else "chargée", self.lag_ms)
        self.t_evt.update_value(f"{bus.total}", f"{ev_rate:.0f}/s · {bus.subscriber_count} "
                                "abonnés", ev_rate)
        active = len(self.app.tasks.active)
        self.t_task.update_value(str(active), f"{len(self.app.tasks.tasks)} au total", active)
        threads = threading.active_count()
        self.t_thr.update_value(str(threads), "pool de tâches inclus", threads)
        up = int(time.time() - self.app.started)
        self.uptime.configure(text=f"En service depuis {up // 3600:d} h {up // 60 % 60:02d} min "
                              f"{up % 60:02d} s")
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        rows = []
        for mid, inst in self.app.instances.items():
            cls = type(inst)
            info = inst.info() if hasattr(inst, "info") else {}
            details = " · ".join(f"{k} {v}" for k, v in list(info.items())[:4])
            rows.append((mid, (f"{cls.icon}  {cls.title}", cls.category,
                               f"{inst.fps:.0f}" if inst.fps else "—", details)))
        existing = set(self.tree.get_children())
        for mid, values in rows:
            if mid in existing:
                self.tree.item(mid, values=values)
                existing.discard(mid)
            else:
                self.tree.insert("", "end", iid=mid, values=values)
        for iid in existing:
            self.tree.delete(iid)

    def _animate(self, _dt: float) -> None:
        self.g_cpu.animate()
        self.g_mem.animate()

    def on_theme(self, theme) -> None:
        for ch in (self.c_cpu, self.c_mem, self.c_lag, self.c_evt):
            ch.redraw()
        for tile in (self.t_cpu, self.t_mem, self.t_lag, self.t_evt, self.t_task, self.t_thr):
            tile.spark.redraw()

    def info(self):
        return {"événements": self.app.bus.total}
