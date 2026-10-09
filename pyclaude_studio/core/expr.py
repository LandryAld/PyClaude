"""Moteur d'expressions mathématiques écrit de zéro.

Chaîne complète : analyse lexicale → analyse syntaxique de Pratt → arbre
syntaxique immuable → évaluation, compilation sûre en fonction Python,
dérivation symbolique et simplification algébrique.

Exemples ::

    >>> compile_expr("2x^2 + sin(x)")(0.0)
    0.0
    >>> str(simplify(derive(parse("x^3 + 2x", implicit_mul=True), "x")))
    '3*x^2 + 2'

Le même moteur alimente le traceur de courbes, le tableur (références de
cellules, plages ``A1:B3``, fonctions paresseuses comme ``SI``) et les surfaces
3D définies par l'utilisateur.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

__all__ = [
    "ExprError", "Node", "Num", "Str", "Var", "Neg", "Bin", "Call", "Range",
    "tokenize", "parse", "derive", "simplify", "to_string", "free_vars",
    "Env", "evaluate", "compile_expr", "CompiledExpr", "FUNCTIONS", "CONSTANTS",
]


class ExprError(Exception):
    """Erreur de syntaxe ou d'évaluation, avec position éventuelle."""

    def __init__(self, message: str, pos: Optional[int] = None) -> None:
        super().__init__(message)
        self.pos = pos


# ---------------------------------------------------------------------------
# Analyse lexicale
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    kind: str  # NUM STR ID OP LP RP COMMA EOF
    value: Any
    pos: int


_NUM_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ID_RE = re.compile(r"\$?[^\W\d][\w$]*(?:\.[^\W\d][\w$]*)*")
_OPERATORS = sorted(
    ["**", "<=", ">=", "==", "!=", "<>", "+", "-", "*", "/", "^", "%", "!",
     "<", ">", "=", ":", "&", "×", "÷", "·", "−"],
    key=len, reverse=True)
_OP_ALIASES = {"**": "^", "×": "*", "÷": "/", "·": "*", "−": "-", "<>": "!=", "==": "="}
_GREEK = {"π": "pi", "τ": "tau", "φ": "phi"}


