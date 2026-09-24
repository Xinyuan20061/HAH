import json
import time
from uuid import uuid4
from typing import Literal

import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from pydantic import BaseModel
from app.api.deps import current_user
from app.core.database import get_db
from app.core.config import settings
from app.core.media_security import validate_media_source_url, UnsafeMediaURL
from app.core.url_security import resolve_ai_host
from app.core.time import utc_now
from app.models import (
    FoodAnalysisSession,
    FoodAnalysisCorrection,
    DietRecord,
    MediaAsset,
    AIJob,
)
from app.schemas.vision import FoodCorrectionIn, FoodFinalizeIn
from app.services.vision.service import analyze_food_image, VisionNotConfigured
from app.services.timeline import add_event
from app.services.evaluation import record_metric
from app.services.agent.actions import execute_action
from app.services.ai_jobs import create_ai_job, public_job, requeue_expired_jobs, json_loads
from app.services.result_summary import generate_result_summary
from app.services.storage import get_storage, StorageError
from app.services.vision.cloud_food import analyze_food_cloud, CloudFoodError

router = APIRouter(prefix="/vision", tags=["vision"])


class FoodJobIn(BaseModel):
    media_id: int
    route: Literal["cloud", "worker"] | None = None


def _food_image_bytes(asset: MediaAsset) -> bytes:
    """Read a bounded image without exposing provider or signed-URL details."""
    limit = settings.food_image_max_bytes
    if asset.size_bytes and asset.size_bytes > limit:
        raise CloudFoodError("invalid_media", "图片超过云端识别大小限制")
    if asset.storage_backend == "local" and not settings.is_production:
        try:
            path = get_storage().local_path(asset.storage_key)
            data = path.read_bytes()
        except (OSError, StorageError):
            raise CloudFoodError("media_unavailable", "图片暂时无法读取") from None
    elif asset.storage_backend == "s3":
        try:
            data = get_storage().local_path(asset.storage_key).read_bytes()
        except (OSError, StorageError):
            raise CloudFoodError("media_unavailable", "图片暂时无法读取") from None
    else:
        try:
            url = validate_media_source_url(asset.source_url)
        except UnsafeMediaURL:
            raise CloudFoodError("media_url_invalid", "图片下载地址无效") from None
        try:
            original = httpx.URL(url)
            request_url, headers, extensions = original, {}, {}
            if settings.is_production:
                request_url = original.copy_with(host=resolve_ai_host(url))
                headers["Host"] = original.netloc.decode("ascii")
                extensions["sni_hostname"] = original.host.encode("ascii")
            chunks = []
            received = 0
            with httpx.Client(
                timeout=10, trust_env=False, follow_redirects=False
            ) as client:
                with client.stream(
                    "GET", request_url, headers=headers, extensions=extensions
                ) as response:
                    if not response.is_success:
                        code = (
                            "media_url_expired"
                            if response.status_code in {401, 403, 404, 410}
                            else "media_http"
                        )
                        raise CloudFoodError(code, "图片暂时无法下载")
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > limit:
                        raise CloudFoodError("invalid_media", "图片超过云端识别大小限制")
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > limit:
                            raise CloudFoodError("invalid_media", "图片超过云端识别大小限制")
                        chunks.append(chunk)
            data = b"".join(chunks)
        except CloudFoodError:
            raise
        except ValueError:
            raise CloudFoodError("media_url_invalid", "图片下载地址无效") from None
        except (httpx.TimeoutException, httpx.TransportError):
            raise CloudFoodError("media_unavailable", "图片暂时无法下载") from None
    if not data or len(data) > limit:
        raise CloudFoodError("invalid_media", "图片为空或超过云端识别大小限制")
    return data


