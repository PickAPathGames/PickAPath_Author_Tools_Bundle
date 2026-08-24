# tools/coverage_csv.py
import csv
from pathlib import Path


class CoverageCSVExporter:
    def __init__(self, node_rows, branch_rows=None, out_dir="out"):
        """
        node_rows:
            list[dict] with keys:
                kind, scene, tag, count, rate

        branch_rows:
            list[dict] with keys:
                kind, scene, tag, branch_type, branch_label, count, rate
        """
        self.node_rows = node_rows
        self.branch_rows = branch_rows or []
        self.out_dir = Path(out_dir)

    # --------------------------------------------------

    def write(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / "coverage.csv"

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow([
                "kind",
                "scene",
                "tag",
                "branch_type",
                "branch_label",
                "line",
                "count",
                "rate",
            ])


            # ---- NODE COVERAGE ----
            for row in self.node_rows:
                writer.writerow([
                    "node",
                    row["scene"],
                    row["tag"],
                    "",
                    "",
                    row.get("line", ""),
                    row["count"],
                    row["rate"],
                ])


            # ---- BRANCH COVERAGE ----
            for row in self.branch_rows:
                writer.writerow([
                    "branch",
                    row["scene"],
                    row["tag"],
                    row.get("branch_type", ""),
                    row.get("branch_label", ""),
                    row.get("line", ""),
                    row["count"],
                    row["rate"],
                ])


        print(f"[coverage_csv] wrote {path}")


























# # tools/coverage_csv.py
# import csv
# from pathlib import Path


# class CoverageCSVExporter:
#     def __init__(self, coverage, branch_coverage=None, out_dir="out"):
#         """
#         coverage:
#             dict[(scene, tag)] -> {count, rate}
#         branch_coverage:
#             dict[(scene, tag, branch_type, branch_label)] -> {count, rate}
#         """
#         self.node_coverage = coverage
#         self.branch_coverage = branch_coverage or {}
#         self.out_dir = Path(out_dir)


#     # --------------------------------------------------

#     def write(self):
#         self.out_dir.mkdir(parents=True, exist_ok=True)
#         path = self.out_dir / "coverage.csv"

#         with path.open("w", newline="", encoding="utf-8") as f:
#             writer = csv.writer(f)

#             writer.writerow([
#                 "kind",
#                 "scene",
#                 "tag",
#                 "branch_type",
#                 "branch_label",
#                 "count",
#                 "rate",
#             ])

#             # ---- NODE COVERAGE ----
#             print("NODE COVERAGE ", dir(self.node_coverage))
#             for (scene, tag), stats in sorted(self.node_coverage.items()):
#                 writer.writerow([
#                     "node",
#                     scene,
#                     tag,
#                     "",
#                     "",
#                     stats["count"],
#                     stats["rate"],
#                 ])

#             # ---- BRANCH COVERAGE ----
#             for row in self.branch_coverage:
#                 writer.writerow([
#                     "branch",
#                     row["scene"],
#                     row["tag"],
#                     row["branch_type"],
#                     row.get("branch_label", ""),
#                     row["count"],
#                     row["rate"],
#                 ])



#         print(f"[coverage_csv] wrote {path}")






















# # tools/coverage_csv.py
# import csv
# from pathlib import Path


# class CoverageCSVExporter:
#     def __init__(self, coverage, out_dir="out"):
#         """
#         coverage:
#             Counter[(scene, tag)] -> count
#         """
#         self.coverage = coverage
#         self.out_dir = Path(out_dir)

#     # --------------------------------------------------

#     def write(self):
#         self.out_dir.mkdir(parents=True, exist_ok=True)
#         path = self.out_dir / "coverage.csv"

#         with path.open("w", newline="", encoding="utf-8") as f:
#             writer = csv.writer(f)
#             writer.writerow(["scene", "tag", "visit_count"])

#             for (scene, tag) in sorted(self.coverage.keys()):
#                 writer.writerow([
#                     scene,
#                     tag,
#                     self.coverage[(scene, tag)],
#                 ])

#         print(f"[coverage_csv] wrote {path}")
