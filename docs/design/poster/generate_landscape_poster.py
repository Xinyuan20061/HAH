"""Render the landscape poster with the voice Agent connecting six screenshot cards."""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

from generate import ASSETS, CREAM, DARK, HERE, INK, LIME, LOGO, MUTED, ROOT, WHITE, font, round_mask, round_rect


W, H = 3840, 2160
STAGE_TOP, STAGE_BOTTOM = 430, 2020
CORE = (1920, 1200)
CORE_R = 296
BADGE_R = 96


FEATURES = [
    # side, row, title, detail, project icon, image file, key content crop
    ("left", 0, "健康目标", "明确每天的方向", "target", "goal.jpg", (40, 320, 1140, 916)),
    ("left", 1, "饮食记录", "拍照留存餐食", "camera", "meal-user.jpg", (0, 390, 960, 910)),
    ("left", 2, "每日打卡", "记录身体状态", "check", "checkin.jpg", (40, 350, 1140, 946)),
    ("right", 0, "今日计划", "建议变成行动", "calendar", "plan.jpg", (40, 1650, 1140, 2246)),
    ("right", 1, "运动复盘", "视频动作反馈", "workout", "motion.jpg", (40, 1090, 1140, 1686)),
    ("right", 2, "趋势周报", "看见每周变化", "trend", "report.jpg", (40, 335, 1140, 931)),
]


def icon(name: str, size: int) -> Image.Image:
    path = ROOT / "miniprogram" / "assets" / "icons" / f"{name}.png"
    return Image.open(BytesIO(path.read_bytes())).convert("RGBA").resize((size, size), Image.LANCZOS)


def card_center(side: str, row: int) -> tuple[int, int]:
    return (1240 if side == "left" else 2600, 665 + row * 525)


def crop_preview(name: str, box: tuple[int, int, int, int]) -> Image.Image:
    image = Image.open(BytesIO((ASSETS / name).read_bytes())).convert("RGB").crop(box)
    # Retain the source proportions; adjust only a few edge pixels if required.
    target_ratio = 720 / 390
    w, h = image.size
    if w / h > target_ratio:
        new_w = round(h * target_ratio)
        left = (w - new_w) // 2
        image = image.crop((left, 0, left + new_w, h))
    else:
        new_h = round(w / target_ratio)
        top = (h - new_h) // 2
        image = image.crop((0, top, w, top + new_h))
    image = image.resize((720, 390), Image.LANCZOS).convert("RGBA")
    image.putalpha(round_mask((720, 390), 31))
    return image


def connect(draw: ImageDraw.ImageDraw, target: tuple[int, int]) -> None:
    dx, dy = target[0] - CORE[0], target[1] - CORE[1]
    dist = math.hypot(dx, dy)
    ux, uy = dx / dist, dy / dist
    x0, y0 = CORE[0] + ux * (CORE_R + 20), CORE[1] + uy * (CORE_R + 20)
    x1, y1 = target[0] - ux * (BADGE_R + 17), target[1] - uy * (BADGE_R + 17)
    draw.line((x0, y0, x1, y1), fill="#A6CA70", width=10)
    wing, back = 24, 34
    draw.polygon(
        [
            (x1, y1),
            (x1 - ux * back - uy * wing, y1 - uy * back + ux * wing),
            (x1 - ux * back + uy * wing, y1 - uy * back - ux * wing),
        ],
        fill="#A6CA70",
    )


