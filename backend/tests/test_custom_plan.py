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


def test_plan_activity_year_distinguishes_empty_partial_and_complete_days(api):
    today = api.get("/api/v1/health/plan/today").json()
    activity = api.get("/api/v1/health/plan/activity/year")
    assert activity.status_code == 200
    body = activity.json()
    assert len(body["days"]) == 365
    assert body["days"][-1]["date"] == body["end"]
    assert body["days"][-1]["status"] == "empty"

    first = today["items"][0]
    assert api.put(
        f"/api/v1/health/plan/today/{first['task_key']}", json={"done": True}
    ).status_code == 200
    partial = api.get("/api/v1/health/plan/activity/year").json()["days"][-1]
    assert partial["status"] == "partial"
    assert partial["done"] == 1
    assert partial["total"] == 3

    for item in today["items"][1:]:
        assert api.put(
            f"/api/v1/health/plan/today/{item['task_key']}", json={"done": True}
        ).status_code == 200
    complete = api.get("/api/v1/health/plan/activity/year").json()["days"][-1]
    assert complete["status"] == "complete"
    assert complete["completion"] == 1
