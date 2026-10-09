"""Bac à sable physique : tissu de Verlet, gravitation Barnes-Hut, billes à impulsions."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core.colors import hsv_to_hex, mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..engines import physics
from ..ui.widgets import (LabeledScale, Section, Segmented, button_row, check, combo, info_label,
                          side_layout, themed_canvas)


class PhysicsModule(StudioModule):
    id = "physics"
    title = "Bac à sable physique"
    icon = "⚛"
    category = "Simulation"
    description = "Tissu déchirable (Verlet), galaxies en collision (Barnes-Hut, saute-mouton) " \
                  "et billes rebondissantes (hachage spatial)."
    order = 31
    keywords = ("verlet", "barnes-hut", "gravité", "n-body", "collision", "tissu")

    def build(self, frame: ttk.Frame) -> None:
        self.mode = "cloth"
        self.cloth = physics.ClothWorld()
        self.gravity = physics.GravityWorld()
        self.balls = physics.BallWorld()
        self.time_scale = 1.0
        self.items: List[int] = []
        self.item_map: Dict[int, int] = {}
        self._stick_count = -1
        self._mouse: Optional[Tuple[float, float]] = None
        self._launch: Optional[Tuple[float, float]] = None
        self._last_energy = 0.0
        self.energy_text = ""
        self.step_ms = 0.0

        main, panel = side_layout(frame, 290, "Physique", "")
        self.hint = ttk.Label(panel, text="", style="Muted.TLabel", wraplength=260, justify="left")
        self.hint.pack(anchor="w", padx=12)
        self.canvas = themed_canvas(main, role="canvas", cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", lambda e: self._right(e, True))
        self.canvas.bind("<B3-Motion>", lambda e: self._right(e, False))

        s = Section(panel, "Scène")
        s.pack(fill="x")
        self.mode_seg = Segmented(s.body, [("cloth", "Tissu"), ("gravity", "Gravité"),
                                           ("balls", "Billes")], "cloth", self.set_mode)
        self.mode_seg.pack(fill="x", pady=3)
        self.play = button_row(s.body, [("⏸ Pause", self.toggle), ("↺ Réinitialiser", self.reset)],
                               accent_first=True)
        LabeledScale(s.body, "Vitesse du temps", 0.1, 3.0, 1.0, fmt="×{:.1f}",
                     command=lambda v: setattr(self, "time_scale", v)).pack(fill="x", pady=(6, 0))

        # -- Tissu
        self.cloth_box = Section(panel, "Tissu (Verlet)")
        b = self.cloth_box.body
        self.cloth_preset = combo(b, ["Tissu épinglé", "Rideau", "Cordes"], "Tissu épinglé",
                                  lambda v: self.reset())
        self.cloth_preset.pack(fill="x", pady=3)
        LabeledScale(b, "Gravité", 0, 2500, self.cloth.gravity, integer=True,
                     command=lambda v: setattr(self.cloth, "gravity", v)).pack(fill="x")
        LabeledScale(b, "Vent", -2500, 2500, 0, integer=True,
                     command=lambda v: setattr(self.cloth, "wind", v)).pack(fill="x")
        LabeledScale(b, "Seuil de déchirure", 1.5, 8.0, self.cloth.tear, fmt="×{:.1f}",
                     command=lambda v: setattr(self.cloth, "tear", v)).pack(fill="x")
        LabeledScale(b, "Itérations du solveur", 1, 16, self.cloth.iterations, integer=True,
                     command=lambda v: setattr(self.cloth, "iterations", int(v))).pack(fill="x")
        self.show_tension = check(b, "Colorer selon la tension", True)

        # -- Gravité
        self.grav_box = Section(panel, "Gravitation (Barnes-Hut)")
        b = self.grav_box.body
        self.grav_preset = combo(b, physics.GRAVITY_PRESETS, "Collision de galaxies",
                                 lambda v: self.reset())
        self.grav_preset.pack(fill="x", pady=3)
        LabeledScale(b, "Constante G", 5, 200, self.gravity.G, integer=True,
                     command=lambda v: setattr(self.gravity, "G", v)).pack(fill="x")
        LabeledScale(b, "Critère θ (précision ↔ vitesse)", 0.1, 1.5, self.gravity.theta,
                     fmt="{:.2f}", command=lambda v: setattr(self.gravity, "theta", v)).pack(fill="x")
        self.bh_var = check(b, "Arbre de Barnes-Hut (sinon O(n²))", True,
                            lambda v: setattr(self.gravity, "use_tree", v))
        self.merge_var = check(b, "Fusion des corps qui se touchent", True,
                               lambda v: setattr(self.gravity, "merge", v))
        self.trails_var = check(b, "Traînées", True)
        self.tree_var = check(b, "Afficher l'arbre quaternaire", False)
        button_row(b, [("Recentrer", self._recenter)])

        # -- Billes
        self.ball_box = Section(panel, "Billes (impulsions)")
        b = self.ball_box.body
        LabeledScale(b, "Gravité", 0, 2000, self.balls.gravity, integer=True,
                     command=lambda v: setattr(self.balls, "gravity", v)).pack(fill="x")
        LabeledScale(b, "Restitution", 0.1, 1.0, self.balls.restitution, fmt="{:.2f}",
                     command=lambda v: setattr(self.balls, "restitution", v)).pack(fill="x")
        LabeledScale(b, "Sous-pas", 1, 10, self.balls.substeps, integer=True,
                     command=lambda v: setattr(self.balls, "substeps", int(v))).pack(fill="x")
        button_row(b, [("Remplir", lambda: (self.balls.fill(200), self._rebuild())),
                       ("Vider", lambda: (self.balls.balls.clear(), self._rebuild()))])

        s = Section(panel, "Mesures")
        s.pack(fill="x", side="bottom")
        self.stats = info_label(s.body, "")
        self.sim = self.loop(self._tick, fps=60, name="physics.sim")
        self.sim.start()
        self.set_mode("cloth")

    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.mode_seg.set(mode)
        for box, m in ((self.cloth_box, "cloth"), (self.grav_box, "gravity"),
                       (self.ball_box, "balls")):
            if m == mode:
                box.pack(fill="x", after=self.mode_seg.master.master)
            else:
                box.pack_forget()
        hints = {"cloth": "Glisser : attraper le tissu · clic droit : couper · Maj + clic : "
                          "épingler / libérer un point.",
                 "gravity": "Glisser : lancer un astre (la flèche donne la vitesse) · clic "
                            "droit : créer une étoile massive.",
                 "balls": "Glisser : lancer une bille · clic droit : pluie de billes."}
        self.hint.configure(text=hints[mode])
        self.reset()

    def reset(self) -> None:
        w, h = max(200, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        if self.mode == "cloth":
            self.cloth.width, self.cloth.height = w, h
            preset = self.cloth_preset.var.get()  # type: ignore[attr-defined]
            if preset == "Rideau":
                self.cloth.make_curtain()
            elif preset == "Cordes":
                self.cloth.make_ropes()
            else:
                cols = max(10, min(48, int(w * 0.75 / 16)))
                self.cloth.make_cloth(cols=cols, rows=max(8, min(30, int(h * 0.6 / 16))))
        elif self.mode == "gravity":
            self.gravity.preset(self.grav_preset.var.get(), w, h)  # type: ignore[attr-defined]
        else:
            self.balls.width, self.balls.height = w, h
            self.balls.fill(160)
        self._rebuild()

    def toggle(self) -> None:
        running = self.sim.toggle()
        self.play[0].configure(text="⏸ Pause" if running else "▶ Lecture")

    def _resize(self, e: Any) -> None:
        self.cloth.width, self.cloth.height = e.width, e.height
        self.balls.width, self.balls.height = e.width, e.height
        if not getattr(self, "_sized", False):
            self._sized = True
            self.reset()

    def _recenter(self) -> None:
        bodies = self.gravity.bodies
        if not bodies:
            return
        m = sum(b.m for b in bodies)
        cx = sum(b.x * b.m for b in bodies) / m
        cy = sum(b.y * b.m for b in bodies) / m
        vx = sum(b.vx * b.m for b in bodies) / m
        vy = sum(b.vy * b.m for b in bodies) / m
        dx = self.canvas.winfo_width() / 2 - cx
        dy = self.canvas.winfo_height() / 2 - cy
        for b in bodies:
            b.x += dx
            b.y += dy
            b.vx -= vx
            b.vy -= vy
            b.trail.clear()

    # ------------------------------------------------------------------
    # Simulation et rendu
    # ------------------------------------------------------------------
    def _tick(self, dt: float) -> None:
        t0 = time.perf_counter()
        dt *= self.time_scale
        if self.mode == "cloth":
            if self._mouse and self.cloth.grabbed:
                self.cloth.grab_target = self._mouse
            self.cloth.step(min(dt, 1 / 30))
        elif self.mode == "gravity":
            steps = 2
            for _ in range(steps):
                self.gravity.step(min(dt, 1 / 30) / steps)
        else:
            self.balls.step(dt)
        self.step_ms = self.step_ms * 0.9 + (time.perf_counter() - t0) * 100
        self.draw()

    def _rebuild(self) -> None:
        self.canvas.delete("all")
        self.items = []
        self.item_map = {}
        self._stick_count = -1
        self.draw()

    def draw(self) -> None:
        if self.mode == "cloth":
            self._draw_cloth()
        elif self.mode == "gravity":
            self._draw_gravity()
        else:
            self._draw_balls()
        self._update_stats()

    def _draw_cloth(self) -> None:
        c = self.canvas
        t = self.theme
        w = self.cloth
        if len(w.sticks) != self._stick_count:
            c.delete("stick")
            self.items = [c.create_line(0, 0, 0, 0, fill=t.accent, width=1.5, tags="stick")
                          for _ in w.sticks]
            self._stick_count = len(w.sticks)
            self._buckets = [-1] * len(w.sticks)
            c.delete("pin")
        tension = self.show_tension.get()
        palette = [mix(t.accent, t.color(1), k / 5) for k in range(6)]
        for i, s in enumerate(w.sticks):
            item = self.items[i]
            c.coords(item, s.a.x, s.a.y, s.b.x, s.b.y)
            if tension:
                bucket = max(0, min(5, int(s.tension * 40)))
                if bucket != self._buckets[i]:
                    self._buckets[i] = bucket
                    c.itemconfigure(item, fill=palette[bucket])
            elif self._buckets[i] != -2:
                self._buckets[i] = -2
                c.itemconfigure(item, fill=t.accent)
        c.delete("pin")
        for p in w.points:
            if p.pinned:
                c.create_oval(p.x - 3, p.y - 3, p.x + 3, p.y + 3, fill=t.fg, outline="",
                              tags="pin")

    def _draw_gravity(self) -> None:
        c = self.canvas
        t = self.theme
        g = self.gravity
        c.delete("dyn")
        if self.tree_var.get() and g.tree is not None:
            stack = [(g.tree, 0)]
            color = mix(t.canvas, t.fg, 0.12)
            n = 0
            while stack and n < 2500:
                node, depth = stack.pop()
                if node.children is None:
                    continue
                n += 1
                c.create_rectangle(node.cx - node.half, node.cy - node.half, node.cx + node.half,
                                   node.cy + node.half, outline=color, tags="dyn")
                if depth < 8:
                    stack.extend((ch, depth + 1) for ch in node.children)
        trails = self.trails_var.get()
        alive = set()
        for b in g.bodies:
            key = id(b)
            alive.add(key)
            color = hsv_to_hex(b.hue, 0.55, 1.0) if b.m < 500 else "#fff6d5"
            if trails and len(b.trail) >= 2:
                pts = [v for p in list(b.trail)[::3] for v in p]
                if len(pts) >= 4:
                    c.create_line(*pts, fill=mix(t.canvas, color, 0.35), tags="dyn")
            r = b.r
            item = self.item_map.get(key)
            if item is None:
                item = c.create_oval(0, 0, 0, 0, fill=color, outline="", tags="body")
                self.item_map[key] = item
            c.coords(item, b.x - r, b.y - r, b.x + r, b.y + r)
            if b.m >= 500:
                c.itemconfigure(item, fill=color)
        for key in list(self.item_map):
            if key not in alive:
                c.delete(self.item_map.pop(key))
        c.tag_raise("body")
        if self._launch and self._mouse:
            (x0, y0), (x1, y1) = self._launch, self._mouse
            c.create_line(x0, y0, x1, y1, fill=t.accent, width=2, arrow="last", tags="dyn")

    def _draw_balls(self) -> None:
        c = self.canvas
        t = self.theme
        alive = set()
        for b in self.balls.balls:
            key = id(b)
            alive.add(key)
            item = self.item_map.get(key)
            if item is None:
                item = c.create_oval(0, 0, 0, 0, fill=t.color(int(b.hue * 8) % 8),
                                     outline=t.canvas, tags="ball")
                self.item_map[key] = item
            c.coords(item, b.x - b.r, b.y - b.r, b.x + b.r, b.y + b.r)
        for key in list(self.item_map):
            if key not in alive:
                c.delete(self.item_map.pop(key))
        c.delete("dyn")
        if self._launch and self._mouse:
            (x0, y0), (x1, y1) = self._launch, self._mouse
            c.create_line(x0, y0, x1, y1, fill=t.accent, width=2, arrow="last", tags="dyn")

    def _update_stats(self) -> None:
        if self.mode == "cloth":
            w = self.cloth
            text = (f"Points       {len(w.points)}\nLiens        {len(w.sticks)}\n"
                    f"Itérations   {w.iterations}")
        elif self.mode == "gravity":
            g = self.gravity
            now = time.time()
            if now - self._last_energy > 1.0 and len(g.bodies) <= 600:
                self._last_energy = now
                ke, pe = g.energy()
                self.energy_text = f"Énergie      {ke + pe:,.0f}".replace(",", " ")
            n = len(g.bodies)
            direct = n * (n - 1)
            gain = direct / g.interactions if g.interactions else 1.0
            text = (f"Corps        {n}\nInteractions {g.interactions}\n"
                    f"Gain vs O(n²) ×{gain:.1f}\n{self.energy_text}")
        else:
            b = self.balls
            n = len(b.balls)
            text = (f"Billes       {n}\nTests/pas    {b.checks // max(1, b.substeps)}\n"
                    f"(naïf : {n * (n - 1) // 2})\nCollisions   {b.collisions}")
        self.stats.configure(text=f"{text}\nCalcul       {self.step_ms:.1f} ms")

    # ------------------------------------------------------------------
    # Souris
    # ------------------------------------------------------------------
    def _press(self, e: tk.Event) -> None:
        self._mouse = (e.x, e.y)
        if self.mode == "cloth":
            if e.state & 0x1:
                p = self.cloth.nearest(e.x, e.y, 25)
                if p:
                    p.pinned = not p.pinned
                    p.pin_x, p.pin_y = p.x, p.y
                return
            self.cloth.grabbed = self.cloth.nearest(e.x, e.y, 40)
        else:
            self._launch = (e.x, e.y)

    def _drag(self, e: tk.Event) -> None:
        self._mouse = (e.x, e.y)

    def _release(self, e: tk.Event) -> None:
        if self.mode == "cloth":
            self.cloth.grabbed = None
        elif self._launch:
            x0, y0 = self._launch
            vx, vy = (e.x - x0) * 2.0, (e.y - y0) * 2.0
            if self.mode == "gravity":
                self.gravity.bodies.append(physics.Body(x0, y0, vx * 0.5, vy * 0.5, 40, 0.15))
            else:
                self.balls.spawn(x0, y0, vx * 2, vy * 2, 14)
        self._launch = None
        self._mouse = None

    def _right(self, e: tk.Event, press: bool) -> None:
        if self.mode == "cloth":
            self.cloth.cut(e.x, e.y, 12)
        elif self.mode == "gravity":
            if press:
                self.gravity.bodies.append(physics.Body(e.x, e.y, 0, 0, 2500, 0.1))
        else:
            for _ in range(3):
                self.balls.spawn(e.x + (len(self.balls.balls) % 7 - 3) * 6, e.y, 0, 0)

    # ------------------------------------------------------------------
    def on_activate(self) -> None:
        self.canvas.focus_set()

    def on_theme(self, theme) -> None:
        self._rebuild()

    def commands(self) -> List[Command]:
        return [Command("physics.toggle", "Pause / lecture de la simulation", self.toggle, icon="⏯"),
                Command("physics.reset", "Réinitialiser la scène", self.reset, icon="↺"),
                Command("physics.cloth", "Scène : tissu", lambda: self.set_mode("cloth"), icon="▦"),
                Command("physics.gravity", "Scène : gravitation", lambda: self.set_mode("gravity"),
                        icon="✺"),
                Command("physics.balls", "Scène : billes", lambda: self.set_mode("balls"), icon="●")]

    def get_state(self) -> Dict[str, Any]:
        return {"mode": self.mode}

    def set_state(self, state: Dict[str, Any]) -> None:
        if state.get("mode") in ("cloth", "gravity", "balls"):
            self.set_mode(state["mode"])

    def info(self) -> Dict[str, Any]:
        return {"scène": self.mode, "calcul": f"{self.step_ms:.1f} ms"}

    def demo(self) -> None:
        order = ["cloth", "gravity", "balls"]
        self.set_mode(order[(order.index(self.mode) + 1) % 3])
