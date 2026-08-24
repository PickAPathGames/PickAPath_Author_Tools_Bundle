# storygraph/model/semantic_nodes.py
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class SemanticNode:
    id: str
    kind: str
    label: str
    scene: str
    tag: str
    line: Optional[int] = None
    parent_id: Optional[str] = None
    blocks: list = field(default_factory=list)
    is_single_pick_child: bool = False
    # --- FIX: Declare meta dictionary with field factory ---
    meta: Dict[str, Any] = field(default_factory=dict)


def semantic_id(*parts: str) -> str:
    return "::".join(str(p) for p in parts if p)