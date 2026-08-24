# coverage_stats.py
from collections import defaultdict


class CoverageStats:
    def __init__(self, visited_nodes, iteration_count, scenes=None):
        self.visited_nodes = visited_nodes
        self.iteration_count = iteration_count
        self.scenes = scenes

    # --------------------------------------------------

    def compute(self):
        """
        Returns:
            list[dict] with explicit node coverage rows
        """
        counts = defaultdict(int)

        if isinstance(self.visited_nodes, dict):
            for (scene, tag), count in self.visited_nodes.items():
                counts[(scene, tag)] += count
        else:
            for node in self.visited_nodes:
                scene, tag = self._normalize(node)
                counts[(scene, tag)] += 1

        rows = []

        for (scene, tag), count in counts.items():
            line = self._get_node_line(scene, tag)

            rows.append({
                "kind": "node",
                "scene": scene,
                "tag": tag,
                "branch_type": "",
                "branch_label": "",
                "line": line,
                "count": count,
                "rate": round(count / self.iteration_count, 6),
            })

        return rows


    # --------------------------------------------------

    def _normalize(self, node):
        if isinstance(node, tuple) and len(node) >= 2:
            return node[0], node[1]

        if hasattr(node, "chapter") and hasattr(node, "tag"):
            return node.chapter, node.tag

        raise ValueError(f"Unrecognized visited node format: {node}")


    # --------------------------------------------------

    def _get_node_line(self, scene, tag):
        if not self.scenes:
            return None

        sc = self.scenes.get(scene)
        if not sc:
            return None

        node = sc.nodes.get(tag)
        if not node:
            return None

        return getattr(node, "line", None)


