"""Render a voice-Agent-centered HealthMate poster and its focus diagram."""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

from generate import ASSETS, CREAM, DARK, HERE, INK, LIME, LOGO, MUTED, ROOT, WHITE, extract_screens, font, round_mask, round_rect


W, H = 2400, 4100
STAGE_TOP, STAGE_BOTTOM = 920, 2860
CENTER = (1200, 1920)
CORE_RADIUS = 300
NODE_RADIUS = 174
NODES = [
    ((628, 1590), "target", "健康目标", "目标与进度"),
    ((1200, 1260), "camera", "饮食记录", "拍照识餐"),
    ((1772, 1590), "calendar", "今日计划", "安排下一步"),
    ((1772, 2250), "workout", "运动复盘", "动作反馈"),
    ((1200, 2580), "trend", "趋势周报", "看见变化"),
    ((628, 2250), "check", "每日打卡", "饮水 · 睡眠 · 步数"),
]


def centered_text(draw: ImageDraw.ImageDraw, x: int, y: int, value: str, size: int, fill: str, bold: bool = False) -> None:
    face = font(size, bold)
    width, _ = draw.textsize(value, font=face)
    draw.text((x - width // 2, y), value, font=face, fill=fill)


def load_icon(name: str, size: int) -> Image.Image:
    path = ROOT / "miniprogram" / "assets" / "icons" / f"{name}.png"
    return Image.open(BytesIO(path.read_bytes())).convert("RGBA").resize((size, size), Image.LANCZOS)


def connect(draw: ImageDraw.ImageDraw, target: tuple[int, int]) -> None:
    dx, dy = target[0] - CENTER[0], target[1] - CENTER[1]
    distance = math.hypot(dx, dy)
    ux, uy = dx / distance, dy / distance
    x0 = CENTER[0] + ux * (CORE_RADIUS + 21)
    y0 = CENTER[1] + uy * (CORE_RADIUS + 21)
    x1 = target[0] - ux * (NODE_RADIUS + 25)
    y1 = target[1] - uy * (NODE_RADIUS + 25)
    draw.line((x0, y0, x1, y1), fill="#B7D77A", width=9)
    wing = 21
    back = 31
    draw.polygon(
        [
            (x1, y1),
            (x1 - ux * back - uy * wing, y1 - uy * back + ux * wing),
            (x1 - ux * back + uy * wing, y1 - uy * back - ux * wing),
        ],
        fill="#B7D77A",
    )


def feature_node(canvas: Image.Image, center: tuple[int, int], icon: str, title: str, detail: str) -> None:
    cx, cy = center
    shadow = Image.new("RGBA", canvas.size)
    sd = ImageDraw.Draw(shadow)
    sd.ellipse((cx - NODE_RADIUS + 10, cy - NODE_RADIUS + 18, cx + NODE_RADIUS + 10, cy + NODE_RADIUS + 18), fill=(0, 0, 0, 86))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(32)))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((cx - NODE_RADIUS, cy - NODE_RADIUS, cx + NODE_RADIUS, cy + NODE_RADIUS), fill="#F4F7E9")
    icon_img = load_icon(icon, 94)
    canvas.alpha_composite(icon_img, (cx - 47, cy - 114))
    draw = ImageDraw.Draw(canvas)
    centered_text(draw, cx, cy + 6, title, 40, INK, True)
    centered_text(draw, cx, cy + 74, detail, 27, MUTED)