def tokenize(text: str, *, implicit_mul: bool = False,
             functions: Optional[Iterable[str]] = None) -> List[Token]:
    funcs = {f.lower() for f in (functions if functions is not None else FUNCTIONS)}
    tokens: List[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUM_RE.match(text, i)
            assert m is not None
            tokens.append(Token("NUM", float(m.group()), i))
            i = m.end()
            continue
        if ch in _GREEK:
            tokens.append(Token("ID", _GREEK[ch], i))
            i += 1
            continue
        if ch.isalpha() or ch in "_$":
            m = _ID_RE.match(text, i)
            if not m:
                raise ExprError(f"identifiant invalide à la position {i + 1}", i)
            tokens.append(Token("ID", m.group(), i))
            i = m.end()
            continue
        if ch == '"':
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise ExprError("chaîne non terminée", i)
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            tokens.append(Token("STR", "".join(buf), i))
            i = j + 1
            continue
        if ch == "(":
            tokens.append(Token("LP", ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("RP", ch, i))
            i += 1
            continue
        if ch in ",;":
            tokens.append(Token("COMMA", ch, i))
            i += 1
            continue
        for op in _OPERATORS:
            if text.startswith(op, i):
                tokens.append(Token("OP", _OP_ALIASES.get(op, op), i))
                i += len(op)
                break
        else:
            raise ExprError(f"caractère inattendu « {ch} » à la position {i + 1}", i)
    if implicit_mul:
        tokens = _insert_implicit_mul(tokens, funcs)
    tokens.append(Token("EOF", None, n))
    return tokens


def _insert_implicit_mul(tokens: List[Token], funcs: Set[str]) -> List[Token]:
    out: List[Token] = []
    for tok in tokens:
        if out:
            prev = out[-1]
            prev_value = prev.kind in ("NUM", "RP") or (prev.kind == "OP" and prev.value == "!")
            prev_id = prev.kind == "ID"
            starts_value = tok.kind in ("NUM", "ID", "LP")
            if starts_value and (
                prev_value and not (prev.kind == "NUM" and tok.kind == "NUM")
                or prev_id and tok.kind != "LP"
                or prev_id and tok.kind == "LP" and str(prev.value).lower() not in funcs
            ):
                out.append(Token("OP", "*", tok.pos))
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Arbre syntaxique
# ---------------------------------------------------------------------------

class Node:
    __slots__ = ()

    def __str__(self) -> str:
        return to_string(self)


@dataclass(frozen=True)
class Num(Node):
    value: float


@dataclass(frozen=True)
class Str(Node):
    value: str


@dataclass(frozen=True)
class Var(Node):
    name: str


@dataclass(frozen=True)
class Neg(Node):
    arg: Node


@dataclass(frozen=True)
class Bin(Node):
    op: str
    left: Node
    right: Node


@dataclass(frozen=True)
class Call(Node):
    name: str
    args: Tuple[Node, ...]


@dataclass(frozen=True)
class Range(Node):
    start: str
    end: str


ZERO, ONE, TWO = Num(0.0), Num(1.0), Num(2.0)

COMPARISONS = {"=", "!=", "<", ">", "<=", ">="}
_INFIX = {
    "=": (10, 11), "!=": (10, 11), "<": (10, 11), ">": (10, 11), "<=": (10, 11), ">=": (10, 11),
    "&": (15, 16),
    "+": (20, 21), "-": (20, 21),
    "*": (30, 31), "/": (30, 31), "%": (30, 31),
    "^": (51, 50),
    ":": (70, 71),
}
_PREFIX_BP = 45
_POSTFIX_BP = 60


class _Parser:
    def __init__(self, tokens: List[Token], text: str) -> None:
        self.tokens = tokens
        self.text = text
        self.i = 0

    def peek(self) -> Token:
        return self.tokens[self.i]

    def next(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, kind: str, what: str) -> Token:
        tok = self.next()
        if tok.kind != kind:
            raise ExprError(f"{what} attendu(e) à la position {tok.pos + 1}", tok.pos)
        return tok

    def parse(self) -> Node:
        if self.peek().kind == "EOF":
            raise ExprError("expression vide", 0)
        node = self.expr(0)
        tok = self.peek()
        if tok.kind != "EOF":
            raise ExprError(f"symbole inattendu « {self.text[tok.pos:tok.pos + 8]} » "
                            f"à la position {tok.pos + 1}", tok.pos)
        return node

    def expr(self, min_bp: int) -> Node:
        tok = self.next()
        if tok.kind == "NUM":
            lhs: Node = Num(tok.value)
        elif tok.kind == "STR":
            lhs = Str(tok.value)
        elif tok.kind == "ID":
            if self.peek().kind == "LP":
                self.next()
                args: List[Node] = []
                if self.peek().kind != "RP":
                    while True:
                        args.append(self.expr(0))
                        if self.peek().kind == "COMMA":
                            self.next()
                            continue
                        break
                self.expect("RP", "« ) »")
                lhs = Call(tok.value, tuple(args))
            else:
                lhs = Var(tok.value)
        elif tok.kind == "LP":
            lhs = self.expr(0)
            self.expect("RP", "« ) »")
        elif tok.kind == "OP" and tok.value == "-":
            lhs = Neg(self.expr(_PREFIX_BP))
        elif tok.kind == "OP" and tok.value == "+":
            lhs = self.expr(_PREFIX_BP)
        elif tok.kind == "EOF":
            raise ExprError("fin d'expression inattendue", tok.pos)
        else:
            raise ExprError(f"symbole inattendu « {tok.value} » à la position {tok.pos + 1}",
                            tok.pos)

        while True:
            tok = self.peek()
            if tok.kind != "OP":
                break
            op = tok.value
            if op == "!":
                if _POSTFIX_BP < min_bp:
                    break
                self.next()
                lhs = Call("fact", (lhs,))
                continue
            if op not in _INFIX:
                break
            lbp, rbp = _INFIX[op]
            if lbp < min_bp:
                break
            self.next()
            rhs = self.expr(rbp)
            if op == ":":
                if not (isinstance(lhs, Var) and isinstance(rhs, Var)):
                    raise ExprError("une plage s'écrit CELLULE:CELLULE", tok.pos)
                lhs = Range(lhs.name, rhs.name)
            else:
                lhs = Bin(op, lhs, rhs)
        return lhs


def parse(text: str, *, implicit_mul: bool = False,
          functions: Optional[Iterable[str]] = None) -> Node:
    """Analyse ``text`` et renvoie l'arbre syntaxique."""
    return _Parser(tokenize(text, implicit_mul=implicit_mul, functions=functions), text).parse()


# ---------------------------------------------------------------------------
# Fonctions et constantes
# ---------------------------------------------------------------------------

def _fact(x: float) -> float:
    if x == int(x) and 0 <= x <= 170:
        return float(math.factorial(int(x)))
    return math.gamma(x + 1)


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def _log(x: float, base: Optional[float] = None) -> float:
    return math.log10(x) if base is None else math.log(x, base)


def _round(x: float, nd: float = 0) -> float:
    return float(round(x, int(nd)))


def _sinc(x: float) -> float:
    return 1.0 if x == 0 else math.sin(x) / x


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


FUNCTIONS: Dict[str, Tuple[Callable[..., float], int, int]] = {
    # nom: (fonction, nb min d'arguments, nb max (-1 = illimité))
    "sin": (math.sin, 1, 1), "cos": (math.cos, 1, 1), "tan": (math.tan, 1, 1),
    "asin": (math.asin, 1, 1), "acos": (math.acos, 1, 1), "atan": (math.atan, 1, 1),
    "arcsin": (math.asin, 1, 1), "arccos": (math.acos, 1, 1), "arctan": (math.atan, 1, 1),
    "sinh": (math.sinh, 1, 1), "cosh": (math.cosh, 1, 1), "tanh": (math.tanh, 1, 1),
    "asinh": (math.asinh, 1, 1), "acosh": (math.acosh, 1, 1), "atanh": (math.atanh, 1, 1),
    "sec": (lambda x: 1 / math.cos(x), 1, 1), "csc": (lambda x: 1 / math.sin(x), 1, 1),
    "cot": (lambda x: 1 / math.tan(x), 1, 1),
    "exp": (math.exp, 1, 1), "ln": (math.log, 1, 1), "log": (_log, 1, 2),
    "log2": (math.log2, 1, 1), "sqrt": (math.sqrt, 1, 1), "cbrt": (_cbrt, 1, 1),
    "abs": (abs, 1, 1), "floor": (math.floor, 1, 1), "ceil": (math.ceil, 1, 1),
    "round": (_round, 1, 2), "sign": (_sign, 1, 1), "frac": (lambda x: x - math.floor(x), 1, 1),
    "min": (min, 1, -1), "max": (max, 1, -1), "atan2": (math.atan2, 2, 2),
    "hypot": (math.hypot, 2, -1), "mod": (math.fmod, 2, 2), "fact": (_fact, 1, 1),
    "gamma": (math.gamma, 1, 1), "sinc": (_sinc, 1, 1), "clamp": (_clamp, 3, 3),
    "step": (lambda x: 1.0 if x >= 0 else 0.0, 1, 1),
}

CONSTANTS: Dict[str, float] = {
    "pi": math.pi, "e": math.e, "tau": math.tau,
    "phi": (1 + math.sqrt(5)) / 2, "inf": math.inf,
}


def _safe_pow(a: float, b: float) -> float:
    if a < 0 and b != int(b):
        inv = 1.0 / b if b else 0.0
        k = round(inv)
        if abs(inv - k) < 1e-9 and k % 2 == 1:  # racine impaire réelle (x^(1/3))
            return -((-a) ** b)
        return math.nan
    try:
        result = a ** b
    except OverflowError:
        return math.inf
    except ZeroDivisionError:
        return math.inf
    if isinstance(result, complex):
        return math.nan
    return result


def _apply_bin(op: str, a: Any, b: Any) -> Any:
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return a / b
    if op == "^":
        return _safe_pow(a, b)
    if op == "%":
        return math.fmod(a, b) if b else math.nan
    if op == "&":
        return f"{_as_text(a)}{_as_text(b)}"
    if op == "=":
        return a == b
    if op == "!=":
        return a != b
    if op == "<":
        return a < b
    if op == ">":
        return a > b
    if op == "<=":
        return a <= b
    if op == ">=":
        return a >= b
    raise ExprError(f"opérateur inconnu {op}")


def _as_text(v: Any) -> str:
    if isinstance(v, bool):
        return "VRAI" if v else "FAUX"
    if isinstance(v, float):
        return format_number(v)
    return str(v)


def format_number(v: float) -> str:
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "∞" if v > 0 else "-∞"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.10g}"


# ---------------------------------------------------------------------------
# Évaluation par parcours d'arbre
# ---------------------------------------------------------------------------

LazyFunc = Callable[["Env", Sequence[Node]], Any]


class Env:
    """Environnement d'évaluation : variables, fonctions, plages."""

    def __init__(self, variables: Optional[Dict[str, Any]] = None,
                 functions: Optional[Dict[str, Callable[..., Any]]] = None,
                 lazy: Optional[Dict[str, LazyFunc]] = None,
                 case_insensitive: bool = False) -> None:
        self.variables: Dict[str, Any] = dict(CONSTANTS)
        if variables:
            self.variables.update(variables)
        self.functions: Dict[str, Callable[..., Any]] = {
            k: v[0] for k, v in FUNCTIONS.items()}
        if functions:
            self.functions.update({k.lower(): v for k, v in functions.items()})
        self.lazy: Dict[str, LazyFunc] = {k.lower(): v for k, v in (lazy or {}).items()}
        self.case_insensitive = case_insensitive

    def lookup(self, name: str) -> Any:
        if name in self.variables:
            return self.variables[name]
        if self.case_insensitive and name.lower() in self.variables:
            return self.variables[name.lower()]
        raise ExprError(f"nom inconnu « {name} »")

    def range(self, start: str, end: str) -> List[Any]:
        raise ExprError("les plages ne sont pas disponibles ici")

    def call(self, name: str, args: Sequence[Node]) -> Any:
        key = name.lower()
        if key in self.lazy:
            return self.lazy[key](self, args)
        func = self.functions.get(key)
        if func is None:
            raise ExprError(f"fonction inconnue « {name} »")
        values: List[Any] = []
        for arg in args:
            v = self.eval(arg)
            if isinstance(arg, Range):
                values.extend(v)
            else:
                values.append(v)
        return func(*values)

    def eval(self, node: Node) -> Any:
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Str):
            return node.value
        if isinstance(node, Var):
            return self.lookup(node.name)
        if isinstance(node, Neg):
            return -self.eval(node.arg)
        if isinstance(node, Bin):
            return _apply_bin(node.op, self.eval(node.left), self.eval(node.right))
        if isinstance(node, Call):
            return self.call(node.name, node.args)
        if isinstance(node, Range):
            return self.range(node.start, node.end)
        raise ExprError(f"nœud inconnu {node!r}")


