# storygraph/exporters/drawio_export.py
import xml.etree.ElementTree as ET
import uuid

CARD_GAP_X = 80
CARD_GAP_Y = 40
# COLUMN_WIDTH = 480   # card width + horizontal spacing
# ROW_HEIGHT = 160     # card height + vertical spacing

DRAWIO_PALETTE = {
    "1": "#ef4444",  # red
    "2": "#22c55e",  # green
    "3": "#3b82f6",  # blue
    "4": "#eab308",  # yellow
    "5": "#a855f7",  # purple
    "6": "#ec4899",  # pink
}


def export_drawio(nodes, edges, out_path):
    """
    Export draw.io (.drawio / .xml) diagram using absolute layout.
    """

    mxfile = ET.Element("mxfile", host="app.diagrams.net")
    diagram = ET.SubElement(mxfile, "diagram", name="Storygraph")

    model = ET.SubElement(
        diagram,
        "mxGraphModel",
        dx="1000",
        dy="1000",
        grid="1",
        gridSize="10",
        guides="1",
        tooltips="1",
        connect="1",
        arrows="1",
        fold="1",
        page="1",
        pageScale="1",
        pageWidth="1920",
        pageHeight="1080",
        math="0",
        shadow="0",
    )

    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    id_map = {}

    # ─────────────────────────────────────────────
    # Nodes
    # ─────────────────────────────────────────────
    for node in nodes:
        nid = _uid()
        id_map[node.id] = nid

        style = [
            "rounded=1",
            "whiteSpace=wrap",
            "html=1",
            "align=center",
            "verticalAlign=middle",
            "strokeColor=#1f2937",
        ]

        fill = _resolve_color(getattr(node, "color", None))
        if fill:
            style.append(f"fillColor={fill}")

        label = node.title
        if node.ops:
            label += "\n" + "\n".join(node.ops)

        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": nid,
                "value": _escape(label),
                "style": ";".join(style),
                "vertex": "1",
                "parent": "1",
            },
        )

        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": str(int(node.x)),
                "y": str(int(node.y)),
                "width": str(int(node.width)),
                "height": str(int(node.height)),
                "as": "geometry",
            },
        )



    # ─────────────────────────────────────────────
    # Edges
    # ─────────────────────────────────────────────
    for e in edges:
        if e.from_id not in id_map or e.to_id not in id_map:
            continue

        eid = _uid()

        style = [
            "endArrow=classic",
            "html=1",
            "rounded=0",
        ]

        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": eid,
                "value": _escape(e.label) if e.label else "",
                "style": ";".join(style),
                "edge": "1",
                "parent": "1",
                "source": id_map[e.from_id],
                "target": id_map[e.to_id],
            },
        )

        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    tree = ET.ElementTree(mxfile)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _resolve_color(color):
    if not isinstance(color, str):
        return None
    if color.startswith("#"):
        return color
    return DRAWIO_PALETTE.get(color)


def _uid():
    return uuid.uuid4().hex


def _escape(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

# def _node_x(node):
#     return node.column * COLUMN_WIDTH

# def _node_y(node):
#     return node.row * ROW_HEIGHT
