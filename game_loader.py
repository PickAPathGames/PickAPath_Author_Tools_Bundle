# game_loader.py

import os
from typing import Tuple
import copy

# External validators and parsers
from validators.config_validator import load_and_validate
from parser.loader import parse_scene_file

# NEW: Import the modern engine
from engine.runtime.engine import PickEngine
from models.game_project import GameProject
from validator_runtime import ValidatorRuntime

def load_game(config_path: str = "scenes/config.txt", validate: bool = True) -> Tuple[PickEngine, GameProject]:
    """
    Loads project files and returns the modern PickEngine + Project Data.
    """
    config_path = os.path.abspath(config_path)
    project_root = os.path.dirname(config_path)

    # 1. Load config.txt
    cfg, errors, warnings = load_and_validate(config_path)
    if errors:
        error_report = "\n".join(errors)
        print(f"CONFIG ERRORS:\n{error_report}")
        raise RuntimeError(f"Game Configuration Failed:\n{error_report}")

    # 2. Parse scenes
    scenes = {}
    first_tag_of = {}
    for basename in cfg.files:
        # 1. Flexible File Discovery
        # Check in order: exact name -> .pap -> .txt
        possible_filenames = [
            basename,
            f"{basename}.pap",
            f"{basename}.txt"
        ]
        
        scene_file = None
        found_filename = None
        
        for p_name in possible_filenames:
            full_path = os.path.join(project_root, p_name)
            if os.path.exists(full_path):
                scene_file = full_path
                found_filename = p_name
                break
        
        if not scene_file:
            print(f"[DEBUG] CRITICAL: Missing scene file for entry '{basename}'")
            print(f"Checked: {possible_filenames}")
            raise FileNotFoundError(f"Missing scene file: {basename} (checked .pap and .txt)")

        # 2. Parse the file using the discovered path
        # We use basename (the name from config) as the chapter key
        parsed_scene = parse_scene_file(
            scene_file, 
            chapter_name=basename, 
            indent_size=cfg.meta.get("indent", 2)
        )

        # 3. Assign metadata
        parsed_scene.name = basename
        scenes[basename] = parsed_scene
        
        # Track first tag for normalization
        nodes_map = getattr(parsed_scene, "nodes", {})
        if nodes_map:
            first_tag = min(nodes_map.values(), key=lambda n: getattr(n, "line", 0)).tag
            first_tag_of[basename] = first_tag

    # After parsing regular scenes...
    stats_file = os.path.join(project_root, "stats.txt")
    if os.path.exists(stats_file):
        parsed_stats = parse_scene_file(stats_file, chapter_name="__stats__")
        scenes["__stats__"] = parsed_stats

    # 3. Normalize Links
    from parser.loader import normalize_scene_links_and_vars as _normalizer
    for name, scene in scenes.items():
        _normalizer(scene, first_tag_of=first_tag_of, files_order=list(cfg.files))

    # 4. Determine Start
    start_scene = cfg.files[0]
    start_tag = first_tag_of.get(start_scene)

    # 5. Build GameProject (The Data Container)
    # ADD: Explicitly inject map_mode into the project's initial variables
    initial_vars = dict(cfg.vars)
    initial_vars["map_visibility"] = cfg.meta.get("map_mode", "visited")
    initial_vars["map_style"] = cfg.meta.get("map_style", "nodes")
    project_map_exclude = getattr(cfg, "map_exclude", set())

    project = GameProject(
        title=cfg.meta.get("title", "Untitled Project"),
        author=cfg.meta.get("author", "Unknown"),
        files=list(cfg.files),
        variables=initial_vars,
        initial_vars=initial_vars,
        save_vars=list(cfg.save_vars),
        goals=dict(cfg.goals),
        scenes=scenes,
        start_scene=start_scene,
        start_tag=start_tag,
        indent=cfg.meta.get("indent", 2),
        meta=cfg.meta,
        map_exclude=project_map_exclude
    )

    # 6. Build MODERN PickEngine
    engine = PickEngine(scenes, story_order=list(cfg.files))
    engine.first_tag_of = first_tag_of

    # Sync the engine's internal state immediately
    engine.state["vars"] = copy.deepcopy(initial_vars)

    # APPLY PERMANENT STATS FROM CONFIG
    engine.state["ui_grid"] = [None] * 4
    for stat_line in cfg.meta.get("permanent_stats", []):
        # We can reuse your existing runtime logic here
        # from commands.system import r_perm_stat
        from commands.stats import r_perm_stat
        r_perm_stat(engine, stat_line, None)

    # 7. Run Validation
    if validate:
        config_mapping = {
            "meta": cfg.meta, "vars": cfg.vars, 
            "save_vars": cfg.save_vars, "goals": cfg.goals, "files": cfg.files
        }
        validator = ValidatorRuntime(scenes, config_mapping)
        validation_result = validator.validate(start_scene, start_tag)
        project.validation = validation_result
        # The modern engine can use the graph_index for smarter navigation
        engine.graph_index = validator.graph_index

    return engine, project

