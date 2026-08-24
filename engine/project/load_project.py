# engine/project/load_project.py

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from game_loader import load_game

# This trick allows IDEs to see the type for autocomplete 
# without causing a circular import crash at runtime.
if TYPE_CHECKING:
    from engine.runtime.engine import PickEngine

@dataclass
class ProjectContext:
    scenes: dict
    # Use a string here to avoid NameError
    runtime: 'PickEngine' 
    project: Any
    diagnostics: dict
    tool_config: Optional[dict] = None

def load_project(
    *,
    scenes_config: str,
    validate: bool = True,
) -> ProjectContext:
    """
    Canonical entry point for loading a Pick-a-Path project.

    Responsibilities:
    - Load scenes
    - Run structural validation (optional)
    - Collect diagnostics
    """

    runtime, project = load_game(scenes_config, validate=validate)

    # print("RUNTIME    ", dir(runtime))

    val_results = getattr(project, "validation", {}) or {}

    diagnostics = {
        "errors": val_results.get("errors", []),
        "warnings": val_results.get("warnings", []),
    }

    return ProjectContext(
        scenes=runtime.scenes,
        runtime=runtime,
        project=project,
        diagnostics=diagnostics,
    )


































# # engine/project/load_project.py

# from dataclasses import dataclass
# from typing import Any, Optional

# from game_loader import load_game


# @dataclass
# class ProjectContext:
#     scenes: dict
#     # runtime: Any
#     runtime: PickEngine
#     project: Any
#     diagnostics: dict
#     tool_config: Optional[dict] = None


# def load_project(
#     *,
#     scenes_config: str,
#     validate: bool = True,
# ) -> ProjectContext:
#     """
#     Canonical entry point for loading a Pick-a-Path project.

#     Responsibilities:
#     - Load scenes
#     - Run structural validation (optional)
#     - Collect diagnostics
#     """

#     runtime, project = load_game(scenes_config, validate=validate)

#     diagnostics = {
#         "errors": project.validation.get("errors", []),
#         "warnings": project.validation.get("warnings", []),
#     }

#     return ProjectContext(
#         scenes=runtime.scenes,
#         runtime=runtime,
#         project=project,
#         diagnostics=diagnostics,
#     )
