"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# storygraph/layout/flow_layout.py

from dataclasses import dataclass
from collections import defaultdict, deque
from storygraph.layout.flow_columns import assign_flow_columns
from storygraph.layout.flow_height import compute_flow_heights


DEFAULT_COL_WIDTH = 520
DEFAULT_MIN_COL_WIDTH = 140  # minimum visual column width
DEFAULT_VERT_GAP = 50
DEFAULT_COL_PAD = 90         # gap between columns after compaction
DEFAULT_BUNDLE_GAP = 400

@dataclass(frozen=True)
class LayoutBox:
    x: int
    y: int
    width: int
    height: int


def _build_forward_tree(nodes, edges):
    nodes_by_id = {n.id: n for n in nodes}
    children = defaultdict(list)
    parent = {}

    for e in edges:
        src = nodes_by_id.get(e.from_id)
        dst = nodes_by_id.get(e.to_id)

        if not src or not dst:
            continue

        # Structure edges are always forward.
        # This keeps -if and Option nodes tethered to their parents.
        is_structure = getattr(e, "kind", "") == "structure"
        
        # Original logic + structure override
        if is_structure or src.scene != dst.scene or dst.index > src.index:
            if dst.id not in parent:
                parent[dst.id] = src.id
                children[src.id].append(dst.id)

    return children, parent


def _find_roots(nodes, parent):
    roots = [n.id for n in nodes if n.id not in parent]

    if not roots and nodes:
        roots = [min(nodes, key=lambda n: n.index).id]

    return roots


def _compute_depths(roots, children):
    depth = {}
    queue = deque()

    for r in roots:
        depth[r] = 0
        queue.append(r)

    while queue:
        cur = queue.popleft()
        for nxt in children.get(cur, []):
            depth[nxt] = depth[cur] + 1
            queue.append(nxt)

    return depth


##########                      SORTED SYMMETRIC SHOVE                   #############

