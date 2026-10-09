"""Explorateur de fractales : rendu progressif en arrière-plan, zoom, Julia, palettes."""

from __future__ import annotations

import os
import time
import tkinter as tk
from dataclasses import asdict, replace
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core import imaging
from ..core.colors import COLORMAPS, hex_to_rgb
from ..core.commands import Command
from ..core.module import StudioModule
from ..core.tasks import Cancelled
from ..engines import fractal
from ..engines.fractal import FractalParams
from ..ui.widgets import (ColorButton, LabeledScale, Section, Segmented, ToolTip, button_row,
                          check, combo, info_label, side_layout, themed_canvas)


class FractalModule(StudioModule):
    id = "fractal"
    title = "Fractales"
    icon = "❂"
    category = "Mathématiques"
    description = "Mandelbrot, Julia, Burning Ship, Newton… Zoom infini, rendu progressif " \
                  "multicœur ou numpy, cyclage de palette en temps réel, export PNG."
    order = 10
    keywords = ("mandelbrot", "julia", "fractale", "zoom")

    def build(self, frame: ttk.Frame) -> None:
        self.params = FractalParams()
        self.colormap = "Aurore"
        self.inside = "#000000"
        self.offset = 0.0
        self.cycle_speed = 40.0
        self.res_div = 1
        self.history: List[FractalParams] = [self.params]
        self.hpos = 0
        self.index: Optional[bytearray] = None
        self.img_w = self.img_h = 0
        self.photo: Optional[tk.PhotoImage] = None
        self.task = None
        self.render_ms = 0.0
        self._render_after: Optional[str] = None
        self._dirty = False
        self._drag: Optional[Tuple[int, int]] = None
        self._pan: Optional[Tuple[int, int]] = None
        self._tables = None

        main, panel = side_layout(frame, 300, "Fractales",
                                  "Molette : zoom · glisser : cadrer · clic droit : menu · "
                                  "Maj + survol : orbite")
        self.canvas = themed_canvas(main, role="canvas", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.img_item = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.request_render(150))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 0.75))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 0.75))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._pan_end)
        self.canvas.bind("<ButtonRelease-3>", self._context)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("orbit"))
        for key, fn in (("<plus>", lambda e: self._zoom_center(0.5)),
                        ("<minus>", lambda e: self._zoom_center(2.0)),
                        ("<BackSpace>", lambda e: self.back())):
            self.canvas.bind(key, fn)
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set())

        s = Section(panel, "Fractale")
        s.pack(fill="x")
        self.kind_cb = combo(s.body, list(fractal.KINDS.values()),
                             fractal.KINDS[self.params.kind], self._on_kind)
        self.kind_cb.pack(fill="x", pady=3)
        ttk.Label(s.body, text="Lieux remarquables", style="Secondary.TLabel").pack(anchor="w",
                                                                                   pady=(6, 0))
        self.preset_cb = combo(s.body, [n for n, _ in fractal.PRESETS], "Vue d'ensemble",
                               self._on_preset)
        self.preset_cb.pack(fill="x", pady=3)

        s = Section(panel, "Calcul")
        s.pack(fill="x")
        self.iter_scale = LabeledScale(s.body, "Itérations max.", 20, 5000, self.params.max_iter,
                                       integer=True, log=True, command=self._on_iter)
        self.iter_scale.pack(fill="x")
        self.res_seg = Segmented(s.body, [("1", "Pleine résolution"), ("2", "Rapide (½)")], "1",
                                 self._on_res)
        self.res_seg.pack(fill="x", pady=(2, 6))
        self.engine_lbl = info_label(s.body, "", "Muted.TLabel")

        s = Section(panel, "Couleurs")
        s.pack(fill="x")
        self.cmap_cb = combo(s.body, list(COLORMAPS), self.colormap, self._on_cmap)
        self.cmap_cb.pack(fill="x", pady=3)
        self.palette_preview = themed_canvas(s.body, role="surface", height=14)
        self.palette_preview.pack(fill="x", pady=(2, 6))
        self.density_scale = LabeledScale(s.body, "Densité des couleurs", 0.3, 16,
                                          self.params.density, fmt="{:.1f}",
                                          command=self._on_density)
        self.density_scale.pack(fill="x")
        self.offset_scale = LabeledScale(s.body, "Décalage", 0, 254, 0, integer=True,
                                         command=self._on_offset)
        self.offset_scale.pack(fill="x")
        row = ttk.Frame(s.body, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Intérieur de l'ensemble", style="Secondary.TLabel").pack(side="left")
        self.inside_btn = ColorButton(row, self.inside, command=self._on_inside)
        self.inside_btn.pack(side="right")
        self.cycle_var = check(s.body, "Cyclage animé de la palette", False, self._on_cycle)
        self.cycle_loop = self.loop(self._cycle_tick, fps=30, name="fractal.cycle")

        self.julia_section = Section(panel, "Paramètre c (Julia)")
        self.julia_section.pack(fill="x")
        self.jr_scale = LabeledScale(self.julia_section.body, "Re(c)", -2.0, 2.0, self.params.jr,
                                     fmt="{:+.4f}", command=self._on_julia)
        self.jr_scale.pack(fill="x")
        self.ji_scale = LabeledScale(self.julia_section.body, "Im(c)", -2.0, 2.0, self.params.ji,
                                     fmt="{:+.4f}", command=self._on_julia)
        self.ji_scale.pack(fill="x")
        ttk.Label(self.julia_section.body, text="Astuce : clic droit sur Mandelbrot › « Julia en "
                  "ce point ».", style="Muted.TLabel", wraplength=260).pack(anchor="w")

        s = Section(panel, "Navigation")
        s.pack(fill="x")
        b = button_row(s.body, [("◀ Préc.", self.back), ("Suiv. ▶", self.forward),
                                ("⟲ Vue", self.reset_view)])
        ToolTip(b[0], "Vue précédente (Retour arrière)")
        button_row(s.body, [("Zoom ×2", lambda: self._zoom_center(0.5)),
                            ("Zoom ÷2", lambda: self._zoom_center(2.0))])
        self.coords_lbl = info_label(s.body, "")

        s = Section(panel, "Exporter")
        s.pack(fill="x")
        button_row(s.body, [("Image PNG…", self.export_png)], accent_first=True)
        button_row(s.body, [("PNG haute définition (×2)…", self.export_hd)])
        self.stats_lbl = info_label(panel, "", "Muted.TLabel")
        self._update_engine_label()
        self._draw_palette_preview()
        self._sync_julia_section()

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------
    @property
    def engine(self) -> str:
        return fractal.resolve_engine(self.app.settings.get("fractal.engine") or "auto")

    def _update_engine_label(self) -> None:
        names = {"numpy": "numpy (vectorisé)",
                 "process": f"multiprocessus ({os.cpu_count() or 1} cœurs)",
                 "thread": "Python pur (1 thread)"}
        self.engine_lbl.configure(text=f"Moteur : {names.get(self.engine, self.engine)}")

    def request_render(self, delay: int = 60) -> None:
        if self._render_after:
            try:
                self.frame.after_cancel(self._render_after)
            except tk.TclError:
                pass
        self._render_after = self.after(delay, self._start_render)

    def _start_render(self) -> None:
        self._render_after = None
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 20 or ch < 20:
            return
        if self.task is not None and self.task.active:
            self.task.cancel()
        w, h = max(8, cw // self.res_div), max(8, ch // self.res_div)
        p = self.params
        engine = self.engine
        self._update_engine_label()
        t0 = time.perf_counter()

        def job(ctx):
            pw, ph = max(4, w // 4), max(4, h // 4)
            prev = fractal.render(replace(p, max_iter=min(p.max_iter, 400)), pw, ph,
                                  engine=engine, cancelled=lambda: ctx.cancelled)
            if prev is None:
                raise Cancelled()
            ctx.partial(("preview", pw, ph, prev))
            done = [0]

            def chunk(y0: int, y1: int, data: bytes) -> None:
                done[0] += y1 - y0
                ctx.partial(("rows", y0, y1, data))
                ctx.progress(done[0] / h, f"{done[0]}/{h} lignes")

            full = fractal.render(p, w, h, engine=engine, on_chunk=chunk,
                                  cancelled=lambda: ctx.cancelled)
            if full is None:
                raise Cancelled()
            return full

        def partial(data: Any) -> None:
            kind = data[0]
            if kind == "preview":
                _, pw, ph, prev = data
                self.img_w, self.img_h = w, h
                self.index = self._upscale_index(prev, pw, ph, w, h)
                self._refresh_image()
            elif kind == "rows" and self.index is not None and self.img_w == w:
                _, y0, y1, rows = data
                self.index[y0 * w:y1 * w] = rows
                self._dirty = True

        def done(full: bytes) -> None:
            self.img_w, self.img_h = w, h
            self.index = bytearray(full)
            self.render_ms = (time.perf_counter() - t0) * 1000
            self._refresh_image()
            self._update_stats()

        self.task = self.run_task(f"Fractale {fractal.KINDS[p.kind]} {w}×{h}", job,
                                  on_partial=partial, on_done=done)
        self._pump_progress()

    def _pump_progress(self) -> None:
        if self._dirty:
            self._dirty = False
            self._refresh_image()
        if self.task is not None and self.task.active:
            self.after(90, self._pump_progress)

    @staticmethod
    def _upscale_index(data: bytes, pw: int, ph: int, w: int, h: int) -> bytearray:
        xs = [min(pw - 1, x * pw // w) for x in range(w)]
        rows = []
        for y in range(h):
            sy = min(ph - 1, y * ph // h)
            row = data[sy * pw:(sy + 1) * pw]
            rows.append(bytes(row[x] for x in xs))
        return bytearray(b"".join(rows))

    def _tables_for(self) -> Tuple[bytes, bytes, bytes]:
        lut = fractal.build_lut(self.params, self.colormap, int(self.offset) % 255, self.inside)
        return imaging.lut_to_tables(lut)

    def _refresh_image(self) -> None:
        if self.index is None or not self.img_w:
            return
        rgb = imaging.index_to_rgb(bytes(self.index), self._tables_for())
        try:
            self.photo = imaging.photo_image(self.canvas, self.img_w, self.img_h, rgb,
                                             zoom=self.res_div)
        except tk.TclError:
            return
        self.canvas.itemconfigure(self.img_item, image=self.photo)
        self.canvas.tag_lower(self.img_item)

    def _update_stats(self) -> None:
        p = self.params
        self.stats_lbl.configure(
            text=f"Rendu {self.img_w}×{self.img_h} en {self.render_ms:.0f} ms\n"
                 f"Zoom ×{p.zoom_level:.3g} · {p.max_iter} itérations")
        self.coords_lbl.configure(text=f"centre {p.cx:+.12f}\n       {p.cy:+.12f} i\n"
                                       f"largeur {p.span:.3e}")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def set_params(self, p: FractalParams, record: bool = True) -> None:
        self.params = p
        if record:
            del self.history[self.hpos + 1:]
            self.history.append(p)
            self.hpos = len(self.history) - 1
            if len(self.history) > 100:
                self.history.pop(0)
                self.hpos -= 1
        self.request_render(60)
        self._update_stats()

    def back(self) -> None:
        if self.hpos > 0:
            self.hpos -= 1
            self._apply_history()

    def forward(self) -> None:
        if self.hpos < len(self.history) - 1:
            self.hpos += 1
            self._apply_history()

    def _apply_history(self) -> None:
        p = self.history[self.hpos]
        self.params = p
        self._sync_controls()
        self.request_render(10)

    def reset_view(self) -> None:
        d = fractal.default_for(self.params.kind)
        self.set_params(replace(self.params, cx=d.cx, cy=d.cy, span=d.span))

    def _size(self) -> Tuple[int, int]:
        return self.canvas.winfo_width(), self.canvas.winfo_height()

    def _zoom_at(self, x: int, y: int, factor: float) -> None:
        w, h = self._size()
        self.set_params(self.params.zoomed(factor, x, y, w, h))

    def _zoom_center(self, factor: float) -> None:
        w, h = self._size()
        self._zoom_at(w // 2, h // 2, factor)

    def _wheel(self, e: tk.Event) -> None:
        self._zoom_at(e.x, e.y, 0.75 if e.delta > 0 else 1 / 0.75)

    def _press(self, e: tk.Event) -> None:
        self._drag = (e.x, e.y)

    def _drag_motion(self, e: tk.Event) -> None:
        if not self._drag:
            return
        x0, y0 = self._drag
        self.canvas.delete("band")
        t = self.theme
        self.canvas.create_rectangle(x0, y0, e.x, e.y, outline="#ffffff", width=2, tags="band")
        self.canvas.create_rectangle(x0, y0, e.x, e.y, outline=t.accent, dash=(4, 3),
                                     tags="band")

    def _release(self, e: tk.Event) -> None:
        if not self._drag:
            return
        x0, y0 = self._drag
        self._drag = None
        self.canvas.delete("band")
        w, h = self._size()
        if abs(e.x - x0) < 6 and abs(e.y - y0) < 6:
            self._zoom_at(e.x, e.y, 0.5)  # simple clic : zoom ×2 sur le point
            return
        self.set_params(self.params.framed(x0, y0, e.x, e.y, w, h))

    def _pan_start(self, e: tk.Event) -> None:
        self._pan = (e.x, e.y)
        self.canvas.configure(cursor="fleur")

    def _pan_move(self, e: tk.Event) -> None:
        if self._pan:
            self.canvas.coords(self.img_item, e.x - self._pan[0], e.y - self._pan[1])

    def _pan_end(self, e: tk.Event) -> None:
        if not self._pan:
            return
        dx, dy = e.x - self._pan[0], e.y - self._pan[1]
        self._pan = None
        self.canvas.configure(cursor="crosshair")
        self.canvas.coords(self.img_item, 0, 0)
        w, _ = self._size()
        s = self.params.scale(w)
        self.set_params(replace(self.params, cx=self.params.cx - dx * s,
                                cy=self.params.cy + dy * s))

    def _motion(self, e: tk.Event) -> None:
        w, h = self._size()
        cr, ci = self.params.to_complex(e.x, e.y, w, h)
        self.app.set_status(f"c = {cr:+.10f} {ci:+.10f} i")
        self.canvas.delete("orbit")
        if e.state & 0x1:  # Maj enfoncée : orbite de z
            pts = fractal.orbit(self.params, cr, ci, 150)
            s = self.params.scale(w)
            coords = []
            for zr, zi in pts:
                coords += [w / 2 + (zr - self.params.cx) / s, h / 2 - (zi - self.params.cy) / s]
            if len(coords) >= 4:
                self.canvas.create_line(*coords, fill="#ffffff", width=1, tags="orbit")
                for i in range(0, min(len(coords), 60), 2):
                    self.canvas.create_oval(coords[i] - 2, coords[i + 1] - 2, coords[i] + 2,
                                            coords[i + 1] + 2, fill=self.theme.accent,
                                            outline="", tags="orbit")

    def _context(self, e: tk.Event) -> None:
        w, h = self._size()
        cr, ci = self.params.to_complex(e.x, e.y, w, h)
        menu = tk.Menu(self.canvas, tearoff=False)
        menu.add_command(label="Centrer ici", command=lambda: self.set_params(
            replace(self.params, cx=cr, cy=ci)))
        menu.add_command(label="Zoom ×4 ici", command=lambda: self._zoom_at(e.x, e.y, 0.25))
        if self.params.kind != "julia":
            menu.add_command(label=f"Julia en ce point (c = {cr:+.4f} {ci:+.4f} i)",
                             command=lambda: self.julia_at(cr, ci))
        menu.add_separator()
        menu.add_command(label="Copier les coordonnées",
                         command=lambda: self._copy(f"{cr!r} {ci!r}"))
        self.app.themes.recolor_tree(menu)
        menu.tk_popup(e.x_root, e.y_root)

    def _copy(self, text: str) -> None:
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self.notify("Coordonnées copiées", "success")

    def julia_at(self, cr: float, ci: float) -> None:
        self.set_params(replace(self.params, kind="julia", jr=cr, ji=ci, cx=0.0, cy=0.0,
                                span=3.2))
        self._sync_controls()

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------
    def _sync_controls(self) -> None:
        p = self.params
        self.kind_cb.var.set(fractal.KINDS[p.kind])  # type: ignore[attr-defined]
        self.iter_scale.set(p.max_iter)
        self.density_scale.set(p.density)
        self.jr_scale.set(p.jr)
        self.ji_scale.set(p.ji)
        self._sync_julia_section()
        self._update_stats()

    def _sync_julia_section(self) -> None:
        state = "!disabled" if self.params.kind == "julia" else "disabled"
        for sc in (self.jr_scale, self.ji_scale):
            sc.scale.state([state])

    def _on_kind(self, label: str) -> None:
        kind = next(k for k, v in fractal.KINDS.items() if v == label)
        d = fractal.default_for(kind)
        self.set_params(replace(d, density=self.params.density))
        self._sync_controls()

    def _on_preset(self, name: str) -> None:
        p = dict(fractal.PRESETS)[name]
        self.set_params(p)
        self._sync_controls()

    def _on_iter(self, v: float) -> None:
        self.params = replace(self.params, max_iter=int(v))
        self.request_render(250)

    def _on_res(self, v: str) -> None:
        self.res_div = int(v)
        self.request_render(10)

    def _on_density(self, v: float) -> None:
        self.params = replace(self.params, density=round(v, 2))
        self.request_render(250)

    def _on_offset(self, v: float) -> None:
        self.offset = v
        self._refresh_image()

    def _on_cmap(self, name: str) -> None:
        self.colormap = name
        self._draw_palette_preview()
        self._refresh_image()

    def _on_inside(self, color: str) -> None:
        self.inside = color
        self._refresh_image()

    def _on_cycle(self, on: bool) -> None:
        if on:
            self.cycle_loop.start()
        else:
            self.cycle_loop.stop()

    def _cycle_tick(self, dt: float) -> None:
        self.offset = (self.offset + self.cycle_speed * dt) % 255
        self._refresh_image()

    def _on_julia(self, _v: float) -> None:
        if self.params.kind != "julia":
            return
        self.params = replace(self.params, jr=self.jr_scale.get(), ji=self.ji_scale.get())
        self.request_render(120)

    def _draw_palette_preview(self) -> None:
        c = self.palette_preview
        c.update_idletasks()
        c.delete("all")
        w = max(c.winfo_width(), 260)
        lut = fractal.build_lut(FractalParams(), self.colormap)
        n = 64
        for i in range(n):
            r, g, b = lut[int(i / n * 254)]
            c.create_rectangle(i * w / n, 0, (i + 1) * w / n + 1, 14, fill=f"#{r:02x}{g:02x}{b:02x}",
                               outline="")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_png(self) -> None:
        if self.index is None:
            return
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".png",
                                            filetypes=[("Image PNG", "*.png")],
                                            initialfile=f"{self.params.kind}.png")
        if not path:
            return
        rgb = imaging.index_to_rgb(bytes(self.index), self._tables_for())
        with open(path, "wb") as fh:
            fh.write(imaging.png(self.img_w, self.img_h, rgb))
        self.notify(f"Image enregistrée : {os.path.basename(path)}", "success")

    def export_hd(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".png",
                                            filetypes=[("Image PNG", "*.png")],
                                            initialfile=f"{self.params.kind}_hd.png")
        if not path:
            return
        w, h = self._size()
        w, h = w * 2, h * 2
        p = replace(self.params, span=self.params.span)
        tables = self._tables_for()
        engine = self.engine

        def job(ctx):
            img = fractal.render(p, w, h, engine=engine, cancelled=lambda: ctx.cancelled,
                                 on_chunk=lambda a, b, d: ctx.progress(b / h))
            if img is None:
                raise Cancelled()
            data = imaging.png(w, h, imaging.index_to_rgb(img, tables))
            with open(path, "wb") as fh:
                fh.write(data)
            return path

        self.run_task(f"Export HD {w}×{h}", job,
                      on_done=lambda p_: self.notify(f"Image HD enregistrée ({w}×{h})", "success"))
        self.notify("Export haute définition lancé en arrière-plan")

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def on_activate(self) -> None:
        if self.index is None:
            self.request_render(50)

    def on_theme(self, theme) -> None:
        self._draw_palette_preview()

    def commands(self) -> List[Command]:
        return [
            Command("fractal.back", "Vue précédente", self.back, shortcut="Alt+Left", icon="◀"),
            Command("fractal.forward", "Vue suivante", self.forward, shortcut="Alt+Right",
                    icon="▶"),
            Command("fractal.reset", "Réinitialiser la vue", self.reset_view, icon="⟲"),
            Command("fractal.export", "Exporter l'image PNG", self.export_png, icon="⇩"),
            Command("fractal.cycle", "Basculer le cyclage de palette", lambda: (
                self.cycle_var.set(not self.cycle_var.get()),
                self._on_cycle(self.cycle_var.get())), icon="◌"),
        ]

    def get_state(self) -> Dict[str, Any]:
        return {"params": asdict(self.params), "colormap": self.colormap, "inside": self.inside,
                "offset": self.offset}

    def set_state(self, state: Dict[str, Any]) -> None:
        try:
            self.params = FractalParams(**state.get("params", {}))
        except TypeError:
            return
        self.history = [self.params]
        self.hpos = 0
        self.colormap = state.get("colormap", self.colormap)
        if self.colormap not in COLORMAPS:
            self.colormap = "Aurore"
        self.cmap_cb.var.set(self.colormap)  # type: ignore[attr-defined]
        self.inside = state.get("inside", self.inside)
        hex_to_rgb(self.inside)
        self.inside_btn.set(self.inside)
        self.offset = float(state.get("offset", 0.0))
        self.offset_scale.set(self.offset)
        self._sync_controls()
        self._draw_palette_preview()

    def info(self) -> Dict[str, Any]:
        return {"zoom": f"×{self.params.zoom_level:.3g}", "moteur": self.engine,
                "rendu": f"{self.render_ms:.0f} ms"}

    def demo(self) -> None:
        names = [n for n, _ in fractal.PRESETS]
        current = self.preset_cb.var.get()  # type: ignore[attr-defined]
        nxt = names[(names.index(current) + 1) % len(names)] if current in names else names[1]
        self.preset_cb.var.set(nxt)  # type: ignore[attr-defined]
        self._on_preset(nxt)
