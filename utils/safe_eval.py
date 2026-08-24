# utils/safe_eval.py
import ast
import operator
import math
import re
from function_registry import FUNCTION_REGISTRY

# --- NEW OVERRIDE LOGIC ---

RESERVED_WORDS = {
    # Structural keywords
    "try", "except", "finally", "with", "as", "import", "from", 
    "def", "class", "del", "return", "yield", "pass", "break", 
    "continue", "lambda", "global", "nonlocal", "assert", 
    "await", "async", "for", "while", "if", "elif", "else",
    # Built-ins that conflict with safe functions
    "int", "str", "float", "bool", "list", "dict", "set", "tuple",
    "min", "max", "sum", "len", "round", "abs"
}

RESERVED_PATTERN = re.compile(r'\b(' + '|'.join(RESERVED_WORDS) + r')\b')

class WrapperVars(dict):
    def __init__(self, raw_vars):
        self.raw = raw_vars

    def __contains__(self, key):
        if key.startswith('__res_'):
            return key[6:] in self.raw
        return key in self.raw

    def __getitem__(self, key):
        actual_key = key[6:] if key.startswith('__res_') else key
        try:
            return self.raw[actual_key]
        except KeyError:
            return 0  # Fallback matches previous behavior

SAFE_FUNCTIONS = {
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
    "round": round, "int": int, "float": float, "str": str, "bool": bool,
}
SAFE_FUNCTIONS.update(FUNCTION_REGISTRY)

BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
}

UNARY_OPS = {
    ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_,
}

CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}

def _eval_node(node, variables):
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        raise ValueError(f"Unknown variable: {node.id}")
    elif isinstance(node, ast.BinOp):
        return BIN_OPS[type(node.op)](
            _eval_node(node.left, variables),
            _eval_node(node.right, variables)
        )
    elif isinstance(node, ast.UnaryOp):
        return UNARY_OPS[type(node.op)](_eval_node(node.operand, variables))
    elif isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_node(v, variables) for v in node.values)
        elif isinstance(node.op, ast.Or):
            return any(_eval_node(v, variables) for v in node.values)
    elif isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, variables)
            if not CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls allowed.")
        func_name = node.func.id
        if func_name not in SAFE_FUNCTIONS:
            raise ValueError(f"Unsafe function: {func_name}")
        args = [_eval_node(arg, variables) for arg in node.args]
        return SAFE_FUNCTIONS[func_name](*args)
    elif isinstance(node, ast.List):
        return [_eval_node(elem, variables) for elem in node.elts]
    elif isinstance(node, ast.Tuple):
        return tuple(_eval_node(elem, variables) for elem in node.elts)
    else:
        raise ValueError(f"Disallowed AST node: {type(node)}")


def safe_eval_expr(expr, variables):
    if not expr or not isinstance(expr, str):
        return None

    fmt = None
    if ":" in expr and not expr.strip().startswith(":"):
        parts = expr.split(":", 1)
        expr = parts[0]
        fmt = parts[1]

    expr = expr.replace("true", "True").replace("false", "False")
    
    # 1. Mask restricted words
    safe_expr = RESERVED_PATTERN.sub(r'__res_\1', expr)
    
    # 2. Wrap variables to unmask during fetch
    safe_vars = WrapperVars(variables)

    try:
        tree = ast.parse(safe_expr.strip(), mode='eval')
        val = _eval_node(tree.body, safe_vars)
        if fmt:
            return f"{val:{fmt}}"
        return val
    except Exception as e:
        print(f"[DEBUG][safe_eval] Error evaluating '{expr}': {e}")
        return 0