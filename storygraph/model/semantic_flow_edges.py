# storygraph/model/semantic_flow_edges.py

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SemanticFlowEdge:
    from_id: str
    to_id: str
    kind: str
    condition: Optional[str] = None
    line: Optional[int] = None
    label: Optional[str] = None

