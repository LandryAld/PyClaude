"""Moteur 3D logiciel : arcball avec inertie, ombrage de Blinn-Phong, brouillard, rendu « toon »."""

from __future__ import annotations

import math
import os
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core.colors import COLORMAPS, hex_to_rgb, mix, rgb_to_hex
from ..core.commands import Command
from ..core.expr import ExprError
from ..core.linalg import Quaternion, arcball_vector, mat_vec
from ..core.module import StudioModule
from ..engines import mesh as M
from ..ui.widgets import (ColorButton, LabeledScale, Section, Segmented, button_row, check, combo,
                          info_label, side_layout, themed_canvas)

SURFACE = "Surface z = f(x, y)"


class Render3DModule(StudioModule):
    id = "render3d"
    title = "Moteur 3D"
    icon = "◭"
    category = "Graphisme"
    description = "Rendu 3D logiciel sur canevas : arcball à quaternions avec inertie, " \
                  "Blinn-Phong, brouillard, style « toon », surfaces mathématiques, OBJ."
    order = 50
    keywords = ("3d", "mesh", "obj", "rendu", "surface", "tore")

    def build(self, frame: ttk.Frame) -> None:
        self.mesh = M.torus().normalize_size(2.6)
        self.settings = M.RenderSettings()
        self.orientation = Quaternion.from_axis_angle((1, 0, 0), 0.5) * \
            Quaternion.from_axis_angle((0, 1, 0), 0.6)
        self.spin = Quaternion()
        self.auto_rotate = True
        self.rotate_speed = 0.6
        self.mode = "shaded"
        self.color_height = False
        self.colormap = "Océan"
        self.pool: List[int] = []
        self._drag: Optional[Tuple[float, float, float]] = None
        self._last_drag_t = 0.0
        self.render_ms = 0.0
        self.culled = 0
        self.drawn = 0
        self.light_az = 35.0
        self.light_el = 40.0

        main, panel = side_layout(frame, 290, "Moteur 3D",
                                  "Glisser : tourner (arcball) · molette : distance · "
                                  "double-clic : vue initiale")
        self.canvas = themed_canvas(main, role="canvas", cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.render())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", lambda e: self._zoom(-1 if e.delta > 0 else 1))
        self.canvas.bind("<Button-4>", lambda e: self._zoom(-1))
        self.canvas.bind("<Button-5>", lambda e: self._zoom(1))
        self.canvas.bind("<Double-Button-1>", lambda e: self._reset_view())

        s = Section(panel, "Objet")
        s.pack(fill="x")
        self.obj_cb = combo(s.body, list(M.PRIMITIVES) + [SURFACE], "Tore", self._on_object)
        self.obj_cb.pack(fill="x", pady=3)
        self.surf_var = tk.StringVar(value=M.SURFACES[0])
        self.surf_cb = ttk.Combobox(s.body, textvariable=self.surf_var, values=M.SURFACES,
                                    font="StudioMono")
        self.surf_cb.pack(fill="x", pady=3)
        self.surf_cb.bind("<Return>", lambda e: self._load_surface())
        self.surf_cb.bind("<<ComboboxSelected>>", lambda e: self._load_surface())
        self.res_scale = LabeledScale(s.body, "Résolution de la surface", 8, 60, 36, integer=True,
                                      command=lambda v: self._load_surface() if
                                      self.obj_cb.var.get() == SURFACE else None)  # type: ignore[attr-defined]
        self.res_scale.pack(fill="x")
        button_row(s.body, [("Importer OBJ…", self.import_obj), ("Exporter OBJ…", self.export_obj)])
        self.mesh_lbl = info_label(s.body, "", "Muted.TLabel")

        s = Section(panel, "Rendu")
        s.pack(fill="x")
        self.mode_seg = Segmented(s.body, [("shaded", "Ombré"), ("mixed", "Mixte"),
                                           ("wire", "Filaire")], "shaded", self._on_mode)
        self.mode_seg.pack(fill="x", pady=3)
        row = ttk.Frame(s.body, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Matière", style="Secondary.TLabel").pack(side="left")
        self.color_btn = ColorButton(row, self.theme.accent, command=self._on_color)
        self.color_btn.pack(side="right")
        self.height_var = check(s.body, "Couleur selon la hauteur", False, self._on_height)
        self.cmap_cb = combo(s.body, list(COLORMAPS), self.colormap, self._on_cmap)
        self.cmap_cb.pack(fill="x", pady=3)
        self.cull_var = check(s.body, "Élimination des faces arrière", True,
                              lambda v: (setattr(self.settings, "cull", v), self.render()))
        self.fog_var = check(s.body, "Brouillard de profondeur", True,
                             lambda v: (setattr(self.settings, "fog", 0.6 if v else 0.0),
                                        self.render()))
        self.settings.fog = 0.6
        self.axes_var = check(s.body, "Repère des axes", True, lambda v: self.render())
        LabeledScale(s.body, "Style « toon » (niveaux)", 0, 6, 0, integer=True,
                     command=lambda v: (setattr(self.settings, "toon", int(v)),
                                        self.render())).pack(fill="x")

        s = Section(panel, "Lumière")
        s.pack(fill="x")
        LabeledScale(s.body, "Azimut", -180, 180, self.light_az, integer=True,
                     command=lambda v: self._light(az=v)).pack(fill="x")
        LabeledScale(s.body, "Élévation", -80, 80, self.light_el, integer=True,
                     command=lambda v: self._light(el=v)).pack(fill="x")
        LabeledScale(s.body, "Ambiante", 0, 0.8, self.settings.ambient, fmt="{:.2f}",
                     command=lambda v: (setattr(self.settings, "ambient", v), self.render())
                     ).pack(fill="x")
        LabeledScale(s.body, "Spéculaire", 0, 1.5, self.settings.specular, fmt="{:.2f}",
                     command=lambda v: (setattr(self.settings, "specular", v), self.render())
                     ).pack(fill="x")
        LabeledScale(s.body, "Brillance", 2, 128, self.settings.shininess, integer=True, log=True,
                     command=lambda v: (setattr(self.settings, "shininess", v), self.render())
                     ).pack(fill="x")

        s = Section(panel, "Caméra")
        s.pack(fill="x")
        LabeledScale(s.body, "Champ de vision", 20, 110, self.settings.fov, integer=True, fmt="{}°",
                     command=lambda v: (setattr(self.settings, "fov", v), self.render())).pack(fill="x")
        self.auto_var = check(s.body, "Rotation automatique", True,
                              lambda v: setattr(self, "auto_rotate", v))
        LabeledScale(s.body, "Vitesse de rotation", 0.05, 3, self.rotate_speed, fmt="{:.2f}",
                     command=lambda v: setattr(self, "rotate_speed", v)).pack(fill="x")
        self.stats = info_label(panel, "", "Mono.TLabel")
        self.stats.pack_configure(padx=12)

        self._light()
        self._on_color(self.theme.accent)
        self.anim = self.loop(self._tick, fps=40, name="render3d.anim")
        self.anim.start()
        self._update_mesh_label()

    # ------------------------------------------------------------------
    def _light(self, az: Optional[float] = None, el: Optional[float] = None) -> None:
        if az is not None:
            self.light_az = az
        if el is not None:
            self.light_el = el
        a, e = math.radians(self.light_az), math.radians(self.light_el)
        self.settings.light = (math.sin(a) * math.cos(e), math.sin(e), math.cos(a) * math.cos(e))
        self.render()

    def _on_object(self, name: str) -> None:
        if name == SURFACE:
            self._load_surface()
            return
        self.mesh = M.PRIMITIVES[name]().normalize_size(2.6)
        self._after_mesh_change()

    def _load_surface(self) -> None:
        self.obj_cb.var.set(SURFACE)  # type: ignore[attr-defined]
        try:
            self.mesh = M.surface(self.surf_var.get(), nx=int(self.res_scale.get()), extent=3.0)
            self.mesh.normalize_size(2.6)
        except ExprError as e:
            self.notify(f"Expression invalide : {e}", "warning")
            return
        self.color_height = True
        self.height_var.set(True)
        self._after_mesh_change()

    def _after_mesh_change(self) -> None:
        if self.color_height:
            self.mesh.color_by_height(COLORMAPS[self.colormap])
        else:
            self.mesh.face_colors = None
        self._update_mesh_label()
        self.render()

    def _update_mesh_label(self) -> None:
        self.mesh_lbl.configure(text=f"{self.mesh.name}\n{self.mesh.stats}"
                                     f"{'' if self.mesh.closed else ' · ouvert'}")

    def _on_mode(self, mode: str) -> None:
        self.mode = mode
        self.render()

    def _on_color(self, color: str) -> None:
        self.settings.base_color = hex_to_rgb(color)
        self.render()

    def _on_height(self, v: bool) -> None:
        self.color_height = v
        self._after_mesh_change()

    def _on_cmap(self, name: str) -> None:
        self.colormap = name
        if self.color_height:
            self._after_mesh_change()

    def _reset_view(self) -> None:
        self.orientation = Quaternion.from_axis_angle((1, 0, 0), 0.5) * \
            Quaternion.from_axis_angle((0, 1, 0), 0.6)
        self.spin = Quaternion()
        self.settings.distance = 5.0
        self.render()

    def _zoom(self, d: int) -> None:
        self.settings.distance = max(2.2, min(30.0, self.settings.distance * (1.1 if d > 0 else 0.9)))
        self.render()

    # ------------------------------------------------------------------
    # Arcball
    # ------------------------------------------------------------------
    def _arc(self, x: float, y: float) -> Tuple[float, float, float]:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        v = arcball_vector(x, y, w, h)
        return (v[0], v[1], -v[2])  # repère caméra : le spectateur est en -z

    def _press(self, e: tk.Event) -> None:
        self._drag = self._arc(e.x, e.y)
        self.spin = Quaternion()

    def _drag_move(self, e: tk.Event) -> None:
        if not self._drag:
            return
        cur = self._arc(e.x, e.y)
        q = Quaternion.between(self._drag, cur)
        self.orientation = (q * self.orientation).normalized()
        self.spin = q
        self._drag = cur
        self._last_drag_t = time.perf_counter()
        self.render()

    def _release(self, _e: tk.Event) -> None:
        self._drag = None
        if time.perf_counter() - self._last_drag_t > 0.08:
            self.spin = Quaternion()  # relâché à l'arrêt : pas d'inertie

    def _tick(self, dt: float) -> None:
        if self._drag:
            return
        moving = False
        if abs(self.spin.w) < 0.999999:
            # inertie : la rotation résiduelle décroît exponentiellement
            self.orientation = (self.spin * self.orientation).normalized()
            self.spin = Quaternion().slerp(self.spin, 0.94)
            moving = True
        if self.auto_rotate:
            q = Quaternion.from_axis_angle((0, 1, 0), self.rotate_speed * dt)
            self.orientation = (self.orientation * q).normalized()
            moving = True
        if moving:
            self.render()

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------
    def render(self) -> None:
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 20 or h < 20:
            return
        t0 = time.perf_counter()
        t = self.theme
        s = self.settings
        s.width, s.height = w, h
        s.background = hex_to_rgb(t.canvas)
        rot = self.orientation.to_matrix()
        faces, self.culled = M.render(self.mesh, rot, s)
        self.drawn = len(faces)
        while len(self.pool) < len(faces):
            self.pool.append(c.create_polygon(0, 0, 0, 0, 0, 0, fill="", outline="",
                                              tags="face"))
        mode = self.mode
        wire = t.accent if mode == "wire" else None
        for i, f in enumerate(faces):
            item = self.pool[i]
            c.coords(item, *f.points)
            color = rgb_to_hex(f.color)
            if mode == "shaded":
                c.itemconfigure(item, fill=color, outline=color, state="normal")
            elif mode == "mixed":
                c.itemconfigure(item, fill=color, outline=mix(color, "#000000", 0.45),
                                state="normal")
            else:
                c.itemconfigure(item, fill="", outline=mix(t.canvas, wire or color, 0.85),
                                state="normal")
        for item in self.pool[len(faces):]:
            c.itemconfigure(item, state="hidden")
        c.configure(background=t.canvas)
        self._draw_axes(rot)
        self.render_ms = self.render_ms * 0.85 + (time.perf_counter() - t0) * 150
        self.stats.configure(text=f"faces dessinées {self.drawn}\nfaces éliminées {self.culled}\n"
                                  f"rendu {self.render_ms:.1f} ms")

    def _draw_axes(self, rot) -> None:
        c = self.canvas
        c.delete("axes")
        if not self.axes_var.get():
            return
        t = self.theme
        ox, oy = 60, c.winfo_height() - 60
        for axis, label, color in (((1, 0, 0), "X", t.color(7)), ((0, 1, 0), "Y", t.color(2)),
                                   ((0, 0, 1), "Z", t.color(0))):
            x, y, _z = mat_vec(rot, axis)
            ex, ey = ox + x * 38, oy - y * 38
            c.create_line(ox, oy, ex, ey, fill=color, width=2, arrow="last", tags="axes")
            c.create_text(ex + x * 10, ey - y * 10, text=label, fill=t.fg2, font="StudioSmall",
                          tags="axes")

    # ------------------------------------------------------------------
    def import_obj(self) -> None:
        path = filedialog.askopenfilename(parent=self.frame, filetypes=[("Wavefront OBJ", "*.obj"),
                                                                        ("Tous", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                self.mesh = M.parse_obj(fh.read(), os.path.basename(path))
        except (OSError, ValueError, IndexError) as exc:
            self.notify(f"OBJ illisible : {exc}", "error")
            return
        self.settings.cull = False
        self.cull_var.set(False)
        self._after_mesh_change()
        self.notify(f"{self.mesh.name} : {self.mesh.stats}", "success")

    def export_obj(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".obj",
                                            filetypes=[("Wavefront OBJ", "*.obj")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(M.to_obj(self.mesh))
            self.notify("Maillage exporté", "success")

    def on_theme(self, theme) -> None:
        self.render()

    def commands(self) -> List[Command]:
        cmds = [Command("render3d.reset", "Vue initiale", self._reset_view, icon="⟲"),
                Command("render3d.rotate", "Basculer la rotation automatique", lambda: (
                    self.auto_var.set(not self.auto_var.get()),
                    setattr(self, "auto_rotate", self.auto_var.get())), icon="↻")]
        for name in M.PRIMITIVES:
            cmds.append(Command(f"render3d.obj.{name}", f"Afficher : {name}",
                                lambda n=name: (self.obj_cb.var.set(n), self._on_object(n)),  # type: ignore[attr-defined]
                                icon="◭"))
        return cmds

    def get_state(self) -> Dict[str, Any]:
        return {"object": self.obj_cb.var.get(), "surface": self.surf_var.get(),  # type: ignore[attr-defined]
                "mode": self.mode, "q": self.orientation.as_tuple()}

    def set_state(self, state: Dict[str, Any]) -> None:
        obj = state.get("object")
        self.surf_var.set(state.get("surface", self.surf_var.get()))
        if obj in M.PRIMITIVES or obj == SURFACE:
            self.obj_cb.var.set(obj)  # type: ignore[attr-defined]
            self._on_object(obj)
        if state.get("mode") in ("shaded", "mixed", "wire"):
            self.mode = state["mode"]
            self.mode_seg.set(self.mode)
        q = state.get("q")
        if q and len(q) == 4:
            self.orientation = Quaternion(*q).normalized()
        self.render()

    def info(self) -> Dict[str, Any]:
        return {"faces": self.drawn, "rendu": f"{self.render_ms:.0f} ms"}

    def demo(self) -> None:
        names = list(M.PRIMITIVES) + [SURFACE]
        cur = self.obj_cb.var.get()  # type: ignore[attr-defined]
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.obj_cb.var.set(nxt)  # type: ignore[attr-defined]
        self._on_object(nxt)
