from __future__ import annotations

import colorsys
import html
import io
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _font_path() -> str | None:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    import os

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path()
    if path is not None:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


NAVY = "#123c53"
TEAL = "#168b82"
PURPLE = "#6c4fd0"
LIGHT = "#eef4f2"
GRID = "#d5e2de"
TEXT = "#314e62"
PALETTE = ["#123c53", "#168b82", "#6c4fd0", "#e0954c", "#4d7fc1", "#7d8ba6", "#2fa49a"]


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines() or [""]:
        current = ""
        for char in raw:
            probe = current + char
            if draw.textlength(probe, font=font) > width and current:
                lines.append(current)
                current = char
            else:
                current = probe
        lines.append(current)
    return lines


def render_bar_chart_png(spec: dict[str, Any]) -> bytes:
    """Deterministic bar chart PNG from a validated ChartSpec."""

    width, height = 960, 640
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(24, bold=True)
    sub_font = _font(15)
    label_font = _font(16)
    value_font = _font(16, bold=True)
    categories = [c for c in spec.get("categories", []) if float(c.get("value", 0) or 0) > 0]
    categories = categories[:12]

    title = str(spec.get("title") or "")
    subtitle = str(spec.get("subtitle") or "")
    y = 28
    if title:
        draw.text((40, y), title, font=title_font, fill=NAVY)
        y += 40
    if subtitle:
        for line in _wrap_lines(draw, subtitle, sub_font, width - 80):
            draw.text((40, y), line, font=sub_font, fill="#6f8390")
            y += 24
    y += 14

    left, right = 150, width - 60
    top, bottom = y + 8, height - 110
    is_cutpoint_distribution = spec.get("chart_mode") == "cutpoint_distribution"
    # Cutpoint charts use interval bounds only as labels/metadata.  The bar
    # height must always be the backend-computed count in ``value``.
    max_value = max((float(c.get("value", 0) or 0) for c in categories), default=0)
    if not is_cutpoint_distribution:
        max_value = max(
            max_value,
            max((float(c.get("upper", c.get("value", 0)) or 0) for c in categories), default=0),
        )
    max_value = max(max_value, 1)

    grid_max = int(math.ceil(max_value / 2) * 2) or 2
    for step in range(5):
        grid_y = bottom - (bottom - top) * step / 4
        draw.line([(left, grid_y), (right, grid_y)], fill=GRID, width=1)
        value = int(round(grid_max * step / 4))
        draw.text((left - 18, grid_y - 9), str(value), font=label_font, fill="#7d8c96", anchor="rs")
    draw.line([(left, bottom), (right, bottom)], fill=NAVY, width=2)
    draw.line([(left, top), (left, bottom)], fill=NAVY, width=2)

    count = len(categories)
    slot = (right - left) / max(count, 1)
    bar_width = min(70.0, slot * 0.55)
    for index, category in enumerate(categories):
        value = float(category.get("value", 0) or 0)
        if is_cutpoint_distribution:
            lower, upper = 0.0, value
        else:
            lower = float(category.get("lower", 0) or 0)
            upper = float(category.get("upper", value) or value)
        bar_height = max(2.0, (bottom - top) * value / grid_max)
        x0 = left + slot * index + (slot - bar_width) / 2
        x1 = x0 + bar_width
        y0 = bottom - (bottom - top) * upper / grid_max
        if upper <= lower:
            y0 = bottom - bar_height
        y1 = bottom - (bottom - top) * lower / grid_max
        color = PALETTE[index % len(PALETTE)]
        draw.rounded_rectangle([x0, y0, x1, y1], radius=4, fill=color)
        value_label = f"{value:g}人" if is_cutpoint_distribution else f"{value:g}{spec.get('unit') or ''}"
        draw.text(
            (x0 + bar_width / 2, y0 - 20),
            value_label,
            font=value_font,
            fill=NAVY,
            anchor="ms",
        )
        label = _truncate(
            draw,
            str(category.get("label") or category.get("key") or ""),
            label_font,
            slot - 8,
        )
        for line_index, line in enumerate(_wrap_lines(draw, label, label_font, slot - 8)[:2]):
            draw.text(
                (x0 + bar_width / 2, bottom + 14 + line_index * 20),
                line,
                font=label_font,
                fill=TEXT,
                anchor="ma",
            )

    footer = str(spec.get("subtitle") or "")
    if footer:
        draw.text((40, height - 46), footer[:120], font=sub_font, fill="#8b99a0")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_pie_chart_png(spec: dict[str, Any]) -> bytes:
    width, height = 960, 640
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(24, bold=True)
    sub_font = _font(15)
    label_font = _font(16)
    categories = [c for c in spec.get("categories", []) if float(c.get("value", 0) or 0) > 0]
    total = sum(float(c.get("value", 0) or 0) for c in categories)

    y = 28
    title = str(spec.get("title") or "")
    if title:
        draw.text((40, y), title, font=title_font, fill=NAVY)
        y += 42
    subtitle = str(spec.get("subtitle") or "")
    for line in _wrap_lines(draw, subtitle, sub_font, width - 80):
        draw.text((40, y), line, font=sub_font, fill="#6f8390")
        y += 24
    y += 8

    center_x, center_y = 330, y + 210
    radius = 170
    start_angle = -90.0
    for index, category in enumerate(categories):
        value = float(category.get("value", 0) or 0)
        if total <= 0:
            break
        span = 360.0 * value / total
        color = PALETTE[index % len(PALETTE)]
        draw.pieslice(
            [
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            ],
            start=start_angle,
            end=start_angle + span,
            fill=color,
            outline="white",
            width=2,
        )
        mid = math.radians(start_angle + span / 2)
        label_r = radius + 34
        label_x = center_x + math.cos(mid) * label_r
        label_y = center_y + math.sin(mid) * label_r
        percent = value * 100 / total if total else 0
        draw.text(
            (label_x, label_y),
            f"{int(round(percent))}%",
            font=label_font,
            fill=NAVY,
            anchor="mm",
        )
        start_angle += span

    legend_x = 640
    legend_y = y + 40
    for index, category in enumerate(categories):
        value = int(category.get("value", 0) or 0)
        percent = value * 100 / total if total else 0
        color = PALETTE[index % len(PALETTE)]
        draw.rectangle([legend_x, legend_y, legend_x + 18, legend_y + 18], fill=color)
        label = _truncate(
            draw,
            str(category.get("label") or category.get("key") or ""),
            label_font,
            210,
        )
        draw.text((legend_x + 28, legend_y), label, font=label_font, fill=TEXT)
        draw.text(
            (legend_x + 28, legend_y + 26),
            f"{value} 人 · {percent:.1f}%",
            font=sub_font,
            fill="#6f8390",
        )
        legend_y += 66

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_chart_png(spec: dict[str, Any]) -> bytes:
    chart_type = str(spec.get("type") or "bar")
    if chart_type == "pie":
        return render_pie_chart_png(spec)
    return render_bar_chart_png(spec)


