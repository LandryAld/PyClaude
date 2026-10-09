"""Moteur de tableur réactif.

* Formules ``=…`` analysées par le moteur d'expressions (références ``A1``,
  ``$A$1``, plages ``A1:C3``, fonctions françaises et anglaises).
* Graphe de dépendances inverse : une modification ne recalcule que les
  cellules impactées, dans l'ordre topologique.
* Détection des références circulaires (``#CYCLE!``) et propagation des erreurs.
* Copier-coller avec ajustement des références relatives.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..core.expr import Env, ExprError, Node, Range, Var, parse
from ..core import expr as _expr

Pos = Tuple[int, int]  # (ligne, colonne), indices à partir de 0
_REF_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,6})$")
_REF_IN_FORMULA = re.compile(
    r'("(?:[^"]|"")*")|(?<![\w.$])(\$?)([A-Za-z]{1,3})(\$?)(\d{1,6})(?![\w(])')
_NUMBER_RE = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?%?$")


class CellError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


ERRORS = {"#DIV/0!", "#REF!", "#NOM?", "#VALEUR!", "#CYCLE!", "#ERREUR!", "#N/A"}


def col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def col_index(name: str) -> int:
    n = 0
    for ch in name.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_ref(ref: str) -> Optional[Pos]:
    m = _REF_RE.match(ref)
    if not m:
        return None
    return int(m.group(2)) - 1, col_index(m.group(1))


def ref_name(pos: Pos) -> str:
    return f"{col_name(pos[1])}{pos[0] + 1}"


def parse_number(text: str) -> Optional[float]:
    t = text.strip().replace(" ", "").replace(" ", "")
    if not _NUMBER_RE.match(t):
        return None
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return v / 100 if pct else v


def format_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "VRAI" if v else "FAUX"
    if isinstance(v, float):
        if math.isnan(v):
            return "#VALEUR!"
        if math.isinf(v):
            return "#DIV/0!"
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.10g}"
    return str(v)


@dataclass
class Cell:
    raw: str = ""
    value: Any = None
    error: Optional[str] = None
    node: Optional[Node] = None
    refs: Set[Pos] = field(default_factory=set)

    @property
    def is_formula(self) -> bool:
        return self.raw.startswith("=")

    @property
    def display(self) -> str:
        return self.error or format_value(self.value)


# -- fonctions du tableur ---------------------------------------------------------

def _nums(values: Iterable[Any]) -> List[float]:
    out = []
    for v in values:
        if isinstance(v, bool):
            out.append(float(v))
        elif isinstance(v, (int, float)):
            out.append(float(v))
    return out


def _criterion(crit: Any) -> Callable[[Any], bool]:
    if isinstance(crit, (int, float)) and not isinstance(crit, bool):
        return lambda v: isinstance(v, (int, float)) and float(v) == float(crit)
    text = str(crit).strip()
    m = re.match(r"^(<=|>=|<>|!=|<|>|=)?(.*)$", text)
    op, rhs = (m.group(1) or "="), m.group(2)  # type: ignore[union-attr]
    num = parse_number(rhs)
    if num is not None:
        ops = {"=": lambda a: a == num, "<>": lambda a: a != num, "!=": lambda a: a != num,
               "<": lambda a: a < num, ">": lambda a: a > num, "<=": lambda a: a <= num,
               ">=": lambda a: a >= num}
        f = ops[op]
        return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and f(float(v))
    if op in ("<>", "!="):
        return lambda v: str(v).lower() != rhs.lower()
    return lambda v: str(v).lower() == rhs.lower()


def _if(env: "SheetEnv", args: Sequence[Node]) -> Any:
    if len(args) not in (2, 3):
        raise CellError("#VALEUR!")
    cond = env.eval(args[0])
    if isinstance(cond, str):
        raise CellError("#VALEUR!")
    if cond:
        return env.eval(args[1])
    return env.eval(args[2]) if len(args) == 3 else False


def _iferror(env: "SheetEnv", args: Sequence[Node]) -> Any:
    if len(args) != 2:
        raise CellError("#VALEUR!")
    try:
        v = env.eval(args[0])
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise CellError("#VALEUR!")
        return v
    except (CellError, ExprError, ArithmeticError, ValueError, TypeError):
        return env.eval(args[1])


def _range_values(env: "SheetEnv", node: Node) -> List[Any]:
    if isinstance(node, Range):
        return env.range(node.start, node.end)
    return [env.eval(node)]


def _countif(env: "SheetEnv", args: Sequence[Node]) -> Any:
    if len(args) != 2:
        raise CellError("#VALEUR!")
    pred = _criterion(env.eval(args[1]))
    return float(sum(1 for v in _range_values(env, args[0]) if v is not None and pred(v)))


def _sumif(env: "SheetEnv", args: Sequence[Node]) -> Any:
    if len(args) not in (2, 3):
        raise CellError("#VALEUR!")
    pred = _criterion(env.eval(args[1]))
    crit_vals = _range_values(env, args[0])
    sum_vals = _range_values(env, args[2]) if len(args) == 3 else crit_vals
    total = 0.0
    for c, s in zip(crit_vals, sum_vals):
        if c is not None and pred(c) and isinstance(s, (int, float)):
            total += float(s)
    return total


def _vlookup(env: "SheetEnv", args: Sequence[Node]) -> Any:
    if len(args) < 3 or not isinstance(args[1], Range):
        raise CellError("#VALEUR!")
    key = env.eval(args[0])
    col = int(env.eval(args[2]))
    a, b = parse_ref(args[1].start), parse_ref(args[1].end)
    if a is None or b is None:
        raise CellError("#REF!")
    r0, r1 = sorted((a[0], b[0]))
    c0, c1 = sorted((a[1], b[1]))
    if not 1 <= col <= c1 - c0 + 1:
        raise CellError("#REF!")
    for r in range(r0, r1 + 1):
        v = env.value_at((r, c0))
        if v == key or (isinstance(v, str) and isinstance(key, str) and v.lower() == key.lower()):
            return env.value_at((r, c0 + col - 1))
    raise CellError("#N/A")


def _concat(*vals: Any) -> str:
    return "".join(format_value(v) for v in vals if v is not None)


def _round(x: float, n: float = 0) -> float:
    return float(round(float(x), int(n)))


SHEET_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "sum": lambda *v: float(sum(_nums(v))),
    "average": lambda *v: statistics.fmean(_nums(v)) if _nums(v) else _raise("#DIV/0!"),
    "min": lambda *v: min(_nums(v)) if _nums(v) else 0.0,
    "max": lambda *v: max(_nums(v)) if _nums(v) else 0.0,
    "count": lambda *v: float(len(_nums(v))),
    "counta": lambda *v: float(sum(1 for x in v if x not in (None, ""))),
    "median": lambda *v: float(statistics.median(_nums(v))) if _nums(v) else _raise("#N/A"),
    "stdev": lambda *v: statistics.stdev(_nums(v)) if len(_nums(v)) > 1 else _raise("#DIV/0!"),
    "product": lambda *v: float(math.prod(_nums(v))),
    "abs": lambda x: abs(float(x)),
    "round": _round,
    "int": lambda x: float(math.floor(float(x))),
    "sqrt": lambda x: math.sqrt(float(x)),
    "power": lambda x, y: float(x) ** float(y),
    "mod": lambda x, y: float(x) % float(y) if float(y) else _raise("#DIV/0!"),
    "pi": lambda: math.pi,
    "and": lambda *v: all(bool(x) for x in v),
    "or": lambda *v: any(bool(x) for x in v),
    "not": lambda x: not bool(x),
    "concat": _concat,
    "len": lambda s: float(len(format_value(s))),
    "upper": lambda s: format_value(s).upper(),
    "lower": lambda s: format_value(s).lower(),
    "true": lambda: True,
    "false": lambda: False,
}
_FR_ALIASES = {
    "somme": "sum", "moyenne": "average", "nb": "count", "nbval": "counta", "mediane": "median",
    "médiane": "median", "ecartype": "stdev", "écartype": "stdev", "produit": "product",
    "arrondi": "round", "ent": "int", "racine": "sqrt", "puissance": "power", "et": "and",
    "ou": "or", "non": "not", "concatener": "concat", "concaténer": "concat", "nbcar": "len",
    "majuscule": "upper", "minuscule": "lower", "vrai": "true", "faux": "false",
}
for _fr, _en in _FR_ALIASES.items():
    SHEET_FUNCTIONS[_fr] = SHEET_FUNCTIONS[_en]

SHEET_LAZY = {"if": _if, "si": _if, "iferror": _iferror, "sierreur": _iferror,
              "countif": _countif, "nb.si": _countif, "sumif": _sumif, "somme.si": _sumif,
              "vlookup": _vlookup, "recherchev": _vlookup}


def _raise(code: str) -> Any:
    raise CellError(code)


class SheetEnv(Env):
    def __init__(self, sheet: "Sheet", origin: Pos) -> None:
        super().__init__(functions=SHEET_FUNCTIONS, lazy=SHEET_LAZY, case_insensitive=True)
        self.sheet = sheet
        self.origin = origin

    def value_at(self, pos: Pos) -> Any:
        return self.sheet._value_for_formula(pos)

    def lookup(self, name: str) -> Any:
        pos = parse_ref(name)
        if pos is not None:
            if not self.sheet.in_bounds(pos):
                raise CellError("#REF!")
            v = self.value_at(pos)
            return 0.0 if v is None else v
        lname = name.lower()
        if lname in ("vrai", "true"):
            return True
        if lname in ("faux", "false"):
            return False
        try:
            return super().lookup(name)
        except ExprError:
            raise CellError("#NOM?") from None

    def range(self, start: str, end: str) -> List[Any]:
        a, b = parse_ref(start), parse_ref(end)
        if a is None or b is None:
            raise CellError("#REF!")
        r0, r1 = sorted((a[0], b[0]))
        c0, c1 = sorted((a[1], b[1]))
        if not (self.sheet.in_bounds((r0, c0)) and self.sheet.in_bounds((r1, c1))):
            raise CellError("#REF!")
        return [self.value_at((r, c)) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]

    def call(self, name: str, args: Sequence[Node]) -> Any:
        key = name.lower()
        if key not in self.functions and key not in self.lazy:
            raise CellError("#NOM?")
        return super().call(name, args)


def references(node: Node, limit: int = 100_000) -> Set[Pos]:
    out: Set[Pos] = set()

    def walk(n: Node) -> None:
        if isinstance(n, Var):
            p = parse_ref(n.name)
            if p is not None:
                out.add(p)
        elif isinstance(n, Range):
            a, b = parse_ref(n.start), parse_ref(n.end)
            if a is not None and b is not None:
                r0, r1 = sorted((a[0], b[0]))
                c0, c1 = sorted((a[1], b[1]))
                if (r1 - r0 + 1) * (c1 - c0 + 1) > limit:
                    raise CellError("#REF!")
                for r in range(r0, r1 + 1):
                    for c in range(c0, c1 + 1):
                        out.add((r, c))
        elif isinstance(n, _expr.Neg):
            walk(n.arg)
        elif isinstance(n, _expr.Bin):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, _expr.Call):
            for a in n.args:
                walk(a)

    walk(node)
    return out


def shift_formula(raw: str, drow: int, dcol: int) -> str:
    """Décale les références relatives d'une formule (les ``$`` restent fixes)."""
    if not raw.startswith("="):
        return raw

    def repl(m: re.Match) -> str:
        if m.group(1):  # chaîne littérale : intacte
            return m.group(1)
        dc, col, dr, row = m.group(2), m.group(3), m.group(4), m.group(5)
        c = col_index(col) + (0 if dc else dcol)
        r = int(row) - 1 + (0 if dr else drow)
        if c < 0 or r < 0:
            return "#REF!"
        return f"{dc}{col_name(c)}{dr}{r + 1}"

    return "=" + _REF_IN_FORMULA.sub(repl, raw[1:])


