# validators/static_expr.py
"""
Static expression type inference & validation.

Public API:
  analyze_expression(expr: str, known_types: Dict[str,str]) -> Dict
    returns:
      {
        "type": "int"|"float"|"str"|"bool"|"toggle"|"any"|None,
        "errors": [ ... ],
        "unknown_names": set([...]),
      }

Rules / notes:
 - Allowed operators:
      arithmetic: + - * / // % ** (power)
      comparisons: == != < <= > >=
      boolean: and or not
      parentheses allowed
 - Literals:
      integers -> int
      floats -> float
      "double-quoted strings" -> str (we treat string tokens in expr as invalid unless quoted)
      True/False -> bool
 - If an identifier name appears: its type is looked up in known_types mapping.
   If missing, it's reported in unknown_names and returned type is None (conservative).
 - bool values are allowed in arithmetic context (treated like 0/1) - numeric ops
   accept bools.
 - toggle types are treated as bool for condition contexts, and as int (0/1) in arithmetic
   contexts (i.e. hybrid).
 - This module is conservative: when unknowns exist we do not claim the type; we
   emit an error list or unknown_names for the caller to decide.
"""

import ast
from typing import Dict, Any, Set, Tuple, List

# Basic type names used here
TYPE_INT = "int"
TYPE_FLOAT = "float"
TYPE_STR = "str"
TYPE_BOOL = "bool"
TYPE_TOGGLE = "toggle"
TYPE_ANY = "any"

_ALLOWED = (
    ast.Expression, ast.Expr, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Name, ast.Load, ast.Constant,
    ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Call,
)
RESERVED_PYTHON = {"try", "except", "finally", "with", "as", "import", "from", "def", "class", "del"}

class ExprAnalysisError(Exception):
    pass

def _is_literal_node(n: ast.AST) -> bool:
    return isinstance(n, ast.Constant)


def _literal_type(n: ast.AST) -> str:
    if not isinstance(n, ast.Constant):
        return TYPE_ANY

    v = n.value
    if isinstance(v, bool):
        return TYPE_BOOL
    if isinstance(v, int):
        return TYPE_INT
    if isinstance(v, float):
        return TYPE_FLOAT
    if isinstance(v, str):
        return TYPE_STR
    return TYPE_ANY


def _promote_numeric(t1: str, t2: str) -> str:
    """
    Numeric promotion:
      int + int -> int
      int + float -> float
      float + float -> float
      bool treated as int for arithmetic
      toggle treated as int in arithmetic
    """
    if TYPE_STR in (t1, t2):
        return TYPE_ANY
    numeric = {TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_TOGGLE}
    if t1 in numeric and t2 in numeric:
        if TYPE_FLOAT in (t1, t2):
            return TYPE_FLOAT
        # bool/toggle/int -> int
        return TYPE_INT
    return TYPE_ANY

def _is_boolean_context(node: ast.AST) -> bool:
    """Heuristic: used for comparisons and boolean ops; allows bool/toggle as valid."""
    return isinstance(node, (ast.BoolOp, ast.UnaryOp, ast.Compare))

def _check_allowed_nodes(node: ast.AST):
    SAFE_FUNCTIONS = {"round", "int", "abs", "min", "max"} 
    
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id in SAFE_FUNCTIONS:
                continue 
            raise ExprAnalysisError(f"Function call '{n.func.id if isinstance(n.func, ast.Name) else 'unknown'}' is not allowed in static expressions")
        
        if isinstance(n, ast.Attribute):
            raise ExprAnalysisError("attribute access is not allowed in static expressions")
        if isinstance(n, ast.Subscript):
            raise ExprAnalysisError("indexing/subscript is not allowed in static expressions")
        
        if not isinstance(n, _ALLOWED):
            raise ExprAnalysisError(f"disallowed expression node: {type(n).__name__}")

