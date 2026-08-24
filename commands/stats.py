"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# commands/stats.py
from command_registry import COMMANDS
from utils.safe_eval import safe_eval_expr
import re

# ---------------------------------------------------------------------------
# Permanent stat bar  (sidebar grid)
# ---------------------------------------------------------------------------

@COMMANDS.register_parser("-permanent_stat")
def p_perm_stat(parser, args, line, level):
    return {"cmd": "-permanent_stat", "args": args.strip(), "__line__": line}


@COMMANDS.register_runtime("-permanent_stat")
def r_perm_stat(engine, args, block):
    parts = args.split(None, 2)
    if len(parts) == 3:
        try:
            slot     = int(parts[0]) - 1
            label    = parts[1].strip('"')
            var_name = parts[2]
            if "ui_grid" not in engine.state:
                engine.state["ui_grid"] = [None] * 4
            if 0 <= slot < 4:
                engine.state["ui_grid"][slot] = {"label": label, "var": var_name}
        except ValueError:
            pass
    return "logic"


@COMMANDS.register_parser("-remove_permanent_stat")
def p_rem_stat(parser, args, line, level):
    return {"cmd": "-remove_permanent_stat", "args": args.strip(), "__line__": line}


@COMMANDS.register_runtime("-remove_permanent_stat")
def r_rem_stat(engine, args, block):
    label       = args.strip()
    stats_display = engine.state.get("ui_stats", {})
    if label in stats_display:
        del stats_display[label]
    return "logic"

# ---------------------------------------------------------------------------
# Stat display commands, parser registrations
#
# All these commands appear as lines in stats.txt.
# Without a register_parser entry the mini-parser treats them as plain text.
# The parser just needs to capture cmd + args as a block; the runtime and
# UIProcessor handle the actual rendering.
# ---------------------------------------------------------------------------

@COMMANDS.register_parser("-stat_header")
def p_stat_header(parser, args, line, level):
    return {"cmd": "-stat_header", "args": args.strip(), "__line__": line}


@COMMANDS.register_runtime("-stat_header")
def r_stat_header(engine, args, block):
    text = args.strip().strip('"').strip()
    return {
        "kind": "display",
        "component": "stat_header",
        "props": {"text": text}
    }


@COMMANDS.register_parser("-stat_row")
def p_stat_row(parser, args, line, level):
    return {"cmd": "-stat_row", "args": args.strip(), "__line__": line}


@COMMANDS.register_runtime("-stat_row")
def r_stat_row(engine, args, block):
    from engine.runtime.interpolation import interpolate_and_format, tokens_to_html
    args_raw = args.strip()
    m = re.match(r'^"([^"]*)"\s+(\S+)$', args_raw)
    if m:
        label    = m.group(1).rstrip()
        var_name = m.group(2)
    else:
        parts = args_raw.split(None, 1)
        if len(parts) < 2: return "logic"
        label    = parts[0].strip('"').rstrip()
        var_name = parts[1].strip()
        
    vars_at_time = engine._eval_vars()
    val     = vars_at_time.get(var_name, "-")
    val_str = str(val)
    tokens  = interpolate_and_format(val_str, vars_at_time)
    return {
        "kind": "display",
        "component": "stat_row",
        "props": {
            "label": label,
            "value": val_str,
            "html": tokens_to_html(tokens)
        }
    }


@COMMANDS.register_parser("-stat_bar")
def p_stat_bar(parser, args, line, level):
    return {"cmd": "-stat_bar", "args": args.strip(), "__line__": line}


@COMMANDS.register_runtime("-stat_bar")
def r_stat_bar(engine, args, block):
    args_raw = args.strip()
    m = re.match(r'^"([^"]*)"\s+(\S+)(?:\s+(\S+))?$', args_raw)
    if m:
        parts = [m.group(1), m.group(2)]
        if m.group(3): parts.append(m.group(3))
    else:
        parts = args_raw.split()
        
    if len(parts) < 2: return "logic"
        
    label    = parts[0].strip('"')
    var_name = parts[1]
    color    = parts[2] if len(parts) > 2 else None
    
    vars_at_time = engine._eval_vars()
    try:
        max_val = float(vars_at_time.get("GLOBAL_MAX_PERCENTAGE", 100.0))
    except (TypeError, ValueError):
        max_val = 100.0
        
    try:
        val = float(vars_at_time.get(var_name, 0))
    except (TypeError, ValueError):
        val = 0.0
        
    pct = min(max(val / max_val * 100, 0), 100) if max_val else 0
    return {
        "kind": "display",
        "component": "stat_bar",
        "props": {
            "label":   label,
            "value":   val,
            "max":     max_val,
            "percent": round(pct, 2),
            "color":   color,
        }
    }