def evaluate(expr: Any, variables: Optional[Dict[str, Any]] = None, **kw: Any) -> Any:
    node = parse(expr, **kw) if isinstance(expr, str) else expr
    try:
        return Env(variables).eval(node)
    except ExprError:
        raise
    except ZeroDivisionError:
        raise ExprError("division par zéro") from None
    except (ValueError, OverflowError, TypeError) as exc:
        raise ExprError(f"erreur de calcul : {exc}") from None


def free_vars(node: Node) -> Set[str]:
    out: Set[str] = set()

    def walk(n: Node) -> None:
        if isinstance(n, Var):
            if n.name not in CONSTANTS:
                out.add(n.name)
        elif isinstance(n, Neg):
            walk(n.arg)
        elif isinstance(n, Bin):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, Call):
            for a in n.args:
                walk(a)

    walk(node)
    return out


def contains_var(node: Node, var: str) -> bool:
    if isinstance(node, Var):
        return node.name == var
    if isinstance(node, Neg):
        return contains_var(node.arg, var)
    if isinstance(node, Bin):
        return contains_var(node.left, var) or contains_var(node.right, var)
    if isinstance(node, Call):
        return any(contains_var(a, var) for a in node.args)
    return False


# ---------------------------------------------------------------------------
# Compilation en fonction Python
# ---------------------------------------------------------------------------

