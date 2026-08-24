# storygraph/layout/flow_utils.py

def is_backward_edge(e, nodes_by_id):
    src = nodes_by_id.get(e.from_id)
    dst = nodes_by_id.get(e.to_id)

    if not src or not dst:
        return False

    # different scene → forward
    if src.scene != dst.scene:
        return False

    # earlier in file → backward
    return dst.index <= src.index


def is_forward_edge(e, nodes_by_id):
    src = nodes_by_id.get(e.from_id)
    dst = nodes_by_id.get(e.to_id)
    if not src or not dst: return True

    # If it's a structural tether, it's ALWAYS forward
    if getattr(e, "edge_type", None) == "structure" or getattr(e, "kind", None) == "structure":
        return True

    if src.scene != dst.scene: return True
    return dst.index > src.index