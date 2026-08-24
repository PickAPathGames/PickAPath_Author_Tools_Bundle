"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# simulator.py
import random
import copy
from collections import defaultdict
from tools.branch_coverage import BranchCoverage
from collections import deque
from utils.safe_eval import safe_eval_expr


class _SimEngineAdapter:
    """
    Minimal shim so COMMANDS.run_runtime() can write to simulator.variables.
    Only exposes state.vars; other engine attributes stay None/empty.
    """
    def __init__(self, sim):
        self.state = {
            "vars": dict(sim.variables),
            "scene": sim.current_scene,
            "tag": sim.current_tag,
            "ui_grid": [None] * 4,
            "return_stack": [],
            "block_stack": [],
            "active_pick": None,
            "finished": False,
        }
        self.session = None

    def _eval_vars(self):
        v = dict(self.state["vars"])
        for k in list(v.keys()):
            if k.startswith("_"):
                alias = k[1:]
                if alias not in v:
                    v[alias] = v[k]
        return v

    def _jump(self, scene, tag):
        self.state["scene"] = scene
        self.state["tag"] = tag
        self.state["ip"] = 0


class Simulator:

    COMMAND_RULES = {
        "-pick":       {"resolves": True,  "may_fallthrough": False},
        "-single_pick":{"resolves": True,  "may_fallthrough": True},
        "-if":         {"resolves": True,  "may_fallthrough": True},
        "-go":         {"resolves": True,  "may_fallthrough": False},
        "-go_file":    {"resolves": True,  "may_fallthrough": False},
        "-go_and_back":{"resolves": True,  "may_fallthrough": False},
        "-go_back":    {"resolves": True,  "may_fallthrough": False},
        "-next":       {"resolves": True,  "may_fallthrough": False},
        "-end":        {"resolves": True,  "may_fallthrough": False},
        "-mvar":       {"resolves": False, "may_fallthrough": True},
        "-tvar":       {"resolves": False, "may_fallthrough": True},
        "-entropy":    {"resolves": False, "may_fallthrough": True},
        "-snapshot":   {"resolves": False, "may_fallthrough": True},
    }

    FLOW_FALLTHROUGH = 0
    FLOW_JUMP = 1
    FLOW_END = 2
    FLOW_RESOLVED = 3
    FLOW_WAIT = object()
    FLOW_PICK = object()
    FLOW_PAUSE = object()

    # def __init__(self, scenes, config=None, seed=None):
    def __init__(self, scenes, config=None, seed=None, branch_coverage=None, interactive=False):
        """
        scenes: dict[str, Scene]
        config: optional runtime config
        seed: optional deterministic seed
        """
        self.scenes = scenes
        self.config = config or {}
        self.random = random.Random(seed)
        self.branch_coverage = branch_coverage
        self.interactive = interactive

        self.reset()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def reset(self):
        self.variables = {}
        self.current_scene = None
        self.current_tag = None
        self.current_node = None

        self.visited_nodes = defaultdict(int)
        self.snapshots = []
        self.errors = {}
        self._error_keys = set()

        self.used_choices = set()  # for pick_once
        self.call_stack = []
        self.return_stack = []

        self.flow_resolved = False

        self.coverage_pick = defaultdict(int)
        self.coverage_if = defaultdict(int)
        self.trace_window = deque(maxlen=20)

        self.node_ip = 0
        self.waiting_pick = None

        self.DEBUG = False

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def simulate_once(self, start_scene, start_tag, initial_vars=None):
        self.reset()
        base = copy.deepcopy(self.config.get("vars", {}))
        if initial_vars: base.update(copy.deepcopy(initial_vars))
        self.variables = base

        self.current_scene = start_scene
        self.current_tag = start_tag

        loop_limit = self.config.get("loop_limit", 500)

        # sc = self.scenes.get(self.current_scene)
        # for tag, node in sc.nodes.items():
        #     print(f"DEBUG: Tag='{tag}' Line={node.line}")

        while True:
            # 1. ALWAYS re-fetch based on current state
            node = self._get_node(self.current_scene, self.current_tag)
            if not node:
                self._error(f"Missing node {self.current_scene}:{self.current_tag}")
                break

            key = (self.current_scene, self.current_tag)
            self.visited_nodes[key] += 1
            
            if self.visited_nodes[key] > loop_limit:
                self._error_at(f"Loop limit exceeded at {key[0]}:{key[1]}", kind="FLOW", line=node.line)
                break

            # 3. CORE EXECUTION
            result = self._execute_node(node)

            # 4. HANDLE RESULTS
            if result in (self.FLOW_PICK, self.FLOW_PAUSE, self.FLOW_END):
                break

            if result == self.FLOW_JUMP:
                # Explicit -go/-next has already called self._jump()
                continue

            if result == self.FLOW_FALLTHROUGH:
                nxt = self._implicit_continuation(node)
                if nxt:
                    # PERFORM THE JUMP
                    self.current_scene = nxt[0]
                    self.current_tag = nxt[1]
                    # We do NOT call continue here yet; we let the loop 
                    # restart naturally to fetch the NEW node.
                    continue 
                else:
                    break

        return self._build_result_package()


    def _execute_node(self, node):
        self.current_node = node
        self.node_ip = 0 # (Keep this for external tracking if needed)
        
        items = self._merge_items(node)
        
        return self._execute_item_sequence(items)

    def _execute_inline_node(self, node_dict):
        """Execute internal blocks (inside an IF or a Choice)."""
        items = self._merge_items_for_dict(node_dict)
        
        return self._execute_item_sequence(items)

    def _merge_items(self, node):
        merged = []
        for t in node.text_items:
            merged.append({"kind": "text", "line": t["__line__"], "data": t})
        
        for b in node.blocks:
            # We treat -pick and -pick_once the same here because they are normalized
            kind = "pick" if b["cmd"] in ("-pick", "-pick_once", "-single_pick") else "block"
            merged.append({"kind": kind, "line": b["__line__"], "data": b})

        merged.sort(key=lambda x: x["line"])
        return merged

    def execute_block(self, block):
        cmd = block["cmd"]
        args = block.get("args", "")

        # --- Picks ---
        if cmd in ("-pick", "-pick_once", "-pick_if", "-single_pick"):
            self.flow_resolved = True
            return self._execute_pick(block)

        # --- Terminals ---
        if cmd == "-end":
            self.flow_resolved = True
            return self.FLOW_END

        if cmd == "-pause":
            return self.FLOW_PAUSE if self.interactive else self.FLOW_FALLTHROUGH

        # --- Navigation ---
        if cmd == "-go":
            self.flow_resolved = True
            return self._cmd_go_jump(args)

        if cmd == "-go_file":
            self.flow_resolved = True
            return self._cmd_go_file(args)

        if cmd == "-go_and_back":
            self.flow_resolved = True
            return self._cmd_go_and_back(block)

        if cmd == "-go_back":
            self.flow_resolved = True
            return self._cmd_go_back(block)

        if cmd == "-next":
            self.flow_resolved = True
            return self._cmd_next()

        # --- Variables (handled directly to avoid engine.state coupling) ---
        if cmd == "-mvar":
            return self._cmd_mvar(args)

        if cmd == "-tvar":
            return self._cmd_tvar(args)

        if cmd == "-entropy":
            return self._cmd_entropy(args)

        if cmd == "-snapshot":
            return self._cmd_snapshot(args)

        if cmd == "-reset_pick":
            return self._cmd_reset_pick()

        # --- Route remaining commands to registry (UI, string, list, etc.) ---
        # These are mostly display-only and don't affect simulation logic.
        # Use a minimal shim so registry commands can read/write self.variables.
        try:
            from command_registry import COMMANDS
            adapter = _SimEngineAdapter(self)
            result = COMMANDS.run_runtime(cmd, adapter, args, block)
            if result is not None and result not in ("logic", "nl", "pause"):
                # Sync any var writes back from adapter
                self.variables.update(adapter.state["vars"])
        except Exception:
            pass

        return self.FLOW_FALLTHROUGH


    # ------------------------------------------------------------------
    # Variable commands
    # ------------------------------------------------------------------

    def _cmd_mvar(self, args):
        from validators.expr import parse_mvar_args
        try:
            name, op, rhs = parse_mvar_args(args)
        except Exception as e:
            self._error(f"mvar parse error: {args} ({e})")
            return self.FLOW_FALLTHROUGH 

        cur = self.variables.get(name, 0)
        rhs_val = self._eval(rhs)

        try:
            if op == "=":   new_val = rhs_val
            elif op == "+=": new_val = cur + rhs_val
            elif op == "-=": new_val = cur - rhs_val
            elif op == "*=": new_val = cur * rhs_val
            elif op == "/=": new_val = cur / rhs_val
            elif op == "%+": new_val = cur + (100 - cur) * (rhs_val / 100)
            elif op == "%-": new_val = cur - cur * (rhs_val / 100)
            else:
                self._error(f"mvar unsupported operator '{op}'")
                return self.FLOW_FALLTHROUGH

            self.variables[name] = new_val
        except Exception as e:
            self._error_at(f"mvar eval error: {args} ({e})", kind="MVAR")
            return self.FLOW_FALLTHROUGH

        if self.DEBUG:
            print(f"[MVAR] {name} {op} {rhs} -> {new_val}")
        return self.FLOW_FALLTHROUGH

    def _cmd_tvar(self, args):
        if "=" in args:
            name, expr = [p.strip() for p in args.split("=", 1)]
        else:
            parts = args.split(None, 1)
            if len(parts) < 2:
                self._error(f"tvar invalid format: {args}")
                return self.FLOW_FALLTHROUGH
            name, expr = parts[0].strip(), parts[1].strip()

        val = self._eval(expr)

        if name in self.variables:
            old = self.variables[name]
            is_old_numeric = isinstance(old, (int, bool))
            is_new_numeric = isinstance(val, (int, bool))

            if not (is_old_numeric and is_new_numeric):
                if type(old) is not type(val):
                    self._error_at(
                        f"tvar type change for '{name}': {type(old).__name__} -> {type(val).__name__}",
                        kind="TVAR",
                    )

        self.variables[name] = val
        if self.DEBUG:
            print(f"[TVAR] {name} = {val}")
        return self.FLOW_FALLTHROUGH

    def _cmd_entropy(self, args):
        parts = args.split(None, 2)
        if len(parts) != 3:
            self._error_at(f"entropy expects: <name> <seed> <lo-hi>, got: '{args}'", kind="ENTROPY")
            return self.FLOW_FALLTHROUGH

        name, seed_str, rng = parts
        try:
            seed = int(seed_str)
            lo_str, hi_str = rng.split("-", 1)
            lo, hi = int(lo_str), int(hi_str)
        except (ValueError, Exception):
            self._error_at(f"entropy params invalid: '{args}'", kind="ENTROPY")
            return self.FLOW_FALLTHROUGH

        if lo > hi: lo, hi = hi, lo

        line_no = getattr(self, "line_no", "entropy")
        entropy_id = f"{self.current_scene}:{self.current_tag}:{line_no}"

        if hasattr(self, "session") and self.session is not None:
            val = self.session.generate_entropy(
                entropy_id=entropy_id,
                generator=lambda: random.Random(seed).randint(lo, hi),
            )
        else:
            val = random.Random(seed).randint(lo, hi)

        self.variables[name] = val
        return self.FLOW_FALLTHROUGH

    def _cmd_snapshot(self, args):
        keys = args if isinstance(args, list) else args.split()
        snap = {
            "scene": self.current_scene,
            "tag": self.current_tag,
            "vars": {k: self.variables.get(k) for k in keys},
        }
        self.snapshots.append(snap)
        return self.FLOW_FALLTHROUGH


    # ------------------------------------------------------------------
    # Flow control
    # ------------------------------------------------------------------

    def _cmd_go(self, tag):
        if self.DEBUG:
            print(f"[SIM] go -> {tag}")
        self._jump(self.current_scene, tag)
        return self.FLOW_JUMP

    def _cmd_go_file(self, args):
        parts = args.split()
        scene = parts[0]
        tag = parts[1] if len(parts) > 1 else self._get_first_tag(scene)
        
        if self.DEBUG:
            print(f"[SIM] go_file -> {scene}:{tag} (clearing used choices + temp vars)")
            
        # Wipe temp vars on file transition (mirrors engine._cmd_go_file)
        self.variables = {k: v for k, v in self.variables.items() if not k.startswith("_")}
        self.used_choices.clear()
        
        self._jump(scene, tag)
        return self.FLOW_JUMP

    def _cmd_go_and_back(self, block):
        parts = block["args"].split()
        if not parts:
            self._error("go_and_back missing target arguments")
            return self.FLOW_FALLTHROUGH

        if len(parts) == 1:
            # Local file jump: use current scene
            scene = self.current_scene
            tag = parts[0]
        else:
            scene = parts[0]
            tag = parts[1]

        resume_tag = block.get("resume_tag")
        if not resume_tag:
            self._error("go_and_back missing resume_tag")
            return self.FLOW_FALLTHROUGH

        # push return point
        self.return_stack.append(
            (self.current_scene, resume_tag)
        )

        if self.DEBUG:
            print(f"[SIM] go_and_back -> {scene}:{tag} resume={resume_tag}")

        self._jump(scene, tag)
        return self.FLOW_JUMP

    def _cmd_go_back(self, block):
        if not self.return_stack:
            self._error_at(
                "go_back with empty return stack",
                kind="FLOW",
                scene=self.current_scene,
                tag=self.current_tag,
                line=block.get("__line__"),
            )
            return self.FLOW_END

        scene, tag = self.return_stack.pop()

        if self.DEBUG:
            print(f"[SIM] go_back -> {scene}:{tag}")

        self._jump(scene, tag)
        return self.FLOW_JUMP

    def _cmd_next(self):
        # Accept both key names for backward compat; story_order matches engine
        order = self.config.get("story_order") or self.config.get("scene_order", [])
        
        if not order:
            self._error_at(
                "-next requires story_order (or scene_order) in simulator config",
                kind="FLOW",
            )
            return self.FLOW_END

        try:
            idx = order.index(self.current_scene)

            if idx + 1 >= len(order):
                if self.DEBUG: print(f"[SIM] Reached end of story_order at {self.current_scene}")
                return self.FLOW_END
            next_scene = order[idx + 1]
        except ValueError:
            self._error_at(f"Current scene {self.current_scene} not in story_order", kind="FLOW")
            return self.FLOW_END

        # Wipe temp vars on file transition (mirrors engine._cmd_next)
        self.variables = {k: v for k, v in self.variables.items() if not k.startswith("_")}
        self.used_choices.clear()

        # Clear return stack on chapter transition (fresh start)
        if self.return_stack:
            if self.DEBUG: print("[SIM] -next clearing return stack")
            self.return_stack.clear()

        first_tag = self._get_first_tag(next_scene)
        if first_tag is None:
            self._error_at(
                f"next resolved to scene '{next_scene}' with no tags",
                kind="FLOW",
            )
            return self.FLOW_END

        self._jump(next_scene, first_tag)
        if self.interactive:
            return self.FLOW_PAUSE 
        
        return self.FLOW_JUMP


    def _cmd_go_jump(self, tag):
        self._jump(self.current_scene, tag)
        return self.FLOW_JUMP


    def _implicit_continuation(self, node):
        # 1. Check explicit continuations (e.g., from -next or parser-injected jumps)
        if node.continuations:
            scene, tag = node.continuations[0][:2]
            if scene == "__NEXT__": return None
            return (scene, tag) if tag else None

        # 2. Sequential File Scan (The Engine Behavior)
        sc = self.scenes.get(self.current_scene)
        if sc and sc.nodes:
            # Sort strictly by line number
            sorted_nodes = sorted(sc.nodes.values(), key=lambda n: n.line)
            
            for n in sorted_nodes:
                # STRICT RULE: The next node MUST be physically lower in the file
                # than the one we just finished.
                if n.line > node.line:
                    # Skip synthetic resume/system points
                    if n.tag.startswith("__res_") or n.tag.startswith("__sys_"):
                        continue
                    return self.current_scene, n.tag
        
        return None


    def _build_result_package(self):
        """
        Gathers all simulation metrics into a single dictionary.
        This is what tools like pickrandom and fuzzers consume.
        """
        return {
            "final_variables": copy.deepcopy(self.variables),
            "snapshots": copy.deepcopy(self.snapshots),
            "visited_nodes": dict(self.visited_nodes),
            "coverage_pick": dict(self.coverage_pick),
            "coverage_if": dict(self.coverage_if),
            "errors": list(self.errors.values()),
            "branch_coverage": self.branch_coverage,
        }

    def _cmd_reset_pick(self):
        if self.DEBUG:
            print("[SIM] Clearing used choices via -reset_pick")
        self.used_choices.clear()
        return self.FLOW_FALLTHROUGH


    # # ------------------------------------------------------------------
    # # IF / ELSEIF / ELSE
    # # ------------------------------------------------------------------

    def _execute_exclusive_if_chain(self, chain):
        for block in chain:
            cmd = block.get("cmd")
            # If it's an else, it must pass.
            if cmd == "-else":
                passed = True
                expr = "else"
            else:
                expr = block.get("cond") or block.get("args")
                # Force boolean check for the conditional logic
                passed = bool(self._eval(expr))

            if passed:
                if self.branch_coverage:
                    self.branch_coverage.record(
                        scene=self.current_scene, 
                        tag=self.current_tag,
                        kind="if" if cmd != "-else" else "else",
                        label=expr,
                        line=block["__line__"]
                    )
                
                res = self._execute_inline_node(block["node"])
                
                # Signal that we found a branch and processed it.
                return res if res != self.FLOW_FALLTHROUGH else self.FLOW_RESOLVED
                
        return self.FLOW_FALLTHROUGH


    def _execute_item_sequence(self, items):
        i = 0
        result = self.FLOW_FALLTHROUGH 

        while i < len(items):
            item = items[i]
            self.current_line = item.get("line")

            if self.DEBUG:
                print(f"[SEQ DEBUG] Executing {item['kind']} at line {item['line']} in {self.current_tag}")
            
            if item["kind"] == "text":
                # Just text, move to next item
                i += 1
                continue

            data = item["data"]
            cmd = data.get("cmd")

            # --- IF/ELSE CHAIN GROUPING ---
            if cmd == "-if":
                chain = [data]
                look_ahead = i + 1
                while look_ahead < len(items):
                    nxt = items[look_ahead]
                    if nxt["kind"] == "block" and nxt["data"].get("cmd") in ("-elseif", "-else"):
                        chain.append(nxt["data"])
                        look_ahead += 1
                    else:
                        break
                
                result = self._execute_exclusive_if_chain(chain)
                i = look_ahead # Skip the elseif/else blocks we just processed
            
            elif cmd in ("-elseif", "-else"):
                # These are handled by the -if look-ahead, so skip if hit directly
                i += 1
                continue
            
            # --- STANDARD COMMANDS ---
            else:
                if item["kind"] == "pick":
                    result = self._execute_pick(data)
                else:
                    result = self.execute_block(data)
                i += 1

            # --- BUBBLE UP SIGNALS ---
            # If any command results in a Jump, Pick, Pause, or End, stop processing this node
            if result in (self.FLOW_JUMP, self.FLOW_END, self.FLOW_PICK, self.FLOW_PAUSE):
                return result

        # return result
        return self.FLOW_FALLTHROUGH


    # ------------------------------------------------------------------
    # Choices (Unified Pick)
    # ------------------------------------------------------------------

    def _execute_pick(self, block):
        cmd = block["cmd"]
        pick_node = block["node"]
        choices = pick_node.get("choices", [])
        if self.DEBUG:
            print(f"[PICK DEBUG] cmd={cmd} tag={self.current_tag} num_choices={len(choices)} used={self.used_choices}")
            for i, c in enumerate(choices):
                print(f"  choice[{i}] id={c.get('choice_id')} subtype={c.get('choice_subtype')}")

            print(f"[PICK DEBUG] raw choices in block: {len(choices)}")
        
        # 1. VISIBILITY & SELECTABILITY FILTERING
        visible_options = []
        for opt in choices:
            opt_id = opt.get("choice_id")
            subtype = opt.get("choice_subtype", "standard")

            # Visibility Check
            cond = opt.get("cond")
            if cond and not self._eval(cond):
                continue

            # 'ONCE' Logic Check (Is it already in the set?)
            # if (subtype == "pick_once" or cmd == "-pick_once") and opt_id in self.used_choices:
            if (subtype == "pick_once" or cmd == "-pick_once") and opt_id and opt_id in self.used_choices:
                continue

            # Selectability Check (-pick_if)
            is_selectable = True
            merged = opt.get("merged_logic") 
            if merged:
                clean_merged = merged.replace("-pick_if", "").replace("-pick_once", "").replace("-if", "").strip()
                is_selectable = self._eval(clean_merged)

            opt["is_selectable"] = is_selectable
            visible_options.append(opt)

        if not visible_options:
            self._error_at("Pick has no visible options", kind="FLOW")
            return self.FLOW_END
        
        clickable_options = [o for o in visible_options if o.get("is_selectable", True)]
        
        if not clickable_options:
            self._error_at("All choices are unselectable (grayed out)", kind="FLOW")
            return self.FLOW_END

        # 2. SELECTION LOGIC
        if not hasattr(self, "_interactive_choice"):
            if not self.interactive:
                selected_opt = self.random.choice(clickable_options)
                self._interactive_choice = visible_options.index(selected_opt)
            else:
                self.waiting_pick = {
                    "type": "pick",
                    "pick_id": pick_node.get("pick_id"),
                    "choices": [
                        {
                            "id": i,
                            "label": self._extract_choice_label(opt),
                            "choice_id": opt.get("choice_id"),
                            "is_selectable": opt.get("is_selectable")
                        } for i, opt in enumerate(visible_options)
                    ]
                }
                return self.FLOW_PICK

        # 3. RESOLUTION
        idx = self._interactive_choice
        del self._interactive_choice
        selected = visible_options[idx]

        # # --- Persist 'Once' State ---
        sid = selected.get("choice_id")
        
        # also mark if parent block cmd is pick_once
        is_once = (
            selected.get("choice_subtype") == "pick_once"
            or cmd == "-pick_once"
        )
        if is_once:
            opt_id = selected.get("choice_id")
            if opt_id is not None:
                self.used_choices.add(opt_id)

        # --- Debugging ---
        if self.DEBUG:
            print(f"[DEBUG] Node: {self.current_tag} | Choice: {sid} | Used: {self.used_choices}")

        # --- Execute and handle fallthrough ---
        result = self._execute_inline_node(selected)

        if result == self.FLOW_FALLTHROUGH and cmd == "-single_pick":
            return self.FLOW_FALLTHROUGH
            
        return result


    def resolve_interactive_pick(self, opt_index):
        self._interactive_choice = opt_index

    def execute_one(self):
        node = self._get_node(self.current_scene, self.current_tag, self.node_ip)
        if not node:
            return self.FLOW_END

        return self._execute_node(node)

    def _merge_items_for_dict(self, node_dict):
        """Helper to sort internal blocks and text by line."""
        merged = []
        for t in node_dict.get("text", []): # Parser uses 'text' for inline
            merged.append({"kind": "text", "line": t["__line__"], "data": t})
        for b in node_dict.get("blocks", []):
            merged.append({"kind": "block", "line": b["__line__"], "data": b})
        merged.sort(key=lambda x: x["line"])
        return merged


    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _eval_vars(self):
        """Returns variables with tvar aliases injected (mirrors engine._eval_vars)."""
        v = dict(self.variables)
        for k in list(v.keys()):
            if k.startswith("_"):
                alias = k[1:]
                if alias not in v:
                    v[alias] = v[k]
        return v

    def _eval(self, expr):
        if not expr or expr.strip() == "": 
            return True
        
        # Standard Replacements
        clean_expr = expr.replace("&&", " and ").replace("||", " or ")

        # Fix Parentheses Balance
        l_count = clean_expr.count('(')
        r_count = clean_expr.count(')')
        if r_count > l_count:
            clean_expr = ("(" * (r_count - l_count)) + clean_expr
        elif l_count > r_count:
            clean_expr = clean_expr + (")" * (l_count - r_count))

        try:
            achieved_goals = self.variables.get("goals", [])
            ev = self._eval_vars()
            
            # Subclass dict to bypass AST strict variable checks
            class GoalResolver(dict):
                def __contains__(self, key):
                    # AST _eval_node checks existence before retrieval. Force true.
                    return True

                def __getitem__(self, key):
                    # Return actual variable or fallback to goal check
                    if super().__contains__(key):
                        return super().__getitem__(key)
                    return key in achieved_goals

            # Execute pure AST evaluation
            val = safe_eval_expr(clean_expr, GoalResolver(ev))
            
            return val
            
        except Exception as e:
            loc = f"{getattr(self, 'current_scene', 'unknown')}:{getattr(self, 'current_tag', 'unknown')}:Line {getattr(self, 'current_line', 'unknown')}"
            self._error(f"Eval error: {loc} || Executed: {clean_expr} || Error: ({e})")
            return False

    def _check_syntax(self, code):
        try:
            compile(code, '<string>', 'eval')
            return True
        except SyntaxError:
            return False


    def _jump(self, scene, tag):
        self.current_scene = scene
        self.current_tag = tag
        self.node_ip = 0


    def _get_node(self, scene, tag):
        sc = self.scenes.get(scene)
        if not sc:
            return None

        node = sc.nodes.get(tag)

        if isinstance(node, dict):
            self._error(
                f"Internal error: node {scene}:{tag} is dict, not Node"
            )
            return None

        return node


    def _error(self, msg):
        key = ("ERROR", msg, self.current_scene, self.current_tag, None)
        if key in self._error_keys:
            return
        self._error_keys.add(key)

        full = f"[ERROR] {msg}"
        self.errors[key] = full


    def _error_at(self, msg, *, scene=None, tag=None, line=None, kind=None):
        scene = scene or self.current_scene
        tag = tag or self.current_tag

        key = (kind, scene, tag, line, msg)
        if key in self._error_keys:
            return

        self._error_keys.add(key)

        prefix = f"[{kind}]" if kind else "[ERROR]"
        where = f"{scene}:{tag}" + (f":{line}" if line else "")
        full = f"{prefix} at {where} | {msg}"

        self.errors[key] = full

    def get_diagnostics(self):
        """Returns a list of unique formatted error strings collected during the run."""
        return list(self.errors.values())


    def _get_next_scene(self):
        story_order = self.config.get("story_order") or self.config.get("scene_order", [])
        try:
            idx = story_order.index(self.current_scene)
            if idx + 1 < len(story_order):
                return story_order[idx + 1]
        except ValueError:
            pass
        return None


    def _get_first_tag(self, scene):
        sc = self.scenes.get(scene)
        if not sc or not sc.nodes:
            return None
        # first tag by line number
        return min(sc.nodes.values(), key=lambda n: n.line).tag


    def _extract_choice_label(self, option):
        # Look for the text object marked as 'choice_text' by the parser
        for item in option.get("text", []):
            if item.get("type") == "choice_text":
                return item.get("text", "")
        return "[Empty Choice]"


    # ------------------------------------------------------------------
    # Return stack helpers
    # ------------------------------------------------------------------

    def _pop_return(self):
        """
        Pop a return address from the return stack.


        Returns:
            (scene, tag) tuple or None if stack is empty
        """
        if not self.return_stack:
            return None
        return self.return_stack.pop()


    # ------------------------------------------------------------------
    # Logic Core
    # ------------------------------------------------------------------

    def process_logic_block(self, block, current_vars):
        working_vars = copy.deepcopy(current_vars)
        cmd = block["cmd"]
        args = block.get("args", "")
        signal = "fallthrough"

        if cmd == "-mvar":
            self._logic_mvar(args, working_vars)
        elif cmd == "-tvar":
            self._logic_tvar(args, working_vars) 
        elif cmd == "-entropy":
            self._logic_entropy(args, working_vars)
        elif cmd in ("-if", "-elseif", "-else"):
            passed = self._eval_in_context(block.get("cond"), working_vars)
            # THIS is the signal the runtime uses to push to block_stack
            signal = "run_body" if passed else "skip" 
        elif cmd == "-pause":
            signal = "pause"
        elif cmd == "-end":
            signal = "end"
        elif cmd in ("-go", "-go_file", "-go_and_back", "-go_back", "-next"):
            signal = "runtime_flow"

        return {
            "updated_vars": working_vars,
            "signal": signal
        }

    def _eval_in_context(self, expr, vars_dict):
        """Stateless eval helper."""
        if not expr: return True
        try:
            return bool(eval(expr, {}, vars_dict))
        except:
            return False


    def _logic_mvar(self, args, vars_dict):
        from validators.expr import parse_mvar_args
        name, op, rhs = parse_mvar_args(args)
        
        cur = vars_dict.get(name, 0)
        # Evaluate RHS using the passed-in vars_dict, not self.variables
        rhs_val = eval(rhs, {}, vars_dict)
        
        try:
            if op == "=":
                new_val = rhs_val

            elif op == "+=":
                new_val = cur + rhs_val

            elif op == "-=":
                new_val = cur - rhs_val

            elif op == "*=":
                new_val = cur * rhs_val

            elif op == "/=":
                new_val = cur / rhs_val

            elif op == "%+":
                new_val = cur + (100 - cur) * (rhs_val / 100)

            elif op == "%-":
                new_val = cur - cur * (rhs_val / 100)

            else:
                self._error(f"mvar unsupported operator '{op}'")
                return

        except Exception as e:
            self._error_at(
                f"mvar eval error: {args} ({e})",
                kind="MVAR",
            )
            return
        
        vars_dict[name] = new_val # Modify the dictionary passed in
        return vars_dict