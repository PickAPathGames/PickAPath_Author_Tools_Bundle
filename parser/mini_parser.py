from __future__ import annotations


"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""

"""
MiniParser (refined)

- Context-aware indentation-driven parser for CYOA-style game text.
- Produces parser.data_model.Scene and Node instances with structured `blocks`,
  `choices`, `continuations`, `terminals`, `ifs`, and metadata suitable for
  validator_runtime and downstream tooling.

Design goals implemented:
- Defensive code: avoid attribute errors, ensure consistent node shapes.
- Centralized debug output through _dbg().
- Clear collection phases: feed lines -> collect structure -> inject synthetic nodes ->
  inject implicit continuations.
- Maintain backward-compatible representation for continuations:
    tuple format: (chapter, tag, is_go_and_back, resume_tag_or_None)
"""
# parser/mini_parser.py


import re
import uuid
import copy
import hashlib
from typing import Optional, Dict, Any, List, Tuple

from parser.constants import INDENT_RE, CMD_RE, TAG_RE
from parser.data_model import Node, Scene, ParserEdge
from validators.expr import parse_mvar_args

STRUCT_COMMANDS = {
    "pick", "pick_once", "pick_if", "if", "elseif", "else",
    "go", "go_file", "go_and_back", "go_back",
    "next", "end", "pause", "single_pick", "user_input"
}


