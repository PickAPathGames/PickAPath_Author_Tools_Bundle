# storygraph/diagram/diagram_nodes.py
from dataclasses import dataclass
from typing import List

@dataclass
class DiagramNode:
    id: str
    kind: str        # tag | if | elseif | else | choice | pick_if | go | next | end
    title: str       # "-if strength > 10", "#The gym", "-tag initial_tag"
    ops: List[str]   # ["-mvar strength +3"]
    source_ref: str  # semantic node id (for traceability)
    scene: str
    index: int
    width: int = 0
    height: int = 0
    color: str | None = None