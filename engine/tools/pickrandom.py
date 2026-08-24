"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/tools/pickrandom.py

from engine.project.load_project import load_project
from tools.pickrandom_v2 import PickRandomRunner, load_pickrandom_config

def _fmt(diag):
    return diag.format_console() if hasattr(diag, "format_console") else str(diag)

def main(
    *,
    scenes_config: str = "scenes/config.txt",
    config_path: str = "tools/pickrandom_configuration.json",
    out_dir: str = "out",
    quiet: bool = False,
) -> dict:
    result = {
        "tool": "pickrandom",
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

    # 2. Extract errors from the runner
    runtime_errors = run_result.get("errors", [])
    result["errors"].extend(runtime_errors)

    # 3. Print the Summary Report
    if not quiet:
        if result["errors"]:
            print("\n" + "!" * 45)
            print(f"  CRITICAL: {len(result['errors'])} Runtime Issues Found")
            print("!" * 45)
            for e in result["errors"]:
                print(f" - {e}")
            result["status"] = "error"
        else:
            print("\n" + "+" + "-" * 43 + "+")
            print("|        Simulation completed successfully   |")
            print(f"|  Passed {run_result['iteration_count']} iterations with 0 errors! |")
            print("+" + "-" * 43 + "+")

    return result


if __name__ == "__main__":
    from engine.tools.cli_utils import standard_parser, run_cli

    parser = standard_parser("pickrandom")
    run_cli(parser=parser, runner=main)