class Sheet:
    def __init__(self, rows: int = 200, cols: int = 26) -> None:
        self.rows = rows
        self.cols = cols
        self.cells: Dict[Pos, Cell] = {}
        self.dependents: Dict[Pos, Set[Pos]] = {}
        self.recalc_count = 0

    def in_bounds(self, pos: Pos) -> bool:
        return 0 <= pos[0] < self.rows and 0 <= pos[1] < self.cols

    # -- accès ------------------------------------------------------------
    def raw(self, pos: Pos) -> str:
        c = self.cells.get(pos)
        return c.raw if c else ""

    def value(self, pos: Pos) -> Any:
        c = self.cells.get(pos)
        if c is None:
            return None
        return c.error or c.value

    def display(self, pos: Pos) -> str:
        c = self.cells.get(pos)
        return c.display if c else ""

    def cell(self, pos: Pos) -> Optional[Cell]:
        return self.cells.get(pos)

    def _value_for_formula(self, pos: Pos) -> Any:
        c = self.cells.get(pos)
        if c is None:
            return None
        if c.error:
            raise CellError(c.error)
        return c.value

    def __getitem__(self, ref: str) -> Any:
        pos = parse_ref(ref)
        if pos is None:
            raise KeyError(ref)
        return self.value(pos)

    def __setitem__(self, ref: str, raw: str) -> None:
        pos = parse_ref(ref)
        if pos is None:
            raise KeyError(ref)
        self.set(pos, raw)

    # -- modification -------------------------------------------------------
    def set(self, pos: Pos, raw: str) -> Set[Pos]:
        """Modifie une cellule et renvoie l'ensemble des cellules recalculées."""
        return self.set_many({pos: raw})

    def set_many(self, changes: Dict[Pos, str]) -> Set[Pos]:
        for pos, raw in changes.items():
            self._unlink(pos)
            raw = "" if raw is None else str(raw)
            if raw == "":
                self.cells.pop(pos, None)
                continue
            cell = Cell(raw=raw)
            self.cells[pos] = cell
            if raw.startswith("="):
                try:
                    cell.node = parse(raw[1:], implicit_mul=False)
                    cell.refs = references(cell.node)
                except ExprError:
                    cell.error = "#ERREUR!"
                except CellError as e:
                    cell.error = e.code
                for ref in cell.refs:
                    self.dependents.setdefault(ref, set()).add(pos)
        return self._recalc(set(changes))

    def _unlink(self, pos: Pos) -> None:
        old = self.cells.get(pos)
        if old is None:
            return
        for ref in old.refs:
            deps = self.dependents.get(ref)
            if deps is not None:
                deps.discard(pos)
                if not deps:
                    del self.dependents[ref]

    def _affected(self, roots: Set[Pos]) -> List[Pos]:
        """Cellules à recalculer, en ordre topologique (DFS post-ordre inversé)."""
        order: List[Pos] = []
        state: Dict[Pos, int] = {}
        for root in roots:
            if state.get(root):
                continue
            stack: List[Tuple[Pos, Iterable[Pos]]] = [(root, iter(sorted(self.dependents.get(root, ()))))]
            state[root] = 1
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    stack.pop()
                    state[node] = 2
                    order.append(node)
                elif not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(sorted(self.dependents.get(nxt, ())))))
        order.reverse()
        return order

    def _find_cycles(self, candidates: Iterable[Pos]) -> Set[Pos]:
        """Cellules appartenant à un cycle (composantes fortement connexes de Tarjan)."""
        index: Dict[Pos, int] = {}
        low: Dict[Pos, int] = {}
        on_stack: Set[Pos] = set()
        stack: List[Pos] = []
        cyclic: Set[Pos] = set()
        counter = [0]

        def succ(p: Pos) -> List[Pos]:
            c = self.cells.get(p)
            return [r for r in c.refs if r in self.cells] if c else []

        for start in candidates:
            if start in index:
                continue
            work = [(start, iter(succ(start)))]
            index[start] = low[start] = counter[0]
            counter[0] += 1
            stack.append(start)
            on_stack.add(start)
            while work:
                node, it = work[-1]
                nxt = next(it, None)
                if nxt is not None:
                    if nxt not in index:
                        index[nxt] = low[nxt] = counter[0]
                        counter[0] += 1
                        stack.append(nxt)
                        on_stack.add(nxt)
                        work.append((nxt, iter(succ(nxt))))
                    elif nxt in on_stack:
                        low[node] = min(low[node], index[nxt])
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    if len(comp) > 1 or node in succ(node):
                        cyclic.update(comp)
        return cyclic

    def _recalc(self, roots: Set[Pos]) -> Set[Pos]:
        order = self._affected(roots)
        cyclic = self._find_cycles([p for p in order if p in self.cells])
        # Les membres d'un cycle sont marqués d'abord : leurs dépendants héritent de l'erreur
        for pos in cyclic:
            self.cells[pos].value, self.cells[pos].error = None, "#CYCLE!"
        for pos in order:
            cell = self.cells.get(pos)
            if cell is not None and pos not in cyclic:
                self._evaluate(pos, cell)
        self.recalc_count += len(order)
        return set(order)

    def _evaluate(self, pos: Pos, cell: Cell) -> None:
        if not cell.is_formula:
            num = parse_number(cell.raw)
            cell.error = None
            if num is not None:
                cell.value = num
            elif cell.raw.upper() in ("VRAI", "TRUE"):
                cell.value = True
            elif cell.raw.upper() in ("FAUX", "FALSE"):
                cell.value = False
            else:
                cell.value = cell.raw[1:] if cell.raw.startswith("'") else cell.raw
            return
        if cell.node is None:
            cell.value = None
            cell.error = cell.error or "#ERREUR!"
            return
        try:
            v = SheetEnv(self, pos).eval(cell.node)
            if isinstance(v, list):
                raise CellError("#VALEUR!")
            if isinstance(v, float) and (math.isnan(v)):
                raise CellError("#VALEUR!")
            if isinstance(v, float) and math.isinf(v):
                raise CellError("#DIV/0!")
            cell.value, cell.error = v, None
        except CellError as e:
            cell.value, cell.error = None, e.code
        except ZeroDivisionError:
            cell.value, cell.error = None, "#DIV/0!"
        except (ExprError, TypeError, ValueError, OverflowError, AttributeError):
            cell.value, cell.error = None, "#VALEUR!"
        except RecursionError:
            cell.value, cell.error = None, "#CYCLE!"

    def recalc_all(self) -> None:
        self._recalc(set(self.cells))

    def clear(self) -> None:
        self.cells.clear()
        self.dependents.clear()

    # -- presse-papiers -----------------------------------------------------
    def copy_block(self, r0: int, c0: int, r1: int, c1: int) -> List[List[str]]:
        return [[self.raw((r, c)) for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)]

    def paste_block(self, block: List[List[str]], r: int, c: int, src: Optional[Pos] = None
                    ) -> Dict[Pos, str]:
        """Renvoie les modifications à appliquer (pour permettre l'annulation)."""
        changes: Dict[Pos, str] = {}
        for i, row in enumerate(block):
            for j, raw in enumerate(row):
                pos = (r + i, c + j)
                if not self.in_bounds(pos):
                    continue
                if src is not None:
                    raw = shift_formula(raw, pos[0] - (src[0] + i), pos[1] - (src[1] + j))
                changes[pos] = raw
        return changes

    # -- sérialisation ----------------------------------------------------------
    def to_dict(self) -> Dict[str, str]:
        return {ref_name(p): c.raw for p, c in sorted(self.cells.items())}

    def load_dict(self, data: Dict[str, str]) -> None:
        self.clear()
        changes = {}
        for ref, raw in data.items():
            pos = parse_ref(ref)
            if pos is not None and self.in_bounds(pos):
                changes[pos] = raw
        self.set_many(changes)

    def to_csv(self, delimiter: str = ";", values: bool = False) -> str:
        if not self.cells:
            return ""
        max_r = max(p[0] for p in self.cells)
        max_c = max(p[1] for p in self.cells)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
        for r in range(max_r + 1):
            w.writerow([(self.display((r, c)) if values else self.raw((r, c)))
                        for c in range(max_c + 1)])
        return buf.getvalue()

    def load_csv(self, text: str) -> None:
        try:
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";" if text.count(";") >= text.count(",") else ","
        self.clear()
        changes = {}
        for r, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter)):
            for c, raw in enumerate(row):
                if raw and self.in_bounds((r, c)):
                    changes[(r, c)] = raw
        self.set_many(changes)

    def to_json(self) -> str:
        return json.dumps({"rows": self.rows, "cols": self.cols, "cells": self.to_dict()},
                          ensure_ascii=False, indent=1)

    def load_json(self, text: str) -> None:
        data = json.loads(text)
        self.load_dict(data.get("cells", {}))


