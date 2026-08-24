"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/runtime/session.py
import copy
import hashlib
import json
import random
from datetime import datetime
from engine.runtime.interpolation import interpolate_and_format, tokens_to_html, interpolate_text


import sys


class SessionManager:
    def __init__(self, engine_instance, initial_vars=None, seed=None, recorder=None, replay=None):
        self.recorder = recorder   # Instance of PickRecorder
        self.replay = replay       # Instance of PickReplay
        self.engine = engine_instance
        self.engine.session = self
        self.initial_vars = initial_vars or {}
        self.achieved_goals = [] 
        self.goals_defs = {}

        # Deterministic RNG
        self.seed = seed if seed is not None else random.randint(0, 1_000_000)
        self.rng = random.Random(self.seed)

        self.history_frames = []
        self.map_trace = []
        self.playhead = -1
        self.slots = {}
        self.timeline = {
            k: [("START", v, datetime.now().isoformat())]
            for k, v in self.initial_vars.items()
        }

        # Author's pick / read-only mode
        # When True, apply_intent only allows navigation (no branching)
        self.is_read_only = False

    # -------------------------------------------------------------------------
    # Game start
    # -------------------------------------------------------------------------

    def _seed_goals(self):
        """Internal helper to ensure goal variables exist in engine state."""
        # Use goals_defs (passed from loader) or check engine project
        defs = getattr(self, "goals_defs", {})
        if not defs and hasattr(self.engine, "project"):
            defs = getattr(self.engine.project, "goals", {})

        for goal_id in defs.keys():
            if goal_id not in self.engine.state["vars"]:
                self.engine.state["vars"][goal_id] = False
        
        if "goals_reached" not in self.engine.state["vars"]:
            self.engine.state["vars"]["goals_reached"] = []

    def start_game(self, scene, tag, online=False, is_replaying=False):
        self.history_frames = []
        self.playhead = -1

        if hasattr(self.engine, "project"):
            meta = self.engine.project.meta
            if "permanent_stats" in meta:
                from engine.commands.system import r_perm_stat
                for stat_line in meta["permanent_stats"]:
                    r_perm_stat(self.engine, stat_line, None)

        self.engine.start(scene, tag, variables=copy.deepcopy(self.initial_vars))

        self._seed_goals()

        # Seed all defined goals as False so conditionals can read them immediately
        if hasattr(self.engine, "project") and hasattr(self.engine.project, "goals"):
            for goal_id in self.engine.project.goals:
                if goal_id not in self.engine.state["vars"]:
                    self.engine.state["vars"][goal_id] = False
            self.engine.state["vars"]["goals_reached"] = []

        initial_frame = {
            "kind": "nav",
            "canonical_id": f"{scene}::{tag}",
            "text": ["Beginning Story..."],
            "scene": scene,
            "tag": tag,
        }

        # REPLAY or OFFLINE
        if online or is_replaying:
            return self._commit_frame(initial_frame)
        else:
            # OFFLINE START
            res_frame = self.engine.step()
            return self._commit_frame(res_frame)


    def apply_stats_intent(self, intent="open", tag=None, choice_id=None):
        """Navigate stats via isolated engine instance. Does not touch story history."""
        # Lazily initialize isolated stats engine
        if not hasattr(self, "_stats_engine") or self._stats_engine is None:
            from engine.runtime.engine import PickEngine
            self._stats_engine = PickEngine(self.engine.scenes, self.engine.story_order)
            self._stats_engine.session = None  # Null stub prevents history tracking

        engine = self._stats_engine
        stats_scene = engine.scenes.get("__stats__")
        if not stats_scene:
            return {"display": [], "choices": [], "kind": "error"}

        # Sync live state variables for accurate conditional evaluation
        engine.state["vars"] = copy.deepcopy(self.engine.state["vars"])

        # Reset engine pointer if opening menu or jumping to explicit tag
        if intent == "open" or tag:
            first_tag = list(stats_scene.nodes.keys())[0]
            target_tag = tag or first_tag
            engine.state["scene"] = "__stats__"
            engine.state["tag"] = target_tag
            engine.state["ip"] = 0
            engine.state["block_stack"] = []
            engine.state["return_stack"] = []
            engine.state["active_pick"] = None
            engine.state["finished"] = False

        accumulated_display = []

        # Execute target frame step
        if intent == "choose" and choice_id is not None:
            raw_frame = engine.step(choice_id=choice_id)
        else:
            raw_frame = engine.step()

        if raw_frame and "display" in raw_frame:
            accumulated_display.extend(raw_frame["display"])

        # Auto-advance through pure-logic or consecutive text segments
        for _ in range(200):
            if not raw_frame or raw_frame.get("kind") in ("pick", "end", "pause", "user_input"):
                break
            raw_frame = engine.step()
            if raw_frame and "display" in raw_frame:
                accumulated_display.extend(raw_frame["display"])

        # Write mutated variables back to main engine (handles settings changes)
        self.engine.state["vars"] = copy.deepcopy(engine.state["vars"])

        # Pack combined arrays into payload dictionary
        final_frame = raw_frame or {}
        final_frame["display"] = accumulated_display
        return self._process_stats_frame(final_frame)

    def _process_stats_frame(self, raw_frame):
        """Build stats display payload from raw engine frame, no history dependency."""
        engine = getattr(self, "_stats_engine", self.engine)
        vars_now = engine._eval_vars()
        display_items = []

        for item in raw_frame.get("display", []):
            if item.get("type") == "text":
                raw_val = item.get("content") or item.get("text", "")
                if raw_val and raw_val.strip():
                    tokens = interpolate_and_format(raw_val, vars_now)
                    html = tokens_to_html(tokens)
                    display_items.append({"type": "text", "html": html, "content": html})
            else:
                display_items.append(item)

        choices = []
        for c in raw_frame.get("choices", []):
            c2 = dict(c)
            if "label" in c2:
                # FIX: Run full interpolation + formatting pipeline
                tokens = interpolate_and_format(c2["label"], vars_now)
                c2["label"] = tokens_to_html(tokens)
            if "target_tag" not in c2:
                c2["target_tag"] = c2.get("tag") or c2.get("target")
            choices.append(c2)

        return {
            "kind": raw_frame.get("kind", "pause"),
            "display": display_items,
            "choices": choices,
            "scene": raw_frame.get("scene"),
            "tag": raw_frame.get("tag"),
        }


    def close_stats(self):
        """Exit stats modal, restore story state."""
        self.engine.exit_modal_view()

    # -------------------------------------------------------------------------
    # Intent handling
    # -------------------------------------------------------------------------

    def apply_intent(self, intent, value=None):
        if self.is_read_only and intent in ("choose", "continue"):
            current_frame = self.history_frames[self.playhead]["raw"]
            
            # If we are at a pick, we MUST have a tape entry
            if current_frame["kind"] == "pick":
                if self.replay and self.replay.choice_index < len(self.replay.choices):
                    recorded_choice = self.replay.next_choice(current_frame.get("canonical_id"))
                    return self._execute_and_commit("choose", recorded_choice)
                else:
                    # We hit a pick but the tape is empty. 
                    # In Author mode, this is the end of the rail.
                    return {"status": "error", "message": "End of recorded path."}
            
            # If it's a 'pause' or 'nav', and are in Read-Only,
            # just let it step forward even if the tape is empty.
            # This allows the "Coast to End" behavior.
            return self.apply_intent("step_forward")

        at_live_edge = (self.playhead == len(self.history_frames) - 1)

        # --- Navigation intents ---
        if intent == "step_back":
            idx = self.playhead - 1
            while idx >= 0 and not self.history_frames[idx].get("is_meaningful"):
                idx -= 1
            if idx >= 0:
                self.playhead = idx
                self._jump_engine_to_history(self.playhead)
                return self.history_frames[self.playhead]["processed"]

        if intent == "step_forward":
            # 1. Try to find the next meaningful frame in already-existing history
            idx = self.playhead + 1
            while idx < len(self.history_frames) and not self.history_frames[idx].get("is_meaningful"):
                idx += 1
                
            if idx < len(self.history_frames):
                # If found a frame in history, just move there
                self.playhead = idx
                self._jump_engine_to_history(self.playhead)
                return self.history_frames[self.playhead]["processed"]
            else:
                # live edge. 
                # If the current frame isn't a 'pick' or 'end', 
                # can ask the engine to step forward to find the actual end.
                current_kind = self.history_frames[self.playhead]["raw"].get("kind")
                if current_kind not in (
                    "pick",
                    "end",
                    ):
                    return self._execute_and_commit("continue", None)
                
                # If it's a pick, we can't move forward without a choice (end of tape)
                return self.history_frames[self.playhead]["processed"]

        if intent == "step_back_10":
            count, idx = 0, self.playhead
            
            # 1. Count back 10 meaningful frames
            while idx > 0 and count < 10:
                idx -= 1
                if self.history_frames[idx].get("is_meaningful"):
                    count += 1
            
            # 2. THE CRITICAL GUARD: 
            # If NOT at a meaningful frame, move FORWARD until hit one.
            # This prevents us from landing on 'Frame 0' if 'Frame 0' is a logic node.
            while idx < len(self.history_frames) - 1 and not self.history_frames[idx].get("is_meaningful"):
                idx += 1
                
            # 3. if overshot the current playhead, 
            # stay where we are or go to the nearest previous meaningful.
            self.playhead = min(idx, self.playhead)
            
            self._jump_engine_to_history(self.playhead)
            return self.history_frames[self.playhead]["processed"]

        if intent == "step_forward_10":
            count, idx = 0, self.playhead
            limit = len(self.history_frames) - 1
            while idx < limit and count < 10:
                idx += 1
                if self.history_frames[idx].get("is_meaningful"):
                    count += 1
            self.playhead = min(limit, idx)
            self._jump_engine_to_history(self.playhead)
            return self.history_frames[self.playhead]["processed"]

        if intent == "step_start":
            idx = 0
            while idx < len(self.history_frames) - 1 and not self.history_frames[idx].get("is_meaningful"):
                idx += 1
            self.playhead = idx
            self._jump_engine_to_history(self.playhead)
            return self.history_frames[self.playhead]["processed"]

        if intent == "step_end":
            self.playhead = len(self.history_frames) - 1
            self._jump_engine_to_history(self.playhead)
            return self.history_frames[self.playhead]["processed"]

        # --- Random advance ---
        elif intent == "pick_random":
            frame = self.history_frames[self.playhead]["raw"]
            if frame["kind"] == "pick" and "choices" in frame:
                available = [
                    c for c in frame["choices"]
                    if c.get("status", "available") == "available"
                ]
                if not available:
                    return self.history_frames[self.playhead]["processed"]
                selection = self.rng.choice(available)

                return self.apply_intent("choose", selection["id"])
                
            elif frame["kind"] == "pause":
                return self.apply_intent("continue")
                
            return self.history_frames[self.playhead]["processed"]

        # --- User input submission ---
        if intent == "continue" and self.history_frames:
            current_raw = self.history_frames[self.playhead]["raw"]
            
            # If at the input box:
            if current_raw.get("kind") == "user_input":
                # 1. Block empty submissions (The Guard)
                if value is None or value == "":
                    return self.get_current_frame()
                    
                # 2. Process the submission
                var_name = current_raw.get("user_input_var") or self.engine.state.get("pending_user_input_var", "user_input")
                text_val = str(value) if value is not None else ""
                self.engine.state["vars"][var_name] = text_val
                
                # Cleanup engine state
                self.engine.state.pop("pending_user_input_var", None)
                self.engine.state.pop("pending_user_input_prompt", None)
                
                if hasattr(self.engine, "session"):
                    loc = f"{self.engine.state['scene']}:{self.engine.state['tag']}"
                    self.record_var_change(var_name, text_val, loc)
                
                # 3. Step away from the input (This moves to the NEXT frame)
                return self._execute_and_commit("continue", None)

        # --- Execution intents (choose / continue) ---
        if intent in ("choose", "continue"):
            # 1. Look inside the current historical frame variables snapshot
            current_vars = self.history_frames[self.playhead].get("vars_snapshot", {})
            
            # 2. Extract BOTH settings straight from engine runtime memory state
            is_story_author_mode = current_vars.get("author_mode", False)
            is_cheat_on          = current_vars.get("cheat_mode", False)
            can_branch = False

            if is_story_author_mode:
                can_branch = True
            elif at_live_edge:
                can_branch = True
            elif is_cheat_on:
                if at_live_edge:
                    can_branch = True
                else:
                    all_picks = [i for i, f in enumerate(self.history_frames) if f["raw"]["kind"] == "pick"]
                    if all_picks:
                        last_pick_idx = all_picks[-1]
                        if self.playhead == last_pick_idx:
                            can_branch = True
                        elif len(all_picks) >= 2:
                            penultimate_pick_idx = all_picks[-2]
                            if self.playhead == penultimate_pick_idx:
                                if last_pick_idx == len(self.history_frames) - 1:
                                    can_branch = True

            if can_branch:
                if not at_live_edge:
                    # Prune history stacks
                    self.history_frames = self.history_frames[: self.playhead + 1]
                    self.map_trace = [(cid, idx) for cid, idx in self.map_trace if idx <= self.playhead]

                    if self.recorder:
                        p_count_before = sum(1 for f in self.history_frames[:self.playhead] 
                                           if f["raw"].get("kind") == "pick" and f.get("choice_id"))
                        self.recorder.choices = self.recorder.choices[:p_count_before]

                    self._jump_engine_to_history(self.playhead)

                return self._execute_and_commit(intent, value)

            else:
                if intent == "continue":
                    return self._execute_and_commit(intent, value)
                p_idx = sum(1 for f in self.history_frames[: self.playhead] if f["raw"]["kind"] == "pick")
                original = self.recorder.choices[p_idx] if self.recorder and p_idx < len(self.recorder.choices) else None
                if value == original:
                    return self.apply_intent("step_forward")
                else:
                    # REMOVE-START
                    msg = "Cannot change history here. (Author mode required)"
                    if is_cheat_on:
                        msg = "Cheat mode only allows undoing the VERY LAST choice."
                    print(f"\n[ SYSTEM ] {msg}")
                    # REMOVE-END
                    return self.history_frames[self.playhead]["processed"]

    # -------------------------------------------------------------------------
    # Telemetry
    # -------------------------------------------------------------------------

    def _record_telemetry(self, kind, data):
        event = {
            "kind": kind,
            "data": data,
            "playhead": self.playhead,
            "loc": f"{self.engine.state['scene']}:{self.engine.state['tag']}",
            "ts": datetime.now().isoformat(),
        }
        if not hasattr(self, "telemetry"):
            self.telemetry = []
        self.telemetry.append(event)

    # -------------------------------------------------------------------------
    # Execution + commit
    # -------------------------------------------------------------------------

    def _execute_and_commit(self, intent, value):
        self._record_telemetry(intent, {"value": value})

        if intent == "choose":
            if self.recorder:
                active_pick = self.engine.state.get("active_pick")
                if active_pick:
                    pid = self._resolve_pid(active_pick)
                    if self.recorder._current_pick == pid:
                        self.recorder.on_choose(pid, value)
                    elif self.recorder._current_pick is not None:
                        self.recorder._current_pick = None

            res_frame = self.engine.step(choice_id=value)
        else:
            res_frame = self.engine.step()

        # ONE commit only
        processed = self._commit_frame(res_frame)
        if intent == "choose":
            self.history_frames[-1]["choice_id"] = value

        # Explicitly block auto-advancing if we are recovering data or executing a read-only standby sweep
        if self.replay or self.is_read_only:
            return processed

        # Auto-advance past non-meaningful, non-pick, non-end frames
        while (not self.history_frames[-1].get("is_meaningful")
            and res_frame.get("kind") not in ("pick", "end", "user_input")):
            res_frame = self.engine.step()
            processed = self._commit_frame(res_frame)

        return processed

    def commit_map_node(self, node_id):
        if self.history_frames and self.history_frames[-1].get("canonical_id") == node_id:
            return
        self._commit_nav(node_id)

    def record_var_change(self, var_name, value, location):
        """
        Unified handler, does both jobs:
        1. Appends to the timeline (for _get_vars_at_time reconstruction).
        2. Attaches the change to the current tape frame (for per-frame diffs).
        """
        # --- Timeline (for time-travel reconstruction) ---
        if var_name not in self.timeline:
            self.timeline[var_name] = []
        last = self.timeline[var_name]
        if not last or last[-1][1] != value:
            self.timeline[var_name].append((location, value, datetime.now().isoformat()))

        # --- Per-frame diff (for save diffs and heatmap payloads) ---
        if not self.history_frames:
            return
        current_frame = self.history_frames[-1]
        if "modifications" not in current_frame:
            current_frame["modifications"] = {}
        current_frame["modifications"][var_name] = {"val": value, "loc": location}

    def _commit_nav(self, node_id):
        nav_frame = {
            "kind": "nav",
            "canonical_id": node_id,
            "text": [],
            "scene": self.engine.state["scene"],
            "tag": self.engine.state["tag"],
        }
        self._commit_frame(nav_frame)

    def _jump_engine_to_history(self, index):
        hist = self.history_frames[index]
        self.engine.load_state(hist["engine_snapshot"])
        self.rng.setstate(hist["rng_state"])

        # Reinject persistent goal booleans (survive load_state overwrite)
        for goal_id in getattr(self, "achieved_goals", []):
            self.engine.state["vars"][goal_id] = True
            if "goals_reached" not in self.engine.state["vars"]:
                self.engine.state["vars"]["goals_reached"] = []
            if goal_id not in self.engine.state["vars"]["goals_reached"]:
                self.engine.state["vars"]["goals_reached"].append(goal_id)

        if hist["raw"]["kind"] == "pick":
            self.engine.state["active_pick"] = {
                "data": hist["raw"]["data"],
                "u_id": hist["canonical_id"],
            }
        else:
            self.engine.state["active_pick"] = None

        if self.recorder:
            self.recorder._current_pick = hist["canonical_id"] if hist["raw"]["kind"] == "pick" else None


    def _commit_frame(self, raw_frame):
        if not raw_frame:
            return None

        vars_snapshot = copy.deepcopy(self.engine.state["vars"])
        processed = self._process_frame(raw_frame, vars_snapshot)

        is_meaningful = raw_frame.get("kind") in (
            "pick",
            "pause",
            "end",
            "user_input",
            ) or (
            raw_frame.get("kind") == "text"
            and any(t.strip() for t in raw_frame.get("text", []))
        )

        cid = self._get_canonical_id({"raw": raw_frame})

        # Notify recorder that a new pick is being presented
        # Must happen BEFORE the snapshot is stored so the recorder's
        # rewind_points align with history_frames indices.
        if raw_frame.get("kind") == "pick" and self.recorder:
            pick_id = cid
            # Only call on_pick if the recorder isn't already tracking this pick
            # (guards against double-commit on step_back/step_forward replays)
            if self.recorder._current_pick != pick_id:
                # If recorder thinks another pick is open, close it cleanly
                if self.recorder._current_pick is not None:
                    self.recorder._current_pick = None
                self.recorder.on_pick(pick_id)

        snapshot = {
            "raw": copy.deepcopy(raw_frame),
            "processed": processed,
            "canonical_id": cid,
            "engine_snapshot": self.engine.export_state(),
            "vars_snapshot": vars_snapshot,
            "rng_state": self.rng.getstate(),
            "is_meaningful": is_meaningful,
            "is_nav": raw_frame.get("kind") == "nav",
        }

        self.history_frames.append(snapshot)

        new_playhead = len(self.history_frames) - 1

        # Store a tuple of (ID, actual_history_index)
        # This creates a "bridge" between the map and the engine playhead
        if not self.map_trace or self.map_trace[-1][0] != cid:
            self.map_trace.append((cid, new_playhead))

        # Only forcefully update playhead pointer indices if the user is interacting at the live edge of the tracking timeline structure.
        # This keeps tape processing steps from hijacking the location pointer.
        if self.playhead == -1:
            self.playhead = new_playhead
        elif not self.replay and (self.playhead == new_playhead - 1 or is_meaningful):
            # Only track forward linearly if we aren't mid-replay
            self.playhead = new_playhead

        return processed


    # -------------------------------------------------------------------------
    # Jump-to (map navigation)
    # -------------------------------------------------------------------------

    def jump_to(self, target_nid, manifest):
        engine_id = manifest.get(target_nid, target_nid)
        search_id = engine_id.split("::opt_")[0]

        match_idx = None
        for i in range(len(self.history_frames) - 1, -1, -1):
            frame = self.history_frames[i]
            if frame.get("tag_context") == search_id:
                match_idx = i
                break
            if frame.get("canonical_id") == search_id:
                match_idx = i
                break
            if search_id in frame.get("id", ""):
                match_idx = i
                break

        if match_idx is not None:
            if "::p_" not in search_id:
                while match_idx > 0 and (
                    self.history_frames[match_idx - 1].get("tag_context") == search_id
                    or search_id in self.history_frames[match_idx - 1].get("id", "")
                ):
                    match_idx -= 1
            while match_idx < len(self.history_frames) - 1 and not self.history_frames[match_idx].get("is_meaningful"):
                match_idx += 1
            return self._perform_jump(match_idx)

        # print(f"[JUMP ERROR] Target {search_id} not found in history frames.")
        return None

    def _perform_jump(self, index):
        # print(f"[DEBUG] Match found at frame {index}!")
        self.playhead = index
        self._jump_engine_to_history(index)
        return self.history_frames[index]["processed"]

    # -------------------------------------------------------------------------
    # Frame processing
    # -------------------------------------------------------------------------

    def _process_frame(self, frame, variables):
        f = copy.deepcopy(frame)
        if "text" in f:
            f["text"] = [interpolate_text(line, variables) for line in f["text"]]
        if "choices" in f:
            for c in f["choices"]:
                if "label" in c:
                    # FIX: Run full interpolation + formatting pipeline
                    tokens = interpolate_and_format(c["label"], variables)
                    c["label"] = tokens_to_html(tokens)
        
        if hasattr(self, "pending_notifications") and self.pending_notifications:
            if "notifications" not in f:
                f["notifications"] = []
            f["notifications"] = list(self.pending_notifications)
            
        return f

    def _get_vars_at_time(self, timestamp_iso):
        past_vars = {}
        for var_name, changes in self.timeline.items():
            current_value = None
            for loc, val, ts in changes:
                if ts <= timestamp_iso:
                    current_value = val
                else:
                    break
            if current_value is not None:
                past_vars[var_name] = current_value
        return past_vars

    # -------------------------------------------------------------------------
    # In-memory slots (fast, no serialization)
    # -------------------------------------------------------------------------

    def save_slot(self, slot_id):
        if session.is_read_only: return
        if 1 <= slot_id <= 10:
            self.slots[slot_id] = {
                "history_frames": copy.deepcopy(self.history_frames),
                "playhead": self.playhead,
                "timeline": copy.deepcopy(self.timeline),
            }
            return True
        return False

    def load_slot(self, slot_id):
        data = self.slots.get(slot_id)
        if data:
            self.history_frames = copy.deepcopy(data["history_frames"])
            self.playhead = data["playhead"]
            self.timeline = copy.deepcopy(data["timeline"])
            self._jump_engine_to_history(self.playhead)
            return self.history_frames[self.playhead]["processed"]
        return None

    # -------------------------------------------------------------------------
    # Entropy
    # -------------------------------------------------------------------------

    def generate_entropy(self, entropy_id, lo, hi, local_seed):
        if self.replay:
            return self.replay.next_entropy(entropy_id)

        combined_seed = f"{self.seed}:{entropy_id}:{local_seed}"
        val = random.Random(combined_seed).randint(lo, hi)

        if self.recorder:
            self.recorder.on_entropy(entropy_id, val)

        return val

    # -------------------------------------------------------------------------
    # ID helpers
    # -------------------------------------------------------------------------

    def _get_canonical_id(self, frame):
        raw = frame.get("raw", {})
        
        # 1. If the frame itself has a fixed ID (like a Nav frame), use it
        if "canonical_id" in raw:
            return raw["canonical_id"]
        
        # 2. Extract from engine state if we are currently at a Pick
        # This ensures the ID includes the p_hash, which we then normalize
        active_pick = self.engine.state.get("active_pick")
        if active_pick and isinstance(active_pick, dict):
            # Try to get the unique ID assigned to this pick block
            uid = active_pick.get("u_id") or active_pick.get("pick_id")
            if uid:
                return uid

        # 3. Check raw data payload
        if "data" in raw and isinstance(raw["data"], dict):
            uid = raw["data"].get("u_id") or raw["data"].get("pick_id")
            if uid:
                return uid

        # 4. Fallback to Scene::Tag
        node = self.engine._get_node(self.engine.state["scene"], self.engine.state["tag"])
        return getattr(node, "u_id", f"{self.engine.state['scene']}::{self.engine.state['tag']}")
    
    def get_canonical_id_at_playhead(self):
        """Returns the raw canonical_id string for save bundle storage."""
        if not self.history_frames or self.playhead < 0:
            return None
        return self.history_frames[self.playhead].get("canonical_id")

    def _resolve_pid(self, frame_or_item):
        return self._get_canonical_id({"raw": frame_or_item})

    # -------------------------------------------------------------------------
    # History trace (for map + telemetry)
    # -------------------------------------------------------------------------

    def get_history_trace(self):
        """
        Returns the pre-filtered map path.
        """
        return self.map_trace


    def get_edge_trace(self):
        """
        Returns (from_id, to_id) pairs for every consecutive frame transition.
        Used for heatmap aggregation, each pair is one traversal of that edge.
        """
        ids = self.get_history_trace()
        return list(zip(ids, ids[1:]))

    # -------------------------------------------------------------------------
    # Persistent save/load  (full serialization for localStorage / file / API)
    # -------------------------------------------------------------------------

    def export_save_data(self, save_type="player_slot", display_name=None, author_note=None):
        """
        Builds the full portable save bundle.

        save_type options:
          "player_slot"  - normal player progress (local or cloud)
          "author_pick"  - read-only linear playthrough shipped with the game
          "bug_report"   - exported trace for author review
          "checkpoint"   - mid-story checkpoint placed by -save_checkpoint command

        The bundle is self-contained: load_from_data() can reconstruct the
        exact session state on any compatible version of the game.
        """
        # --- Game identity (pulled from project meta if available) ---
        game_id = None
        game_version = None
        if hasattr(self.engine, "project"):
            meta = getattr(self.engine.project, "meta", {})
            game_id = meta.get("id") or meta.get("title")
            game_version = meta.get("version")

        # --- Start node ---
        start_node = None
        if self.history_frames:
            r = self.history_frames[0]["raw"]
            start_node = {"scene": r.get("scene"), "tag": r.get("tag")}

        # --- Tapes ---
        choice_tape = getattr(self.recorder, "choices", []) if self.recorder else []
        entropy_tape = getattr(self.recorder, "entropy", []) if self.recorder else []
        # telemetry_path = self.get_history_trace()

        # --- Vars snapshot at current playhead ---
        vars_snapshot = {}
        if 0 <= self.playhead < len(self.history_frames):
            vars_snapshot = copy.deepcopy(
                self.history_frames[self.playhead].get("vars_snapshot", {})
            )
        
        # --- Telemetry (for heatmaps, analytics) ---
        telemetry_path = self.get_history_trace()

        # Extract the ID from the (ID, Index) tuple
        def _get_cid_at_idx(path, idx):
            if 0 <= idx < len(path):
                entry = path[idx]
                return entry[0] if isinstance(entry, (list, tuple)) else entry
            return None

        playhead_canonical_id = self.get_canonical_id_at_playhead()

        if 0 <= self.playhead < len(telemetry_path):
            entry = telemetry_path[self.playhead]
            playhead_canonical_id = entry[0] if isinstance(entry, (list, tuple)) else entry
        else:
            playhead_canonical_id = None

        # --- Build bundle ---
        bundle = {
            "format_version": "1.1",
            "save_type": save_type,
            "game_id": game_id,
            "game_version": game_version,
            "created_at": datetime.now().isoformat(),
            "display_name": display_name or self._default_save_name(save_type),
            "author_note": author_note,
            "is_read_only": save_type == "author_pick",
            # --- Replay core ---
            "seed": self.seed,
            "start_node": start_node,
            "choice_tape": choice_tape,
            "entropy_tape": entropy_tape,
            "playhead_idx": self.playhead,
            "playhead_canonical_id": playhead_canonical_id,
            # --- Snapshot (for fast UI preview without re-running) ---
            "vars_snapshot": vars_snapshot,
            # --- Telemetry (for heatmaps, analytics) ---
            "telemetry_path": telemetry_path,
            # --- Integrity ---
            "checksum": self._tape_checksum(choice_tape, entropy_tape),
        }
        return bundle

    def _default_save_name(self, save_type):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        labels = {
            "player_slot": f"Save - {ts}",
            "author_pick": "Author's Pick",
            "bug_report": f"Bug Report - {ts}",
            "checkpoint": f"Checkpoint - {ts}",
        }
        return labels.get(save_type, f"Save - {ts}")

    @staticmethod
    def _tape_checksum(choice_tape, entropy_tape):
        """SHA-256 of the serialized tapes. Detects file corruption."""
        payload = json.dumps(
            {"c": choice_tape, "e": entropy_tape}, separators=(",", ":"), sort_keys=True
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    # -------------------------------------------------------------------------
    # load_from_data, reconstruct session from a save bundle
    # -------------------------------------------------------------------------

    def load_from_data(self, data):
        """
        Reconstruct the session from a save bundle.

        Strategy
        --------
        1. Replay the choice_tape - rebuilds history_frames with all frames.
        2. Restore playhead using playhead_canonical_id: find the last
           history frame whose canonical_id matches, searching from the end.
           This is robust to frame index shifts caused by game updates.
        3. Fall back to numeric playhead_idx if canonical ID not found.
        """
        if not data or "choice_tape" not in data:
            return {"error": "Invalid save data: missing choice_tape."}

        # --- Integrity ---
        expected = self._tape_checksum(data["choice_tape"], data.get("entropy_tape", []))
        if data.get("checksum") and data["checksum"] != expected:
            return {"error": "Save file is corrupted (checksum mismatch)."}

        # --- Game identity ---
        warnings = []
        if hasattr(self.engine, "project"):
            meta          = getattr(self.engine.project, "meta", {})
            saved_game_id = data.get("game_id")
            current_id    = meta.get("id") or meta.get("title")
            saved_version = data.get("game_version")
            current_ver   = meta.get("version")

            if saved_game_id and current_id and saved_game_id != current_id:
                return {"error": (
                    f"Save is for a different game: '{saved_game_id}' "
                    f"(current: '{current_id}')."
                )}
            if saved_version and current_ver and saved_version != current_ver:
                warnings.append(
                    f"Game updated ({saved_version} → {current_ver}). "
                    "Checking compatibility…"
                )

        # --- Reset ---
        self.seed           = data.get("seed", self.seed)
        self.rng            = random.Random(self.seed)
        self.history_frames = []
        self.playhead       = -1
        self.is_read_only   = data.get("is_read_only", False)

        self.seed = data.get("seed", self.seed)
        # Make sure your engine also gets this seed!
        self.engine.rng = random.Random(self.seed)

        entropy_tape = data.get("entropy_tape", [])
        if entropy_tape:
            from engine.runtime.recorder import PickReplay
            self.replay = PickReplay(entropy_tape)
        else:
            self.replay = None

        # --- Restart engine ---
        start = data.get("start_node", {})
        try:
            self.history_frames = []
            self.playhead = -1
            
            # Tell start_game we are replaying so it initializes on empty placeholder frame
            self.start_game(start.get("scene"), start.get("tag"), is_replaying=True)
            
            # Perform one step to reach the first actual content frame
            res = self.engine.step()
            if res:
                self._commit_frame(res)

            self.playhead = len(self.history_frames) - 1

        except Exception as e:
            return {"error": f"Starting node not found: {e}"}

        # --- Replay choice tape ---
        # Each step advance until hit a pick frame, then commit the choice.
        for i, choice_id in enumerate(data["choice_tape"]):

            # Run the engine forward until the current playhead frame is a pick.
            # This consumes text/pause/nav frames that sit between choices.
            ok, err = self._replay_advance_to_pick(i)#), len(data["choice_tape"]))
            if err:
                return err

            current_raw = self.history_frames[self.playhead]["raw"]
            if current_raw["kind"] != "pick":
                # Engine ended before consumed all choices
                return {
                    "error":      "Save corruption: ran out of pick frames.",
                    "at_step":    i,
                    "frame_kind": current_raw["kind"],
                }

            valid = [c["id"] for c in current_raw.get("choices", [])]

            if choice_id not in valid:
                return {
                    "error": (
                        f"Incompatible save: choice '{choice_id}' no longer exists "
                        f"(step {i + 1}/{len(data['choice_tape'])})."
                    ),
                    "at_node": self.history_frames[self.playhead]["canonical_id"],
                    "hint":    "The game was updated in a way that affects this save.",
                }
            
            self.history_frames[self.playhead]["choice_id"] = choice_id

            self._execute_and_commit("choose", choice_id)

        # --- Advance past final pauses to reach the saved position ---
        # After the last choice is committed the engine may be sitting on a
        # pause or nav frame. Keep stepping until find target_cid or reach
        # the next decision point (pick/end).
        target_cid = data.get("playhead_canonical_id")
        target_idx = data.get("playhead_idx") # Use the numeric backup

        if target_cid:
            # First check: is the target already in history_frames?
            # (handles saves made at a pick or nav frame)
            match_idx = self._find_canonical_in_history(target_cid)

            if match_idx is None:
                # Target not yet reached, advance the engine forward until
                # we find it or hit another decision point.
                MAX_STEPS = 2000
                steps = 0
                while steps < MAX_STEPS:
                    kind = self.history_frames[self.playhead]["raw"].get("kind")

                    # Stop if we've overshot into a new decision point
                    if kind == "pick":
                        break
                    if kind == "end":
                        break

                    # Check if the NEXT frame in history already has our target
                    # (may have it but haven't moved the cursor there)
                    next_idx = self.playhead + 1
                    if next_idx < len(self.history_frames):
                        self.playhead = next_idx
                        self._jump_engine_to_history(self.playhead)
                    else:
                        # Execute to produce the next frame
                        if kind == "pause":
                            self._execute_and_commit("continue", None)
                        else:
                            res = self.engine.step()
                            if res:
                                self._commit_frame(res)
                            else:
                                break

                    # Check after advancing
                    match_idx = self._find_canonical_in_history(target_cid)
                    if match_idx is not None:
                        break

                    steps += 1

            if match_idx is not None:
                self.playhead = match_idx
                self._jump_engine_to_history(self.playhead)
            elif target_idx is not None and target_idx < len(self.history_frames):
                # Fallback to numeric index if ID search failed 
                # (common in updated games where IDs changed)
                self.playhead = target_idx
            else:
                warnings.append(
                    f"Saved position '{target_cid}' not found after replay; "
                    "placed at the nearest available point."
                )
                # Stay wherever the engine landed (end of tape + advance)

        else:
            # Older bundle without playhead_canonical_id
            target_ph = min(
                data.get("playhead_idx", self.playhead),
                len(self.history_frames) - 1,
            )
            self.playhead = target_ph
            self._jump_engine_to_history(self.playhead)
        
        # --- Advance past final pauses to reach the saved position ---
        # Protect user-interaction nodes ("pause", "user_input") from being forcefully skipped over.
        # Only advance if the engine is sitting on an ephemeral infrastructure block.
        last_frame = self.history_frames[self.playhead]["raw"]
        if last_frame.get("kind") not in ("pick", "end", "pause", "user_input"):
            res = self.engine.step()
            if res:
                self._commit_frame(res)
                self.playhead = len(self.history_frames) - 1


        # --- FINAL SYNC ---
        # Before returning the frame to the UI, make sure the replay cursor 
        # matches where we landed.
        self.sync_replay_index()

        result_frame = self.history_frames[self.playhead]["processed"]

        # --- Synchronize Map Trace ---
        # Rebuild the map_trace from the history_frames we just replayed.
        # This ensures 'future' nodes from before the load are purged.
        
        self.map_trace = []
        for i, frame in enumerate(self.history_frames):
            cid = frame.get("canonical_id")
            if cid:
                # Store the ID and the index to maintain the (ID, Index) tuple format
                self.map_trace.append((cid, i))

        if warnings:
            return {"status": "warning", "message": " ".join(warnings), "frame": result_frame}
        return {"status": "success", "frame": result_frame}


    def _replay_advance_to_pick(self, choice_index):
        MAX_STEPS = 2000
        steps = 0

        while steps < MAX_STEPS:
            current_frame = self.history_frames[self.playhead]["raw"]
            kind = current_frame.get("kind")

            if kind == "pick":
                return True, None

            if kind == "end":
                return False, {"error": "Game ended early", "at_step": choice_index}

            # Unified advancement
            # If have future history, use it. 
            if self.playhead < len(self.history_frames) - 1:
                self.playhead += 1
                self._jump_engine_to_history(self.playhead)
            else:
                # If at the edge, execute based on kind
                if kind == "pause":
                    self._execute_and_commit("continue", None)
                else:
                    # This handles 'nav' or 'text' frames by stepping the engine
                    res = self.engine.step()
                    if res:
                        self._commit_frame(res)
                    else:
                        break
            steps += 1

        return False, {
            "error":   "Save corruption: could not reach next pick frame.",
            "at_step": choice_index,
        }


    def sync_replay_index(self):
        """Aligns the replay choice cursor with the current playhead."""
        if not self.replay:
            return
        
        # Count how many picks were actually made to get to this playhead
        picks_count = 0
        for i in range(self.playhead):
            frame = self.history_frames[i]
            # Only count if it was a pick AND it resulted in a choice being recorded
            if frame["raw"].get("kind") == "pick" and frame.get("choice_id"):
                picks_count += 1
                
        self.replay.choice_index = picks_count
        

    def _find_canonical_in_history(self, target_cid: str):
        """
        Search history_frames backwards for target_cid string.
        """
        for idx in range(len(self.history_frames) - 1, -1, -1):
            frame_cid = self.history_frames[idx].get("canonical_id")
            
            # Handle both formats just in case
            if isinstance(frame_cid, (list, tuple)):
                frame_cid = frame_cid[0]
                
            if frame_cid == target_cid:
                return idx
        return None


    def get_goals_manifest(self):
        all_goals_defs = getattr(self, "goals_defs", {})
        reached_ids = self.engine.state["vars"].get("goals_reached", [])
        
        # Point Tracking
        total_points = 0
        current_points = 0
        
        manifest = []
        for g_id, g_obj in all_goals_defs.items():
            attrs = getattr(g_obj, "attributes", {})
            is_reached = g_id in reached_ids
            
            # Parse points (default to 0 if missing)
            pts = int(attrs.get("points", 0))
            total_points += pts
            if is_reached:
                current_points += pts

            is_visible = str(attrs.get("visible", "true")).lower() == "true"
            
            # Logic: If it's a secret goal and not reached, hide everything.
            if not is_visible and not is_reached:
                manifest.append({
                    "id": g_id,
                    "title": "???",
                    "desc": "",
                    "reached": False,
                    "hidden": True
                })
            else:
                # Logic: Use 'prompt' as the title. 
                # Hide 'description' unless reached.
                manifest.append({
                    "id": g_id,
                    "title": attrs.get("prompt") or g_id.replace("_", " ").title(),
                    "desc": attrs.get("description") if is_reached else "",
                    "reached": is_reached,
                    "points": pts
                })

        return {
            "goals": manifest,
            "total_points": total_points,
            "current_points": current_points
        }

