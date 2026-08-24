import csv
from pathlib import Path

class SnapshotTableCSVExporter:
    def __init__(self, snapshots, out_dir="out"):
        """
        snapshots: list of snapshot dicts
        """
        self.snapshots = snapshots
        self.out_dir = Path(out_dir)

    def write(self, filename="snapshots_raw.csv"):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / filename

        # Collect all variable names
        vars_set = set()
        for snap in self.snapshots:
            vars_set.update(snap["vars"].keys())

        vars_sorted = sorted(vars_set)

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # Header
            writer.writerow(["scene", "tag", *vars_sorted])

            # Rows
            for snap in self.snapshots:
                row = [
                    snap["scene"],
                    snap["tag"],
                ]
                for v in vars_sorted:
                    row.append(snap["vars"].get(v, ""))
                writer.writerow(row)

        print(f"[snapshot_raw_csv] wrote {path}")
