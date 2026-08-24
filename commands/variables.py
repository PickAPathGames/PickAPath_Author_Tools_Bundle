"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# commands/variables.py
"""
Handles variable declarations, math operations, and expression evaluation.
Supports both permanent (-mvar) and temporary (-tvar) variables.
"""

from command_registry import COMMANDS
from utils.safe_eval import safe_eval_expr
import re


# --- SHARED MATH LOGIC ---

def apply_op(left_val, op, rhs_val, limit=100.0):
    """The 'Source of Truth' for all math operations in the engine."""
    if op == "=":  return rhs_val
    if op == "+":  return left_val + rhs_val
    if op == "-":  return left_val - rhs_val
    if op == "*":  return left_val * rhs_val
    if op == "/":  return left_val / rhs_val
    if op == "+=": return left_val + rhs_val
    if op == "-=": return left_val - rhs_val
    if op == "*=": return left_val * rhs_val
    if op == "/=": return left_val / rhs_val if rhs_val != 0 else left_val
    if op == "%+": return left_val + (limit - left_val) * (rhs_val / 100.0)
    if op == "%-": return left_val - (left_val * (rhs_val / 100.0))

    if op == "%" or op == "%=": 
        return left_val % rhs_val if rhs_val != 0 else left_val
    
    if op == "round":
        return round(left_val, int(rhs_val))

    return rhs_val

