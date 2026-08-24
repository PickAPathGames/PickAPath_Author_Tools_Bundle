"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# commands/logic.py
from command_registry import COMMANDS
from utils.safe_eval import safe_eval_expr

# --- PARSERS (Building the Tree) ---

@COMMANDS.register_parser("-if")
def parse_if(parser, args, line_no, level):
    return {
        "cmd": "-if",
        "args": args.strip(),
        "__line__": line_no,
        "expects_indent": True,
        "context_name": "if_block",
        "node": {"text": [], "blocks": []}
    }

@COMMANDS.register_parser("-elseif")
def parse_elseif(parser, args, line_no, level):
    return {
        "cmd": "-elseif",
        "args": args.strip(),
        "__line__": line_no,
        "expects_indent": True,
        "context_name": "if_block",
        "node": {"text": [], "blocks": []}
    }

@COMMANDS.register_parser("-else")
def parse_else(parser, args, line_no, level):
    return {
        "cmd": "-else",
        "args": args,
        "__line__": line_no,
        "expects_indent": True,
        "context_name": "else_block",
        "node": {"text": [], "blocks": []}
    }

# --- RUNTIMES (Executing the Logic) ---

@COMMANDS.register_runtime("-if")
def run_if(engine, args, block):
    # The engine uses the 'args' stored during parsing
    cond_str = block.get("args", "False")
    # print("TTTT      ", cond_str, engine.state)
    # print("OOOO      ", engine.state["vars"])
    result = bool(safe_eval_expr(cond_str, engine.state["vars"]))
    
    if result:
        # Push the nested node's blocks onto the execution stack
        engine.state["block_stack"].append(block["node"]["blocks"])
        return "logic"
    return "skip" # Tell engine to move to the next sibling block
