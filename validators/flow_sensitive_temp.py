# validators/flow_sensitive_temp.py
from typing import Tuple, Set, Dict, List
import re
import keyword
from engine.command_kinds import TVAR_CMDS as _TVAR_CMDS, USE_CMDS as _USE_CMDS
from engine.command_kinds import COND_CMDS as _COND_CMDS, NON_VAR_ARG_CMDS as _NON_VAR_ARG_CMDS
from parser.constants import FLOW_IDENT_RE

"""
Simple flow-sensitive temp variable checker.

Usage:
    flow_sensitive_temp_check(validator, start=(start_chapter, start_tag))

It expects `validator.scene_map` to be available (mapping chapter->Scene),
and that each Scene.nodes[tag] is a node object with:
 - node.blocks: list of block-dicts with keys 'cmd' (e.g. '-tvar', '-mvar', '-if'...) and 'args'
 - node.continuations: list of continuation tuples (chapter, tag, ...) if present (parser injects)
If node.continuations is not present for a node, the checker will still work but may miss
edges produced by some syntactic constructs, the main graph is usually created earlier.
"""


def _extract_identifiers_from_arg(arg: str) -> List[str]:
    if not arg:
        return []
    # Remove string literals to avoid picking names inside quotes
    # crude removal: remove double-quoted and single-quoted spans
    arg_clean = re.sub(r'(["\']).*?\1', '""', arg)
    return FLOW_IDENT_RE.findall(arg_clean)

def _node_blocks(node):
    return getattr(node, "blocks", []) or []

def _node_continuations(node):
    return getattr(node, "continuations", []) or []

