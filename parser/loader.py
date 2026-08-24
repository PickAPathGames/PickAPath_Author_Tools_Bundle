"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# parser/loader.py
from typing import Optional, Dict, List, Tuple, Set
from collections import defaultdict
import os
import re

from .data_model import Node, Scene
from command_registry import COMMANDS
from .mini_parser import MiniParser
from validators.expr import extract_names  
from engine.command_kinds import WRITE_CMDS as _WRITE_CMDS, MUTATE_CMDS as _MUTATE_CMDS
from engine.command_kinds import BARE_NAV_CMDS, BUILTIN_WORDS
from engine.command_kinds import BARE_VAR_CONTEXT_CMDS


# =========================================================
# DEBUG FLAG
# =========================================================
DEBUG = False

def dprint(*args):
    if DEBUG:
        print("[LOADER]", *args)


# Expose MiniParser under the generic name "Parser"
Parser = MiniParser


# ---------------------------------------------------------
# Helper: extract variable names from expressions
# ---------------------------------------------------------
def _collect_var_names_from_expr(expr: str) -> Set[str]:
    try:
        names = set(extract_names(expr))
        clean = {n for n in names if n not in BUILTIN_WORDS}
        if DEBUG and clean:
            dprint("  EXPR VAR NAMES:", expr, "→", clean)
        return clean
    except Exception:
        return set()


# ---------------------------------------------------------
# MAIN DEBUG-ENHANCED BLOCK SCANNER
# ---------------------------------------------------------
def _scan_blocks_for_node_vars(blocks, node: "Node"):
    """
    Populate node.var_reads, node.var_writes, node.var_mutations.
    Fully instrumented with debug prints.
    """
    # dprint(f"SCAN_NODE {node.scene}:{node.tag}")
    chapter = getattr(node, "chapter", None) or getattr(node, "scene_name", None) or "?"
    dprint(f"SCAN_NODE {chapter}:{node.tag}")

    dprint(f"  Total blocks: {len(blocks) if blocks else 0}")

    reads = set()
    writes = set()
    muts = set()

    for idx, b in enumerate(blocks or []):
        dprint(f"    BLOCK[{idx}]:", b.get("cmd"), "args=", b.get("args"))

        cmd = b.get("cmd")
        args = b.get("args", "") if b.get("args") is not None else ""
        cond = b.get("cond", None)
        text = b.get("text", "")

        # ----------------------------------------
        # descend into node-embedded structures
        # ----------------------------------------
        if not cmd:
            if "node" in b and isinstance(b["node"], dict):
                dprint("      DESCEND: nested node")
                _scan_blocks_for_node_vars(b["node"].get("blocks", []), node)
                for ch in b["node"].get("choices", []):
                    dprint("        DESCEND: nested choice")
                    choice_cond = ch.get("cond") or ch.get("choice_cond") or ch.get("choice_cond_text")
                    if choice_cond:
                        reads.update(_collect_var_names_from_expr(choice_cond))
                    _scan_blocks_for_node_vars(ch.get("blocks", []), node)
            if "blocks" in b and b["blocks"] is not blocks:
                dprint("      DESCEND: b['blocks']")
                _scan_blocks_for_node_vars(b["blocks"], node)
            if text:
                reads.update(_collect_var_names_from_expr(text))
            continue

        # cmd exists
        cmd_name = cmd.lstrip("-").strip()

        # SPECIAL: navigation commands don't inspect args for variables
        if cmd_name in BARE_NAV_CMDS:
            dprint("      NAV CMD:", cmd_name)
            if "node" in b and isinstance(b["node"], dict):
                _scan_blocks_for_node_vars(b["node"].get("blocks", []), node)
                for ch in b["node"].get("choices", []):
                    _scan_blocks_for_node_vars(ch.get("blocks", []), node)
            continue

        # CONDITION FIELD
        if cond:
            dprint("      CONDITIONAL:", cond)
            if isinstance(cond, str):
                reads.update(_collect_var_names_from_expr(cond))

        # ARGUMENTS (maybe expression)
        if cmd_name in BARE_VAR_CONTEXT_CMDS:
            if args:
                dprint("      VAR_CTX:", args)
                reads.update(_collect_var_names_from_expr(args))
        else:
            if isinstance(args, str) and args and any(ch in args for ch in ("$", "{", "}", "+", "-", "*", "/", "(", ")", "==", ">", "<", "!=")):
                dprint("      HEUR_EXPR:", args)
                reads.update(_collect_var_names_from_expr(args))

        # WRITE / MUTATION detection
        if cmd_name in _WRITE_CMDS:
            dprint("      WRITE CMD:", cmd_name)
            if isinstance(args, str) and args.strip():
                tok = args.strip().split(None, 1)[0]
                tok = tok.rstrip("=").strip()
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tok):
                    writes.add(tok)
                    dprint("        VAR WRITE:", tok)
                if cmd_name in _MUTATE_CMDS:
                    muts.add(tok)
                    dprint("        VAR MUT:", tok)

        if text:
            reads.update(_collect_var_names_from_expr(text))

        if "text_items" in b:
            for ti in b["text_items"]:
                reads.update(_collect_var_names_from_expr(ti))

        if "node" in b and isinstance(b["node"], dict):
            _scan_blocks_for_node_vars(b["node"].get("blocks", []), node)
            for ch in b["node"].get("choices", []):
                choice_cond = ch.get("cond") or ch.get("choice_cond")
                if choice_cond:
                    reads.update(_collect_var_names_from_expr(choice_cond))
                _scan_blocks_for_node_vars(ch.get("blocks", []), node)

        if "blocks" in b and b["blocks"] is not blocks:
            _scan_blocks_for_node_vars(b["blocks"], node)

    # Store results
    node.var_reads = sorted(reads - writes)
    node.var_writes = sorted(writes)
    node.var_mutations = sorted(muts)

    dprint(f"  RESULT: reads={node.var_reads} writes={node.var_writes} mut={node.var_mutations}")


