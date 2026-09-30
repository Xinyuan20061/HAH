"""Tencent Cloud voice integration tests (spec sections 5, 7, 10 P0-D).

The real Tencent SDK is exercised only through injected stub clients
(MockTransport-style); no external network call is ever made. Error injection
covers empty audio, bad format, over-size, over-duration, budget exceeded,
unconfigured credentials, and segment-2-failure graceful degradation.
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.harness import voice as v
from app.models import (
    ProviderConnectionCheck,
    ProviderInvocation,
    User,
    VoiceUsageDaily,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeProvider:
    name = "tencent_cloud"

    def __init__(self, fail_on_synth=()):
        self.synth_calls = 0
        self.transcribe_calls = 0
        self.fail_on_synth = set(fail_on_synth)

    async def transcribe(self, audio: bytes, fmt: str) -> v.TranscribeResult:
        self.transcribe_calls += 1
        return v.TranscribeResult(text="今天练什么", provider_request_id="asr-req-1")

    async def synthesize_segment(self, text: str) -> v.TtsSegment:
        self.synth_calls += 1
        if self.synth_calls in self.fail_on_synth:
            raise v.VoiceGatewayError("upstream synth failed", 503, "tts_upstream_error")
        return v.TtsSegment(
            audio=b"mp3-frame", content_type="audio/mpeg", provider_request_id="tts-req-1"
        )


class FakeAsrClient:
    def __init__(self):
        self.captured: list[dict] = []

    def SentenceRecognition(self, req):
        self.captured.append(json.loads(req.to_json_string()))

        class R:
            pass

        r = R()
        r.Result = "你好世界"
        r.RequestId = "asr-req-sdk"
        return r


class FakeTtsClient:
    def __init__(self):
        self.captured: list[dict] = []

    def TextToVoice(self, req):
        self.captured.append(json.loads(req.to_json_string()))

        class R:
            pass

        r = R()
        r.Audio = base64.b64encode(b"mp3data").decode("ascii")
        r.RequestId = "tts-req-sdk"
        return r


@pytest.fixture
def db_user(migrated_engine):
    with Session(migrated_engine) as db:
        user = User(openid="voice-" + uuid4().hex)
        db.add(user)
        db.commit()
        db.refresh(user)
        yield db, user


def make_gateway(db, user, provider=None):
    return v.VoiceGateway(provider or FakeProvider(), user.id, db)


# ---------------------------------------------------------------------------
# Text splitting (never silently truncates)
# ---------------------------------------------------------------------------
def test_split_covers_whole_text_without_truncation():
    long_text = "第一句话。" * 60  # 300 chars
    segs = v.split_tts_text(long_text, 120)
    assert all(len(s) <= 120 for s in segs)
    assert "".join(segs) == long_text.strip()
    assert len(segs) >= 2


def test_split_short_text_single_segment():
    assert v.split_tts_text("你好", 120) == ["你好"]


# ---------------------------------------------------------------------------
# Local rejection gates
# ---------------------------------------------------------------------------
def test_transcribe_rejects_empty_audio(db_user):
    db, user = db_user
    with pytest.raises(v.VoiceRejected) as exc:
        asyncio.run(make_gateway(db, user).transcribe(b"", "mp3"))
    assert exc.value.code == "empty_audio"


def test_transcribe_rejects_bad_format(db_user):
    db, user = db_user
    with pytest.raises(v.VoiceRejected) as exc:
        asyncio.run(make_gateway(db, user).transcribe(b"abc", "flac"))
    assert exc.value.code == "unsupported_audio"


def test_transcribe_rejects_over_size(db_user, monkeypatch):
    db, user = db_user
    monkeypatch.setattr(settings, "voice_max_audio_bytes", 10)
    with pytest.raises(v.VoiceRejected) as exc:
        asyncio.run(make_gateway(db, user).transcribe(b"x" * 100, "mp3"))
    assert exc.value.code == "audio_too_large"


def test_transcribe_rejects_over_duration(db_user, monkeypatch):
    db, user = db_user
    monkeypatch.setattr(v, "_estimate_audio_seconds", lambda audio: 999.0)
    with pytest.raises(v.VoiceRejected) as exc:
        asyncio.run(make_gateway(db, user).transcribe(b"x" * 100, "mp3"))
    assert exc.value.code == "audio_too_long"


def test_transcribe_budget_exceeded(db_user, monkeypatch):
    db, user = db_user
    monkeypatch.setattr(settings, "voice_monthly_asr_budget", 1)
    db.add(
        VoiceUsageDaily(
            user_id=user.id,
            usage_date=date.today(),
            provider="tencent_cloud",
            asr_count=1,
            tts_chars=0,
            failure_count=0,
        )
    )
    db.commit()
    with pytest.raises(v.VoiceBudgetExceeded):
        asyncio.run(make_gateway(db, user).transcribe(b"x" * 100, "mp3"))


# ---------------------------------------------------------------------------
# Happy path: ledger + usage + trace
# ---------------------------------------------------------------------------
def test_transcribe_success_writes_ledger_and_usage(db_user):
    db, user = db_user
    result = asyncio.run(make_gateway(db, user).transcribe(b"x" * 100, "mp3"))
    assert result.text == "今天练什么"
    rows = db.execute(
        select(ProviderInvocation).where(ProviderInvocation.user_id == user.id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].operation == "asr"
    assert rows[0].status == "success"
    assert rows[0].provider_request_id == "asr-req-1"
    usage = db.execute(
        select(VoiceUsageDaily).where(VoiceUsageDaily.user_id == user.id)
    ).scalar_one()
    assert usage.asr_count == 1


def test_synthesize_success_segments_and_billing(db_user):
    db, user = db_user
    text = "短句一。短句二。短句三。" * 10  # multi-segment
    out = asyncio.run(make_gateway(db, user).synthesize(text))
    assert out.partial is False
    assert len(out.segments) >= 1
    usage = db.execute(
        select(VoiceUsageDaily).where(VoiceUsageDaily.user_id == user.id)
    ).scalar_one()
    assert usage.tts_chars == len(text.strip())


def test_synthesize_segment2_failure_keeps_segment1_no_resynth(db_user):
    db, user = db_user
    text = "第一句话。" * 30  # 150 chars -> splits into 2 segments
    provider = FakeProvider(fail_on_synth={2})
    gw = v.VoiceGateway(provider, user.id, db)
    out = asyncio.run(gw.synthesize(text))
    # Segment 1 succeeded, segment 2 failed -> degraded but still returns seg 1.
    assert out.partial is True
    assert len(out.segments) == 1
    # Called exactly twice (seg1 ok, seg2 failed); did NOT re-synthesize seg1.
    assert provider.synth_calls == 2


# ---------------------------------------------------------------------------
# Tencent SDK request-shape (injected stub clients)
# ---------------------------------------------------------------------------
def test_tencent_asr_request_shape():
    asr = FakeAsrClient()
    tts = FakeTtsClient()
    prov = v.TencentCloudVoiceProvider(
        "secret-id", "secret-key", "ap-shanghai", "16k_zh", 101001,
        asr_client=asr, tts_client=tts,
    )
    audio = b"\x00\x01\x02"
    res = asyncio.run(prov.transcribe(audio, "mp3"))
    assert res.text == "你好世界"
    body = asr.captured[0]
    assert body["EngSerViceType"] == "16k_zh"
    assert body["SubServiceType"] == 2
    assert body["SourceType"] == 1
    assert body["VoiceFormat"] == "mp3"
    assert body["DataLen"] == len(audio)
    assert base64.b64decode(body["Data"]) == audio


def test_tencent_tts_request_shape():
    asr = FakeAsrClient()
    tts = FakeTtsClient()
    prov = v.TencentCloudVoiceProvider(
        "secret-id", "secret-key", "ap-shanghai", "16k_zh", 101002,
        asr_client=asr, tts_client=tts,
    )
    seg = asyncio.run(prov.synthesize_segment("你好"))
    assert seg.audio == b"mp3data"
    body = tts.captured[0]
    assert body["Text"] == "你好"
    assert body["VoiceType"] == 101002
    assert body["Codec"] == "mp3"
    assert body["SessionId"]


# ---------------------------------------------------------------------------
# verify-once: connected -> stop testing (unique-constraint dedup)
# ---------------------------------------------------------------------------
def test_verify_once_dedup_prevents_second_real_call(db_user, monkeypatch):
    db, user = db_user
    monkeypatch.setattr(settings, "voice_live_verify_enabled", True)
    monkeypatch.setattr(settings, "tencent_secret_id", "ak")
    monkeypatch.setattr(settings, "tencent_secret_key", "sk")
    stub = FakeProvider()

    first = asyncio.run(v.run_verify_once(db, user, "tts", provider_stub=stub))
    assert first["called"] is True
    assert first["cached"] is False
    second = asyncio.run(v.run_verify_once(db, user, "tts", provider_stub=stub))
    assert second["called"] is False
    assert second["cached"] is True
    # The stub provider was only used on the first (real) attempt.
    assert stub.synth_calls == 1

    rows = db.execute(
        select(ProviderConnectionCheck).where(
            ProviderConnectionCheck.direction == "tts",
            ProviderConnectionCheck.status == "success",
        )
    ).scalars().all()
    assert len(rows) == 1


def test_verify_once_blocked_when_not_configured(db_user, monkeypatch):
    db, user = db_user
    monkeypatch.setattr(settings, "voice_live_verify_enabled", True)
    monkeypatch.setattr(settings, "tencent_secret_id", "")
    monkeypatch.setattr(settings, "tencent_secret_key", "")
    with pytest.raises(v.VoiceNotConfigured):
        asyncio.run(v.run_verify_once(db, user, "tts"))


# ---------------------------------------------------------------------------
# Endpoint-level: no keys -> status reports configured=false, no fake connectivity
# ---------------------------------------------------------------------------
def test_voice_status_endpoint_no_cloud_call(api, monkeypatch):
    # Deterministic regardless of local .env: force "not configured" so the
    # endpoint reports configured=false and never fakes connectivity.
    monkeypatch.setattr(settings, "tencent_secret_id", "")
    monkeypatch.setattr(settings, "tencent_secret_key", "")
    r = api.get("/api/v1/harness/voice/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["asr_available"] is False
    assert body["last_verified_at"] is None


def test_transcribe_endpoint_not_configured_returns_503(api):
    audio = base64.b64encode(b"x" * 100).decode()
    r = api.post(
        "/api/v1/harness/voice/transcribe",
        json={"agent_id": "xiaojian", "audio_base64": audio, "format": "mp3"},
    )
    assert r.status_code == 503
