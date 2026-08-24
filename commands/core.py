# commands/core.py
"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""
"""
Core commands for PickQuick / PickRandom parser.
Handles basic configuration, navigation, and scene structure.
"""

from command_registry import COMMANDS

# --- NAVIGATION & FLOW ---

@COMMANDS.register_parser("-go")
def parse_go(parser, args, line_no, level):
    # Standardize navigation into a simple block
    return {"cmd": "-go", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-go") # Match the cmd string in the block
def run_go(engine, args, block):
    # Logic moved from PickEngine._execute_block
    engine._jump(engine.state["scene"], args.strip())
    return "jump"

@COMMANDS.register_parser("-pause")
def parse_pause(parser, args, line_no, level):
    return {"cmd": "-pause", "__line__": line_no}

@COMMANDS.register_runtime("-pause")
def run_pause(engine, args, block):
    return "pause"

# --- METADATA & UI ---

@COMMANDS.register_parser("-pic")
def parse_pic(parser, args, line_no, level):
    return {"cmd": "-pic", "args": args, "__line__": line_no}

@COMMANDS.register_runtime("-pic")
def run_pic(engine, args, block):
    parts    = args.strip().split()
    filename = parts[0] if parts else ""
    align    = parts[1] if len(parts) > 1 else "center"
    engine.state["current_image"] = args.strip()
    return {
        "kind":      "display",
        "component": "pic",
        "props": {
            "filename": filename,
            "align":    align,
            "src":      f"/images/{filename}",
        }
    }

@COMMANDS.register_parser("-reach_goal")
def parse_goal(parser, args, line_no, level):
    return {"cmd": "-reach_goal", "args": args, "__line__": line_no}


@COMMANDS.register_runtime("-reach_goal")
def r_reach_goal(engine, args, block):
    goal_id = args.strip()
    if not goal_id: return "logic"

    if "goals_reached" not in engine.state["vars"]:
        engine.state["vars"]["goals_reached"] = []
    
    reached_list = engine.state["vars"]["goals_reached"]
    
    if goal_id not in reached_list:
        reached_list.append(goal_id)
        # Write as boolean var so -if can read it
        engine.state["vars"][goal_id] = True

        sess = getattr(engine, "session", None)
        prompt = goal_id
        
        if sess and hasattr(sess, "goals_defs"):
            goal_def = sess.goals_defs.get(goal_id)
            if goal_def:
                attrs = getattr(goal_def, "attributes", {})
                prompt = attrs.get("prompt", goal_id)

        if sess:
            if not hasattr(sess, "achieved_goals"):
                sess.achieved_goals = []
            if goal_id not in sess.achieved_goals:
                sess.achieved_goals.append(goal_id)

            if not hasattr(sess, "pending_notifications"):
                sess.pending_notifications = []
            if not any(n.get("id") == goal_id for n in sess.pending_notifications):
                sess.pending_notifications.append({
                    "kind": "notification",
                    "type": "goal",
                    "id": goal_id,
                    "title": prompt
                })
    else:
        # Already reached, ensure var exists (survives engine.load_state)
        engine.state["vars"][goal_id] = True
        
    return "logic"
    

### placeholder for future implementation ###
@COMMANDS.register_parser("-bg_color")
def parse_bg_color(parser, node, args):
    # During parsing: just record the intent in the node's metadata
    node.meta["bg_color"] = " ".join(args).strip()
    # also return a block so it stays in the block sequence if order matters
    return {"cmd": "bg_color", "args": args}

@COMMANDS.register_runtime("-bg_color")
def run_bg_color(engine, args):
    # During play: tell the UI to change color
    color = " ".join(args).strip()
    engine.state["current_bg"] = color
    # (In a web engine, this might be sent to the frontend)

@COMMANDS.register_parser("-body_color")
def parse_body_color(parser, node, args):
    # During parsing: just record the intent in the node's metadata
    node.meta["body_color"] = " ".join(args).strip()
    # also return a block so it stays in the block sequence if order matters
    return {"cmd": "body_color", "args": args}

@COMMANDS.register_runtime("-body_color")
def run_body_color(engine, args):
    # During play: tell the UI to change color
    color = " ".join(args).strip()
    engine.state["current_body"] = color
    # (In a web engine, this might be sent to the frontend)
### placeholder for future implementation ###



@COMMANDS.register_parser("-pick_once")
def parse_pick_once(parser, args, line_no, level):
    # This ensures pick_once creates the SAME structure as -pick
    # but with the 'is_once_block' flag for choice_subtype logic.
    from commands.core import parse_pick # ensure import
    block = parse_pick(parser, args, line_no, level)
    block["cmd"] = "-pick_once" # keep original identity
    block["is_once_block"] = True
    return block

@COMMANDS.register_parser("-reset_pick")
def parse_reset_pick(parser, args, line_no, level):
    return {
        "cmd": "-reset_pick",
        "args": args, # Usually empty
        "__line__": line_no
    }

@COMMANDS.register_runtime("-reset_pick")
def run_reset_pick(engine, args, block):
    """Clear all used pick_once choices so they become available again."""
    engine.state["vars"]["_used_choices"] = []
    if hasattr(engine, "session"):
        engine.session.record_var_change(
            "_used_choices", [],
            f"{engine.state['scene']}:{engine.state['tag']}"
        )
    return "logic"

@COMMANDS.register_parser("-pick_if")
def parse_pick_if(parser, args, line_no, level):
    rest = args.strip()
    expr = rest 
    choice_text = None

    # Handle inline: -pick_if drawings == 1 # Look at the book
    if "#" in rest:
        expr, choice_text = rest.split("#", 1)
        expr = expr.strip()
        choice_text = choice_text.strip()

    # Create the block
    block = {
        "cmd": "-pick_if",
        "args": expr,        # <--- Standardized key for the Validator
        "__line__": line_no,
    }

    if choice_text:
        block["inline_choice"] = choice_text
    else:
        block["expects_indent"] = True
        block["context_name"] = "choice"
        block["node"] = {"text": [], "blocks": []}

    return block

@COMMANDS.register_parser("-single_pick")
def parse_single_pick(parser, args, line_no, level):
    pick_id = f"spick_{line_no}"
    return {
        "cmd": "-single_pick",
        "args": args,
        "__line__": line_no,
        "expects_indent": True,
        "context_name": "pick_block",
        "pick_id": pick_id,
        "node": {
            "blocks": [], 
            "choices": [], 
            "pick_id": pick_id 
        }
    }

# --- STORY CONFIGURATION (Parser-only mostly) ---

@COMMANDS.register_parser("-title")
def parse_title(parser, args, line, level):
    parser.config["TITLE"] = args.strip()
    return {"cmd": "-title", "args": args}

@COMMANDS.register_parser("-author")
def parse_author(parser, args, line, level):
    parser.config["AUTHOR"] = args.strip()
    return {"cmd": "-author", "args": args}

@COMMANDS.register_parser("-version")
def parse_version(parser, args, line, level):
    parser.config["VERSION"] = args.strip()
    return {"cmd": "-version", "args": args}

@COMMANDS.register_parser("-files")
def parse_files(parser, args, line, level):
    parser.file_order = args.split()
    return {"cmd": "-files", "args": args}

# --- NAVIGATION ---

@COMMANDS.register_parser("-tag")
def parse_tag(parser, args, line, level):
    parts = args.strip().split()
    if not parts:
        raise ValueError(f"Missing tag name at {parser.scene.name} line {line}")
    
    tag = parts[0]
    # This is a meta-command that helps the parser organize chapters
    if tag in parser.current_chapter:
        print(f"[ERROR] Duplicate tag '{tag}' at line {line}")
    return {"cmd": "-tag", "args": tag}

@COMMANDS.register_parser("-go_file")
def parse_go_file(parser, args, line, level):
    return {"cmd": "-go_file", "args": args.strip(), "__line__": line}

@COMMANDS.register_runtime("-go_file")
def run_go_file(engine, args, block):
    parts = args.split()
    if len(parts) >= 2:
        scene, tag = parts[0], parts[1]
        engine.state["scene"] = scene
        engine._jump(scene, tag)
        return "jump"
    return "logic"

@COMMANDS.register_parser("-go_and_back")
def parse_go_and_back(parser, args, line, level):
    return {"cmd": "-go_and_back", "args": args.strip(), "__line__": line}

@COMMANDS.register_parser("-go_back")
def parse_go_back(parser, args, line, level):
    return {"cmd": "-go_back", "__line__": line}

@COMMANDS.register_runtime("-go_and_back")
def run_go_and_back(engine, args, block):
    resume_tag = block.get("resume_tag")
    current_scene = engine.state.get("scene")

    if resume_tag:
        # PUSH AS DICTIONARY
        engine.state.setdefault("return_stack", []).append({
            "scene": current_scene,
            "tag": resume_tag,
            "ip": 0 # Start at the beginning of the resume tag
        })

    parts = args.split()
    if len(parts) == 1:
        # Single argument means a local tag jump in the current scene
        target_scene = current_scene
        target_tag = parts[0]
    else:
        target_scene = parts[0]
        target_tag = parts[1] if len(parts) > 1 else "start"
    
    engine._jump(target_scene, target_tag)
    return "jump" # Tell engine we jumped

@COMMANDS.register_runtime("-go_back")
def run_go_back(engine, args, block):
    stack = engine.state.get("return_stack")
    if stack:
        ret = stack.pop()
        # Simply update the state. The engine's 'step' loop 
        # will see the new scene/tag on the next iteration.
        engine.state["scene"] = ret["scene"]
        engine.state["tag"] = ret["tag"]
        engine.state["ip"] = ret.get("ip", 0)
        engine.state.get("block_stack", []).clear()
        
        # return "jump" to tell the engine to restart the loop 
        # with the new scene/tag coordinates we just set.
        return "jump"
    
    return "logic"


# --- THE PICK SYSTEM ---
@COMMANDS.register_parser("-pick")
def parse_pick(parser, args, line_no, level):
    pick_id = f"pick_{line_no}"
    return {
        "cmd": "-pick",
        "args": args,
        "__line__": line_no,
        "expects_indent": True,
        "context_name": "pick_block",
        "pick_id": pick_id, # Parent level
        "node": {
            "blocks": [], 
            "choices": [], 
            "pick_id": pick_id # Inner level for the choices to see
        }
    }

@COMMANDS.register_runtime("-pick")
def run_pick(engine, args, block):
    engine.state["active_pick"] = {"data": block}
    return {
        "kind": "pick", 
        "choices": engine._build_choices(block["node"]),
        "scene": engine.state["scene"], 
        "tag": engine.state["tag"]
    }

# --- UI & MISC ---

@COMMANDS.register_parser("-nl")
def parse_nl(parser, args, line, level):
    return {"cmd": "-nl", "__line__": line}

@COMMANDS.register_runtime("-nl")
def run_nl(engine, args, block):
    return {"kind": "nl"}

@COMMANDS.register_parser("-next")
def parse_next(parser, args, line_no, level):
    return {
        "cmd": "-next",
        "args": args,
        "__line__": line_no,
        "expects_indent": False
    }

@COMMANDS.register_runtime("-next")
def run_next(engine, args, block):
    current_scene = engine.state.get("scene") 
    
    # Get scenes from the loader context or engine keys
    all_scenes = list(engine.scenes.keys()) 
    
    try:
        idx = all_scenes.index(current_scene)
        if idx + 1 < len(all_scenes):
            next_scene = all_scenes[idx + 1]
            # Perform the jump and explicitly return "jump"
            engine._jump(next_scene, "start")
            return "jump" 
    except ValueError:
        pass
    
    # If last scene, return "end"
    return "end"

@COMMANDS.register_parser("-end")
def parse_termination(parser, args, line, level):
    return {"cmd": "-end", "__line__": line}

@COMMANDS.register_runtime("-end")
def run_termination(engine, args, block):
    return "end"

@COMMANDS.register_parser("-stats")
def parse_stats(parser, args, line, level):
    return {"cmd": "-stats", "__line__": line}

# @COMMANDS.register_runtime("-stats")
# def run_stats(engine, args, block):
#     return "stats_screen"

