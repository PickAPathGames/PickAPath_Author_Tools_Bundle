# validators/expr.py
import re
import ast
from typing import Tuple, Set

class ExprError(Exception):
    pass

# Allow +=, -=, *=, /=, =  and fairmath operators "%+" and "%-"
_MVAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:(\+=|-=|\*=|/=|=|%\+|%-)\s*(.+))?$")
_MVAR_LEGACY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*([+\-*/])\s*(.+)$")
_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_RESERVED = {"and", "or", "not", "true", "false", "None"}

def parse_mvar_args(argtext: str) -> Tuple[str, str, str]:
    if not argtext or not argtext.strip():
        raise ExprError("empty arguments")
    
    s = argtext.strip()
    m = _MVAR_RE.match(s)
    if m:
        name = m.group(1)
        op = m.group(2)
        rhs = m.group(3)
        if op is None:
            raise ExprError("missing assignment operator")
        return name, op, rhs.strip() if rhs is not None else ""

    m2 = _MVAR_LEGACY.match(s)
    if m2:
        name = m2.group(1)
        simple_op = m2.group(2)
        rhs = m2.group(3)
        op = "+=" if simple_op == "+" else ("-=" if simple_op == "-" else None)
        if op is None:
            raise ExprError("legacy operator not supported")
        return name, op, rhs.strip()

    raise ExprError(f"Can't parse mvar args: '{argtext}'")

def extract_names(expr: str) -> Set[str]:
    """
    Return the set of identifier names used in `expr`, ignoring string literals.
    Uses AST parsing so quoted strings won't produce variable names.
    """
    if not expr or not expr.strip():
        return set()

    try:
        node = ast.parse(expr, mode="eval")
    except Exception:
        # fallback: try parsing as simple expression, but return empty on error
        return set()

    names = set()
    for n in ast.walk(node):
        # Only collect Name nodes (identifiers) used as variables
        if isinstance(n, ast.Name):
            names.add(n.id)
    return names

def parse_literal(token: str):
    lower = token.lower()
    if lower in ("true", "yes"):
        return 1
    if lower in ("false", "no"):
        return 0
    # fallback to int/float parsing or string...
    try:
        if "." in token:
            return float(token)
        return int(token)
    except Exception:
        return token


