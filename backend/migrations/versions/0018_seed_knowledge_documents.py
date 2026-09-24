"""Seed the audited health knowledge base with authoritative public sources.

Revision ID: 0018_seed_knowledge_documents
Revises: 0017_motion_six_action_baseline

Adds eight chunks on top of the four seeded in 0010_knowledge_rag (total
knowledge base: 12 documents). Each chunk is a short, human-reviewed summary
of a public authoritative document: WHO 2020 physical activity guidelines
(sedentary behaviour), 中国居民膳食指南 2022 (少盐少油控糖限酒),
国家卫健委 高血压营养和运动指导原则 2024, 中国2型糖尿病运动治疗指南 2024,
国家体育总局 深蹲要点, 中国新闻网 俯卧撑要点, 杭州市中医院 箭步蹲要点.
Titles/organizations/URLs are kept verbatim from the upstream sources so every
retrieved citation is traceable.
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0018_seed_knowledge_documents"
down_revision = "0017_motion_six_action_baseline"
branch_labels = None
depends_on = None

KNOWLEDGE_DOCUMENTS = [
    {
        "source_key": "who-pa-sedentary-2020",
        "title": "世卫组织关于身体活动和久坐行为的指南（2020）",
        "organization": "世界卫生组织",
        "source_url": "https://www.who.int/zh/news/item/25-11-2020-every-move-counts-towards-better-health-says-who",
        "source_published_at": "2020-11-25",
        "section": "久坐行为",
        "content": (
            "指南指出减少久坐行为、增加身体活动有利于健康。建议用任何强度的身体活动（包括轻活动）"
            "替代久坐时间；久坐时间过长本身与全因死亡和心血管疾病风险升高相关。"
        ),
        "tags_json": '["久坐","身体活动不足","静坐时间"]',
    },
    {
        "source_key": "nhc-diet-salt-oil-sugar-2022",
        "title": "中国居民膳食指南（2022）少盐少油，控糖限酒",
        "organization": "中国营养学会",
        "source_url": "https://www.cnsoc.org/notice/442220200.html",
        "source_published_at": "2022-04-26",
        "section": "准则五 少盐少油，控糖限酒",
        "content": (
            "培养清淡饮食习惯，少吃高盐和油炸食品。成年人每天摄入食盐不超过5克，烹调油25至30克；"
            "控制添加糖每天不超过50克，最好控制在25克以下；反式脂肪酸每天摄入量不超过2克；"
            "不喝或少喝含糖饮料。"
        ),
        "tags_json": '["少盐","少油","控糖","食盐5克","膳食"]',
    },
    {
        "source_key": "nhc-hypertension-exercise-2024",
        "title": "高血压营养和运动指导原则（2024年版）",
        "organization": "国家卫生健康委员会",
        "source_url": "https://www.gov.cn/zhengce/zhengceku/202407/P020240701700333981869.pdf",
        "source_published_at": "2024-07",
        "section": "运动指导原则",
        "content": (
            "高血压患者应坚持有规律的运动、保持充足身体活动、减少久坐时间。以有氧运动为主，"
            "中等强度有氧运动每周至少150分钟；提倡结合多种形式的抗阻训练并辅以柔韧性训练。"
            "适度量力、循序渐进，避免突然大幅度增加运动强度、时间、频率或类型。"
            "血压未有效控制或合并严重并发症者应在医生指导下运动。"
        ),
        "tags_json": '["高血压","运动","150分钟","有氧","抗阻训练"]',
    },
    {
        "source_key": "nhc-hypertension-nutrition-2024",
        "title": "高血压营养和运动指导原则（2024年版）",
        "organization": "国家卫生健康委员会",
        "source_url": "https://www.gov.cn/zhengce/zhengceku/202407/P020240701700333981869.pdf",
        "source_published_at": "2024-07",
        "section": "营养指导原则",
        "content": (
            "高血压患者应坚持植物性食物为主、动物性食物适量的膳食模式，做到食物多样、"
            "三大营养素供能比例适当、盐不超量（每天食盐摄入不超过5克），并戒烟限酒。"
        ),
        "tags_json": '["高血压","膳食","限盐","少盐","营养"]',
    },
    {
        "source_key": "cma-t2dm-exercise-2024",
        "title": "中国2型糖尿病运动治疗指南（2024版）",
        "organization": "中华医学会糖尿病学分会",
        "source_url": "https://rs.yiigle.com/cmaid/1508094",
        "source_published_at": "2024-10",
        "section": "运动处方",
        "content": (
            "指南推荐2型糖尿病患者每周至少累计进行150至300分钟中等强度有氧运动，"
            "或75至150分钟较大强度有氧运动，或等效组合；每周进行2至3天规律抗阻运动。"
            "无经验者可从小负荷开始，循序渐进。运动治疗应在评估并发症和低血糖风险后进行。"
        ),
        "tags_json": '["糖尿病","运动","有氧","抗阻","150分钟"]',
    },
    {
        "source_key": "nhc-diet-variety-2022",
        "title": "中国居民膳食指南（2022）食物多样，合理搭配",
        "organization": "中国疾病预防控制中心",
        "source_url": "https://www.chinacdc.cn/jkkp/yyjk/jbyy/202408/t20240825_295601.html",
        "source_published_at": "2023-12-25",
        "section": "食物多样量化建议",
        "content": (
            "按照中国居民膳食指南（2022）推荐，每天食物种类应达到12种以上："
            "谷薯类至少3种、蔬菜水果类至少4种、鱼禽肉蛋类至少3种、奶类大豆和坚果类至少2种；"
            "每周达到25种以上。每餐尽量多吃新鲜蔬菜水果，适量增加鱼虾、奶类和大豆制品。"
        ),
        "tags_json": '["食物多样","12种","25种","膳食","合理搭配"]',
    },
    {
        "source_key": "sport-gov-squat",
        "title": "深蹲益处多 全龄练习有讲究",
        "organization": "国家体育总局",
        "source_url": "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c29401699/content.html",
        "source_published_at": "2026-02-04",
        "section": "动作要点",
        "content": (
            "深蹲起始时双脚与肩同宽、脚尖可稍外展；动作中想象臀部向后下方坐，核心收紧，"
            "背部自然挺直，膝盖方向与脚尖一致，下蹲至大腿平行于地面或力所能及的幅度，"
            "然后发力站起。呼吸配合：下蹲吸气、起身呼气，切忌憋气。"
            "初学者若力量或平衡不足，可从靠墙深蹲或扶椅辅助开始，循序渐进。"
        ),
        "tags_json": '["深蹲","动作要点","膝盖","核心收紧","自重训练"]',
    },
    {
        "source_key": "chinanews-pushup",
        "title": "居家锻炼，从做好一个标准俯卧撑开始",
        "organization": "中国新闻网",
        "source_url": "https://www.chinanews.com.cn/m/ty/2022/05-07/9748293.shtml",
        "source_published_at": "2022-05-07",
        "section": "动作要点",
        "content": (
            "标准俯卧撑从双手撑地开始，手与肩同宽或稍宽，肩膀和手腕在同一直线，手指稍分开、"
            "指尖朝前；收紧核心肌群和臀大肌，臀部保持水平，身体从头到脚保持一条直线；"
            "肩胛骨向后拉伸避免耸肩，屈肘下降至胸部接近地面后推起。"
        ),
        "tags_json": '["俯卧撑","动作要点","核心收紧","身体成直线"]',
    },
    {
        "source_key": "hztcm-lunge",
        "title": "箭步蹲动作要点（运动处方示例）",
        "organization": "杭州市中医院",
        "source_url": "https://hztcm.cn/plot/detail/html/10682.html",
        "source_published_at": "2026-08",
        "section": "动作要点",
        "content": (
            "箭步蹲：双脚打开与肩同宽，一侧脚向后迈出，头、背、臀、踝保持在一条直线上；"
            "重心放在前脚，膝盖对准脚尖方向，身体微微俯身，前脚踩地发力起身；"
            "两侧交替进行，保持躯干稳定、膝盖不内扣。"
        ),
        "tags_json": '["箭步蹲","弓步","动作要点","膝盖","下肢训练"]',
    },
]


def upgrade():
    connection = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    table = sa.table(
        "knowledge_documents",
        sa.column("source_key", sa.String),
        sa.column("title", sa.String),
        sa.column("organization", sa.String),
        sa.column("source_url", sa.String),
        sa.column("source_published_at", sa.String),
        sa.column("section", sa.String),
        sa.column("content", sa.Text),
        sa.column("tags_json", sa.Text),
        sa.column("active", sa.Boolean),
        sa.column("reviewed_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    existing = {
        row[0]
        for row in connection.execute(
            sa.text("SELECT source_key FROM knowledge_documents")
        ).fetchall()
    }
    rows = [
        {**item, "active": True, "reviewed_at": now, "created_at": now, "updated_at": now}
        for item in KNOWLEDGE_DOCUMENTS
        if item["source_key"] not in existing
    ]
    if rows:
        op.bulk_insert(table, rows)


def downgrade():
    connection = op.get_bind()
    keys = [item["source_key"] for item in KNOWLEDGE_DOCUMENTS]
    placeholders = ", ".join(f":key{index}" for index in range(len(keys)))
    connection.execute(
        sa.text(f"DELETE FROM knowledge_documents WHERE source_key IN ({placeholders})"),
        {f"key{index}": key for index, key in enumerate(keys)},
    )
