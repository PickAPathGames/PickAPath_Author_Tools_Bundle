# storygraph/model/semantic_edges.py
from dataclasses import dataclass

@dataclass(frozen=True)
class SemanticEdge:
    from_id: str
    to_id: str
    index: int 