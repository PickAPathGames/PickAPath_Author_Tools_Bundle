# storygraph/model/build_semantic_graph.py
from .semantic_edges import SemanticEdge
from collections import defaultdict

TERMINAL_KINDS = {
    "terminal",
    "end",
    "go",
    "go_back",
    "go_and_back",
}

def build_semantic_edges(nodes):
    edges = []
    children_by_parent = defaultdict(list)

    # build lookup so we can inspect parent nodes
    node_by_id = {node.id: node for node in nodes}

    # preserve node iteration order
    for node in nodes:
        if not node.parent_id:
            continue

        parent = node_by_id.get(node.parent_id)
        if not parent:
            # parent filtered out or missing
            continue

        if parent.kind in TERMINAL_KINDS:
            continue

        children_by_parent[node.parent_id].append(node)

    for parent_id, children in children_by_parent.items():
        for idx, child in enumerate(children):
            edges.append(
                SemanticEdge(
                    from_id=parent_id,
                    to_id=child.id,
                    index=idx
                )
            )

    return edges




