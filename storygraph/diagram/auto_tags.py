# storygraph/diagram/auto_tags.py
from storygraph.model.semantic_nodes import SemanticNode


def is_auto_tag(x) -> bool:
    if isinstance(x, SemanticNode):
        return (
            x.kind in ("resume", "logic")
            or (x.tag and (x.tag.startswith("__sys__auto__") or x.tag.startswith("__res_")))
        )

    if isinstance(x, str):
        return x.startswith("__sys__auto__") or x.startswith("__res_")

    return False