# ---------------------------------------------------------
# LINK NORMALIZER (debug-patched)
# ---------------------------------------------------------
def normalize_scene_links_and_vars(scene, first_tag_of: dict = None, files_order: List[str] = None):
    """
    Strict normalization of links and variable usage. Fully debug-instrumented.
    """
    from parser.data_model import LinkTarget

    dprint(f"NORMALIZE SCENE: {scene.name}")
    dprint("  first_tag_of:", first_tag_of)
    dprint("  files_order:", files_order)

    first_tag_of = first_tag_of or {}
    files_order = list(files_order) if files_order else None

    scene_id = getattr(scene, "file_id", None) or getattr(scene, "scene_id", None) or scene.name

    def _link_exists(node, chapter, tag, is_gab, is_next):
        for lt in getattr(node, "links", []):
            if lt.chapter == chapter and lt.tag == tag and lt.is_go_and_back == is_gab and lt.is_next == is_next:
                return True
        return False

    scene.file_meta = getattr(scene, "file_meta", {}) or {}
    scene.file_meta.setdefault("link_errors", [])

    for tag, node in list(scene.nodes.items()):
        # print("DIR NODE               ", dir(node))
        dprint(f"  NODE {scene.name}:{tag}")
        # dprint("    INITIAL go:", node.go)
        dprint("    continuations:", node.continuations)

        # continuations
        for cont in list(node.continuations or []):
            try:
                dprint("    CONT:", cont)

                if isinstance(cont, (tuple, list)):
                    tch = cont[0]
                    ttag = cont[1] if len(cont) > 1 else None
                    is_gab = bool(cont[2]) if len(cont) > 2 else False

                    # RESOLUTION LOGIC
                    if tch == "__NEXT__":
                        if files_order:
                            try:
                                idx = files_order.index(scene.name)
                                if idx + 1 < len(files_order):
                                    nx = files_order[idx + 1]
                                    nxt_tag = first_tag_of.get(nx)
                                    if nxt_tag:
                                        node.add_link(nx, nxt_tag, is_go_and_back=is_gab)
                                        # IMPORTANT: Also add to edges for Validator compatibility
                                        if not hasattr(node, 'edges'): node.edges = []
                                        node.edges.append({"chapter": nx, "tag": nxt_tag, "kind": "next"})
                                        dprint("    RESOLVED __NEXT__:", nx, nxt_tag)
                            except ValueError:
                                pass # Scene name not in files_order
                        continue

                    # Standard Target Resolution
                    tgt_ch = tch or scene_id
                    final_tag = ttag
                    if ttag is None:
                        final_tag = first_tag_of.get(tgt_ch)
                    
                    node.add_link(tgt_ch, final_tag, is_go_and_back=is_gab)
                    
                    # Ensure it shows up in edges for pickquick
                    if not hasattr(node, 'edges'): node.edges = []
                    kind = "go_and_back" if is_gab else "go"
                    node.edges.append({"chapter": tgt_ch, "tag": final_tag, "kind": kind})

                elif isinstance(cont, str):
                    node.add_link(scene_id, cont)
                    if not hasattr(node, 'edges'): node.edges = []
                    node.edges.append({"chapter": scene_id, "tag": cont, "kind": "go"})

            except Exception as e:
                dprint("    CONT ERROR:", e)
                continue

        # Finally: variable analysis
        _scan_blocks_for_node_vars(node.blocks, node)


# ---------------------------------------------------------
# Scene file reader
# ---------------------------------------------------------

def parse_scene_file(path: str, chapter_name: Optional[str] = None, command_registry=COMMANDS, indent_size: int = 2) -> Scene:
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()

    if chapter_name is None:
        chapter_name = os.path.splitext(os.path.basename(path))[0]

    parser = MiniParser(
        filename=path, 
        chapter_name=chapter_name, 
        command_registry=command_registry, 
        indent_size=indent_size
    )
    scene = parser.parse(content)

    scene.file_meta = getattr(scene, "file_meta", {}) or {}

    dprint(f"PARSED SCENE: {chapter_name} from {path}")
    return scene


# Monkey-patch Parser.parse_file
def _parser_parse_file(self, path: str, chapter_name: Optional[str] = None) -> Scene:
    return parse_scene_file(path, chapter_name, command_registry=COMMANDS)

Parser.parse_file = _parser_parse_file


# ---------------------------------------------------------
# Old adjacency builder (unchanged)
# ---------------------------------------------------------
def build_adj_from_scene(scene: Scene) -> Dict[Tuple[str, str], List[Tuple[str, str]]]:
    adj = defaultdict(list)
    for tag, node in scene.nodes.items():
        src = (scene.name, tag)

        if node.go and isinstance(node.go, dict):
            _, pch, ptag, _ = node.go.get("parsed", (None, None, None, -1))
            if ptag:
                adj[src].append((pch or scene.name, ptag))

        for c in node.choices:
            go = c.get("go")
            if go and isinstance(go, dict):
                _, gch, gtag, _ = go.get("parsed", (None, None, None, -1))
                if gtag:
                    adj[src].append((gch or scene.name, gtag))
    return adj

