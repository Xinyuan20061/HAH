from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.harness import (
    HARNESS_VERSION,
    MULTI_AGENT_VERSION,
    get_persona,
    get_tool_registry,
    list_personas,
    list_workers,
)
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


@router.get("/manifest")
def manifest(user=Depends(current_user)):
    return {
        "version": HARNESS_VERSION,
        "architecture": "router-workers-decision",
        "collaboration_version": MULTI_AGENT_VERSION,
        "agents": list_personas(),
        "workers": list_workers(),
        "tools": get_tool_registry().manifest(),
        "policies": {
            "private_reasoning_exposed": False,
            "write_actions_require_confirmation": True,
            "medical_diagnosis_allowed": False,
            "worker_tools_least_privilege": True,
            "decision_agent_is_final_writer": True,
        },
        "voice_configured": voice_gateway.voice_configured(user),
    }


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