DEMO = {
    "A1": "Produit", "B1": "Quantité", "C1": "Prix unitaire", "D1": "Total", "E1": "Remise ?",
    "A2": "Clavier", "B2": "3", "C2": "49,90", "D2": "=B2*C2", "E2": "=SI(D2>100;\"oui\";\"non\")",
    "A3": "Souris", "B3": "5", "C3": "19,5", "D3": "=B3*C3", "E3": "=SI(D3>100;\"oui\";\"non\")",
    "A4": "Écran", "B4": "2", "C4": "229", "D4": "=B4*C4", "E4": "=SI(D4>100;\"oui\";\"non\")",
    "A5": "Câble", "B5": "12", "C5": "7,25", "D5": "=B5*C5", "E5": "=SI(D5>100;\"oui\";\"non\")",
    "A6": "Casque", "B6": "4", "C6": "89", "D6": "=B6*C6", "E6": "=SI(D6>100;\"oui\";\"non\")",
    "A8": "Total HT", "D8": "=SOMME(D2:D6)",
    "A9": "TVA 20 %", "D9": "=ARRONDI(D8*0,2;2)".replace("0,2", "0.2"),
    "A10": "Total TTC", "D10": "=D8+D9",
    "A12": "Article le plus cher", "D12": "=MAX(C2:C6)",
    "A13": "Moyenne des totaux", "D13": "=ARRONDI(MOYENNE(D2:D6);2)",
    "A14": "Lignes > 100 €", "D14": "=NB.SI(D2:D6;\">100\")",
    "A15": "Somme si remise", "D15": "=SOMME.SI(E2:E6;\"oui\";D2:D6)",
    "A16": "Prix de « Écran »", "D16": "=RECHERCHEV(\"Écran\";A2:C6;3)",
    "G1": "x", "H1": "sin(x)", "I1": "x²/10",
    **{f"G{i}": str((i - 2) * 0.5) for i in range(2, 22)},
    **{f"H{i}": f"=ARRONDI(sin(G{i});4)" for i in range(2, 22)},
    **{f"I{i}": f"=G{i}^2/10" for i in range(2, 22)},
}
