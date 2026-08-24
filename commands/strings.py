"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# commands/strings.py
from command_registry import COMMANDS

@COMMANDS.register_parser("-upper")
def p_upper(parser, args, line, level):
    return {"cmd": "-upper", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-upper")
def r_upper(engine, args, block):
    var_name = args.strip()
    val = str(engine.state["vars"].get(var_name, ""))
    engine.state["vars"][var_name] = val.upper()
    return "logic"

@COMMANDS.register_parser("-lower")
def p_lower(parser, args, line, level):
    return {"cmd": "-lower", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-lower")
def r_lower(engine, args, block):
    var_name = args.strip()
    val = str(engine.state["vars"].get(var_name, ""))
    engine.state["vars"][var_name] = val.lower()
    return "logic"

@COMMANDS.register_parser("-naming")
def p_naming(parser, args, line, level):
    return {"cmd": "-naming", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-naming")
def r_naming(engine, args, block):
    var_name = args.strip()
    val = str(engine.state["vars"].get(var_name, ""))
    engine.state["vars"][var_name] = val.title()
    return "logic"

@COMMANDS.register_parser("-turn_around")
def p_turn_around(parser, args, line, level):
    return {"cmd": "-turn_around", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-turn_around")
def r_turn_around(engine, args, block):
    # args example: "reversed_name original_name"
    parts = args.split()
    if len(parts) == 2:
        store_var, source_var = parts[0], parts[1]
        val = str(engine.state["vars"].get(source_var, ""))
        engine.state["vars"][store_var] = val[::-1]
    return "logic"
















































# # commands/strings.py
# """
# String manipulation commands.
# """

# # from commands_registry import register_command
# # from parser.registry import register_command
# from command_registry import register_command


# @register_command("upper")
# def cmd_upper(parser, node, args):
#     """Turns a variable to uppercase."""
#     if len(args) != 1:
#         parser.record_error("Usage: -upper var_name")
#         return
#     var_name = args[0]
#     val = str(parser.variables.get(var_name, ""))
#     parser.variables[var_name] = val.upper()


# @register_command("lower")
# def cmd_lower(parser, node, args):
#     """Turns a variable to lowercase."""
#     if len(args) != 1:
#         parser.record_error("Usage: -lower var_name")
#         return
#     var_name = args[0]
#     val = str(parser.variables.get(var_name, ""))
#     parser.variables[var_name] = val.lower()


# @register_command("naming")
# def cmd_naming(parser, node, args):
#     """Capitalizes the first letter of each word."""
#     if len(args) != 1:
#         parser.record_error("Usage: -naming var_name")
#         return
#     var_name = args[0]
#     val = str(parser.variables.get(var_name, ""))
#     parser.variables[var_name] = val.title()


# @register_command("turn_around")
# def cmd_turn_around(parser, node, args):
#     """Reverses the text of a variable."""
#     if len(args) != 2:
#         parser.record_error("Usage: -turn_around store_var source_var")
#         return
#     store_var, source_var = args
#     val = str(parser.variables.get(source_var, ""))
#     parser.variables[store_var] = val[::-1]
