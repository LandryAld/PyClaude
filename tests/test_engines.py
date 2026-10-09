"""Tests des moteurs de calcul (sans interface graphique)."""

import math
import random
import unittest

from pyclaude_studio.engines import (fourier, fractal, life, lsystem, mesh, neural, pathfinding,
                                     physics, sorting, spreadsheet)


class FractalTest(unittest.TestCase):
    def test_engines_agree(self):
        for kind in fractal.KINDS:
            p = fractal.default_for(kind)
            a = fractal.render(p, 64, 40, engine="thread")
            self.assertEqual(len(a), 64 * 40)
            if fractal.numpy_available():
                b = fractal.render(p, 64, 40, engine="numpy")
                diff = sum(1 for x, y in zip(a, b) if x != y)
                self.assertLess(diff, 64 * 40 * 0.01, msg=kind)

    def test_mandelbrot_known_points(self):
        p = fractal.FractalParams(cx=0.0, cy=0.0, span=4.0, max_iter=100)
        img = fractal.render(p, 5, 5, engine="thread")
        self.assertEqual(img[2 * 5 + 2], fractal.INSIDE)  # c = 0 : intérieur
        self.assertNotEqual(img[0], fractal.INSIDE)       # c = -2+2i : extérieur

    def test_view_math(self):
        p = fractal.FractalParams()
        z = p.zoomed(0.5, 100, 50, 400, 300)
        self.assertAlmostEqual(z.span, p.span / 2)
        a = p.to_complex(100, 50, 400, 300)
        b = z.to_complex(100, 50, 400, 300)
        self.assertAlmostEqual(a[0], b[0])
        self.assertAlmostEqual(a[1], b[1])

    def test_cancel(self):
        out = fractal.render(fractal.FractalParams(), 50, 50, engine="thread",
                             cancelled=lambda: True)
        self.assertIsNone(out)

    def test_lut(self):
        lut = fractal.build_lut(fractal.FractalParams(), "Feu")
        self.assertEqual(len(lut), 256)
        self.assertEqual(lut[255], (0, 0, 0))


class LifeTest(unittest.TestCase):
    def periodic(self, name):
        cells, _ = life.parse_rle(life.PATTERNS[name])
        g = life.LifeGrid(120, 120, wrap=False)
        g.place(cells, 60, 60)
        start, bb = g.normalized(), g.bounding_box()
        for i in range(1, 40):
            g.step()
            if g.normalized() == start:
                nb = g.bounding_box()
                return i, (nb[0] - bb[0], nb[1] - bb[1])
        return None

    def test_oscillators_and_ships(self):
        self.assertEqual(self.periodic("Planeur"), (4, (1, 1)))
        self.assertEqual(self.periodic("Vaisseau léger (LWSS)")[0], 4)
        self.assertEqual(self.periodic("Pulsar"), (3, (0, 0)))
        self.assertEqual(self.periodic("Pentadécathlon"), (15, (0, 0)))
        self.assertEqual(self.periodic("Galaxie de Kok"), (8, (0, 0)))

    def test_gun_emits_gliders(self):
        cells, _ = life.parse_rle(life.PATTERNS["Canon à planeurs de Gosper"])
        g = life.LifeGrid(200, 200, wrap=False)
        g.place(cells, 40, 40)
        p0 = g.population
        g.run(120)
        self.assertGreater(g.population, p0 + 15)

    def test_rle_roundtrip_and_rules(self):
        cells, _ = life.parse_rle(life.PATTERNS["Canon à planeurs de Gosper"])
        again, rule = life.parse_rle(life.to_rle(cells))
        self.assertEqual(cells, again)
        self.assertEqual(rule, "B3/S23")
        r = life.Rule.parse("B36/S23")
        self.assertEqual((r.birth, r.survive), ({3, 6}, {2, 3}))
        self.assertEqual(life.Rule.parse("B2/S/C3").states, 3)
        with self.assertRaises(ValueError):
            life.Rule.parse("n'importe quoi")

    def test_generations_rule(self):
        g = life.LifeGrid(40, 40, rule="B2/S/C3", wrap=False)
        g.set(10, 10)
        g.set(11, 10)
        g.step()
        self.assertEqual(set(g.dying), {(10, 10), (11, 10)})
        self.assertTrue(g.population > 0)

    def test_wraparound(self):
        g = life.LifeGrid(10, 10, wrap=True)
        g.place(life.parse_rle(life.PATTERNS["Planeur"])[0], 0, 0, center=False)
        g.run(40)  # 40 générations = 10 cellules : retour au point de départ
        self.assertEqual(g.population, 5)


