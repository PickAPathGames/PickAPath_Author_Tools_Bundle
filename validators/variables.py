# validators/variables.py
"""
VariableSummary

Responsibilities:
 - Track global declared variables seen by the validator (note_declared may be used by higher-level code)
 - Track used variable names found in expressions / interpolations
 - Track temporary (per scene:tag) declarations created with -tvar
 - Provide a robust analyze_block_args(cmd, args, scene_tag, line) used by validator runtime
 - Provide scan_expr_for_names(expr) helper that uses validators.expr.extract_names with a conservative fallback

Important semantic rule implemented here:
 - -mvar / -set / -add / -sub / -mset / -madd / -msub are MUTATION/USAGE commands and DO NOT declare a global variable.
 - -tvar declares a temporary variable (scoped to scene_tag) and is recorded as such.
 - This module does NOT attempt to read the project's config -var declarations; that is the caller's job.
"""

from typing import Dict, Any, Set, Tuple, List

from .expr import parse_mvar_args, extract_names, ExprError
from engine.command_kinds import BARE_MUTATION_CMDS as _MUTATION_CMDS
from engine.command_kinds import BARE_DECLARATION_CMDS as _DECLARATION_CMDS
from engine.command_kinds import BARE_CONDITIONAL_CMDS as _CONDITION_CMDS
from parser.constants import _BARE_NAME_RE, _INTERP_RE


