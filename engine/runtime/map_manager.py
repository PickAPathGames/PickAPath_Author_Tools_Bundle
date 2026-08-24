"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/runtime/map_manager.py
from storygraph.model.extract_semantic import extract_semantic_from_tag
from storygraph.model.build_semantic_flow import build_semantic_flow_edges
from storygraph.diagram.build_diagram_graph import build_diagram_graph
from storygraph.layout.flow_layout import compute_flow_layout
from storygraph.config.load_mindmap_config import load_mindmap_config
from storygraph.layout.card_size import measure_card_size

class MapManager:
    def build_live_map(self, runtime, ordered_scenes=None, map_exclude=None, override_style=None):
        mindmap_cfg = load_mindmap_config()

        exclude_set = set(map_exclude) if map_exclude else set()

        if not ordered_scenes:
            ordered_scenes = getattr(runtime, 'files', sorted(runtime.scenes.keys()))

        all_web_nodes, all_web_edges, chapters, manifest = [], [], [], {}
        current_x_offset = 0
        # padding = 500
        padding = 100

        # OVERRIDE LOGIC: Use the URL parameter if provided, otherwise fall back to engine vars
        if override_style and override_style.strip().lower() in ["nodes", "cards", "lines"]:
            map_style = override_style.strip().lower()
        else:
            raw_style = runtime.state["vars"].get("map_style", "nodes")
            map_style = str(raw_style).replace('"', '').replace("'", "").strip().lower()

        for scene_name in ordered_scenes:
            if scene_name not in runtime.scenes:
                continue

            if scene_name in exclude_set: 
                continue

            scene_obj = runtime.scenes[scene_name]

            # --- CHAPTER HEADERS ---
            # Add a chapter marker at the start of each scene's x_offset
            chapters.append({"name": scene_name, "x": current_x_offset})

        # for scene_name, scene_obj in sorted(runtime.scenes.items()):
            scene_data_map = {tag: {"line": n.line, "continuations": n.continuations} for tag, n in scene_obj.nodes.items()}
            full_scene_context = {"tags": {t: {"blocks": n.blocks, "line": n.line} for t, n in scene_obj.nodes.items()}}
            semantic_nodes = []

            # # 1. Extraction & Initial Measurement
            # for tag, node_obj in scene_obj.nodes.items():
            #     if tag.startswith("__res_") or tag.startswith("__sys_"): continue
            #     extracted = extract_semantic_from_tag(scene_name, tag, {"blocks": node_obj.blocks, "line": node_obj.line}, full_scene_context)
                
            #     for sem_node in extracted:
            #         # If style is 'nodes', use a fixed circle diameter (e.g., 50)
            #         if map_style == "nodes":
            #             sem_node.width = 50
            #             sem_node.height = 50
            #         else:
            #             txt = getattr(sem_node, 'title', getattr(sem_node, 'label', "unknown"))
            #             w, h = measure_card_size(txt, mindmap_cfg["card"])
            #             sem_node.width = w
            #             sem_node.height = h
            #     semantic_nodes.extend(extracted)

            # 1. Extraction & Initial Measurement
            for tag, node_obj in scene_obj.nodes.items():
                # if tag.startswith("__res_") or tag.startswith("__sys_"): continue
                if tag.startswith("__sys_"): continue
                extracted = extract_semantic_from_tag(scene_name, tag, {"blocks": node_obj.blocks, "line": node_obj.line}, full_scene_context)
                
                for sem_node in extracted:
                    # 'lines' mode shares the compact structural boundaries of 'nodes' mode
                    if map_style in ["nodes", "lines"]:
                        sem_node.width = 50
                        sem_node.height = 50
                    else:
                        txt = getattr(sem_node, 'title', getattr(sem_node, 'label', "unknown"))
                        w, h = measure_card_size(txt, mindmap_cfg["card"])
                        sem_node.width = w
                        sem_node.height = h
                semantic_nodes.extend(extracted)

            # 2. Layout Calculation
            raw_edges = build_semantic_flow_edges({"scene_name": scene_name, "tags": scene_data_map}, semantic_nodes)

            # Create NEW edge objects with normalized IDs instead of modifying them
            from storygraph.diagram.diagram_edges import DiagramEdge 

            scene_edges = []
            for e in raw_edges:

                # SKIP SELF-LOOPS: If normalization made from/to identical, 
                # internal bounce don't need to draw.
                if e.from_id == e.to_id:
                    continue
                # Use the constructor to build a fresh, clean edge
                clean_edge = DiagramEdge(
                    from_id=e.from_id,
                    to_id=e.to_id,
                    label=getattr(e, 'label', None)
                )
                scene_edges.append(clean_edge)

            diag_nodes, diag_edges = build_diagram_graph(semantic_nodes, scene_edges)

            # Transfer measured sizes from semantic_nodes to diagram_nodes
            sem_lookup = {n.id: n for n in semantic_nodes}
            for dn in diag_nodes:
                sn = sem_lookup.get(dn.id)
                if sn:
                    dn.width = sn.width
                    dn.height = sn.height
                else:
                    # Fallback if not found
                    dn.width = 180
                    dn.height = 45
            
            # Handle different map styles sizing configurations
            layout_config = mindmap_cfg.copy()
            if map_style in ["nodes", "lines"]:
                layout_config["layout"] = {
                    "col_width": 60, "vert_gap": 40, "bundle_gap": 70, "col_pad": 20
                }
                scene_layout = compute_flow_layout(diag_nodes, diag_edges, config=layout_config)
            else:
                scene_layout = compute_flow_layout(diag_nodes, diag_edges, config=mindmap_cfg)

            if not scene_layout: continue

            # 3. FINALIZE NODE DATA
            chapter_start_x = current_x_offset
            scene_max_width = 0
            scene_lookup = {} 

            for n in diag_nodes:
                box = scene_layout.get(n.id)
                if box:
                    final_x = box.x + current_x_offset
                    node_text = getattr(n, 'title', "unknown")
                    
                    node_data = {
                        "id": n.id,
                        "x": final_x,
                        "y": box.y,
                        "width": box.width,  
                        "height": box.height, 
                        "text": node_text,
                        "scene": scene_name,
                        "style": map_style
                    }
                    all_web_nodes.append(node_data)
                    scene_lookup[n.id] = node_data
                    manifest[n.id] = n.id
                    scene_max_width = max(scene_max_width, box.x + box.width)

            # --- 4. CALCULATE EDGES (With Absolute Center Convergence) ---
            for e in diag_edges:
                src = scene_lookup.get(e.from_id)
                dst = scene_lookup.get(e.to_id)
                
                if src and dst:
                    is_return = dst['x'] <= src['x']

                    if map_style == "lines":
                        # CRITICAL: Structural paths bypass node boundaries and meet at the center
                        sX = src['x'] + (src['width'] / 2)
                        sY = src['y'] + (src['height'] / 2)
                        eX = dst['x'] + (dst['width'] / 2)
                        eY = dst['y'] + (dst['height'] / 2)
                        edge_type = "return" if is_return else "forward"
                    else:
                        # Standard edge-docking parameters for Cards and Nodes layouts
                        if is_return:
                            sX = src['x'] + (src['width'] / 2)
                            sY = src['y']
                            eX = dst['x'] + (dst['width'] / 2)
                            eY = dst['y']
                            edge_type = "return"
                        else:
                            sX = src['x'] + src['width']
                            sY = src['y'] + (src['height'] / 2)
                            eX = dst['x']
                            eY = dst['y'] + (dst['height'] / 2)
                            edge_type = "forward"

                    all_web_edges.append({
                        "from": e.from_id,
                        "to": e.to_id,
                        "startX": sX,
                        "startY": sY,
                        "endX": eX,
                        "endY": eY,
                        "type": edge_type  
                    })

            current_x_offset += scene_max_width + padding

        # --- CROSS-CHAPTER -next EDGES (full map only) ---
        # Build lookup: scene_name -> first web node (lowest x then y)
        scene_first_node = {}
        for node_data in all_web_nodes:
            sc = node_data["scene"]
            if sc not in scene_first_node:
                scene_first_node[sc] = node_data
            else:
                prev = scene_first_node[sc]
                if (node_data["x"], node_data["y"]) < (prev["x"], prev["y"]):
                    scene_first_node[sc] = node_data

        # Build lookup: node_id -> scene_name (from all_web_nodes)
        node_id_to_scene = {nd["id"]: nd["scene"] for nd in all_web_nodes}
        # Build lookup: scene_name -> web node dict by id
        node_id_to_data = {nd["id"]: nd for nd in all_web_nodes}

        # Ordered non-excluded scenes actually rendered
        rendered_scenes = [
            s for s in ordered_scenes
            if s in runtime.scenes and s not in exclude_set
        ]

        for web_node in all_web_nodes:
            scene_name = web_node["scene"]
            node_id = web_node["id"]

            # Find the original scene/tag to inspect blocks
            # node_id format: "scene::tag" or deeper
            parts = node_id.split("::")
            if len(parts) < 2:
                continue
            scene_obj = runtime.scenes.get(scene_name)
            if not scene_obj:
                continue

            # Check if ANY block in the underlying tag has cmd="-next"
            # The node_id may be a tag node or a child; walk up to find the tag
            tag_name = parts[1] if len(parts) >= 2 else None
            if not tag_name:
                continue
            tag_node = scene_obj.nodes.get(tag_name)
            if not tag_node:
                continue

            has_next = any(
                b.get("cmd") == "-next"
                for b in getattr(tag_node, "blocks", [])
            )
            if not has_next:
                continue

            # Find next rendered scene
            try:
                sc_idx = rendered_scenes.index(scene_name)
            except ValueError:
                continue
            if sc_idx + 1 >= len(rendered_scenes):
                continue
            next_scene = rendered_scenes[sc_idx + 1]
            dst = scene_first_node.get(next_scene)
            if not dst:
                continue

            src = web_node
            if map_style == "lines":
                sX = src["x"] + src["width"] / 2
                sY = src["y"] + src["height"] / 2
                eX = dst["x"] + dst["width"] / 2
                eY = dst["y"] + dst["height"] / 2
            else:
                sX = src["x"] + src["width"]
                sY = src["y"] + src["height"] / 2
                eX = dst["x"]
                eY = dst["y"] + dst["height"] / 2

            all_web_edges.append({
                "from": src["id"],
                "to": dst["id"],
                "startX": sX,
                "startY": sY,
                "endX": eX,
                "endY": eY,
                "type": "next",
            })

        return {"canvas": {"nodes": all_web_nodes, "edges": all_web_edges}, "chapters": chapters, "manifest": manifest}

