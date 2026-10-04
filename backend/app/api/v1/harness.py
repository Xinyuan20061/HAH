from __future__ import annotations

import base64
import binascii
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.time import utc_now
from app.schemas.errors import ApiException
from app.harness import (
    HARNESS_VERSION,
    MULTI_AGENT_VERSION,
    get_persona,
    get_tool_registry,
    list_personas,
    list_workers,
)
from app.harness.plugins import (
    PluginError,
    _installation,
    _public_installation,
    audit_idempotent_result,
    audit_request_hash,
    default_config,
    effective_scopes,
    get_plugin,
    list_plugins,
    normalize_config,
    write_audit,
)
from app.models import AgentActionProposal, HarnessCapabilityAudit, HarnessPluginInstallation
from app.harness import voice as voice_gateway
from app.harness.voice import (
    VoiceGatewayError,
    VoiceNotConfigured,
    VoiceRejected,
)


router = APIRouter(prefix="/harness", tags=["health-harness"])


class VoiceTranscriptionRequest(BaseModel):
    agent_id: str = Field(max_length=30)
    audio_base64: str = Field(min_length=1)
    format: str = Field(default="mp3", max_length=10)
    request_id: str | None = Field(default=None, max_length=64)


class VoiceSynthesisRequest(BaseModel):
    agent_id: str = Field(max_length=30)
    text: str = Field(min_length=1, max_length=4000)
    request_id: str | None = Field(default=None, max_length=64)


class VoiceVerifyOnceRequest(BaseModel):
    provider: str = Field(default="tencent_cloud", max_length=30)
    check: str = Field(default="tts", pattern="^(asr|tts)$")
    acknowledge_quota: bool = False


class PluginEnableRequest(BaseModel):
    data_scope: list[str] = Field(default_factory=list, max_length=20)


class InstallationCreateRequest(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=80)
    config: dict = Field(default_factory=dict)


class InstallationPatchRequest(BaseModel):
    config_version: int = Field(ge=1)
    config: dict = Field(min_length=1)


class InstallationVersionRequest(BaseModel):
    config_version: int = Field(ge=1)


def _plugin_error(exc: PluginError):
    status = 404 if exc.code in {"PLUGIN_NOT_FOUND", "INSTALLATION_NOT_FOUND"} else 409 if exc.code in {"IDEMPOTENCY_PARAM_MISMATCH", "PLUGIN_CONFIG_VERSION_CONFLICT", "INSTALLATION_EXISTS"} else 422
    raise ApiException(status, exc.code, exc.message)


def _require_idempotency(key: str | None) -> str:
    if not key:
        raise ApiException(400, "IDEMPOTENCY_KEY_REQUIRED", "请重试这项操作；请求编号缺失")
    return key


def _owned_installation(db: Session, user_id: int, installation_id: int) -> HarnessPluginInstallation:
    row = db.get(HarnessPluginInstallation, installation_id)
    if row is None or row.user_id != user_id:
        raise ApiException(404, "INSTALLATION_NOT_FOUND", "这项能力配置不存在")
    return row


def _check_version(row: HarnessPluginInstallation, expected: int) -> None:
    if row.config_version != expected:
        raise ApiException(409, "PLUGIN_CONFIG_VERSION_CONFLICT", "配置已在别处更新，请刷新后再试", details={"current_version": row.config_version})


def _expire_pending_actions(db: Session, user_id: int, manifest, now) -> None:
    if not manifest.action_names:
        return
    proposals = db.scalars(select(AgentActionProposal).where(
        AgentActionProposal.user_id == user_id,
        AgentActionProposal.action_key.in_(manifest.action_names),
        AgentActionProposal.status.in_(("pending", "failed")),
    )).all()
    for proposal in proposals:
        proposal.status = "expired"
        try:
            display = json.loads(proposal.display_json or "{}")
        except (TypeError, ValueError):
            display = {}
        if not isinstance(display, dict):
            display = {}
        display["reason"] = "能力已暂停或授权配置发生变化，请按当前配置重新生成操作申请。"
        proposal.display_json = json.dumps(display, ensure_ascii=False, sort_keys=True)
        proposal.updated_at = now
        db.add(proposal)


