"""Maillages 3D : primitives, surfaces paramétriques, surfaces définies par
l'utilisateur (``z = f(x, y)``), import OBJ, et pipeline de rendu logiciel
(transformation, élimination des faces arrière, ombrage de Blinn-Phong,
algorithme du peintre)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..core.colors import gradient, hex_to_rgb
from ..core.expr import compile_expr
from ..core.linalg import Mat3, Vec3, bounding_box, normalize, polygon_normal

Face = Tuple[int, ...]


@dataclass
class Mesh:
    name: str
    vertices: List[Vec3]
    faces: List[Face]
    closed: bool = True
    face_colors: Optional[List[Tuple[int, int, int]]] = None
    normals: List[Vec3] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.normals:
            self.compute_normals()

    def compute_normals(self) -> None:
        v = self.vertices
        self.normals = [polygon_normal([v[i] for i in f]) for f in self.faces]

    def normalize_size(self, target: float = 2.0) -> "Mesh":
        lo, hi = bounding_box(self.vertices)
        c = tuple((lo[i] + hi[i]) / 2 for i in range(3))
        size = max(hi[i] - lo[i] for i in range(3)) or 1.0
        k = target / size
        self.vertices = [((x - c[0]) * k, (y - c[1]) * k, (z - c[2]) * k)
                         for x, y, z in self.vertices]
        self.compute_normals()
        return self

    def color_by_height(self, colormap: Sequence[Tuple[float, str]]) -> "Mesh":
        lut = gradient(colormap, 64)
        ys = [sum(self.vertices[i][1] for i in f) / len(f) for f in self.faces]
        lo, hi = min(ys), max(ys)
        span = (hi - lo) or 1.0
        self.face_colors = [lut[min(63, int((y - lo) / span * 63))] for y in ys]
        return self

    @property
    def stats(self) -> str:
        return f"{len(self.vertices)} sommets · {len(self.faces)} faces"


# -- primitives -----------------------------------------------------------------

def cube() -> Mesh:
    v = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return Mesh("Cube", [tuple(map(float, p)) for p in v], faces)  # type: ignore[misc]


def uv_sphere(seg: int = 32, rings: int = 16) -> Mesh:
    verts: List[Vec3] = [(0.0, 1.0, 0.0)]
    for r in range(1, rings):
        phi = math.pi * r / rings
        for s in range(seg):
            th = math.tau * s / seg
            verts.append((math.sin(phi) * math.cos(th), math.cos(phi), math.sin(phi) * math.sin(th)))
    verts.append((0.0, -1.0, 0.0))
    faces: List[Face] = []
    for s in range(seg):
        faces.append((0, 1 + (s + 1) % seg, 1 + s))
    for r in range(rings - 2):
        for s in range(seg):
            a = 1 + r * seg + s
            b = 1 + r * seg + (s + 1) % seg
            faces.append((a, b, b + seg, a + seg))
    last = len(verts) - 1
    base = 1 + (rings - 2) * seg
    for s in range(seg):
        faces.append((last, base + s, base + (s + 1) % seg))
    return Mesh("Sphère UV", verts, faces)


def icosphere(subdivisions: int = 2) -> Mesh:
    t = (1 + math.sqrt(5)) / 2
    verts = [normalize(p) for p in [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
                                    (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
                                    (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
             (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
             (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(subdivisions):
        cache: Dict[Tuple[int, int], int] = {}

        def mid(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key not in cache:
                pa, pb = verts[a], verts[b]
                verts.append(normalize(((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2,
                                        (pa[2] + pb[2]) / 2)))
                cache[key] = len(verts) - 1
            return cache[key]

        new = []
        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            new += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = new
    # orientation sortante (normales vers l'extérieur)
    fixed = []
    for f in faces:
        n = polygon_normal([verts[i] for i in f])
        c = [sum(verts[i][k] for i in f) for k in range(3)]
        fixed.append(f if n[0] * c[0] + n[1] * c[1] + n[2] * c[2] >= 0 else f[::-1])
    return Mesh("Icosphère", verts, fixed)


def parametric(name: str, fn: Callable[[float, float], Vec3], nu: int, nv: int,
               u_range: Tuple[float, float], v_range: Tuple[float, float],
               wrap_u: bool = False, wrap_v: bool = False, closed: bool = False) -> Mesh:
    verts: List[Vec3] = []
    cu = nu if wrap_u else nu + 1
    cv = nv if wrap_v else nv + 1
    for i in range(cu):
        u = u_range[0] + (u_range[1] - u_range[0]) * i / nu
        for j in range(cv):
            v = v_range[0] + (v_range[1] - v_range[0]) * j / nv
            verts.append(fn(u, v))
    faces: List[Face] = []
    for i in range(nu):
        for j in range(nv):
            i2 = (i + 1) % cu if wrap_u else i + 1
            j2 = (j + 1) % cv if wrap_v else j + 1
            faces.append((i * cv + j, i * cv + j2, i2 * cv + j2, i2 * cv + j))
    return Mesh(name, verts, faces, closed=closed)


def torus(R: float = 1.0, r: float = 0.38, nu: int = 44, nv: int = 20) -> Mesh:
    def f(u: float, v: float) -> Vec3:
        return ((R + r * math.cos(v)) * math.cos(u), r * math.sin(v),
                (R + r * math.cos(v)) * math.sin(u))

    return parametric("Tore", f, nu, nv, (0, math.tau), (0, math.tau), True, True, closed=True)


def mobius(nu: int = 60, nv: int = 8) -> Mesh:
    def f(u: float, v: float) -> Vec3:
        return ((1 + v / 2 * math.cos(u / 2)) * math.cos(u), v / 2 * math.sin(u / 2),
                (1 + v / 2 * math.cos(u / 2)) * math.sin(u))

    return parametric("Ruban de Möbius", f, nu, nv, (0, math.tau), (-1, 1))


def klein(nu: int = 56, nv: int = 24) -> Mesh:
    """Bouteille de Klein (immersion « en huit »)."""
    a = 2.0

    def f(u: float, v: float) -> Vec3:
        cu2, su2 = math.cos(u / 2), math.sin(u / 2)
        r = a + cu2 * math.sin(v) - su2 * math.sin(2 * v)
        return (r * math.cos(u), su2 * math.sin(v) + cu2 * math.sin(2 * v), r * math.sin(u))

    return parametric("Bouteille de Klein", f, nu, nv, (0, math.tau), (0, math.tau), True, True)


def trefoil(nu: int = 120, nv: int = 12, radius: float = 0.32) -> Mesh:
    """Tube autour d'un nœud de trèfle (repère de Frenet discret)."""
    def curve(t: float) -> Vec3:
        return (math.sin(t) + 2 * math.sin(2 * t), math.cos(t) - 2 * math.cos(2 * t),
                -math.sin(3 * t))

    def f(u: float, v: float) -> Vec3:
        p = curve(u)
        e = 1e-3
        p2 = curve(u + e)
        tangent = normalize((p2[0] - p[0], p2[1] - p[1], p2[2] - p[2]))
        ref = (0.0, 0.0, 1.0) if abs(tangent[2]) < 0.9 else (1.0, 0.0, 0.0)
        n1 = normalize((tangent[1] * ref[2] - tangent[2] * ref[1],
                        tangent[2] * ref[0] - tangent[0] * ref[2],
                        tangent[0] * ref[1] - tangent[1] * ref[0]))
        n2 = (tangent[1] * n1[2] - tangent[2] * n1[1], tangent[2] * n1[0] - tangent[0] * n1[2],
              tangent[0] * n1[1] - tangent[1] * n1[0])
        c, s = math.cos(v) * radius, math.sin(v) * radius
        return (p[0] + n1[0] * c + n2[0] * s, p[1] + n1[1] * c + n2[1] * s,
                p[2] + n1[2] * c + n2[2] * s)

    return parametric("Nœud de trèfle", f, nu, nv, (0, math.tau), (0, math.tau), True, True,
                      closed=True)


