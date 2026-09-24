# -*- coding: utf-8 -*-
"""Tests for custom plan tasks (manual add / delete / restore)."""


def test_custom_plan_lifecycle(api):
    # add
    created = api.post(
        "/api/v1/health/plan/today/custom",
        json={"title": "晚饭后散步 20 分钟", "description": "和妈妈一起", "task_type": "other"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["ok"] is True
    key = body["item"]["task_key"]
    assert key.startswith("custom:")

    # today plan contains it
    plan = api.get("/api/v1/health/plan/today")
    assert plan.status_code == 200
    items = plan.json()["items"]
    custom = [item for item in items if item.get("custom")]
    assert any(item["task_key"] == key and item["title"] == "晚饭后散步 20 分钟" for item in custom)

    # toggle done then restore
    assert api.put(f"/api/v1/health/plan/today/{key}", json={"done": True}).status_code == 200
    restored = api.get("/api/v1/health/plan/today").json()["items"]
    assert next(item for item in restored if item["task_key"] == key)["done"] is True
    assert api.put(f"/api/v1/health/plan/today/{key}", json={"done": False}).status_code == 200
    restored = api.get("/api/v1/health/plan/today").json()["items"]
    assert next(item for item in restored if item["task_key"] == key)["done"] is False

    # delete
    assert api.delete(f"/api/v1/health/plan/today/custom/{key}").status_code == 200
    plan = api.get("/api/v1/health/plan/today").json()["items"]
    assert not any(item.get("custom") for item in plan)


def test_custom_plan_rejects_non_custom_delete(api):
    assert api.delete("/api/v1/health/plan/today/custom/diet_record").status_code == 400


def test_custom_plan_requires_title(api):
    res = api.post("/api/v1/health/plan/today/custom", json={"title": "  "})
    assert res.status_code == 422
