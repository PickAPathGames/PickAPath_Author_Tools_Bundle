# storygraph/exporters/mermaid_export.py

from collections import OrderedDict


# Obsidian-like palette (1–6)
MERMAID_PALETTE = {
    "1": "#ef4444",  # red
    "2": "#f97316",  # orange
    "3": "#eab308",  # yellow
    "4": "#22c55e",  # green
    "5": "#3b82f6",  # blue
    "6": "#a855f7",  # purple
}



def export_mermaid(nodes, edges, out_path):
    """
    Export a Mermaid flowchart (LR).
    Geometry is ignored; structure only.
    """

    # Stable ordering for diffs
    nodes = sorted(nodes, key=lambda n: (n.scene, n.index))

    lines = []
    lines.append("flowchart LR")

    # Mermaid node ids must be simple
    id_map = {}
    for i, node in enumerate(nodes):
        mid = f"N{i}"
        id_map[node.id] = mid

        label = _escape(node.title)
        if node.ops:
            label += "\\n" + "\\n".join(_escape(op) for op in node.ops)

        lines.append(f'  {mid}["{label}"]')

    # Edges
    for e in edges:
        if e.from_id not in id_map or e.to_id not in id_map:
            continue

        src = id_map[e.from_id]
        dst = id_map[e.to_id]

        if e.label:
            lbl = _escape(e.label)
            lines.append(f"  {src} -->|{lbl}| {dst}")
        else:
            lines.append(f"  {src} --> {dst}")

    # Styles (node colors)
    for node in nodes:
        color = getattr(node, "color", None)
        if not color:
            continue

        mid = id_map[node.id]
        fill = _resolve_color(color)
        if fill:
            lines.append(
                f"  style {mid} fill:{fill},stroke:#333,stroke-width:1px"
            )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _resolve_color(color):
    """
    Accepts:
      - "1" .. "6"
      - "#rrggbb"
    """
    if isinstance(color, str):
        if color.startswith("#"):
            return color
        return MERMAID_PALETTE.get(color)
    return None


def _escape(text):
    # Mermaid nodes with quotes inside [" "] often fail.
    # Replacing actual double quotes with single quotes or "smart" quotes
    # is the safest way to ensure the graph always renders.
    return (
        text.replace("\\", "\\\\")
            .replace('"', "'")  # Change double quotes to single quotes
            .replace("\n", "<br/>") # Mermaid uses <br/> for newlines in some versions
    )