def compute_flow_layout(nodes, edges, *, center_columns=False, config=None):

    layout_cfg = config.get("layout", {}) if config else {}
    
    col_width = layout_cfg.get("col_width", DEFAULT_COL_WIDTH)
    min_col_width = layout_cfg.get("min_col_width", DEFAULT_MIN_COL_WIDTH)
    vert_gap = layout_cfg.get("vert_gap", DEFAULT_VERT_GAP)
    col_pad = layout_cfg.get("col_pad", DEFAULT_COL_PAD)
    bundle_gap = layout_cfg.get("bundle_gap", DEFAULT_BUNDLE_GAP)

    nodes_by_id = {n.id: n for n in nodes}
    children_map, parent_map = _build_forward_tree(nodes, edges)
    roots = _find_roots(nodes, parent_map)
    
    col_map = _compute_depths(roots, children_map)
    col_map = _enforce_merge_columns(col_map, nodes, edges, col_width)
    col_map = _propagate_forward_columns(col_map, nodes, edges)

    incoming = defaultdict(list)
    for e in edges: incoming[e.to_id].append(e.from_id)

    layout = {}
    max_col = max(col_map.values()) if col_map else 0
    
    for c in range(max_col + 1):
        col_nodes = [n for n in nodes if col_map.get(n.id) == c]
        
        # REFINED TOPOLOGICAL SORT
        # Prioritize the average parent Y to keep the 'bundle' centered, 
        # but use node.index as the primary tie-breaker for siblings.
        def topo_sort_key(n):
            parents = [p for p in incoming[n.id] if p in layout]
            if not parents:
                return (0, n.index) # Roots sorted by file order
            
            # The 'Center of Gravity' of the parents
            avg_p_y = sum(layout[p].y + layout[p].height // 2 for p in parents) / len(parents)
            
            # Use a large multiplier for Y to keep bundles separate, 
            # but the n.index ensures 0, 1, 2, 3 stays in order within that bundle.
            return (avg_p_y, n.index)

        col_nodes.sort(key=topo_sort_key)

        # 1. Initial Placement (Center of Parents)
        for n in col_nodes:
            parents = [p for p in incoming[n.id] if p in layout]
            if not parents:
                ideal_y = _get_nearby_y_from_placed(n, layout, nodes_by_id, bundle_gap)
            else:
                ideal_y = sum(layout[p].y + (layout[p].height // 2) for p in parents) / len(parents)
            
            layout[n.id] = LayoutBox(
                x=c * col_width,
                y=int(ideal_y - (n.height // 2)),
                width=n.width,
                height=n.height
            )

        # 2. Resolve Collisions Symmetrically (Keeps the 'Fan Out')
        _resolve_column_symmetrically(col_nodes, layout, incoming, vert_gap, bundle_gap)
        
        # 3. Final Safety Shove (Guarantee the sorted order is physically maintained)
        _force_hard_shove(col_nodes, layout, incoming, vert_gap, bundle_gap)
        
        # 4. Anti-Drift Alignment
        _align_column_to_parents(col_nodes, layout, incoming)

    return _compact_columns(
        layout, 
        center_columns=center_columns, 
        col_width=col_width, 
        min_col_width=min_col_width, 
        col_pad=col_pad
    )

# --- Helper functions (Slightly tuned for stability) ---

def _resolve_column_symmetrically(sorted_nodes, layout, incoming, vert_gap, bundle_gap):
    nids = [n.id for n in sorted_nodes]
    for _ in range(10): # Increased iterations for complex choice blocks
        changed = False
        for i in range(len(nids) - 1):
            nid_a, nid_b = nids[i], nids[i+1]
            box_a, box_b = layout[nid_a], layout[nid_b]
            gap = vert_gap if (set(incoming[nid_a]) & set(incoming[nid_b])) else bundle_gap
            
            overlap = (box_a.y + box_a.height + gap) - box_b.y
            if overlap > 0:
                shift = overlap / 2
                layout[nid_a] = LayoutBox(box_a.x, int(box_a.y - shift), box_a.width, box_a.height)
                layout[nid_b] = LayoutBox(box_b.x, int(box_b.y + shift), box_b.width, box_b.height)
                changed = True
        if not changed: break


def _force_hard_shove(sorted_nodes, layout, incoming, vert_gap, bundle_gap):
    """Ensures the topological order is physically enforced."""
    nids = [n.id for n in sorted_nodes]
    for i in range(1, len(nids)):
        prev_id, curr_id = nids[i-1], nids[i]
        box_a, box_b = layout[prev_id], layout[curr_id]
        gap = vert_gap if (set(incoming[prev_id]) & set(incoming[curr_id])) else bundle_gap
        
        min_y = box_a.y + box_a.height + gap
        if box_b.y < min_y:
            layout[curr_id] = LayoutBox(box_b.x, int(min_y), box_b.width, box_b.height)


def _align_column_to_parents(sorted_nodes, layout, incoming):
    if not sorted_nodes: return
    nids = [n.id for n in sorted_nodes]
    curr_center = sum(layout[n].y + layout[n].height // 2 for n in nids) / len(nids)
    parent_ys = [layout[p].y + layout[p].height // 2 for n in nids for p in incoming[n] if p in layout]
    
    if parent_ys:
        target_center = sum(parent_ys) / len(parent_ys)
        shift = target_center - curr_center
        for nid in nids:
            box = layout[nid]
            layout[nid] = LayoutBox(box.x, int(box.y + shift), box.width, box.height)


def _get_nearby_y_from_placed(node, layout, nodes_by_id, bundle_gap):
    # Find nodes in the same scene already placed
    same_scene = [layout[nid].y + layout[nid].height for nid, n in nodes_by_id.items() 
                  if n.scene == node.scene and nid in layout]

    if same_scene:
        return max(same_scene) + bundle_gap
    
    # If it's a totally orphaned node, put it below the last placed node in the whole layout
    if layout:
        return max(box.y + box.height for box in layout.values()) + bundle_gap
    
    return 0

##########                      SORTED SYMMETRIC SHOVE                   #############


def _is_forward_edge(e, nodes_by_id):
    src = nodes_by_id.get(e.from_id)
    dst = nodes_by_id.get(e.to_id)

    if not src or not dst:
        return False

    if getattr(e, "kind", "") == "structure":
        return True

    # 1. Different scene is always forward
    if src.scene != dst.scene:
        return True

    # 2. Synthetic Node Logic:
    # Use .id because DiagramNode doesn't have a .tag attribute.
    # We compare the destination's 'origin_tag' metadata to the source's ID.
    src_id = getattr(src, "id", None)
    origin_of_dst = getattr(dst, "meta", {}).get("origin_tag")
    
    if origin_of_dst and origin_of_dst == src_id:
        return True

    # 3. Fallback to index
    src_idx = getattr(src, "index", 0)
    dst_idx = getattr(dst, "index", 0)
    
    # If indices are equal (common for synthetic nodes), 
    # treat it as forward if it's a resume node
    if src_idx == dst_idx and getattr(dst, "meta", {}).get("auto_resume"):
        return True

    return dst_idx > src_idx


def _adjust_merge_nodes(layout, nodes, edges, col_width):
    nodes_by_id = {n.id: n for n in nodes}

    # collect forward incoming edges only
    incoming_forward = defaultdict(list)
    for e in edges:
        if _is_forward_edge(e, nodes_by_id):
            incoming_forward[e.to_id].append(e.from_id)

    for node_id, parents in incoming_forward.items():
        if len(parents) < 2:
            continue  # not a merge

        # ignore parents not placed (safety)
        parents = [p for p in parents if p in layout]
        if len(parents) < 2:
            continue

        parent_xs = [layout[p].x for p in parents]
        min_x = min(parent_xs)
        max_x = max(parent_xs)

        # --- rules ---
        # 1. always move forward at least one column
        base_min_x = max_x + col_width

        # 2. span-based extra push
        span = max_x - min_x
        extra_steps = span // (2 * col_width)
        target_x = base_min_x + extra_steps * col_width

        box = layout.get(node_id)
        if not box:
            continue

        if target_x > box.x:
            layout[node_id] = LayoutBox(
                x=target_x,
                y=box.y,
                width=box.width,
                height=box.height,
            )

def _enforce_merge_columns(col, nodes, edges, col_width):
    nodes_by_id = {n.id: n for n in nodes}

    incoming = defaultdict(list)
    for e in edges:
        if _is_forward_edge(e, nodes_by_id):
            incoming[e.to_id].append(e.from_id)

    changed = True
    MAX_PASSES = len(nodes)

    for _ in range(MAX_PASSES):
        if not changed:
            break
        changed = False

        for node_id, parents in incoming.items():
            if len(parents) < 2:
                continue

            parent_cols = [col[p] for p in parents if p in col]
            if len(parent_cols) < 2:
                continue

            min_parent = min(parent_cols)
            max_parent = max(parent_cols)

            # ALWAYS consume a fresh column for merges
            target = max_parent + 2

            # extra spacing if parents are wide
            span = max_parent - min_parent
            target += span // 2

            if col.get(node_id, 0) < target:
                col[node_id] = target
                changed = True

    return col


def _propagate_forward_columns(col, nodes, edges):
    nodes_by_id = {n.id: n for n in nodes}

    forward_children = defaultdict(list)
    for e in edges:
        if _is_forward_edge(e, nodes_by_id):
            forward_children[e.from_id].append(e.to_id)

    changed = True
    MAX_PASSES = len(nodes)

    for _ in range(MAX_PASSES):
        if not changed:
            break
        changed = False

        for src, children in forward_children.items():
            if src not in col:
                continue

            for dst in children:
                min_col = col[src] + 1
                if col.get(dst, 0) < min_col:
                    col[dst] = min_col
                    changed = True

    return col


def _compact_columns(layout, *, center_columns, col_width, min_col_width, col_pad):
    """
    Post-layout visual compaction.
    Shrinks columns to wrap their widest card.
    Optionally centers cards inside their column.
    """

    # 1. group nodes by column index
    cols = defaultdict(list)
    for node_id, box in layout.items():
        col = box.x // col_width
        cols[col].append((node_id, box))

    # 2. compute required width per column
    col_widths = {}
    for col, items in cols.items():
        max_w = max(box.width for _, box in items)
        col_widths[col] = max(min_col_width, max_w)

    # 3. compute compacted x offsets
    col_offsets = {}
    cursor_x = 0
    for col in sorted(col_widths):
        col_offsets[col] = cursor_x
        cursor_x += col_widths[col] + col_pad

    # 4. apply offsets + optional centering
    new_layout = {}
    for node_id, box in layout.items():
        col = box.x // col_width
        col_x = col_offsets[col]
        col_w = col_widths[col]

        if center_columns:
            dx = (col_w - box.width) // 2
        else:
            dx = 0

        new_layout[node_id] = LayoutBox(
            x=col_x + dx,
            y=box.y,
            width=box.width,
            height=box.height,
        )

    return new_layout


