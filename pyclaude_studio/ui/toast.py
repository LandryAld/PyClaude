"""Notifications « toast » animées, empilées en bas à droite de la fenêtre."""

from __future__ import annotations

import tkinter as tk
from typing import List

from .widgets import theme_of

_ICONS = {"info": "ℹ", "success": "✔", "warning": "⚠", "error": "✖"}


class Toast:
    def __init__(self, manager: "ToastManager", message: str, kind: str, duration: int) -> None:
        self.manager = manager
        root = manager.root
        t = theme_of(root)
        color = {"success": t.success, "warning": t.warning, "error": t.error}.get(kind, t.accent)
        self.frame = tk.Frame(root, background=t.border, padx=1, pady=1)
        self.frame._studio_role = "skip"  # type: ignore[attr-defined]
        inner = tk.Frame(self.frame, background=t.surface2)
        inner.pack(fill="both", expand=True)
        stripe = tk.Frame(inner, background=color, width=4)
        stripe.pack(side="left", fill="y")
        tk.Label(inner, text=_ICONS.get(kind, "ℹ"), background=t.surface2, foreground=color,
                 font="StudioHeading", padx=10).pack(side="left")
        tk.Label(inner, text=message, background=t.surface2, foreground=t.fg,
                 font="StudioBody", justify="left", wraplength=320, pady=10).pack(
            side="left", padx=(0, 6))
        close = tk.Label(inner, text="×", background=t.surface2, foreground=t.muted,
                         font="StudioHeading", padx=8, cursor="hand2")
        close.pack(side="right", anchor="n")
        close.bind("<Button-1>", lambda e: self.dismiss())
        for w in (self.frame, inner):
            w.bind("<Button-1>", lambda e: self.dismiss())
        self.y = 0.0
        self.target = 0.0
        self.alive = True
        self.fade = 1.0
        self._after = root.after(duration, self.dismiss)

    def dismiss(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.manager.root.after_cancel(self._after)
        except tk.TclError:
            pass
        self.frame.destroy()
        self.manager.relayout()


class ToastManager:
    MARGIN = 16
    GAP = 8
    MAX = 5

    def __init__(self, root: tk.Misc, enabled=lambda: True) -> None:
        self.root = root
        self.enabled = enabled
        self.toasts: List[Toast] = []
        self._animating = False
        self.bottom_offset = 34  # au-dessus de la barre d'état

    def show(self, message: str, kind: str = "info", duration: int = 3800) -> None:
        if not self.enabled() and kind not in ("error", "warning"):
            return
        toast = Toast(self, message, kind, duration)
        toast.y = -40  # entre par le bas
        self.toasts.append(toast)
        while len([t for t in self.toasts if t.alive]) > self.MAX:
            next(t for t in self.toasts if t.alive).dismiss()
        self.relayout()

    def relayout(self) -> None:
        self.toasts = [t for t in self.toasts if t.alive]
        y = 0.0
        for toast in reversed(self.toasts):
            toast.frame.update_idletasks()
            toast.target = y
            y += toast.frame.winfo_reqheight() + self.GAP
        if not self._animating:
            self._animating = True
            self._animate()

    def _animate(self) -> None:
        moving = False
        for toast in self.toasts:
            if not toast.alive:
                continue
            delta = toast.target - toast.y
            if abs(delta) > 0.5:
                toast.y += delta * 0.3
                moving = True
            else:
                toast.y = toast.target
            try:
                toast.frame.place(relx=1.0, rely=1.0, anchor="se", x=-self.MARGIN,
                                  y=-(self.bottom_offset + self.MARGIN + toast.y))
                toast.frame.lift()
            except tk.TclError:
                toast.alive = False
        if moving:
            self.root.after(16, self._animate)
        else:
            self._animating = False
