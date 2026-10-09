"""Exemple de plugin utilisateur : une horloge analogique.

Copiez ce fichier dans le dossier « plugins » de la configuration de PyClaude
Studio (voir Réglages › Données), puis redémarrez : le module apparaît
automatiquement dans la barre latérale, la palette de commandes et l'accueil.

Un plugin est une simple sous-classe de ``StudioModule`` :

* ``build(frame)``      construit l'interface (appelée une fois) ;
* ``self.loop(f, fps)`` crée une boucle d'animation mise en pause
                         automatiquement quand l'onglet est masqué ;
* ``on_theme(theme)``    redessine avec les couleurs du thème courant ;
* ``commands()``         expose des commandes à la palette (Ctrl+Maj+P) ;
* ``get_state`` / ``set_state`` persistent l'état entre deux sessions.
"""

import math
import time

from pyclaude_studio.core.commands import Command
from pyclaude_studio.core.module import StudioModule
from pyclaude_studio.ui.widgets import themed_canvas


class ClockModule(StudioModule):
    id = "horloge"
    title = "Horloge (plugin)"
    icon = "◷"
    category = "Plugins"
    description = "Exemple de plugin utilisateur : horloge analogique animée."
    order = 900

    def build(self, frame):
        self.smooth = True
        self.canvas = themed_canvas(frame, role="canvas")
        self.canvas.pack(fill="both", expand=True)
        self.loop(self.draw, fps=30).start()

    def draw(self, _dt=0.0):
        c, t = self.canvas, self.theme
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        cx, cy, r = w / 2, h / 2, min(w, h) * 0.38
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=t.border, width=3, fill=t.surface2)
        for i in range(60):
            a = math.tau * i / 60
            inner = r * (0.86 if i % 5 == 0 else 0.93)
            c.create_line(cx + inner * math.sin(a), cy - inner * math.cos(a),
                          cx + r * 0.97 * math.sin(a), cy - r * 0.97 * math.cos(a),
                          fill=t.fg if i % 5 == 0 else t.muted, width=3 if i % 5 == 0 else 1)
        now = time.localtime()
        frac = (time.time() % 1) if self.smooth else 0.0
        sec = now.tm_sec + frac
        minute = now.tm_min + sec / 60
        hour = now.tm_hour % 12 + minute / 60
        for value, length, width, color in ((hour / 12, 0.5, 7, t.fg), (minute / 60, 0.75, 4, t.fg),
                                            (sec / 60, 0.85, 2, t.accent)):
            a = math.tau * value
            c.create_line(cx, cy, cx + r * length * math.sin(a), cy - r * length * math.cos(a),
                          fill=color, width=width, capstyle="round")
        c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=t.accent, outline="")
        c.create_text(cx, cy + r * 0.45, text=time.strftime("%H:%M:%S"), fill=t.fg2,
                      font="StudioTitle")

    def commands(self):
        return [Command("horloge.smooth", "Trotteuse continue / saccadée",
                        lambda: setattr(self, "smooth", not self.smooth), icon="◷")]

    def get_state(self):
        return {"smooth": self.smooth}

    def set_state(self, state):
        self.smooth = bool(state.get("smooth", True))
