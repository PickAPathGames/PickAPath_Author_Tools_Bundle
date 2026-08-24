# author_tools/pickrandom.py

from engine.tools.pickrandom import main
from pathlib import Path

CONFIG = Path(__file__).with_name("pickrandom_and_stats_config.json")

if __name__ == "__main__":
    main(config_path=str(CONFIG))

