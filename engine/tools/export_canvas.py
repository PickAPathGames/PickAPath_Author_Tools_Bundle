"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/tools/export_canvas.py

import os
from collections import Counter
from storygraph.model.extract_semantic import extract_semantic_from_tag
from storygraph.model.build_semantic_flow import build_semantic_flow_edges
from storygraph.diagram.build_diagram_graph import build_diagram_graph
from storygraph.exporters.debug_canvas_export import export_debug_canvas
from storygraph.model.build_semantic_graph import build_semantic_edges
from storygraph.layout.card_size import measure_card_size
from storygraph.config.load_mindmap_config import load_mindmap_config
from storygraph.config.validate_mindmap_config import validate_mindmap_config
from storygraph.layout.variable_colors import apply_variable_colors
from storygraph.exporters.mermaid_export import export_mermaid
from storygraph.exporters.graphviz_export import export_graphviz
from storygraph.exporters.freemind_export import export_freemind
from storygraph.exporters.freeplane_export import export_freeplane
from storygraph.exporters.drawio_export import export_drawio
from storygraph.layout.apply_flow_layout import apply_flow_layout
from engine.project.load_project import load_project


EXPORTERS = {
    "obsidian_canvas": {
        "func": export_debug_canvas,
        "ext": "canvas",
        "per_scene": True,
        "extra": lambda cfg: {
            "center_columns": cfg.get("card", {}).get("center_in_column", False)
        },
    },
    "mermaid": {
        "func": export_mermaid,
        "ext": "mermaid",
        "per_scene": True,
        "extra": lambda cfg: {},
    },
    "graphviz": {
        "func": export_graphviz,
        "ext": "dot",
        "per_scene": True,
        "extra": lambda cfg: {},
    },
    "freemind": {
        "func": export_freemind,
        "ext": "mm",
        "per_scene": True,
        "extra": lambda cfg: lambda scene: {"scene_name": scene},
    },
    "freeplane": {
        "func": export_freeplane,
        "ext": "freeplane",
        "per_scene": True,
        "extra": lambda cfg: lambda scene: {"scene_name": scene},
    },
    "drawio": {
        "func": export_drawio,
        "ext": "drawio",
        "per_scene": True,
        "extra": lambda cfg: {},
    },
}


