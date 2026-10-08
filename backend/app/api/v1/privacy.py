import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import engine, get_db
from app.models import PrivacyAudit
from app.schemas.errors import ApiException
from app.services.agent.actions import ACTION_REGISTRY, execute_action
from app.services.cloudbase_storage import CloudBaseStorageAdmin
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
        media_storage = "微信云存储"
    elif backend == "s3":
        media_storage = "云端对象存储"
    else:
        media_storage = "本地测试存储"
    return {
        "database": "微信云托管服务"
        if engine.dialect.name == "mysql"
        else "本地测试服务",
        "media_storage": media_storage,
        "api_key": "你设置的服务密钥会加密保存在服务端，不会在应用中显示。",
        "ai_processing": "餐食图片和动作视频按当前服务设置处理。开启云端动作复核前会单独征求同意；动作预览图片最多保留 7 天。",
        "logs": "服务日志用于排查故障，不记录聊天内容、访问令牌或服务密钥。",
        "export": "可以导出个人资料、记录、计划和处理历史；导出文件不含服务密钥、媒体文件或临时访问链接。",
        "deletion": "确认后会删除账户记录，并检查关联的云端媒体；尚未完成的项目会继续重试。",
        "medical": "用于日常健康管理，不提供疾病诊断、处方或药物调整。",
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
    """Delete the account with a server-verifiable media ledger (spec §10.2)."""
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
    cloudbase_admin = CloudBaseStorageAdmin()
    client_reported: set[str] = set()
    if files and not cloudbase_admin.configured:
        if set(files) != set(body.cloud_files_deleted):
            raise ApiException(
                409,
                "CLOUD_MEDIA_NOT_DELETED",
                "服务端 CloudBase 删除凭据未配置，请先在微信端删除全部关联文件后重试",
                details={"status": "client_deletion_incomplete"},
            )
        client_reported = set(body.cloud_files_deleted)
    result = finalize_account_deletion(
        db,
        user_id=user.id,
        reason="user_request",
        client_reported_cloud_file_ids=client_reported,
    )
    if not files:
        cloud_media_state = "no_cloud_files"
    elif client_reported:
        cloud_media_state = "client_reported"
    elif result.get("cloud_media_objects_verified") == len(files):
        cloud_media_state = "server_verified"
    elif result.get("cloud_media_objects_pending"):
        cloud_media_state = "pending"
    else:
        cloud_media_state = "partial"
    result["cloud_media_deletion"] = cloud_media_state
    result["message"] = (
        "账户与关联数据已删除，媒体删除仍在安全重试。"
        if result.get("verification") == "pending"
        else "账户与关联健康数据已删除，当前登录凭证将不再有效。"
    )
    return {
        "ok": True,
        "deleted": True,
        **result,
    }


@router.get("/deletion-status")
def deletion_status(user=Depends(current_user), db: Session = Depends(get_db)):
    """What the server can actually prove about this account's media deletion."""
    return deletion_summary(db, user.id)
