"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/tools/pickquick.py

import os
import sys
from engine.project.load_project import load_project
from validator_runtime import ValidatorRuntime
from validators.validator_flow import FlowError # Import the error to intercept it

def main(
    *,
    scenes_config: str = "scenes/config.txt",
    quiet: bool = False,
    **_,
) -> dict:
    result = {
        "tool": "pickquick",
        "status": "ok",
        "errors": [],
        "warnings": [],
        "artifacts": {},
    }

    # CRASH PROTECTION SHIELD: Keep tool alive even if game structure collapses
    try:
        ctx = load_project(
            scenes_config=scenes_config,
            validate=True,
        )
        project = ctx.project
        scenes = ctx.scenes

        config = {
            "start_scene": project.start_scene,
            "start_tag": project.start_tag,
            "meta": getattr(project, "meta", {}),
            "story_order": getattr(project, "files", []),
        }

        validator = ValidatorRuntime(scenes, config)
        validator.validate(project.start_scene, project.start_tag)
        validator.quickpick(project.start_scene, project.start_tag)

        # --- STATS VALIDATION ---
        if "__stats__" in scenes:
            stats_scene = scenes["__stats__"]
            if "main_page" not in stats_scene.nodes:
                result["errors"].append("[__stats__] CRITICAL: stats.txt must start with '-tag main_page'")
            else:
                try:
                    validator.validate("__stats__", "main_page")
                    validator.quickpick("__stats__", "main_page")
                except FlowError as fe:
                    result["errors"].append(f"[__stats__] Flow Error: {str(fe)}")
                except Exception as err:
                    result["errors"].append(f"[__stats__] Validation Error: {str(err)}")
        # -----------------------------

        # Gather diagnostics safely
        result["errors"] = [_format_console(e) for e in ctx.diagnostics.get("errors", [])]
        result["warnings"] = [_format_console(w) for w in ctx.diagnostics.get("warnings", [])]

    except FlowError as fe:
        # Capture the structural story breakdown cleanly without a full crash dump
        result["errors"].append(f"Flow Error: {str(fe)}")
    except Exception as general_err:
        # Fallback security catch-all
        result["errors"].append(f"Critical Compilation Interrupted: {str(general_err)}")

    # Add any sub-errors found inside the scenes collection records
    if 'scenes' in locals():
        for s_name, s_obj in scenes.items():
            if hasattr(s_obj, 'errors') and s_obj.errors:
                for err in s_obj.errors:
                    msg = err.get('message') if isinstance(err, dict) else str(err)
                    result["errors"].append(f"[{s_name}] {msg}")

    if result["errors"]:
        result["status"] = "error"

    if not quiet:
        if result["errors"]:
            print("\n❌ Structural issues found in script structure:")
            for e in result["errors"]:
                print(" -", e)

        if result["warnings"]:
            print("\n⚠️ Warnings:")
            for w in result["warnings"]:
                print(" -", w)
        
        if not result["errors"] and not result["warnings"]:
            print("+" + "-"*43 + "+")
            print("|        Static validation completed        |")
            print("|  No structural errors and warnings found  |")
            print("+" + "-"*43 + "+")

    return result

def _format_console(diag):
    if hasattr(diag, "format_console"):
        return diag.format_console()
    return str(diag)


if __name__ == "__main__":
    from engine.tools.cli_utils import standard_parser, run_cli

    parser = standard_parser("pickquick")
    run_cli(parser=parser, runner=main)