def seashell(nu: int = 80, nv: int = 18) -> Mesh:
    def f(u: float, v: float) -> Vec3:
        k = math.exp(u / (6 * math.pi))
        r = k * (1 + math.cos(v))
        return (r * math.cos(u) * 0.5, (k * math.sin(v) - 2.6 * k) * 0.5 + 1.5, r * math.sin(u) * 0.5)

    return parametric("Coquillage", f, nu, nv, (0, 6 * math.pi), (0, math.tau), False, True)


def surface(expression: str, nx: int = 36, extent: float = 3.0) -> Mesh:
    """Surface ``y = f(x, z)`` saisie par l'utilisateur (axe vertical = y)."""
    fn = compile_expr(expression, ("x", "y"))
    verts: List[Vec3] = []
    for i in range(nx + 1):
        x = -extent + 2 * extent * i / nx
        for j in range(nx + 1):
            z = -extent + 2 * extent * j / nx
            h = fn(x, z)
            if not math.isfinite(h):
                h = 0.0
            verts.append((x, max(-8.0, min(8.0, h)), z))
    faces = [(i * (nx + 1) + j, i * (nx + 1) + j + 1, (i + 1) * (nx + 1) + j + 1,
              (i + 1) * (nx + 1) + j) for i in range(nx) for j in range(nx)]
    return Mesh(f"z = {expression}", verts, faces, closed=False)


def parse_obj(text: str, name: str = "OBJ") -> Mesh:
    verts: List[Vec3] = []
    faces: List[Face] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 4:
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == "f" and len(parts) >= 4:
            idx = []
            for p in parts[1:]:
                i = int(p.split("/")[0])
                idx.append(i - 1 if i > 0 else len(verts) + i)
            faces.append(tuple(idx))
    if not verts or not faces:
        raise ValueError("fichier OBJ vide ou invalide")
    return Mesh(name, verts, faces, closed=False).normalize_size()


def to_obj(mesh: Mesh) -> str:
    lines = [f"# {mesh.name} — exporté par PyClaude Studio"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in mesh.vertices]
    lines += ["f " + " ".join(str(i + 1) for i in f) for f in mesh.faces]
    return "\n".join(lines) + "\n"


