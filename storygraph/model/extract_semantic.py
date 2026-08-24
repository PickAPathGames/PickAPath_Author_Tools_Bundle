# storygraph/model/extract_semantic.py
from .semantic_nodes import SemanticNode, semantic_id
import hashlib
from engine.command_kinds import TERMINAL_CMDS as _DASH_TERMINALS, BARE_TERMINAL_CMDS as TERMINAL_CMDS
 
POINTER_FLOW_CMDS = {"-go_and_back"}
 
 
def get_content_hash(text, length=6):
    return hashlib.blake2b(text.encode(), digest_size=3).hexdigest()
 
def extract_semantic_from_tag(scene_name, tag_name, tag_data, parsed_scene, all_nodes_in_scene=None):
    nodes = []
 
    if tag_name.startswith("__sys_"):
        return []
 
    is_resume = tag_name.startswith("__res_")
    tag_node = SemanticNode(
        id=semantic_id(scene_name, tag_name),
        kind="resume" if is_resume else "tag",
        label="[resume]" if is_resume else f"-tag {tag_name}",
        scene=scene_name,
        tag=tag_name,
        line=tag_data.get("line"),
    )
    nodes.append(tag_node)
 
    walk_blocks(
        blocks=tag_data.get("blocks", []),
        scene=scene_name,
        tag=tag_name,
        parent=tag_node,
        nodes=nodes,
        node_map=all_nodes_in_scene or {},
        parsed_scene=parsed_scene
    )
 
    return nodes
 

def walk_blocks(blocks, scene, tag, parent, nodes, node_map, parsed_scene=None):
    buffer = []
    current_anchor = parent # Track the "leaf" of the current structural chain
 
    def flush_ops():
        if not buffer: return
        for b in buffer:
            if b.get("cmd") == "-nl": continue
            current_anchor.blocks.append(render_block(b))
        buffer.clear()
 
    i = 0
    while i < len(blocks):
        block = blocks[i]
        cmd = block.get("cmd")
 
        # CONDITIONALS
        if cmd in ("-if", "-elseif", "-else"):
            flush_ops()
            chain = []
            while i < len(blocks) and blocks[i].get("cmd") in ("-if", "-elseif", "-else"):
                chain.append(blocks[i])
                i += 1
            # Update the anchor using the balanced layout chain handler
            current_anchor = handle_conditional_chain(chain, scene, tag, current_anchor, nodes, node_map)
            continue
 
        # PICKS
        if cmd in ("-pick", "-pick_once", "-pick_if", "-single_pick"):
            flush_ops()
            handle_pick(block, scene, tag, current_anchor, nodes, node_map)
            i += 1
            continue
 
        # JUMPS / TERMINALS (Exclude standard inline 'go' commands)
        if (cmd in POINTER_FLOW_CMDS or (cmd and cmd.lstrip("-") in TERMINAL_CMDS)) and (cmd and cmd.lstrip("-") != "go"):
            flush_ops()
            node = SemanticNode(
                id=semantic_id(current_anchor.id, cmd, str(block.get("__line__"))),
                kind="jump" if cmd in POINTER_FLOW_CMDS else "terminal",
                label=render_block(block),
                scene=scene,
                tag=tag,
                line=block.get("__line__"),
                parent_id=current_anchor.id, # Attach sequentially to the current anchor
            )
            # Store the block inside the node so extract_go_targets can trace its destination
            node.blocks.append(block)

            if cmd == "-go_and_back" and block.get("resume_tag"):
                node.meta["resume_tag"] = block["resume_tag"]
            nodes.append(node)
            current_anchor = node # The jump/terminal becomes the new anchor
            i += 1
            continue
 
        # DEFAULT: Add to buffer (Standard -go lines land here and remain transparent)
        buffer.append(block)
        i += 1
 
    flush_ops()


def handle_conditional_chain(chain, scene, tag, parent, nodes, node_map):
    for block in chain:
        cmd = block.get("cmd", "-if")
        logic = block.get("args", "").strip()
        full_label = f"{cmd} {logic}".strip()
 
        node_id = block.get("u_id") 
        if not node_id:
            node_id = f"{parent.id}zzz{cmd.lstrip('-')}zzz{block.get('__line__')}"
 
        # Tie every conditional element to the original parent block to stack them vertically
        cond_node = SemanticNode(
            id=node_id,
            kind="logic", 
            label=full_label,
            scene=scene,
            tag=tag,
            line=block.get("line") or block.get("__line__"),
            parent_id=parent.id, 
        )
        nodes.append(cond_node)
        
        nested_content = block.get("node", {})
        if isinstance(nested_content, dict):
            blocks_to_walk = nested_content.get("blocks", [])
            # Nested logic elements inside the branch remain parented under this specific conditional card
            walk_blocks(blocks_to_walk, scene, tag, cond_node, nodes, node_map)
            
    # Return the original parent so inline checks do not pull subsequent nodes out to the right
    return parent
 

def render_choice_label(choice):
    text_list = choice.get("text", [])
    for item in text_list:
        if isinstance(item, dict) and item.get("type") == "choice_text":
            raw_text = item.get("text", "").strip()
            if not raw_text: continue
            return raw_text if raw_text.startswith("#") else f"#{raw_text}"
    return "#[choice]"

def handle_pick(block, scene, tag, parent, nodes, node_map):
    pick_id = block.get("pick_id") or block.get("u_id")
    is_single = (block.get("cmd") == "-single_pick")
    
    choices = block.get("node", {}).get("choices", [])
    
    for idx, choice in enumerate(choices):
        option_id = choice.get("choice_id") or f"{pick_id}zzzopt_idx_{idx}"
        
        option_node = SemanticNode(
            id=option_id,
            kind="option",
            label=render_choice_label(choice),
            scene=scene,
            tag=tag,
            line=choice.get("__line__"),
            parent_id=parent.id, 
        )
        option_node.is_single_pick_child = is_single 
        
        nodes.append(option_node)
        walk_blocks(choice.get("blocks", []), scene, tag, option_node, nodes, node_map)
 
 
def render_block(block):
    cmd = block.get("cmd", "")
    args = block.get("args")
    return f"{cmd} {args}".strip() if args is not None else cmd