def _audit_view(row: HarnessCapabilityAudit) -> dict:
    try:
        detail = json.loads(row.detail_json or "{}")
    except (TypeError, ValueError):
        detail = {}
    return {
        "event": row.event_type,
        "at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "config_version": row.config_version,
        "detail": detail,
    }


@router.get("/manifest")
def manifest(user=Depends(current_user), db: Session = Depends(get_db)):
    return {
        "version": HARNESS_VERSION,
        "architecture": "router-workers-decision",
        "collaboration_version": MULTI_AGENT_VERSION,
        "agents": list_personas(),
        "workers": list_workers(),
        "tools": get_tool_registry().manifest(),
        "plugins": list_plugins(db, user.id, include_internal=True),
        "policies": {
            "private_reasoning_exposed": False,
            "write_actions_require_confirmation": True,
            "medical_diagnosis_allowed": False,
            "worker_tools_least_privilege": True,
            "decision_agent_is_final_writer": True,
            "unknown_tools_fail_closed": True,
            "capability_scopes_are_user_configurable": True,
            "paused_allows_history_and_closure": True,
            "arbitrary_user_code_supported": False,
        },
        "voice_configured": voice_gateway.voice_configured(user),
    }


@router.get("/plugins")
def plugins(user=Depends(current_user), db: Session = Depends(get_db)):
    """User-facing capability catalog; technical tool names remain metadata."""
    return {
        "plugins": list_plugins(db, user.id),
        "principle": "能力只读取本人授权数据；行动必须经过用户确认；能力可随时关闭和撤销。",
    }


@router.get("/plugin-catalog")
def plugin_catalog(user=Depends(current_user), db: Session = Depends(get_db)):
    return {"plugins": list_plugins(db, user.id), "extension_policy": "仅提供审核内置能力；不执行用户上传代码。"}


@router.get("/installations")
def installations(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(HarnessPluginInstallation).where(
        HarnessPluginInstallation.user_id == user.id,
    ).order_by(HarnessPluginInstallation.created_at.asc())).all()
    return {"installations": [_public_installation(get_plugin(row.plugin_id), row) for row in rows]}


@router.post("/installations")
def create_installation(
    body: InstallationCreateRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
):
    key = _require_idempotency(idempotency_key)
    try:
        manifest = get_plugin(body.plugin_id)
    except PluginError as exc:
        _plugin_error(exc)
    request_hash = audit_request_hash({"route": "installations.create", "plugin_id": body.plugin_id, "config": body.config})
    try:
        replay = audit_idempotent_result(db, user.id, key, request_hash)
        if replay is not None:
            return replay
        if _installation(db, user.id, manifest.plugin_id) is not None:
            raise ApiException(409, "INSTALLATION_EXISTS", "这项能力已有配置，请直接修改现有配置")
        config = normalize_config(manifest, body.config, base=default_config(manifest))
        row = HarnessPluginInstallation(
            user_id=user.id, plugin_id=manifest.plugin_id, plugin_version=manifest.version,
            enabled=False, config_json=json.dumps(config, ensure_ascii=False, sort_keys=True),
            # The current manifest is recorded as reviewed only after the user
            # explicitly confirms and enables this exact configuration.
            config_version=1, reviewed_manifest_hash="",
            last_error="", enabled_at=None,
        )
        db.add(row)
        db.flush()
        result = {"ok": True, "installation": _public_installation(manifest, row)}
        write_audit(
            db, user_id=user.id, plugin_id=manifest.plugin_id, installation_id=row.id,
            event_type="configured", idempotency_key=key, request_hash=request_hash,
            config_version=row.config_version, detail={"changed": ["initial_config"]}, response=result,
        )
        db.commit()
        return result
    except PluginError as exc:
        db.rollback()
        _plugin_error(exc)
    except IntegrityError:
        db.rollback()
        replay = audit_idempotent_result(db, user.id, key, request_hash)
        if replay is not None:
            return replay
        if _installation(db, user.id, manifest.plugin_id) is not None:
            raise ApiException(409, "INSTALLATION_EXISTS", "这项能力已有配置，请刷新后继续") from None
        raise ApiException(409, "IDEMPOTENCY_IN_PROGRESS", "这项配置正在处理，请稍后刷新查看") from None
    except Exception:
        db.rollback()
        raise