class PathfindingTest(unittest.TestCase):
    def grid(self, seed=1, diagonal=False):
        g = pathfinding.Grid(41, 31, diagonal=diagonal, start=(1, 1), goal=(39, 29))
        for _ in pathfinding.maze_braid(g, random.Random(seed)):
            pass
        return g

    def test_all_algorithms_find_valid_paths(self):
        g = self.grid()
        best = None
        for name in pathfinding.ALGORITHMS:
            path, explored = pathfinding.solve(g, name)
            self.assertIsNotNone(path, msg=name)
            self.assertEqual(path[0], g.start)
            self.assertEqual(path[-1], g.goal)
            for a, b in zip(path, path[1:]):
                self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)
                self.assertNotIn(b, g.walls)
            if name == "Dijkstra":
                best = pathfinding.path_cost(g, path)
        astar, _ = pathfinding.solve(g, "A*")
        self.assertAlmostEqual(pathfinding.path_cost(g, astar), best)

    def test_weights_and_diagonals(self):
        g = pathfinding.Grid(30, 20, diagonal=True, start=(0, 0), goal=(29, 19))
        for _ in pathfinding.maze_scatter(g, random.Random(3), density=0.2):
            pass
        g.walls.discard(g.start)
        g.walls.discard(g.goal)
        d, _ = pathfinding.solve(g, "Dijkstra")
        a, explored_a = pathfinding.solve(g, "A*", heuristic="Octile")
        if d is None:
            self.assertIsNone(a)
        else:
            self.assertAlmostEqual(pathfinding.path_cost(g, d), pathfinding.path_cost(g, a))

    def test_unreachable(self):
        g = pathfinding.Grid(10, 10, start=(0, 0), goal=(9, 9))
        g.walls = {(5, y) for y in range(10)}
        for name in pathfinding.ALGORITHMS:
            self.assertIsNone(pathfinding.solve(g, name)[0])

    def test_perfect_maze_is_tree(self):
        g = pathfinding.Grid(31, 21)
        for gen in (pathfinding.maze_backtracker, pathfinding.maze_prim, pathfinding.maze_kruskal):
            for _ in gen(g, random.Random(5)):
                pass
            rooms = [(x, y) for y in range(1, 20, 2) for x in range(1, 30, 2)]
            open_cells = {(x, y) for x in range(31) for y in range(21)} - g.walls
            edges = sum(1 for (x, y) in open_cells for n in ((x + 1, y), (x, y + 1))
                        if n in open_cells)
            # Un arbre couvrant : |arêtes| = |sommets| - 1
            self.assertEqual(edges, len(open_cells) - 1, msg=gen.__name__)
            self.assertTrue(all(r in open_cells for r in rooms))


class SortingTest(unittest.TestCase):
    def test_all_algorithms_sort(self):
        rng = random.Random(0)
        for name, algo in sorting.ALGORITHMS.items():
            for n in (0, 1, 2, 7, 33, 64):
                data = [rng.randint(1, 50) for _ in range(n)]
                arr = list(data)
                for op in algo(arr):
                    self.assertIn(op[0], ("cmp", "swap", "set", "pivot", "done"))
                self.assertEqual(arr, sorted(data), msg=f"{name} n={n}")

    def test_distributions(self):
        for kind in sorting.DISTRIBUTIONS:
            self.assertEqual(len(sorting.make_data(50, kind)), 50)


