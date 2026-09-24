from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.models import SafetyEvent
from app.services.safety import MEDICAL_DISCLAIMER

router = APIRouter(prefix="/safety", tags=["safety"])


@router.get("/policy")
def policy(user=Depends(current_user)):
    return {
        "disclaimer": MEDICAL_DISCLAIMER,
        "boundaries": [
            "不诊断疾病",
            "不提供处方",
            "不建议自行停药/加药/换药",
            "不生成极端节食、催吐或泻药减重方案",
            "急症和自伤风险优先升级处理",
        ],
        "data_policy": "高风险审计默认保存规则类别、哈希和脱敏片段，不把完整敏感对话写入安全日志。",
    }


@router.get("/events/summary")
def event_summary(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(SafetyEvent.category, func.count(SafetyEvent.id))
        .where(SafetyEvent.user_id == user.id)
        .group_by(SafetyEvent.category)
    ).all()
    return {"total": sum(x[1] for x in rows), "by_category": {x[0]: x[1] for x in rows}}