@router.patch("/installations/{installation_id}")
def patch_installation(
    installation_id: int,
    body: InstallationPatchRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
):
    key = _require_idempotency(idempotency_key)
    request_hash = audit_request_hash({"route": "installations.patch", "id": installation_id, "version": body.config_version, "config": body.config})
    try:
        replay = audit_idempotent_result(db, user.id, key, request_hash)
        if replay is not None:
            return replay
        row = _owned_installation(db, user.id, installation_id)
        _check_version(row, body.config_version)
        manifest = get_plugin(row.plugin_id)
        current = _public_installation(manifest, row)["config"]
        config = normalize_config(manifest, body.config, base=current)
        now = utc_now()
        changed = db.execute(update(HarnessPluginInstallation).where(
            HarnessPluginInstallation.id == row.id,
            HarnessPluginInstallation.user_id == user.id,
            HarnessPluginInstallation.config_version == body.config_version,
        ).values(
            config_json=json.dumps(config, ensure_ascii=False, sort_keys=True),
            config_version=body.config_version + 1,
            # Any material configuration write revokes the active session. The
            # new settings become effective only after explicit user confirmation.
            enabled=False,
            disabled_at=now,
            updated_at=now,
        ))
        if changed.rowcount != 1:
            db.rollback()
            current = _owned_installation(db, user.id, installation_id)
            _check_version(current, body.config_version)
            raise ApiException(409, "PLUGIN_CONFIG_VERSION_CONFLICT", "配置已在别处更新，请刷新后再试")
        db.refresh(row)
        _expire_pending_actions(db, user.id, manifest, now)
        result = {"ok": True, "installation": _public_installation(manifest, row)}
        write_audit(
            db, user_id=user.id, plugin_id=row.plugin_id, installation_id=row.id,
            event_type="configured", idempotency_key=key, request_hash=request_hash,
            config_version=row.config_version, detail={"changed": sorted(body.config), "requires_explicit_consent": True}, response=result,
        )
        db.commit()
        return result
    except PluginError as exc:
        db.rollback()
        _plugin_error(exc)


@router.post("/installations/{installation_id}/preview")
def preview_installation(
    installation_id: int,
    body: InstallationVersionRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
):
    key = _require_idempotency(idempotency_key)
    request_hash = audit_request_hash({"route": "installations.preview", "id": installation_id, "version": body.config_version})
    try:
        replay = audit_idempotent_result(db, user.id, key, request_hash)
        if replay is not None:
            return replay
        row = _owned_installation(db, user.id, installation_id)
        _check_version(row, body.config_version)
        manifest = get_plugin(row.plugin_id)
        config = _public_installation(manifest, row)["config"]
        goal_label = next((label for key_, label in manifest.goals if key_ == config["goal"]), "健康建议")
        examples = {
            "health_state": "今天记录较少，所以先给出低负担建议；当前无法判断长期趋势。",
            "personal_policy": "先确认一个可观察的小目标，再复查执行情况；单次结果不会被当作健康效果证明。",
            "motion_evidence": "这次动作分析有可查看的画面观察；没有通过门禁的数值评分时，不展示分数。",
            "plan_outcome": "依据目标与已授权的执行记录给出计划选项；调整计划前仍由你确认。",
        }
        result = {
            "ok": True,
            "preview_mode": "synthetic_no_personal_data",
            "used_personal_data": False,
            "would_read_scopes": sorted(effective_scopes(row, manifest)),
            "goal": goal_label,
            "input_example": "仅展示能力说明用的合成示例，不读取你的健康记录。",
            "output_example": examples[manifest.plugin_id],
            "action_proposal_allowed": bool(config.get("allow_action_proposals")) and manifest.may_propose_actions,
            "disclaimer": "预览不代表本人的实际判断，也不会创建健康记录、计划或行动提案。",
        }
        write_audit(
            db, user_id=user.id, plugin_id=row.plugin_id, installation_id=row.id,
            event_type="previewed", idempotency_key=key, request_hash=request_hash,
            config_version=row.config_version, detail={"mode": "synthetic_no_personal_data"}, response=result,
        )
        db.commit()
        return result
    except PluginError as exc:
        db.rollback()
        _plugin_error(exc)


