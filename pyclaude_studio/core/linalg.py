"""Algèbre linéaire 3D minimaliste : vecteurs (tuples), matrices 3×3, quaternions.

Les vecteurs sont de simples tuples ``(x, y, z)`` : c'est la représentation la
plus rapide en Python pur pour les boucles de rendu.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def normalize(a: Vec3) -> Vec3:
    n = length(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def mat_mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )  # type: ignore[return-value]


def mat_vec(m: Mat3, v: Vec3) -> Vec3:
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def transpose(m: Mat3) -> Mat3:
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def rotation_x(a: float) -> Mat3:
    c, s = math.cos(a), math.sin(a)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def rotation_y(a: float) -> Mat3:
    c, s = math.cos(a), math.sin(a)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def rotation_z(a: float) -> Mat3:
    c, s = math.cos(a), math.sin(a)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


class Quaternion:
    """Quaternion unitaire pour les rotations sans blocage de cardan (arcball)."""

    __slots__ = ("w", "x", "y", "z")

    def __init__(self, w: float = 1.0, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.w, self.x, self.y, self.z = w, x, y, z

    @classmethod
    def from_axis_angle(cls, axis: Vec3, angle: float) -> "Quaternion":
        ax = normalize(axis)
        s = math.sin(angle / 2)
        return cls(math.cos(angle / 2), ax[0] * s, ax[1] * s, ax[2] * s)

    @classmethod
    def between(cls, a: Vec3, b: Vec3) -> "Quaternion":
        """Rotation minimale amenant le vecteur unitaire ``a`` sur ``b``."""
        d = dot(a, b)
        if d < -0.999999:
            axis = cross((1.0, 0.0, 0.0), a)
            if length(axis) < 1e-6:
                axis = cross((0.0, 1.0, 0.0), a)
            return cls.from_axis_angle(axis, math.pi)
        c = cross(a, b)
        q = cls(1.0 + d, c[0], c[1], c[2])
        return q.normalized()

    def __mul__(self, o: "Quaternion") -> "Quaternion":
        return Quaternion(
            self.w * o.w - self.x * o.x - self.y * o.y - self.z * o.z,
            self.w * o.x + self.x * o.w + self.y * o.z - self.z * o.y,
            self.w * o.y - self.x * o.z + self.y * o.w + self.z * o.x,
            self.w * o.z + self.x * o.y - self.y * o.x + self.z * o.w,
        )

    def normalized(self) -> "Quaternion":
        n = math.sqrt(self.w ** 2 + self.x ** 2 + self.y ** 2 + self.z ** 2) or 1.0
        return Quaternion(self.w / n, self.x / n, self.y / n, self.z / n)

    def conjugate(self) -> "Quaternion":
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def rotate(self, v: Vec3) -> Vec3:
        return mat_vec(self.to_matrix(), v)

    def to_matrix(self) -> Mat3:
        w, x, y, z = self.w, self.x, self.y, self.z
        return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
                (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
                (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))

    def slerp(self, o: "Quaternion", t: float) -> "Quaternion":
        d = self.w * o.w + self.x * o.x + self.y * o.y + self.z * o.z
        if d < 0:
            o = Quaternion(-o.w, -o.x, -o.y, -o.z)
            d = -d
        if d > 0.9995:
            return Quaternion(self.w + (o.w - self.w) * t, self.x + (o.x - self.x) * t,
                              self.y + (o.y - self.y) * t, self.z + (o.z - self.z) * t).normalized()
        theta = math.acos(d)
        s = math.sin(theta)
        a, b = math.sin((1 - t) * theta) / s, math.sin(t * theta) / s
        return Quaternion(a * self.w + b * o.w, a * self.x + b * o.x,
                          a * self.y + b * o.y, a * self.z + b * o.z)

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.w, self.x, self.y, self.z)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Quaternion({self.w:.3f}, {self.x:.3f}, {self.y:.3f}, {self.z:.3f})"


def arcball_vector(x: float, y: float, width: float, height: float) -> Vec3:
    """Projette un point écran sur la sphère virtuelle de l'arcball (Shoemake)."""
    r = min(width, height) / 2 or 1.0
    px = (x - width / 2) / r
    py = (height / 2 - y) / r
    d2 = px * px + py * py
    if d2 <= 1.0:
        return (px, py, math.sqrt(1.0 - d2))
    n = math.sqrt(d2)
    return (px / n, py / n, 0.0)


def bounding_box(points: Iterable[Vec3]) -> Tuple[Vec3, Vec3]:
    pts = list(points)
    if not pts:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    xs, ys, zs = zip(*pts)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def centroid(points: Sequence[Vec3]) -> Vec3:
    n = len(points) or 1
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n,
            sum(p[2] for p in points) / n)


def face_normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    return normalize(cross(sub(b, a), sub(c, a)))


def polygon_normal(points: List[Vec3]) -> Vec3:
    """Normale de Newell : robuste pour les polygones non plans."""
    nx = ny = nz = 0.0
    count = len(points)
    for i in range(count):
        x0, y0, z0 = points[i]
        x1, y1, z1 = points[(i + 1) % count]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return normalize((nx, ny, nz))
