"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/runtime/engine.py
import copy
import re
from validators.expr import parse_mvar_args
from engine.runtime.interpolation import interpolate_text
from command_registry import COMMANDS
from utils.safe_eval import safe_eval_expr

class PickEngine:
    def __init__(self, game_data, story_order=None):
        self.scenes = game_data
        self.story_order = story_order or list(game_data.keys())

        self.state = {
            "scene": None,
            "tag": None,
            "ip": 0,
            "vars": {},
            "ui_grid": [None] * 4,
            "return_stack": [],
            "block_stack": [],
            "last_pick_snap": None,
            "active_pick": None,
            "finished": False
        }
        self.DEBUG = False
        self.main_game_state_backup = None

    def enter_modal_view(self, scene_name, tag_name):
        """Backup story state and switch to modal scene (stats screen)."""
        if not self.main_game_state_backup:
            self.main_game_state_backup = self.export_state()
        self.state["scene"] = scene_name
        self.state["tag"] = tag_name
        self.state["ip"] = 0
        self.state["block_stack"] = []
        self.state["return_stack"] = []
        self.state["active_pick"] = None
        self.state["finished"] = False

    def exit_modal_view(self):
        """Restore story state; carry vars over (settings/unit changes persist)."""
        if self.main_game_state_backup:
            carried_vars = copy.deepcopy(self.state["vars"])
            self.load_state(self.main_game_state_backup)
            self.state["vars"] = carried_vars
            self.main_game_state_backup = None

    
    def export_state(self):
        return {
            "scene": self.state["scene"],
            "tag": self.state["tag"],
            "ip": self.state["ip"],
            "vars": copy.deepcopy(self.state["vars"]),
            "ui_grid": copy.deepcopy(self.state.get("ui_grid", [None]*4)),
            "return_stack": copy.deepcopy(self.state["return_stack"]),
            "block_stack": copy.deepcopy(self.state["block_stack"]),
            "finished": self.state["finished"],
            "active_pick": copy.deepcopy(self.state["active_pick"]),
            "modal_backup": copy.deepcopy(self.main_game_state_backup),
        }

    def load_state(self, snapshot):
        self.state = copy.deepcopy(snapshot)
        self.main_game_state_backup = snapshot.get("modal_backup")

    # --- Core Execution ---
    def start(self, scene, tag, variables=None):
        self.state["vars"] = variables or {}
        self._jump(scene, tag)
        self.state["finished"] = False
        if hasattr(self, "session"):
            for k, v in self.state["vars"].items():
                self.session.record_var_change(k, v, "START")


    def step(self, choice_id=None):
        if self.state["finished"]:
            return {"kind": "end", "scene": self.state["scene"], "tag": self.state["tag"]}

        if choice_id:
            self._handle_choice(choice_id)

        text_buffer = []
        display_buffer = []
        display_stack = [display_buffer]  # Tracks container nesting level
        notifications = []
        current_flow = ""

        def flush_flow():
            nonlocal current_flow
            if current_flow.strip():
                display_stack[-1].append({
                    "type": "text",
                    "content": current_flow.strip()
                })
                current_flow = ""

        while True:
            node = self._get_node(self.state["scene"], self.state["tag"])

            # 1. Resolve Context
            if self.state["block_stack"]:
                ctx = self.state["block_stack"][-1]
                if isinstance(ctx, list):
                    ctx = {"items": ctx, "ip": 0}
                    self.state["block_stack"][-1] = ctx
                items = ctx["items"]
                current_ctx = ctx
            else:
                if not node:
                    self.state["finished"] = True
                    return {
                        "kind": "end",
                        "text": text_buffer,
                        "display": display_buffer,
                        "scene": self.state["scene"],
                        "tag": self.state["tag"]
                    }
                items = self._merge_items(node)
                current_ctx = self.state

            # 2. Check for End of Node/Block
            if current_ctx["ip"] >= len(items):
                if self.state.get("active_pick") or self.state["finished"]:
                    flush_flow()
                    return {"kind": "pause", "text": text_buffer, "display": display_buffer, "scene": self.state["scene"], "tag": self.state["tag"]}

                if self.state["block_stack"]:
                    self.state["block_stack"].pop()
                    continue 
                
                if node.continuations:
                    next_s, next_t = node.continuations[0][:2]
                    if next_s != "__NEXT__" and next_t != self.state["tag"]:
                        self._jump(next_s, next_t)
                        continue 

                if self.state["return_stack"]:
                    if self.DEBUG:
                        print("[DEBUG] Return stack found. Executing implicit return.")
                    
                    ret = self.state["return_stack"].pop()
                    if isinstance(ret, dict):
                        self.state["scene"] = ret["scene"]
                        self.state["tag"] = ret["tag"]
                        self.state["ip"] = ret.get("ip", 0)
                    else:
                        self.state["scene"], self.state["tag"], self.state["ip"] = ret
                        
                    self.state["block_stack"].clear()
                    continue 

                sc = self.scenes.get(self.state["scene"])
                if sc and sc.nodes:
                    tags = list(sc.nodes.keys())
                    try:
                        idx = tags.index(self.state["tag"])
                        if idx + 1 < len(tags):
                            if display_buffer or text_buffer:
                                flush_flow()
                                return {"kind": "pause", "text": text_buffer, "display": display_buffer, "scene": self.state["scene"], "tag": self.state["tag"]}
                            
                            if self.DEBUG:
                                print(f"[DEBUG] Implicit fallthrough to tag: {tags[idx+1]}")
                            self._jump(self.state["scene"], tags[idx + 1])
                            continue
                    except: pass

                flush_flow()
                self.state["finished"] = True
                return {"kind": "end", "text": text_buffer, "display": display_buffer, "scene": self.state["scene"], "tag": self.state["tag"]}

            # 3. Execute Next Item
            item = items[current_ctx["ip"]]
            current_ctx["ip"] += 1

            if "kind" not in item:
                k = "pick" if item.get("cmd") in ("-pick", "-pick_once") else "block"
                item = {"kind": k, "data": item, "line": item.get("__line__", 0)}

            if item["kind"] == "text":
                raw_text = item["data"].get("text", "")
                
                if not raw_text.strip():
                    if current_flow.strip():
                        display_stack[-1].append({"type": "text", "content": current_flow.strip()})
                        current_flow = ""
                    if not display_stack[-1] or display_stack[-1][-1].get("component") != "blank_line":
                        display_stack[-1].append({"type": "component", "component": "blank_line"})
                    continue
                else:
                    if current_flow and not current_flow.endswith(" ") and not current_flow.endswith("\n"):
                        current_flow += " "
                    current_flow += interpolate_text(raw_text, self._eval_vars())
                continue

            if item["kind"] == "pick":
                flush_flow()
                pick_u_id = item["data"].get("u_id") or item["data"].get("pick_id")
                self.state["active_pick"] = {"data": item["data"], "u_id": pick_u_id}
                return {
                    "kind": "pick", "canonical_id": pick_u_id, "text": text_buffer, "display": display_buffer,
                    "choices": self._build_choices(item["data"]),
                    "scene": self.state["scene"], "tag": self.state["tag"], "data": item["data"]
                }
                
            if item["kind"] == "block":
                # REMOVED: flush_flow() from here to preserve continuous text lines
                cmd_data = item["data"]
                cmd_name = cmd_data.get("cmd", "")

                if cmd_name == "-pic":
                    pass 

                res = self._execute_block(item["data"])

                # Intercept container stack control bounds
                if isinstance(res, dict) and res.get("kind") == "stat_block_open":
                    flush_flow()
                    new_block = {
                        "type": "component",
                        "component": "stat_block",
                        "props": {},
                        "children": []
                    }
                    display_stack[-1].append(new_block)
                    display_stack.append(new_block["children"])
                    continue

                if isinstance(res, dict) and res.get("kind") == "stat_block_close":
                    flush_flow()
                    if len(display_stack) > 1:
                        display_stack.pop()
                    continue

                if isinstance(res, dict) and res.get("kind") == "notification":
                    notifications.append(res)
                    continue

                if res == "descend_skip_siblings":
                    while current_ctx["ip"] < len(items):
                        next_item = items[current_ctx["ip"]]
                        cmd_to_check = next_item.get("data", {}).get("cmd", "")
                        if cmd_to_check in ("-elseif", "-else"):
                            current_ctx["ip"] += 1
                        else:
                            break
                    continue 

                if res == "nl" or (isinstance(res, dict) and res.get("kind") == "nl"):
                    current_flow += "\n"
                    continue

                if res == "jump": 
                    continue
 
                if res in ("pause", "next", "user_input", "jump_pause"):
                    flush_flow()
                    kind = "pause" if res == "jump_pause" else res
                    block_u_id = item["data"].get("u_id") or f"gen::{item.get('line')}"
 
                    frame = {
                        "kind": kind, 
                        "canonical_id": block_u_id, 
                        "text": text_buffer,
                        "display": display_buffer,
                        "scene": self.state["scene"], 
                        "tag": self.state["tag"]
                    }
                    if kind == "user_input":
                        frame["user_input_var"]    = self.state.get("pending_user_input_var", "")
                        frame["user_input_prompt"] = self.state.get("pending_user_input_prompt", "")
                    return frame

 
                if res == "checkpoint_loaded":
                    return {
                        "kind":         "pause",
                        "canonical_id": "",
                        "text":         text_buffer,
                        "display":      display_buffer,
                        "scene":        self.state["scene"],
                        "tag":          self.state["tag"],
                    }

                if isinstance(res, dict) and res.get("kind") == "display":
                    flush_flow()  # ADDED: Flush text block before rendering visual plugins/images
                    display_stack[-1].append({
                        "type":      "component",
                        "component": res["component"],
                        "props":     res["props"],
                    })
                    continue

                continue

            if current_ctx["ip"] >= len(items) and text_buffer:
                 return {
                    "kind": "pause", "text": text_buffer, "display": display_buffer,
                    "scene": self.state["scene"], "tag": self.state["tag"], "notifications": notifications
                }

    def _execute_block(self, block):
        cmd = block["cmd"]
        args = block.get("args", "").strip() # Clean up whitespace

        # MOVE STRUCTURAL COMMANDS TO THE TOP
        # These are the "bones" of the engine; they shouldn't be overridden by plugins
        if cmd in ("-if", "-elseif", "-else"):
            if cmd == "-else":
                condition_met = True
            else:
                # Only eval if there is actually a string to eval
                condition_met = self._eval(args) if args else False

            if condition_met:
                block_uid = block.get("u_id")
                # Use getattr or check truthiness to ensure session isn't None
                if block_uid and getattr(self, "session", None):
                    self.session.commit_map_node(block_uid)

                nested_node = block.get("node", {})
                nested_items = self._merge_dict_items(nested_node)

                self.state["block_stack"].append({
                    "items": nested_items, 
                    "ip": 0
                })
                return "descend_skip_siblings" 

            return "skip"

        if cmd == "-go":
            self._jump(self.state["scene"], args)
            return "jump"

        if cmd == "-go_file":
            self.state["vars"] = {k: v for k, v in self.state["vars"].items()
                if not k.startswith("_")}
            self._jump(*args.split())
            return "jump"

        if cmd == "-go_back":
            if self.state["return_stack"]:
                ret = self.state["return_stack"].pop()
                if isinstance(ret, dict):
                    self.state["scene"] = ret["scene"]
                    self.state["tag"] = ret["tag"]
                    self.state["ip"] = ret.get("ip", 0)
                else:
                    self.state["scene"], self.state["tag"], self.state["ip"] = ret

                self.state["block_stack"].clear()
                return "jump" # Tell the loop to restart at the return location
            else:
                if self.DEBUG: print("[DEBUG] go_back called with empty stack!")
                return "logic"

        if cmd == "-pause": return "pause"

        if cmd == "-next":
            self.state["vars"] = {k: v for k, v in self.state["vars"].items() 
                if not k.startswith("_")}
            next_scene_name = self._get_next_scene()
            if next_scene_name:
                scene_obj = self.scenes.get(next_scene_name)
                first_tag = list(scene_obj.nodes.keys())[0] if scene_obj.nodes else "start"
                self._jump(next_scene_name, first_tag)
                return "jump_pause"
            else:
                # config has run out of sequential files, raise validation halt.
                raise ValueError(
                    f"Engine Error: Visual compilation reached a structural '-next' command sequence "
                    f"inside your final registered story scene ('{self.state['scene']}'). "
                    f"Please terminate your final sequence paths using an explicit '-end' command block instead."
                )

        # NOW check the Registry for cosmetic/plugin commands
        result = COMMANDS.run_runtime(cmd, self, args, block)

        if result is not None:
            return result 

        return "logic"


    # --- Logic & Commands ---
    def _do_mvar(self, args):
        name, op, rhs_str = parse_mvar_args(args)
        ev = self._eval_vars()  # aliased vars
        storage_key = f"_{name}" if f"_{name}" in self.state["vars"] \
                    and name not in self.state["vars"] else name
        cur = ev.get(storage_key, ev.get(name, 0))
        rhs = safe_eval_expr(rhs_str, ev)   # ← ev not self.state["vars"]
        ops = {
            "=":  lambda a, b: b,
            "+=": lambda a, b: a + b,
            "-=": lambda a, b: a - b,
            "*=": lambda a, b: a * b,
            "/=": lambda a, b: a / b,
            "%+": lambda a, b: a + (100 - a) * (b / 100),
            "%-": lambda a, b: a - a * (b / 100),
        }
        self.state["vars"][storage_key] = ops[op](cur, rhs)
        if hasattr(self, "session"):
            loc = f"{self.state['scene']}:{self.state['tag']}"
            self.session.record_var_change(storage_key,
                                        self.state["vars"][storage_key], loc)

    # --- Helpers ---
    def _jump(self, scene, tag):
        self.state["scene"] = scene
        self.state["tag"] = tag
        self.state["ip"] = 0 
        self.state["block_stack"].clear()

        node = self._get_node(scene, tag)
        if node and hasattr(node, 'u_id') and hasattr(self, "session"):
            # The ONLY place a Tag enters the Tape is upon jumping to it
            self.session.commit_map_node(node.u_id)

    def _eval(self, expr):
        result = safe_eval_expr(expr, self._eval_vars())  # ← ev
        # print(f"[EVAL DEBUG] {expr} -> {result}")
        return bool(result)

    def _get_node(self, scene, tag):
        sc = self.scenes.get(scene)
        return getattr(sc, "nodes", getattr(sc, "tags", {})).get(tag)


    def _get_next_scene(self):
        scene_names = self.story_order 
        
        try:
            current_idx = scene_names.index(self.state["scene"])
            if current_idx + 1 < len(scene_names):
                return scene_names[current_idx + 1]
        except (ValueError, AttributeError):
            pass
        return None


    def _merge_items(self, node):
        """Top-level node merger. Now uses the same logic as nested blocks."""
        # treat the node as a node_dict to reuse the logic
        node_dict = {
            "text": getattr(node, "text_items", []),
            "blocks": getattr(node, "blocks", [])
        }
        return self._merge_dict_items(node_dict)

    def _merge_dict_items(self, node_dict):
        """Merges Text and Blocks into one timeline based on line number."""
        raw_items = []
        
        # Collect everything into one flat list
        for t in node_dict.get("text", []):
            if t.get("type") == "choice_text": continue
            raw_items.append({"kind": "text", "data": t, "line": t.get("__line__", 0)})
            
        for b in node_dict.get("blocks", []):
            # Check if it's a pick or a standard block
            cmd = b.get("cmd", "")
            k = "pick" if cmd in ("-pick", "-pick_once", "-single_pick") else "block"
            raw_items.append({"kind": k, "data": b, "line": b.get("__line__", 0)})
            
        # Sort the unified list by line number
        return sorted(raw_items, key=lambda x: x["line"])


    def _build_choices(self, pick_block):
        ev          = self._eval_vars()
        used        = self.state["vars"].get("_used_choices", [])
        pick_id     = pick_block.get("pick_id", "")
        choices_out = []
        parent_is_pick_once = pick_block.get("cmd") == "-pick_once" or pick_block.get("is_once_block", False)

        for idx, c in enumerate(pick_block["node"]["choices"]):
            choice_id   = c.get("choice_id", f"{pick_id}::c{idx}")
            subtype     = c.get("choice_subtype", "standard")
            if parent_is_pick_once:
                subtype = "pick_once"
            cond        = c.get("cond", "").strip()      
            merged_logic = c.get("merged_logic", "")

            # --- Extract target tag for frontend navigation compatibility ---
            target_tag = None
            for b in c.get("blocks", []):
                if b.get("cmd") == "-go":
                    target_tag = b.get("args", "").strip()
                    break
            if not target_tag and c.get("continuation") and len(c["continuation"]) >= 2:
                target_tag = c["continuation"][1]

            raw_label = "".join(
                t["text"] for t in c.get("text", [])
                if t.get("type") == "choice_text"
            )
            label = interpolate_text(raw_label, ev) if raw_label else f"Choice {idx+1}"

            status = "available"
            if subtype == "pick_once" and choice_id in used:
                status = "used"

            if status == "available" and cond:
                is_if_choice = c.get("is_if_choice", False)
                try:
                    cond_met = bool(safe_eval_expr(cond, ev))
                except Exception:
                    cond_met = False

                if not cond_met:
                    status = "hidden" if is_if_choice else "locked"

            choices_out.append({
                "id":           f"c{idx}",
                "choice_id":    choice_id,
                "label":        label,
                "status":       status,           
                "subtype":      subtype,
                "blocks":       c.get("blocks", []),
                "continuation": c.get("continuation"),
                "target_tag":   target_tag,  # Restore bridge key
            })

        return choices_out

    def _handle_choice(self, choice_id):
        if not self.state["active_pick"]:
            return

        idx         = int(choice_id[1:])
        pick_data   = self.state["active_pick"]["data"]
        choice_data = pick_data["node"]["choices"][idx]
        choice_uid  = choice_data.get("choice_id")

        is_pick_once_type = (
            choice_data.get("choice_subtype") == "pick_once" or 
            pick_data.get("cmd") == "-pick_once" or 
            pick_data.get("is_once_block") == True
        )

        if hasattr(self, "session") and choice_uid:
            self.session.commit_map_node(choice_uid)

        # Record pick_once usage
        if is_pick_once_type:
            if "_used_choices" not in self.state["vars"]:
                self.state["vars"]["_used_choices"] = []
            if choice_uid and choice_uid not in self.state["vars"]["_used_choices"]:
                self.state["vars"]["_used_choices"].append(choice_uid)
                if hasattr(self, "session"):
                    self.session.record_var_change(
                        "_used_choices",
                        self.state["vars"]["_used_choices"],
                        f"{self.state['scene']}:{self.state['tag']}"
                    )

        self.state["active_pick"] = None
        items = self._merge_dict_items(choice_data)

        # 1. Handle explicit continuation (e.g., -go inside a choice)
        if choice_data.get("continuation"):
            if items:
                self.state["block_stack"].append({"items": items, "ip": 0})
            self._jump(*choice_data["continuation"][:2])
            return

        # 2. Handle -single_pick automatic flow
        if pick_data.get("cmd") == "-single_pick":
            # Check if the chosen inner items already contain an explicit routing override 
            # (like an internal -go, -go_file, -next, or -end)
            has_explicit_route = any(
                isinstance(i, dict) and i.get("data", {}).get("cmd") in ("-go", "-go_file", "-next", "-go_back", "-end")
                for i in items
            )

            if not has_explicit_route:
                sc = self.scenes.get(self.state["scene"])
                tags = list(sc.nodes.keys())
                try:
                    curr_idx = tags.index(self.state["tag"])
                    if curr_idx + 1 < len(tags):
                        next_tag = tags[curr_idx + 1]
                        
                        # Verify the next tag isn't just an orphan marker or termination state
                        next_node = self._get_node(self.state["scene"], next_tag)
                        
                        # Look for an explicit -end inside the next node's structural blocks list
                        is_next_node_end = False
                        if next_node and hasattr(next_node, 'blocks'):
                            is_next_node_end = any(b.get("cmd") == "-end" for b in next_node.blocks)

                        if is_next_node_end:
                            # If next tag immediately triggers an end, do not schedule a return track step.
                            # Just add choice items to the stack and let fallthrough execute it.
                            if items:
                                self.state["block_stack"].append({"items": items, "ip": 0})
                        else:
                            # tracking path routing execution
                            if items:
                                self.state["return_stack"].append({
                                    "scene": self.state["scene"],
                                    "tag": next_tag,
                                    "ip": 0
                                })

                                if next_node and hasattr(next_node, "u_id") and hasattr(self, "session"):
                                    self.session.commit_map_node(next_node.u_id)

                                self.state["block_stack"].append({"items": items, "ip": 0})
                            else:
                                self._jump(self.state["scene"], next_tag)
                except Exception:
                    pass
            else:
                # If the choice text contains an explicit redirection (-go, etc.), 
                # append the items and let them execute their own jump logic naturally.
                if items:
                    self.state["block_stack"].append({"items": items, "ip": 0})
            return

        if items:
            self.state["block_stack"].append({"items": items, "ip": 0})

        if choice_data.get("continuation"):
            self._jump(*choice_data["continuation"][:2])
        elif pick_data["cmd"] == "-single_pick":
            sc   = self.scenes.get(self.state["scene"])
            tags = list(sc.nodes.keys())
            try:
                curr_idx = tags.index(self.state["tag"])
                if curr_idx + 1 < len(tags):
                    self._jump(self.state["scene"], tags[curr_idx + 1])
            except Exception:
                pass


    def backtrack(self):
        if self.state["history"]:
            self.state = self.state["history"].pop()
            return True
        return False


    def cheat_rewind(self):
        if self.state.get("last_pick_snap"):
            self.state = copy.deepcopy(self.state["last_pick_snap"])
            return True
        return False


    def get_random_int(self, min_val, max_val):
        """Standardized random call that uses the session's deterministic RNG."""
        if hasattr(self, "session"):
            return self.session.rng.randint(min_val, max_val)
        return random.randint(min_val, max_val)


    def _cmd_entropy(self, args, block):
        # Expected: name seed lo-hi (e.g., "luck 123 1-100")
        parts = args.split()
        if len(parts) != 3:
            return

        var_name, seed_str, range_str = parts
        
        try:
            local_seed = int(seed_str)
            lo_str, hi_str = range_str.split("-")
            lo, hi = int(lo_str), int(hi_str)
        except ValueError:
            return

        # Unique ID for this specific line in the game
        line_no = block.get("__line__", "unknown")
        entropy_id = f"{self.state['scene']}:{self.state['tag']}:{line_no}"

        if hasattr(self, "session"):
            val = self.session.generate_entropy(entropy_id, lo, hi, local_seed)
            self.state["vars"][var_name] = val
            self.session.record_var_change(var_name, val, entropy_id)
        else:
            # Fallback for standalone engine testing
            val = random.Random(seed_str).randint(lo, hi)
            self.state["vars"][var_name] = val


    def _interpolate(self, text):
        """Replaces ${var_name} with the actual value from state['vars']."""
        def replacement(match):
            var_name = match.group(1)
            # Return the variable value, or a clear 'undefined' marker for debugging
            return str(self.state["vars"].get(var_name, f"<undefined:{var_name}>"))
        
        return re.sub(r"\${(\w+)}", replacement, text)


    def _eval_vars(self):
        """Returns vars dict with tvar aliases so ${stat} finds _stat."""
        v = dict(self.state["vars"])
        for k in list(v.keys()):
            if k.startswith("_"):
                alias = k[1:]
                if alias not in v:   # don't overwrite real vars
                    v[alias] = v[k]
        return v