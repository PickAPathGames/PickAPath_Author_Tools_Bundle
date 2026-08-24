"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# commands/persistence.py
import csv
import os
from datetime import datetime
from command_registry import COMMANDS

@COMMANDS.register_parser("-save_vars")
def parse_save_vars(parser, args, line, level):
    # args: "hp mp gold location"
    # Store this in the scene metadata so the engine knows the 'schema'
    var_list = args.replace(",", " ").split()
    parser.scene.file_meta["save_vars"] = var_list
    return {"cmd": "-save_vars", "args": var_list, "__line__": line}

@COMMANDS.register_runtime("-save_vars")
def run_save_vars(engine, args, block):
    # Runtime doesn't change anything, just registers the list for snapshots
    engine.state["save_vars_list"] = block["args"]
    return "logic"

@COMMANDS.register_parser("-snapshot")
def parse_snapshot(parser, args, line, level):
    return {"cmd": "-snapshot", "args": args, "__line__": line}

@COMMANDS.register_runtime("-snapshot")
def run_snapshot(engine, args, block):
    """Appends current values of save_vars to a CSV file."""
    save_vars = engine.state.get("save_vars_list", [])
    if not save_vars:
        return "logic" # Nothing to save

    filename = "game_snapshots.csv"
    file_exists = os.path.isfile(filename)

    # Prepare the data row
    # Include a timestamp and the current scene/tag for context
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scene": engine.state["scene"],
        "tag": engine.state["tag"]
    }
    
    # Add the requested variables
    for var in save_vars:
        row[var] = engine.state["vars"].get(var, "N/A")

    # Write to CSV
    with open(filename, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "scene", "tag"] + save_vars)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return "logic"
























