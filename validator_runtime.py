"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# validator_runtime.py
"""
Strict ValidatorRuntime - builds a strict graph_index from parser Scene objects,
performs strict reachability analysis, and does conservative variable scanning.

Design goals:
 - Strict: no silent guessing. Missing tags/chapters produce errors.
 - Deterministic: graph_index keys are (chapter, tag).
 - Useful diagnostics: detailed record_error messages for bad links / unreachable nodes.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple, Set, Optional
from collections import deque, defaultdict

# Project imports (assumes these modules exist in repo)
from parser.data_model import Node as ParsedNode
from structure_rules import check_structure
from validators.variables import VariableSummary
from utils.template import validate_template_indices

from validators.expr import extract_names as extract_names_from_expr, parse_mvar_args, parse_mvar_args, ExprError as ExprParseError
from engine.diagnostics import Diagnostic
from validators.validator_flow import validate_node_flow
from validators.flow_sensitive_temp import flow_sensitive_temp_check
from validators.static_expr import analyze_expression
from command_registry import COMMANDS
from parser.constants import _TEMPLATE_EXPR_RE


def extract_template_expr_vars(text: str):
    """Return set of identifier names used inside ${...} expressions in a text/string."""
    names = set()
    if not text:
        return names
    for inner in _TEMPLATE_EXPR_RE.findall(text):
        try:
            names |= set(extract_names_from_expr(inner))
        except Exception:
            # conservative: ignore parse errors in templates
            pass
    return names


@dataclass
class ValidatorNode:
    """Representation of a node in the validator graph index."""
    scene: str
    tag: str
    node_obj: Optional[Any] = None  # actual parser Node (if loaded)
    outgoing: List[Tuple[str, str]] = field(default_factory=list)
    incoming: List[Tuple[str, str]] = field(default_factory=list)
    line: Optional[int] = None


class ValidationError(Exception):
    pass


class ValidatorRuntime:
    """
    Strict validator that accepts either:
      - ValidatorRuntime(scene_map: Dict[str, Scene], config: Dict[str, Any])
      - ValidatorRuntime(config_obj) where config_obj has .files/.vars/.meta and optionally .scenes

    After construction:
      - self.scene_map : Dict[str, Scene]
      - self.config : dict-like mapping (if provided)
      - self.graph_index : Dict[(scene,tag) -> ValidatorNode] (built by build_graph())
    """

    def __init__(self, scene_map: Dict[str, Any] = None, config: Any = None, indent_width=2):
        # Accept scene_map dict directly OR config-like with .scenes
        if scene_map is None and config is None:
            raise ValidationError("ValidatorRuntime requires at least a scene_map or config.")

        # Normalize scene_map
        if isinstance(scene_map, dict):
            self.scene_map = scene_map
            self.config = config or {}
        else:
            # scene_map is a config-like object
            maybe = scene_map
            # prefer .scenes mapping if present
            if hasattr(maybe, "scenes") and isinstance(getattr(maybe, "scenes"), dict):
                self.scene_map = getattr(maybe, "scenes")
            else:
                # fallback: empty for strict mode (caller must pass scenes explicitly)
                self.scene_map = {}
            # create a simple mapping of config fields
            cfg = {}
            if hasattr(maybe, "meta"):
                cfg["meta"] = getattr(maybe, "meta")
            if hasattr(maybe, "vars"):
                cfg["vars"] = getattr(maybe, "vars")
            if hasattr(maybe, "files"):
                cfg["files"] = getattr(maybe, "files")
            self.config = cfg

        # If second arg is a dict and scene_map was not a dict, handle it
        if not isinstance(scene_map, dict) and isinstance(config, dict) and not self.scene_map:
            # caller likely passed (config_dict) as second arg
            self.scene_map = {}
            self.config = config

        # strict requirement: config mapping should at least be a dict-like
        if self.config is None:
            self.config = {}

        # outputs & internal state
        self.errors: List[str] = []
        self.warnings: List[str] = []

        self.var_summary = VariableSummary()
        self.global_vars: Dict[str, Any] = dict(self._cfg_get("vars", {}) or {})

        goals_config = self._cfg_get("goals", {})
        if isinstance(goals_config, dict):
            for goal_name in goals_config.keys():
                # We default to False for static analysis
                if goal_name not in self.global_vars:
                    self.global_vars[goal_name] = False

        # indices populated by helper functions & build_graph()
        self.first_tag_of: Dict[str, Optional[str]] = {}
        self.scene_tag_index: Dict[Tuple[str, str], Any] = {}  # maps (scene, tag) -> parser Node
        self.graph_index: Dict[Tuple[str, str], ValidatorNode] = {}

        # quick results caches
        self.quickpick_result: Dict[str, Any] = {}
        self.DEBUG: bool = False

        # prebuild lightweight indices if scenes are present
        if self.scene_map:
            self._rebuild_first_tag_index()
            self._rebuild_scene_tag_index()

        self.indent_width = indent_width

    # -------------------------
    # Small helpers to access config that might be dict-like
    # -------------------------
    def _cfg_get(self, key: str, default: Any = None) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        # object-like config support (not used in strict mode normally)
        if hasattr(self.config, key):
            return getattr(self.config, key)
        return default

    def _cfg_as_mapping(self) -> Dict[str, Any]:
        return {
            "meta": dict(self._cfg_get("meta", {}) or {}),
            "vars": dict(self._cfg_get("vars", {}) or {}),
            "files": list(self._cfg_get("files", []) or []),
            "start_scene": self._cfg_get("start_scene", None),
            "start_tag": self._cfg_get("start_tag", "start"),
        }

    # -------------------------
    # Error / warning recording
    # -------------------------

    def record_diagnostic(
        self,
        message: str,
        *,
        file: str = "?",
        tag: str = "?",
        line: int = 0,
        phase: str = "runtime",
        code: str = "GENERIC",
        severity: str = "error",
    ):
        try:
            line = int(line) if line is not None else 0
        except Exception:
            line = 0

        diag = Diagnostic(
            file=file or "?",
            tag=tag or "?",
            line=line,
            column=0,
            length=None,
            severity=severity,
            phase=phase,
            code=code,
            message=message,
        )

        if severity == "error":
            self.errors.append(diag)
        else:
            self.warnings.append(diag)


    def record_error(self, message: str, **kwargs):
        self.record_diagnostic(message, severity="error", **kwargs)

    def record_warning(self, message: str, **kwargs):
        self.record_diagnostic(message, severity="warning", **kwargs)

    # -------------------------
    # Index builders
    # -------------------------
    def _rebuild_first_tag_index(self):
        """Populate self.first_tag_of from scene_map in strict fashion."""
        self.first_tag_of = {}
        for scene_name, scene in self.scene_map.items():
            nodes_map = getattr(scene, "nodes", None)
            if not isinstance(nodes_map, dict) or not nodes_map:
                # explicit: scene with no nodes -> record None
                self.first_tag_of[scene_name] = None
                continue
            # choose lowest line number as first tag (parser-order)
            try:
                first_node = min(nodes_map.values(), key=lambda n: getattr(n, "line", 0))
                self.first_tag_of[scene_name] = getattr(first_node, "tag", None)
            except Exception:
                # guard: something unexpected in nodes_map contents
                self.first_tag_of[scene_name] = None

    def _rebuild_scene_tag_index(self):
        """
        Build mapping of (scene,tag) -> node_obj for strict presence checks.
        Replaces any prior mapping.
        """
        self.scene_tag_index = {}
        for scene_name, scene in self.scene_map.items():
            nodes_map = getattr(scene, "nodes", {}) or {}
            if not isinstance(nodes_map, dict):
                continue
            for tag, node in nodes_map.items():
                key = (scene_name, tag)
                self.scene_tag_index[key] = node

    # -------------------------
    # Graph builder (strict)
    # -------------------------
    def build_graph(self):
        """
        Build self.graph_index: keys = (scene,tag) -> ValidatorNode including outgoing/incoming edges.
        This is strict: missing chapters/tags produce record_error and the edge is omitted.
        """
        # reset graph
        self.graph_index = {}

        # populate node entries
        for (scene_name, scene) in self.scene_map.items():
            nodes_map = getattr(scene, "nodes", {}) or {}
            if not isinstance(nodes_map, dict):
                continue
            for tag, node in nodes_map.items():
                key = (scene_name, tag)
                gn = ValidatorNode(scene=scene_name, tag=tag, node_obj=node, line=getattr(node, "line", None))
                self.graph_index[key] = gn

        # collect outgoing edges strictly from parser-provided metadata
        for (scene_name, tag), gn in list(self.graph_index.items()):
            node = gn.node_obj
            if node is None:
                continue

            # --- use Node.iter_links() when available (parser-provided)
            try:
                for (tch, ttag, _meta) in node.iter_links():
                    # handle special __NEXT__ marker produced by parser for -next
                    if tch == "__NEXT__":
                        next_ch = self._get_next_chapter(scene_name)
                        if not next_ch:
                            self.record_error(
                                f"[STRUCTURE] Final scene '{scene_name}' contains an invalid trailing '-next' command sequence. Use '-end' to finish stories.",
                                file=scene_name,
                                tag=tag,
                                line=getattr(node, "line", 0),
                                code="TRAILING_NEXT_COMMAND",
                            )
                            continue
                        # next chapter found - require its first tag
                        resolved = self.first_tag_of.get(next_ch)
                        if resolved is None:
                            # can't resolve first tag for next chapter → skip (quiet)
                            continue
                        target = (next_ch, resolved)

                    else:
                        # tch must be present (chapter string)
                        if not tch:
                            # self.record_error(f"[LINK] {scene_name}:{tag} → malformed link: empty chapter in iter_links()")
                            # continue
                            self.record_error(
                                f"[LINK] {scene_name}:{tag} → malformed link: empty chapter in iter_links()",
                                file=scene_name,
                                tag=tag,
                                line=getattr(node, "line", 0),
                                code="RUNTIME_ERROR",
                            )
                            continue

                        # if tag omitted or empty, require first_tag_of to be present
                        if ttag in (None, "", []):
                            resolved = self.first_tag_of.get(tch)
                            if resolved is None:
                                # self.record_error(f"[LINK] {scene_name}:{tag} → -go_file {tch} has no resolvable first tag")
                                # continue
                                self.record_error(
                                    f"[LINK] {scene_name}:{tag} → -go_file {tch} has no resolvable first tag",
                                    file=scene_name,
                                    tag=tag,
                                    line=getattr(node, "line", 0),
                                    code="RUNTIME_ERROR",
                                )
                                continue
                            
                            target = (tch, resolved)
                        else:
                            target = (tch, ttag)

                    # check that target exists
                    if target not in self.graph_index:
                        self.record_error(
                            f"[LINK] {scene_name}:{tag} → target {target[0]}:{target[1]} not found",
                            file=scene_name,
                            tag=tag,
                            line=getattr(node, "line", 0),
                            code="RUNTIME_ERROR",
                        )
                        continue

                    gn.outgoing.append(target)
                    self.graph_index[target].incoming.append((scene_name, tag))
            except Exception:
                # If node doesn't implement iter_links elegantly, do not guess - skip
                pass

            # --- NEW: Extract links from choices if they aren't in iter_links
            choices = getattr(node, "choices", []) or []
            for choice in choices:
                # A choice dict might have a 'go', 'go_file', or 'blocks'
                # Let's look for any explicit 'continuations' or 'links' inside the choice
                target_tag = choice.get("go") or choice.get("tag")
                if target_tag:
                    # Handle local jump within the same chapter
                    target = (scene_name, target_tag)
                    if target in self.graph_index:
                        if target not in gn.outgoing:
                            gn.outgoing.append(target)
                            self.graph_index[target].incoming.append((scene_name, tag))

                # If choices have nested blocks (like yours do), we need to scan them too
                choice_blocks = choice.get("blocks", [])
                for b in choice_blocks:
                    if b.get("cmd") in ("-go", "-goto", "-cont"):
                        target = (scene_name, b.get("args").strip())
                        if target in self.graph_index:
                            if target not in gn.outgoing:
                                gn.outgoing.append(target)
                                self.graph_index[target].incoming.append((scene_name, tag))
                    elif b.get("cmd") == "-go_file":
                        # Handle cross-file jump: "-go_file demo testing_gofile"
                        args = b.get("args", "").split()
                        if args:
                            dest_ch = args[0]
                            dest_tag = args[1] if len(args) > 1 else self.first_tag_of.get(dest_ch)
                            target = (dest_ch, dest_tag)
                            if target in self.graph_index:
                                if target not in gn.outgoing:
                                    gn.outgoing.append(target)
                                    self.graph_index[target].incoming.append((scene_name, tag))

            # --- use Node.continuations if parser injected them (strict handling)
            for cont in getattr(node, "continuations", []) or []:
                if isinstance(cont, (tuple, list)):
                    tch = cont[0] if len(cont) > 0 else None
                    ttag = cont[1] if len(cont) > 1 else None
                    
                    # 1. BRIDGE: link HEAD to TAIL (the return point)
                    # We check cont[3] which is the resume_tag name
                    if len(cont) > 3 and cont[3]:
                        res_tag = cont[3]
                        # Ensure using the canonical scene_name from 
                        # graph_index keys to avoid extension mismatches.
                        resume_target = (scene_name, res_tag)
                        
                        if resume_target in self.graph_index:
                            if resume_target not in gn.outgoing:
                                gn.outgoing.append(resume_target)
                                self.graph_index[resume_target].incoming.append((scene_name, tag))
                                if self.DEBUG: print(f"BRIDGE SUCCESS: {tag} -> {res_tag}")
                        else:
                            # Fallback: Search for the tag if naming is weird
                            if self.DEBUG: print(f"BRIDGE FAILED: Looking for {res_tag} in {scene_name}")

                    # 2. RESOLVE: Determine the jump target (The actual Subroutine call)
                    target = None
                    if tch == "__NEXT__":
                        next_ch = self._get_next_chapter(scene_name)
                        if next_ch:
                            tgt_tag = self.first_tag_of.get(next_ch)
                            if tgt_tag: target = (next_ch, tgt_tag)
                        else:
                            # Record error for nested flow continuations
                            self.record_error(
                                f"[STRUCTURE] Subroutine block in final scene '{scene_name}' points to a non-existent trailing '-next' transition. Use '-end'.",
                                file=scene_name,
                                tag=tag,
                                line=getattr(node, "line", 0),
                                code="TRAILING_NEXT_COMMAND",
                            )
                    else:
                        dst_scene = tch or scene_name
                        tgt_tag = ttag or self.first_tag_of.get(dst_scene)
                        if tgt_tag: target = (dst_scene, tgt_tag)

                    # 3. COMMIT: Add the subroutine call edge
                    if target and target in self.graph_index:
                        if target not in gn.outgoing:
                            gn.outgoing.append(target)
                            self.graph_index[target].incoming.append((scene_name, tag))


            # --- NEW: Implicit Fall-Through Logic
            # If this node has NO outgoing edges yet, it must implicitly fall through 
            # to the next tag in the scene (provided one exists).
            if not gn.outgoing:
                # CHECK FOR TERMINALS: Don't fall through if the node ends the story
                blocks = getattr(node, "blocks", [])
                has_terminal = any(b.get("cmd") in ("-end", "-die", "-go_back", "-next") for b in blocks)
                
                if not has_terminal:
                    scene_obj = self.scene_map.get(scene_name)
            
                    if scene_obj:
                        scene_tags = list(getattr(scene_obj, "nodes", {}).keys())
                        try:
                            current_idx = scene_tags.index(tag)
                            if current_idx + 1 < len(scene_tags):
                                next_tag_name = scene_tags[current_idx + 1]
                                target = (scene_name, next_tag_name)
                                
                                if target in self.graph_index:
                                    gn.outgoing.append(target)
                                    self.graph_index[target].incoming.append((scene_name, tag))
                                    if self.DEBUG: print(f"DEBUG: Implicit fall-through: {tag} -> {next_tag_name}")
                        except ValueError:
                            pass

        # graph built
        return self.graph_index

    # -------------------------
    # Strict reachability analysis
    # -------------------------
    def _strict_reachability(self, start_chapter: str, start_tag: str) -> Dict[str, Any]:
        start = (start_chapter, start_tag)
        # print(f"DEBUG: BFS Starting at {start}")

        visited: Set[Tuple[str, str]] = set()
        stack = deque([start])

        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            
            visited.add(cur)
            # print(f"DEBUG: Visiting {cur[0]}:{cur[1]}")
            
            gn = self.graph_index.get(cur)
            if not gn:
                # print(f"DEBUG: Node {cur} NOT FOUND in graph_index")
                continue
                
            # print(f"DEBUG: {cur} has outgoing edges to: {gn.outgoing}")
            for tgt in gn.outgoing:
                if tgt not in visited:
                    stack.append(tgt)

        all_reportable_nodes = set()
        for ch, scene in self.scene_map.items():
            for tag, node in getattr(scene, "nodes", {}).items():
                # We ONLY exclude them from the "report card", not the "map"
                # is_synthetic = isinstance(tag, str) and "__sys_" in tag
                is_synthetic = isinstance(tag, str) and tag.startswith("__sys_")
                is_generated = getattr(node, "meta", {}).get("generated")
                
                if not (is_synthetic or is_generated):
                    all_reportable_nodes.add((ch, tag))

        # unreachable = reportable nodes that weren't visited
        unreachable = sorted(all_reportable_nodes - visited)

        return {"visited": visited, "unreachable": unreachable}

    # -------------------------
    # Quickpick (strict) - uses graph_index
    # -------------------------
    def quickpick(self, start_chapter: str, start_tag: str):
        """
        Return structure similar to older quickpick:
          { "visited": set((ch,tag)), "unreachable": [(ch,tag)], "edges": {"ch:tag": ["ch:tag", ...] } }
        Uses STRICT graph_index produced by build_graph().
        """
        if not self.graph_index:
            # build_graph must be called first (validate() does it)
            self.build_graph()

        start = (start_chapter, start_tag)
        if start not in self.graph_index:
            self.record_error(f"[QUICKPICK] Start node {start_chapter}:{start_tag} not in graph")
            return {"visited": set(), "unreachable": list(self.graph_index.keys()), "edges": {}}

        reach = self._strict_reachability(start_chapter, start_tag)
        visited = reach["visited"]
        unreachable = reach["unreachable"]

        edges_out = {}
        for key, gn in self.graph_index.items():
            src = f"{key[0]}:{key[1]}"
            edges_out[src] = [f"{t[0]}:{t[1]}" for t in gn.outgoing]

        self.quickpick_result = {"visited": visited, "unreachable": unreachable, "edges": edges_out}
        return self.quickpick_result


    # -------------------------
    # Block scanning for variable analysis
    # -------------------------

    def _scan_blocks_for_vars(self, blocks: List[Dict[str, Any]], scene_tag: Tuple[str, str]):
        """
        Fully recursive block scanner - handles:
        - text
        - blocks
        - node.blocks
        - choices[].blocks (pick branches)
        - nested pick-if
        - nested commands
        """
        for b in blocks or []:
            cmd = b.get("cmd")
            args = b.get("args") or ""
            line = b.get("__line__", b.get("line_no"))

            # Validate inline text templates
            text = b.get("text")
            if text:
                try:
                    validate_template_indices(
                        text,
                        scene_tag,
                        self.var_summary,
                        lambda msg: self._record_template_error(msg, scene_tag, line),
                    )

                except Exception:
                    pass

                # collect var usages from ${...} and mark them as used
                for varname in extract_template_expr_vars(text):
                    try:
                        self.var_summary.note_used(varname, source=(scene_tag[0], scene_tag[1], line))
                    except Exception:
                        try:
                            self.var_summary.note_used(varname, source=line)
                        except Exception:
                            pass

            # Validate text items
            for ti in b.get("text_items", []):
                text_item = ti if isinstance(ti, str) else ti.get("text", "")
                try:
                    validate_template_indices(
                        text_item,
                        scene_tag,
                        self.var_summary,
                        lambda msg: self._record_template_error(msg, scene_tag, line),
                    )

                except Exception:
                    pass

                # collect var usages from ${...} and mark them as used
                for varname in extract_template_expr_vars(text_item):
                    try:
                        self.var_summary.note_used(varname, source=(scene_tag[0], scene_tag[1], line))
                    except Exception:
                        try:
                            self.var_summary.note_used(varname, source=line)
                        except Exception:
                            pass

            # ---- Handle commands with args (variable detection) ----
            if cmd:
                # Pre-split args so we can always use 'first' (needed for tvar recording even if analyze succeeds)
                parts = (args or "").split()
                first = parts[0] if parts else None

                try:
                    # Let the analyzer attempt to parse (preferred path)
                    self.var_summary.analyze_block_args(cmd, args, scene_tag, line)
                except Exception:
                    # Fallback conservative behavior:
                    # - For declaration commands like -tvar we record a temp declaration.
                    # - For mutation commands like -mvar we should mark the variable as USED.
                    if not first:
                        # nothing to fall back on
                        pass
                    else:
                        if cmd.lstrip("-") == "tvar":
                            try:
                                self.var_summary.note_temp(scene_tag, first, line)
                            except Exception:
                                pass
                        else:
                            try:
                                self.var_summary.note_used(first, source=(scene_tag[0], scene_tag[1], line))
                            except Exception:
                                try:
                                    self.var_summary.note_used(first, source=line)
                                except Exception:
                                    pass

                # --- Ensure -tvar is always registered (even if analyze_block_args succeeded) ---
                # This solves the case where analyze_block_args parses the tvar but doesn't raise,
                # leaving us without an explicit temp_decl entry.
                try:
                    if cmd.lstrip("-") == "tvar" and first:
                        # If note_temp is idempotent it won't duplicate; otherwise it ensures temp_decl is present.
                        self.var_summary.note_temp(scene_tag, first, line)
                except Exception:
                    pass

                # In validator_runtime.py -> _scan_blocks_for_vars
                if cmd.lstrip("-") == "go_and_back":
                    resume_tag = b.get("resume_tag")
                    if resume_tag:
                        # Register the resume_tag as a "used" location so it's not marked unreachable
                        self.var_summary.note_used(resume_tag, source=(scene_tag[0], scene_tag[1], line))

                # --- static expression validation (best-effort) ---
                if analyze_expression and cmd:
                    name = cmd.lstrip("-")
                    expr_cmds = {
                        "if", "elseif", "pick_if",
                        "set", "add", "sub", "mvar", "mset", "madd", "msub",
                        "%+", "%-", "winner", "loser", "middle", "average", "range",
                    }

                    if name in expr_cmds:
                        try:
                            # 1. RESOLVE TYPES
                            known_types = {k: "any" for k in (self.global_vars or {}).keys()}
                            for k, v in (self.global_vars or {}).items():
                                if isinstance(v, bool): known_types[k] = "bool"
                                elif isinstance(v, int): known_types[k] = "int"
                                elif isinstance(v, float): known_types[k] = "float"
                                elif isinstance(v, str): known_types[k] = "str"

                            if hasattr(self.var_summary, "types"):
                                known_types.update(self.var_summary.types)

                            current_chapter = scene_tag[0]
                            for (sc, tg), mapping in (getattr(self.var_summary, "temp_declared", {}) or {}).items():
                                if sc == current_chapter:
                                    for tv in mapping.keys():
                                        known_types.setdefault(tv, "any")

                            # 2. EXTRACT EXPRESSION
                            CONDITIONAL_CMDS = {"if", "elseif", "pick_if"}
                            MUTATION_CMDS = {"mvar", "mset", "madd", "msub", "set", "add", "sub", "%+", "%-"}
                            
                            expr_to_check = ""
                            if name in CONDITIONAL_CMDS:
                                expr_to_check = args.strip()
                            elif name in MUTATION_CMDS:
                                try:
                                    _, _op, rhs = parse_mvar_args(args)
                                    expr_to_check = (rhs or "").strip()
                                except Exception:
                                    parts = (args or "").split(None, 1)
                                    expr_to_check = parts[1].strip() if len(parts) > 1 else ""
                            else:
                                expr_to_check = args.strip()

                            # 3. ANALYZE AND REPORT
                            if not expr_to_check:
                                # This is the ONLY place "empty expression" should be recorded
                                if name in (CONDITIONAL_CMDS | MUTATION_CMDS):
                                    self.record_error(
                                        f"In -{name}: empty expression 99",
                                        file=scene_tag[0], tag=scene_tag[1], line=line,
                                        phase="semantic", code="EXPR_ERROR"
                                    )
                            else:
                                res = analyze_expression(expr_to_check, known_types)

                                # Report Unknown Vars
                                if res.get("unknown_names"):
                                    for nm in sorted(res["unknown_names"]):
                                        self.record_error(
                                            f"Unknown variable '{nm}' in expression for -{name}",
                                            file=scene_tag[0], tag=scene_tag[1], line=line,
                                            phase="variables", code="UNDECLARED_VAR"
                                        )

                                # Report Semantic Errors (like syntax or type mismatch)
                                for e in res.get("errors", []):
                                    self.record_error(
                                        f"In -{name}: {e}",
                                        file=scene_tag[0], tag=scene_tag[1], line=line,
                                        phase="semantic", code="EXPR_ERROR"
                                    )

                        except Exception:
                            # Silently continue if the logic above crashes
                            pass
                
                # In _scan_blocks_for_vars, inside the "if cmd:" block, after the existing checks:
                if cmd == "-pic":
                    parts = (args or "").strip().split()
                    filename = parts[0] if parts else None
                    if filename:
                        import os
                        # Look for the image relative to the game root
                        # scene_tag[0] is the chapter/scene name; we need the project root
                        # The validator doesn't have direct access to the filesystem path,
                        # but we can check via a known relative path
                        images_dir = os.path.join(os.getcwd(), "scenes", "images")
                        image_path = os.path.join(images_dir, filename)
                        if not os.path.exists(image_path):
                            self.record_warning(
                                f"-pic: image file '{filename}' not found in scenes/images/",
                                file=scene_tag[0],
                                tag=scene_tag[1],
                                line=line,
                                phase="assets",
                                code="MISSING_IMAGE",
                            )

            # ---- DESCEND INTO SUB-BLOCKS ----

            # 1. If this block contains a 'node' bundle (typical)
            node = b.get("node")

            if isinstance(node, dict):
                # node.blocks
                self._scan_blocks_for_vars(node.get("blocks", []), scene_tag)

                # node.choices → pick branches
                for ch in node.get("choices", []):
                    self._scan_blocks_for_vars(ch.get("blocks", []), scene_tag)

            # 2. Direct choices[] ( alternative parser form )
            for ch in b.get("choices", []):
                self._scan_blocks_for_vars(ch.get("blocks", []), scene_tag)

            # 3. b.blocks (direct nested blocks)
            sub = b.get("blocks")
            if sub and sub is not blocks:
                self._scan_blocks_for_vars(sub, scene_tag)


    def validate(self, start_chapter: str, start_tag: str = "start") -> Dict[str, Any]:
        """
        Cleaned and ordered validation logic.
        """
        self.errors = []
        self.warnings = []
        self.var_summary = VariableSummary()
        self.graph_index = {}
        self._rebuild_first_tag_index()
        self._rebuild_scene_tag_index()
        diagnostics = []

        self.global_vars = dict(self._cfg_get("vars", {}) or {})
        goals_config = self._cfg_get("goals", {})
        if isinstance(goals_config, dict):
            for goal_name in goals_config.keys():
                if goal_name not in self.global_vars:
                    # We treat goals as booleans for type checking
                    self.global_vars[goal_name] = False 

        if (start_chapter, start_tag) not in self.scene_tag_index:
            self.record_error(f"[START] Start node {start_chapter}:{start_tag} not present.")
            return {"errors": self.errors, "warnings": self.warnings, "structurally_sound": False}

        # 1. Helper Functions (Defined FIRST to avoid UnboundLocalError)
        def _is_chapter_or_tag(name: str) -> bool:
            if name in (self.var_summary.declared or {}) or name in (self.global_vars or {}):
                return False
            if name in self.scene_map: return True
            for s in self.scene_map.values():
                if name in getattr(s, "nodes", {}): return True
            return False

        def _looks_like_literal(tok: str) -> bool:
            if not isinstance(tok, str): return False
            tok = tok.strip()
            if tok.lower() in ("true", "false", "none", "null"): return True
            if re.match(r"^-?\d+(\.\d+)?$", tok): return True
            if (tok.startswith('"') and tok.endswith('"')) or (tok.startswith("'") and tok.endswith("'")):
                return True
            return False

        # 2. Structural & Flow Checks
        for ch, scene in self.scene_map.items():
            if ch == "__stats__":          # stats has different rules, skip structure check
                continue
            check_structure(scene, self, diagnostics)
            for tag, node in (getattr(scene, "nodes", {}) or {}).items():
                validate_node_flow(node, scene)

        # 3. Variable Scanning Pass
        for ch, scene in self.scene_map.items():
            for tag, node in (getattr(scene, "nodes", {}) or {}).items():
                # Prefer parser-annotated usage
                for vn in getattr(node, "var_writes", []) + getattr(node, "var_mutations", []):
                    self.var_summary.note_declared(vn, getattr(node, "line", 0))
                    self.global_vars.setdefault(vn, None)
                for vn in getattr(node, "var_reads", []):
                    self.var_summary.note_used(vn, getattr(node, "line", 0))

                # Deep scan blocks
                self._scan_blocks_for_vars(getattr(node, "blocks", []), (ch, tag))

        # 4. Graph & Reachability
        self.build_graph()
        flow_sensitive_temp_check(self, start=(start_chapter, start_tag))
        self._strict_reachability(start_chapter, start_tag)

        # Step 5: Variable Consolidation
        undeclared_vars = []

        for v, sc, tg, line in undeclared_vars:
            is_found = (v in global_declared)
            
            if not is_found:
                for (decl_sc, decl_tg), mapping in self.var_summary.temp_declared.items():
                    # CHANGE: If it's a known TVAR anywhere in the project, 
                    # we allow it (or at least downgrade it to a warning) 
                    # to support cross-file subroutines.
                    if v in mapping:
                        is_found = True
                        break
            
            if not is_found:
                if v not in self.var_summary.temp_before_decl:
                    self.record_error(f"Variable '{v}' used but not declared.", 
                                    file=sc, tag=tg, line=line, phase="variables")

        # 6. Final Semantic/Type Checks
        # ----------------------------------------------------------
        # TYPE-MISUSE CHECK: Detect clear invalid operations on vars
        # ----------------------------------------------------------
        decl_type_map = {}

        # Merge declared types from summaries and config
        if hasattr(self.var_summary, "types"):
            decl_type_map.update(self.var_summary.types)
        # include config var types (runtime stores actual python values)
        for k, v in (self.global_vars or {}).items():
            if isinstance(v, bool):
                decl_type_map[k] = "bool"
            elif isinstance(v, int):
                decl_type_map[k] = "int"
            elif isinstance(v, float):
                decl_type_map[k] = "float"
            elif isinstance(v, str):
                decl_type_map[k] = "str"

        # Scan scene nodes for mutation commands and check contextual misuse
        for (ch, scene) in self.scene_map.items():
            for (tag, node) in (getattr(scene, "nodes", {}) or {}).items():
                line = getattr(node, "line", None)
                cmd = getattr(node, "cmd", None)
                args = getattr(node, "args", None)

                if not cmd:
                    continue

                cname = cmd.lstrip("-")

                # operations that definitely involve arithmetic
                arith_ops = ("add", "sub", "madd", "msub", "%+", "%-")
                other_mut_ops = ("set", "mvar", "tvar", "var")

                if cname in arith_ops:
                    # expect: args = "varName value"
                    parts = (args or "").split()
                    if parts:
                        varname = parts[0]
                        vtype = decl_type_map.get(varname)

                        if vtype == "bool":
                            self.record_error(
                                f"Arithmetic operation '{cname}' on boolean variable '{varname}'",
                                file=ch,
                                tag=tag,
                                line=line,
                                phase="types",
                                code="TYPE_MISMATCH",
                            )

                # toggle misuse: (cmd == toggle)
                if cname == "toggle":
                    varname = (args or "").split()[0] if args else None
                    if varname:
                        vtype = decl_type_map.get(varname)
                        if vtype not in ("bool", "toggle"):
                            self.record_error(
                                f"[TYPE] toggle used on non-boolean var '{varname}'",
                                file=ch,
                                tag=tag,
                                line=line,
                                phase="types",
                                code="TYPE_MISMATCH",
                            )

        # debug output if requested
        if self.DEBUG:
            print("ValidatorRuntime DEBUG: graph_index nodes:", len(self.graph_index))
            print("CONFIG mapping:", self._cfg_as_mapping())

        for d in diagnostics:
            if d.severity == "error":
                self.errors.append(d)
            elif d.severity == "warning":
                self.warnings.append(d)

        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "structurally_sound": len(self.errors) == 0,
        }


    # -------------------------
    # Utility: get next chapter using strict config order
    # -------------------------
    def _get_next_chapter(self, chapter_name: str) -> Optional[str]:
        """
        Return the next chapter filename using the strict configuration list,
        completely isolating special meta-files like stats.
        """
        # Read from our new config property, fallback to map keys if absent
        keys = self.config.get("story_order", list(self.scene_map.keys()))
        
        # Strip out __stats__ just in case it bled into story_order somewhere
        keys = [k for k in keys if k != "__stats__"]
        
        try:
            idx = keys.index(chapter_name)
        except ValueError:
            return None
            
        if idx + 1 < len(keys):
            return keys[idx + 1]
        return None

