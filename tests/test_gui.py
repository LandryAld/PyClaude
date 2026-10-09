"""Test de fumée de l'interface : ouvre chaque module, exécute sa démo, change de thème.

Ignoré automatiquement sans écran (serveur CI sans X) ou sans Tkinter.
"""

import importlib.util
import logging
import os
import sys
import tempfile
import unittest

HAS_TK = importlib.util.find_spec("tkinter") is not None
HAS_DISPLAY = sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "Tkinter ou affichage indisponible")
class GuiSmokeTest(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        os.environ["PYCLAUDE_STUDIO_HOME"] = self._home.name
        from pyclaude_studio.app import StudioApp
        from pyclaude_studio.core.settings import Settings

        self.errors = []
        test = self

        class Collector(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    test.errors.append(self.format(record))

        self.collector = Collector()
        logging.getLogger().addHandler(self.collector)
        self.app = StudioApp(splash=False, restore=False, plugins=False,
                             settings=Settings.load())

    def tearDown(self):
        logging.getLogger().removeHandler(self.collector)
        try:
            self.app.quit()
        except Exception:
            pass
        self._home.cleanup()

    def pump(self, ms):
        import time

        end = time.time() + ms / 1000
        while time.time() < end:
            self.app.root.dooneevent(2)
        self.app.root.update_idletasks()

    def test_every_module_opens_and_runs(self):
        app = self.app
        ids = [spec.id for spec in app.registry.ordered()]
        self.assertGreaterEqual(len(ids), 15)
        self.assertFalse(app.registry.failures)
        for mid in ids:
            inst = app.open_module(mid)
            self.assertIsNotNone(inst, mid)
            self.pump(150)
            if hasattr(inst, "demo"):
                inst.demo()
            self.pump(150)
            state = inst.get_state()
            inst.set_state(state)  # l'état doit pouvoir être relu
        for name in ("Clair", "Néon", "Nuit"):
            app.set_theme(name)
            self.pump(100)
        for mid in list(app.instances):
            app.close_module(mid)
        self.pump(50)
        self.assertEqual(self.errors, [])

    def test_command_palette_and_registry(self):
        app = self.app
        app.palette.open()
        app.palette.var.set("fractales")
        self.pump(50)
        self.assertTrue(app.palette.results)
        app.palette._run()
        self.pump(200)
        self.assertEqual(app.active_id, "fractal")
        self.assertIn("app.palette", {c.id for c in app.commands.all()})
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