def draw_card(canvas: Image.Image, side: str, row: int, title: str, detail: str, pictogram: str, image_name: str, crop: tuple[int, int, int, int]) -> None:
    x = 150 if side == "left" else 2610
    y = 450 + row * 525
    card_w, card_h = 1080, 430

    shadow = Image.new("RGBA", canvas.size)
    sd = ImageDraw.Draw(shadow)
    round_rect(sd, (x + 10, y + 18, x + card_w + 10, y + card_h + 18), 44, (0, 0, 0, 100))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(31)))
    draw = ImageDraw.Draw(canvas)
    round_rect(draw, (x, y, x + card_w, y + card_h), 43, WHITE)

    image_x = x + 20 if side == "left" else x + 340
    canvas.alpha_composite(crop_preview(image_name, crop), (image_x, y + 20))

    draw = ImageDraw.Draw(canvas)
    label_x = x + 765 if side == "left" else x + 133
    draw.text((label_x, y + 104), f"0{row + 1 if side == 'left' else row + 4}", font=font(29, True), fill="#7E9F44")
    draw.text((label_x, y + 164), title, font=font(46, True), fill=INK)
    draw.text((label_x, y + 250), detail, font=font(25), fill=MUTED)

    cx, cy = card_center(side, row)
    draw.ellipse((cx - BADGE_R - 10, cy - BADGE_R - 10, cx + BADGE_R + 10, cy + BADGE_R + 10), fill=DARK)
    draw.ellipse((cx - BADGE_R, cy - BADGE_R, cx + BADGE_R, cy + BADGE_R), fill="#E3F0BF")
    canvas.alpha_composite(icon(pictogram, 116), (cx - 58, cy - 58))


def main() -> None:
    missing = [name for *_, name, crop in FEATURES if not (ASSETS / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing poster sources: {missing}")

    canvas = Image.new("RGBA", (W, H), CREAM)
    draw = ImageDraw.Draw(canvas)
    canvas.alpha_composite(Image.open(BytesIO(LOGO.read_bytes())).convert("RGBA").resize((112, 112), Image.LANCZOS), (150, 78))
    draw.text((288, 82), "HealthMate", font=font(69, True), fill=INK)
    round_rect(draw, (2952, 101, 3690, 169), 34, "#E3EDBD")
    draw.text((3000, 116), "微信小程序  ·  个人健康 Agent 工作台", font=font(32, True), fill=INK)
    draw.text((151, 215), "开口一句，联动健康每一步。", font=font(109, True), fill=INK)
    draw.text((161, 348), "以语音 Agent 为中心，连接记录、目标、计划、运动与反馈。", font=font(41), fill=MUTED)

    draw.rectangle((0, STAGE_TOP, W, STAGE_BOTTOM), fill=DARK)
    glow = Image.new("RGBA", canvas.size)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((1380, 645, 2460, 1745), fill=(170, 218, 110, 80))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(140)))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((1465, 745, 2375, 1655), outline="#42684D", width=4)
    for side, row, *_ in FEATURES:
        connect(draw, card_center(side, row))

    for feature in FEATURES:
        draw_card(canvas, *feature)

    draw = ImageDraw.Draw(canvas)
    draw.ellipse((CORE[0] - 324, CORE[1] - 324, CORE[0] + 324, CORE[1] + 324), outline="#9BBC70", width=7)
    draw.ellipse((CORE[0] - CORE_R, CORE[1] - CORE_R, CORE[0] + CORE_R, CORE[1] + CORE_R), fill=LIME)
    canvas.alpha_composite(icon("mic", 145), (CORE[0] - 72, CORE[1] - 184))
    canvas.alpha_composite(icon("spark", 88), (CORE[0] + 125, CORE[1] - 185))
    draw = ImageDraw.Draw(canvas)
    draw.text((CORE[0] - 177, CORE[1] + 1), "语音 Agent", font=font(75, True), fill=INK)
    draw.text((CORE[0] - 142, CORE[1] + 111), "一句话发起 · 能力协同", font=font(31), fill="#4B5E38")

    draw.text((154, 2074), "HealthMate  /  记录 → 建议 → 行动 → 复盘", font=font(33), fill=MUTED)
    draw.text((3402, 2074), "HAH / 2026", font=font(28, True), fill=MUTED)

    rgb = canvas.convert("RGB")
    png = HERE / "HealthMate_语音Agent横版海报.png"
    pdf = HERE / "HealthMate_语音Agent横版海报.pdf"
    rgb.save(png, optimize=True)
    rgb.save(pdf, "PDF", resolution=300.0)
    print(png)
    print(pdf)


if __name__ == "__main__":
    main()