@COMMANDS.register_parser("-global_max_percentage")
def p_set_limit(parser, args, line, level):
    return {"cmd": "-global_max_percentage", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-global_max_percentage")
def r_set_limit(engine, args, block):
    try:
        # Limpiar signo igual y espacios
        clean_args = args.strip().lstrip('= ').strip()
        val = float(safe_eval_expr(clean_args, engine._eval_vars()))
        engine.state["vars"]["GLOBAL_MAX_PERCENTAGE"] = val
    except:
        engine.state["vars"]["GLOBAL_MAX_PERCENTAGE"] = 100.0
    return "logic"
    
# --- MVAR (MODULAR VARIABLE) ---

@COMMANDS.register_parser("-mvar")
def parse_mvar(parser, args, line_no, level):
    """
    Parser-side: Identify the variable being written to for the Validator.
    """
    # Use your existing robust parse_mvar_args helper
    from validators.expr import parse_mvar_args
    try:
        name, op, rhs_str = parse_mvar_args(args)
        # Inform the parser that this node modifies this variable
        if parser.current_node:
            parser.current_node.var_writes.append(name)
    except:
        pass 

    return {"cmd": "-mvar", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-mvar")
def run_mvar(engine, args, block):
    from validators.expr import parse_mvar_args
    name, op, rhs_str = parse_mvar_args(args)
    
    ev = engine._eval_vars()
    
    # Resolve storage key, tvar uses underscore prefix
    storage_key = f"_{name}" if f"_{name}" in engine.state["vars"] \
                  and name not in engine.state["vars"] else name
    
    current_val = ev.get(storage_key, ev.get(name, 0))
    limit = engine.state["vars"].get("GLOBAL_MAX_PERCENTAGE", 100.0)
    rhs_val = safe_eval_expr(rhs_str, ev)
    
    new_val = apply_op(current_val, op, rhs_val, limit)
    
    if isinstance(new_val, float) and new_val.is_integer():
        new_val = int(new_val)
        
    engine.state["vars"][storage_key] = new_val
    
    if hasattr(engine, "session"):
        loc = f"{engine.state['scene']}:{engine.state['tag']}"
        engine.session.record_var_change(storage_key, new_val, loc)
    
    return "logic"

# --- WINNER / LOSER (RANKING) ---

@COMMANDS.register_parser("-winner")
def parse_winner(parser, args, line_no, level):
    # args: "base_name (var1, var2, var3)"
    return {"cmd": "-winner", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-winner")
def run_winner(engine, args, block):
    # args: target_var (var1, var2, var3)
    parts = args.split(None, 1)
    if len(parts) < 2: return "logic"
    
    target_base = parts[0]
    # Clean string: "(Joe, Bill, Paul)" -> ["Joe", "Bill", "Paul"]
    raw_list = parts[1].strip("()").replace(",", " ").split()
    
    # Filter to only variables that actually exist in the engine
    scores = {name: engine.state["vars"].get(name, 0) for name in raw_list}
    if not scores: return "logic"

    max_val = max(scores.values())
    winners = [name for name, val in scores.items() if val == max_val]

    # Populate the "Flat" structure
    vars_dict = engine.state["vars"]
    vars_dict[f"{target_base}_names"] = winners
    vars_dict[f"{target_base}_count"] = len(winners)
    vars_dict[f"{target_base}_score"] = max_val
    # Main var is the first winner (String) or a join if you prefer
    vars_dict[target_base] = winners[0] if len(winners) == 1 else ", ".join(winners)
    
    return "logic"

@COMMANDS.register_parser("-loser")
def p_loser(p, a, l, lv): return {"cmd": "-loser", "args": a, "__line__": l}

@COMMANDS.register_runtime("-loser")
def r_loser(engine, args, block):
    parts = args.split(None, 1)
    if len(parts) < 2: return "logic"
    
    target_base = parts[0]
    raw_list = parts[1].strip("()").replace(",", " ").split()
    
    # Get values from engine state
    scores = {name: engine.state["vars"].get(name, 0) for name in raw_list}
    if not scores: return "logic"

    min_val = min(scores.values())
    losers = [name for name, val in scores.items() if val == min_val]

    # Populate the "Flat" structure (Exact mirror of -winner)
    vars_dict = engine.state["vars"]
    vars_dict[f"{target_base}_names"] = losers
    vars_dict[f"{target_base}_count"] = len(losers)
    vars_dict[f"{target_base}_score"] = min_val # Consistent naming
    
    # Default variable is the first loser name
    vars_dict[target_base] = losers[0] if len(losers) == 1 else ", ".join(losers)
    
    return "logic"


@COMMANDS.register_runtime("-average")
def r_average(engine, args, block):
    # Usage: -average result_var (var1, var2, var3)
    parts = args.split(None, 1)
    if len(parts) < 2: return "logic"
    
    target_var = parts[0]
    raw_list = parts[1].strip("()").replace(",", " ").split()
    
    # Get values, defaulting to 0 for missing variables
    vals = [float(engine.state["vars"].get(v, 0)) for v in raw_list]
    
    if vals:
        engine.state["vars"][target_var] = sum(vals) / len(vals)
    else:
        engine.state["vars"][target_var] = 0
        
    return "logic"

@COMMANDS.register_runtime("-range")
def r_range(engine, args, block):
    # Usage: -range result_var (var1, var2, var3)
    parts = args.split(None, 1)
    if len(parts) < 2: return "logic"
    
    target_var = parts[0]
    raw_list = parts[1].strip("()").replace(",", " ").split()
    
    vals = [float(engine.state["vars"].get(v, 0)) for v in raw_list]
    
    if vals:
        engine.state["vars"][target_var] = max(vals) - min(vals)
    else:
        engine.state["vars"][target_var] = 0
        
    return "logic"

@COMMANDS.register_runtime("-mod")
def r_mod(engine, args, block):
    """Usage: -mod var_name divisor (e.g., -mod turn_counter 4)"""
    parts = args.split()
    if len(parts) < 2: return "logic"
    
    name, divisor_str = parts[0], parts[1]
    ev = engine._eval_vars()
    
    current = ev.get(name, 0)
    divisor = safe_eval_expr(divisor_str, ev)
    
    engine.state["vars"][name] = current % divisor if divisor != 0 else current
    return "logic"

@COMMANDS.register_runtime("-round")
def r_round(engine, args, block):
    """Usage: -round var_name [decimals] (e.g., -round health 0)"""
    parts = args.split()
    if not parts: return "logic"
    
    name = parts[0]
    decimals = int(parts[1]) if len(parts) > 1 else 0
    
    ev = engine._eval_vars()
    current = ev.get(name, 0)
    
    engine.state["vars"][name] = round(float(current), decimals)
    return "logic"

# --- HELPERS ---

def _coerce_bool(value):
    if isinstance(value, bool): return value
    if value == 0: return False
    if value == 1: return True
    return None

def _get_limit(engine):
    # Check engine state vars first (set by -global_max_percentage)
    # Fallback to engine config, then hardcoded 100.0
    return float(engine.state["vars"].get("GLOBAL_MAX_PERCENTAGE", 
                 engine.state.get("config", {}).get("GLOBAL_MAX_PERCENTAGE", 100.0)))

# --- MATH CORE ---

def parse_generic_math(parser, args, line_no, level, cmd_name):
    # Shared parser for simple -add, -sub, -tvar
    return {"cmd": f"-{cmd_name.lstrip('-')}", "args": args, "__line__": line_no}

# --- REGISTRATIONS ---

@COMMANDS.register_parser("-add")
def p_add(p, a, l, lv): return parse_generic_math(p, a, l, lv, "add")

@COMMANDS.register_runtime("-add")
def r_add(engine, args, block):
    parts = args.split(None, 1)
    var_name, expr = parts[0], parts[1]
    val = safe_eval_expr(expr, engine.state["vars"])
    engine.state["vars"][var_name] = engine.state["vars"].get(var_name, 0) + val
    return "logic"

@COMMANDS.register_parser("-sub")
def p_sub(p, a, l, lv): return parse_generic_math(p, a, l, lv, "sub")

@COMMANDS.register_runtime("-sub")
def r_sub(engine, args, block):
    parts = args.split(None, 1)
    var_name, expr = parts[0], parts[1]
    val = safe_eval_expr(expr, engine.state["vars"])
    engine.state["vars"][var_name] = engine.state["vars"].get(var_name, 0) - val
    return "logic"

@COMMANDS.register_parser("-tvar")
def p_tvar(p, a, l, lv): return parse_generic_math(p, a, l, lv, "tvar")

@COMMANDS.register_runtime("-tvar")
def r_tvar(engine, args, block):
    # 1. Use a regex to split by name, optional '=', and the expression
    # This matches: [name] [optional =] [rest of the string]
    match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*=?\s*(.*)$", args.strip())
    
    if not match:
        print(f"[ERROR] Invalid -tvar format: {args}")
        return "logic"
    
    raw_name, expr = match.groups()
    
    # Force the underscore prefix
    safe_name = raw_name if raw_name.startswith("_") else f"_{raw_name}"
    
    # Now expr will be '0' instead of '= 0'
    val = safe_eval_expr(expr, engine._eval_vars())
    
    engine.state["vars"][safe_name] = val
    return "logic"

# --- FAIRMATH ---

@COMMANDS.register_parser("-%+")
def p_fadd(p, a, l, lv): return {"cmd": "-%+", "args": a, "__line__": l}

@COMMANDS.register_parser("-%-")
def p_fsub(p, a, l, lv): return {"cmd": "-%-", "args": a, "__line__": l}

@COMMANDS.register_runtime("-%+")
def r_fadd(engine, args, block):
    parts = args.split(None, 1)
    name, pct_expr = parts[0], parts[1]
    
    rhs_val = float(safe_eval_expr(pct_expr, engine._eval_vars()))
    current_val = engine.state["vars"].get(name, 0)
    
    new_val = apply_op(current_val, "%+", rhs_val, _get_limit(engine))
    engine.state["vars"][name] = new_val

    # Sync with session
    if hasattr(engine, "session"):
        engine.session.record_var_change(name, new_val, f"line:{block.get('__line__')}")
    return "logic"

@COMMANDS.register_runtime("-%-")
def r_fsub(engine, args, block):
    parts = args.split(None, 1)
    name, pct_expr = parts[0], parts[1]
    
    rhs_val = float(safe_eval_expr(pct_expr, engine._eval_vars()))
    current_val = engine.state["vars"].get(name, 0)
    
    # Use the Source of Truth!
    new_val = apply_op(current_val, "%-", rhs_val, _get_limit(engine))
    
    engine.state["vars"][name] = new_val
    return "logic"

# --- UTILITY ---

@COMMANDS.register_parser("-toggle")
def p_toggle(p, a, l, lv): return {"cmd": "-toggle", "args": a, "__line__": l}

@COMMANDS.register_runtime("-toggle")
def r_toggle(engine, args, block):
    name = args.strip().split()[0]
    old = engine.state["vars"].get(name)
    b_val = _coerce_bool(old)
    if b_val is not None:
        engine.state["vars"][name] = not b_val
    return "logic"

@COMMANDS.register_parser("-entropy")
def p_entropy(p, a, l, lv): return {"cmd": "-entropy", "args": a, "__line__": l}

@COMMANDS.register_runtime("-entropy")
def r_entropy(engine, args, block):
    import random
    import re
    parts = args.split()
    if len(parts) < 3: return "logic"

    target_var, seed_val, range_str = parts[0], parts[1], parts[2]
    local_rng = random.Random(seed_val)
    match = re.match(r"(\d+)[-:](\d+)", range_str)

    if match:
        low, high = map(int, match.groups())
        result = local_rng.randint(low, high)
        engine.state["vars"][target_var] = result
        
        if hasattr(engine, "session"):
            engine.session.record_var_change(target_var, result, f"entropy:{seed_val}")
            
    return "logic"
 
# --- USER INPUT ---
 
@COMMANDS.register_parser("-user_input")
def p_user_input(parser, args, line_no, level):
    # args: "var_name" or "var_name Some prompt text"
    parts = args.strip().split(None, 1)
    var_name = parts[0] if parts else "user_input"
    prompt   = parts[1] if len(parts) > 1 else ""
    return {"cmd": "-user_input", "args": args, "var_name": var_name, "prompt": prompt, "__line__": line_no}
 
# @COMMANDS.register_runtime("-user_input")
# def r_user_input(engine, args, block):
#     parts    = args.strip().split(None, 1)
#     var_name = parts[0] if parts else "user_input"
#     prompt   = parts[1] if len(parts) > 1 else ""
#     # Stash so session.apply_intent can assign the value after user submits
#     engine.state["pending_user_input_var"]    = var_name
#     engine.state["pending_user_input_prompt"] = prompt
#     return "user_input"


@COMMANDS.register_runtime("-user_input")
def r_user_input(engine, args, block):
    # Use the data already structured by the parser (p_user_input)
    var_name = block.get("var_name", "user_input")
    prompt   = block.get("prompt", "")
    
    engine.state["pending_user_input_var"]    = var_name
    engine.state["pending_user_input_prompt"] = prompt
    
    # Return exactly this string
    return "user_input"

