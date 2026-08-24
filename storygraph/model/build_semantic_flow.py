"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


from .semantic_flow_edges import SemanticFlowEdge
from collections import defaultdict

 
TERMINAL_CMDS = {"go", "go_file", "end", "next", "go_back", "finish"}
SUBROUTINE_CMDS = {"go_and_back"}
 
def build_semantic_flow_edges(parsed_scene, semantic_nodes):
    scene_name = parsed_scene["scene_name"]
    tag_index = {}
    ordered_tags = list(parsed_scene.get("tags", {}).keys())
    
    for n in semantic_nodes:
        if n.kind in ("tag", "resume"):
            tag_index[(n.scene, n.tag)] = n.id
    
    edges = []

    for node in semantic_nodes:
        if node.scene != scene_name: 
            continue
 
        # 1. THE TETHER (Internal structure)
        if node.parent_id:
            edges.append(SemanticFlowEdge(
                from_id=node.parent_id, to_id=node.id, kind="structure", line=node.line
            ))
 
        # 2. THE JUMP (Explicit -go commands)
        targets = extract_go_targets(node)
        for tgt_scene, tgt_tag, cmd, raw_block in targets:
            actual_scene = tgt_scene if tgt_scene else scene_name
            tgt_id = tag_index.get((actual_scene, tgt_tag)) or f"external::{actual_scene}::{tgt_tag}"
            
            edges.append(SemanticFlowEdge(
                from_id=node.id, to_id=tgt_id, kind="go", line=node.line, label=raw_block
            ))
 
        # 3. Handle single_pick fallthrough (Option nodes that leak)
        if node.kind == "option" and getattr(node, "is_single_pick_child", False) and not targets:
            try:
                current_tag_index = ordered_tags.index(node.tag)
                next_tag_in_file = ordered_tags[current_tag_index + 1] if current_tag_index + 1 < len(ordered_tags) else None
                if next_tag_in_file:
                    dst_id = tag_index.get((scene_name, next_tag_in_file))
                    if dst_id:
                        edges.append(SemanticFlowEdge(
                            from_id=node.id, to_id=dst_id, kind="flow", line=node.line, label="[auto-next]"
                        ))
            except ValueError:
                pass
 
    # 4. Add Implicit Fallthrough Edges
    for tag_name, tag_data in parsed_scene.get("tags", {}).items():
        if tag_name.startswith("__sys_"): continue
 
        src_id = tag_index.get((scene_name, tag_name))
        if not src_id: continue
 
        for cont in tag_data.get("continuations", []):
            tgt_scene, tgt_tag, is_subroutine, resume_tag = cont
            if is_subroutine: continue
            if not tgt_tag or tgt_tag == "__NEXT__": continue
 
            actual_scene = tgt_scene if tgt_scene else scene_name
            dst_id = tag_index.get((actual_scene, tgt_tag))
            if dst_id and dst_id != src_id:
                edges.append(SemanticFlowEdge(
                    from_id=src_id, to_id=dst_id, kind="flow", line=tag_data.get("line"), label="[fallthrough]"
                ))
 
    # Adjacency list to traverse structural flows
    outgoing = defaultdict(list)
    for e in edges:
        outgoing[e.from_id].append(e.to_id)
        
    nodes_by_id = {n.id: n for n in semantic_nodes}

    for node in semantic_nodes:
        if node.scene != scene_name: continue
        if node.kind != "jump": continue
 
        resume_tag = (node.meta or {}).get("resume_tag")
        if not resume_tag: continue
 
        parts = node.label.strip().split()
        if len(parts) == 2:
            target_id = tag_index.get((scene_name, parts[1]))
        else:
            target_id = None
 
        resume_id = tag_index.get((scene_name, resume_tag))
 
        if target_id:
            # 1. Forward Call
            edges.append(SemanticFlowEdge(
                from_id=node.id, to_id=target_id, kind="subroutine", label="[call]", line=node.line
            ))
            
            # 2. Dynamic Return (BFS now successfully hits the distinct -go_back nodes)
            go_backs = set()
            visited = set()
            queue = [target_id]
            while queue:
                curr = queue.pop(0)
                if curr in visited: continue
                visited.add(curr)
                
                curr_node = nodes_by_id.get(curr)
                if curr_node and "-go_back" in curr_node.label:
                    go_backs.add(curr)
                    continue
                    
                for nxt in outgoing[curr]:
                    queue.append(nxt)
                    
            if go_backs:
                for gb in go_backs:
                    edges.append(SemanticFlowEdge(
                        from_id=gb, to_id=node.id, kind="return", label="[return]", line=node.line
                    ))
            else:
                edges.append(SemanticFlowEdge(
                    from_id=target_id, to_id=node.id, kind="return", label="[return]", line=node.line
                ))
 
        # 3. Resume connection post-subroutine
        if resume_id:
            edges.append(SemanticFlowEdge(
                from_id=node.id, to_id=resume_id, kind="flow", label="[resume]", line=node.line
            ))
 
    return edges
 
def is_flow_exhausted(blocks):
    for b in blocks:
        if isinstance(b, dict):
            cmd = b.get("cmd", "").lstrip("-")
        elif isinstance(b, str) and b.strip().startswith("-"):
            cmd = b.strip().split()[0].lstrip("-")
        else:
            continue
            
        if cmd in TERMINAL_CMDS:
            return True
        if cmd == "if":
            return check_conditional_exhaustion(blocks, blocks.index(b))
        if cmd in ("pick", "pick_once"):
            return True
        if cmd == "single_pick":
            return False 
    return False
 
def check_conditional_exhaustion(blocks, if_index):
    has_else = False
    branches_terminate = []
    
    i = if_index
    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, dict):
            cmd = b.get("cmd", "").lstrip("-")
        else:
            break
 
        if cmd in ("if", "elseif", "else"):
            if cmd == "else": has_else = True
            inner_blocks = b.get("node", {}).get("blocks", [])
            branches_terminate.append(is_flow_exhausted(inner_blocks))
        else:
            break
        i += 1
        
    return has_else and all(branches_terminate)
 
def extract_go_targets(semantic_node):
    targets = []
    for b in semantic_node.blocks:
        if isinstance(b, str) and b.startswith("-go"):
            parts = b.split()
            if len(parts) > 1:
                if parts[0] == "-go_file" and len(parts) >= 3:
                    targets.append((parts[1], parts[2], "go", b))
                else:
                    targets.append((semantic_node.scene, parts[1], "go", b))
        elif isinstance(b, dict) and b.get("cmd", "").startswith("-go"):
            cmd = b["cmd"]
            args = b.get("args", "").split()
            if args:
                if cmd == "-go_file" and len(args) >= 2:
                    targets.append((args[0], args[1], "go", f"{cmd} {b['args']}"))
                else:
                    targets.append((semantic_node.scene, args[0], "go", f"{cmd} {b['args']}"))
    return targets
 
 