def draw_network(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, STAGE_TOP, W, STAGE_BOTTOM), fill=DARK)

    glow = Image.new("RGBA", canvas.size)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((605, 1300, 1795, 2540), fill=(154, 204, 107, 55))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))

    draw = ImageDraw.Draw(canvas)
    draw.text((150, 985), "语音 Agent 驱动 · 六项能力协同", font=font(54, True), fill=WHITE)
    draw.text((153, 1065), "开口提出需求，记录、行动与反馈自然连接", font=font(30), fill="#BBCDBB")
    draw.ellipse((540, 1260, 1860, 2580), outline="#365344", width=3)
    for center, _, _, _ in NODES:
        connect(draw, center)

    # The lime core is the focus; arrows point outward to each capability.
    outer = Image.new("RGBA", canvas.size)
    od = ImageDraw.Draw(outer)
    od.ellipse((CENTER[0] - 345, CENTER[1] - 345, CENTER[0] + 345, CENTER[1] + 345), fill=(183, 220, 111, 95))
    canvas.alpha_composite(outer.filter(ImageFilter.GaussianBlur(43)))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((CENTER[0] - 324, CENTER[1] - 324, CENTER[0] + 324, CENTER[1] + 324), outline="#93B66C", width=5)
    draw.ellipse((CENTER[0] - CORE_RADIUS, CENTER[1] - CORE_RADIUS, CENTER[0] + CORE_RADIUS, CENTER[1] + CORE_RADIUS), fill=LIME)
    canvas.alpha_composite(load_icon("mic", 142), (CENTER[0] - 71, CENTER[1] - 171))
    canvas.alpha_composite(load_icon("spark", 84), (CENTER[0] + 130, CENTER[1] - 177))
    draw = ImageDraw.Draw(canvas)
    centered_text(draw, CENTER[0], CENTER[1] + 5, "语音 Agent", 67, INK, True)
    centered_text(draw, CENTER[0], CENTER[1] + 103, "一句话发起 · 协同完成", 30, "#465A35")

    for center, icon, title, detail in NODES:
        feature_node(canvas, center, icon, title, detail)


def screenshot_card(canvas: Image.Image, x: int, path: str, box: tuple[int, int, int, int], title: str, detail: str) -> None:
    y, width, height = 3150, 670, 735
    draw = ImageDraw.Draw(canvas)
    round_rect(draw, (x, y, x + width, y + height), 38, "#FFFFFF")
    source = Image.open(BytesIO((ASSETS / path).read_bytes())).convert("RGB").crop(box)
    image = source.resize((638, 510), Image.LANCZOS).convert("RGBA")
    image.putalpha(round_mask((638, 510), 27))
    canvas.alpha_composite(image, (x + 16, y + 16))
    draw = ImageDraw.Draw(canvas)
    draw.text((x + 32, y + 553), title, font=font(43, True), fill=INK)
    draw.text((x + 32, y + 630), detail, font=font(28), fill=MUTED)


def main() -> None:
    extract_screens()
    canvas = Image.new("RGBA", (W, H), CREAM)
    draw = ImageDraw.Draw(canvas)

    canvas.alpha_composite(Image.open(BytesIO(LOGO.read_bytes())).convert("RGBA").resize((118, 118), Image.LANCZOS), (148, 126))
    draw.text((294, 137), "HealthMate", font=font(66, True), fill=INK)
    draw.text((300, 212), "个人健康 Agent 工作台", font=font(30), fill=MUTED)
    round_rect(draw, (150, 315, 553, 378), 31, "#E4EEBF")
    draw.text((182, 328), "微信小程序 · 健康陪伴", font=font(30, True), fill=INK)
    draw.text((142, 436), "一句话，串起", font=font(126, True), fill=INK)
    draw.text((142, 582), "健康每一步。", font=font(126, True), fill=INK)
    draw.text((153, 790), "从日常记录到下一步行动，让 Agent 帮你联动。", font=font(43), fill=MUTED)

    draw_network(canvas)

    draw = ImageDraw.Draw(canvas)
    draw.text((150, 2964), "从一句话，到可执行的结果", font=font(61, True), fill=INK)
    screenshot_card(canvas, 140, "record.jpg", (90, 765, 1090, 1565), "拍照记餐", "识别后确认入账")
    screenshot_card(canvas, 865, "companion.jpg", (50, 1040, 1125, 1900), "语音陪伴", "小康、小健在场")
    screenshot_card(canvas, 1590, "report.jpg", (90, 340, 1090, 1140), "周报复盘", "让变化看得见")
    draw = ImageDraw.Draw(canvas)
    draw.text((154, 4000), "HealthMate  /  记录 → 建议 → 行动 → 复盘", font=font(32), fill=MUTED)

    rgb = canvas.convert("RGB")
    poster = HERE / "HealthMate_语音Agent海报.png"
    pdf = HERE / "HealthMate_语音Agent海报.pdf"
    diagram = HERE / "HealthMate_语音Agent功能互联图.png"
    rgb.save(poster, optimize=True)
    rgb.save(pdf, "PDF", resolution=240.0)
    rgb.crop((0, STAGE_TOP, W, STAGE_BOTTOM)).save(diagram, optimize=True)
    print(poster)
    print(pdf)
    print(diagram)


if __name__ == "__main__":
    main()
