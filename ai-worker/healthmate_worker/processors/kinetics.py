"""Kinetics-400 standalone job processor (recognition-only, no scoring).

This backs the formal ``kinetics400`` capability: it runs the local SlowFast R50
official checkpoint (400 classes, no training) on a video and returns the top-k
Kinetics-400 labels with Chinese display names. Only ``squat`` / ``push up`` /
``lunge`` map back to a full rule analyzer (``mapped_exercise``); every other
recognized class is intentionally unscored — this result is a recognition-only
second opinion for exercises the six analyzers cannot count or score.
"""

from __future__ import annotations

from ..errors import ProcessingError
from ..models.kinetics_runtime import (
    KINETICS_TO_EXERCISE,
    map_kinetics_label,
    recognize_video_kinetics400,
    slugify_kinetics,
)

# Curated Chinese display names for fitness-relevant Kinetics-400 labels.
# Labels not listed here fall back to the raw English label in the UI.
KINETICS_LABEL_ZH = {
    "squat": "深蹲",
    "push up": "俯卧撑",
    "lunge": "弓步",
    "pull ups": "引体向上",
    "bench pressing": "卧推",
    "deadlifting": "硬拉",
    "skipping rope": "跳绳",
    "front raises": "前平举",
    "situp": "卷腹",
    "yoga": "瑜伽",
    "tai chi": "太极",
    "stretching arm": "手臂拉伸",
    "stretching leg": "腿部拉伸",
    "swinging legs": "摆腿",
    "exercising with an exercise ball": "健身球训练",
    "doing aerobics": "有氧操",
    "zumba": "尊巴舞",
    "running on treadmill": "跑步机跑步",
    "snatch weight lifting": "举重抓举",
    "climbing a rope": "爬绳",
    "high kick": "高踢",
    "side kick": "侧踢",
    "punching bag": "打沙袋",
    "punching person (boxing)": "拳击",
    "jumpstyle dancing": "跳跃舞",
    "breakdancing": "霹雳舞",
    "robot dancing": "机械舞",
    "gymnastics tumbling": "体操翻腾",
    "hula hooping": "呼啦圈",
    "country line dancing": "乡村排舞",
    "belly dancing": "肚皮舞",
    "dancing ballet": "芭蕾舞",
    "tango dancing": "探戈",
    "tap dancing": "踢踏舞",
    "swing dancing": "摇摆舞",
    "salsa dancing": "萨尔萨舞",
    "playing badminton": "打羽毛球",
    "playing tennis": "打网球",
    "playing volleyball": "打排球",
    "playing basketball": "打篮球",
    "playing ice hockey": "打冰球",
    "playing kickball": "踢球",
    "dribbling basketball": "运球",
    "dunking basketball": "扣篮",
    "shooting basketball": "投篮",
    "juggling soccer ball": "颠球",
    "kicking soccer ball": "踢足球",
    "throwing ball": "投球",
    "disc golfing": "飞盘高尔夫",
    "golf chipping": "高尔夫切球",
    "golf driving": "高尔夫开球",
    "golf putting": "高尔夫推杆",
    "hopscotch": "跳房子",
    "long jump": "跳远",
    "triple jump": "三级跳",
    "high jump": "跳高",
    "ski jumping": "跳台滑雪",
    "bungee jumping": "蹦极",
    "jumping into pool": "跳水",
    "rock climbing": "攀岩",
    "ice climbing": "攀冰",
    "climbing tree": "爬树",
    "climbing ladder": "爬梯",
    "motorcycling": "骑摩托",
    "riding or walking with horse": "骑马",
    "chopping wood": "劈柴",
    "pushing cart": "推车",
    "pushing car": "推车",
    "pushing wheelchair": "推轮椅",
    "walking the dog": "遛狗行走",
    "throwing axe": "掷斧",
    "throwing discus": "掷铁饼",
    "bending back": "后弯腰",
    "drop kicking": "凌空踢",
    "hockey stop": "冰球急停",
    "juggling balls": "抛球杂耍",
}


def analyze_kinetics400(video_path: str, *, progress=None) -> dict:
    """Run the 400-class recognizer and shape a validated job result.

    The model is lazy-loaded once (thread-safe); when the checkpoint is missing
    or PyTorch is absent this raises a non-retryable ProcessingError instead of
    silently fabricating a result.
    """
    if progress:
        progress(20, "kinetics400")
    raw = recognize_video_kinetics400(video_path)
    if raw is None:
        raise ProcessingError(
            "kinetics400_unavailable",
            "Kinetics-400 模型不可用（未配置 checkpoint 或缺少 PyTorch）",
            False,
        )
    candidates = []
    for item in raw.get("candidates", []):
        label = str(item.get("label", ""))
        # Canonical id comes from the shared catalog (None when the catalog has no
        # exact mapping — keep the raw label, never force-map to six classes).
        canonical = map_kinetics_label(label)
        candidates.append(
            {
                "label": label,
                "label_zh": KINETICS_LABEL_ZH.get(label, ""),
                "class_index": int(item.get("class_index", -1)),
                "probability": round(float(item.get("probability", 0.0)), 4),
                "exercise_slug": slugify_kinetics(label),
                "mapped_exercise": canonical,
                "canonical_id": canonical,
            }
        )
    top_label = str(raw.get("top_label", ""))
    return {
        "method": "slowfast_kinetics400_v1",
        "top_label": top_label,
        "top_label_zh": KINETICS_LABEL_ZH.get(top_label, ""),
        "top_probability": round(float(raw.get("top_probability", 0.0)), 4),
        "mapped_exercise": raw.get("mapped_exercise"),
        "exercise_slug": raw.get("exercise_slug"),
        "candidates": candidates,
        "is_estimate": True,
        "scope": (
            "Kinetics-400 预训练 400 类动作识别；不含次数与评分，"
            "结果仅供健身参考，不代表医学判断。"
        ),
    }
