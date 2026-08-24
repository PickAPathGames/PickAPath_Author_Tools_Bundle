# tools/snapshot_stats.py
from collections import defaultdict
from statistics import mean

class SnapshotStats:
    def __init__(self, snapshots_by_tag, iteration_count):
        self.snapshots_by_tag = snapshots_by_tag
        self.iteration_count = iteration_count

    # --------------------------------------------------

    def compute(self):
        """
        Returns:
            dict[(scene, tag)] -> {
                "snapshots": int,
                "rate": float,
                "variables": dict[var -> stats]
            }
        """
        out = {}

        for (scene, tag), rows in self.snapshots_by_tag.items():
            stats = self._compute_for_tag(rows)

            out[(scene, tag)] = {
                "snapshots": len(rows),
                "rate": round(len(rows) / self.iteration_count, 6),
                "variables": stats,
            }

            print(
                f"[SNAPSHOT_STATS] ({scene}, {tag}) "
                f"snapshots={len(rows)} rate={len(rows)/self.iteration_count:.3f} "
                f"vars={list(stats.keys())}"
            )

        return out

    # --------------------------------------------------

    def _compute_for_tag(self, rows):
        values = defaultdict(list)
        missing = defaultdict(int)

        all_vars = set()
        for row in rows:
            all_vars.update(row.keys())

        for row in rows:
            for var in all_vars:
                if var not in row or row[var] is None:
                    missing[var] += 1
                else:
                    val = row[var]
                    if isinstance(val, (int, float)):
                        values[var].append(val)

        stats = {}

        for var in all_vars:
            vals = values.get(var, [])

            stats[var] = {
                "count": len(rows),
                "missing": missing.get(var, 0),
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
                "mean": round(mean(vals), 3) if vals else None,
            }

        return stats
