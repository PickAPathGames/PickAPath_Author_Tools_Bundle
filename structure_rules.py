# structure_rules.py
"""
Static structure validator for story files.
Checks indentation consistency, branching, and conditional pairing.
Does not perform runtime-level cross-scene validation.
"""
from parser.data_model import assert_block_shape
from engine.diagnostics import Diagnostic
from engine.command_kinds import TERMINAL_CMDS, BRANCHING_CMDS, STRUCTURAL_CMDS, NON_BLOCK_COMMANDS


def indent_level(node, indent_width):
    # The parser uses '__indent__' for the raw space count
    raw = node.get("__indent__")
    
    # If it's a root-level command inside a tag, indent is 0
    if raw == 0:
        return 0
    
    if raw is None or not isinstance(raw, int):
        return None

    if raw % indent_width != 0:
        return None

    return raw // indent_width

class StructureValidator:
    def __init__(self, runtime, diagnostics):
        self.runtime = runtime
        self.diagnostics = diagnostics
        self.context_stack = []
        self.DEBUG = False

        self.indent_width = getattr(runtime, "indent_width", 2)


    def check_scene(self, scene):
        for tag_name, tag_data in scene.nodes.items():
            if self.DEBUG:
                print(f"[DEBUG] Checking tag {tag_name}")
            
            blocks = getattr(tag_data, "blocks", []) or []
            
            # Check indentation consistency
            self._check_branch(blocks, scene.name, tag_name)
            
            # Check that every #choice leads to a terminal (-go, -end, -next)
            self._check_choices_have_targets(blocks, scene.name, tag_name)


    def _check_branch(self, blocks, scene_name, tag_name):
        stack = []

        for node in blocks:
            line = node.get("__line__", 0)
            cmd = node.get("cmd")
            
            # Use the fixed helper
            raw_indent = node.get("__indent__")
            indent = indent_level(node, self.indent_width)

            # If indent is None but raw_indent exists, it's a misalignment
            if indent is None and raw_indent is not None:
                self._error(
                    scene_name,
                    tag_name,
                    line,
                    f"indentation ({raw_indent}) not aligned to indent width ({self.indent_width})"
                )
                continue
            
            # If both are None, this is likely a synthetic block; skip indent check
            if indent is None:
                indent = stack[-1]["indent"] if stack else 0
                
            else:
                # non-structural content inherits current block indent
                # indent = stack[-1]["indent"] + 1 if stack else 0
                if indent is None:
                    self._error(
                        scene_name,
                        tag_name,
                        line,
                        f"indentation not aligned to indent width ({self.indent_width})"
                    )
                    continue

            # ---- Close blocks by indentation ----
            while stack and indent < stack[-1]["indent"]:
                stack.pop()

            # ---- TEXT ----
            if cmd is None:
                if stack and indent <= stack[-1]["indent"]:
                    self._error(
                        scene_name,
                        tag_name,
                        line,
                        f"content must be indented inside {stack[-1]['type']}"
                    )
                continue

            if cmd == "-if":
                stack.append({
                    "type": "if",
                    "indent": indent,
                    "line": line
                })
                continue

            if cmd == "-elseif":
                if not stack:
                    self._error(scene_name, tag_name, line, "elseif without preceding -if")
                    continue

                prev = stack[-1]
                if prev["type"] not in ("if", "elseif") or prev["indent"] != indent:
                    self._error(scene_name, tag_name, line, "elseif without matching -if")
                    continue

                stack.pop()
                stack.append({
                    "type": "elseif",
                    "indent": indent,
                    "line": line
                })
                continue

            if cmd == "-else":
                if not stack:
                    self._error(scene_name, tag_name, line, "else without preceding -if")
                    continue

                prev = stack[-1]
                if prev["type"] not in ("if", "elseif") or prev["indent"] != indent:
                    self._error(scene_name, tag_name, line, "else without matching -if")
                    continue

                stack.pop()
                stack.append({
                    "type": "else",
                    "indent": indent,
                    "line": line
                })
                continue

            if cmd == "-pick":
                stack.append({
                    "type": "pick",
                    "indent": indent,
                    "line": line
                })
                continue

            if cmd == "#choice":
                if not stack or stack[-1]["type"] != "pick":
                    self._error(scene_name, tag_name, line, "choice outside -pick")
                    continue
                if indent <= stack[-1]["indent"]:
                    self._error(scene_name, tag_name, line, "choice must be indented inside -pick")
                continue

            if cmd in NON_BLOCK_COMMANDS:
                if stack and indent <= stack[-1]["indent"]:
                    self._error(
                        scene_name,
                        tag_name,
                        line,
                        f"command must be indented inside {stack[-1]['type']}"
                    )
                continue


    def _check_choices_have_targets(self, blocks, scene_name, tag_name): # Added scene_name
        for node in blocks:
            cmd = node.get("cmd") # node is a dict, use .get()
            if cmd in ("-pick", "-pick_once") and "node" in node:
                choices = node["node"].get("choices", [])
                for choice in choices:
                    sub_blocks = choice.get("blocks", [])
                    if not self._choice_has_terminal(sub_blocks):
                        line = choice.get("__line__", node.get("__line__", "?"))
                        self._error(scene_name, tag_name, line, "choice must end with a command like -go or -end")


    def _choice_has_terminal(self, blocks):
        """Recursively detect if a path leads to a terminal or a nested valid structure."""
        if not blocks:
            return False

        for b in blocks:
            cmd = b.get("cmd")
            
            # Direct terminals
            if cmd in {"-go", "-end", "-next", "-go_back", "-go_and_back", "-go_file", "-finish"}:
                return True

            # Nested picks: If it's a -single_pick, it's safe. 
            # If it's a normal -pick, all its internal choices must be safe.
            if cmd == "-single_pick":
                return True
                
            if cmd in ("-pick", "-pick_once"):
                choices = b.get("node", {}).get("choices", [])
                # If all sub-choices have terminals, this branch is technically terminal
                if choices and all(self._choice_has_terminal(c.get("blocks", [])) for c in choices):
                    return True

            # Conditionals: This is tricky. For now, we assume if the 
            # internal block has a terminal, that branch is covered.
            inner_node = b.get("node")
            if inner_node and "blocks" in inner_node:
                if self._choice_has_terminal(inner_node["blocks"]):
                    return True

        return False


    def _error(self, scene, tag, line, message):
        self.diagnostics.append(
            Diagnostic(
                file=scene,
                tag=tag,
                line=line,
                column=0,
                length=None,
                severity="error",
                phase="structure",
                code="STRUCTURE_ERROR",
                message=f"{message}"
            )
        )

# --- Entry point called from validator_runtime.py ---
def check_structure(scene, runtime, diagnostics):
    validator = StructureValidator(runtime, diagnostics)
    validator.check_scene(scene)

