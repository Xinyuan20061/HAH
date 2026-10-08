"""Render the concise HealthMate poster from real mini-program screenshots."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFilter, ImageFont


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
SOURCE_ZIP = Path(r"C:\Users\xinyuan\OneDrive\Desktop\图片.zip")
LOGO = ROOT / "miniprogram" / "assets" / "icons" / "spark.png"
ASSETS = HERE / "assets"
SCREENS = {
    "record.jpg": "04e3358608f9cc836122ac0865d91df7.jpg",
    "companion.jpg": "6b89d80ea637de1ade681e5fdbbd04d5.jpg",
    "report.jpg": "0926a81491db46d7e31202209125c729.jpg",
}

W, H = 2400, 3400
CREAM = "#F5F6EC"
DARK = "#10291F"
INK = "#132A20"
MUTED = "#657369"
LIME = "#C4E267"
WHITE = "#FBFCF7"
FONT_REGULAR = r"C:\Windows\Fonts\NotoSansSC-VF.ttf"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


def extract_screens() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    missing = {output: source for output, source in SCREENS.items() if not (ASSETS / output).exists()}
    if not missing:
        return
    if not SOURCE_ZIP.exists():
        raise FileNotFoundError(f"Source screenshots missing: {SOURCE_ZIP}")
    with ZipFile(SOURCE_ZIP) as archive:
        for output, source in missing.items():
            (ASSETS / output).write_bytes(archive.read(source))


def round_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: object) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle((x0 + radius, y0, x1 - radius, y1), fill=fill)
    draw.rectangle((x0, y0 + radius, x1, y1 - radius), fill=fill)
    draw.ellipse((x0, y0, x0 + radius * 2, y0 + radius * 2), fill=fill)
    draw.ellipse((x1 - radius * 2, y0, x1, y0 + radius * 2), fill=fill)
    draw.ellipse((x0, y1 - radius * 2, x0 + radius * 2, y1), fill=fill)
    draw.ellipse((x1 - radius * 2, y1 - radius * 2, x1, y1), fill=fill)


def round_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    round_rect(ImageDraw.Draw(mask), (0, 0, size[0] - 1, size[1] - 1), radius, 255)
    return mask


def add_phone(canvas: Image.Image, path: Path, x: int, y: int, width: int) -> tuple[int, int]:
    src = Image.open(BytesIO(path.read_bytes())).convert("RGB")
    height = round(width * src.height / src.width)
    pad = 12
    outer = (width + pad * 2, height + pad * 2)
    shadow = Image.new("RGBA", canvas.size)
    sd = ImageDraw.Draw(shadow)
    round_rect(sd, (x - 4, y + 22, x + outer[0] + 4, y + outer[1] + 36), 47, (0, 0, 0, 100))
    shadow = shadow.filter(ImageFilter.GaussianBlur(42))
    canvas.alpha_composite(shadow)
    frame = Image.new("RGBA", outer, WHITE)
    frame.putalpha(round_mask(outer, 48))
    canvas.alpha_composite(frame, (x, y))
    screen = src.resize((width, height), Image.LANCZOS).convert("RGBA")
    screen.putalpha(round_mask((width, height), 39))
    canvas.alpha_composite(screen, (x + pad, y + pad))
    return outer


def main() -> None:
    extract_screens()
    canvas = Image.new("RGBA", (W, H), CREAM)
    draw = ImageDraw.Draw(canvas)

    # A spacious brand and message area replaces the earlier feature inventory.
    logo = Image.open(BytesIO(LOGO.read_bytes())).convert("RGBA").resize((118, 118), Image.LANCZOS)
    canvas.alpha_composite(logo, (148, 139))
    draw.text((294, 151), "HealthMate", font=font(66, True), fill=INK)
    draw.text((300, 224), "个人健康 Agent 工作台", font=font(30), fill=MUTED)
    round_rect(draw, (150, 365, 553, 428), 31, "#E4EEBF")
    draw.text((182, 378), "微信小程序 · 健康陪伴", font=font(30, True), fill=INK)

    draw.text((143, 490), "让健康管理，", font=font(126, True), fill=INK)
    draw.text((143, 644), "真正融入每一天。", font=font(126, True), fill=INK)
    draw.text((154, 860), "记录日常、得到建议、看见变化。", font=font(50), fill=MUTED)

    # Dark product stage with one subtle halo behind the real UI screenshots.
    stage_top, stage_bottom = 1100, 2910
    draw.rectangle((0, stage_top, W, stage_bottom), fill=DARK)
    halo = Image.new("RGBA", canvas.size)
    hd = ImageDraw.Draw(halo)
    hd.ellipse((495, 1110, 1885, 2760), fill=(156, 190, 99, 48))
    hd.ellipse((1000, 1390, 2380, 2760), fill=(93, 139, 76, 30))
    canvas.alpha_composite(halo.filter(ImageFilter.GaussianBlur(115)))
    draw = ImageDraw.Draw(canvas)
    draw.text((150, 1173), "从今天的一餐，到更好的下一步", font=font(53, True), fill=WHITE)
    draw.text((152, 1254), "产品界面 · 一天的健康日常", font=font(30), fill="#B8C9B7")

    # Side phones support the central companion screen without a dense grid.
    add_phone(canvas, ASSETS / "record.jpg", 120, 1510, 540)
    add_phone(canvas, ASSETS / "report.jpg", 1710, 1510, 540)
    add_phone(canvas, ASSETS / "companion.jpg", 875, 1350, 625)

    draw = ImageDraw.Draw(canvas)
    captions = [
        (142, "01", "随手记录", "拍照识别饮食"),
        (858, "02", "贴身陪伴", "小康、小健在场"),
        (1730, "03", "看见变化", "周报与趋势"),
    ]
    for x, number, title, detail in captions:
        draw.text((x, 2785), number, font=font(28, True), fill=LIME)
        draw.text((x + 70, 2770), title, font=font(45, True), fill=WHITE)
        draw.text((x + 70, 2830), detail, font=font(29), fill="#BDCCBE")

    # One short loop makes the product promise readable from poster distance.
    round_rect(draw, (150, 3026, 2250, 3236), 43, "#E7EFCB")
    draw.text((238, 3071), "记录", font=font(63, True), fill=INK)
    draw.text((666, 3063), "→", font=font(74), fill="#768D64")
    draw.text((1063, 3071), "建议", font=font(63, True), fill=INK)
    draw.text((1489, 3063), "→", font=font(74), fill="#768D64")
    draw.text((1880, 3071), "复盘", font=font(63, True), fill=INK)
    draw.text((155, 3302), "HealthMate  /  让健康变成做得到的事", font=font(31), fill=MUTED)

    rgb = canvas.convert("RGB")
    png_path = HERE / "HealthMate_简洁版海报.png"
    pdf_path = HERE / "HealthMate_简洁版海报.pdf"
    rgb.save(png_path, optimize=True)
    rgb.save(pdf_path, "PDF", resolution=240.0)
    print(png_path)
    print(pdf_path)


if __name__ == "__main__":
    main()