_PY_GLOBALS: Dict[str, Any] = {"__builtins__": {}, "_nan": math.nan, "_pow": _safe_pow,
                               "_float": float, "_fmod": math.fmod,
                               "ArithmeticError": ArithmeticError, "ValueError": ValueError,
                               "TypeError": TypeError}
for _name, (_fn, _lo, _hi) in FUNCTIONS.items():
    _PY_GLOBALS["_f_" + _name] = _fn
for _name, _value in CONSTANTS.items():
    _PY_GLOBALS["_c_" + _name] = _value


def _mod(a: float, b: float) -> float:
    return math.fmod(a, b) if b else math.nan


_PY_GLOBALS["_mod"] = _mod


def _to_python(node: Node, params: Dict[str, str]) -> str:
    if isinstance(node, Num):
        return repr(float(node.value))
    if isinstance(node, Var):
        if node.name in params:
            return params[node.name]
        if node.name in CONSTANTS:
            return "_c_" + node.name
        raise ExprError(f"variable inconnue « {node.name} » "
                        f"(variables permises : {', '.join(params) or 'aucune'})")
    if isinstance(node, Neg):
        return f"(-{_to_python(node.arg, params)})"
    if isinstance(node, Bin):
        a, b = _to_python(node.left, params), _to_python(node.right, params)
        if node.op in ("+", "-", "*", "/"):
            return f"({a} {node.op} {b})"
        if node.op == "^":
            return f"_pow({a}, {b})"
        if node.op == "%":
            return f"_mod({a}, {b})"
        if node.op in COMPARISONS:
            pyop = "==" if node.op == "=" else node.op
            return f"(1.0 if {a} {pyop} {b} else 0.0)"
        raise ExprError(f"opérateur « {node.op} » non numérique")
    if isinstance(node, Call):
        name = node.name.lower()
        spec = FUNCTIONS.get(name)
        if spec is None:
            raise ExprError(f"fonction inconnue « {node.name} »")
        _, lo, hi = spec
        if len(node.args) < lo or (hi >= 0 and len(node.args) > hi):
            raise ExprError(f"nombre d'arguments incorrect pour {name}()")
        args = ", ".join(_to_python(a, params) for a in node.args)
        return f"_f_{name}({args})"
    if isinstance(node, Str):
        raise ExprError("les chaînes ne sont pas permises dans une expression numérique")
    raise ExprError("les plages ne sont pas permises ici")


