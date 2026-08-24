# tools/snapshot_json.py
import json
from pathlib import Path


class SnapshotJSONExporter:
    def __init__(self, stats, out_dir="out", filename="snapshot_stats.json"):
        """
        stats:
            dict[(scene, tag)] -> dict[var -> stats]
        """
        self.stats = stats
        self.out_dir = Path(out_dir)
        self.filename = filename


    def write(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / self.filename

        out = {}

        for (scene, tag), tag_stats in self.stats.items():
            key = f"{scene}:{tag}"

            out[key] = {
                "snapshots": tag_stats["snapshots"],
                "rate": tag_stats["rate"],
                "variables": tag_stats["variables"],
            }

        with path.open("w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

        print(f"[snapshot_json] wrote {path}")
