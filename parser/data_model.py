"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# parser/data_model.py
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import json


def assert_block_shape(block: dict):
    assert isinstance(block, dict), f"Block must be dict, got {type(block)}"
    assert "cmd" in block, f"Block missing 'cmd': {block}"
    assert "__line__" in block, f"Block missing '__line__': {block}"
    assert "__indent__" in block or "indent" in block, (
        f"Block missing indentation info: {block}"
    )

@dataclass
class LinkTarget:
    """
    Normalized link target representation.
      - chapter: target chapter name (string)
      - tag: target tag name or None (resolve to chapter first-tag later)
      - is_next: True if special __NEXT__ marker (used for -next)
      - is_go_and_back: optional boolean if the link indicates go + back behavior
    """
    chapter: str
    tag: Optional[str] = None
    is_next: bool = False
    is_go_and_back: bool = False

    def as_tuple(self) -> Tuple[str, Optional[str], bool]:
        # For compatibility with older code: (chapter, tag, is_go_and_back)
        return (self.chapter, self.tag, self.is_go_and_back)

    # NOTE: LinkTarget is a single target object; it does not hold a .links list.
    # Iteration of links is handled on Node level (which aggregates LinkTarget objects).
    def to_dict(self):
        return {"chapter": self.chapter, "tag": self.tag, "is_next": self.is_next, "is_go_and_back": self.is_go_and_back}

# ------------------------------
# Node dataclass - backward + forward compatible
# ------------------------------
@dataclass
class Node:
    # legacy parser expects to be able to call Node(id=..., chapter=..., tag=..., line=...)
    # we therefore expose an 'id' field (string) along with semantic aliases.
    id: Optional[str] = None             # legacy unique id (parser sometimes set this explicitly)
    chapter: Optional[str] = None       # chapter name (set by parser)
    tag: Optional[str] = None           # node tag (required for scene.nodes mapping)
    line: Optional[int] = None          # parser line number

    # file / parser metadata used by MiniParser
    file_id: Optional[str] = None
    indent: Optional[int] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    # --- Parser legacy fields (preserve compatibility) ---
    text_items: List[Any] = field(default_factory=list)
    terminals: List[Any] = field(default_factory=list)
    choices: List[Any] = field(default_factory=list)
    commands: List[Any] = field(default_factory=list)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    ifs: List[str] = field(default_factory=list)

    # --- Link representations ---
    # `links` holds LinkTarget objects (newer normalized)
    links: List[LinkTarget] = field(default_factory=list)
    # `continuations` keeps parser-style tuples for backward compat:
    # (chapter, tag_or_None, is_go_and_back:bool, resume_tag_or_None?) - variable length tolerated
    continuations: List[Tuple[Any, ...]] = field(default_factory=list)

    # parser/validator edges (ParserEdge objects) produced by mini_parser._inject_edges_from_structure
    edges: List[Any] = field(default_factory=list)

    # --- Variable usage annotations (used by validators) ---
    var_reads: List[str] = field(default_factory=list)
    var_writes: List[str] = field(default_factory=list)
    var_mutations: List[str] = field(default_factory=list)

    # convenience: alias node_id property mapping to 'id'
    @property
    def node_id(self) -> Optional[str]:
        return self.id

    @node_id.setter
    def node_id(self, v: Optional[str]):
        self.id = v

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "chapter": self.chapter,
            "tag": self.tag,
            "line": self.line,
            "file_id": self.file_id,
            "indent": self.indent,
            "meta": dict(self.meta or {}),
            "text_items": list(self.text_items or []),
            "blocks": list(self.blocks or []),
            "choices": list(self.choices or []),
            "continuations": [tuple(c) for c in (self.continuations or [])],
            "links": [lt.to_dict() for lt in (self.links or [])],
            # Only serialize edges that actually have a target tag
            "edges": [
                {
                    "chapter": getattr(e, "chapter", None),
                    "tag": getattr(e, "tag", None),
                    "kind": getattr(e, "kind", None),
                    "condition": getattr(e, "condition", None)
                }
                for e in (self.edges or []) 
                if getattr(e, "tag", None) is not None
            ],
            "vars": {
                "reads": list(set(self.var_reads or [])),
                "writes": list(set(self.var_writes or [])),
                "mutations": list(self.var_mutations or []),
            },
        }

    # Iterator expected by ValidatorRuntime.iter_links view
    def iter_links(self):
        """
        Yield normalized outgoing links as tuples (chapter, tag_or_None, is_go_and_back)
        This is the view ValidatorRuntime expects.
        De-duplicates identical targets (based on chapter/tag/is_go_and_back).
        """
        seen = set()  # set of (chapter, tag, is_go_and_back)
        # First yield normalized LinkTarget entries
        for lt in self.links or []:
            key = (lt.chapter, lt.tag, bool(lt.is_go_and_back))
            if key in seen:
                continue
            seen.add(key)
            yield key

        # Then yield continuation tuples (backwards-compat). These may have extra trailing fields.
        for cont in self.continuations or []:
            try:
                # support several shapes: (chapter, tag, is_gab, resume?), (chapter, tag, is_gab)
                ch = cont[0] if len(cont) > 0 else None
                tg = cont[1] if len(cont) > 1 else None
                is_gab = bool(cont[2]) if len(cont) > 2 else False
                key = (ch, tg, is_gab)
                if key in seen:
                    continue
                seen.add(key)
                yield key
            except Exception:
                # be resilient to malformed continuation entries
                continue

    def add_link(self, chapter: str, tag: Optional[str] = None, is_go_and_back: bool = False, is_next: bool = False):
        """Convenience to add a normalized LinkTarget and keep continuity with older code."""
        # Add LinkTarget (normalized)
        self.links.append(LinkTarget(chapter=chapter, tag=tag, is_next=is_next, is_go_and_back=is_go_and_back))

        # Also keep a lightweight continuation tuple for older consumers,
        # but avoid duplicating identical continuation entries.
        cont_tuple = (chapter, tag, bool(is_go_and_back), None)
        # initialize continuations list if absent (defensive)
        if self.continuations is None:
            self.continuations = []
        # Only append if identical tuple not already present
        if cont_tuple not in self.continuations:
            self.continuations.append(cont_tuple)


    # small helper used by debug/printing code
    def __repr__(self):
        return f"<Node {self.chapter or '??'}:{self.tag or '??'} line={self.line} id={self.id}>"

