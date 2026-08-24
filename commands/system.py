"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# commands/system.py
import copy
from command_registry import COMMANDS


@COMMANDS.register_parser("-cheat_mode")
def p_cheat(parser, args, line, level):
    if hasattr(parser, "meta"):
        parser.meta["CHEAT_MODE"] = args.lower() == "true"
    return {"cmd": "-cheat_mode", "args": args, "__line__": line}

@COMMANDS.register_runtime("-cheat_mode")
def r_cheat(engine, args, block):
    clean_args = args.replace("=", "").strip().lower()
    val = (clean_args == "true")

    engine.state["vars"]["cheat_mode"] = val
    
    if hasattr(engine, "session"):
        loc = f"{engine.state['scene']}:{engine.state['tag']}"
        engine.session.record_var_change("cheat_mode", val, loc)
    return "logic"

@COMMANDS.register_parser("-author_mode")
def p_author_mode(parser, args, line, level):
    return {"cmd": "-author_mode", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-author_mode")
def r_author_mode(engine, args, block):
    # Syntax: -author_mode true/false
    clean_args = args.replace("=", "").strip().lower()
    val = (clean_args == "true")

    # 1. Update engine variables cleanly so snapshots pick it up locally
    engine.state["vars"]["author_mode"] = val
    
    # 2. Synchronize with the active session history context frame instantly
    if hasattr(engine, "session"):
        loc = f"{engine.state['scene']}:{engine.state['tag']}"
        engine.session.record_var_change("author_mode", val, loc)

    return "logic"

@COMMANDS.register_parser("-save_checkpoint")
def p_save_cp(parser, args, line, level):
    return {"cmd": "-save_checkpoint", "args": args.strip(), "__line__": line}

@COMMANDS.register_parser("-load_checkpoint")
def p_load_cp(parser, args, line, level):
    return {"cmd": "-load_checkpoint", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-save_checkpoint")
def r_save_cp(engine, args, block):
    sess = getattr(engine, "session", None)
    if not sess:
        return "logic"

    name = args.strip() or "auto"

    # Checkpoint = playhead index into the history tape
    if "_checkpoints" not in engine.state["vars"]:
        engine.state["vars"]["_checkpoints"] = {}

    engine.state["vars"]["_checkpoints"][name] = sess.playhead
    sess.record_var_change(
        "_checkpoints",
        engine.state["vars"]["_checkpoints"],
        f"{engine.state['scene']}:{engine.state['tag']}"
    )

    # Toast notification
    if not hasattr(sess, "pending_notifications"):
        sess.pending_notifications = []
    sess.pending_notifications.append({
        "kind":  "notification",
        "type":  "checkpoint",
        "title": f"Checkpoint: {name.replace('_', ' ').title()}",
        "name":  name,
    })

    return "logic"

@COMMANDS.register_runtime("-load_checkpoint")
def r_load_cp(engine, args, block):
    sess = getattr(engine, "session", None)
    if not sess:
        return "logic"

    name        = args.strip() or "auto"
    checkpoints = engine.state["vars"].get("_checkpoints", {})

    if name not in checkpoints:
        print(f"[CHECKPOINT] '{name}' not found. Available: {list(checkpoints.keys())}")
        return "logic"

    target = checkpoints[name]

    if target >= len(sess.history_frames):
        print(f"[CHECKPOINT] Playhead {target} out of range.")
        return "logic"

    # Rewind: truncate history after checkpoint
    sess.history_frames = sess.history_frames[:target + 1]
    sess.playhead       = target

    # Restore engine state from snapshot at checkpoint frame
    snapshot = sess.history_frames[target].get("engine_snapshot")
    if snapshot:
        engine.load_state(snapshot)

    # Prune checkpoints that are now in the future
    engine.state["vars"]["_checkpoints"] = {
        k: v for k, v in checkpoints.items()
        if v <= target
    }

    print(f"[CHECKPOINT] Loaded '{name}' - rewound to playhead {target}")

    # Return "jump", engine loop continues stepping from the restored
    # position until it hits pause/pick/end naturally
    return "jump"

@COMMANDS.register_parser("-map_mode")
def p_map_mode(parser, args, line, level):
    return {"cmd": "-map_mode", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-map_mode")
def r_map_mode(engine, args, block):
    mode = args.replace("=", "").replace('"', "").replace("'", "").strip().lower()
    valid_modes = ["visited", "wire", "full"]

    if mode in valid_modes:
        # Use 'map_mode' here to match the server launcher
        engine.state["vars"]["map_mode"] = mode
        
        import sys
        if 'engine.web.server_launcher' in sys.modules:
            sys.modules['engine.web.server_launcher'].state["map_visibility"] = mode
            
    return "logic"

@COMMANDS.register_parser("-map_style")
def p_map_style(parser, args, line, level):
    # Syntax: -map_style nodes  OR  -map_style cards
    return {"cmd": "-map_style", "args": args.strip().lower(), "__line__": line}

@COMMANDS.register_runtime("-map_style")
def r_map_style(engine, args, block):
    style = args.strip().lower()
    if style in ["nodes", "cards", "lines"]:
        # Store in engine vars so MapManager can see it
        engine.state["vars"]["map_style"] = style
    return "logic"





