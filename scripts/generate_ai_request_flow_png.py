"""Render the HealthMate text AI request path as a compact landscape PNG."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


ROOT = Path.cwd()
OUT = ROOT / "docs" / "AI_REQUEST_SERVICE_FLOW.png"
SCALE = 2
WIDTH, HEIGHT = 2400, 1120

FONT_REGULAR = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"


def font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size * SCALE)


def rounded_fill(draw, coords, radius, fill):
    x0, y0, x1, y1 = coords
    r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    draw.rectangle((x0 + r, y0, x1 - r, y1), fill=fill)
    draw.rectangle((x0, y0 + r, x1, y1 - r), fill=fill)
    for cx in (x0 + r, x1 - r):
        for cy in (y0 + r, y1 - r):
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)


def box(draw, xy, fill, outline=None, radius=22, width=2, shadow=False):
    x0, y0, x1, y1 = (int(n * SCALE) for n in xy)
    if shadow:
        rounded_fill(draw, (x0 + 6 * SCALE, y0 + 9 * SCALE,
                            x1 + 6 * SCALE, y1 + 9 * SCALE),
                     radius * SCALE, "#DDE5DF")
    if outline:
        rounded_fill(draw, (x0, y0, x1, y1), radius * SCALE, outline)
        inset = width * SCALE
        rounded_fill(draw, (x0 + inset, y0 + inset, x1 - inset, y1 - inset),
                     max(0, radius * SCALE - inset), fill)
    else:
        rounded_fill(draw, (x0, y0, x1, y1), radius * SCALE, fill)


def label(draw, xy, value, size, fill, bold=False, anchor="la"):
    draw.text(
        (xy[0] * SCALE, xy[1] * SCALE),
        value,
        font=font(size, bold),
        fill=fill,
        anchor=anchor,
    )


def arrow(draw, start, end, color="#64786D", width=5, dashed=False):
    x0, y0 = start
    x1, y1 = end
    if dashed:
        segments = 7
        for i in range(0, segments, 2):
            a = i / segments
            b = min((i + 1) / segments, 0.90)
            draw.line(
                ((x0 + (x1 - x0) * a) * SCALE,
                 (y0 + (y1 - y0) * a) * SCALE,
                 (x0 + (x1 - x0) * b) * SCALE,
                 (y0 + (y1 - y0) * b) * SCALE),
                fill=color,
                width=width * SCALE,
            )
    else:
        draw.line((x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE),
                  fill=color, width=width * SCALE)
    if x1 > x0:
        points = [(x1, y1), (x1 - 16, y1 - 11), (x1 - 16, y1 + 11)]
    elif x1 < x0:
        points = [(x1, y1), (x1 + 16, y1 - 11), (x1 + 16, y1 + 11)]
    else:
        points = [(x1, y1), (x1 - 11, y1 - 16), (x1 + 11, y1 - 16)]
    draw.polygon([(int(x * SCALE), int(y * SCALE)) for x, y in points], fill=color)


def step(draw, number, x, title, subtitle, width=300, tint="#E4EFE7", subtitle_size=20):
    y = 226
    box(draw, (x, y, x + width, y + 162), "#FFFFFF", "#D5E0D8", 24, 2, True)
    box(draw, (x + 20, y + 20, x + 63, y + 63), tint, radius=13)
    label(draw, (x + 41, y + 42), f"{number:02d}", 19, "#315944", True, "mm")
    label(draw, (x + 23, y + 84), title, 29, "#173C2D", True)
    label(draw, (x + 23, y + 127), subtitle, subtitle_size, "#61746A")


image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), "#F6F9F6")
draw = ImageDraw.Draw(image)

# Header
box(draw, (70, 55, 202, 95), "#DBECE0", radius=16)
label(draw, (136, 75), "HealthMate", 19, "#315944", True, "mm")
label(draw, (70, 112), "AI 请求服务传播链路", 48, "#143B2B", True)
label(draw, (70, 179), "微信小程序对话请求  ·  入口 → 服务编排 → 模型 → 安全复核 → 返回", 23, "#60756A")

# Six-step main path
xs = [70, 425, 780, 1135, 1490, 1845]
items = [
    ("用户提问", "聊天页 / 悬浮 Agent"),
    ("请求传输", "streamPost · Bearer JWT"),
    ("API 接入", "Cloud Run · FastAPI"),
    ("安全与证据", "评估 · 授权数据 · 知识"),
    ("Agent 编排", "Router → Worker → Decision"),
    ("复核并返回", "审查 · 留痕 · 回传"),
]
for i, (title, subtitle) in enumerate(items):
    step(draw, i + 1, xs[i], title, subtitle, 485 if i == 5 else 300,
         "#E5F0E8" if i not in (3, 5) else "#E5EFF5",
         17 if i == 4 else 20)
    if i < 5:
        arrow(draw, (xs[i] + 301, 307), (xs[i + 1] - 9, 307))

# Safety short circuit
box(draw, (1135, 410, 1435, 455), "#FFF0DC", radius=15)
label(draw, (1285, 432), "风险拦截 → 安全规则答复", 18, "#9A6028", True, "mm")
arrow(draw, (1285, 388), (1285, 407), "#C39151", 3)

# Detail panels
box(draw, (70, 510, 690, 745), "#FFFFFF", "#D5E0D8", 22, 2, True)
label(draw, (98, 546), "请求入口", 27, "#173C2D", True)
box(draw, (97, 575, 663, 642), "#E8F2EC", radius=13)
label(draw, (116, 597), "公网 HTTPS", 20, "#315944", True)
label(draw, (293, 597), "POST /agent/respond/stream", 19, "#315944")
box(draw, (97, 652, 663, 719), "#EEF3F7", radius=13)
label(draw, (116, 674), "容器调用", 20, "#3F6680", True)
label(draw, (293, 674), "POST /agent/respond", 19, "#3F6680")
arrow(draw, (575, 389), (575, 506), "#8AA799", 3, True)

box(draw, (720, 510, 1320, 745), "#FFFFFF", "#D5E0D8", 22, 2, True)
label(draw, (748, 546), "数据与证据", 27, "#173C2D", True)
box(draw, (748, 580, 1292, 638), "#E8F2EC", radius=13)
label(draw, (769, 609), "业务数据库", 20, "#315944", True, "lm")
label(draw, (940, 609), "档案 / 记录 / 目标 / 健康状态", 19, "#315944", anchor="lm")
box(draw, (748, 652, 1292, 710), "#EEF3F7", radius=13)
label(draw, (769, 681), "已审核知识", 20, "#3F6680", True, "lm")
label(draw, (940, 681), "RAG 检索 / 资源", 19, "#3F6680", anchor="lm")

box(draw, (1350, 510, 2330, 745), "#FFFFFF", "#D5E0D8", 22, 2, True)
label(draw, (1378, 546), "模型调用与故障兜底", 27, "#173C2D", True)
box(draw, (1378, 580, 1814, 710), "#E8F2EC", radius=13)
label(draw, (1400, 606), "AI 网关", 22, "#315944", True)
label(draw, (1400, 645), "用户配置 → 系统配置", 19, "#315944")
label(draw, (1400, 678), "DeepSeek /chat/completions", 19, "#315944")
arrow(draw, (1819, 645), (1855, 645), "#8AA799", 4)
box(draw, (1864, 580, 2302, 710), "#FFF2E1", radius=13)
label(draw, (1886, 606), "异常 / 无效结果", 21, "#9A6028", True)
label(draw, (1886, 645), "非计划可尝试本地 Qwen", 18, "#9A6028")
label(draw, (1886, 678), "最终使用规则或知识摘要", 18, "#9A6028")
arrow(draw, (1640, 389), (1640, 506), "#8AA799", 3, True)

# Two outcome notes
box(draw, (70, 807, 1165, 974), "#E3F0E7", radius=22)
label(draw, (101, 845), "回答返回", 27, "#173C2D", True)
label(draw, (101, 893), "完整生成并完成安全复核后，才向小程序发送结果。", 21, "#315944")
label(draw, (101, 929), "分段格式：meta → stage → answer / delta → done", 20, "#315944")

box(draw, (1195, 807, 2330, 974), "#E8F0F5", radius=22)
label(draw, (1226, 845), "计划与行动写入", 27, "#244A64", True)
label(draw, (1226, 893), "模型只生成提案；用户确认后，独立接口才会写入。", 21, "#3F6680")
label(draw, (1226, 929), "/agent/actions/{id}/confirm  ·  /agent/runs/{id}/apply-plan", 19, "#3F6680")

# Implementation note, kept separate from the conceptual service path.
box(draw, (70, 1000, 2330, 1072), "#FFF4E6", radius=16)
label(draw, (95, 1036), "当前配置提示", 20, "#A86E28", True, "lm")
label(draw, (260, 1036), "公网 URL 已含 /api/v1，请求层又追加 /api/v1；流式请求可能失败后回退到一次性接口。", 20, "#8C632E", anchor="lm")

OUT.parent.mkdir(parents=True, exist_ok=True)
image.resize((WIDTH, HEIGHT), Image.LANCZOS).save(OUT, "PNG", optimize=True)
print(f"{OUT} ({WIDTH}x{HEIGHT})")