@dataclass
class ParserEdge:
    chapter: str
    tag: str
    kind: str                  # "continuation", "go", "next", "go_file", "go_back", etc.
    condition: Optional[str] = None


@dataclass
class Scene:
    name: str
    nodes: Dict[str, Node] = field(default_factory=dict)
    file_meta: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Convert the Scene to JSON, including all nodes."""
        data = {"__file__": self.file_meta}
        for tag, node in self.nodes.items():
            data[tag] = node.to_dict()
        return json.dumps(data, indent=2, ensure_ascii=False)

    def debug_dump(self):
        print("\n==============================")
        print(f" SCENE GRAPH DUMP for '{self.name}'")
        print("==============================")

        if not self.nodes:
            print("(no nodes!)")
            return

        for tag, node in self.nodes.items():


            print("\nNODE STRUCTURE:", type(node), vars(node))

            print(f"\nTAG: {tag}  (line {node.line})")
            print("  Outgoing links:")

            any_links = False
            for (ch, tg, gab) in node.iter_links():
                any_links = True
                # prepare display-friendly tag
                display_tag = tg if tg is not None else "__FIRST__"
                # show special __NEXT__ marker if tg is a sentinel
                if tg == "__NEXT__" or getattr(tg, "upper", lambda: None)() == "__NEXT__":
                    display_tag = "__NEXT__"
                if gab:
                    print(f"    -> {ch}:{display_tag}   (go_and_back)")
                else:
                    print(f"    -> {ch}:{display_tag}")

            if not any_links:
                print("    (none)")

            if node.terminals:
                print(f"  Terminals: {node.terminals}")

            if node.continuations:
                print("  Implicit continuations:")
                seen = set()
                for cont in node.continuations:
                    try:
                        ch = cont[0] if len(cont) > 0 else None
                        tg = cont[1] if len(cont) > 1 else None
                        gab = cont[2] if len(cont) > 2 else None
                        # unique key ignores resume_tag/extra, we care about the target & go_and_back flag
                        key = (ch, tg, bool(gab))
                        if key in seen:
                            continue
                        seen.add(key)
                        extra = tuple(cont[3:]) if len(cont) > 3 else ()
                        print(f"    -> {ch}:{tg} (go&back={gab})  extra={extra}")
                    except Exception as e:
                        print("    [Error printing continuation]", cont, e)



        print("==============================\n")

