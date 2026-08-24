# storygraph/diagram/diagram_edges.py
from dataclasses import dataclass

@dataclass(frozen=True)
class DiagramEdge:
    from_id: str
    to_id: str
    label: str | None = None  # optional (rarely needed)
