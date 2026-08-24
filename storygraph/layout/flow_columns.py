# storygraph/layout/flow_columns.py

from collections import defaultdict
from storygraph.layout.flow_utils import is_backward_edge


def assign_flow_columns(nodes, edges):
    nodes_by_id = {n.id: n for n in nodes}
    col = {n.id: 0 for n in nodes}

    MAX_PASSES = len(nodes)

    for _ in range(MAX_PASSES):
        changed = False
        for e in edges:
            if e.from_id not in col or e.to_id not in col:
                continue

            if is_backward_edge(e, nodes_by_id):
                continue

            src = e.from_id
            dst = e.to_id

            if src not in col:
                continue

            # External / synthetic target → place one column right
            if dst not in col:
                col[dst] = col[src] + 1
                continue

            if col[dst] <= col[src]:
                col[dst] = col[src] + 1

                changed = True

        if not changed:
            break

    return col