class SpreadsheetTest(unittest.TestCase):
    def setUp(self):
        self.s = spreadsheet.Sheet()
        self.s.load_dict(spreadsheet.DEMO)

    def test_demo_values(self):
        s = self.s
        self.assertAlmostEqual(s["D8"], 1148.2)
        self.assertAlmostEqual(s["D10"], 1377.84)
        self.assertEqual(s["E2"], "oui")
        self.assertEqual(s["D14"], 3)
        self.assertEqual(s["D16"], 229)

    def test_propagation(self):
        s = self.s
        changed = s.set(spreadsheet.parse_ref("B2"), "10")
        self.assertIn(spreadsheet.parse_ref("D10"), changed)
        self.assertAlmostEqual(s["D2"], 499.0)
        self.assertEqual(s["E2"], "oui")

    def test_errors_and_cycles(self):
        s = self.s
        s["Z1"] = "=Z2"
        s["Z2"] = "=Z1"
        s["Z3"] = "=Z1*2"
        self.assertEqual((s["Z1"], s["Z2"], s["Z3"]), ("#CYCLE!",) * 3)
        s["Z2"] = "4"
        self.assertEqual((s["Z1"], s["Z3"]), (4, 8))
        s["Y1"] = "=1/0"
        s["Y2"] = "=Y1+1"
        s["Y3"] = "=SIERREUR(Y1;0)"
        s["Y4"] = "=INCONNUE(1)"
        s["Y5"] = '="a"*2'
        s["Y6"] = "=SOMME(A1:"
        self.assertEqual((s["Y1"], s["Y2"], s["Y3"], s["Y4"], s["Y5"], s["Y6"]),
                         ("#DIV/0!", "#DIV/0!", 0, "#NOM?", "#VALEUR!", "#ERREUR!"))

    def test_shift_and_paste(self):
        self.assertEqual(spreadsheet.shift_formula("=A1+$B$2+C$3", 2, 1), "=B3+$B$2+D$3")
        self.assertEqual(spreadsheet.shift_formula('="A1"&A1', 1, 0), '="A1"&A2')
        block = self.s.copy_block(1, 3, 2, 3)  # D2:D3
        changes = self.s.paste_block(block, 1, 5, src=(1, 3))
        self.assertEqual(changes[(1, 5)], "=D2*E2")

    def test_csv_json_roundtrip(self):
        s2 = spreadsheet.Sheet()
        s2.load_csv(self.s.to_csv())
        self.assertAlmostEqual(s2["D10"], self.s["D10"])
        s3 = spreadsheet.Sheet()
        s3.load_json(self.s.to_json())
        self.assertEqual(s3.to_dict(), self.s.to_dict())

    def test_names(self):
        self.assertEqual(spreadsheet.col_name(0), "A")
        self.assertEqual(spreadsheet.col_name(27), "AB")
        self.assertEqual(spreadsheet.col_index("AB"), 27)
        self.assertEqual(spreadsheet.parse_number("12,5%"), 0.125)


class PhysicsTest(unittest.TestCase):
    def test_barnes_hut_matches_direct(self):
        w = physics.GravityWorld()
        w.preset("Nuage primordial", 800, 600, seed=4)
        w.theta = 0.3
        tree = physics.build_tree(w.bodies)
        soft2 = w.softening ** 2
        errs = []
        for b in w.bodies[:40]:
            ax, ay, _ = physics.tree_acceleration(tree, b, w.theta, w.G, soft2)
            dx, dy = physics.direct_acceleration(w.bodies, b, w.G, soft2)
            errs.append(math.hypot(ax - dx, ay - dy) / (math.hypot(dx, dy) + 1e-9))
        self.assertLess(sum(errs) / len(errs), 0.02)

    def test_momentum_conservation(self):
        w = physics.GravityWorld()
        w.preset("Collision de galaxies", 1000, 700)
        w.use_tree = False
        p0 = w.momentum()
        for _ in range(20):
            w.step(0.01)
        p1 = w.momentum()
        scale = sum(abs(b.m * b.vx) + abs(b.m * b.vy) for b in w.bodies)
        self.assertLess(abs(p1[0] - p0[0]) + abs(p1[1] - p0[1]), 1e-6 * scale)

    def test_cloth_tears_and_cuts(self):
        c = physics.ClothWorld()
        c.make_cloth(10, 8)
        n0 = len(c.sticks)
        for _ in range(30):
            c.step(1 / 60)
        self.assertEqual(len(c.sticks), n0)
        cut = c.cut(c.points[45].x, c.points[45].y, 12)
        self.assertGreater(cut, 0)

    def test_balls_stay_in_bounds(self):
        b = physics.BallWorld(400, 300)
        b.fill(60)
        for _ in range(60):
            b.step(1 / 60)
        for ball in b.balls:
            self.assertTrue(-1 <= ball.x <= 401 and -1 <= ball.y <= 301)