def _set_installation_state(
    installation_id: int,
    body: InstallationVersionRequest,
    *,
    enabled: bool,
    user,
    db: Session,
    idempotency_key: str | None,
):
    key = _require_idempotency(idempotency_key)
    event = "resumed" if enabled else "paused"
    request_hash = audit_request_hash({"route": f"installations.{event}", "id": installation_id, "version": body.config_version})
    try:
        replay = audit_idempotent_result(db, user.id, key, request_hash)
        if replay is not None:
            return replay
        row = _owned_installation(db, user.id, installation_id)
        _check_version(row, body.config_version)
        manifest = get_plugin(row.plugin_id)
        now = utc_now()
        changed = db.execute(update(HarnessPluginInstallation).where(
            HarnessPluginInstallation.id == row.id,
            HarnessPluginInstallation.user_id == user.id,
            HarnessPluginInstallation.config_version == body.config_version,
        ).values(
            enabled=enabled,
            config_version=body.config_version + 1,
            plugin_version=manifest.version if enabled else row.plugin_version,
            reviewed_manifest_hash=manifest.manifest_hash() if enabled else row.reviewed_manifest_hash,
            enabled_at=now if enabled else row.enabled_at,
            disabled_at=None if enabled else now,
            updated_at=now,
        ))
        if changed.rowcount != 1:
            db.rollback()
            current = _owned_installation(db, user.id, installation_id)
            _check_version(current, body.config_version)
            raise ApiException(409, "PLUGIN_CONFIG_VERSION_CONFLICT", "配置已在别处更新，请刷新后再试")
        if not enabled:
            _expire_pending_actions(db, user.id, manifest, now)
        db.refresh(row)
        result = {"ok": True, "installation": _public_installation(manifest, row)}
        write_audit(
            db, user_id=user.id, plugin_id=row.plugin_id, installation_id=row.id,
            event_type=event, idempotency_key=key, request_hash=request_hash,
            config_version=row.config_version,
            detail={"enabled": enabled, "effective_scopes": result["installation"]["effective_scopes"]}, response=result,
        )
        db.commit()
        return result
    except PluginError as exc:
        db.rollback()
        _plugin_error(exc)


@router.post("/installations/{installation_id}/pause")
def pause_installation(installation_id: int, body: InstallationVersionRequest, user=Depends(current_user), db: Session = Depends(get_db), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120)):
    return _set_installation_state(installation_id, body, enabled=False, user=user, db=db, idempotency_key=idempotency_key)


@router.post("/installations/{installation_id}/resume")
def resume_installation(installation_id: int, body: InstallationVersionRequest, user=Depends(current_user), db: Session = Depends(get_db), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120)):
    return _set_installation_state(installation_id, body, enabled=True, user=user, db=db, idempotency_key=idempotency_key)


@router.delete("/installations/{installation_id}")
def delete_installation(
    installation_id: int,
    config_version: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
):
    key = _require_idempotency(idempotency_key)
    request_hash = audit_request_hash({"route": "installations.delete", "id": installation_id, "version": config_version})
    try:
        replay = audit_idempotent_result(db, user.id, key, request_hash)
        if replay is not None:
            return replay
        row = _owned_installation(db, user.id, installation_id)
        _check_version(row, config_version)
        _expire_pending_actions(db, user.id, get_plugin(row.plugin_id), utc_now())
        result = {"ok": True, "deleted": True, "plugin_id": row.plugin_id}
        write_audit(
            db, user_id=user.id, plugin_id=row.plugin_id, installation_id=row.id,
            event_type="deleted", idempotency_key=key, request_hash=request_hash,
            config_version=row.config_version + 1, detail={"configuration_only": True}, response=result,
        )
        deleted = db.execute(sql_delete(HarnessPluginInstallation).where(
            HarnessPluginInstallation.id == row.id,
            HarnessPluginInstallation.user_id == user.id,
            HarnessPluginInstallation.config_version == config_version,
        ))
        if deleted.rowcount != 1:
            db.rollback()
            raise ApiException(409, "PLUGIN_CONFIG_VERSION_CONFLICT", "配置已在别处更新，请刷新后再试")
        db.commit()
        return result
    except PluginError as exc:
        db.rollback()
        _plugin_error(exc)


