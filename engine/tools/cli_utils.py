# engine/tools/cli_utils.py
import argparse
import json
import sys
import os
from typing import Callable


def standard_parser(tool_name: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=tool_name)

    p.add_argument(
        "--config",
        dest="config_path",
        help="Path to tool configuration file",
    )

    p.add_argument(
        "--scenes",
        dest="scenes_config",
        # default="scenes/config.txt",
        default=None,
        help="Path to scenes config file",
    )

    p.add_argument(
        "--out",
        dest="out_dir",
        default="out",
        help="Output directory",
    )

    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON result",
    )

    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress human-readable output",
    )

    return p


def run_cli(
    *,
    parser: argparse.ArgumentParser,
    runner: Callable[..., dict],
):
    args = parser.parse_args()

    scenes_config = args.scenes_config
    if scenes_config is None:
        if os.path.exists("scenes/config.pap"):
            scenes_config = "scenes/config.pap"
        elif os.path.exists("scenes/config.txt"):
            scenes_config = "scenes/config.txt"
        else:
            scenes_config = None  # let tools error cleanly

    result = runner(
        scenes_config=scenes_config,
        config_path=args.config_path,
        out_dir=args.out_dir,
        quiet=args.quiet or args.json,
    )

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")

    if result is None:
        print("Engine reached the end of the story.")
        return
    if result.get("status") != "ok" and not args.json:
        sys.exit(1)


