"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/tools/pickstats.py

from engine.project.load_project import load_project
from tools.pickrandom_v2 import PickRandomRunner, load_pickrandom_config
from tools.snapshot_stats import SnapshotStats
from tools.coverage_stats import CoverageStats
from tools.coverage_csv import CoverageCSVExporter
from tools.snapshot_csv import SnapshotCSVExporter
from tools.snapshot_json import SnapshotJSONExporter

def _fmt(diag):
    return diag.format_console() if hasattr(diag, "format_console") else str(diag)

def main(
    *,
    scenes_config: str = "scenes/config.txt",
    config_path: str = "tools/pickrandom_configuration.json",
    out_dir: str = "out",
    quiet: bool = False,
) -> dict:
    """
    Run PickRandom + statistics exporters.

    Returns a structured diagnostics dict suitable for:
    - CLI
    - VS Code extension
    - CI / automation
    """

    result = {
        "tool": "pickstats",
        "status": "ok",
        "errors": [],
        "warnings": [],
        "artifacts": {},
    }

    # ─────────────────────────────────────────────
    # Load project (single entry point)
    # ─────────────────────────────────────────────
    ctx = load_project(
        scenes_config=scenes_config,
        validate=True,
    )

    project = ctx.project
    runtime = ctx.runtime

    # Collect diagnostics
    result["errors"] = [_fmt(e) for e in ctx.diagnostics.get("errors", [])]
    result["warnings"] = [_fmt(w) for w in ctx.diagnostics.get("warnings", [])]

    if result["errors"]:
        result["status"] = "error"

        if not quiet:
            print("ERROR: Project is not structurally sound.")
            for e in result["errors"]:
                print(" -", e)

        return result

    if result["warnings"] and not quiet:
        print("Warnings:")
        for w in result["warnings"]:
            print(" -", w)

    # ─────────────────────────────────────────────
    # Load PickRandom config
    # ─────────────────────────────────────────────
    config = load_pickrandom_config(config_path)

    # ─────────────────────────────────────────────
    # Run PickRandom
    # ─────────────────────────────────────────────
    picker = PickRandomRunner(
        scenes=runtime.scenes,
        start_scene=project.start_scene,
        start_tag=project.start_tag,
        config=config,
        initial_variables=project.initial_vars,
        scene_order=getattr(project, "files", None),
    )

    run_result = picker.run()
    iteration_count = run_result["iteration_count"]

    result["artifacts"]["iterations"] = iteration_count

    iteration_count = run_result.get("iteration_count", 0)
    visited_count = len(run_result.get("visited_nodes", {}))

    if not quiet:
        print(f"DEBUG: Iterations: {iteration_count}")
        print(f"DEBUG: Visited Nodes: {visited_count}")


    # ─────────────────────────────────────────────
    # Snapshot statistics
    # ─────────────────────────────────────────────
    if config.get("snapshots", {}).get("enabled", True):
        snapshot_stats = SnapshotStats(
            run_result["snapshots"],
            iteration_count=iteration_count,
        )

        snapshot_result = snapshot_stats.compute()

        SnapshotJSONExporter(snapshot_result, out_dir=out_dir).write()
        SnapshotCSVExporter(snapshot_result, out_dir=out_dir).write_all()

        result["artifacts"]["snapshots"] = {
            "json": True,
            "csv": True,
        }

    # ─────────────────────────────────────────────
    # Coverage statistics
    # ─────────────────────────────────────────────
    coverage_cfg = config.get("coverage", {})

    if coverage_cfg.get("enabled", True):
        coverage_stats = CoverageStats(
            run_result["visited_nodes"],
            iteration_count=iteration_count,
            scenes=runtime.scenes,
        )

        node_rows = coverage_stats.compute()

        branch_rows = []
        if coverage_cfg.get("export_branch_coverage", False):
            branch_cov = run_result.get("branch_coverage")
            if branch_cov:
                branch_rows = branch_cov.compute(iteration_count)

        CoverageCSVExporter(
            node_rows=node_rows,
            branch_rows=branch_rows,
            out_dir=out_dir,
        ).write()

        result["artifacts"]["coverage"] = {
            "nodes": True,
            "branches": bool(branch_rows),
        }

    if not quiet:
        print("DONE")

    return result


if __name__ == "__main__":
    from engine.tools.cli_utils import standard_parser, run_cli

    parser = standard_parser("pickstats")
    run_cli(parser=parser, runner=main)

