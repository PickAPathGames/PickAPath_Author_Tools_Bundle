# validators/config_validator.py
"""
Config validator for PickQuick-style projects.

Provides:
  - validate_config(config, project_root) -> (errors:list, warnings:list)
  - load_and_validate(config_path) -> (config_or_None, errors:list, warnings:list)

This file is intended to be a self-contained compatibility layer for older
callers (game_loader.py) that expect load_and_validate to exist.
"""

import os
from typing import Tuple, List, Any
from engine.constants import SCENE_EXTENSIONS
from parser.constants import _VARNAME_RE
from parser.config_parser import ConfigParser
from config_exceptions import ConfigSyntaxError


# -------------------------
# Validation helpers
# -------------------------


def _is_valid_var_name(name: str) -> bool:
    return bool(_VARNAME_RE.match(name))


def _exists_scene_file(project_root: str, basename: str) -> bool:
    # Look directly in the project_root (which should be the game folder)
    for ext in SCENE_EXTENSIONS:
        path = os.path.join(project_root, basename + ext)
        if os.path.isfile(path):
            return True
    return False

# -------------------------
# Core validation logic
# -------------------------
def load_and_validate_config(path: str):
    parser = ConfigParser(path)
    config = parser.parse()

    # Structural validations for the NEW config format
    
    # 1. Must define -files with at least one item
    if not config.files:
        raise ConfigSyntaxError(
            "Config must define a -files block with at least one file entry."
        )

    # 2. Must define game metadata (title, author)
    if "title" not in config.meta:
        raise ConfigSyntaxError("Config must define a -title entry.")

    if "author" not in config.meta:
        raise ConfigSyntaxError("Config must define an -author entry.")

    # 3. Optionally check goals:
    # (only if you want to enforce structure)
    for name, goal in config.goals.items():
        if not goal.attributes:
            raise ConfigSyntaxError(
                f"Goal '{name}' is defined but has no attributes."
            )

    return config


