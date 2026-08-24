# engine/tools/validate.py

from collections import defaultdict
from typing import Dict, List

from engine.project.load_project import load_project
from engine.diagnostics import Diagnostic


def _group_diagnostics(
    diagnostics: List[Diagnostic],
) -> Dict[str, List[dict]]:
    files: Dict[str, List[dict]] = defaultdict(list)

    for d in diagnostics:
        files[d.file].append(
            {
                "line": d.line,
                "column": d.column,
                "length": d.length,
                "severity": d.severity,
                "code": d.code,
                "message": d.message,
                "phase": d.phase,
            }
        )

    return dict(files)


def _serialize_diagnostic(d: Diagnostic) -> dict:
    return {
        "file": d.file,
        "tag": d.tag,
        "line": d.line,
        "column": d.column,
        "length": d.length,
        "severity": d.severity,
        "phase": d.phase,
        "code": d.code,
        "message": d.message,
    }


def main(
    *,
    scenes_config: str = "scenes/config.txt",
    quiet: bool = False,
    format: str = "console",
    **_,
) -> dict:
    if format in ("json", "ndjson"):
        quiet = True

    result = {
        "tool": "validate",
        "status": "ok",
        "errors": [],
        "warnings": [],
        "artifacts": {
            "files": {},
        },
    }

    ctx = load_project(
        scenes_config=scenes_config,
        validate=True,
    )

    errors: List[Diagnostic] = ctx.diagnostics.get("errors", [])
    warnings: List[Diagnostic] = ctx.diagnostics.get("warnings", [])

    for scene in ctx.scenes.values():
        if getattr(scene, "parser_errors", None):
            for err in scene.parser_errors:
                errors.append(
                    Diagnostic(
                        file=err.get("file"),
                        tag=None,
                        line=err.get("line", 0),
                        column=0,
                        length=None,
                        severity="error",
                        phase="parser",
                        code="PARSER_ERROR",
                        message=err.get("msg", "Parser error"),
                    )
                )


    if errors:
        result["status"] = "error"

    result["errors"] = [_serialize_diagnostic(e) for e in errors]
    result["warnings"] = [_serialize_diagnostic(w) for w in warnings]

    all_diags = errors + warnings
    result["artifacts"]["files"] = _group_diagnostics(all_diags)

    if not quiet:
        if errors:
            print("Errors:")
            for e in errors:
                print(" -", e.format_console())

        if warnings:
            print("Warnings:")
            for w in warnings:
                print(" -", w.format_console())

        if not errors and not warnings:
            print("No issues found.")

    return result


if __name__ == "__main__":
    from engine.tools.cli_utils import standard_parser, run_cli

    parser = standard_parser("validate")
    run_cli(parser=parser, runner=main)

