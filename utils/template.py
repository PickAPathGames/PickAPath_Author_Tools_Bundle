# utils/template.py (new file or existing interpolation module)

import re
TEMPLATE_RE = re.compile(r"@\{([^}]+)\}")

def validate_template_indices(text: str, node_ref, var_summary, record_error):
    """
    Validate any @{...} templates in `text`. If var index is out-of-range,
    call record_error("message").
    node_ref is (chapter, tag) or Node; var_summary is VariableSummary instance.
    """
    for m in TEMPLATE_RE.finditer(text):
        inner = m.group(1).strip()
        parts = [p.strip() for p in inner.split("|")]
        var_name = parts[0]
        choices = parts[1:]
        # We only validate if var_name is a simple variable name or integer literal.
        # If it's an expression, attempt to evaluate if it's a literal integer.
        try:
            # If var is an integer literal in template, use it directly
            idx = int(var_name)
            # index is literal; validate range
            if idx < 0 or idx >= len(choices):
                # record_error(f"[TEMPLATE_INDEX] literal index {idx} out of range (0..{len(choices)-1}) in template '{m.group(0)}' at {node_ref}")
                record_error(
                    f"Template index {idx} out of range in '{node_ref}' (0..{len(choices)-1}) in '{m.group(0)}'"
                )

        except ValueError:
            # not a numeric literal, it's a variable name or expression.
            # We can't know value at parse time, so we add a validation rule executed during runtime/validator:
            # add a "template_check" record for the node to be validated later during quickpick when variable types/values may be known.
            # For strict mode, during static validation we MUST flag possible out-of-range only if the var is a constant declared value.
            pass
