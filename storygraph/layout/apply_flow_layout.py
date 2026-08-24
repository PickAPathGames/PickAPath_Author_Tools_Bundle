# storygraph/layout/apply_flow_layout.py

from storygraph.layout.flow_layout import compute_flow_layout


def apply_flow_layout(nodes, edges, *, center_columns=False, config=None):
    """
    Mutates nodes in-place:
    - sets node.x
    - sets node.y
    """

    layout = compute_flow_layout(
        nodes,
        edges,
        center_columns=center_columns,
    )

    for node in nodes:
        box = layout.get(node.id)
        if not box:
            continue

        node.x = box.x
        node.y = box.y
