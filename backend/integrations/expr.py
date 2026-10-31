"""Custom mapping expressions, e.g. `lower(first_name) + "." + lower(last_name)`.

HR writes these, so they never reach eval(): the expression is parsed with `ast` and only the node types
and functions listed here are allowed. Source fields are plain names. No attributes, imports, lambdas or
comprehensions, so there's no way out to Python itself.
"""
import ast
import re
import unicodedata
from datetime import date, datetime


class ExpressionError(ValueError):
    pass


def _s(v) -> str:
    if v is None:
        return ""
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v)


def _slug(v, sep: str = "") -> str:
    ascii_ = unicodedata.normalize("NFKD", _s(v)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", sep, ascii_.lower()).strip(sep)


def _date(v, fmt: str = "%d %b %Y") -> str:
    s = _s(v)
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s).strftime(fmt)
    except ValueError:
        raise ExpressionError(f"'{s}' isn't a date (expected YYYY-MM-DD)")


FUNCTIONS = {
    "lower": lambda v: _s(v).lower(),
    "upper": lambda v: _s(v).upper(),
    "title": lambda v: _s(v).title(),
    "trim": lambda v: _s(v).strip(),
    "slug": _slug,
    "initials": lambda v: "".join(w[0] for w in _s(v).split() if w).upper(),
    "first": lambda v, n=1: _s(v)[: int(n)],
    "last": lambda v, n=1: _s(v)[-int(n):] if int(n) else "",
    "word": lambda v, i=0: (_s(v).split() or [""])[int(i)] if int(i) < len(_s(v).split()) else "",
    "split": lambda v, sep, i=0: (_s(v).split(sep) + [""])[int(i)] if int(i) < len(_s(v).split(sep)) else "",
    "replace": lambda v, old, new: _s(v).replace(_s(old), _s(new)),
    "concat": lambda *a: "".join(_s(x) for x in a),
    "join": lambda sep, *a: _s(sep).join(_s(x) for x in a if _s(x)),
    "coalesce": lambda *a: next((x for x in a if _s(x)), ""),
    "length": lambda v: len(_s(v)),
    "pad": lambda v, n, ch="0": _s(v).rjust(int(n), _s(ch)[:1] or "0"),
    "date": _date,
    "today": lambda fmt="%Y-%m-%d": date.today().strftime(fmt),
    "number": lambda v: int(re.sub(r"\D", "", _s(v)) or 0),
}
# Shown in the expression builder, so keep them short and true.
HELP = {
    "lower": "lower(first_name)", "upper": "upper(department_name)", "title": "title(first_name)", "trim": "trim(email)",
    "slug": 'slug(last_name) or slug(last_name, "-")', "initials": "initials(first_name + ' ' + last_name)",
    "first": "first(first_name, 1)  → first letter", "last": "last(phone, 4)", "word": "word(designation, 0)",
    "split": 'split(email, "@", 1)', "replace": 'replace(phone, " ", "")', "concat": 'concat(first_name, ".", last_name)',
    "join": 'join(" ", first_name, last_name)', "coalesce": 'coalesce(designation, "Associate")', "length": "length(last_name)",
    "pad": "pad(number(candidate_id), 6)", "date": 'date(joining_date, "%d/%m/%Y")', "today": "today()",
    "number": "number(grade)  → 3 for G3",
}

_ALLOWED = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Mod,
            ast.Call, ast.IfExp, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
            ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Subscript, ast.Slice, ast.List, ast.Tuple)
MAX_LEN = 500


