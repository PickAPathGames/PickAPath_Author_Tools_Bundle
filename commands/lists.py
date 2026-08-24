"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# commands/lists.py
from command_registry import COMMANDS
from utils.safe_eval import safe_eval_expr

# --- HELPERS ---

def resolve_list_items(expr, variables):
    """
    Parses something like (var1, var2, 10, var3) and resolves values 
    against the current engine variables.
    """
    expr = expr.strip().strip("()")
    if not expr:
        return []
        
    # If the evaluator can handle comma-separated expressions:
    try:
        # Many evaluators handle this as a tuple/list automatically
        return safe_eval_expr(f"[{expr}]", variables)
    except Exception:
        # Fallback to your manual split if the evaluator is strictly single-expression
        items = [v.strip() for v in expr.split(",") if v.strip()]

    resolved = []
    for it in items:
        # If it's a known variable, use its value
        if it in variables:
            resolved.append(variables[it])
        else:
            # Otherwise, try to evaluate it as a literal (number/string)
            try:
                resolved.append(safe_eval_expr(it, variables))
            except Exception:
                resolved.append(it)
    return resolved

# --- REGISTRATIONS ---

@COMMANDS.register_parser("-list")
def p_list(parser, args, line_no, level):
    return {"cmd": "-list", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-list")
def r_list(engine, args, block):
    parts = args.split(None, 1)
    if len(parts) < 2:
        return "logic"
    
    var_name = parts[0]
    list_expr = parts[1]
    
    values = resolve_list_items(list_expr, engine.state["vars"])
    engine.state["vars"][var_name] = values
    return "logic"

@COMMANDS.register_parser("-reverse")
def p_reverse(parser, args, line_no, level):
    return {"cmd": "-reverse", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-reverse")
def r_reverse(engine, args, block):
    var_name = args.strip()
    lst = engine.state["vars"].get(var_name)
    if isinstance(lst, list):
        # Mutate in place or reassign; reassigning is safer for state tracking
        engine.state["vars"][var_name] = lst[::-1]
    return "logic"

@COMMANDS.register_parser("-sort_asc")
def p_sort_asc(parser, args, line_no, level):
    return {"cmd": "-sort_asc", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-sort_asc")
def r_sort_asc(engine, args, block):
    var_name = args.strip()
    lst = engine.state["vars"].get(var_name)
    if isinstance(lst, list):
        try:
            engine.state["vars"][var_name] = sorted(lst)
        except TypeError:
            # Handle mixed-type sort errors gracefully
            pass
    return "logic"

@COMMANDS.register_parser("-sort_des")
def p_sort_des(parser, args, line_no, level):
    return {"cmd": "-sort_des", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-sort_des")
def r_sort_des(engine, args, block):
    var_name = args.strip()
    lst = engine.state["vars"].get(var_name)
    if isinstance(lst, list):
        try:
            engine.state["vars"][var_name] = sorted(lst, reverse=True)
        except TypeError:
            pass
    return "logic"









































# # commands/lists.py
# """
# List handling utilities.
# """

# from command_registry import register_command


# def parse_list_expr(expr, variables):
#     """Parses something like (var1, var2, 10, var3)."""
#     expr = expr.strip("()")
#     items = [v.strip() for v in expr.split(",") if v.strip()]
#     resolved = []
#     for it in items:
#         if it in variables:
#             resolved.append(variables[it])
#         else:
#             try:
#                 resolved.append(eval(it, {"__builtins__": {}}, variables))
#             except Exception:
#                 resolved.append(it)
#     return resolved


# @register_command("list")
# def cmd_list(parser, node, args):
#     """Creates a list variable."""
#     if len(args) < 2:
#         parser.record_error("Usage: -list var_name (values...)")
#         return
#     var_name = args[0]
#     values = parse_list_expr(" ".join(args[1:]), parser.variables)
#     parser.variables[var_name] = values


# @register_command("reverse")
# def cmd_reverse(parser, node, args):
#     """Reverses the order of a list."""
#     if len(args) != 1:
#         parser.record_error("Usage: -reverse var_name")
#         return
#     var_name = args[0]
#     lst = parser.variables.get(var_name, [])
#     if not isinstance(lst, list):
#         parser.record_error(f"{var_name} is not a list")
#         return
#     parser.variables[var_name] = lst[::-1]


# @register_command("sort_asc")
# def cmd_sort_asc(parser, node, args):
#     """Sorts a list ascending."""
#     if len(args) != 1:
#         parser.record_error("Usage: -sort_asc var_name")
#         return
#     var_name = args[0]
#     parser.variables[var_name] = sorted(parser.variables.get(var_name, []))


# @register_command("sort_des")
# def cmd_sort_des(parser, node, args):
#     """Sorts a list descending."""
#     if len(args) != 1:
#         parser.record_error("Usage: -sort_des var_name")
#         return
#     var_name = args[0]
#     parser.variables[var_name] = sorted(parser.variables.get(var_name, []), reverse=True)











