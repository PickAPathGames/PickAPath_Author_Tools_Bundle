from __future__ import annotations


"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""

# parser/config_parser.py
"""
Strict config parser for PickQuick-style config.txt

Rules enforced:
 - Directives (lines beginning with '-') must start at column 0 (no leading spaces).
 - Certain directives open an indented block (e.g. -files, -define_goal).
 - -var requires a name and a value; values must be:
      * double-quoted strings (required for strings),
      * ints, floats, or booleans (True/False).
 - Unknown top-level directives raise a ConfigSyntaxError (strict mode).
 - Good error messages include line number and context.
"""

import re
from typing import List, Optional
from config.config_model import Config
from config_exceptions import ConfigSyntaxError


# name validation (same as validator)
_VARNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_valid_var_name(name: str) -> bool:
    return bool(_VARNAME_RE.match(name))


class ConfigParser:
    def __init__(self, path: str, strict: bool = True):
        self.path = path
        self.strict = strict  # unknown directive behavior (we will raise if True)

    def parse(self) -> Config:
        with open(self.path, "r", encoding="utf-8") as fh:
            raw_lines = fh.readlines()

        cfg = Config()

        # Parser state for blocks
        in_files_block = False
        current_goal: Optional[str] = None
        goal_indent: Optional[int] = None
        files_block_indent: Optional[int] = None

        for idx, raw in enumerate(raw_lines, start=1):
            stripped = raw.lstrip(" ")

            # Only enforce indentation rules when NOT inside a block
            if current_goal is None and not in_files_block:
                # If line starts with spaces or tabs and then a dash, reject it
                if re.match(r"^[ \t]+-", raw):
                    raise ConfigSyntaxError(
                        "Indentation is not allowed at top-level directives",
                        line=idx
                    )

            # Keep the original raw to report in errors if needed
            line_raw = raw.rstrip("\n")
            # identify indentation and content
            # NOTE: tabs are not allowed; convert tabs -> spaces to be predictable
            if "\t" in line_raw:
                raise ConfigSyntaxError("Tabs are not allowed; use spaces for indentation", line=idx)

            # Skip comments & blank lines
            if not line_raw.strip() or line_raw.strip().startswith("#") or line_raw.strip().startswith("-ignore"):
                continue

            # indentation count (number of leading spaces)
            indent = len(line_raw) - len(line_raw.lstrip(" "))
            content = line_raw.lstrip(" ")

            # If we're currently inside the -files block:
            if in_files_block:
                # Block entries MUST be more-indented than the -files line.
                if indent <= files_block_indent:
                    # block ended, treat this line again as top-level
                    in_files_block = False
                    files_block_indent = None
                    # fallthrough to handle as top-level line
                else:
                    # this is a files-entry line, must be a bare token, no dash
                    if content.startswith("-"):
                        raise ConfigSyntaxError("Invalid entry inside -files block (must be a bare basename, not a directive)", line=idx)
                    val = content.strip()
                    if not val:
                        raise ConfigSyntaxError("Empty entry inside -files block", line=idx)
                    # simple basename validation (no dots or slashes allowed)
                    if "/" in val or "\\" in val or val.endswith(".txt"):
                        raise ConfigSyntaxError("Files list entries should be basenames (e.g. 'demo'), not paths", line=idx)
                    cfg.files.append(val)
                    continue

            # If inside a goal block:
            if current_goal is not None:
                # goal attributes must be indented
                if indent <= goal_indent:
                    # end of goal block; reset and reprocess line as top-level
                    current_goal = None
                    goal_indent = None
                    # fallthrough to process as top-level
                else:
                    # parse a goal attribute: expecting "-key = value" or "-key value"
                    if not content.startswith("-"):
                        raise ConfigSyntaxError("Goal attribute lines must start with '-'", line=idx)
                    # remove leading dash and parse
                    after_dash = content[1:].lstrip()
                    if not after_dash:
                        raise ConfigSyntaxError("Malformed goal attribute", line=idx)
                    # split on first '=' or whitespace
                    if "=" in after_dash:
                        key, _, val = after_dash.partition("=")
                        key = key.strip()
                        val = val.strip()
                    else:
                        parts = after_dash.split(None, 1)
                        key = parts[0].strip()
                        val = parts[1].strip() if len(parts) > 1 else ""
                    if not key:
                        raise ConfigSyntaxError("Empty goal attribute name", line=idx)
                    # Enforce quoted-strings for string attributes
                    parsed_val = _parse_value_strict(val, line=idx)
                    goal = cfg.add_goal(current_goal)
                    goal.attributes[key] = parsed_val
                    continue

            # Top-level directives MUST start at column 0 (indent == 0)
            if indent != 0:
                raise ConfigSyntaxError("Unexpected indentation at top-level; top-level directives must start at column 0", line=idx)

            # Top-level content should start with '-'
            if not content.startswith("-"):
                raise ConfigSyntaxError("Top-level lines must be directives starting with '-'", line=idx)

            # Extract directive name and rest
            parts = content.split(None, 1)  # splits into (-directive, rest?) where directive includes dash
            directive = parts[0]  # e.g. "-title"
            rest = parts[1].strip() if len(parts) > 1 else ""

            # Handle known directives:
            if directive == "-title":
                if not rest:
                    raise ConfigSyntaxError("Missing value for -title", line=idx)
                cfg.meta["title"] = rest
                continue

            if directive == "-author":
                if not rest:
                    # allow empty author as warning; but we use strict behavior -> error
                    raise ConfigSyntaxError("Missing value for -author", line=idx)
                cfg.meta["author"] = rest
                continue

            if directive == "-version":
                if not rest:
                    cfg.meta["version"] = "0"
                else:
                    cfg.meta["version"] = rest
                continue

            if directive == "-indent":
                if not rest:
                    raise ConfigSyntaxError("Missing value for -indent", line=idx)
                try:
                    ival = int(rest)
                    if ival <= 0:
                        raise ValueError()
                    cfg.meta["indent"] = ival
                except Exception:
                    raise ConfigSyntaxError("-indent must be a positive integer", line=idx)
                continue

            if directive == "-files":
                # start files block; next lines must be indented
                in_files_block = True
                # base indent is the indent of subsequent lines; here zero, so requires indent > 0
                files_block_indent = 0
                continue

            if directive == "-var":
                if not rest:
                    raise ConfigSyntaxError("Invalid -var syntax (missing name and value)", line=idx)

                # Split into tokens first
                tokens = rest.split()

                # Case 1: "name = value" OR "name value"
                if len(tokens) >= 2 and tokens[1] != "=":
                    name = tokens[0]
                    raw_val = rest[len(name):].strip()
                    # If starts with '=', remove it
                    if raw_val.startswith("="):
                        raw_val = raw_val[1:].strip()
                else:
                    # Case 2: "name=value" OR malformed something
                    if "=" in rest:
                        name, raw_val = rest.split("=", 1)
                        name = name.strip()
                        raw_val = raw_val.strip()
                    else:
                        # Missing "=" AND missing value
                        raise ConfigSyntaxError(
                            f"Missing value for variable '{tokens[0]}'",
                            line=idx
                        )

                if not _is_valid_var_name(name):
                    raise ConfigSyntaxError(f"Invalid variable name '{name}'", line=idx)

                if raw_val == "":
                    raise ConfigSyntaxError(f"Missing value for variable '{name}'", line=idx)

                parsed_val = _parse_value_strict(raw_val, line=idx)
                cfg.vars[name] = parsed_val
                continue

            if directive == "-save_vars":
                # expects parentheses: (a, b, c)
                if not rest:
                    raise ConfigSyntaxError("Missing value for -save_vars", line=idx)
                m = re.search(r"\((.*?)\)", rest)
                if not m:
                    raise ConfigSyntaxError("Expected parentheses for -save_vars, e.g. -save_vars = (a, b)", line=idx)
                items = [x.strip() for x in m.group(1).split(",") if x.strip()]
                for it in items:
                    if not _is_valid_var_name(it):
                        raise ConfigSyntaxError(f"Invalid save variable name '{it}'", line=idx)
                cfg.save_vars = items
                continue

            if directive == "-define_goal":
                if not rest:
                    raise ConfigSyntaxError("Missing goal name for -define_goal", line=idx)
                goal_name = rest.strip()
                if not _is_valid_var_name(goal_name):
                    raise ConfigSyntaxError(f"Invalid goal name '{goal_name}'", line=idx)
                current_goal = goal_name
                # goal attributes must be indented (indent > 0)
                goal_indent = 0
                cfg.add_goal(goal_name)
                continue

            if directive == "-reach_goal":
                # top-level reach_goal might be allowed, but in config we just record it as a meta-flag
                if not rest:
                    raise ConfigSyntaxError("Missing goal name for -reach_goal", line=idx)
                cfg.meta.setdefault("reach_goals", []).append(rest.strip())
                continue
                
            if directive == "-author_mode":
                val_str = self._strip_equals(rest)
                cfg.meta["author_mode"] = _parse_value_strict(val_str, line=idx)
                continue

            if directive == "-map_mode":
                val_str = self._strip_equals(rest)
                cfg.meta["map_mode"] = val_str.lower()
                continue
            
            if directive == "-map_style":
                val_str = self._strip_equals(rest)
                cfg.meta["map_style"] = val_str.lower()
                continue

            if directive == "-permanent_stat":
                # Store these in a list in the config object
                cfg.meta.setdefault("permanent_stats", []).append(rest.strip())
                continue

            if directive == "-map_exclude":
                if not rest:
                    raise ConfigSyntaxError("Missing value for -map_exclude", line=idx)
                
                # Support space-separated values
                files_to_exclude = rest.split()
                if not hasattr(cfg, "map_exclude"):
                    cfg.map_exclude = set()
                
                for f in files_to_exclude:
                    # Basic validation: ensure it's a basename, not a path
                    if "/" in f or "\\" in f or f.endswith(".txt"):
                        raise ConfigSyntaxError(f"Exclude list entries should be basenames: '{f}'", line=idx)
                    cfg.map_exclude.add(f)
                continue

            # Unknown directive
            if self.strict:
                raise ConfigSyntaxError(f"Unknown directive '{directive}'", line=idx)
            else:
                # permissive: ignore unknown directive
                continue

        # done reading lines
        return cfg
    

    def _strip_equals(self, rest: str) -> str:
        """Removes a leading '=' if present and returns the cleaned value string."""
        content = rest.strip()
        if content.startswith("="):
            return content[1:].lstrip()
        return content