def main(
    *,
    scenes_config: str = "scenes/config.txt",
    out_dir: str = "canvas",
    config_path: str | None = None,
    quiet: bool = False,
    **_,
) -> dict:

    """
    Export semantic diagrams to canvas / mindmap formats.

    Returns a structured result suitable for:
    - CLI
    - VS Code extension
    - CI / automation
    """

    result = {
        "tool": "export_canvas",
        "status": "ok",
        "errors": [],
        "warnings": [],
        "artifacts": {
            "exported_scenes": [],
        },
    }

    try:
        # ─────────────────────────────────────────────
        # 0. Load project (single entry point)
        # ─────────────────────────────────────────────
        ctx = load_project(
            scenes_config=scenes_config,
            validate=True,
        )

        result["errors"].extend(
            d.format_console() for d in ctx.diagnostics.get("errors", [])
        )
        result["warnings"].extend(
            d.format_console() for d in ctx.diagnostics.get("warnings", [])
        )

        if result["errors"]:
            result["status"] = "error"
            if not quiet:
                print("Structural issues found:")
                for e in result["errors"]:
                    print(" -", e)
            return result

        # ─────────────────────────────────────────────
        # 1. Build semantic nodes
        # ─────────────────────────────────────────────
        semantic_nodes = []

        for scene in ctx.scenes.values():
            for node in scene.nodes.values():
                # SKIP sys tags at the entry point
                # if node.tag.startswith("__sys_"):
                #     continue
                # if node.tag.startswith("__res_"):
                #     continue
                    
                semantic_nodes.extend(
                    extract_semantic_from_node(scene, node)
                )

        if not quiet:
            counts = Counter(node.kind for node in semantic_nodes)
            print("\n[semantic node kinds]")
            for k, v in counts.items():
                print(f"{k:10} {v}")
            print()

        semantic_scene_by_id = {
            node.id: node.scene
            for node in semantic_nodes
        }

        semantic_structural_edges = build_semantic_edges(semantic_nodes)

        # ─────────────────────────────────────────────
        # 2. Build semantic FLOW edges
        # ─────────────────────────────────────────────
        semantic_flow_edges = []

        for scene in ctx.scenes.values():
            semantic_flow_edges.extend(
                build_semantic_flow_edges_from_scene(
                    scene=scene,
                    semantic_nodes=semantic_nodes,
                )
            )

        all_semantic_edges = (
            semantic_structural_edges
            + semantic_flow_edges
        )

        # ─────────────────────────────────────────────
        # 3. Build diagram graph
        # ─────────────────────────────────────────────
        diagram_nodes, diagram_edges = build_diagram_graph(
            semantic_nodes,
            all_semantic_edges,
        )

        if not quiet:
            counts = Counter(node.kind for node in diagram_nodes)
            print("\n[diagram node kinds]")
            for k, v in sorted(counts.items()):
                print(f"{k:10} {v}")

            print(
                f"\n[diagram] nodes={len(diagram_nodes)} "
                f"edges={len(diagram_edges)}"
            )

        # ─────────────────────────────────────────────
        # 4. Load & validate mindmap config
        # ─────────────────────────────────────────────
        mindmap_cfg = load_mindmap_config(config_path)

        known_vars = {
            node.var
            for node in semantic_nodes
            if hasattr(node, "var") and node.var
        }

        mindmap_cfg = validate_mindmap_config(
            mindmap_cfg,
            known_vars=known_vars,
        )

        # ─────────────────────────────────────────────
        # 5. Resolve enabled exporters
        # ─────────────────────────────────────────────
        enabled_exporters: set[str] = set()

        if mindmap_cfg:
            enabled_exporters = {
                name
                for name, enabled in mindmap_cfg.get("exporters", {}).items()
                if enabled
            }

        if not enabled_exporters:
            msg = "No exporters enabled in mindmap_config.json"
            result["warnings"].append(msg)
            if not quiet:
                print("[warn]", msg)

        # ─────────────────────────────────────────────
        # 6. Measure card sizes
        # ─────────────────────────────────────────────
        card_cfg = None
        if mindmap_cfg and mindmap_cfg.get("card", {}).get("auto_size"):
            card_cfg = mindmap_cfg["card"]

        if card_cfg:
            for node in diagram_nodes:
                text = node.title
                if node.ops:
                    text += "\n" + "\n".join(node.ops)

                w, h = measure_card_size(text, card_cfg)
                node.width = w
                node.height = h
        else:
            for node in diagram_nodes:
                node.width = 420
                node.height = 120

        # ─────────────────────────────────────────────
        # 7. Variable colors
        # ─────────────────────────────────────────────
        if mindmap_cfg:
            rules = mindmap_cfg.get("variable_colors", [])
            apply_variable_colors(diagram_nodes, rules)

        # ─────────────────────────────────────────────
        # 8. Apply layout (ONCE)
        # ─────────────────────────────────────────────
        center_cols = False
        if mindmap_cfg:
            center_cols = mindmap_cfg.get("card", {}).get("center_in_column", False)

        apply_flow_layout(
            diagram_nodes,
            diagram_edges,
            center_columns=center_cols,
            config=mindmap_cfg
        )

        # ─────────────────────────────────────────────
        # 9. Export
        # ─────────────────────────────────────────────
        os.makedirs(out_dir, exist_ok=True)

        nodes_by_scene, edges_by_scene = split_diagram_by_scene(
            diagram_nodes,
            diagram_edges,
            semantic_scene_by_id,
        )

        for scene_name, scene_nodes in nodes_by_scene.items():
            scene_edges = edges_by_scene.get(scene_name, [])
            result["artifacts"]["exported_scenes"].append(scene_name)

            if not quiet:
                print(
                    f"[export] {scene_name}: "
                    f"nodes={len(scene_nodes)} "
                    f"edges={len(scene_edges)}"
                )

            for exporter_name in enabled_exporters:
                spec = EXPORTERS.get(exporter_name)
                if not spec:
                    continue

                kwargs = {
                    "out_path": f"{out_dir}/{scene_name}.{spec['ext']}",
                }

                extra = spec["extra"](mindmap_cfg)
                if callable(extra):
                    kwargs.update(extra(scene_name))
                else:
                    kwargs.update(extra)

                spec["func"](
                    scene_nodes,
                    scene_edges,
                    **kwargs,
                )

        if not quiet:
            print("DONE")

        return result

    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(str(exc))

        if not quiet:
            raise

        return result

def extract_semantic_from_node(scene, node):
    # Create a full map of the scene's tags/nodes
    # This acts as our 'parsed_scene' reference for stitching
    scene_data_map = {
        n.tag: {
            "text_items": n.text_items,
            "blocks": n.blocks,
            "line": n.line,
            "continuations": n.continuations
        }
        for n in scene.nodes.values()
    }

    return extract_semantic_from_tag(
        scene_name=scene.name,
        tag_name=node.tag,
        tag_data={
            "text_items": node.text_items,
            "blocks": node.blocks,
            "line": node.line,
        },
        parsed_scene={"tags": scene_data_map}, # Pass the full context here
        all_nodes_in_scene={} # (Optional node_map for ID tracking)
    )

def build_semantic_flow_edges_from_scene(scene, semantic_nodes):
    return build_semantic_flow_edges(
        parsed_scene={
            "scene_name": scene.name,
            "nodes": {
                node.tag: {
                    "blocks": node.blocks,
                    "line": node.line,
                    "continuations": node.continuations,
                }
                for node in scene.nodes.values()
            },
        },
        semantic_nodes=semantic_nodes,
    )

def split_diagram_by_scene(diagram_nodes, diagram_edges, semantic_scene_by_id):
    nodes_by_scene = {}

    for node in diagram_nodes:
        scene = semantic_scene_by_id.get(node.id)
        if scene is None:
            scene = "__external__"
        nodes_by_scene.setdefault(scene, []).append(node)

    edges_by_scene = {}
    for scene, nodes in nodes_by_scene.items():
        node_ids = {n.id for n in nodes}
        edges_by_scene[scene] = [
            e for e in diagram_edges
            if e.from_id in node_ids
        ]

    return nodes_by_scene, edges_by_scene


if __name__ == "__main__":
    from engine.tools.cli_utils import standard_parser, run_cli

    parser = standard_parser("export_canvas")
    run_cli(parser=parser, runner=main)