@COMMANDS.register_parser("-stat_vs")
def p_stat_vs(parser, args, line, level):
    return {"cmd": "-stat_vs", "args": args.strip(), "__line__": line}


@COMMANDS.register_runtime("-stat_vs")
def r_stat_vs(engine, args, block):
    args_raw = args.strip()
    m = re.match(r'^(\S+)\s+"([^"]*)"\s+"([^"]*)"(?:\s+(\S+))?(?:\s+(\S+))?$', args_raw)
    if m:
        parts = [m.group(1), m.group(2), m.group(3)]
        if m.group(4): parts.append(m.group(4))
        if m.group(5): parts.append(m.group(5))
    else:
        parts = args_raw.split()
        
    if len(parts) < 3: return "logic"
        
    var_name    = parts[0]
    left_label  = parts[1].strip('"')
    right_label = parts[2].strip('"')
    color_left  = parts[3] if len(parts) > 3 else "#3b82f6"
    color_right = parts[4] if len(parts) > 4 else "#ef4444"
    
    vars_at_time = engine._eval_vars()
    try:
        max_val = float(vars_at_time.get("GLOBAL_MAX_PERCENTAGE", 100.0))
    except (TypeError, ValueError):
        max_val = 100.0
        
    try:
        val = float(vars_at_time.get(var_name, 0))
    except (TypeError, ValueError):
        val = 0.0
        
    pct = min(max(val / max_val * 100, 0), 100) if max_val else 0
    return {
        "kind": "display",
        "component": "stat_vs",
        "props": {
            "var_name":    var_name,
            "left_label":  left_label,
            "right_label": right_label,
            "value":       val,
            "max":         max_val,
            "percent":     round(pct, 2),
            "color_left":  color_left,
            "color_right": color_right,
        }
    }


@COMMANDS.register_parser("-stat_break")
def p_stat_break(parser, args, line, level):
    return {"cmd": "-stat_break", "args": "", "__line__": line}


@COMMANDS.register_runtime("-stat_break")
def r_stat_break(engine, args, block):
    return {
        "kind": "display",
        "component": "stat_break",
        "props": {}
    }


@COMMANDS.register_parser("-stat_block")
def p_stat_block(parser, args, line, level):
    return {"cmd": "-stat_block", "args": args.strip(), "__line__": line}


@COMMANDS.register_runtime("-stat_block")
def r_stat_block(engine, args, block):
    return {"kind": "stat_block_open"}


@COMMANDS.register_parser("-stat_block_end")
def p_stat_block_end(parser, args, line, level):
    return {"cmd": "-stat_block_end", "args": "", "__line__": line}


@COMMANDS.register_runtime("-stat_block_end")
def r_stat_block_end(engine, args, block):
    return {"kind": "stat_block_close"}

@COMMANDS.register_parser("-stat_item")
def p_stat_item(parser, args, line, level):
    return {"cmd": "-stat_item", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-stat_item")
def r_stat_item(engine, args, block):
    # Strip wrapping quotes if present
    label = args.strip().strip('"')
    return {
        "kind": "display",
        "component": "stat_item",
        "props": {
            "label": label
        }
    }

@COMMANDS.register_parser("-stat_items")
def p_stat_items(parser, args, line, level):
    return {"cmd": "-stat_items", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-stat_items")
def r_stat_items(engine, args, block):
    import shlex
    
    args_raw = args.strip()
    
    # Check for quotes. No quotes equals all labels.
    if '"' not in args_raw and "'" not in args_raw:
        labels_part = args_raw.split()
        vars_part = []
    else:
        # Split at first quote. Left = vars. Right = labels.
        m = re.match(r'^([^"\']*)(.*)$', args_raw)
        vars_lead = m.group(1).strip()
        labels_lead = m.group(2).strip()
        
        vars_part = vars_lead.split() if vars_lead else []
        labels_part = shlex.split(labels_lead) if labels_lead else []

    vars_at_time = engine._eval_vars()
    
    if not vars_part:
        active = labels_part
    else:
        active = []
        for i, var_name in enumerate(vars_part):
            if i >= len(labels_part):
                break
            val = vars_at_time.get(var_name, 0)
            if val and val != "0" and str(val).lower() != "false":
                active.append(labels_part[i])

    display_str = " | ".join(active) + " |" if active else ""

    return {
        "kind": "display",
        "component": "stat_list",
        "props": {
            "text": display_str
        }
    }