def _run_cloud_food_job(db: Session, job: AIJob, asset: MediaAsset) -> AIJob:
    """Own a cloud-routed job once; any failure makes it claimable by the Worker."""
    payload = _loads(job.payload_json)
    if payload.get("route") != "cloud" or not settings.deepseek_api_key.strip():
        return job
    lease = uuid4().hex
    now = utc_now()
    claimed = db.execute(
        update(AIJob)
        .where(AIJob.id == job.id, AIJob.status == "queued", AIJob.attempts == 0)
        .values(
            status="processing",
            progress=10,
            worker_id="cloud-food",
            lease_token=lease,
            started_at=job.started_at or now,
            error_code="",
            error_message="",
        ),
        execution_options={"synchronize_session": False},
    ).rowcount
    db.commit()
    if not claimed:
        db.refresh(job)
        return job
    started = time.perf_counter()
    try:
        result = analyze_food_cloud(_food_image_bytes(asset))
        session = FoodAnalysisSession(
            user_id=job.user_id,
            image_sha256=str(result.pop("image_sha256", ""))[:64],
            provider=result.get("provider", "deepseek"),
            model=result.get("model", settings.deepseek_vision_model),
            confidence=float(result.get("confidence") or 0),
            initial_json=json.dumps(result, ensure_ascii=False, default=str),
            status="analyzed",
        )
        db.add(session)
        db.flush()
        public_result = {
            "analysis_id": session.id,
            **result,
            "editable": True,
            "requires_confirmation": True,
        }
        elapsed = (time.perf_counter() - started) * 1000
        record_metric(
            db,
            job.user_id,
            "food_analysis_latency_ms",
            elapsed,
            "ms",
            "cloud_direct",
            True,
            {"provider": "deepseek", "confidence": session.confidence},
        )
        add_event(
            db,
            job.user_id,
            "food_analysis_completed",
            {
                "job_id": job.id,
                "analysis_id": session.id,
                "provider": "deepseek",
                "source": "cloud",
            },
            source="cloud_direct",
            ref_type="ai_job",
            ref_id=job.id,
        )
        job.status = "done"
        job.progress = 100
        job.result_json = json.dumps(public_result, ensure_ascii=False, default=str)
        job.finished_at = utc_now()
        job.error_code = ""
        job.error_message = ""
    except CloudFoodError as exc:
        # The persistent job remains available for the original Worker route.
        job.status = "queued"
        job.progress = 0
        job.worker_id = ""
        job.lease_token = ""
        job.lease_expires_at = None
        job.error_code = "cloud_fallback"
        job.error_message = "云端识别暂不可用，已转为本地排队"
        if exc.code == "media_url_expired":
            job.status = "waiting_source_refresh"
            job.error_code = "media_url_expired"
            job.error_message = "请在小程序刷新媒体下载地址"
        record_metric(
            db,
            job.user_id,
            "food_analysis_latency_ms",
            (time.perf_counter() - started) * 1000,
            "ms",
            "cloud_direct",
            False,
            {"reason": exc.code, "fallback": "worker"},
        )
    finally:
        db.add(job)
        db.commit()
        db.refresh(job)
    return job