class CompiledExpr:
    """Expression compilée en fonction Python rapide et sûre (nan en cas d'erreur)."""

    def __init__(self, node: Node, params: Sequence[str], source: str = "") -> None:
        self.node = node
        self.params = tuple(params)
        self.source = source or to_string(node)
        mapping = {p: f"v_{i}" for i, p in enumerate(self.params)}
        body = _to_python(node, mapping)
        arglist = ", ".join(mapping[p] for p in self.params)
        code = (f"def __expr({arglist}):\n"
                f"    try:\n"
                f"        return _float({body})\n"
                f"    except (ArithmeticError, ValueError, TypeError):\n"
                f"        return _nan\n")
        namespace = dict(_PY_GLOBALS)
        exec(compile(code, "<expression>", "exec"), namespace)  # code généré depuis l'AST validé
        self._fn: Callable[..., float] = namespace["__expr"]

    def __call__(self, *args: float) -> float:
        return self._fn(*args)

    def derivative(self, var: Optional[str] = None) -> "CompiledExpr":
        var = var or self.params[0]
        return CompiledExpr(simplify(derive(self.node, var)), self.params)

    def __repr__(self) -> str:
        return f"CompiledExpr({self.source!r}, params={self.params})"


def compile_expr(source: Any, params: Sequence[str] = ("x",), *,
                 implicit_mul: bool = True) -> CompiledExpr:
    node = parse(source, implicit_mul=implicit_mul) if isinstance(source, str) else source
    return CompiledExpr(node, params, source if isinstance(source, str) else "")


# ---------------------------------------------------------------------------
# Dérivation symbolique
# ---------------------------------------------------------------------------

