"""Render two source-aligned HealthMate architecture diagrams as equal-size SVGs."""

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
W, H = 1600, 1000

INK = "#263342"
MUTED = "#667085"
LINE = "#64748B"
BG = "#FEFFFE"
GREEN = "#DFF2E9"
BLUE = "#DCEAF7"
LAVENDER = "#EAE5F6"
PEACH = "#F8E6D4"
YELLOW = "#FAECCB"
PINK = "#F5DFDC"
GREY = "#EEF1F3"


class SVG:
    def __init__(self, title: str, desc: str):
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">',
            f'<title id="title">{escape(title)}</title>',
            f'<desc id="desc">{escape(desc)}</desc>',
            '<defs><marker id="arrow" markerWidth="11" markerHeight="11" refX="9" refY="5.5" '
            'orient="auto" markerUnits="strokeWidth"><path d="M1 1 L9 5.5 L1 10" '
            f'fill="none" stroke="{LINE}" stroke-width="1.8"/></marker></defs>',
            f'<rect x="0" y="0" width="{W}" height="{H}" fill="{BG}"/>',
        ]

    def add(self, fragment: str):
        self.parts.append(fragment)

    def rect(self, x, y, w, h, fill, *, stroke=LINE, sw=2, rx=12, dash=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{extra}/>' )

    def text(self, x, y, value, *, size=26, weight=400, color=INK, anchor="middle", spacing=None):
        extra = f' letter-spacing="{spacing}"' if spacing is not None else ""
        self.add(f'<text x="{x}" y="{y}" text-anchor="{anchor}" dominant-baseline="middle" '
                 f'font-family="Microsoft YaHei, Noto Sans CJK SC, Arial, sans-serif" '
                 f'font-size="{size}" font-weight="{weight}" fill="{color}"{extra}>{escape(str(value))}</text>')

    def line(self, x1, y1, x2, y2, *, arrow=False, dash=None, sw=2.4, color=LINE):
        extra = (' marker-end="url(#arrow)"' if arrow else '') + (f' stroke-dasharray="{dash}"' if dash else '')
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                 f'stroke="{color}" stroke-width="{sw}"{extra}/>' )

    def path(self, d, *, arrow=False, dash=None, sw=2.4, color=LINE):
        extra = (' marker-end="url(#arrow)"' if arrow else '') + (f' stroke-dasharray="{dash}"' if dash else '')
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{extra}/>' )

    def node(self, x, y, w, h, fill, title, subtitle=None, *, title_size=27, sub_size=19):
        self.rect(x, y, w, h, fill)
        if subtitle:
            self.text(x+w/2, y+h/2-13, title, size=title_size, weight=500)
            self.text(x+w/2, y+h/2+20, subtitle, size=sub_size, color=MUTED)
        else:
            self.text(x+w/2, y+h/2, title, size=title_size, weight=500)

    def title_block(self, title, subtitle, number):
        self.text(75, 66, number, size=27, weight=500, color=MUTED, anchor="start")
        self.text(156, 65, title, size=44, weight=500, anchor="start")
        self.text(158, 113, subtitle, size=21, color=MUTED, anchor="start")
        self.line(54, 147, 1546, 147, sw=1.5, color="#CBD5E1")

    def save(self, name):
        self.add('</svg>')
        path = OUT / name
        path.write_text('\n'.join(self.parts), encoding='utf-8')
        return path


