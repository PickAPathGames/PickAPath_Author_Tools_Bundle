# tools/snapshot_csv.py
import csv
from pathlib import Path


class SnapshotCSVExporter:
    def __init__(self, stats, out_dir="out"):
        """
        stats:
            dict[(scene, tag)] -> {
                "variables": {...},
                "snapshots": int,
                "rate": float
            }
        """
        self.stats = stats or {}
        self.out_dir = Path(out_dir)

    # --------------------------------------------------

    def write_all(self):
        if not self.stats:
            print("[snapshot_csv] no snapshot stats to export")
            return

        for (scene, tag), tag_stats in self.stats.items():
            self._write_one(scene, tag, tag_stats)

    # --------------------------------------------------

    def _write_one(self, scene, tag, tag_stats):
        if "variables" not in tag_stats:
            print(f"[snapshot_csv] skipping {scene}:{tag} (no variables)")
            return

        path = self.out_dir / f"snapshot_{scene}_{tag}.csv"

        variables = tag_stats["variables"]
        snapshots = tag_stats.get("snapshots", 0)
        rate = tag_stats.get("rate", 0.0)

        with path.open("w", encoding="utf-8") as f:
            f.write("variable,count,missing,min,max,mean,snapshots,rate\n")

            for var, s in variables.items():
                f.write(
                    f"{var},"
                    f"{s.get('count', 0)},"
                    f"{s.get('missing', 0)},"
                    f"{s.get('min')},"
                    f"{s.get('max')},"
                    f"{s.get('mean')},"
                    f"{snapshots},"
                    f"{rate}\n"
                )

        print(f"[snapshot_csv] wrote {path}")