def check(expression: str, fields: set[str]) -> None:
    """Raise ExpressionError when the expression isn't allowed or names something unknown."""
    if not expression or not expression.strip():
        raise ExpressionError("Write an expression")
    if len(expression) > MAX_LEN:
        raise ExpressionError(f"Keep expressions under {MAX_LEN} characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"That expression doesn't parse: {e.msg} at character {e.offset}")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ExpressionError(f"{type(node).__name__} isn't allowed in expressions")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS):
            name = node.func.id if isinstance(node.func, ast.Name) else "that"
            raise ExpressionError(f"There's no function called {name}. Use one of: {', '.join(FUNCTIONS)}")
        if isinstance(node, ast.Call) and node.keywords:
            raise ExpressionError("Pass function arguments by position")
        if isinstance(node, ast.Name) and node.id not in FUNCTIONS and node.id not in fields:
            raise ExpressionError(f"There's no source field called {node.id}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            for side in (node.left, node.right):  # "a" * 10**9 would eat memory
                if isinstance(side, ast.Constant) and isinstance(side.value, int) and side.value > 100:
                    raise ExpressionError("Multiplying by more than 100 isn't allowed")


def evaluate(expression: str, values: dict, fields: set[str] | None = None):
    check(expression, set(fields if fields is not None else values))
    return _eval(ast.parse(expression.strip(), mode="eval").body, values)


def _eval(n, env):
    match n:
        case ast.Constant():
            return n.value
        case ast.Name():
            return env.get(n.id, "")
        case ast.BinOp():
            a, b = _eval(n.left, env), _eval(n.right, env)
            if isinstance(n.op, ast.Add):
                return a + b if isinstance(a, (int, float)) and isinstance(b, (int, float)) else _s(a) + _s(b)
            try:
                return {ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Mod: lambda: a % b}[type(n.op)]()
            except (TypeError, ZeroDivisionError) as e:
                raise ExpressionError(str(e))
        case ast.Call():
            args = [_eval(a, env) for a in n.args]
            try:
                out = FUNCTIONS[n.func.id](*args)
            except ExpressionError:
                raise
            except (TypeError, ValueError, IndexError) as e:
                raise ExpressionError(f"{n.func.id}(): {e}. Example: {HELP[n.func.id]}")
            if isinstance(out, str) and len(out) > 5000:
                raise ExpressionError("The result is too long")
            return out
        case ast.IfExp():
            return _eval(n.body, env) if _eval(n.test, env) else _eval(n.orelse, env)
        case ast.Compare():
            left = _eval(n.left, env)
            for op, comp in zip(n.ops, n.comparators):
                right = _eval(comp, env)
                ok = {ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right, ast.Lt: lambda: left < right,
                      ast.LtE: lambda: left <= right, ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right,
                      ast.In: lambda: left in right, ast.NotIn: lambda: left not in right}[type(op)]
                try:
                    if not ok():
                        return False
                except TypeError as e:
                    raise ExpressionError(str(e))
                left = right
            return True
        case ast.BoolOp():
            vals = [_eval(v, env) for v in n.values]
            if isinstance(n.op, ast.And):
                return next((v for v in vals if not v), vals[-1])
            return next((v for v in vals if v), vals[-1])
        case ast.UnaryOp():
            v = _eval(n.operand, env)
            return (not v) if isinstance(n.op, ast.Not) else -v
        case ast.Subscript():
            v = _s(_eval(n.value, env))
            if isinstance(n.slice, ast.Slice):
                lo = _eval(n.slice.lower, env) if n.slice.lower else None
                hi = _eval(n.slice.upper, env) if n.slice.upper else None
                return v[lo:hi]
            try:
                return v[_eval(n.slice, env)]
            except (IndexError, TypeError):
                return ""
        case ast.List() | ast.Tuple():
            return [_eval(e, env) for e in n.elts]
    raise ExpressionError("Unsupported expression")


# ---------- unique email ----------

EMAIL_PRESETS = {
    "first.last": 'slug(first_name) + "." + slug(last_name)',
    "firstlast": "slug(first_name) + slug(last_name)",
    "f.last": 'first(slug(first_name), 1) + "." + slug(last_name)',
    "first.l": 'slug(first_name) + "." + first(slug(last_name), 1)',
    "first_last": 'slug(first_name) + "_" + slug(last_name)',
}
_LOCAL = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")


def unique_email(expression: str, domain: str, values: dict, taken, fields: set[str] | None = None) -> str:
    """Build local@domain from the expression; on a clash add 2, 3, … (john.doe2). `taken(email)` says if one is used."""
    domain = (domain or "").strip().lower().lstrip("@")
    if not _DOMAIN.match(domain):
        raise ExpressionError(f"'{domain}' isn't an email domain like azentio.com")
    local = re.sub(r"\.{2,}", ".", _s(evaluate(expression, values, fields)).strip().lower()).strip("._-")
    if not local:
        raise ExpressionError("The email rule produced an empty name for this candidate")
    if not _LOCAL.match(local):
        raise ExpressionError(f"'{local}' can't be the start of an email address; wrap the fields in slug()")
    for n in range(1, 1000):
        email = f"{local}{n if n > 1 else ''}@{domain}"
        if not taken(email):
            return email
    raise ExpressionError("Couldn't find a free address after 999 tries")


if __name__ == "__main__":
    v = {"first_name": "Ánanya", "last_name": "Krishnan K", "joining_date": "2026-12-01", "grade": "G3", "designation": ""}
    assert evaluate('lower(first_name) + "." + slug(last_name)', v) == "ánanya.krishnank"
    assert evaluate(EMAIL_PRESETS["f.last"], v) == "a.krishnank"
    assert evaluate('coalesce(designation, "Associate")', v) == "Associate"
    assert evaluate('"Senior" if number(grade) >= 5 else "Associate"', v) == "Associate"
    assert evaluate('date(joining_date, "%d/%m/%Y")', v) == "01/12/2026"
    assert evaluate("last_name[:3]", v) == "Kri"
    for bad in ("__import__('os')", "first_name.upper()", "[x for x in first_name]", "nope(first_name)", "unknown_field", "'a' * 999"):
        try:
            evaluate(bad, v)
            raise SystemExit(f"should have failed: {bad}")
        except ExpressionError:
            pass
    used = {"ananya.krishnank@azentio.com"}
    assert unique_email(EMAIL_PRESETS["first.last"], "azentio.com", v, used.__contains__) == "ananya.krishnank2@azentio.com"
    print("expr ok")
