"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# storygraph/diagram/build_diagram_graph.py

from collections import defaultdict
from storygraph.diagram.diagram_nodes import DiagramNode
from storygraph.diagram.diagram_edges import DiagramEdge
from storygraph.model.semantic_nodes import SemanticNode, semantic_id
from storygraph.diagram.auto_tags import is_auto_tag


INLINE_OPS = {"mvar", "tvar", "set", "pause", "snapshot"}
# Treat 'logic' and 'resume' nodes as layout paths, not visible boxes
INVISIBLE_KINDS = {"choice", "resume"}# "logic"


def build_diagram_graph(semantic_nodes, flow_edges):
    nodes_by_id = {n.id: n for n in semantic_nodes}

    scene_counters = defaultdict(int)

    # Identify system nodes that should NEVER have a card
    # Explicitly catch both __sys_ and __res_ prefixes
    auto_ids = {
        n.id for n in semantic_nodes
        if (n.tag and (n.tag.startswith("__sys_") or n.tag.startswith("__res_"))) 
        or (n.label and ("__sys_" in n.label or "__res_" in n.label))
        or n.kind in INVISIBLE_KINDS
    }

    outgoing = defaultdict(list)
    for e in flow_edges:
        outgoing[e.from_id].append(e)

    diagram_nodes = {}

    for n in semantic_nodes:
        if n.id in auto_ids:
            continue

        idx = scene_counters[n.scene]
        scene_counters[n.scene] += 1

        diagram_nodes[n.id] = DiagramNode(
            id=n.id,
            kind=n.kind,
            title=n.label,
            ops=_extract_ops(n),
            source_ref=n.id,
            scene=n.scene,
            index=idx,
        )


    def resolve_visible_targets(node_id, seen=None):
        if seen is None:
            seen = set()

        if node_id in seen:
            return []

        seen.add(node_id)

        if node_id not in auto_ids:
            return [node_id]

        targets = []
        for e in outgoing.get(node_id, []):
            targets.extend(resolve_visible_targets(e.to_id, seen))
        return targets

    diagram_edges = []

    seen_edges = set()
    for e in flow_edges:
        if e.from_id in auto_ids:
            continue

        for tgt in resolve_visible_targets(e.to_id):
            # If the resolved target is STILL a system tag, skip it entirely.
            if "__sys_" in tgt or "__res_" in tgt:
                continue

            edge_key = (e.from_id, tgt)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            if tgt not in diagram_nodes:
                sn = nodes_by_id.get(tgt)
                
                if not sn or sn.id in auto_ids:
                    continue

                diagram_nodes[tgt] = DiagramNode(
                    id=tgt,
                    kind="external",
                    title=sn.label if sn else tgt,
                    ops=[],
                    source_ref=tgt,
                    scene=sn.scene if sn else "__external__",
                    index=sn.index if sn else 10_000,
                )

            label = None
            if hasattr(e, "label") and e.label:
                label = e.label
            elif hasattr(e, "type"):
                label = e.type

            # Ensure we don't draw redundant self-loops created by internal logic processing
            if e.from_id == tgt:
                continue

            diagram_edges.append(
                DiagramEdge(
                    from_id=e.from_id,
                    to_id=tgt,
                    label=label,
                )
            )

    for node in diagram_nodes.values():
        assert not (node.title.startswith("__sys_") or node.title.startswith("__res_")), (
            f"Auto tag leaked into canvas: {node.title}"
        )

    return list(diagram_nodes.values()), diagram_edges

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _is_diagram_node(sn: SemanticNode) -> bool:
    # Never show auto-tags
    if is_auto_tag(sn):
        return False

    # Only show meaningful flow nodes
    return sn.kind in {
        "tag",
        "option",
        "if",
        "elseif",
        "else",
    }


def _make_diagram_node(sn: SemanticNode) -> DiagramNode:
    return DiagramNode(
        id=semantic_id("diagram", sn.id),
        kind=sn.kind,
        title=_build_title(sn),
        ops=_extract_ops(sn),
        source_ref=sn.id,
    )


def _build_title(sn: SemanticNode) -> str:
    return sn.label


def _extract_ops(sn: SemanticNode) -> list[str]:
    ops = []
    for block in sn.blocks or []:
        # Normalize block to a string string if it arrived as a raw dictionary
        if isinstance(block, dict):
            cmd = block.get("cmd", "")
            args = block.get("args")
            block_str = f"{cmd} {args}".strip() if args is not None else cmd
        else:
            block_str = block

        # Skip empty blocks to avoid IndexError
        if not block_str.strip():
            continue
            
        # Safely extract the command name
        parts = block_str.lstrip("-").split()
        if not parts:
            continue
            
        cmd = parts[0]
        
        # Only show the commands specified in INLINE_OPS
        if cmd in INLINE_OPS:
            # Already filtered out 'nl' in extract_semantic.py,
            # but this check ensures it doesn't show up here either.
            if cmd == "nl":
                continue
            ops.append(block_str)
    return ops

