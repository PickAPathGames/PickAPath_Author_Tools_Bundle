# /storygraph/layout/card_size.py
import math

def measure_card_size(text, cfg):
    if not text:
        lines = [""]
    else:
        lines = text.split("\n")

    char_w = cfg["char_width"]
    line_h = cfg["line_height"]
    pad_x = cfg["padding_x"]
    pad_y = cfg["padding_y"]
    min_w = cfg["min_width"]
    max_w = cfg["max_width"]

    longest = max(len(line) for line in lines)
    content_w = longest * char_w
    width = content_w + pad_x * 2
    width = max(min_w, min(max_w, width))

    usable_w = max(1, width - pad_x * 2)
    chars_per_line = max(1, usable_w // char_w)

    visual_lines = 0
    for line in lines:
        if not line:
            visual_lines += 1
        else:
            visual_lines += math.ceil(len(line) / chars_per_line)

    height = visual_lines * line_h + pad_y * 2
    return width, height
