# engine/runtime/interpolation.py
import re
from utils.safe_eval import safe_eval_expr

PATTERN = re.compile(
    r"\$\{(?P<val_expr>[^{}]+(?:\{[^}]*\}[^}]*)*)(?::(?P<fmt>[^}]+))?\}"
    r"|\$\$\{(?P<name_expr>[^}]+)\}"
    r"|@\{(?P<multi_expr>[^}]+)\}"
)


FORMAT_PATTERN = re.compile(
    r"\[(?P<tag>b|i|u|s|color|url)(?:=(?P<attr>[^\]]+))?\](?P<content>.*?)\[/(?P=tag)\]",
    re.DOTALL
)

def get_nested(data, expr):
    parts = re.split(r'\.(?![^\[]*\])', expr)
    current = data
    for part in parts:
        part = part.strip()
        if "[" in part:
            name, index = part.split("[", 1)
            index = index.rstrip("]")
            current = current.get(name) if isinstance(current, dict) else getattr(current, name, None)
            if current is None:
                return None
            try:
                key = int(index) if index.isdigit() else index.strip('"\'')
                current = current[key]
            except Exception:
                return None
        else:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = getattr(current, part, None)
        if current is None:
            return None
    return current


def interpolate_text(text, variables):
    """Variable substitution only. Formatting tags pass through unchanged."""
    if not text:
        return ""

    def replace(match):
        name_expr  = match.group("name_expr")
        val_expr   = match.group("val_expr")
        fmt_spec   = match.group("fmt")
        multi_expr = match.group("multi_expr")

        if multi_expr:
            s = multi_expr.strip()
            
            # Strip conditional prefixes if present
            if s.startswith("-if "):
                s = s[4:].strip()
            elif s.startswith("if "):
                s = s[3:].strip()

            # Split expression from options. Ignore spaces inside parens.
            paren_depth = 0
            split_idx = -1
            for i, char in enumerate(s):
                if char == '(':
                    paren_depth += 1
                elif char == ')':
                    paren_depth -= 1
                elif char.isspace() and paren_depth == 0:
                    split_idx = i
                    break

            if split_idx != -1:
                expr_str = s[:split_idx].strip()
                options_raw = s[split_idx:].strip()
            else:
                parts = s.split(None, 1)
                if len(parts) < 2:
                    return f"<invalid_multi:{multi_expr}>"
                expr_str, options_raw = parts

            options = [opt.strip() for opt in options_raw.split("|")]

            try:
                val = safe_eval_expr(expr_str, variables)
                
                # 0-based routing logic
                if isinstance(val, bool):
                    idx = 1 if val else 0
                else:
                    idx = int(float(val))

                if 0 <= idx < len(options):
                    return options[idx]
                return f"<out_of_bounds:{idx}>"
            except Exception:
                return f"<error_multi:{expr_str}>"

        if name_expr:
            return name_expr.strip()

        expr = val_expr.strip()
        try:
            if not any(tok in expr for tok in "()[]+-*/'\""):
                val = get_nested(variables, expr)
            else:
                val = None
            # if val is None:
            #     val = safe_eval_expr(expr, variables)
            if val is None and not expr.startswith("_"):
                val = variables.get(f"_{expr}")
            if fmt_spec and val is not None:
                val = f"{val:{fmt_spec}}"
            return str(val) if val is not None else f"<undefined:{expr}>"
        except Exception:
            return f"<error:{expr}>"

    return PATTERN.sub(replace, text)


def parse_formatting(text):
    """
    Parse [b], [i], [u], [s], [color=#hex] tags into a token list.

    Token types: text | bold | italic | underline | strikethrough | color
    Formatted tokens carry a nested 'tokens' list (recursive).
    Color tokens also carry a 'color' string.

    Plain text → [{"type": "text", "text": "..."}]
    """
    if not text:
        return [{"type": "text", "text": ""}]

    tokens   = []
    last_end = 0

    for match in FORMAT_PATTERN.finditer(text):
        start = match.start()
        if start > last_end:
            tokens.append({"type": "text", "text": text[last_end:start]})

        tag     = match.group("tag")
        attr    = match.group("attr")
        content = match.group("content")
        inner   = parse_formatting(content)

        tag_map = {
            "b": "bold", 
            "i": "italic", 
            "u": "underline",
            "s": "strikethrough", 
            "color": "color",
            "url": "link"
        }
        tag_type = tag_map[tag]

        if tag_type == "color":
            tokens.append({"type": "color", "color": _sanitize_color(attr or "#fff"),
                           "tokens": inner})
        elif tag_type == "link":
            # Store the URL attribute
            tokens.append({"type": "link", "url": attr or "#", "tokens": inner})
        else:
            tokens.append({"type": tag_type, "tokens": inner})

        last_end = match.end()

    if last_end < len(text):
        tokens.append({"type": "text", "text": text[last_end:]})

    return tokens or [{"type": "text", "text": text}]


def tokens_to_html(tokens):
    """Convert token list to safe HTML string for web innerHTML."""
    parts = []
    for tok in tokens:
        t = tok["type"]
        if t == "text":
            parts.append(_escape_html(tok.get("text", "")))
        elif t == "bold":
            parts.append(f"<strong>{tokens_to_html(tok['tokens'])}</strong>")
        elif t == "italic":
            parts.append(f"<em>{tokens_to_html(tok['tokens'])}</em>")
        elif t == "underline":
            parts.append(f"<u>{tokens_to_html(tok['tokens'])}</u>")
        elif t == "strikethrough":
            parts.append(f"<s>{tokens_to_html(tok['tokens'])}</s>")
        elif t == "color":
            color = _sanitize_color(tok.get("color", "#fff"))
            parts.append(f'<span style="color:{color}">{tokens_to_html(tok["tokens"])}</span>')
        elif t == "link":
            raw_url = tok.get("url", "#")
            # escape the URL to prevent XSS (closing the href quote early)
            safe_url = _escape_html(raw_url)
            inner_html = tokens_to_html(tok['tokens'])
            parts.append(f'<a href="{safe_url}" target="_blank" class="story-link">{inner_html}</a>')

    return "".join(parts)


def tokens_to_plain(tokens):
    """Strip formatting, return plain text."""
    parts = []
    for tok in tokens:
        if tok["type"] == "text":
            parts.append(tok.get("text", ""))
        elif tok["type"] in ("bold", "italic", "underline", "strikethrough", "color", "link"):
            parts.append(tokens_to_plain(tok["tokens"]))
    return "".join(parts)


def interpolate_and_format(text, variables):
    """
    Full pipeline: variable substitution → format token list.
    This is what UIProcessor calls for all display text.
    """
    return parse_formatting(interpolate_text(text, variables))


def has_formatting(text):
    return bool(FORMAT_PATTERN.search(text)) if text else False


_HTML_ESCAPE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;",
                               '"': "&quot;", "'": "&#x27;"})

def _escape_html(text):
    return text.translate(_HTML_ESCAPE)

def _sanitize_color(color):
    color = color.strip()
    if re.match(r'^#[0-9a-fA-F]{3,6}$', color):
        return color
    if re.match(r'^[a-zA-Z]+$', color):
        return color
    return "#ffffff"