@router.post("/food-jobs")
def create_food_job(
    body: FoodJobIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    asset = db.get(MediaAsset, body.media_id)
    if not asset or asset.user_id != user.id:
        raise HTTPException(404, "图片资产不存在")
    if asset.media_type != "image":
        raise HTTPException(400, "识餐任务仅支持图片")
    job = create_ai_job(
        db,
        user_id=user.id,
        job_type="food_vision",
        media_asset_id=asset.id,
        payload={
            "purpose": "food_analysis",
            **({"route": body.route} if body.route else {}),
        },
    )
    job = _run_cloud_food_job(db, job, asset)
    return {"ok": True, **public_job(job)}


@router.get("/food-jobs/{job_id}")
async def get_food_job(
    job_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    requeue_expired_jobs(db)
    job = db.get(AIJob, job_id)
    if not job or job.user_id != user.id or job.job_type != "food_vision":
        raise HTTPException(404, "任务不存在")
    result = json_loads(job.result_json, None) if job.result_json else None
    if (
        isinstance(result, dict)
        and job.status in {"done", "completed", "succeeded"}
        and not result.get("summary")
    ):
        summary = await generate_result_summary(db, user, result)
        if summary:
            result["summary"] = summary
            job.result_json = json.dumps(result, ensure_ascii=False)
            db.commit()
    return public_job(job)


def _loads(s):
    try:
        return json.loads(s or "{}")
    except Exception:
        return {}


def _public(data: dict):
    return {k: v for k, v in data.items() if not k.startswith("_")}


@router.post("/food-analysis")
async def food_analysis(
    file: UploadFile = File(...),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if settings.is_production:
        raise HTTPException(
            409, "生产环境识餐请使用 /vision/food-jobs，由任务路由选择云端直连或本地处理。"
        )
    started = time.perf_counter()
    try:
        result = await analyze_food_image(file, user)
        elapsed = (time.perf_counter() - started) * 1000
        image_hash = result.pop("_image_sha256", "")
        provider = result.get("provider", "")
        model = result.get("model", "")
        session = FoodAnalysisSession(
            user_id=user.id,
            image_sha256=image_hash,
            provider=provider,
            model=model,
            confidence=float(result.get("confidence") or 0),
            initial_json=json.dumps(_public(result), ensure_ascii=False),
            status="analyzed",
        )
        db.add(session)
        db.flush()
        record_metric(
            db,
            user.id,
            "food_analysis_latency_ms",
            elapsed,
            "ms",
            "vision",
            True,
            {"provider": provider, "confidence": session.confidence},
        )
        db.commit()
        db.refresh(session)
        return {
            "analysis_id": session.id,
            **_public(result),
            "editable": True,
            "requires_confirmation": True,
        }
    except VisionNotConfigured as e:
        record_metric(
            db,
            user.id,
            "food_analysis_latency_ms",
            (time.perf_counter() - started) * 1000,
            "ms",
            "vision",
            False,
            {"reason": "not_configured"},
        )
        db.commit()
        raise HTTPException(501, str(e))
    except httpx.HTTPStatusError:
        record_metric(
            db,
            user.id,
            "food_analysis_latency_ms",
            (time.perf_counter() - started) * 1000,
            "ms",
            "vision",
            False,
            {"reason": "provider_http"},
        )
        db.commit()
        raise HTTPException(502, "DeepSeek 视觉识别暂时不可用，请稍后重试或手动记录。")
    except ValueError as e:
        record_metric(
            db,
            user.id,
            "food_analysis_latency_ms",
            (time.perf_counter() - started) * 1000,
            "ms",
            "vision",
            False,
            {"reason": "invalid_result"},
        )
        db.commit()
        raise HTTPException(400, str(e))


@router.get("/food-analysis/{analysis_id}")
def get_food_analysis(
    analysis_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    x = db.get(FoodAnalysisSession, analysis_id)
    if not x or x.user_id != user.id:
        raise HTTPException(404, "识餐记录不存在")
    initial = _loads(x.initial_json)
    corrected = _loads(x.corrected_json) if x.corrected_json else None
    return {
        "analysis_id": x.id,
        "status": x.status,
        "initial": initial,
        "corrected": corrected,
        "correction_count": x.correction_count,
        "finalized_record_id": x.finalized_record_id,
    }


@router.put("/food-analysis/{analysis_id}/correct")
def correct_food_analysis(
    analysis_id: int,
    body: FoodCorrectionIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    x = db.scalar(
        select(FoodAnalysisSession)
        .where(FoodAnalysisSession.id == analysis_id)
        .with_for_update()
    )
    if not x or x.user_id != user.id:
        raise HTTPException(404, "识餐记录不存在")
    if x.finalized_record_id or x.status == "record_deleted":
        raise HTTPException(409, "该识餐结果已保存或对应记录已删除，请直接编辑饮食记录")
    initial = _loads(x.initial_json)
    submitted = body.model_dump(exclude_unset=True)
    if "items" in submitted:
        submitted.update(
            {
                key: getattr(body, key)
                for key in [
                    "weight_g",
                    "calories",
                    "protein",
                    "carbs",
                    "fat",
                    "fiber",
                ]
            }
        )
    corrected = {**initial, **submitted}
    changed = [
        k
        for k, v in submitted.items()
        if initial.get(k if k != "weight_g" else "estimated_weight_g") != v
    ]
    x.corrected_json = json.dumps(corrected, ensure_ascii=False)
    x.correction_count += 1
    x.status = "corrected"
    db.add(x)
    c = FoodAnalysisCorrection(
        analysis_id=x.id,
        user_id=user.id,
        corrected_json=x.corrected_json,
        changed_fields_json=json.dumps(changed, ensure_ascii=False),
    )
    db.add(c)
    add_event(
        db,
        user.id,
        "food_analysis_corrected",
        {
            "analysis_id": x.id,
            "changed_fields": changed,
            "correction_count": x.correction_count,
        },
        source="user",
        ref_type="food_analysis",
        ref_id=x.id,
    )
    record_metric(
        db,
        user.id,
        "food_correction",
        1,
        "count",
        "vision_feedback",
        True,
        {"changed_fields": changed},
    )
    db.commit()
    db.refresh(x)
    return {
        "ok": True,
        "analysis_id": x.id,
        "corrected": corrected,
        "changed_fields": changed,
        "correction_count": x.correction_count,
    }


@router.post("/food-analysis/{analysis_id}/finalize")
def finalize_food_analysis(
    analysis_id: int,
    body: FoodFinalizeIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    x = db.scalar(
        select(FoodAnalysisSession)
        .where(FoodAnalysisSession.id == analysis_id)
        .with_for_update()
    )
    if not x or x.user_id != user.id:
        raise HTTPException(404, "识餐记录不存在")
    if x.status == "record_deleted":
        raise HTTPException(409, "该识餐对应的记录已删除，请手动新增饮食记录")
    if x.finalized_record_id:
        record = db.get(DietRecord, x.finalized_record_id)
        return {
            "ok": True,
            "already_finalized": True,
            "record_id": record.id if record else x.finalized_record_id,
        }
    data = _loads(x.corrected_json) if x.corrected_json else _loads(x.initial_json)
    payload = {
        "analysis_id": x.id,
        "dish_name": data.get("dish_name"),
        "meal_type": body.meal_type,
        "corrected": bool(x.corrected_json),
    }

    def perform():
        changed = db.execute(
            update(FoodAnalysisSession)
            .where(
                FoodAnalysisSession.id == x.id,
                FoodAnalysisSession.status.in_(["analyzed", "corrected"]),
            )
            .values(status="finalizing"),
            execution_options={"synchronize_session": False},
        ).rowcount
        if not changed:
            db.refresh(x)
            if x.finalized_record_id:
                return {
                    "record_id": x.finalized_record_id,
                    "analysis_id": x.id,
                    "already_finalized": True,
                }
            raise HTTPException(409, "识餐记录正在保存，请稍后查看")
        record = DietRecord(
            user_id=user.id,
            name=str(data.get("dish_name") or "AI识别餐食")[:120],
            meal_type=body.meal_type,
            calories=float(data.get("calories") or 0),
            protein=float(data.get("protein") or 0),
            carbs=float(data.get("carbs") or 0),
            fat=float(data.get("fat") or 0),
            fiber=float(data.get("fiber") or 0),
            portion=str(data.get("portion") or "")[:120],
            cooking_method=str(data.get("cooking_method") or "")[:120],
            weight_g=float(data.get("weight_g") or data.get("estimated_weight_g") or 0),
            source="ai_vision_corrected" if x.corrected_json else "ai_vision",
            vision_analysis_id=x.id,
            items_json=json.dumps(data.get("items") or [], ensure_ascii=False),
        )
        db.add(record)
        db.flush()
        x.finalized_record_id = record.id
        x.status = "finalized"
        db.add(x)
        add_event(
            db,
            user.id,
            "diet",
            {
                "name": record.name,
                "meal_type": record.meal_type,
                "calories": record.calories,
                "protein": record.protein,
                "carbs": record.carbs,
                "fat": record.fat,
                "fiber": record.fiber,
                "portion": record.portion,
                "cooking_method": record.cooking_method,
                "weight_g": record.weight_g,
                "analysis_id": x.id,
                "item_count": len(data.get("items") or []),
            },
            source=record.source,
            ref_type="diet",
            ref_id=record.id,
            occurred_at=record.recorded_at,
        )
        add_event(
            db,
            user.id,
            "food_analysis_finalized",
            {
                "analysis_id": x.id,
                "record_id": record.id,
                "corrected": bool(x.corrected_json),
            },
            source="user",
            ref_type="food_analysis",
            ref_id=x.id,
        )
        record_metric(
            db,
            user.id,
            "food_finalized",
            1,
            "count",
            "vision_feedback",
            True,
            {"corrected": bool(x.corrected_json)},
        )
        db.flush()
        return {
            "record_id": record.id,
            "analysis_id": x.id,
            "corrected": bool(x.corrected_json),
        }

    action = execute_action(
        db,
        user.id,
        "diet.ai.finalize",
        perform,
        confirmed=body.confirmed,
        source="user",
        input_data=payload,
    )
    if not action["executed"]:
        return {"ok": False, **action}
    return {
        "ok": True,
        "already_finalized": False,
        "action_audit_id": action["audit_id"],
        **action["result"],
    }
