"""Analyse de code Python pour l'éditeur : coloration, plan (outline), diagnostics.

La coloration utilise une expression régulière maîtresse tolérante (le code en
cours de frappe est souvent invalide, ce qui ferait échouer ``tokenize``).
"""

from __future__ import annotations

import ast
import builtins
import bisect
import keyword
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

_KEYWORDS = r"\b(?:" + "|".join(sorted(keyword.kwlist + ["match", "case", "type"], key=len,
                                          reverse=True)) + r")\b"
_BUILTINS = r"\b(?:" + "|".join(sorted((n for n in dir(builtins) if not n.startswith("_")),
                                       key=len, reverse=True)) + r")\b"
_STR_PREFIX = r"(?:[rRbBuUfF]{0,2})"

_MASTER = re.compile(
    r"(?P<comment>#[^\n]*)"
    r"|(?P<string>" + _STR_PREFIX + r"(?:'''[\s\S]*?(?:'''|\Z)|\"\"\"[\s\S]*?(?:\"\"\"|\Z)"
    r"|'(?:\\.|[^'\\\n])*(?:'|$)|\"(?:\\.|[^\"\\\n])*(?:\"|$)))"
    r"|(?P<decorator>^[ \t]*@[\w.]+)"
    r"|(?P<defname>(?<=\bdef )\w+|(?<=\bclass )\w+)"
    r"|(?P<keyword>" + _KEYWORDS + r")"
    r"|(?P<self>\b(?:self|cls)\b)"
    r"|(?P<builtin>" + _BUILTINS + r")"
    r"|(?P<number>\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*\.?[\d_]*(?:[eE][+-]?\d+)?j?|\.\d+)\b)"
    r"|(?P<op>[-+*/%=<>!&|^~@:]+)",
    re.MULTILINE,
)

TAGS = ("comment", "string", "decorator", "defname", "keyword", "self", "builtin", "number", "op")


def highlight(text: str) -> List[Tuple[str, int, int]]:
    """Renvoie la liste (étiquette, début, fin) en positions de caractères."""
    spans = []
    for m in _MASTER.finditer(text):
        kind = m.lastgroup
        if kind:
            spans.append((kind, m.start(), m.end()))
    return spans


class LineIndex:
    """Conversion position de caractère → (ligne, colonne) au format Tk « l.c »."""

    def __init__(self, text: str) -> None:
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def index(self, offset: int) -> str:
        line = bisect.bisect_right(self.starts, offset) - 1
        return f"{line + 1}.{offset - self.starts[line]}"


@dataclass
class OutlineItem:
    kind: str  # "class" | "def" | "async def"
    name: str
    line: int
    depth: int
    signature: str = ""


def outline(source: str) -> List[OutlineItem]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    items: List[OutlineItem] = []

    def visit(node: ast.AST, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in child.bases) if hasattr(ast, "unparse") else ""
                items.append(OutlineItem("class", child.name, child.lineno, depth,
                                         f"({bases})" if bases else ""))
                visit(child, depth + 1)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in child.args.args]
                kind = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                items.append(OutlineItem(kind, child.name, child.lineno, depth,
                                         f"({', '.join(args)})"))
                visit(child, depth + 1)

    visit(tree, 0)
    return items


@dataclass
class Diagnostic:
    line: int
    column: int
    message: str


def check(source: str, filename: str = "<éditeur>") -> Optional[Diagnostic]:
    try:
        compile(source, filename, "exec", dont_inherit=True)
    except SyntaxError as e:
        return Diagnostic(e.lineno or 1, max(0, (e.offset or 1) - 1), e.msg or "erreur de syntaxe")
    except ValueError as e:  # octets nuls, etc.
        return Diagnostic(1, 0, str(e))
    return None


def code_stats(source: str) -> dict:
    lines = source.splitlines()
    code = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    return {"lignes": len(lines), "code": len(code),
            "commentaires": sum(1 for l in lines if l.strip().startswith("#"))}


SAMPLE = '''"""Exemple : calcul de π par la méthode de Monte-Carlo et suite de Fibonacci."""

import math
import random
from functools import lru_cache


@lru_cache(maxsize=None)
def fibonacci(n: int) -> int:
    """n-ième nombre de Fibonacci (mémoïsé)."""
    return n if n < 2 else fibonacci(n - 1) + fibonacci(n - 2)


class MonteCarlo:
    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)

    def estimate_pi(self, samples: int = 200_000) -> float:
        inside = sum(1 for _ in range(samples)
                     if self.rng.random() ** 2 + self.rng.random() ** 2 <= 1.0)
        return 4 * inside / samples


if __name__ == "__main__":
    mc = MonteCarlo()
    for n in (1_000, 10_000, 100_000):
        est = mc.estimate_pi(n)
        print(f"{n:>7} échantillons : π ≈ {est:.5f}  (erreur {abs(est - math.pi):.5f})")
    print("Fibonacci :", [fibonacci(i) for i in range(15)])
'''