class VariableSummary:
    """
    Simple summary object tracking declared/used/temp variables.
    Public fields:
      - declared: Dict[name -> line]  (populated by external callers or by this module for temp declarations)
      - used: Set[name]
      - used_locs: Dict[name -> List[(scene, tag, line)] ]  (optional locations)
      - temp_declared: Dict[(scene,tag) -> Dict[name -> line]]
      - types: Dict[name -> type_str]  (optional hints)
    """
    def __init__(self):
        self.declared: Dict[str, Any] = {}
        self.declared_locs = {}
        self.declared = {}
        self.used: Set[str] = set()
        self.used_locs: Dict[str, List[Tuple[str,str,int]]] = {}
        self.temp_declared: Dict[Tuple[str,str], Dict[str, Any]] = {}
        self.types: Dict[str, str] = {}  # "bool"|"int"|None
        self.temp_before_decl: set[str] = set()

    # ----- Basic recorders -----
    def note_declared(self, name: str, line=None, vtype: str=None):
        """Record a (global) declared variable. Caller (config loader) is expected to call this."""
        if name:
            self.declared.setdefault(name, line)
            if vtype:
                self.types.setdefault(name, vtype)

    def note_used(self, name, source=None):
        """
        Record a variable use. Accept flexible 'source' formats:
          - None
          - int/str  -> treated as line number (scene/tag unknown)
          - (scene, tag) -> line unknown
          - (scene, tag, line) -> full info
        Stores usages in self.used and optionally self.used_locs[name] as (scene, tag, line)
        """
        if not name:
            return
        self.used.add(name)

        if source is None:
            return

        # Normalize into (scene, tag, line)
        scene = None
        tag = None
        line = None
        try:
            # tuple/list-like (scene,tag,line) or (scene,tag)
            if isinstance(source, (tuple, list)):
                if len(source) == 3:
                    scene, tag, line = source
                elif len(source) == 2:
                    scene, tag = source
                    line = None
                elif len(source) == 1:
                    line = source[0]
                else:
                    # unexpected length - stick last element as line
                    line = source[-1]
            else:
                # single value (likely an int line or string)
                if isinstance(source, (int, str)):
                    line = source
                else:
                    # fallback: try to use repr of source as line-ish info
                    line = source
        except Exception:
            # be defensive - fallback to storing raw source in line slot
            line = source

        self.used_locs.setdefault(name, []).append((scene, tag, line))


    def note_temp(self, scene_tag: Tuple[str,str], name: str, line=None):
        """Record a temporary declaration local to (scene,tag)."""
        if not scene_tag or not name:
            return
        self.temp_declared.setdefault(scene_tag, {})[name] = line

    def note_type(self, name: str, vtype: str):
        """Suggest a variable type if known."""
        if name and vtype:
            if name not in self.types or self.types[name] is None:
                self.types[name] = vtype

    # ----- Expression scanning helpers -----
    def scan_expr_for_names(self, expr: str):
        """
        Use the expression parser (extract_names) when possible; fall back to a conservative
        bare-identifier extractor when parsing fails - *but* only when the string looks like
        an actual expression (contains operators, parentheses, or numeric literals).
        This avoids counting ordinary story text as variable usage.
        """
        if not expr:
            return
        try:
            names = set(extract_names(expr))
            for n in names:
                self.note_used(n)
        except ExprError:
            # Fallback only when expr looks like an expression:
            if re.search(r"[+\-*/%()<>!=]", expr) or re.search(r"\b\d+\b", expr):
                for n in _BARE_NAME_RE.findall(expr or ""):
                    self.note_used(n)
            # otherwise it's likely plain text/label - don't treat bare words as variables


    def _scan_interpolations(self, text: str):
        """
        Extract names from ${...} / $${...} / @{...} interpolation blocks and record them used.
        """
        if not text:
            return
        for m in _INTERP_RE.finditer(text):
            expr = m.group(1) or m.group(2) or m.group(3) or ""
            self.scan_expr_for_names(expr)

    # ----- Block argument analyzer (main entry) -----
    def analyze_block_args(self, cmd, args, scene_tag, line=None):
        if not cmd:
            # Plain story text - only check ${...} interpolations
            self._scan_interpolations(args)
            return

        name = cmd.lstrip("-").strip()
        args = (args or "").strip()

        # 1) tvar declaration
        if name in _DECLARATION_CMDS:
            try:
                varname, op, rhs = parse_mvar_args(args)
                self.note_temp(scene_tag, varname, line)
                
                # Guess the type of the RHS to avoid "any"
                if rhs:
                    if rhs.strip().lower() in ("true", "false"):
                        self.note_type(varname, "bool")
                    elif re.match(r"^-?\d+$", rhs.strip()):
                        self.note_type(varname, "int")
                    elif re.match(r"^-?\d+\.\d+$", rhs.strip()):
                        self.note_type(varname, "float")
                    elif rhs.strip().startswith('"'):
                        self.note_type(varname, "str")
                
                self.scan_expr_for_names(rhs)
            except ExprError:
                m = _BARE_NAME_RE.match(args or "")
                if m:
                    varname = m.group(1)
                    self.note_temp(scene_tag, varname, line)
                    parts = (args or "").split(None, 1)
                    if len(parts) > 1:
                        self.scan_expr_for_names(parts[1])
            return

        # 2) mutation commands (never declare)
        if name in _MUTATION_CMDS:
            try:
                varname, op, rhs = parse_mvar_args(args)
                self.note_used(varname, (scene_tag[0], scene_tag[1], line))
                self.scan_expr_for_names(rhs)
            except ExprError:
                m = _BARE_NAME_RE.match(args or "")
                if m:
                    varname = m.group(1)
                    self.note_used(varname, (scene_tag[0], scene_tag[1], line))
                for n in _BARE_NAME_RE.findall(args or ""):
                    self.note_used(n, (scene_tag[0], scene_tag[1], line))
            return

        # 3) logical conditions
        if name in _CONDITION_CMDS:
            names = []
            try:
                names = extract_names(args)
            except ExprError:
                if re.search(r"[+\-*/%()<>!=]", args) or re.search(r"\b\d+\b", args):
                    names = _BARE_NAME_RE.findall(args or "")

            for n in names:
                self.note_used(n, (scene_tag[0], scene_tag[1], line))
            return


        # 4) For anything else (commands with text arguments),
        # ONLY scan interpolation blocks - DO NOT treat bare words as variables.
        if args:
            self._scan_interpolations(args)


    # ----- Merge helper -----
    def merge(self, other: 'VariableSummary'):
        """
        Merge another VariableSummary into self. Used when scanning fragments independently.
        """
        for k, v in (other.declared or {}).items():
            self.declared.setdefault(k, v)
        self.used |= set(other.used or set())
        # merge temp_declared: keep existing entries and update
        for st, mapping in (other.temp_declared or {}).items():
            self.temp_declared.setdefault(st, {}).update(mapping)
        for k, v in (other.types or {}).items():
            if k not in self.types or self.types[k] is None:
                self.types[k] = v


    def is_temp_declared_in_chapter(self, chapter: str, name: str) -> bool:
        """Checks if a variable was declared as a tvar anywhere in the given chapter."""
        for (sc, tg), mapping in self.temp_declared.items():
            if sc == chapter:
                if name in mapping:
                    return True
        return False