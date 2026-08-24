# storygraph/exporters/debug_canvas_export.py
import json
import uuid
from storygraph.layout.flow_layout import compute_flow_layout


NODE_WIDTH = 260
NODE_HEIGHT = 80
X_STEP = 320
Y_STEP = 140


def export_debug_canvas(nodes, edges, out_path="semantic.canvas", *, center_columns=False):

    # --------------------------------------------------
    # 1. Compute flow-based layout
    # --------------------------------------------------
    layout = compute_flow_layout(
        nodes,
        edges,
        center_columns=center_columns,
    )

    canvas_nodes = []
    for n in nodes:
        box = layout.get(n.id)
        if not box:
            continue

        canvas_nodes.append({
            "id": n.id,
            "type": "text",
            "text": _build_text(n),
            "x": box.x,
            "y": box.y,
            "width": box.width,
            "height": box.height,
            **({"color": n.color} if getattr(n, "color", None) else {}),
        })

    canvas_edges = []
    for e in edges:
        if e.from_id not in layout:
            continue
        if e.to_id not in layout:
            continue

        canvas_edges.append({
            "id": uuid.uuid4().hex,
            "fromNode": e.from_id,
            "fromSide": "right",
            "toNode": e.to_id,
            "toSide": "left",
        })

    # 3. Process edges with routing logic
    canvas_edges = []
    for e in edges:
        from_box = layout.get(e.from_id)
        to_box = layout.get(e.to_id)

        if not from_box or not to_box:
            continue

        # ROUTING LOGIC
        from_side = "right"
        to_side = "left"
        color = None # Default Obsidian color

        if to_box.x <= from_box.x:
            from_side = "top"
            to_side = "top"
            color = "#272727"
            
        edge_data = {
            "id": uuid.uuid4().hex,
            "fromNode": e.from_id,
            "fromSide": from_side,
            "toNode": e.to_id,
            "toSide": to_side,
        }

        if color:
            edge_data["color"] = color

        # Add label if your DiagramEdge has one (for [resume] text)
        # if getattr(e, "label", None):
        #     edge_data["label"] = e.label

        canvas_edges.append(edge_data)

    payload = {
        "nodes": canvas_nodes,
        "edges": canvas_edges,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _build_text(node):
    """
    Matches your manual canvas style.
    """
    lines = [node.title]
    for op in node.ops or []:
        lines.append(op)
    return "\n".join(lines)

