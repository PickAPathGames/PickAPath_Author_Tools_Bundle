# config/load_mindmap_config.py
from pathlib import Path
import json

DEFAULT_CONFIG = Path(__file__).parents[1] / "mindmap_config.json"

def load_mindmap_config(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else DEFAULT_CONFIG

    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