def validate_config(config: Any, project_root: str) -> Tuple[List[str], List[str]]:
    """
    Validate a parsed Config object.

    Returns (errors, warnings) lists of strings.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # Basic shape guards
    meta = getattr(config, "meta", {}) or {}
    files = getattr(config, "files", []) or []
    vars_dict = getattr(config, "vars", {}) or {}
    save_vars = getattr(config, "save_vars", []) or []
    goals = getattr(config, "goals", {}) or {}
    map_exclude = getattr(config, "map_exclude", set())

    # ---- Meta checks ----
    title = meta.get("title", "") if isinstance(meta.get("title", ""), str) else meta.get("title")
    author = meta.get("author", "") if isinstance(meta.get("author", ""), str) else meta.get("author")

    if not title or (isinstance(title, str) and not title.strip()):
        errors.append("[config] Missing or empty -title in config.")
    if not author:
        warnings.append("[config] Missing -author in config (optional but recommended).")

    # Unknown meta keys
    # allowed_meta = {"title", "author", "version", "indent"}
    allowed_meta = {
        "title", "author", "version", "indent", 
        "author_mode", "map_mode", "reach_goals, permanent_stat"
    }
    for k in meta.keys():
        if k not in allowed_meta:
            warnings.append(f"[config] Unknown meta key '-{k}' in config.meta (allowed: {sorted(list(allowed_meta))}).")

    # indent validity (if present)
    if "indent" in meta:
        try:
            iv = int(meta["indent"])
            if iv <= 0:
                errors.append("[config] -indent must be a positive integer.")
        except Exception:
            errors.append("[config] -indent must be an integer.")

    # ---- Files list ----
    if not files or not isinstance(files, list):
        errors.append("[config] -files block is missing or empty. At least one scene must be listed.")
    else:
        seen = set()
        for f in files:
            if not isinstance(f, str) or not f.strip():
                errors.append(f"[config] Invalid entry in -files: {f!r}")
                continue
            if f in seen:
                warnings.append(f"[config] Duplicate entry in -files: '{f}'")
            seen.add(f)
            if not _exists_scene_file(project_root, f):
                errors.append(f"[config] Scene file not found: '{f}.txt' (listed in -files)")

    # ---- Vars ----
    if not isinstance(vars_dict, dict):
        errors.append("[config] -var declarations parsed into unexpected structure (expected mapping).")
        vars_dict = {}

    for name in vars_dict.keys():
        if not _is_valid_var_name(name):
            errors.append(f"[config] Invalid variable name: '{name}' (must match /^[A-Za-z_][A-Za-z0-9_]*$/).")

    # ---- save_vars ----
    if not isinstance(save_vars, list):
        errors.append("[config] -save_vars must be a list.")
        save_vars = []

    for sv in save_vars:
        if sv not in vars_dict:
            errors.append(f"[config] -save_vars contains '{sv}' which is not declared in -var.")
        elif not _is_valid_var_name(sv):
            errors.append(f"[config] -save_vars contains invalid variable name '{sv}'.")

    # ---- goals ----
    if not isinstance(goals, dict):
        errors.append("[config] -define_goal / -reach_goal structure malformed (expected mapping).")
        goals = {}

    for gname, gdata in goals.items():
        if not _is_valid_var_name(gname):
            warnings.append(f"[config] Goal name '{gname}' does not follow variable naming conventions.")
        if not isinstance(gdata, dict):
            warnings.append(f"[config] Goal '{gname}' should be a mapping of attributes (points,prompt,description...).")
            continue
        # recommended required attributes
        if "points" not in gdata:
            warnings.append(f"[config] Goal '{gname}' missing 'points' attribute (recommended).")
        if "prompt" not in gdata:
            warnings.append(f"[config] Goal '{gname}' missing 'prompt' attribute (recommended).")
        if "description" not in gdata:
            warnings.append(f"[config] Goal '{gname}' missing 'description' attribute (recommended).")
        # validate points numeric-ish if present
        pts = gdata.get("points")
        if pts is not None:
            try:
                float(pts)
            except Exception:
                warnings.append(f"[config] Goal '{gname}' has non-numeric points value: {pts!r}")
    
    # ---- Map exclude ----
    for f in map_exclude:
        if not _exists_scene_file(project_root, f):
            warnings.append(f"[config] -map_exclude references file not found: '{f}.txt'")

    # ---- Extra: sanity checks ----
    if len(vars_dict) == 0:
        warnings.append("[config] No -var declarations found, ensure this is intended.")

    return errors, warnings


# -------------------------
# Backwards-compatible loader used by game_loader.load_game()
# -------------------------
def load_and_validate(config_path: str):
    """
    Parse and validate a config file.

    Returns: (cfg_or_None, errors:list, warnings:list)
    """
    errors: List[str] = []
    warnings: List[str] = []

    if ConfigParser is None:
        errors.append("Internal error: ConfigParser not available (failed import).")
        return None, errors, warnings

    # parse
    try:
        cfg = ConfigParser(config_path).parse()
    except ConfigSyntaxError as e:
        # keep the parse error message (already formatted)
        errors.append(str(e))
        return None, errors, warnings
    except Exception as e:
        errors.append(f"Internal parser error: {e}")
        return None, errors, warnings

    # validate using validate_config
    try:
        # The project root is simply the folder containing config.txt
        project_root = os.path.dirname(os.path.abspath(config_path))
        
        v_errors, v_warnings = validate_config(cfg, project_root)
        errors.extend(v_errors)
        warnings.extend(v_warnings)
    except Exception as e:
        errors.append(f"Internal validation error: {e}")

    return cfg, errors, warnings


# CLI convenience for direct invocation
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python validators/config_validator.py path/to/config.txt")
        sys.exit(2)
    cfg, errs, warns = load_and_validate(sys.argv[1])
    if errs:
        print("ERRORS:")
        for e in errs:
            print(" -", e)
    else:
        print("No errors.")
    if warns:
        print("\nWARNINGS:")
        for w in warns:
            print(" -", w)
    else:
        print("No warnings.")