class MeshTest(unittest.TestCase):
    def signed_volume(self, m):
        v = 0.0
        for f in m.faces:
            p = [m.vertices[i] for i in f]
            for k in range(1, len(p) - 1):
                a, b, c = p[0], p[k], p[k + 1]
                v += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                      + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
        return v

    def test_closed_meshes_outward(self):
        for name, factory in mesh.PRIMITIVES.items():
            m = factory()
            if m.closed:
                self.assertGreater(self.signed_volume(m), 0, msg=name)
        self.assertAlmostEqual(self.signed_volume(mesh.cube()), 8.0)

    def test_render_and_obj(self):
        m = mesh.torus()
        from pyclaude_studio.core.linalg import IDENTITY
        faces, culled = mesh.render(m, IDENTITY, mesh.RenderSettings())
        self.assertEqual(len(faces) + culled, len(m.faces))
        depths = [f.depth for f in faces]
        self.assertEqual(depths, sorted(depths, reverse=True))
        again = mesh.parse_obj(mesh.to_obj(m))
        self.assertEqual(len(again.faces), len(m.faces))
        s = mesh.surface("sin(x) * cos(y)", nx=10)
        self.assertEqual(len(s.faces), 100)


class NeuralTest(unittest.TestCase):
    def test_gradient_check(self):
        net = neural.MLP([3, 5, 4, 1], "tanh", seed=1)
        self.assertLess(net.numeric_gradient_check([0.3, -0.7, 0.2], 1), 1e-4)
        net2 = neural.MLP([2, 4, 1], "sigmoïde", seed=2)
        self.assertLess(net2.numeric_gradient_check([0.5, 0.1], 0), 1e-4)

    def test_learns_xor(self):
        data = neural.make_dataset("XOR", 120, noise=0.0, seed=1)
        X = [[x, y] for x, y, _ in data]
        Y = [l for _, _, l in data]
        net = neural.MLP([2, 8, 1], "tanh", seed=3)
        for epoch in range(150):
            net.train_batch(X, Y, 0.05, "Adam")
        self.assertGreater(net.accuracy(X, Y), 0.95)

    def test_datasets(self):
        for kind in neural.DATASETS:
            d = neural.make_dataset(kind, 50, seed=0)
            self.assertEqual(len(d), 50)
            self.assertTrue({l for _, _, l in d} <= {0, 1})


class FourierTest(unittest.TestCase):
    def test_fft_matches_dft(self):
        rng = random.Random(0)
        x = [complex(rng.random(), rng.random()) for _ in range(64)]
        for a, b in zip(fourier.fft(x), fourier.dft(x)):
            self.assertAlmostEqual(abs(a - b), 0, places=9)
        back = fourier.ifft(fourier.fft(x))
        for a, b in zip(back, x):
            self.assertAlmostEqual(abs(a - b), 0, places=9)

    def test_full_reconstruction(self):
        pts = fourier.shape("Cœur", 128)
        cycles = fourier.epicycles(pts)
        self.assertLess(fourier.reconstruction_error(pts, cycles, len(cycles)), 1e-9)
        self.assertGreater(fourier.reconstruction_error(pts, cycles, 3), 1e-3)

    def test_resample_spacing(self):
        pts = fourier.resample([(0, 0), (10, 0), (10, 10), (0, 10)], 40)
        gaps = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        self.assertLess(max(gaps) - min(gaps), 1e-9)


class LSystemTest(unittest.TestCase):
    def test_expansion(self):
        koch = next(p for p in lsystem.PRESETS if p.name == "Flocon de Koch")
        s = koch.expand(2)
        self.assertEqual(s.count("F"), 3 * 4 ** 2)
        segs = koch.interpret(s, 1.0)
        self.assertEqual(len(segs), 48)
        x0, y0, x1, y1 = lsystem.bounds(segs)
        self.assertGreater(x1 - x0, 0)

    def test_stochastic_is_seeded(self):
        tree = next(p for p in lsystem.PRESETS if p.name == "Arbre stochastique")
        self.assertEqual(tree.expand(4, seed=1), tree.expand(4, seed=1))
        self.assertNotEqual(tree.expand(4, seed=1), tree.expand(4, seed=2))

    def test_limit(self):
        dragon = next(p for p in lsystem.PRESETS if p.name == "Courbe du dragon")
        with self.assertRaises(ValueError):
            dragon.expand(30, limit=10_000)


if __name__ == "__main__":
    unittest.main()
