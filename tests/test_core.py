"""Tests du noyau : expressions, analyse numérique, images, recherche floue, bus, réglages."""

import math
import tempfile
import threading
import unittest
from pathlib import Path

from pyclaude_studio.core import expr, fuzzy, imaging, numeric
from pyclaude_studio.core.commands import shortcut_sequences
from pyclaude_studio.core.eventbus import EventBus
from pyclaude_studio.core.linalg import Quaternion, mat_vec, rotation_y
from pyclaude_studio.core.settings import Settings
from pyclaude_studio.core.tasks import TaskManager


class ExprParsingTest(unittest.TestCase):
    def ev(self, text, **vars_):
        return expr.evaluate(text, vars_, implicit_mul=True)

    def test_precedence_and_associativity(self):
        self.assertEqual(self.ev("2 + 3 * 4"), 14)
        self.assertEqual(self.ev("2 ^ 3 ^ 2"), 512)          # associativité à droite
        self.assertEqual(self.ev("-2 ^ 2"), -4)               # -(2^2)
        self.assertEqual(self.ev("(1 + 2) * (3 + 4)"), 21)
        self.assertEqual(self.ev("10 - 4 - 3"), 3)            # associativité à gauche
        self.assertEqual(self.ev("2 ** 10"), 1024)
        self.assertEqual(self.ev("5!"), 120)
        self.assertEqual(self.ev("3! ^ 2"), 36)

    def test_implicit_multiplication(self):
        self.assertAlmostEqual(self.ev("2x", x=3), 6)
        self.assertAlmostEqual(self.ev("2(x + 1)", x=3), 8)
        self.assertAlmostEqual(self.ev("(x + 1)(x - 1)", x=3), 8)
        self.assertAlmostEqual(self.ev("3sin(0) + 2pi"), 2 * math.pi)
        self.assertAlmostEqual(self.ev("x(x + 1)", x=2), 6)

    def test_functions_and_constants(self):
        self.assertAlmostEqual(self.ev("sin(pi / 2)"), 1)
        self.assertAlmostEqual(self.ev("log(1000)"), 3)
        self.assertAlmostEqual(self.ev("log(8, 2)"), 3)
        self.assertAlmostEqual(self.ev("ln(e)"), 1)
        self.assertAlmostEqual(self.ev("max(1, 7, 3)"), 7)
        self.assertAlmostEqual(self.ev("π"), math.pi)
        self.assertAlmostEqual(self.ev("hypot(3, 4)"), 5)

    def test_errors(self):
        for bad in ("2 +", "(1 + 2", "sin()", "1 2", "@", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(expr.ExprError):
                    expr.evaluate(bad)
        with self.assertRaises(expr.ExprError):
            expr.evaluate("1 / 0")
        with self.assertRaises(expr.ExprError):
            expr.evaluate("y + 1")

    def test_string_roundtrip(self):
        for text in ("x^2 + 3*x - 1", "-(x + 1)", "sin(x)^2", "x^(-1)", "(-x)^2", "2^3^4",
                     "a - (b - c)", "a/(b*c)", "x!"):
            node = expr.parse(text)
            again = expr.parse(str(node))
            self.assertEqual(node, again, msg=f"{text} -> {node}")


class ExprCompileDeriveTest(unittest.TestCase):
    def test_compile_matches_eval(self):
        f = expr.compile_expr("x^3 - 2x + sin(x) / (1 + x^2)")
        for x in (-2.0, -0.5, 0.0, 1.3, 4.0):
            ref = expr.evaluate("x^3 - 2*x + sin(x) / (1 + x^2)", {"x": x})
            self.assertAlmostEqual(f(x), ref)

    def test_compile_is_safe(self):
        with self.assertRaises(expr.ExprError):
            expr.compile_expr("__import__('os')")
        with self.assertRaises(expr.ExprError):
            expr.compile_expr('"texte"')
        f = expr.compile_expr("1 / x")
        self.assertTrue(math.isnan(f(0.0)))
        self.assertTrue(math.isnan(expr.compile_expr("sqrt(x)")(-1.0)))

    def test_odd_root_of_negative(self):
        self.assertAlmostEqual(expr.compile_expr("x^(1/3)")(-8.0), -2.0)

    def test_symbolic_derivatives(self):
        cases = {
            "x^3 + 2x": "3*x^2 + 2",
            "sin(x)": "cos(x)",
            "x * x": "2*x",
            "5": "0",
        }
        for src, expected in cases.items():
            node = expr.simplify(expr.derive(expr.parse(src, implicit_mul=True), "x"))
            self.assertEqual(str(node), expected, msg=src)

    def test_derivatives_numerically(self):
        for src in ("x^x", "ln(x) * exp(2x)", "sqrt(1 + x^2)", "tan(x) / x", "atan(x^2)",
                    "cosh(x) - sinh(x)", "log(x)", "abs(x - 0.2)", "x^2 / (1 + x)"):
            f = expr.compile_expr(src)
            df = f.derivative("x")
            for x in (0.7, 1.3, 2.1):
                num = (f(x + 1e-6) - f(x - 1e-6)) / 2e-6
                self.assertAlmostEqual(df(x), num, places=4, msg=f"{src} en {x}")

    def test_two_variables(self):
        g = expr.compile_expr("x*y + y^2", ("x", "y"))
        self.assertEqual(g(2.0, 3.0), 15.0)
        self.assertEqual(expr.free_vars(expr.parse("x*y + pi")), {"x", "y"})


class NumericTest(unittest.TestCase):
    def test_nice_ticks(self):
        step, ticks = numeric.nice_ticks(0, 97, 6)
        self.assertEqual(step, 20)
        self.assertEqual(ticks[0], 0)
        step, ticks = numeric.nice_ticks(-1.3, 2.7, 8)
        self.assertTrue(all(abs(t / step - round(t / step)) < 1e-9 for t in ticks))

    def test_roots_and_poles(self):
        roots = numeric.find_roots(math.sin, -7, 7)
        self.assertEqual(len(roots), 5)
        for r, k in zip(roots, (-2, -1, 0, 1, 2)):
            self.assertAlmostEqual(r, k * math.pi, places=8)
        # tan change de signe aux pôles : ce ne sont pas des racines
        tan_roots = numeric.find_roots(math.tan, -4, 4)
        self.assertEqual(len(tan_roots), 3)

    def test_integration_and_extrema(self):
        self.assertAlmostEqual(numeric.simpson(math.sin, 0, math.pi), 2.0, places=8)
        self.assertAlmostEqual(numeric.simpson(lambda x: x * x, 0, 3), 9.0, places=8)
        ext = numeric.find_extrema(lambda x: x ** 3 - 3 * x, -3, 3)
        kinds = sorted((round(x, 4), k) for x, _, k in ext)
        self.assertEqual(kinds, [(-1.0, "max"), (1.0, "min")])


class ImagingTest(unittest.TestCase):
    def test_png_roundtrip(self):
        w, h = 17, 9
        rgb = bytes((x * 15 + y) % 256 for y in range(h) for x in range(w) for _ in range(3))
        data = imaging.png(w, h, rgb)
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertEqual(imaging.decode_png_rgb(data), (w, h, rgb))

    def test_index_translation(self):
        lut = [(i, 255 - i, i // 2) for i in range(256)]
        tables = imaging.lut_to_tables(lut)
        out = imaging.index_to_rgb(bytes([0, 10, 255]), tables)
        self.assertEqual(out, bytes([0, 255, 0, 10, 245, 5, 255, 0, 127]))

    def test_upscale(self):
        w, h, data = imaging.upscale(2, 1, bytes([1, 2, 3, 4, 5, 6]), 2)
        self.assertEqual((w, h), (4, 2))
        self.assertEqual(data, bytes([1, 2, 3] * 2 + [4, 5, 6] * 2) * 2)


class FuzzyTest(unittest.TestCase):
    def test_subsequence_and_accents(self):
        self.assertIsNotNone(fuzzy.match("theme", "Thème : Nuit"))
        self.assertIsNone(fuzzy.match("xyz", "Fractales"))
        score, idx = fuzzy.match("frac", "Ouvrir « Fractales »")
        self.assertEqual(len(idx), 4)

    def test_ranking_prefers_word_starts(self):
        items = ["Fermer l'onglet", "Afficher le journal", "Fractales"]
        ranked = fuzzy.rank("fr", items)
        self.assertEqual(ranked[0][1], "Fractales")


class EventBusTest(unittest.TestCase):
    def test_glob_priority_once(self):
        bus = EventBus()
        seen = []
        bus.subscribe("task.*", lambda e: seen.append(("glob", e.topic)))
        bus.subscribe("task.done", lambda e: seen.append(("first", e.topic)), priority=10)
        bus.subscribe("task.done", lambda e: seen.append(("once", e.topic)), once=True)
        bus.publish("task.done")
        bus.publish("task.done")
        bus.publish("other")
        self.assertEqual(seen[0], ("first", "task.done"))
        self.assertEqual(sum(1 for s in seen if s[0] == "once"), 1)
        self.assertEqual(bus.counts["task.done"], 2)

    def test_errors_are_isolated_and_threads_queue(self):
        bus = EventBus()
        got = []
        bus.subscribe("x", lambda e: 1 / 0)
        bus.subscribe("x", lambda e: got.append(e["v"]))
        t = threading.Thread(target=lambda: bus.publish("x", v=42))
        t.start()
        t.join()
        self.assertEqual(got, [])      # mis en file depuis un autre thread
        self.assertEqual(bus.pump(), 1)
        self.assertEqual(got, [42])


class SettingsTest(unittest.TestCase):
    def test_persistence_and_observers(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            s = Settings(path)
            changes = []
            s.observe("ui.", lambda k, v: changes.append((k, v)))
            s.set("ui.theme", "Clair")
            s.set_module_state("fractal", {"zoom": 3})
            self.assertTrue(s.save())
            s2 = Settings.load(path)
            self.assertEqual(s2.get("ui.theme"), "Clair")
            self.assertEqual(s2.module_state("fractal"), {"zoom": 3})
            self.assertEqual(s2.get("inconnu", 5), 5)
            self.assertEqual(changes, [("ui.theme", "Clair")])

    def test_corrupted_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            path.write_text("{ pas du json", encoding="utf-8")
            s = Settings.load(path)
            self.assertEqual(s.get("ui.theme"), "Nuit")


class TasksTest(unittest.TestCase):
    def test_progress_partial_cancel(self):
        tm = TaskManager()
        results, partials = [], []

        def job(ctx, n):
            for i in range(n):
                ctx.partial(i)
            return n * 2

        t = tm.submit("x", job, 3, on_done=results.append, on_partial=partials.append)

        def slow(ctx):
            while True:
                ctx.check()

        t2 = tm.submit("slow", slow)
        t2.cancel()
        import time
        deadline = time.time() + 5
        while (t.active or t2.active) and time.time() < deadline:
            tm.pump()
            time.sleep(0.01)
        tm.pump()
        self.assertEqual(results, [6])
        self.assertEqual(partials, [0, 1, 2])
        self.assertEqual(t2.status, "annulée")
        tm.shutdown()


class MiscTest(unittest.TestCase):
    def test_shortcuts(self):
        self.assertEqual(shortcut_sequences("Ctrl+Shift+P"), ["<Control-Shift-P>"])
        self.assertEqual(shortcut_sequences("Ctrl+1"), ["<Control-Key-1>"])
        self.assertIn("<Control-plus>", shortcut_sequences("Ctrl+="))
        self.assertIn("<Control-ISO_Left_Tab>", shortcut_sequences("Ctrl+Shift+Tab", "x11"))
        self.assertEqual(shortcut_sequences("F11"), ["<F11>"])

    def test_quaternion_rotation(self):
        q = Quaternion.from_axis_angle((0, 1, 0), math.pi / 2)
        v = q.rotate((1, 0, 0))
        ref = mat_vec(rotation_y(math.pi / 2), (1, 0, 0))
        for a, b in zip(v, ref):
            self.assertAlmostEqual(a, b)
        q2 = Quaternion.between((1, 0, 0), (0, 0, 1))
        for a, b in zip(q2.rotate((1, 0, 0)), (0, 0, 1)):
            self.assertAlmostEqual(a, b)


if __name__ == "__main__":
    unittest.main()
