#!/usr/bin/env python3
"""Fill a National Day makeup-plan image from a fixed PNG template."""
from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "apps" / "teacher_workbench" / "static" / "assets" / "national_day_makeup_template.png"


def font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size, index=1 if bold and path.suffix == ".ttc" else 0)
            except Exception:
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, min_size: int = 24):
    size = start_size
    while size > min_size:
        f = font(size, True)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_width:
            return f
        size -= 1
    return font(min_size, True)


def render_card(template: Path, name: str, lessons: list[str], out: Path) -> Path:
    img = Image.open(template).convert("RGB")
    w, h = img.size
    sx, sy = w / 1086, h / 1448
    draw = ImageDraw.Draw(img)
    dark_green = (0, 100, 44)
    cream = (255, 255, 235)
    row_fill = (253, 253, 244)
    gold = (255, 210, 97)
    black = (15, 23, 42)
    red = (232, 78, 85)
    panel_fill = (233, 252, 197)
    badge_green = (0, 173, 83)

    def sc_rect(rect):
        x1, y1, x2, y2 = rect
        return (round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy))

    name_rect = sc_rect((63, 580, 354, 665))
    draw.rounded_rectangle(name_rect, radius=round(18 * sx), fill=cream)
    name_font = fit_font(draw, name, round(250 * sx), round(44 * sx), round(30 * sx))
    name_w = draw.textbbox((0, 0), name, font=name_font)[2]
    name_h = draw.textbbox((0, 0), name, font=name_font)[3]
    draw.text((round((208 * sx) - name_w / 2), round((620 * sy) - name_h / 2)), name, font=name_font, fill=dark_green)
    draw.arc(sc_rect((88, 673, 330, 714)), 200, 340, fill=gold, width=max(3, round(4 * sx)))

    row_slots = [
        (402, 341, 1056, 445, 421, 356, 532, 433, 570, 363, 958, 408, "10.1", "周四"),
        (402, 461, 1056, 565, 421, 476, 532, 553, 570, 483, 958, 528, "10.2", "周五"),
        (402, 581, 1056, 685, 421, 596, 532, 673, 570, 603, 958, 648, "10.3", "周六"),
        (402, 701, 1056, 805, 421, 716, 532, 793, 570, 723, 958, 768, "10.4", "周日"),
        (402, 821, 1056, 925, 421, 836, 532, 913, 570, 843, 958, 888, "10.5", "周一"),
        (402, 921, 1056, 1025, 421, 936, 532, 1013, 570, 943, 958, 988, "10.6", "周二"),
        (402, 1041, 1056, 1145, 421, 1056, 532, 1133, 570, 1063, 958, 1108, "10.7", "周三"),
    ]
    visible_lessons = [item.strip() for item in lessons if item.strip()]
    visible_count = min(len(visible_lessons), len(row_slots))
    draw.rounded_rectangle(sc_rect((396, 335, 1061, 1148)), radius=round(26 * sx), fill=panel_fill)
    date_font = font(round(18 * sx), True)
    date_num_font = font(round(32 * sx), True)
    for idx, slot in enumerate(row_slots[:visible_count]):
        row_rect = slot[:4]
        badge_rect = slot[4:8]
        rect = slot[8:12]
        date_text = slot[12]
        weekday_text = slot[13]
        text = visible_lessons[idx]
        draw.rounded_rectangle(sc_rect(row_rect), radius=round(18 * sx), fill=row_fill)
        draw.rounded_rectangle(sc_rect(badge_rect), radius=round(10 * sx), fill=badge_green)
        date_w = draw.textbbox((0, 0), date_text, font=date_num_font)[2]
        week_w = draw.textbbox((0, 0), weekday_text, font=date_font)[2]
        draw.text((round(((badge_rect[0] + badge_rect[2]) / 2) * sx - date_w / 2), round((badge_rect[1] + 8) * sy)), date_text, font=date_num_font, fill="white")
        draw.text((round(((badge_rect[0] + badge_rect[2]) / 2) * sx - week_w / 2), round((badge_rect[1] + 48) * sy)), weekday_text, font=date_font, fill="white")
        draw.line((round(548 * sx), round((rect[1] - 2) * sy), round(548 * sx), round((rect[3] + 2) * sy)), fill=red, width=max(2, round(2 * sx)))
        parts = [part.strip() for part in text.split("、") if part.strip()]
        if len(parts) > 1:
            line_font = fit_font(draw, max(parts, key=len), round(435 * sx), round(22 * sx), round(17 * sx))
            base_y = round((rect[1] - 2) * sy)
            line_gap = round(25 * sy)
            for line_index, part in enumerate(parts[:2]):
                draw.text((round(rect[0] * sx), base_y + line_index * line_gap), part, font=line_font, fill=black)
        else:
            f = fit_font(draw, text, round(430 * sx), round(31 * sx), round(23 * sx))
            draw.text((round(rect[0] * sx), round(rect[1] * sy)), text, font=f, fill=black)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=96)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--name", required=True)
    parser.add_argument("--lessons", required=True, help="Lessons separated by |; multiple lessons in one day separated by 、")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    lessons = [item.strip() for item in args.lessons.split("|") if item.strip()]
    print(render_card(args.template, args.name, lessons, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
