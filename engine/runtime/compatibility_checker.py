# engine/runtime/compatibility_checker.py
"""
CompatibilityChecker
====================
Validates a save bundle against the current game graph before loading.

Three outcomes
--------------
"ok"      - tape walks cleanly. Load without hesitation.
"warn"    - structural changes exist, but only BEHIND the saved playhead.
            The player's forward path is intact. Offer to load with a notice.
"block"   - the saved position or its forward path is broken.
            Cannot load safely. Show the specific failure point.

Usage
-----
    from engine.runtime.compatibility_checker import CompatibilityChecker

    checker = CompatibilityChecker(scenes)      # scenes = game_data dict
    result = checker.check(bundle)
    # result = {
    #   "status":  "ok" | "warn" | "block",
    #   "message": str,
    #   "details": [ ... ],   # list of individual findings
    #   "fail_step": int | None,
    #   "fail_node": str | None,
    # }
"""

from __future__ import annotations
from typing import Optional


class CompatibilityChecker:

    def __init__(self, scenes: dict):
        """
        scenes : the game_data dict (same structure passed to PickEngine).
                 Keys are scene names, values are Scene objects with .nodes.
        """
        self.scenes = scenes

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def check(self, bundle: dict) -> dict:
        """
        Walk the choice_tape from the bundle against the current game graph.
        Returns a result dict (see module docstring).
        """
        details = []
        fail_step = None
        fail_node = None

        choice_tape  = bundle.get("choice_tape", [])
        start_node   = bundle.get("start_node", {})
        playhead_idx = bundle.get("playhead_idx", len(choice_tape))

        # 1. Verify the start node still exists
        start_scene = start_node.get("scene")
        start_tag   = start_node.get("tag")
        if not self._node_exists(start_scene, start_tag):
            return self._result(
                "block",
                f"Starting node '{start_scene}::{start_tag}' no longer exists.",
                details,
                fail_step=0,
                fail_node=f"{start_scene}::{start_tag}",
            )

        # 2. Walk the tape: simulate each pick using only graph structure
        #    (no engine execution, just structure checks)
        current_scene = start_scene
        current_tag   = start_tag
        choices_made  = 0

        for step_idx, choice_id in enumerate(choice_tape):
            # Find the pick block in the current node
            node = self._get_node(current_scene, current_tag)
            if node is None:
                details.append({
                    "step": step_idx,
                    "node": f"{current_scene}::{current_tag}",
                    "issue": "node no longer exists",
                    "severity": "block" if step_idx >= playhead_idx else "warn",
                })
                if step_idx >= playhead_idx:
                    fail_step = step_idx
                    fail_node = f"{current_scene}::{current_tag}"
                    break
                continue

            pick_block = self._find_pick_block(node)
            if pick_block is None:
                details.append({
                    "step": step_idx,
                    "node": f"{current_scene}::{current_tag}",
                    "issue": "expected a pick block, none found",
                    "severity": "block" if step_idx >= playhead_idx else "warn",
                })
                if step_idx >= playhead_idx:
                    fail_step = step_idx
                    fail_node = f"{current_scene}::{current_tag}"
                    break
                continue

            # Validate choice_id exists
            choice_idx = self._parse_choice_idx(choice_id)
            choices    = pick_block.get("node", {}).get("choices", [])

            if choice_idx is None or choice_idx >= len(choices):
                severity = "block" if step_idx >= playhead_idx else "warn"
                details.append({
                    "step": step_idx,
                    "node": f"{current_scene}::{current_tag}",
                    "issue": f"choice '{choice_id}' no longer exists (had {len(choices)} choices)",
                    "severity": severity,
                })
                if severity == "block":
                    fail_step = step_idx
                    fail_node = f"{current_scene}::{current_tag}"
                    break
                # Behind playhead: note warning, continue with best-effort
                continue

            # Advance to next node via choice continuation
            choice_data = choices[choice_idx]
            cont = choice_data.get("continuation")
            if cont:
                current_scene, current_tag = cont[0], cont[1]
            else:
                # No explicit continuation, stay in same node (single_pick or inline)
                pass

            choices_made += 1

        # 3. Determine overall status
        blocking = [d for d in details if d["severity"] == "block"]
        warnings = [d for d in details if d["severity"] == "warn"]

        if blocking or fail_step is not None:
            msg = self._block_message(blocking[0] if blocking else {"node": fail_node, "issue": "unknown"})
            return self._result("block", msg, details, fail_step, fail_node)

        if warnings:
            msg = (
                f"The game was updated since this save was created. "
                f"{len(warnings)} change(s) were detected in parts of the story "
                f"you've already passed. Your current position is intact."
            )
            return self._result("warn", msg, details)

        return self._result("ok", "Save is fully compatible.", details)

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------

    def _node_exists(self, scene: str, tag: str) -> bool:
        return self._get_node(scene, tag) is not None

    def _get_node(self, scene: str, tag: str):
        sc = self.scenes.get(scene)
        if sc is None:
            return None
        return getattr(sc, "nodes", getattr(sc, "tags", {})).get(tag)

    def _find_pick_block(self, node) -> Optional[dict]:
        """Return the first pick block in the node's blocks list, or None."""
        blocks = getattr(node, "blocks", [])
        for b in blocks:
            if b.get("cmd") in ("-pick", "-pick_once", "-single_pick"):
                return b
        return None

    @staticmethod
    def _parse_choice_idx(choice_id: str) -> Optional[int]:
        """'c0' → 0, 'c3' → 3, anything else → None."""
        if choice_id and choice_id.startswith("c") and choice_id[1:].isdigit():
            return int(choice_id[1:])
        return None

    # ------------------------------------------------------------------
    # Result builders
    # ------------------------------------------------------------------

    @staticmethod
    def _result(status, message, details, fail_step=None, fail_node=None) -> dict:
        return {
            "status":     status,
            "message":    message,
            "details":    details,
            "fail_step":  fail_step,
            "fail_node":  fail_node,
        }

    @staticmethod
    def _block_message(finding: dict) -> str:
        return (
            f"This save cannot be loaded: {finding.get('issue', 'incompatible change')} "
            f"at '{finding.get('node', 'unknown node')}'. "
            f"The game was updated in a way that affects your saved path."
        )