# function_registry.py
"""
Global registry for custom functions that can be safely used
in expressions or interpolations.
Allows dynamic registration of new functions while providing a set of safe defaults.
"""

import math
import random

FUNCTION_REGISTRY = {}

def register_function(name, func):
    """Register a custom function."""
    FUNCTION_REGISTRY[name] = func

def get_registered_function(name):
    """Retrieve a registered function by name."""
    return FUNCTION_REGISTRY.get(name)

def list_registered_functions():
    """Return all registered function names."""
    return list(FUNCTION_REGISTRY.keys())

# --- Common helper functions automatically registered ---

def avg(lst):
    return sum(lst) / len(lst) if lst else 0

def percent(part, whole):
    return (part / whole) * 100 if whole != 0 else 0

def join_list(lst, sep=", "):
    return sep.join(map(str, lst))

def top(seq, n=1):
    return sorted(seq, reverse=True)[:n]

def clamp(val, lo, hi):
    return max(lo, min(hi, val))

def unique(seq):
    return list(dict.fromkeys(seq))

def count(seq, val=None):
    return len(seq) if val is None else seq.count(val)

def exists(x):
    return x is not None

def is_empty(seq):
    return not bool(seq)

def minimum(*args):
    return min(args) if args else None

def maximum(*args):
    return max(args) if args else None

def sum_list(lst):
    return sum(lst)

def median(lst):
    if not lst:
        return 0
    sorted_lst = sorted(lst)
    n = len(sorted_lst)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_lst[mid - 1] + sorted_lst[mid]) / 2
    return sorted_lst[mid]

def stddev(lst):
    """Standard deviation of numeric list."""
    if not lst:
        return 0
    mean = sum(lst) / len(lst)
    var = sum((x - mean) ** 2 for x in lst) / len(lst)
    return math.sqrt(var)

def random_choice(seq):
    return random.choice(seq) if seq else None

def random_int(lo, hi):
    return random.randint(lo, hi)

def round_to(val, digits=0):
    return round(val, digits)

def floor(val):
    return math.floor(val)

def ceil(val):
    return math.ceil(val)

def between(x, lo, hi):
    return lo <= x <= hi

def repeat(text, times):
    return str(text) * int(times)

def concat(*args):
    return "".join(map(str, args))

# Pre-register these by default
for name, fn in {
    "avg": avg,
    "percent": percent,
    "join_list": join_list,
    "top": top,
    "clamp": clamp,
    "unique": unique,
    "count": count,
    "exists": exists,
    "is_empty": is_empty,
    "min": minimum,
    "max": maximum,
    "sum_list": sum_list,
    "median": median,
    "stddev": stddev,
    "rand_choice": random_choice,
    "rand_int": random_int,
    "round_to": round_to,
    "floor": floor,
    "ceil": ceil,
    "between": between,
    "repeat": repeat,
    "concat": concat,
}.items():
    register_function(name, fn)