def analyze_expression(expr: str, known_types: Dict[str, str]) -> Dict[str, Any]:
    """
    Analyze expr using known_types mapping (name -> type string).
    Returns a dict: { "type": type_str or None, "errors": [...], "unknown_names": set(...) }
    If unknown_names non-empty: type will be None (conservative).
    """
    result = {"type": None, "errors": [], "unknown_names": set()}

    if not expr or not expr.strip():
        return result

    # We replace 'try' with '_var_try' so Python doesn't panic
    safe_expr = expr
    for word in RESERVED_PYTHON:
        # Regex ensures we only replace the whole word 'try', not 'country'
        safe_expr = re.sub(r'\b' + word + r'\b', f'_var_{word}', safe_expr)
    
    # We also need a version of known_types that has the new names
    safe_known_types = {}
    for k, v in known_types.items():
        if k in RESERVED_PYTHON:
            safe_known_types[f'_var_{k}'] = v
        else:
            safe_known_types[k] = v

    try:
        # 🔑 Parse only once!
        node = ast.parse(safe_expr, mode="eval")
        _check_allowed_nodes(node)
    except SyntaxError as se:
        result["errors"].append(f"syntax error: {se}")
        return result
    except ExprAnalysisError as e:
        result["errors"].append(str(e))
        return result

    # check allowed nodes
    try:
        _check_allowed_nodes(node)
    except ExprAnalysisError as e:
        result["errors"].append(str(e))
        return result

    # helper: recursively infer type
    def infer(n: ast.AST) -> str:
        # -------- SPECIAL CASE: True/False should be boolean literals, not unknown names --------
        if isinstance(n, ast.Name):
            nm = n.id
            if nm in ("True", "False"):
                return TYPE_BOOL
            # USE safe_known_types here
            if nm in safe_known_types:
                return safe_known_types[nm]
            else:
                # If it's a 'safe' name like _var_try, report the original name 'try'
                orig_name = nm.replace("_var_", "") if nm.startswith("_var_") else nm
                result["unknown_names"].add(orig_name)
                return None

        # literals
        if _is_literal_node(n):
            return _literal_type(n)
        # Name: use known_types or mark unknown
        if isinstance(n, ast.Name):
            nm = n.id
            if nm in known_types:
                return known_types[nm]
            else:
                result["unknown_names"].add(nm)
                return None  # unknown
        # Unary ops: +x, -x, not x
        if isinstance(n, ast.UnaryOp):
            t = infer(n.operand)
            if isinstance(n.op, (ast.UAdd, ast.USub)):
                # numeric unary -> require numeric-ish
                if t is None:
                    return None
                if t in (TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_TOGGLE):
                    return t if t != TYPE_BOOL else TYPE_INT
                return TYPE_ANY
            if isinstance(n.op, ast.Not):
                # not -> boolean
                return TYPE_BOOL
        # BoolOp: and/or
        if isinstance(n, ast.BoolOp):
            # children must be boolean-compatible
            child_types = []
            for val in n.values:
                ct = infer(val)
                child_types.append(ct)
            # if any unknown -> unknown
            if any(ct is None for ct in child_types):
                return None
            # boolean operators accept bool/toggle and also comparisons that return bool
            return TYPE_BOOL
        # BinOp: arithmetic + - * / // % **
        if isinstance(n, ast.BinOp):
            left_t = infer(n.left)
            right_t = infer(n.right)
            # short-circuit unknowns
            if left_t is None or right_t is None:
                return None
            # string concatenation: + with strings
            if isinstance(n.op, ast.Add):
                if left_t == TYPE_STR and right_t == TYPE_STR:
                    return TYPE_STR
            # numeric promotion (allow bool/toggle in numeric ops)
            if left_t in (TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_TOGGLE) and right_t in (TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_TOGGLE):
                return _promote_numeric(left_t, right_t)
            # otherwise
            result["errors"].append(f"Invalid operands for '{type(n.op).__name__}': {left_t} vs {right_t}")
            return TYPE_ANY
        # Compare: returns boolean, operands must be comparable
        if isinstance(n, ast.Compare):
            # left and list of comparators; check pairwise types
            left_t = infer(n.left)
            if left_t is None:
                return None
            for comp in n.comparators:
                ct = infer(comp)
                if ct is None:
                    return None
                # If either side is string, only allow equality/inequality
                if TYPE_STR in (left_t, ct):
                    # allow Eq/NotEq; other comparisons are invalid for strings
                    for op in n.ops:
                        if not isinstance(op, (ast.Eq, ast.NotEq)):
                            result["errors"].append("Invalid comparison between string and non-string using ordering operator")
                            return TYPE_BOOL
                # numeric comparisons: allow int/float/bool/toggle
                # mixed numeric types OK
            return TYPE_BOOL
        # Fallback
        result["errors"].append(f"Unsupported expression node: {type(n).__name__}")
        return TYPE_ANY

    top_type = infer(node.body)  # type or None
    # If there are unknown names, be conservative: return None type & include unknowns
    if result["unknown_names"]:
        result["type"] = None
    else:
        result["type"] = top_type

    return result
