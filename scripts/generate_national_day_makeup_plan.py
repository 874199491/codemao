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

    lesson_slots = [
        (570, 363, 958, 408),
        (570, 483, 958, 528),
        (570, 603, 958, 648),
        (570, 723, 958, 768),
        (570, 843, 958, 888),
        (570, 943, 958, 988),
        (570, 1063, 958, 1108),
    ]
    for idx, rect in enumerate(lesson_slots):
        text = lessons[idx] if idx < len(lessons) else "复习 / 机动"
        text_cover = sc_rect((rect[0] - 10, rect[1] - 22, 1042, rect[3] + 22))
        draw.rounded_rectangle(text_cover, radius=round(8 * sx), fill=(253, 254, 249))
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
    parser.add_argument("--lessons", required=True, help="7 lessons separated by |; multiple lessons in one day separated by 、")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    lessons = [item.strip() for item in args.lessons.split("|")]
    print(render_card(args.template, args.name, lessons, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