class MiniParser:
    def __init__(self, filename: str, chapter_name: str, command_registry, indent_size: int = 2):
        self.filename = filename
        self.chapter = chapter_name
        self.registry = command_registry

        self.indent_size = indent_size
        
        # Parser outputs
        self.scene = Scene(chapter_name)

        # Runtime / parse state
        self.current_tag: Optional[str] = None
        self.current_node: Optional[Node] = None
        self.line_no = 0
        self.env: Dict[str, Any] = {}
        self.DEBUG = False
        self.errors: List[Dict[str, Any]] = []
        self.declared_vars = set()
        self.file_id = chapter_name  # Needed by Node

        # Stack for indentation context: list of (level:int, container:Node|dict, context:str)
        self._indent_stack: List[Tuple[int, Any, str]] = []
        self._skip_until_level: Optional[int] = None
        self._last_tag_at_level: Dict[int, str] = {}
        # Source lines (populated at parse time) used by _has_blocking_between
        self.source_lines: List[str] = []

        self._pick_counter = 0
        self._pick_seq = 0
        self._entropy_counter = 0
        self._resume_counters = {}

    # ---
    # Debug
    # ---
    def _dbg(self, *args, **kwargs):
        if self.DEBUG:
            print(*args, **kwargs)

    # ---
    # Node creation / util
    # ---
    def _new_node(self, tag: str, indent: int):
        semantic_id = f"{self.chapter}::{tag}"
    
        node = Node(
            tag=tag,
            line=self.line_no,
            blocks=[]
        )
        node.u_id = semantic_id
        node.indent = indent
        node.chapter = self.chapter
        node.file_id = self.file_id

        # Legacy compatibility fields (MiniParser depends on these)
        node.links = []
        node.continuations = []
        node.var_reads = []
        node.var_writes = []
        node.var_mutations = []

        self.scene.nodes[tag] = node

        return node

    def _indent_error(self, msg: str, *, column: int = 0, length: int = 1):
        """
        Record a parser error in a structured, editor-friendly format.
        """
        rec = {
            "file": self.scene.name if hasattr(self, "scene") else self.chapter,
            "line": self.line_no,          # 1-based
            "column": max(column, 0),      # 0-based
            "length": max(length, 1),      # must be >= 1
            "severity": "error",
            "code": "PARSER_ERROR",
            "msg": msg,
        }
        self.errors.append(rec)
        self._dbg(
            f"[PARSER_ERROR] {rec['file']}:{rec['line']}:{rec['column']} → {msg}"
        )

    def _top_frame(self):
        if not self._indent_stack:
            return (0, None, "root")
        return self._indent_stack[-1]

    # ---
    # Line classification helpers
    # ---
    def _detect_line_type(self, line: str):
        stripped = line.lstrip()
        if not stripped:
            return ("blank", None)
        if stripped.startswith("#"):
            return ("choice", None)
        if TAG_RE.match(line) or stripped.startswith("-tag "):
            return ("tag", None)
        m = re.match(r"^\s*-(?P<cmd>\S+)(?:\s+(?P<rest>.*))?$", line)
        if m:
            return ("command", m.group("cmd"))
        return ("text", None)

    def _is_pick_header_cmd(self, cmd_name: Optional[str]):
        if not cmd_name:
            return False
        return cmd_name.startswith("pick") or cmd_name in ("if", "else", "elseif", "single_pick")

    def _allowed_in_context(self, context: str, line_type: str, cmd_name: Optional[str]) -> bool:
        if context in ("root", "node", "generic"):
            return True
        if context == "pick_block":
            if line_type == "choice":
                return True
            if line_type == "command" and self._is_pick_header_cmd(cmd_name):
                return True
            if line_type == "tag":
                return True
            return False
        if context == "choice":
            if line_type == "choice":
                return False
            return True
        if context in ("if_block", "else_block"):
            if line_type == "choice":
                return False
            return True
        return True

    # ---
    # Core: feed a single line
    # ---
    def feed_line(self, raw_line: str):
        self.line_no += 1
        m = INDENT_RE.match(raw_line)
        if not m: return

        # 1. Basic Line Processing
        stripped_left = raw_line.lstrip()

        # Adding blank lines and skipping multiple occurrences
        if not stripped_left:
            current_level, current_container, current_context = self._top_frame()
            
            # CRASH GUARD: If there's no container yet, ignore leading blank lines gracefully
            if current_container is None:
                return

            target = current_container if isinstance(current_container, Node) else current_container.get("text", [])
            
            # the last text item was already a blank line
            items = target if isinstance(target, list) else target.text_items
            if items and items[-1].get("text") == "":
                return 
                
            text_obj = {"type": "text", "text": "", "__line__": self.line_no}
            if isinstance(current_container, Node):
                current_container.text_items.append(text_obj)
            else:
                current_container.setdefault("text", []).append(text_obj)
            return

        indent_str = raw_line[:len(raw_line) - len(stripped_left)].replace("\t", " " * self.indent_size)
        indent = len(indent_str)
        # line = stripped_left.rstrip()
        line = stripped_left.rstrip('\n\r')
        level = indent // self.indent_size

        self.source_lines.append(raw_line)
        # if not line.strip() or line.lstrip().startswith("-ignore"): return
        if line.lstrip().startswith("-ignore"): return

        # 2. Indent Validation & Context
        if indent % self.indent_size != 0:
            self._indent_error(f"Misaligned indentation: {indent} spaces", column=0, length=indent)
            return

        if self._skip_until_level is not None:
            if level > self._skip_until_level: return
            self._skip_until_level = None

        if not self._indent_stack:
            self._indent_stack = [(0, self.current_node or None, "node" if self.current_node else "root")]

        current_level, current_container, current_context = self._top_frame()

        if level > current_level + 1:
            self._indent_error(f"Unexpected indent level {level}", column=0, length=indent)
            self._skip_until_level = current_level
            return

        if level < current_level:
            while self._indent_stack and self._indent_stack[-1][0] > level:
                self._indent_stack.pop()
            current_level, current_container, current_context = self._top_frame()

        # After resolving current_container from the indent stack:
        if not line.strip():
            # Treat a blank line as just... a blank line of text.
            # This keeps the validator happy because it stays in text_items.
            text_obj = {"type": "text", "text": "", "__line__": self.line_no}
            if isinstance(current_container, Node): 
                current_container.text_items.append(text_obj)
            else: 
                current_container.setdefault("text", []).append(text_obj)
            return

        # 3. TAG HANDLING
        mt = TAG_RE.match(line)
        is_cmd_prefix = line.strip().startswith("-")
        cmd_candidate = line.strip().split()[0].lstrip("-") if is_cmd_prefix else ""

        if (line.startswith("-tag ") or (mt and cmd_candidate not in self.registry.commands)):
            tag = mt.group("tag") if mt else line.split(None, 1)[1]
            self.current_tag = tag
            self.current_node = self._new_node(tag, indent=level)
            self._indent_stack = [(0, self.current_node, "node")]
            return

        # 4. COMMAND & CHOICE HANDLING
        cmd_m = CMD_RE.match(line)
        if cmd_m:
            cmd_name = cmd_m.group("cmd")
            rest = (cmd_m.group("rest") or "").strip()
            full_cmd_name = f"-{cmd_name}"

            # A: Pick-Logic (Special Handling for -if, -pick_if, -pick_once inside a pick)
            is_pick_logic = current_context == "pick_block" and full_cmd_name in ("-if", "-pick_if", "-pick_once")

            # -> Section 4A (Pick-Logic)
            if is_pick_logic:
                # 1. Separate the logic from the label/comment
                logic_part, label_part = rest.split("#", 1) if "#" in rest else (rest, "")
                logic_part = logic_part.strip()

                # the PARENT block was a -pick_once
                is_parent_once = current_container.get("cmd") == "-pick_once"

                # 2. detect merged commands
                # Check if a second command like -pick_if or -pick_once is hidden inside the logic_part
                merged_cmd = None
                for cmd_trigger in ["-pick_if", "-pick_once", "-if"]:
                    if cmd_trigger in logic_part:
                        primary_logic, secondary_part = logic_part.split(cmd_trigger, 1)
                        logic_part = primary_logic.strip()
                        merged_cmd = f"{cmd_trigger} {secondary_part.strip()}"
                        break

                choice_text = line.lstrip()[1:].strip()
                # Create a hash of the text so it's stable even if you move the block
                content_hash = self._generate_content_id(choice_text)

                # 3. Build the choice object
                choice = {
                    "text": [{"type": "choice_text", "text": label_part.strip(), "__line__": self.line_no}],
                    "blocks": [],
                    "cond": logic_part, # This is now JUST the condition
                    "merged_logic": merged_cmd, # Store the second condition here
                    "is_if_choice": full_cmd_name == "-if",
                    "choice_subtype": "pick_once" if (is_parent_once or full_cmd_name == "-pick_once" or (merged_cmd and "-pick_once" in merged_cmd)) else "standard",
                    "choice_id": f"{current_container.get('pick_id')}::opt_{content_hash}"
                }
                # self._choice_counter += 1
                current_container["choices"].append(choice)
                self._indent_stack.append((level + 1, choice, "choice"))
                return

            # B: Standard Command Registry Logic
            registry_key = full_cmd_name if full_cmd_name in self.registry._parsers else cmd_name
            if registry_key in self.registry._parsers:
                
                # 1. Generate a stable, unique ID for THIS specific block
                clean_rest = rest.split('#')[0].strip()
                # Including line_no ensures two identical commands in one tag have different IDs
                hash_input = f"{self.current_tag}{registry_key}{clean_rest}{self.line_no}"
                content_hash = self._generate_content_id(hash_input)
                
                # This is the "General" ID for the map to track
                block_u_id = f"{self.chapter}::{self.current_tag}::b_{content_hash}"
                
                # 2. Create the block via registry
                block = self.registry.create_block(registry_key, self, rest, self.line_no, level)

                # 3. Assign IDs
                block["u_id"] = block_u_id # Every block gets this for the breadcrumb trail
                
                # 4. Special Handling for Picks
                if registry_key in ("-pick", "-pick_once", "-pick_if", "-single_pick"):
                    # Picks use a 'p_' prefix which the map specifically looks for
                    pick_id = f"{self.chapter}::{self.current_tag}::p_{content_hash}"
                    block["u_id"] = pick_id # Overwrite with the specific pick ID
                    block["pick_id"] = pick_id
                    
                    if "node" in block and isinstance(block["node"], dict):
                        block["node"]["pick_id"] = pick_id
                        block["node"]["u_id"] = pick_id
                
                # Special cases for flow control
                if registry_key == "-go_and_back":
                    count = self._resume_counters.get(self.current_tag, 0)
                    res_tag = f"__res_{self.current_tag}_{count}"
                    self._resume_counters[self.current_tag] = count + 1
                    
                    block["resume_tag"] = res_tag
                    
                    # Create the new node and mark it as a resume point
                    res_node = self._new_node(res_tag, indent=level)
                    res_node.u_id = f"{self.chapter}::{res_tag}"
                    res_node.meta["auto_resume"] = True
                    res_node.meta["origin_tag"] = self.current_tag # Link back to the caller
                    res_node.line = self.line_no

                    self.scene.nodes[res_tag] = res_node
                    self.current_node = res_node
                    
                    # Update stack so subsequent lines (the combat logic) go into THIS node
                    self._indent_stack[-1] = (level, res_node, "node")

                # Add block to container
                target = current_container.blocks if isinstance(current_container, Node) else current_container.setdefault("blocks", [])
                target.append(block)

                if block.get("expects_indent"):
                    self._indent_stack.append((level + 1, block["node"], block["context_name"]))
                return

        # 5. CHOICE (#) HANDLING
        if line.lstrip().startswith("#"):
            if current_context != "pick_block":
                self._indent_error("Choice '#' outside pick block", column=0, length=1)
                return
            
            choice_text = line.lstrip()[1:].strip()
            # Create a hash of the text so it's stable even if you move the block
            content_hash = self._generate_content_id(choice_text)
            
            choice = {
                "text": [{"type": "choice_text", "text": line.lstrip()[1:].strip(), "__line__": self.line_no}],
                "blocks": [],
                "choice_subtype": "pick_once" if current_container.get("is_once_block") else "standard",
                "choice_id": f"{current_container.get('pick_id')}::opt_{content_hash}"
            }
            current_container["choices"].append(choice)
            self._indent_stack.append((level + 1, choice, "choice"))
            return

        # 6. PLAIN TEXT (Fallback)
        target = current_container if current_container else self.current_node
        
        # CRASH GUARD: Dialogue text or comment written before any -tag exists
        if not target:
            self._indent_error("Orphaned dialogue text: Written before declaring any '-tag'", column=0, length=len(line))
            return

        original_trailing = raw_line.rstrip('\n\r\t')
        has_trailing_space = original_trailing.endswith(' ')
        text_content = line  
        if has_trailing_space:
            text_content = line + ' '   
        text_obj = {"type": "text", "text": text_content, "__line__": self.line_no}
        if isinstance(target, Node): 
            target.text_items.append(text_obj)
        else: 
            target.setdefault("text", []).append(text_obj)


    # ---
    # Helpers for continuation detection / blocking
    # ---
    def _node_blocks_block_continuation(self, node: Node) -> bool:
        """
        Strict check: Does this node have a command that kills the fallthrough?
        """
        for blk in getattr(node, "blocks", []):
            cmd = blk.get("cmd", "").lstrip("-")
            # If any of these exist, the node is 'sealed'
            if cmd in ("go", "go_file", "go_and_back", "next", "end", "go_back"):
                return True
        return False

    # ---
    # Implicit continuation injector
    # ---
    def _inject_implicit_continuations(self, scene: Scene):
        self._dbg(">>> [IMPLICIT] Running implicit continuation pass")
        nodes = sorted(scene.nodes.values(), key=lambda n: n.line)
        
        for i in range(len(nodes) - 1):
            node = nodes[i]
            next_node = nodes[i + 1]

            # 1. NEW SEAL CHECK: 
            # If the node has choices, it's a branching point. 
            # It should NOT fall through to the next tag unless it's a '-pick_once' 
            # AND we explicitly want it to (but usually, picks swallow flow).
            if node.choices or node.continuations or node.terminals:
                continue

            # 2. Level Check
            if getattr(node, "indent", 0) != getattr(next_node, "indent", 0):
                continue

            # 3. Structural Blocking (Simplified)
            if self._has_blocking_between(node.line, next_node.line):
                continue

            node.continuations.append((self.chapter, next_node.tag, False, None))


    def _has_blocking_between(self, start_line: int, end_line: int) -> bool:
        # Use the full unfiltered file lines so indices match node.line (1-based)
        file_lines = getattr(self, '_all_file_lines', None) or self.source_lines
        if not file_lines:
            return False

        # node.line is 1-based; convert to 0-based slice
        # scan from line AFTER start_line up to (not including) end_line
        s_idx = start_line      # 0-based = 1-based line start_line+1 - 1
        e_idx = end_line - 1    # 0-based = 1-based line end_line - 1

        for ln in file_lines[s_idx:e_idx]:
            stripped = ln.strip()
            if not stripped:
                continue

            indent = len(ln) - len(ln.lstrip())
            if indent > 0:
                continue

            first = stripped.split()[0]

            # Hit another tag, everything from here belongs to the next node
            if first == "-tag":
                break

            if first in ("-if", "-elseif", "-else", "-pick", "-pick_once"):
                return True
        return False

    # ---
    # Small helpers
    # ---
    def _get_active_pick_block(self):
        node = self.current_node
        if not getattr(node, "blocks", None):
            return None
        for blk in reversed(node.blocks):
            if blk.get("cmd") in ("-pick", "-pick_once"):
                return blk
        return None

    def _parser_state(self):
        return {"parser": self, "scene": self.scene, "current_node": self.current_node, "env": self.env}

    # ---
    # Post-parse: collection / synth
    # ---
    def _collect_structure(self, node: Node, blocks: List[Dict[str, Any]], nested=False):
        for blk in blocks:
            cmd = blk.get("cmd")
            if not cmd: continue
            name = cmd.lstrip("-")
            
            # 1. Handle Jumps
            if name in ("go", "go_file", "next", "go_and_back"):
                args = (blk.get("args") or "").strip().split()
                resume_tag = blk.get("resume_tag")

                if name == "next" and not args:
                    tgt = ("__NEXT__", None, False, None)
                elif not args: 
                    continue
                else:
                    if name == "go_file":
                        tgt_ch, tgt_tag = (args[0], args[1]) if len(args) >= 2 else (args[0], None)
                    elif name == "go_and_back":
                        # --- ROBUST SINGLE ARGUMENT HANDLING ---
                        if len(args) == 1:
                            tgt_ch = self.chapter
                            tgt_tag = args[0]
                        else:
                            tgt_ch = args[0]
                            tgt_tag = args[1]
                    else:
                        tgt_ch, tgt_tag = self.chapter, args[0]
                    
                    tgt = (tgt_ch, tgt_tag, (name == "go_and_back"), resume_tag)

                # Only add to the Tag's continuation list if NOT nested in an IF or PICK.
                if not nested:
                    if tgt not in node.continuations:
                        node.continuations.append(tgt)
                continue

            # 2. Handle Terminals
            if name == "end" or name == "go_back":
                if name not in node.terminals:
                    node.terminals.append(name)
                continue

            # 3. UNIFIED PICK COLLECTION
            if name in ("pick", "single_pick"):
                # Use blk.get() safely to find choices
                choices_list = blk.get("choices", []) if "choices" in blk else blk.get("node", {}).get("choices", [])
                for choice in choices_list:
                    c = {
                        "text": choice.get("text"),
                        "line": choice.get("__line__"),
                        "choice_subtype": choice.get("choice_subtype", "standard"),
                        "continuation": self._extract_choice_continuation(choice),
                    }
                    node.choices.append(c)
                    # Recursively collect blocks inside the choice with nested=True
                    self._collect_structure(node, choice.get("blocks", []), nested=True)
                continue

            # 4. Handle Conditional Blocks
            if name in ("if", "else", "elseif"):
                if name == "if" and "cond" in blk:
                    node.ifs.append(blk["cond"])
                # Recursively collect blocks inside the if/else with nested=True
                self._collect_structure(node, blk.get("node", {}).get("blocks", []), nested=True)
                continue

    def _collect_logic_only(self, node: Node, blocks: List[Dict[str, Any]]):
        # Regex to find variable names (words not followed by '(' and not keywords)
        # This is a 'quick and dirty' way to find what variables are 'Read'
        VAR_FINDER = r'\b(?!(?:and|or|not|True|False)\b)[a-zA-Z_][a-zA-Z0-9_]*\b'

        for blk in blocks:
            cmd = blk.get("cmd", "").lstrip("-")
            
            # 1. Track Reads in Conditions
            if "cond" in blk and blk["cond"]:
                reads = re.findall(VAR_FINDER, blk["cond"])
                for var in reads:
                    if var not in node.var_reads:
                        node.var_reads.append(var)

            # 2. Track Variable Mutations (mvar)
            if cmd == "mvar":
                args_str = blk.get("args", "")
                try:
                    name, op, rhs = parse_mvar_args(args_str)
                    mutation = (name, op)
                    if mutation not in node.var_mutations:
                        node.var_mutations.append(mutation)
                    if name not in node.var_writes:
                        node.var_writes.append(name)
                except Exception as e:
                    # Log the error so you know WHY the mutation wasn't tracked
                    self._indent_error(f"Invalid mvar syntax '{args_str}': {str(e)}")

            # 3. Recurse into nested blocks (like -if blocks)
            if "node" in blk and isinstance(blk["node"], dict):
                self._collect_logic_only(node, blk["node"].get("blocks", []))
            
            # 4. Handle choices inside picks
            if "choices" in blk:
                for choice in blk["choices"]:
                    if "cond" in choice and choice["cond"]:
                        reads = re.findall(VAR_FINDER, choice["cond"])
                        node.var_reads.extend([v for v in reads if v not in node.var_reads])
                    self._collect_logic_only(node, choice.get("blocks", []))


    def _extract_choice_continuation(self, choice: Dict[str, Any]):
        """
        Search blocks inside a choice for a go/go_file/go_and_back and return normalized continuation
        (chapter, tag, is_go_and_back, resume_tag_or_None) or None if no continuation.
        """
        for blk in choice.get("blocks", []):
            cmd = blk.get("cmd")
            if not cmd:
                continue
            name = cmd.lstrip("-")
            if name in ("go", "next"):
                args = (blk.get("args") or "").strip().split()
                if not args:
                    continue
                if len(args) >= 2:
                    return (args[0], args[1], False, None)
                else:
                    return (self.chapter, args[0], False, None)
            if name == "go_file":
                args = (blk.get("args") or "").strip().split()
                if len(args) >= 2:
                    return (args[0], args[1], False, None)
            if name == "go_and_back":
                args = (blk.get("args") or "").strip().split()
                if len(args) >= 1:
                    resume = blk.get("resume_tag")
                    if len(args) == 1:
                        # Local file jump inside choice
                        return (self.chapter, args[0], True, resume)
                    else:
                        # Cross-file jump inside choice
                        return (args[0], args[1], True, resume)
        return None

    # ---
    # Main parse entry
    # ---
    def parse(self, source: str) -> Scene:
        # 1. First Pass: Build the initial node map from raw text
        self.source_lines = []
        self._all_file_lines = source.splitlines(keepends=True)
        for ln in source.splitlines():
            self.feed_line(ln)

        # 2. Structural Split: Handle Subroutines (-go_and_back)
        # This physically divides nodes into Head and Tail (resume) parts.
        # self._inject_resume_tags()

        # 3. Canonical Initialization
        # Ensure every node (including the new synthetic ones) has required lists.
        for node in self.scene.nodes.values():
            node.choices = []
            node.continuations = []
            node.terminals = []
            node.ifs = []
            # Clear edges in case of re-parsing or stale data
            node.edges = [] 

        # 4. Logical Collection
        # Now walk the blocks. Since we split the nodes, the trailing logic
        # (like -next) is now inside the resume nodes where it belongs.
        chapter_snapshot_vars = []
        for node in self.scene.nodes.values():
            self._collect_structure(node, getattr(node, "blocks", []))
            
            # Collect snapshots (using the deterministic collection we discussed)
            node_snapshot_vars = []
            for blk in node.blocks:
                if blk.get("cmd") == "-snapshot": # Note the dash if your parser adds it
                    raw = blk.get("args") or []
                    if isinstance(raw, str): raw = raw.split()
                    vars_list = [v.strip() for v in raw if v.strip()]
                    node_snapshot_vars.extend(vars_list)
            
            if node_snapshot_vars:
                node.meta = getattr(node, "meta", {}) or {}
                node.meta["snapshot"] = list(set(node_snapshot_vars))
                chapter_snapshot_vars.extend(node_snapshot_vars)

        self.scene.snapshot_targets = list(set(chapter_snapshot_vars))

        # 5. Graph Finalization
        # Inject fallthroughs only where no explicit flow exists.
        try:
            self._inject_implicit_continuations(self.scene)
        except Exception as e:
            self._dbg("[WARN] _inject_implicit_continuations failed:", e)

        # Build the final ParserEdges for the VM
        for node in self.scene.nodes.values():
            self._inject_edges_from_structure(node)

        self.scene.parser_errors = list(self.errors)
        return self.scene


    def _inject_edges_from_structure(self, node: Node):
        """
        Convert parser-collected continuations and choices into ParserEdge objects.
        """
        seen_edges = set()

        # 1. Process Continuations
        for (ch, tg, is_gab, resume) in node.continuations:
            if tg is None: continue
            edge_key = (ch, tg, "go_and_back" if is_gab else "go")
            if edge_key not in seen_edges:
                node.edges.append(
                    ParserEdge(chapter=ch, tag=tg, kind="go_and_back" if is_gab else "go", condition=None)
                )
                seen_edges.add(edge_key)

        # 2. Process Choices
        for choice in node.choices:
            cont = choice.get("continuation")
            if cont:
                ch, tg, is_gab, resume = cont
                if tg:
                    # 🔑 Metadata in the Edge: Help the simulator/validator know if this is a 'once' edge
                    kind = "choice_once" if choice.get("choice_subtype") == "pick_once" else "choice"
                    edge_key = (ch, tg, kind)
                    if edge_key not in seen_edges:
                        node.edges.append(
                            ParserEdge(chapter=ch, tag=tg, kind=kind, condition=None)
                        )
                        seen_edges.add(edge_key)


    def _generate_content_id(self, *parts: str) -> str:
        """Generates a stable 8-char hash from strings."""
        # Clean parts to make them typo-resistent (lowercase, no spaces)
        clean_content = "".join(parts).lower().replace(" ", "").strip()
        
        return hashlib.blake2b(clean_content.encode(), digest_size=4).hexdigest()



# ---
# quick demo run if module run directly
# ---
if __name__ == "__main__":
    SAMPLE = """
# start
This is the start.
# second
This is second.
# third
This is third.
"""
    # minimal fake registry
    class DummyRegistry:
        def handle(self, cmd, state, rest):
            # no-op for demo
            return None

    p = MiniParser("demo.txt", "demo", DummyRegistry())
    s = p.parse(SAMPLE)
    s.debug_dump()