def flow_sensitive_temp_check(validator, start: Tuple[str,str] = None):
    """
    validator: instance of ValidatorRuntime
    start: (chapter, tag) starting node; if None, uses validator._rebuild_first_tag_index() mapping
    """
    scene_map = getattr(validator, "scene_map", {}) or {}
    # Build quick node lookup
    def get_node(ch, tg):
        sc = scene_map.get(ch)
        if not sc:
            return None
        return getattr(sc, "nodes", {}).get(tg)

    # Determine start
    if start is None:
        # try validator._rebuild_first_tag_index or first_tag_index attribute
        start = (validator._cfg_get("files", [])[0] if validator._cfg_get("files") else None, None)
        # fallback to validator._rebuild_first_tag_index existing data
        try:
            first_map = getattr(validator, "first_tag_of", None)
            if first_map:
                the_scene = next(iter(first_map.keys()))
                start = (the_scene, first_map[the_scene])
        except Exception:
            pass
    start_ch, start_tag = start

    # If missing start info, don't proceed
    if not start_ch or not start_tag:
        return

    # BFS / worklist: items are (chapter, tag, frozenset(declared_temp_vars))
    from collections import deque, defaultdict
    q = deque()

    # preload with temp declarations that belong to starting node
    initial_decl = set()
    if validator.var_summary.temp_declared.get((start_ch, start_tag)):
        initial_decl.update(validator.var_summary.temp_declared[(start_ch, start_tag)])
    start_state = frozenset(initial_decl)
    q.append((start_ch, start_tag, start_state))


    # Track for each node the set of declared-sets we've seen to avoid re-exploring subsets
    seen: Dict[Tuple[str,str], List[frozenset]] = defaultdict(list)

    def seen_contains_superior(existing_list, s: frozenset):
        # if any existing set is a superset of s, return True (no need to explore s)
        for ex in existing_list:
            if ex.issuperset(s):
                return True
        return False

    while q:
        ch, tg, ds = q.popleft()
        node = get_node(ch, tg)
        if node is None:
            continue

        key = (ch, tg)
        if seen_contains_superior(seen[key], ds):
            # there's already a visited set that dominates us, skip
            continue
        # record this set
        seen[key].append(ds)

        # Inspect node blocks for declarations and uses
        blocks = _node_blocks(node)
        # start with current declared set (mutable copy for scanning this node)
        current_decl = set(ds)

        # preload declarations the var_summary already knows for this node
        predecl = validator.var_summary.temp_declared.get((ch, tg), {})
        if predecl:
            current_decl.update(predecl.keys())


        # iterate blocks in sequence (declaration can precede uses in same node)
        for b in blocks:
            cmd = (b.get("cmd") or "").strip()
            raw_args = b.get("args")

            if isinstance(raw_args, list):
                args = " ".join(str(x) for x in raw_args)
            elif isinstance(raw_args, str):
                args = raw_args
            else:
                args = ""

            args = args.strip()

            # Normalize cmd tokens that may or may not include leading dash
            if not cmd.startswith("-"):
                cmd = "-" + cmd

            # tvar declaration
            if cmd in _TVAR_CMDS:
                parts = args.split()
                if parts:
                    varname = parts[0]
                    current_decl.add(varname)
                    # record temp declaration for validator runtime summary
                    try:
                        validator.var_summary.note_temp((ch, tg), varname, b.get("__line__"))
                    except Exception:
                        pass
                continue

            # conditional expressions: check identifiers inside args
            if cmd in _COND_CMDS:
                idents = _extract_identifiers_from_arg(args)
                for ident in idents:
                    # ignore boolean literals and numeric tokens
                    if ident in ("True", "False", "None", "null"):
                        continue
                    if re.match(r"^-?\d+(\.\d+)?$", ident):
                        continue
                    if ident in keyword.kwlist:
                        continue
                    # if neither global var nor in current_decl → report error
                    if ident not in current_decl and ident not in (validator.global_vars or {}):
                        validator.record_error(
                            f"Unknown identifier '{ident}' used in condition",
                            file=ch,
                            tag=tg,
                            line=b.get("__line__", 0),
                            phase="variables",
                            code="TEMP_UNKNOWN_IDENT",
                        )

                continue

            # uses / mutations
            if cmd in _USE_CMDS:
                # first token is var name usually
                parts = args.split()
                if parts:
                    target = parts[0]
                    # allow shorthand where developers write '+5' and no var, skip those
                    if target and not re.match(r'^[+\-*/%]', target):
                        if target not in current_decl and target not in (validator.global_vars or {}):
                            validator.var_summary.temp_before_decl.add(target)

                            validator.record_error(
                                f"Use of temp-var '{target}' before declaration",
                                file=ch,
                                tag=tg,
                                line=b.get("__line__", 0),
                                phase="variables",
                                code="TEMP_BEFORE_DECL",
                            )

                # continue scanning, do not modify declarations
                continue

            # generic / unknown command: also check its args for bare references (fallback)
            # look for identifiers in args that look like variable names (not inside quotes)
            if args and cmd not in _NON_VAR_ARG_CMDS:
                idents = _extract_identifiers_from_arg(args)
                for ident in idents:
                    if ident in ("True", "False", "None", "null"):
                        continue
                    if re.match(r"^-?\d+(\.\d+)?$", ident):
                        continue
                    if ident in keyword.kwlist:
                        continue
                    # skip if looks like a qualified target (chapter:tag or contains '/')
                    # naive check: if args contains ':' near ident, treat as link not var.
                    if ":" in args:
                        # likely a target, skip
                        continue
                    if ident not in current_decl and ident not in (validator.global_vars or {}):
                        # only flag if it looks like a variable usage (best-effort)
                        # avoid flagging choice labels etc. This is conservative.
                        validator.record_error(
                            f"Possibly undeclared identifier '{ident}' in command arguments",
                            file=ch,
                            tag=tg,
                            line=b.get("__line__", 0),
                            phase="variables",
                            code="TEMP_POSSIBLE_UNDECLARED",
                        )

        # push successors, primarily rely on node.continuations for the parser-produced graph
        conts = _node_continuations(node)
        if conts:
            for cont in conts:
                # cont may be tuple (chapter, tag, ...)
                try:
                    succ_ch = cont[0]
                    succ_tag = cont[1]
                except Exception:
                    continue
                q.append((succ_ch, succ_tag, frozenset(current_decl)))
        else:
            # fallback: no continuations, try to follow explicit '-go' blocks found in blocks
            for b in blocks:
                cmd = (b.get("cmd") or "").strip()
                if not cmd.startswith("-"):
                    cmd = "-" + cmd
                if cmd in ("-go", "-go_and_back", "-go_file", "-next"):
                    tgt = (b.get("args") or "").strip()
                    # simple parse: if contains space, assume "scene tag" or "scene:tag" not handled here
                    if not tgt:
                        continue
                    # if "scene tag" form
                    parts = tgt.split()
                    if len(parts) == 1:
                        succ_tag = parts[0]
                        succ_ch = ch  # same chapter
                    else:
                        # "scene tag" or "scene:tag" -> we take last as tag, first as scene if looks like
                        succ_ch = parts[0]
                        succ_tag = parts[1]
                    q.append((succ_ch, succ_tag, frozenset(current_decl)))

