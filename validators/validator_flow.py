# validators/validator_flow.py
TERMINAL_COMMANDS = {
    "-go",
    "-go_back",
    "-go_and_back",
    "-end",
    "-finish",
    "-next",
    "-go_file",
}


class FlowError(Exception):
    def __init__(self, message, indent):
        super().__init__(message)
        self.indent = indent


def validate_node_flow(node, scene):
    if getattr(node, "meta", {}).get("generated"):
        return
        
    tag_rejoins = collect_rejoin_tags(node, scene)
    
    try:
        validate_block_list(
            blocks=node.blocks,
            indent=node.indent,
            tag_rejoins=tag_rejoins,
            context=f"{node.chapter}:{node.tag}",
            allow_outer_rejoin=True, 
        )
    except FlowError as e:
        if can_fallthrough(e.indent, tag_rejoins):
            return
            
        if hasattr(scene, "errors"):
            scene.errors.append({
                "line": getattr(node, "line", 0),
                "message": str(e),
                "severity": "error"
            })
        elif hasattr(scene, "diagnostics"):
            scene.diagnostics.append(str(e))
        else:
            raise


def collect_rejoin_tags(node, scene):
    """
    Finds the immediate next sequential node in the file.
    If the current node safely terminates, NO fall-through is possible.
    """
    rejoins = {}
    node_line = getattr(node, "line", -1)
    
    # 1. Check if the current node already ends in an explicit terminal command.
    # If it does, ANY fall-through down the file is completely blocked.
    if node.blocks:
        # Check the last structural block of this node
        last_blk = node.blocks[-1]
        if last_blk.get("cmd") in TERMINAL_COMMANDS:
            return rejoins # Returns empty: fall-through is impossible

    # 2. Find the single closest node that exists directly below this one
    next_node = None
    for other in scene.nodes.values():
        if other.chapter != node.chapter or other.tag == node.tag:
            continue
        other_line = getattr(other, "line", -1)
        if other_line > node_line:
            if next_node is None or other_line < getattr(next_node, "line", -1):
                next_node = other

    # 3. If there is a node directly below, it's the ONLY one we can fall into
    if next_node:
        rejoins.setdefault(next_node.indent, []).append(next_node.tag)

    return rejoins


def can_fallthrough(current_indent, tag_rejoins):
    # A path is safe if there's a tag at the same indentation level 
    # OR at a shallower level (outdented).
    return any(tag_level <= current_indent for tag_level in tag_rejoins)

def validate_block_list(blocks, indent, tag_rejoins, context, allow_outer_rejoin):
    if not blocks:
        return "CONTINUE"

    for blk in blocks:
        result = validate_block(
            blk,
            indent,
            tag_rejoins,
            context,
            allow_outer_rejoin,
        )
        # If we hit a terminal command anywhere in the list, the whole list is safe.
        if result == "TERMINATES":
            return "TERMINATES"

    # After checking all blocks, if we haven't hit a 'TERMINATES':
    # 1. If we are allowed to fall into a tag below us, do that.
    if allow_outer_rejoin and can_fallthrough(indent, tag_rejoins):
        return "TERMINATES"

    # 2. If this is a sub-list (inside an -if or -pick), return CONTINUE.
    # This allows the parent list to keep checking the next blocks.
    if not allow_outer_rejoin:
        return "CONTINUE"

    # 3. If we are at the root level of a tag and no blocks terminated,
    # and no tag is below us to catch the fall... ERROR.
    raise FlowError(
        f"{context}: execution path may fall through (indent {indent})",
        indent,
    )

def validate_block(blk, base_indent, tag_rejoins, context, allow_outer_rejoin):
    # Text and Variables always CONTINUE
    if blk.get("kind") == "text" or blk.get("type") == "blank" or "cmd" not in blk:
        return "CONTINUE"
    
    cmd = blk.get("cmd")
    indent = blk.get("__indent__", base_indent)

    if cmd in TERMINAL_COMMANDS:
        return "TERMINATES"

    if cmd in ("-pick", "-pick_once"):
        items = blk["node"].get("choices", []) + blk["node"].get("blocks", [])
        for item in items:
            blocks_to_check = item.get("blocks") or item.get("node", {}).get("blocks")
            if blocks_to_check:
                # We validate the INSIDE of the choice.
                # If a choice uses -go, it's fine.
                # If a choice is just text/vars, it will return CONTINUE.
                validate_block_list(
                    blocks_to_check,
                    indent + 1,
                    tag_rejoins,
                    context,
                    allow_outer_rejoin=False, 
                )
        return "TERMINATES"

    if cmd in ("-if", "-elseif", "-else"):
        # We process the inner blocks.
        # We pass allow_outer_rejoin=False because we don't want a 
        # sub-branch to claim it 'terminates' just by seeing a tag below.
        res = validate_block_list(
            blk["node"]["blocks"],
            indent + 1,
            tag_rejoins,
            context,
            allow_outer_rejoin=False, 
        )
        
        # KEY LOGIC: 
        # If the code INSIDE the if-branch terminates (e.g., -go or -go_back),
        # then this specific branch is terminal.
        # Otherwise, it's just a narrative branch that continues.
        return res

    return "CONTINUE"