def _pw(a: Node, b: Node) -> Node:
    return Bin("^", a, b)


def _mul(a: Node, b: Node) -> Node:
    return Bin("*", a, b)


def _div(a: Node, b: Node) -> Node:
    return Bin("/", a, b)


def _add(a: Node, b: Node) -> Node:
    return Bin("+", a, b)


def _sub(a: Node, b: Node) -> Node:
    return Bin("-", a, b)


def _fn(name: str, *args: Node) -> Node:
    return Call(name, tuple(args))


def derive(node: Node, var: str) -> Node:
    """Dérivée symbolique de ``node`` par rapport à ``var`` (non simplifiée)."""
    if isinstance(node, (Num, Str)):
        return ZERO
    if isinstance(node, Var):
        return ONE if node.name == var else ZERO
    if not contains_var(node, var):
        return ZERO
    if isinstance(node, Neg):
        return Neg(derive(node.arg, var))
    if isinstance(node, Bin):
        u, v = node.left, node.right
        du, dv = derive(u, var), derive(v, var)
        op = node.op
        if op in ("+", "-"):
            return Bin(op, du, dv)
        if op == "*":
            return _add(_mul(du, v), _mul(u, dv))
        if op == "/":
            return _div(_sub(_mul(du, v), _mul(u, dv)), _pw(v, TWO))
        if op == "^":
            if not contains_var(v, var):
                return _mul(_mul(v, _pw(u, _sub(v, ONE))), du)
            if not contains_var(u, var):
                return _mul(_mul(node, _fn("ln", u)), dv)
            return _mul(node, _add(_mul(dv, _fn("ln", u)), _div(_mul(v, du), u)))
        if op == "%":
            return du
        if op in COMPARISONS:
            return ZERO
        raise ExprError(f"dérivation impossible pour l'opérateur {op}")
    if isinstance(node, Call):
        name = node.name.lower()
        if name == "log" and len(node.args) == 2:
            return derive(_div(_fn("ln", node.args[0]), _fn("ln", node.args[1])), var)
        if len(node.args) != 1:
            raise ExprError(f"dérivée de {name}() non prise en charge")
        u = node.args[0]
        du = derive(u, var)
        rule = _CHAIN_RULES.get(name)
        if rule is None:
            raise ExprError(f"dérivée de {name}() non prise en charge")
        return _mul(rule(u), du)
    raise ExprError("dérivation impossible pour ce type d'expression")


_CHAIN_RULES: Dict[str, Callable[[Node], Node]] = {
    "sin": lambda u: _fn("cos", u),
    "cos": lambda u: Neg(_fn("sin", u)),
    "tan": lambda u: _div(ONE, _pw(_fn("cos", u), TWO)),
    "asin": lambda u: _div(ONE, _fn("sqrt", _sub(ONE, _pw(u, TWO)))),
    "arcsin": lambda u: _div(ONE, _fn("sqrt", _sub(ONE, _pw(u, TWO)))),
    "acos": lambda u: Neg(_div(ONE, _fn("sqrt", _sub(ONE, _pw(u, TWO))))),
    "arccos": lambda u: Neg(_div(ONE, _fn("sqrt", _sub(ONE, _pw(u, TWO))))),
    "atan": lambda u: _div(ONE, _add(ONE, _pw(u, TWO))),
    "arctan": lambda u: _div(ONE, _add(ONE, _pw(u, TWO))),
    "sinh": lambda u: _fn("cosh", u),
    "cosh": lambda u: _fn("sinh", u),
    "tanh": lambda u: _sub(ONE, _pw(_fn("tanh", u), TWO)),
    "asinh": lambda u: _div(ONE, _fn("sqrt", _add(_pw(u, TWO), ONE))),
    "acosh": lambda u: _div(ONE, _fn("sqrt", _sub(_pw(u, TWO), ONE))),
    "atanh": lambda u: _div(ONE, _sub(ONE, _pw(u, TWO))),
    "sec": lambda u: _mul(_fn("sec", u), _fn("tan", u)),
    "csc": lambda u: Neg(_mul(_fn("csc", u), _fn("cot", u))),
    "cot": lambda u: Neg(_pw(_fn("csc", u), TWO)),
    "exp": lambda u: _fn("exp", u),
    "ln": lambda u: _div(ONE, u),
    "log": lambda u: _div(ONE, _mul(u, _fn("ln", Num(10.0)))),
    "log2": lambda u: _div(ONE, _mul(u, _fn("ln", TWO))),
    "sqrt": lambda u: _div(ONE, _mul(TWO, _fn("sqrt", u))),
    "cbrt": lambda u: _div(ONE, _mul(Num(3.0), _pw(_fn("cbrt", u), TWO))),
    "abs": lambda u: _fn("sign", u),
    "sign": lambda u: ZERO,
    "floor": lambda u: ZERO,
    "ceil": lambda u: ZERO,
    "round": lambda u: ZERO,
    "step": lambda u: ZERO,
    "frac": lambda u: ONE,
    "sinc": lambda u: _div(_sub(_mul(u, _fn("cos", u)), _fn("sin", u)), _pw(u, TWO)),
}