def architecture():
    s = SVG(
        "HealthMate 多 Agent ReAct Harness 架构",
        "小健、小康、小管家共享安全与上下文层；Router 路由至领域 Agent，领域 Agent 在受限工具中进行 ReAct，Decision 输出唯一答复，写操作等待用户确认。",
    )
    s.title_block("Health Agent Harness｜多 Agent 协作架构", "人格决定表达 · 领域 Agent 决定任务 · Harness 决定权限与执行", "01")

    # Product personas: three entrances, one kernel.
    for x, name, sub in (
        (230, "小健", "训练与自律"),
        (650, "小康", "饮食与恢复"),
        (1070, "小管家", "记录与计划"),
    ):
        s.node(x, 177, 300, 70, GREEN, name, sub, title_size=25, sub_size=18)
        s.line(x+150, 247, x+150, 271, sw=2)
    s.line(380, 271, 1220, 271)
    s.line(800, 271, 800, 298, arrow=True)
    s.node(500, 302, 600, 64, BLUE, "输入安全与上下文装配", "身份 · 目标 · 结构化记忆 · 已授权数据", title_size=25, sub_size=18)
    s.line(800, 366, 800, 398, arrow=True)
    s.node(590, 402, 420, 68, BLUE, "Router Agent", "单领域规则直达；复杂问题按需分派", title_size=27, sub_size=18)
    s.line(800, 470, 800, 500, arrow=True)

    # Scoped worker pool and the bounded ReAct loop.
    s.rect(270, 504, 1060, 267, "#F8F7FC", stroke="#86799C", dash="11 7", rx=18)
    s.text(309, 531, "领域子 Agent · 最小权限并行协作", size=25, weight=500, anchor="start")
    s.text(1294, 531, "选择 1–3 个", size=18, color=MUTED, anchor="end")
    workers = [
        (300, 555, "Planner", "计划"), (648, 555, "Coach", "训练"), (996, 555, "Nutritionist", "营养"),
        (300, 624, "Recovery", "恢复"), (648, 624, "Records", "记录"), (996, 624, "General", "通用"),
    ]
    for x, y, name, sub in workers:
        s.node(x, y, 302, 56, LAVENDER, f"{name}  /  {sub}", title_size=22)
    s.rect(340, 696, 920, 52, GREY, stroke="#A8B1BA", rx=8)
    s.text(800, 722, "子循环：请求注册工具  →  观察返回结果  →  继续或收束（最多 3 步）", size=21, weight=500)

    s.line(800, 771, 800, 808, arrow=True)
    s.node(590, 812, 420, 67, PINK, "Decision Agent", "核对证据与冲突，形成唯一答复", title_size=27, sub_size=18)
    s.line(800, 879, 800, 909, arrow=True)
    s.node(447, 913, 706, 62, YELLOW, "输出安全复核 → 建议 / 待确认提案", "写入必须通过用户确认接口；保留工具与路由轨迹", title_size=24, sub_size=17)

    # Supporting seams, deliberately outside the model's execution authority.
    s.node(45, 402, 190, 86, GREY, "模型网关", "云端 / 本地适配", title_size=23, sub_size=17)
    s.path("M235 445 H350 V436 H582", dash="7 6", arrow=True, sw=2)
    s.node(45, 601, 190, 86, GREEN, "长期记忆", "偏好 / 来源 / 有效期", title_size=23, sub_size=17)
    s.line(235, 644, 263, 644, arrow=True, dash="7 6", sw=2)
    s.node(1365, 558, 190, 86, PEACH, "Tool Registry", "权限 / 确认 / 审计", title_size=22, sub_size=17)
    s.line(1360, 601, 1338, 601, arrow=True, dash="7 6", sw=2)
    s.node(1365, 686, 190, 86, GREY, "健康领域服务", "记录 / 计划 / 知识", title_size=22, sub_size=17)
    s.line(1460, 686, 1460, 652, arrow=True, dash="7 6", sw=2)
    return s.save('health-agent-architecture.svg')


def invocation():
    s = SVG(
        "HealthMate 制定计划的软件调用时序",
        "用户在小程序或 Android 端提出制定计划请求；API 校验身份和授权；Harness 路由 Planner，并调用工具与模型；Decision 形成待确认草案；用户确认后才由 Action 接口入库。",
    )
    s.title_block("一次“制定计划”请求如何运转", "单领域请求规则直达 Planner；复杂请求另由 Router 选择多个领域 Agent", "02")
    headers = [
        (45, "用户", "选择目标", GREEN),
        (305, "小程序 / Android", "文字与语音界面", GREEN),
        (565, "FastAPI", "接口与身份", BLUE),
        (825, "Harness Kernel", "协作编排", LAVENDER),
        (1085, "领域工具", "业务服务 / 存储", PEACH),
        (1345, "模型网关", "文本模型适配", GREY),
    ]
    centers = [145, 405, 665, 925, 1185, 1445]
    for x, title, sub, fill in headers:
        s.node(x, 178, 210, 70, fill, title, sub, title_size=22, sub_size=16)
    for x in centers:
        s.line(x, 249, x, 949, dash="6 7", sw=1.5, color="#B9C2CA")

    def call(y, source, target, label, *, color=LINE):
        x1, x2 = centers[source], centers[target]
        dx = 13 if x2 > x1 else -13
        s.line(x1+dx, y, x2-dx, y, arrow=True, sw=2.6, color=color)
        s.text((x1+x2)/2, y-22, label, size=19, weight=500)

    call(293, 0, 1, "01 说出 / 输入计划目标")
    call(353, 1, 2, "02 语音先转写，再提交请求")
    call(413, 2, 3, "03 身份、安全与能力授权")
    call(473, 3, 5, "04 规则直达 Planner，首轮推理")
    call(533, 3, 4, "05 ReAct：Planner 读取约束")
    call(593, 4, 3, "06 Observation：事实与缺口")
    call(653, 3, 5, "07 Worker 收束 → Decision")
    call(713, 3, 2, "08 安全复核，生成计划草案")
    call(773, 2, 1, "09 计划页 + 悬浮会话审阅")
    call(833, 0, 1, "10 用户确认草案")
    call(893, 1, 2, "11 确认请求")
    call(946, 2, 4, "12 Action 校验 / 幂等 / 入库")
    s.text(800, 983, "关键边界：草案不直接写库；确认后才执行 Action", size=17, color=MUTED)
    return s.save('health-agent-call-flow.svg')


if __name__ == '__main__':
    print(architecture())
    print(invocation())