def _parse_value_strict(raw_val: str, line: Optional[int] = None):
    """
    Parse a value under strict rules:
      - double-quoted strings required for strings
      - integers
      - floats
      - booleans True/False
    Anything else → raise ConfigSyntaxError.
    """
    if raw_val is None:
        raise ConfigSyntaxError("Missing value", line=line)

    rv = raw_val.strip()

    # Double-quoted string
    if rv.startswith('"') and rv.endswith('"'):
        inner = rv[1:-1]
        # reject internal stray quotes (we require simple "..." only)
        if '"' in inner:
            raise ConfigSyntaxError("Invalid quoted string (unexpected '\"' inside)", line=line)
        return inner

    # Single quotes are forbidden
    if rv.startswith("'") or rv.endswith("'"):
        raise ConfigSyntaxError("Single-quoted strings are not allowed; use double quotes", line=line)

    # Integer
    if re.fullmatch(r"-?\d+", rv):
        return int(rv)

    # Float
    if re.fullmatch(r"-?\d+\.\d+", rv):
        try:
            return float(rv)
        except Exception:
            pass

    # Boolean (case-insensitive)
    if rv.lower() in ("true", "false"):
        return rv.lower() == "true"

    # empty string not allowed (must use "")
    if rv == "":
        raise ConfigSyntaxError("Empty value; strings must be double-quoted", line=line)

    # Anything else: invalid under strict rules
    raise ConfigSyntaxError(f"Unquoted or invalid value: {rv!r}", line=line)

