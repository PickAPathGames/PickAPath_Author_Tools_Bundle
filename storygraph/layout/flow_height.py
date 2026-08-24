# storygraph/layout/flow_height.py

from collections import defaultdict

NODE_HEIGHT = 120
V_GAP = 40

# def compute_flow_heights(nodes, edges):
def compute_flow_heights(nodes, edges, v_gap=40):
    nodes_by_id = {n.id: n for n in nodes}
    outgoing = defaultdict(list)
    for e in edges:
        outgoing[e.from_id].append(e.to_id)

    height = {}

    def dfs(nid, seen):
        if nid in seen:
            return NODE_HEIGHT  # loop → minimal footprint
        if nid in height:
            return height[nid]
        
        node = nodes_by_id.get(nid)
        this_node_h = node.height if node else 40 # Fallback

        seen.add(nid)
        children = outgoing.get(nid, [])

        if not children:
            # h = NODE_HEIGHT
            h = this_node_h
        else:
            # h = sum(dfs(c, seen.copy()) + V_GAP for c in children) - V_GAP
            h = sum(dfs(c, seen.copy()) + v_gap for c in children) - v_gap

        # height[nid] = max(h, NODE_HEIGHT)
        height[nid] = max(h, this_node_h)
        return height[nid]

    for n in nodes:
        dfs(n.id, set())

    return height
