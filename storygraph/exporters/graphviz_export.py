# storygraph/exporters/graphviz_export.py

from storygraph.layout.flow_layout import compute_flow_layout

DOT_PALETTE = {
    "1": "#ef4444",  # red
    "2": "#f97316",  # orange
    "3": "#eab308",  # yellow
    "4": "#22c55e",  # green
    "5": "#3b82f6",  # blue
    "6": "#a855f7",  # purple
}


PX_TO_IN = 1 / 96  # Graphviz uses inches


def export_graphviz(nodes, edges, out_path):
    """
    Export Graphviz DOT using flow layout columns.
    """

    # --------------------------------------------------
    # 1. Compute layout (authoritative)
    # --------------------------------------------------
    layout = compute_flow_layout(nodes, edges)

    # --------------------------------------------------
    # 2. Derive columns from X positions
    # --------------------------------------------------
    columns = {}
    for node in nodes:
        box = layout.get(node.id)
        if not box:
            continue

        col_key = int(box.x)  # x is column anchor
        columns.setdefault(col_key, []).append(node)

    # Sort columns left → right
    sorted_columns = [
        columns[k] for k in sorted(columns.keys())
    ]

    lines = []
    lines.append("digraph G {")
    lines.append("  rankdir=LR;")
    lines.append("  nodesep=0.5;")
    lines.append("  ranksep=0.75;")
    lines.append("  splines=true;")
    lines.append("  node [shape=box, fontname=\"Inter\"];")

    # --------------------------------------------------
    # 3. Emit nodes
    # --------------------------------------------------
    id_map = {}

    node_index = 0
    for col in sorted_columns:
        for node in col:
            nid = f"N{node_index}"
            node_index += 1
            id_map[node.id] = nid

            label = _escape(node.title)
            if node.ops:
                label += "\\n" + "\\n".join(_escape(op) for op in node.ops)

            box = layout[node.id]

            attrs = {
                "label": f"\"{label}\"",
                "fixedsize": "true",
                "width": f"{box.width * PX_TO_IN:.2f}",
                "height": f"{box.height * PX_TO_IN:.2f}",
            }

            color = getattr(node, "color", None)
            fill = _resolve_color(color)
            if fill:
                attrs["style"] = "filled"
                attrs["fillcolor"] = f"\"{fill}\""

            attr_str = ", ".join(f"{k}={v}" for k, v in attrs.items())
            lines.append(f"  {nid} [{attr_str}];")

    # --------------------------------------------------
    # 4. Column ranks
    # --------------------------------------------------
    for col in sorted_columns:
        if len(col) <= 1:
            continue

        ids = [id_map[n.id] for n in col if n.id in id_map]
        joined = "; ".join(ids)
        lines.append(f"  {{ rank=same; {joined}; }}")

    # --------------------------------------------------
    # 5. Edges
    # --------------------------------------------------
    for e in edges:
        if e.from_id not in id_map or e.to_id not in id_map:
            continue

        src = id_map[e.from_id]
        dst = id_map[e.to_id]

        if e.label:
            lbl = _escape(e.label)
            lines.append(f"  {src} -> {dst} [label=\"{lbl}\"];")
        else:
            lines.append(f"  {src} -> {dst};")

    lines.append("}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _resolve_color(color):
    if isinstance(color, str):
        if color.startswith("#"):
            return color
        return DOT_PALETTE.get(color)
    return None


def _escape(text):
    return (
        text.replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
    )