def render_chart_svg(spec: dict[str, Any]) -> str:
    """Minimal deterministic SVG mirror used for the downloadable SVG file."""

    chart_type = str(spec.get("type") or "bar")
    title = str(spec.get("title") or "")
    subtitle = str(spec.get("subtitle") or "")
    categories = spec.get("categories", [])
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="640" viewBox="0 0 960 640">',
        '<rect width="960" height="640" fill="#ffffff"/>',
    ]
    if title:
        parts.append(
            f'<text x="40" y="46" font-size="24" font-weight="700" fill="#123c53">{html.escape(title)}</text>'
        )
    if subtitle:
        parts.append(
            f'<text x="40" y="78" font-size="15" fill="#6f8390">{html.escape(subtitle)}</text>'
        )
    if chart_type == "pie":
        total = max(sum(float(c.get("value", 0) or 0) for c in categories), 1)
        cx, cy, radius = 330, 300, 170
        start = -90.0
        for index, category in enumerate(categories):
            value = float(category.get("value", 0) or 0)
            span = 360.0 * value / total
            color = PALETTE[index % len(PALETTE)]
            large = 1 if span > 180 else 0
            start_rad = math.radians(start)
            end_rad = math.radians(start + span)
            x1 = cx + radius * math.cos(start_rad)
            y1 = cy + radius * math.sin(start_rad)
            x2 = cx + radius * math.cos(end_rad)
            y2 = cy + radius * math.sin(end_rad)
            parts.append(
                f'<path d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {radius} {radius} 0 {large} 1 '
                f'{x2:.1f} {y2:.1f} Z" fill="{color}" stroke="#ffffff" stroke-width="2"/>'
            )
            start += span
    else:
        categories = [c for c in categories if float(c.get("value", 0) or 0) > 0][:12]
        max_value = max((float(c.get("value", 0) or 0) for c in categories), default=1)
        left, right, bottom = 150, 900, 520
        top = 130
        slot = (right - left) / max(len(categories), 1)
        for index, category in enumerate(categories):
            value = float(category.get("value", 0) or 0)
            bar_height = max(2, int((bottom - top) * value / max_value))
            x0 = left + slot * index + slot * 0.2
            x1 = x0 + slot * 0.6
            color = PALETTE[index % len(PALETTE)]
            parts.append(
                f'<rect x="{x0:.1f}" y="{bottom - bar_height}" width="{x1 - x0:.1f}" '
                f'height="{bar_height}" rx="4" fill="{color}"/>'
            )
            label = str(category.get("label") or category.get("key") or "")
            parts.append(
                f'<text x="{x0 + (x1 - x0) / 2:.1f}" y="{bottom + 24}" font-size="14" '
                f'fill="#314e62" text-anchor="middle">{html.escape(label)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)
