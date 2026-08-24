# engine/command_kinds.py
"""
Single source of truth for command category sets.

Two naming conventions exist across the codebase:
  - DASH prefix  (e.g. "-go")  - used by structure_rules, flow_sensitive_temp
  - BARE name    (e.g. "go")   - used by storygraph, validator_runtime expr checks, loader

"""

# ---------------------------------------------------------------------------
# DASH-PREFIX sets  (structure_rules, validators/flow_sensitive_temp)
# ---------------------------------------------------------------------------

# Commands that end a node's flow unconditionally
TERMINAL_CMDS = {
    "-go",
    "-end",
    "-next",
    "-go_back",
    "-go_and_back",
    "-go_file",
}

# Commands that open a branching block (require indented children)
BRANCHING_CMDS = {
    "-pick",
    "-pick_once",
    "-if",
    "-elseif",
    "-else",
    "-single_pick",
}

# Commands that define structure (validator tracks these specially)
STRUCTURAL_CMDS = {
    "-tag",
    "-if",
    "-elseif",
    "-else",
    "-pick",
    "-pick_once",   # kept for safety during transition
    "#choice",
}

# Leaf commands, take args on same line, no indented block beneath them.
# Used by structure_rules._check_branch to validate indent position.
NON_BLOCK_COMMANDS = {
    # Flow / navigation
    "-go",
    "-go_back",
    "-go_and_back",
    "-go_file",
    "-next",
    "-end",
    "-pause",
    "-author_mode",
    # Variable ops
    "-set",
    "-mvar",
    "-tvar",
    "-add",
    "-sub",
    "-mset",
    "-madd",
    "-msub",
    "-%+",
    "-%-",
    "-toggle",
    "-winner",
    "-loser",
    "-average",
    "-range",
    "-middle",
    "-entropy",
    # String / list ops
    "-upper",
    "-lower",
    "-naming",
    "-list",
    "-sort_asc",
    "-sort_des",
    "-reverse",
    # UI / display
    "-nl",
    "-pic",
    "-stat_header",
    "-stat_row",
    "-stat_bar",
    "-stat_item",
    "-stat_vs",
    "-stat_break",
    "-stat_block_end",
    "-bg_color",
    "-body_color",
    "-global_max_percentage",
    "-permanent_stat",
    "-remove_permanent_stat",
    # Input
    "-user_input",
    # Map / meta
    "-map_mode",
    "-map_style",
    "-map_exclude",
    "-cheat_mode",
    "-title",
    "-author",
    "-version",
    # Goals / save
    "-reach_goal",
    "-save_checkpoint",
    "-load_checkpoint",
    "-snapshot",
    "-turn_around",
}

# Commands whose args should NOT be scanned for variable names
# (flow_sensitive_temp, loader)
NON_VAR_ARG_CMDS = {
    "-go",
    "-go_and_back",
    "-go_file",
    "-next",
    "-tag",
    "-author",
    "-author_mode",
    "-cheat_mode",
    "-title",
    "-version",
    "-pic",
    "-stat_header",
    "-stat_row",
    "-stat_bar",
    "-stat_vs",
    "-stat_break",
    "-stat_block",
    "-stat_block_end",
    "-bg_color",
    "-body_color",
    "-permanent_stat",
    "-remove_permanent_stat",
    "-global_max_percentage",
    "-map_mode",
    "-map_style",
    "-map_exclude",
    "-reach_goal",
    "-save_checkpoint",
    "-load_checkpoint",
    "-snapshot",
    "-turn_around",
}

# Temp-var declaration commands (flow_sensitive_temp)
TVAR_CMDS = {"-tvar"}

# Mutation/use commands that require prior declaration (flow_sensitive_temp)
USE_CMDS = {
    "-mvar",
    "-set",
    "-mset",
    "-add",
    "-sub",
    "-madd",
    "-msub",
    "-%+",
    "-%-",
    "-toggle",
}

# Condition commands whose args contain boolean expressions (flow_sensitive_temp)
COND_CMDS = {"-if", "-elseif", "-pick_if"}

# Write commands, declare or overwrite a variable value (loader)
WRITE_CMDS = {
    "-var",
    "-mvar",
    "-tvar",
    "-set",
    "-mset",
    "-madd",
    "-msub",
    "-add",
    "-sub",
    "-fairmath_add",
    "-fairmath_sub",
}

# Mutate commands, both read and write (loader)
MUTATE_CMDS = {
    "-mvar",
    "-madd",
    "-msub",
    "-add",
    "-sub",
    "-fairmath_add",
    "-fairmath_sub",
}

# ---------------------------------------------------------------------------
# BARE-NAME sets  (storygraph, validator_runtime expr checks, loader)
# ---------------------------------------------------------------------------

# Terminals in storygraph flow analysis
BARE_TERMINAL_CMDS = {
    "go",
    "go_file",
    "end",
    "next",
    "go_back",
}

# Subroutine / pointer commands (storygraph)
BARE_SUBROUTINE_CMDS = {"go_and_back"}

# Inline-only navigation (storygraph/extract_semantic)
BARE_INLINE_CMDS = {"go"}

# Condition commands, bare (validator_runtime static expr check)
BARE_CONDITIONAL_CMDS = {"if", "elseif", "pick_if"}

# Mutation commands, bare (validator_runtime, variables.py)
BARE_MUTATION_CMDS = {
    "mvar", "mset", "madd", "msub",
    "set", "add", "sub",
    "fairmath_add", "fairmath_sub",
    "%+", "%-",
}

# Declaration commands, bare (variables.py)
BARE_DECLARATION_CMDS = {"tvar"}

# Nav commands, bare (loader)
BARE_NAV_CMDS = {
    "go", "go_file", "go_and_back",
    "next", "go_back",
    "cont", "link_to", "goto",
}

# Commands whose args contain variable-like context (loader)
BARE_VAR_CONTEXT_CMDS = {
    "var", "mvar", "tvar",
    "set", "mset",
    "madd", "msub", "add", "sub",
    "fairmath_add", "fairmath_sub",
    "pick_if", "if", "elseif",
}

# ---------------------------------------------------------------------------
# Shared non-command tokens
# ---------------------------------------------------------------------------

# Python builtins allowed in expressions (not variable names)
BUILTIN_WORDS = {
    "str", "int", "float", "bool",
    "min", "max", "round", "abs",
    "len", "True", "False", "None",
    "and", "or", "not",
}

