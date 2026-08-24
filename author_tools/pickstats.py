# author_tools/pickstats.py

from engine.tools.pickstats import main
from pathlib import Path
import sys

CONFIG = Path(__file__).with_name("pickrandom_and_stats_config.json")

if __name__ == "__main__":
    result = main(config_path=str(CONFIG))
    if result["status"] != "ok":
        sys.exit(1)


