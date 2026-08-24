"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# engine/runtime/ui_processor.py
import re
import copy
from engine.runtime.interpolation import interpolate_and_format, tokens_to_html
from utils.safe_eval import safe_eval_expr


class UIProcessor:

    MERMAID_PALETTE = {
        "1": "#ef4444", "2": "#f97316", "3": "#eab308",
        "4": "#22c55e", "5": "#3b82f6", "6": "#a855f7",
    }

    # =========================================================================
    # Main story frame
    # =========================================================================

    @staticmethod
    def process_frame(session):
        engine = session.engine

        if not session.history_frames:
            return {"kind": "error", "display": []}

        history_entry   = session.history_frames[session.playhead]
        raw_frame       = history_entry["processed"]
        vars_at_time    = history_entry["vars_snapshot"]
        engine_snapshot = history_entry.get("engine_snapshot", {})
        raw_grid        = engine_snapshot.get("ui_grid", [None] * 4)
        display_items = []
        raw_display = history_entry["raw"].get("display") or []

        # First pass: interpolate all text items
        interpolated = []
        for item in raw_display:
            if item.get("type") == "text":
                # Check for 'content' (new engine) or 'text' (old engine/parser)
                raw_val = item.get("content") or item.get("text", "")
                
                tokens = interpolate_and_format(raw_val, vars_at_time)
                interpolated.append({
                    "type":    "text",
                    "tokens":  tokens,
                    "html":    tokens_to_html(tokens),
                    "raw":     raw_val,
                })
            else:
                interpolated.append(item)

        # Second pass: group text items into paragraphs
        display_items = _assemble_paragraphs(interpolated)

        # Sidebar permanent stat grid
        sidebar_grid = []
        for i in range(4):
            slot = raw_grid[i] if i < len(raw_grid) else None
            if slot:
                var_name = slot.get("var")
                val      = vars_at_time.get(var_name, 0)
                sidebar_grid.append({"label": slot.get("label", ""), "value": val})
            else:
                sidebar_grid.append(None)

        # Author's pick metadata
        next_choice_id  = None
        author_progress = None

        if getattr(session, "is_read_only", False) and session.recorder:
            picks_so_far = sum(
                1 for f in session.history_frames[: session.playhead]  # ← exclude current frame
                if f["raw"].get("kind") == "pick"
            )
            choices_list = getattr(session.recorder, "choices", [])
            if picks_so_far < len(choices_list):
                next_choice_id = choices_list[picks_so_far]

            meaningful_total = sum(1 for f in session.history_frames if f.get("is_meaningful"))
            meaningful_now   = sum(
                1 for f in session.history_frames[: session.playhead + 1]
                if f.get("is_meaningful")
            )
            author_progress = {"current": meaningful_now, "total": meaningful_total}

        history_trace = session.get_history_trace()

        # --- Resolve the Visual Active Node for the Map ---
        # Fallback to an empty set if it wasn't attached for some reason
        map_exclude = getattr(session, "map_exclude", set())
        visual_active_id = None
        
        # Start at the current playhead and work backwards
        for i in range(session.playhead, -1, -1):
            frame_id = session.history_frames[i].get("canonical_id")
            if not frame_id:
                continue
            
            # Check if this file is excluded
            file_name = frame_id.split("::")[0]
            if file_name not in map_exclude:
                visual_active_id = frame_id
                break
        
        # Find the first meaningful frame index (true "start" for back-button disable)
        first_meaningful_idx = next(
            (i for i, f in enumerate(session.history_frames) if f.get("is_meaningful")),
            0
        )
        # If we went all the way back and found nothing visible, 
        # default to the current frame to avoid a null error in the frontend
        if not visual_active_id:
            visual_active_id = history_entry.get("canonical_id")

        payload = {
            "kind":        raw_frame["kind"],
            "scene":       raw_frame.get("scene"),
            "first_meaningful_idx": first_meaningful_idx,
            "display":     display_items,
            "ui_grid":     sidebar_grid,
            "choices":     raw_frame.get("choices", []),
            "vars":        vars_at_time,
            "playhead":    int(session.playhead),
            "history_len": len(session.history_frames),
            "map_state": {
                "active_id": visual_active_id or history_entry.get("canonical_id"),
                "history":   history_trace,
            },
            "next_choice_id":  next_choice_id,
            "author_progress": author_progress,
            "is_read_only":    getattr(session, "is_read_only", False),
            "notifications": getattr(session, "pending_notifications", []),
            "user_input_var":    raw_frame.get("user_input_var"),
            "user_input_prompt": raw_frame.get("user_input_prompt", ""),
        }

        session.pending_notifications = []

        return payload


    # =========================================================================
    # Stats screen
    # =========================================================================

    @staticmethod
    def process_stats(session, tag="main_page"):
        """Engine-driven stats rendering via modal view."""
        return session.apply_stats_intent(intent="open", tag=tag)


    @staticmethod
    def _choice_go_target(choice):
        """Extract the -go target tag from a choice's blocks."""
        for block in choice.get("blocks", []):
            if block.get("cmd") == "-go":
                return block.get("args", "").strip()
        cont = choice.get("continuation")
        if cont and len(cont) >= 2:
            return cont[1]
        return None

    # =========================================================================
    # Stat component renderers  (shared: story nodes + stats screen)
    # =========================================================================

    @staticmethod
    def _get_max(vars_at_time):
        try:
            return float(vars_at_time.get("GLOBAL_MAX_PERCENTAGE", 100.0))
        except (TypeError, ValueError):
            return 100.0

    @staticmethod
    def _render_stat_bar(args, vars_at_time):
        """
        -stat_bar "Label" var_name [color]
        """
        if len(args) < 2:
            return None
        label    = args[0].strip('"')
        var_name = args[1]
        color    = args[2] if len(args) > 2 else None
        max_val  = UIProcessor._get_max(vars_at_time)
        val      = float(vars_at_time.get(var_name, 0))
        pct      = min(max(val / max_val * 100, 0), 100) if max_val else 0
        return {
            "type":      "component",
            "component": "stat_bar",
            "props": {
                "label":   label,
                "value":   val,
                "max":     max_val,
                "percent": round(pct, 2),
                "color":   color,
            },
        }


    @staticmethod
    def _render_stat_vs(args, vars_at_time):
        """
        -stat_vs var_name "Left label" "Right label" [colorL] [colorR]
        Left = var%; Right = (100 - var)%.
        """
        if len(args) < 3:
            return None
        var_name    = args[0]
        left_label  = args[1].strip('"')
        right_label = args[2].strip('"')
        color_left  = args[3] if len(args) > 3 else "#3b82f6"
        color_right = args[4] if len(args) > 4 else "#ef4444"
        max_val     = UIProcessor._get_max(vars_at_time)
        val         = float(vars_at_time.get(var_name, 0))
        pct         = min(max(val / max_val * 100, 0), 100) if max_val else 0
        return {
            "type":      "component",
            "component": "stat_vs",
            "props": {
                "var_name":    var_name,
                "left_label":  left_label,
                "right_label": right_label,
                "value":       val,
                "max":         max_val,
                "percent":     round(pct, 2),
                "color_left":  color_left,
                "color_right": color_right,
            },
        }


    @staticmethod
    def _render_stats_block(block, vars_at_time):
        """
        Dispatch one block dict to the correct stat renderer.
        Returns a list of display item dicts (may be empty).
        Note: -stat_block and -pick are handled by walk_items in process_stats;
        this method handles the leaf display commands only.
        """
        import re as _re
        cmd      = block.get("cmd", "")
        args_raw = (block.get("args", "") or "").strip()

        if cmd == "-stat_header":
            text = args_raw.strip('"').strip()
            return [{"type": "component", "component": "stat_header",
                     "props": {"text": text}}]

        if cmd == "-stat_row":
            import re as _re
            # Match: "Label text" var_name  (label may contain spaces/colons)
            m = _re.match(r'^"([^"]*)"\s+(\S+)$', args_raw)
            if m:
                label    = m.group(1).rstrip()   # strip trailing space from label
                var_name = m.group(2)
            else:
                parts = args_raw.split(None, 1)
                if len(parts) < 2:
                    return []
                label    = parts[0].strip('"').rstrip()
                var_name = parts[1].strip()
            val     = vars_at_time.get(var_name, "-")
            val_str = str(val)
            tokens  = interpolate_and_format(val_str, vars_at_time)
            return [{"type": "component", "component": "stat_row",
                     "props": {"label": label, "value": val_str,
                               "html": tokens_to_html(tokens)}}]

        if cmd == "-stat_bar":
            # Safely extract quoted labels with spaces, followed by variable name and optional color
            m = _re.match(r'^"([^"]*)"\s+(\S+)(?:\s+(\S+))?$', args_raw)
            if m:
                args = [m.group(1), m.group(2)]
                if m.group(3): args.append(m.group(3))
            else:
                args = args_raw.split()
            comp = UIProcessor._render_stat_bar(args, vars_at_time)
            return [comp] if comp else []

        if cmd == "-stat_vs":
            # Safely match: var_name "Left Label" "Right Label" [colorL] [colorR]
            m = _re.match(r'^(\S+)\s+"([^"]*)"\s+"([^"]*)"(?:\s+(\S+))?(?:\s+(\S+))?$', args_raw)
            if m:
                args = [m.group(1), m.group(2), m.group(3)]
                if m.group(4): args.append(m.group(4))
                if m.group(5): args.append(m.group(5))
            else:
                args = args_raw.split()
            comp = UIProcessor._render_stat_vs(args, vars_at_time)
            return [comp] if comp else []

        if cmd == "-stat_break":
            return [{"type": "component", "component": "stat_break", "props": {}}]
        
        if cmd == "-pic":
            parts    = args_raw.strip().split()
            filename = parts[0] if parts else ""
            align    = parts[1] if len(parts) > 1 else "center"
            if filename:
                return [{"type": "component", "component": "pic",
                        "props": {"filename": filename, "align": align,
                                "src": f"/images/{filename}"}}]
            return []
        
        if cmd == "-stat_item":
            label = args_raw.strip('"').strip("'")
            return [{
                "type": "component",
                "component": "stat_item",
                "props": {"label": label}
            }]

        if cmd == "-stat_items":
            if '"' not in args_raw and "'" not in args_raw:
                labels_part = args_raw.split()
                vars_part = []
            else:
                m = _re.match(r'^([^"\']*)(.*)$', args_raw)
                vars_lead = m.group(1).strip()
                labels_lead = m.group(2).strip()
                
                vars_part = vars_lead.split() if vars_lead else []
                labels_part = shlex.split(labels_lead) if labels_lead else []

            if not vars_part:
                active = labels_part
            else:
                active = []
                for i, var_name in enumerate(vars_part):
                    if i >= len(labels_part):
                        break
                    val = vars_at_time.get(var_name, 0)
                    if val and val != "0" and str(val).lower() != "false":
                        active.append(labels_part[i])

            display_str = " | ".join(active) + " |" if active else ""
            return [{
                "type": "component",
                "component": "stat_list",
                "props": {"text": display_str}
            }]

        return []

    # =========================================================================
    # Legacy compatibility
    # =========================================================================

    @staticmethod
    def render_node_to_components(node, current_vars):
        """Legacy helper kept for any older callers. Prefer process_stats()."""
        items   = []
        choices = []

        for text in getattr(node, "text_items", getattr(node, "text", [])):
            raw = text if isinstance(text, str) else text.get("text", "")
            if raw.strip():
                tokens = interpolate_and_format(raw, current_vars)
                items.append({
                    "type": "text", "tokens": tokens,
                    "html": tokens_to_html(tokens),
                    "content": tokens_to_html(tokens),
                })

        for block in getattr(node, "blocks", []):
            items.extend(UIProcessor._render_stats_block(block, current_vars))

        for choice in getattr(node, "choices", []):
            choices.append({
                "label":      choice.get("label", ""),
                "target_tag": choice.get("target_tag"),
            })

        return {"display": items, "choices": choices}


def _assemble_paragraphs(items):
    final_display = []
    for item in items:
        if item.get("type") == "text":
            html = item.get("html", "")
            if not html.strip(): 
                continue # Skip the blank objects; the <p> margins handle spacing
            
            final_display.append({
                "type": "text",
                "html": html.replace("\n", "<br>")
            })
        else:
            final_display.append(item)
    return final_display
