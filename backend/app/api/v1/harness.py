from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import current_user
from app.core.config import settings
from app.harness import (
    HARNESS_VERSION,
    MULTI_AGENT_VERSION,
    get_persona,
    get_tool_registry,
    list_personas,
    list_workers,
)
from app.harness import voice as voice_gateway
from app.harness.voice import VoiceUnavailable


router = APIRouter(prefix="/harness", tags=["health-harness"])


class VoiceTranscriptionRequest(BaseModel):
    agent_id: str = Field(max_length=30)
    audio_base64: str = Field(min_length=1)
    format: str = Field(default="mp3", max_length=10)


class VoiceSynthesisRequest(BaseModel):
    agent_id: str = Field(max_length=30)
    text: str = Field(min_length=1, max_length=1200)


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


@router.post("/voice/transcribe")
async def transcribe(body: VoiceTranscriptionRequest, user=Depends(current_user)):
    persona = get_persona(body.agent_id)
    if persona.id != body.agent_id or not persona.voice_input:
        raise HTTPException(status_code=400, detail="当前智能体不支持语音输入")
    audio_format = body.format.lower().lstrip(".")
    content_types = {
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "wav": "audio/wav",
    }
    if audio_format not in content_types:
        raise HTTPException(status_code=400, detail="仅支持 mp3、m4a、aac 或 wav")
    if len(body.audio_base64) > (settings.voice_max_audio_bytes * 4 // 3) + 16:
        raise HTTPException(status_code=413, detail="语音文件超过大小限制")
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=400, detail="语音数据格式无效") from None
    if not audio or len(audio) > settings.voice_max_audio_bytes:
        raise HTTPException(status_code=413, detail="语音文件为空或超过大小限制")
    try:
        text = await voice_gateway.get_voice_provider(user).transcribe(
            audio, f"voice.{audio_format}", content_types[audio_format]
        )
    except VoiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {"text": text, "agent_id": persona.id}


@router.post("/voice/synthesize")
async def synthesize(body: VoiceSynthesisRequest, user=Depends(current_user)):
    persona = get_persona(body.agent_id)
    if persona.id != body.agent_id or not persona.voice_output:
        raise HTTPException(status_code=400, detail="当前智能体不支持语音播报")
    try:
        audio = await voice_gateway.get_voice_provider(user).synthesize(body.text.strip())
    except VoiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {
        "audio_base64": base64.b64encode(audio.data).decode("ascii"),
        "content_type": audio.content_type,
        "agent_id": persona.id,
    }