# ---------------------------------------------------------------------------
# Simplification algébrique
# ---------------------------------------------------------------------------

def _is_num(n: Node, value: Optional[float] = None) -> bool:
    return isinstance(n, Num) and (value is None or n.value == value)


def _fold(op: str, a: float, b: float) -> Optional[float]:
    try:
        if op in ("+", "-", "*"):
            r = _apply_bin(op, a, b)
        elif op == "/":
            if b == 0:
                return None
            r = a / b
            if r != int(r):
                return None
        elif op == "^":
            if a != int(a) or b != int(b) or b < 0 or b > 64:
                return None
            r = float(a ** b)
            if abs(r) > 1e15:
                return None
        else:
            return None
    except (ArithmeticError, ValueError):
        return None
    if isinstance(r, complex) or math.isnan(r) or math.isinf(r):
        return None
    return float(r)


def _simplify_once(node: Node) -> Node:
    if isinstance(node, Neg):
        a = _simplify_once(node.arg)
        if isinstance(a, Num):
            return Num(-a.value)
        if isinstance(a, Neg):
            return a.arg
        if isinstance(a, Bin) and a.op == "-":
            return Bin("-", a.right, a.left)
        if isinstance(a, Bin) and a.op in ("*", "/") and isinstance(a.left, Num):
            return Bin(a.op, Num(-a.left.value), a.right)
        return Neg(a)
    if isinstance(node, Call):
        return Call(node.name, tuple(_simplify_once(a) for a in node.args))
    if not isinstance(node, Bin):
        return node

    op = node.op
    a, b = _simplify_once(node.left), _simplify_once(node.right)
    if isinstance(a, Num) and isinstance(b, Num):
        folded = _fold(op, a.value, b.value)
        if folded is not None:
            return Num(folded)

    if op == "+":
        if _is_num(a, 0):
            return b
        if _is_num(b, 0):
            return a
        if isinstance(b, Neg):
            return Bin("-", a, b.arg)
        if isinstance(b, Num) and b.value < 0:
            return Bin("-", a, Num(-b.value))
        if isinstance(a, Neg):
            return Bin("-", b, a.arg)
        if a == b:
            return Bin("*", TWO, a)
        if isinstance(a, Num) and not isinstance(b, Num):
            return Bin("+", b, a)  # constantes en fin de somme
    elif op == "-":
        if _is_num(b, 0):
            return a
        if _is_num(a, 0):
            return Neg(b)
        if a == b:
            return ZERO
        if isinstance(b, Neg):
            return Bin("+", a, b.arg)
        if isinstance(b, Num) and b.value < 0:
            return Bin("+", a, Num(-b.value))
    elif op == "*":
        if _is_num(a, 0) or _is_num(b, 0):
            return ZERO
        if _is_num(a, 1):
            return b
        if _is_num(b, 1):
            return a
        if _is_num(a, -1):
            return Neg(b)
        if _is_num(b, -1):
            return Neg(a)
        if isinstance(b, Num) and not isinstance(a, Num):
            return Bin("*", b, a)  # coefficients en tête
        if isinstance(a, Neg):
            return Neg(Bin("*", a.arg, b))
        if isinstance(b, Neg):
            return Neg(Bin("*", a, b.arg))
        if isinstance(a, Num) and isinstance(b, Bin) and b.op == "*" and isinstance(b.left, Num):
            folded = _fold("*", a.value, b.left.value)
            if folded is not None:
                return Bin("*", Num(folded), b.right)
        if isinstance(a, Num) and isinstance(b, Bin) and b.op == "/" and isinstance(b.left, Num):
            folded = _fold("*", a.value, b.left.value)
            if folded is not None:
                return Bin("/", Num(folded), b.right)
        if a == b:
            return Bin("^", a, TWO)
        if isinstance(b, Bin) and b.op == "^" and b.left == a and isinstance(b.right, Num):
            return Bin("^", a, Num(b.right.value + 1))
        if isinstance(a, Bin) and a.op == "^" and a.left == b and isinstance(a.right, Num):
            return Bin("^", b, Num(a.right.value + 1))
        if isinstance(a, Bin) and a.op == "/" and _is_num(a.left, 1):
            return Bin("/", b, a.right)
        if isinstance(b, Bin) and b.op == "/" and _is_num(b.left, 1):
            return Bin("/", a, b.right)
    elif op == "/":
        if _is_num(a, 0):
            return ZERO
        if _is_num(b, 1):
            return a
        if a == b:
            return ONE
        if isinstance(a, Neg):
            return Neg(Bin("/", a.arg, b))
        if isinstance(a, Bin) and a.op == "*" and isinstance(a.left, Num) and isinstance(b, Num):
            folded = _fold("/", a.left.value, b.value)
            if folded is not None:
                return Bin("*", Num(folded), a.right)
    elif op == "^":
        if _is_num(b, 0):
            return ONE
        if _is_num(b, 1):
            return a
        if _is_num(a, 1):
            return ONE
        if isinstance(a, Bin) and a.op == "^" and isinstance(a.right, Num) and isinstance(b, Num):
            return Bin("^", a.left, Num(a.right.value * b.value))
        if isinstance(a, Call) and a.name == "sqrt" and _is_num(b, 2):
            return a.args[0]
    return Bin(op, a, b)


