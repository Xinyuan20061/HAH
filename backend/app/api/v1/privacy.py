import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.database import engine
from app.models import PrivacyAudit
from app.schemas.errors import ApiException
from app.services.agent.actions import ACTION_REGISTRY, execute_action
from app.services.media_reconciliation import (
    deletion_summary,
    finalize_account_deletion,
)
from app.services.privacy import (
    build_export_zip,
    cloud_file_ids,
    export_preview,
)

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.get("/policy")
def privacy_policy(user=Depends(current_user)):
    backend = settings.storage_backend.lower()
    if backend == "cloud_ref":
        media_storage = "微信 CloudBase 云存储（后端仅保存 fileID 与短期下载地址）"
    elif backend == "s3":
        media_storage = "S3 兼容对象存储"
    else:
        media_storage = "本地开发存储（生产环境请切换 CloudBase）"
    return {
        "database": "MySQL 持久化"
        if engine.dialect.name == "mysql"
        else "SQLite 本地开发库",
        "media_storage": media_storage,
        "api_key": "仅服务端密文保存；接口只返回是否已配置和脱敏提示，不回传明文。",
        "ai_processing": "动作视频由授权的本地 AI Worker 处理；识餐图片会先在 Worker 校验和压缩，并按 VLM_PROVIDER 发送到本地模型或 DeepSeek 视觉服务。Worker 不持有数据库密码或用户 JWT。动作证据预览默认 7 天后自动清除图片，仅保留结构化事件。",
        "logs": "请求日志不记录 body、Authorization、Cookie、API Key、媒体临时 URL 或原始聊天内容。",
        "export": "用户可导出结构化个人数据；导出包不包含 API Key 明文、临时下载 URL 和服务端密钥。",
        "deletion": "二次确认后删除账户数据库记录；CloudBase 媒体先由小程序调用 wx.cloud.deleteFile 删除。",
        "medical": "HealthMate 不执行疾病诊断、处方或药物调整。",
    }


class ConfirmIn(BaseModel):
    confirmation: str = ""
    cloud_files_deleted: list[str] = Field(default_factory=list)


@router.get("/export/preview")
def preview(user=Depends(current_user), db: Session = Depends(get_db)):
    return export_preview(db, user.id)


@router.get("/cloud-media")
def list_cloud_media(user=Depends(current_user), db: Session = Depends(get_db)):
    files = cloud_file_ids(db, user.id)
    return {"file_ids": files, "count": len(files)}


@router.post("/export")
def export_data(
    body: ConfirmIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    if body.confirmation != "EXPORT":
        raise HTTPException(400, "请输入 EXPORT 确认导出")
    holder = {}

    def perform():
        data = build_export_zip(db, user.id)
        holder["data"] = data
        db.add(
            PrivacyAudit(
                user_id=user.id,
                action="data_export",
                status="completed",
                detail_json=json.dumps({"bytes": len(data)}, ensure_ascii=False),
            )
        )
        db.flush()
        return {"bytes": len(data)}

    action = execute_action(
        db,
        user.id,
        "privacy.export",
        perform,
        confirmed=True,
        source="user",
        input_data={"confirmation": "EXPORT"},
    )
    if not action["executed"]:
        raise HTTPException(403, "导出动作被安全策略阻止")
    return Response(
        content=holder["data"],
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="healthmate-export.zip"',
            "X-Action-Audit-ID": str(action["audit_id"]),
        },
    )


@router.delete("/account")
def delete_account(
    body: ConfirmIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Delete the account with a server-verifiable media ledger (spec §10.2).

    The typed confirmation is mandatory — a generic ``confirmation=true`` cannot
    authorize this action. CloudBase objects cannot be removed without the user's
    WeChat session, so whatever the server cannot itself verify is reported as
    ``manual_review`` instead of a blanket success.
    """
    if body.confirmation != "DELETE MY DATA":
        raise ApiException(
            422,
            "TYPED_CONFIRMATION_REQUIRED",
            "请输入 DELETE MY DATA 进行二次确认",
            details={"required_confirmation": "DELETE MY DATA"},
        )
    spec = ACTION_REGISTRY["privacy.account.delete"]
    if not spec.requires_confirmation:
        raise ApiException(
            500, "DELETION_POLICY_MISCONFIGURED", "删除策略配置异常"
        )
    files = cloud_file_ids(db, user.id)
    if set(files) != set(body.cloud_files_deleted):
        raise ApiException(
            409,
            "CLOUD_MEDIA_NOT_DELETED",
            "请先由小程序删除全部关联 CloudBase 文件，再提交删除确认；"
            "后端无法替您删除云文件",
            details={"status": "client_deletion_incomplete"},
        )
    result = finalize_account_deletion(db, user_id=user.id, reason="user_request")
    return {
        "ok": True,
        "deleted": True,
        # Kept for one release cycle: the client still reads this value, and it
        # is genuinely a *client* report — it is not the deletion verification.
        "cloud_media_deletion": "client_reported" if files else "no_cloud_files",
        **result,
        "message": "账户与关联健康数据已删除，当前登录凭证将不再有效。",
    }


@router.get("/deletion-status")
def deletion_status(user=Depends(current_user), db: Session = Depends(get_db)):
    """What the server can actually prove about this account's media deletion."""
    return deletion_summary(db, user.id)