PRIMITIVES: Dict[str, Callable[[], Mesh]] = {
    "Tore": torus,
    "Nœud de trèfle": trefoil,
    "Bouteille de Klein": klein,
    "Icosphère": lambda: icosphere(2),
    "Sphère UV": uv_sphere,
    "Ruban de Möbius": mobius,
    "Coquillage": seashell,
    "Cube": cube,
}

SURFACES = ["sin(sqrt(x^2 + y^2) * 2) / (1 + 0.2*(x^2 + y^2)) * 2",
            "cos(x) * sin(y)",
            "0.3 * (x^2 - y^2)",
            "exp(-(x^2 + y^2) / 2) * 2.5",
            "sin(x * y)",
            "abs(x) - abs(y)"]


# -- pipeline de rendu ------------------------------------------------------------

@dataclass
class RenderSettings:
    width: int = 800
    height: int = 600
    distance: float = 5.0
    fov: float = 60.0
    cull: bool = True
    light: Vec3 = (0.45, 0.65, 0.6)
    ambient: float = 0.18
    specular: float = 0.35
    shininess: float = 24.0
    base_color: Tuple[int, int, int] = (124, 108, 255)
    background: Tuple[int, int, int] = (20, 20, 23)
    fog: float = 0.0
    toon: int = 0  # nombre de niveaux (0 = désactivé)


@dataclass
class ProjectedFace:
    depth: float
    points: List[float]
    color: Tuple[int, int, int]
    index: int


def render(mesh: Mesh, rotation: Mat3, s: RenderSettings) -> Tuple[List[ProjectedFace], int]:
    """Transforme, éclaire et trie les faces ; renvoie (faces dessinables, faces éliminées)."""
    m = rotation
    m00, m01, m02 = m[0]
    m10, m11, m12 = m[1]
    m20, m21, m22 = m[2]
    focal = (s.height / 2) / math.tan(math.radians(s.fov) / 2)
    cx, cy = s.width / 2, s.height / 2
    dist = s.distance
    # Sommets dans le repère caméra (caméra en z = -dist regardant +z)
    cam: List[Vec3] = []
    proj: List[Tuple[float, float]] = []
    for x, y, z in mesh.vertices:
        X = m00 * x + m01 * y + m02 * z
        Y = m10 * x + m11 * y + m12 * z
        Z = m20 * x + m21 * y + m22 * z + dist
        cam.append((X, Y, Z))
        zz = Z if Z > 0.05 else 0.05
        proj.append((cx + X * focal / zz, cy - Y * focal / zz))
    # Repère caméra : x à droite, y en haut, z vers l'avant (on regarde selon +z).
    # La lumière est donnée « vers le spectateur » positif : on inverse z.
    lx, ly, lz = normalize((s.light[0], s.light[1], -s.light[2]))
    # Vecteur demi-angle de Blinn entre la lumière et la direction de vue (0, 0, -1)
    hx, hy, hz = normalize((lx, ly, lz - 1.0))
    out: List[ProjectedFace] = []
    culled = 0
    colors = mesh.face_colors
    base = s.base_color
    bg = s.background
    max_depth = dist + 2.5
    for fi, face in enumerate(mesh.faces):
        nx0, ny0, nz0 = mesh.normals[fi]
        nx = m00 * nx0 + m01 * ny0 + m02 * nz0
        ny = m10 * nx0 + m11 * ny0 + m12 * nz0
        nz = m20 * nx0 + m21 * ny0 + m22 * nz0
        p0 = cam[face[0]]
        # Face orientée vers la caméra si n · (p - caméra) < 0, caméra à l'origine
        facing = nx * p0[0] + ny * p0[1] + nz * p0[2]
        if facing > 0:
            if s.cull and mesh.closed:
                culled += 1
                continue
            nx, ny, nz = -nx, -ny, -nz  # éclairage double face
        depth = 0.0
        pts: List[float] = []
        for i in face:
            depth += cam[i][2]
            pts.extend(proj[i])
        depth /= len(face)
        diffuse = max(0.0, nx * lx + ny * ly + nz * lz)
        spec = max(0.0, nx * hx + ny * hy + nz * hz) ** s.shininess * s.specular
        k = s.ambient + (1 - s.ambient) * diffuse
        if s.toon:
            k = math.ceil(k * s.toon) / s.toon
            spec = 0.6 if spec > 0.3 else 0.0
        c = colors[fi] if colors else base
        r = c[0] * k + 255 * spec
        g = c[1] * k + 255 * spec
        b = c[2] * k + 255 * spec
        if s.fog:
            t = max(0.0, min(1.0, (depth - (dist - 1.5)) / (max_depth - dist + 1.5))) * s.fog
            r += (bg[0] - r) * t
            g += (bg[1] - g) * t
            b += (bg[2] - b) * t
        out.append(ProjectedFace(depth, pts, (min(255, int(r)), min(255, int(g)), min(255, int(b))),
                                 fi))
    out.sort(key=lambda f: -f.depth)  # algorithme du peintre : du plus loin au plus proche
    return out, culled


def hex_color(c: str) -> Tuple[int, int, int]:
    return hex_to_rgb(c)