def simplify(node: Node, passes: int = 12) -> Node:
    for _ in range(passes):
        new = _simplify_once(node)
        if new == node:
            return new
        node = new
    return node


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

_PREC = {"=": 10, "!=": 10, "<": 10, ">": 10, "<=": 10, ">=": 10, "&": 15,
         "+": 20, "-": 20, "*": 30, "/": 30, "%": 30, "^": 50}


def _prec(node: Node) -> int:
    if isinstance(node, Bin):
        return _PREC[node.op]
    if isinstance(node, Neg):
        return 40
    if isinstance(node, Num) and node.value < 0:
        return 40
    return 100


def to_string(node: Node) -> str:
    if isinstance(node, Num):
        return format_number(node.value)
    if isinstance(node, Str):
        return '"' + node.value.replace('"', '""') + '"'
    if isinstance(node, Var):
        return node.name
    if isinstance(node, Range):
        return f"{node.start}:{node.end}"
    if isinstance(node, Call):
        if node.name == "fact" and len(node.args) == 1:
            inner = to_string(node.args[0])
            return (inner if _prec(node.args[0]) >= 100 else f"({inner})") + "!"
        return f"{node.name}({', '.join(to_string(a) for a in node.args)})"
    if isinstance(node, Neg):
        inner = to_string(node.arg)
        if _prec(node.arg) < 30 or isinstance(node.arg, Neg) or _prec(node.arg) == 40:
            inner = f"({inner})"
        return "-" + inner
    if isinstance(node, Bin):
        p = _PREC[node.op]
        left, right = to_string(node.left), to_string(node.right)
        lp, rp = _prec(node.left), _prec(node.right)
        if lp < p or (node.op == "^" and lp <= p):
            left = f"({left})"
        if rp < p or (rp == p and node.op in ("-", "/", "%", "^") and node.op != "^") \
                or (rp == 40 and node.op != "+"):
            right = f"({right})"
        if node.op in ("+", "-") or node.op in COMPARISONS or node.op == "&":
            return f"{left} {node.op} {right}"
        return f"{left}{node.op}{right}"
    return repr(node)