@router.get("/installations/{installation_id}/audit")
def installation_audit(installation_id: int, user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(HarnessPluginInstallation, installation_id)
    if row is not None and row.user_id != user.id:
        raise ApiException(404, "INSTALLATION_NOT_FOUND", "这项能力配置不存在")
    events = db.scalars(select(HarnessCapabilityAudit).where(
        HarnessCapabilityAudit.user_id == user.id,
        HarnessCapabilityAudit.installation_id == installation_id,
    ).order_by(HarnessCapabilityAudit.created_at.desc(), HarnessCapabilityAudit.id.desc()).limit(100)).all()
    if row is None and not events:
        raise ApiException(404, "INSTALLATION_NOT_FOUND", "这项能力配置不存在")
    return {"events": [_audit_view(event) for event in events]}


@router.post("/plugins/{plugin_id}/enable")
def enable_plugin(plugin_id: str, body: PluginEnableRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    raise ApiException(409, "CAPABILITY_FLOW_REQUIRED", "请在健康能力页面确认授权范围后开启")


@router.post("/plugins/{plugin_id}/disable")
def disable_plugin(plugin_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    raise ApiException(409, "CAPABILITY_FLOW_REQUIRED", "请从健康能力页面暂停该能力，以保留版本检查和使用记录")


@router.get("/voice/status")
def voice_status(user=Depends(current_user), db: Session = Depends(get_db)):
    """Report voice capability without calling any cloud service (spec 5/7.3)."""
    return voice_gateway.voice_status(db, user)


@router.post("/voice/transcribe")
async def transcribe(
    body: VoiceTranscriptionRequest, user=Depends(current_user), db: Session = Depends(get_db)
):
    persona = get_persona(body.agent_id)
    if persona.id != body.agent_id or not persona.voice_input:
        raise HTTPException(status_code=400, detail="当前智能体不支持语音输入")
    audio_format = body.format.lower().lstrip(".")
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=400, detail="语音数据格式无效") from None
    try:
        gateway = voice_gateway.build_voice_gateway(user, db)
        result = await gateway.transcribe(audio, audio_format)
    except VoiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    except VoiceRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except VoiceGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return {
        "text": result.text,
        "agent_id": persona.id,
        "provider": gateway.provider_name,
        "trace_id": gateway.trace_id,
    }


@router.post("/voice/synthesize")
async def synthesize(
    body: VoiceSynthesisRequest, user=Depends(current_user), db: Session = Depends(get_db)
):
    persona = get_persona(body.agent_id)
    if persona.id != body.agent_id or not persona.voice_output:
        raise HTTPException(status_code=400, detail="当前智能体不支持语音播报")
    try:
        gateway = voice_gateway.build_voice_gateway(user, db)
        result = await gateway.synthesize(body.text)
        segments = result.segments
    except VoiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    except VoiceRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except VoiceGatewayError as exc:
        # Segment 0 itself failed -> nothing playable.
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    payload = {
        "segments": [
            {
                "index": i,
                "audio_base64": base64.b64encode(seg.audio).decode("ascii"),
                "content_type": seg.content_type,
            }
            for i, seg in enumerate(segments)
        ],
        "partial": result.partial,
        "provider": gateway.provider_name,
        "trace_id": gateway.trace_id,
        "agent_id": persona.id,
    }
    # Legacy single-segment compatibility for one version cycle (spec 5).
    if len(segments) == 1:
        payload["audio_base64"] = payload["segments"][0]["audio_base64"]
        payload["content_type"] = segments[0].content_type
    return payload


@router.post("/voice/verify-once")
async def verify_once(
    body: VoiceVerifyOnceRequest, user=Depends(current_user), db: Session = Depends(get_db)
):
    if body.provider != "tencent_cloud":
        raise HTTPException(status_code=400, detail="当前仅支持 tencent_cloud 验证")
    if not body.acknowledge_quota:
        raise HTTPException(status_code=400, detail="请确认了解本次验证会消耗一次外部额度")
    try:
        result = await voice_gateway.run_verify_once(db, user, body.check)
    except VoiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    except VoiceRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except VoiceGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return result
