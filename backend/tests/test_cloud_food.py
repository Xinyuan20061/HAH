from types import SimpleNamespace

import httpx

from app.core.config import settings
from app.services.vision.cloud_food import CloudFoodError
from test_reliability import register, claim


def result():
    return {
        "dish_name": "测试餐",
        "calories": 120.0,
        "protein": 8.0,
        "carbs": 15.0,
        "fat": 4.0,
        "confidence": 0.8,
        "provider": "deepseek",
        "model": "deepseek-flash",
        "source": "cloud",
        "image_sha256": "a" * 64,
    }


def test_cloud_food_route_completes_without_worker(api, monkeypatch):
    import app.api.v1.vision as vision

    asset, _ = register(api)
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(vision, "_food_image_bytes", lambda asset: b"image")
    monkeypatch.setattr(vision, "analyze_food_cloud", lambda data: result())
    response = api.post(
        "/api/v1/vision/food-jobs",
        json={"media_id": asset["media_id"], "route": "cloud"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["result"]["source"] == "cloud"
    assert body["result"]["analysis_id"]


def test_cloud_food_failure_silently_falls_back_to_worker_queue(api, monkeypatch):
    import app.api.v1.vision as vision

    asset, _ = register(api)
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(vision, "_food_image_bytes", lambda asset: b"image")

    def fail(_data):
        raise CloudFoodError("vlm_unavailable", "provider detail must stay private")

    monkeypatch.setattr(vision, "analyze_food_cloud", fail)
    response = api.post(
        "/api/v1/vision/food-jobs",
        json={"media_id": asset["media_id"], "route": "cloud"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["error_code"] == "cloud_fallback"
    assert "provider detail" not in response.text
    assert claim(api)["job_id"] == body["job_id"]


def test_cloud_food_download_pins_validated_public_address(monkeypatch):
    import app.api.v1.vision as vision

    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["host"] = request.headers.get("host")
        return httpx.Response(200, content=b"image")

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(vision, "resolve_ai_host", lambda url: "203.0.113.9")
    asset = SimpleNamespace(
        size_bytes=5,
        storage_backend="cloud_ref",
        storage_key="",
        source_url="https://media.example.test/meal.jpg",
    )
    assert vision._food_image_bytes(asset) == b"image"
    assert captured == {
        "url": "https://203.0.113.9/meal.jpg",
        "host": "media.example.test",
    }
