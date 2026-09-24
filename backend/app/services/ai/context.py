from app.models import HealthProfile


def build_system_prompt(profile: HealthProfile | None, summary: dict) -> str:
    p = profile
    profile_text = (
        "用户尚未完善健康档案"
        if not p
        else f"年龄{p.age}岁，身高{p.height_cm}cm，体重{p.weight_kg}kg，目标={p.goal_type}，活动水平={p.activity_level}，饮食偏好={p.diet_preference or '无'}，过敏={p.allergies or '无'}"
    )
    return (
        "你是 HealthMate 健康生活助手。你的职责是提供一般性的生活方式、饮食、运动和健康知识帮助，而不是诊断疾病或替代医生。\n"
        f"用户档案：{profile_text}\n"
        f"今日状态：摄入 {summary['calories']}/{summary['calorie_target']} kcal，蛋白质 {summary['protein']}/{summary['protein_target']} g，运动 {summary['exercise_min']}/{summary['exercise_target']} 分钟。\n"
        "原则：建议要具体、克制、可执行；涉及紧急症状时优先建议寻求当地紧急医疗帮助；不要擅自修改药物剂量。"
    )
