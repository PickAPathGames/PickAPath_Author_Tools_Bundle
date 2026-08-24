# parser/constants.py
import re

INDENT_RE = re.compile(r"^(?P<indent>\s*)(?P<line>.*)$")
CMD_RE = re.compile(r"^\s*-(?P<cmd>[A-Za-z0-9_%\+-]+)(?:[ \t]+(?P<rest>.*))?$")
TAG_RE = re.compile(r"^-tag\s+(?P<tag>\S+)")
_VARNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEMPLATE_EXPR_RE = re.compile(r"\$\{([^}]*)\}")
FLOW_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

# Regex fallback to extract bare identifiers conservatively
_BARE_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_INTERP_RE = re.compile(r"\$\{([^}]+)\}|\$\$\{([^}]+)\}|@\{([^}]+)\}")
