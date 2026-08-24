import re
from copy import deepcopy


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
VALID_NUMERIC_COLORS = {str(i) for i in range(1, 7)}


def validate_mindmap_config(cfg, *, known_vars=None):
    """
    Validate mindmap_config.json.

    - Never raises
    - Prints warnings
    - Returns a sanitized copy of cfg
    """

    if not cfg or not isinstance(cfg, dict):
        return cfg

    cfg = deepcopy(cfg)

    if known_vars is None:
        known_vars = set()

    _validate_card_config(cfg)
    _validate_layout_config(cfg)
    _validate_variable_colors(cfg, known_vars)

    return cfg


# --------------------------------------------------
# Card config validation
# --------------------------------------------------

def _validate_card_config(cfg):
    card = cfg.get("card")
    if not isinstance(card, dict):
        return

    def warn(msg):
        print(f"[config warning] {msg}")

    def clamp_int(key, min_val=None):
        val = card.get(key)
        if val is None:
            return
        if not isinstance(val, (int, float)):
            warn(f"card.{key} must be a number")
            card.pop(key, None)
            return
        if min_val is not None and val < min_val:
            warn(f"card.{key} < {min_val}, clamped")
            card[key] = min_val

    clamp_int("min_width", 0)
    clamp_int("max_width", 0)
    clamp_int("padding_x", 0)
    clamp_int("padding_y", 0)
    clamp_int("line_height", 1)
    clamp_int("char_width", 1)

    min_w = card.get("min_width")
    max_w = card.get("max_width")

    if (
        isinstance(min_w, (int, float))
        and isinstance(max_w, (int, float))
        and min_w > max_w
    ):
        warn("card.min_width > card.max_width (swapping values)")
        card["min_width"], card["max_width"] = max_w, min_w


# --------------------------------------------------
# Variable color rules validation
# --------------------------------------------------

def _validate_variable_colors(cfg, known_vars):
    rules = cfg.get("variable_colors")
    if not isinstance(rules, list):
        return

    def warn(msg):
        print(f"[config warning] {msg}")

    valid_rules = []

    for idx, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            warn(f"variable_colors rule #{idx} is not an object")
            continue

        var = rule.get("var")
        color = rule.get("color")
        ops = rule.get("ops")

        # --- var ---
        if not isinstance(var, str):
            warn(f"variable_colors rule #{idx}: 'var' must be a string")
            continue

        if known_vars and var not in known_vars:
            warn(
                f"variable_colors rule #{idx}: unknown variable '{var}'"
            )
            continue

        # --- color ---
        if not _is_valid_color(color):
            warn(
                f"variable_colors rule #{idx}: invalid color '{color}'"
            )
            continue

        # --- ops ---
        if ops is not None:
            if (
                not isinstance(ops, list)
                or not all(isinstance(o, str) for o in ops)
            ):
                warn(
                    f"variable_colors rule #{idx}: 'ops' must be a list of strings"
                )
                continue

        valid_rules.append(rule)

    cfg["variable_colors"] = valid_rules

def _validate_layout_config(cfg):
    layout = cfg.get("layout")
    if not isinstance(layout, dict):
        return

    def warn(msg):
        print(f"[config warning] {msg}")

    def clamp_int(key, min_val=0):
        val = layout.get(key)
        if val is None: return
        if not isinstance(val, (int, float)):
            warn(f"layout.{key} must be a number")
            layout.pop(key, None)
            return
        if val < min_val:
            layout[key] = min_val

    # Validate our specific flow keys
    clamp_int("col_width", 100)
    clamp_int("min_col_width", 50)
    clamp_int("col_pad", 0)
    clamp_int("vert_gap", 0)
    clamp_int("bundle_gap", 0)

def _is_valid_color(color):
    if not isinstance(color, str):
        return False

    if color in VALID_NUMERIC_COLORS:
        return True

    if HEX_COLOR_RE.match(color):
        return True

    return False
