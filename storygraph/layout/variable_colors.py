from typing import List, Dict, Any
from storygraph.diagram.diagram_nodes import DiagramNode


def apply_variable_colors(
    nodes: List[DiagramNode],
    variable_rules: List[Dict[str, Any]],
):
    """
    Mutates nodes in-place.
    First matching rule wins.
    """

    if not variable_rules:
        return

    for node in nodes:
        _apply_node_color(node, variable_rules)


def _apply_node_color(node: DiagramNode, rules):
    if not node.ops:
        return

    for rule in rules:
        if _rule_matches_node(rule, node):
            node.color = rule.get("color")
            return  # 🔑 first-match-wins


def _rule_matches_node(rule, node: DiagramNode) -> bool:
    var = rule.get("var")
    ops = rule.get("ops")

    for op in node.ops:
        if var and var not in op:
            continue

        if ops:
            if not any(op.startswith(f"-{o}") or f" {o}" in op for o in ops):
                continue

        return True

    return False
