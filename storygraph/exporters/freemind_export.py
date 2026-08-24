# storygraph/exporters/freemind_export.py
import xml.etree.ElementTree as ET
from collections import defaultdict
import uuid
import time

FREEMIND_PALETTE = {
    "1": "#ef4444",  # red
    "2": "#22c55e",  # green
    "3": "#3b82f6",  # blue
    "4": "#eab308",  # yellow
    "5": "#a855f7",  # purple
    "6": "#ec4899",  # pink
}


def export_freemind(nodes, edges, out_path, scene_name="Scene"):
    """
    Export a FreeMind (.mm) file.
    Tree-based, lossy view.
    """

    # ─────────────────────────────────────────────
    # 1. Index nodes
    # ─────────────────────────────────────────────
    nodes_by_id = {n.id: n for n in nodes}
    incoming = defaultdict(list)

    for e in edges:
        if e.to_id in nodes_by_id and e.from_id in nodes_by_id:
            incoming[e.to_id].append(e.from_id)

    # Deterministic ordering helper
    def node_sort_key(nid):
        n = nodes_by_id[nid]
        return (getattr(n, "column", 0), getattr(n, "row", 0), n.index)

    # ─────────────────────────────────────────────
    # 2. Choose parents (spanning tree)
    # ─────────────────────────────────────────────
    parent = {}
    for nid, srcs in incoming.items():
        if not srcs:
            continue

        if len(srcs) > 1:
            print(
                f"[freemind] WARNING: node {nid} has "
                f"{len(srcs)} parents, keeping first"
            )

        srcs = sorted(srcs, key=node_sort_key)
        parent[nid] = srcs[0]

    # ─────────────────────────────────────────────
    # 3. Build children map
    # ─────────────────────────────────────────────
    children = defaultdict(list)
    for child, par in parent.items():
        children[par].append(child)

    # roots = [n.id for n in nodes if n.id not in parent]
    roots = [n.id for n in nodes if n.id not in parent]

    if not roots:
        roots = [n.id for n in nodes]  # attach everything to scene root


    # ─────────────────────────────────────────────
    # 4. XML construction
    # ─────────────────────────────────────────────
    map_el = ET.Element("map", version="1.0.1")

    root_el = ET.SubElement(
        map_el,
        "node",
        {
            "ID": _uid(),
            "TEXT": f"Scene: {scene_name}",
            "CREATED": _ts(),
            "MODIFIED": _ts(),
        },
    )

    for rid in sorted(roots, key=node_sort_key):
        _emit_node(
            root_el,
            rid,
            nodes_by_id,
            children,
            visited=set(),
            is_root_child=True,
        )



    tree = ET.ElementTree(map_el)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _emit_node(
    parent_el,
    node_id,
    nodes_by_id,
    children,
    *,
    visited,
    is_root_child=False,
):
    # Cycle guard
    if node_id in visited:
        return
    visited.add(node_id)

    node = nodes_by_id[node_id]

    text = node.title
    if node.ops:
        text += "\n" + "\n".join(node.ops)

    attrs = {
        "ID": _uid(),
        "TEXT": text,
        "CREATED": _ts(),
        "MODIFIED": _ts(),
    }

    el = ET.SubElement(parent_el, "node", attrs)

    if is_root_child:
        el.set("POSITION", "right")

    color = _resolve_color(getattr(node, "color", None))
    if color:
        el.set("BACKGROUND_COLOR", color)

    for child_id in sorted(
        children.get(node_id, []),
        key=lambda i: nodes_by_id[i].index,
    ):
        _emit_node(
            el,
            child_id,
            nodes_by_id,
            children,
            visited=visited,
        )



def _resolve_color(color):
    if not isinstance(color, str):
        return None
    if color.startswith("#"):
        return color
    return FREEMIND_PALETTE.get(color)


def _uid():
    return uuid.uuid4().hex


def _ts():
    return str(int(time.time() * 1000))
