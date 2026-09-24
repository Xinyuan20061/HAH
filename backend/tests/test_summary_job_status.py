# -*- coding: utf-8 -*-
"""API-level regression: done jobs get a lazy summary (status 'done' fix)."""

import json

from sqlalchemy.orm import Session

from app.api.v1 import media as media_mod
from app.api.v1 import vision as vision_mod
from app.models import AIJob, MediaAsset


def _seed_done_job(migrated_engine, user_id, job_type, result_dict):
    with Session(migrated_engine) as db:
        asset = MediaAsset(
            user_id=user_id,
            media_type="video" if job_type == "motion_pose" else "image",
            storage_backend="cloud_ref",
            storage_key=f"test-{job_type}",
        )
        db.add(asset)
        db.flush()
        job = AIJob(
            user_id=user_id,
            media_asset_id=asset.id,
            job_type=job_type,
            status="done",
            result_json=json.dumps(result_dict, ensure_ascii=False),
        )
        db.add(job)
        db.commit()
        return job.id


def test_done_motion_job_gets_summary(migrated_engine, api, monkeypatch):
    async def fake_summary(db, user, result):
        return "这次深蹲整体稳定，可以再增加一组。"

    monkeypatch.setattr(media_mod, "generate_result_summary", fake_summary)
    job_id = _seed_done_job(
        migrated_engine,
        api.user_id,
        "motion_pose",
        {"pose": {"exercise_type": "squat", "reps": 12}, "score": {"overall": 86}},
    )
    resp = api.get(f"/api/v1/media/motion-jobs/{job_id}")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["summary"] == "这次深蹲整体稳定，可以再增加一组。"


def test_done_food_job_gets_summary(migrated_engine, api, monkeypatch):
    async def fake_summary(db, user, result):
        return "这餐整体均衡，热量接近今日目标。"

    monkeypatch.setattr(vision_mod, "generate_result_summary", fake_summary)
    job_id = _seed_done_job(
        migrated_engine,
        api.user_id,
        "food_vision",
        {"dish_name": "番茄鸡蛋面", "calories": 520, "protein": 18, "carbs": 70, "fat": 14},
    )
    resp = api.get(f"/api/v1/vision/food-jobs/{job_id}")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["summary"] == "这餐整体均衡，热量接近今日目标。"


def test_done_job_summary_cached(migrated_engine, api, monkeypatch):
    calls = []

    async def fake_summary(db, user, result):
        calls.append(1)
        return "只生成一次。"

    monkeypatch.setattr(vision_mod, "generate_result_summary", fake_summary)
    job_id = _seed_done_job(
        migrated_engine,
        api.user_id,
        "food_vision",
        {"dish_name": "米饭", "calories": 200, "protein": 4},
    )
    api.get(f"/api/v1/vision/food-jobs/{job_id}")
    api.get(f"/api/v1/vision/food-jobs/{job_id}")
    assert len(calls) == 1
