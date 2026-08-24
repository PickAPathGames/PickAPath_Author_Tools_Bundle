# author_tools/export_canvas.py
from engine.tools.export_canvas import main
from pathlib import Path

# Locate the config file sitting next to this script
CONFIG = Path(__file__).parent / "mindmap_config.json"

if __name__ == "__main__":
    # Pass the explicit path to the tool
    main(config_path=str(CONFIG))

