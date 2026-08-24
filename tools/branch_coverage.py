# tools/branch_coverage.py
from collections import defaultdict


class BranchCoverage:
    """
    Tracks branch executions in a normalized, export-ready format.

    Keyed by:
        (scene, tag, kind, label, line)
    """

    def __init__(self):
        self.counts = defaultdict(int)

    # --------------------------------------------------

    def record(
        self,
        scene,
        tag,
        kind,
        label=None,
        line=None,
    ):
        key = (
            scene,
            tag,
            kind,
            str(label) if label is not None else "",
            int(line) if line is not None else None,
        )
        label = self._normalize_label_and_line(label, line)

        self.counts[key] += 1


    # --------------------------------------------------

    def merge(self, other):
        for key, count in other.counts.items():
            self.counts[key] += count

    # --------------------------------------------------

    def compute(self, iteration_count):
        rows = []

        for (
            scene,
            tag,
            kind,
            label,
            line,
        ), count in self.counts.items():
            rows.append({
                "kind": "branch",
                "scene": scene,
                "tag": tag,
                "branch_type": kind,
                "branch_label": label,
                "line": line,
                "count": count,
                "rate": round(count / iteration_count, 6),
            })

        print("[DEBUG] Branch compute rows:", len(rows))
        return rows


    def _normalize_label(self, label):
        """
        Normalize branch labels into a short, human-readable string.
        """
        if label is None:
            return ""

        # Already clean
        if isinstance(label, str):
            return label

        # Choice blocks: list of AST nodes
        if isinstance(label, list):
            for item in label:
                if isinstance(item, dict) and item.get("type") == "choice_text":
                    return item.get("text", "")
            return ""

        # Fallback: make it safe & readable
        return str(label)


    def _normalize_label_and_line(self, branch_type, branch_label):
        """
        Returns:
            (label: str, line: int | None)
        """

        # ---- CHOICES ----
        if branch_type == "choice" and isinstance(branch_label, list):
            for token in branch_label:
                if token.get("type") == "choice_text":
                    return token.get("text", "").strip(), token.get("__line__")

            # fallback
            return "choice", None

        # ---- IF / ELSE ----
        if branch_type in ("if", "else"):
            if isinstance(branch_label, str):
                return branch_label, None
            return "", None

        # ---- FALLBACK ----
        if branch_label is None:
            return "", None

        return str(branch_label), None


