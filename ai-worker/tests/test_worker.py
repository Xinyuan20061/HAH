import importlib
import io
import json
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from healthmate_worker import capabilities
from healthmate_worker.client import CloudAPI, CloudAPIError
from healthmate_worker.config import settings
from healthmate_worker.downloader import download_media
from healthmate_worker.errors import ProcessingError
from healthmate_worker.evaluation import evaluate_food
from healthmate_worker.models.semantic_runtime import (
    infer_motion_semantics,
    semantic_model_status,
)
from healthmate_worker.models.skeleton_schema import JOINT_NAMES, build_skeleton_tensor
from healthmate_worker.processors.analyzers import angle, get_analyzer
from healthmate_worker.processors.food import (
    _extract_json,
    analyze_food,
    preprocess_image,
)
from healthmate_worker.processors.motion import build_motion_score, explain_keyframes
from healthmate_worker.processors.recognition import recognize_exercise
from healthmate_worker.processors.semantic_features import infer_compositional_semantics
from healthmate_worker.results import FoodResult
from healthmate_worker.visualize import render_motion_preview
from healthmate_worker.security import UnsafeDownloadURL, validate_download_url
from PIL import Image
from pydantic import ValidationError

REAL_CLIENT = httpx.Client


def recognition_samples(kind: str) -> dict[str, list[dict]]:
    knee_cycle = [170, 155, 130, 95, 80, 110, 145, 165, 172]
    elbow_cycle = [170, 150, 125, 90, 80, 110, 145, 160, 170]
    result = {name: [] for name in ["squat", "pushup", "lunge"]}
    for knee, elbow in zip(knee_cycle, elbow_cycle):
        if kind == "pushup":
            row = dict(
                visibility=0.9,
                knee=170,
                elbow=elbow,
                trunk=78,
                body_line=173,
                back_knee=170,
            )
        elif kind == "squat":
            row = dict(
                visibility=0.9,
                knee=knee,
                elbow=170,
                trunk=18,
                body_line=150,
                back_knee=knee + 2,
            )
        elif kind == "lunge":
            row = dict(
                visibility=0.9,
                knee=knee,
                elbow=170,
                trunk=18,
                body_line=150,
                back_knee=min(178, knee + 52),
            )
        else:
            row = dict(
                visibility=0.9,
                knee=170,
                elbow=170,
                trunk=20,
                body_line=150,
                back_knee=170,
            )
        for exercise in result:
            result[exercise].append(dict(row))
    return result


@pytest.mark.parametrize("exercise", ["squat", "pushup", "lunge"])
def test_auto_recognition_selects_clear_feature_patterns(exercise):
    result = recognize_exercise(recognition_samples(exercise))
    assert result["accepted"] is True
    assert result["selected_type"] == exercise
    assert result["candidates"][0]["exercise_type"] == exercise
    assert 0 <= result["confidence"] <= 1


def test_auto_recognition_abstains_for_static_or_insufficient_evidence():
    static = recognize_exercise(recognition_samples("static"))
    insufficient = recognize_exercise(
        {name: rows[:2] for name, rows in recognition_samples("squat").items()}
    )
    assert static["accepted"] is False and static["selected_type"] is None
    assert insufficient["accepted"] is False and insufficient["candidates"] == []


@pytest.mark.parametrize(
    "kind,pattern,region",
    [
        ("squat", "bilateral_lower_body", "lower_body"),
        ("lunge", "unilateral_lower_body", "lower_body"),
        ("pushup", "horizontal_upper_body", "upper_body"),
    ],
)
def test_compositional_semantics_describe_motion_without_named_action(
    kind, pattern, region
):
    result = infer_compositional_semantics(recognition_samples(kind))
    assert result["available"] is True
    assert pattern in {item["key"] for item in result["movement_patterns"]}
    assert region in {item["key"] for item in result["observed_regions"]}
    assert "肌肉激活" in result["scope"]


def test_compositional_semantics_refuses_insufficient_pose_samples():
    result = infer_compositional_semantics(
        {name: rows[:2] for name, rows in recognition_samples("squat").items()}
    )
    assert result["available"] is False
    assert result["movement_patterns"] == []


def skeleton_samples(offset=0.0, scale=1.0):
    base = {
        "left_shoulder": (0.42, 0.25),
        "right_shoulder": (0.58, 0.25),
        "left_elbow": (0.38, 0.40),
        "right_elbow": (0.62, 0.40),
        "left_wrist": (0.36, 0.55),
        "right_wrist": (0.64, 0.55),
        "left_hip": (0.46, 0.52),
        "right_hip": (0.54, 0.52),
        "left_knee": (0.45, 0.72),
        "right_knee": (0.55, 0.72),
        "left_ankle": (0.44, 0.92),
        "right_ankle": (0.56, 0.92),
    }
    rows = []
    for frame_index in range(8):
        rows.append(
            {
                "skeleton": [
                    {
                        "id": name,
                        "x": x * scale + offset,
                        "y": (y + frame_index * 0.002) * scale + offset,
                        "visibility": 0.95,
                    }
                    for name, (x, y) in base.items()
                ]
            }
        )
    return rows


def test_canonical_skeleton_tensor_is_resampled_and_translation_scale_invariant():
    first = build_skeleton_tensor(skeleton_samples(), 32)
    transformed = build_skeleton_tensor(skeleton_samples(offset=2.5, scale=3.0), 32)
    assert first.shape == (32, len(JOINT_NAMES), 3)
    assert transformed.shape == first.shape
    assert abs(first[:, :, :2] - transformed[:, :, :2]).max() < 1e-4


def test_semantic_runtime_uses_rules_without_model_and_safely_falls_back(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "semantic_model_path", "")
    rule = infer_motion_semantics(recognition_samples("squat"))
    assert rule["method"] == "pose_compositional_rules_v1"
    assert semantic_model_status()["configured"] is False

    monkeypatch.setattr(settings, "semantic_model_path", str(tmp_path / "missing.pt"))
    fallback = infer_motion_semantics(recognition_samples("squat"))
    assert fallback["method"] == "pose_compositional_rules_v1"
    assert fallback["model_fallback"]["used"] is True


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setattr(settings, "api_base_url", "https://cloud.example/api/v1")
    monkeypatch.setattr(settings, "worker_token", "test-token")
    monkeypatch.setattr(settings, "vlm_provider", "local")
    monkeypatch.setattr(settings, "local_vlm_model", "test-vision")
    monkeypatch.setattr(settings, "allow_private_media_hosts", False)


def mock_client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: REAL_CLIENT(transport=transport, **kwargs)
    )


@pytest.mark.parametrize(
    "status,reason", [(401, "key/token"), (404, "/v1"), (500, "HTTP")]
)
def test_vlm_rejects_non_2xx(monkeypatch, status, reason):
    mock_client(monkeypatch, lambda request: httpx.Response(status))
    result = capabilities.vlm_status()
    assert not result["available"] and reason in result["reason"]


@pytest.mark.parametrize(
    "model,available", [("test-vision", True), ("missing", False), ("", False)]
)
def test_vlm_model_discovery(monkeypatch, model, available):
    monkeypatch.setattr(settings, "local_vlm_model", model)
    mock_client(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": [{"id": "test-vision"}]}),
    )
    result = capabilities.vlm_status()
    assert result["available"] is available
    assert result["models"] == ["test-vision"]


def test_vlm_invalid_model_response(monkeypatch):
    mock_client(
        monkeypatch, lambda request: httpx.Response(200, json={"unexpected": []})
    )
    assert not capabilities.vlm_status()["available"]


def test_capabilities_are_independent(monkeypatch):
    monkeypatch.setattr(capabilities, "pose_status", lambda: (True, "test"))
    monkeypatch.setattr(capabilities, "vlm_status", lambda: {"available": False})
    assert capabilities.effective_capabilities() == ["motion_pose"]
    monkeypatch.setattr(capabilities, "pose_status", lambda: (False, "test"))
    monkeypatch.setattr(capabilities, "vlm_status", lambda: {"available": True})
    assert capabilities.effective_capabilities() == ["food_vision"]


def test_missing_mediapipe_is_detected_without_import_crash(monkeypatch):
    real_import = importlib.import_module

    def load(name):
        if name == "mediapipe":
            raise ImportError("not installed")
        return real_import(name)

    monkeypatch.setattr(capabilities.importlib, "import_module", load)
    assert not capabilities.pose_status()[0]
    import healthmate_worker.processors  # Heavy dependencies are imported only during inference.


@pytest.mark.parametrize(
    "text",
    [
        '{"dish_name":"a","nested":{"x":"brace } inside"}}',
        '```json\n{"dish_name":"a","nested":{"x":1}}\n```',
        'prefix {"meta":1} then {"dish_name":"a"} suffix',
    ],
)
def test_json_parser_nested_fenced_and_prose(text):
    assert _extract_json(text)["dish_name"] == "a"


@pytest.mark.parametrize(
    "text", ["no json", '{"dish_name":"a"} {"dish_name":"b"}', "{invalid}"]
)
def test_json_parser_rejects_missing_or_ambiguous(text):
    with pytest.raises(ProcessingError):
        _extract_json(text)


def test_food_validation_requires_ranges_lists_and_finite_values():
    data = {
        "dish_name": "meal",
        "calories": 1.0,
        "protein": 1.0,
        "carbs": 1.0,
        "fat": 1.0,
        "confidence": 0.1,
    }
    result = FoodResult.model_validate(data)
    assert result.is_estimate and "校正" in result.warning
    assert result.calorie_range_low <= result.calories <= result.calorie_range_high
    assert result.portion_basis
    for change in [
        {"calories": -1},
        {"fat": float("nan")},
        {"protein": float("inf")},
        {"estimated_weight_g": 99999},
        {"confidence": 2},
        {"tips": "a string"},
        {"calories": "100"},
        {"calorie_range_low": 2.0, "calorie_range_high": 3.0},
        {"visible_items": ["x" * 161]},
    ]:
        with pytest.raises(ValidationError):
            FoodResult.model_validate({**data, **change})


def test_food_items_are_auditable_and_totals_are_deterministic():
    result = FoodResult.model_validate(
        {
            "dish_name": "米饭配鸡胸",
            "calories": 400.0,
            "protein": 35.0,
            "carbs": 45.0,
            "fat": 8.0,
            "confidence": 0.8,
            "items": [
                {"name": "米饭", "calories": 220.0, "evidence": "可见一碗米饭"},
                {"name": "鸡胸", "calories": 180.0, "evidence": "可见切片鸡胸"},
            ],
        }
    )
    assert result.calories == 400
    assert result.visible_items == ["米饭", "鸡胸"]
    assert result.calorie_range_low == 320
    with pytest.raises(ValidationError, match="逐项热量合计"):
        FoodResult.model_validate(
            {
                "dish_name": "冲突结果",
                "calories": 500.0,
                "protein": 1.0,
                "carbs": 1.0,
                "fat": 1.0,
                "confidence": 0.5,
                "items": [{"name": "可见食物", "calories": 100.0}],
            }
        )


def test_motion_preview_is_jpeg_bounded_and_face_anonymized():
    import numpy as np

    frame = np.full((480, 640, 3), 180, dtype=np.uint8)
    skeleton = [
        {"id": "left_shoulder", "x": 0.4, "y": 0.4, "visibility": 0.9},
        {"id": "right_shoulder", "x": 0.6, "y": 0.4, "visibility": 0.9},
        {"id": "left_hip", "x": 0.44, "y": 0.65, "visibility": 0.9},
        {"id": "right_hip", "x": 0.56, "y": 0.65, "visibility": 0.9},
    ]
    preview = render_motion_preview(
        frame, {"event": "squat_bottom", "timestamp": 1.2, "skeleton": skeleton}
    )
    assert preview["image_mime"] == "image/jpeg"
    assert preview["preview_bytes"] <= 80 * 1024
    assert preview["face_anonymized"] is True
    assert len(preview["preview_sha256"]) == 64


def test_food_benchmark_penalizes_failures_and_reports_evidence_metrics():
    annotations = [
        {"sample_id": "a", "dish_name": "番茄炒蛋", "items": ["番茄", "鸡蛋"], "calories": 300},
        {"sample_id": "b", "dish_name": "米饭", "items": ["米饭"], "calories": 200},
    ]
    predictions = [
        {
            "sample_id": "a",
            "status": "completed",
            "result": {
                "dish_name": "番茄炒蛋",
                "items": [{"name": "番茄", "calories": 80}, {"name": "鸡蛋", "calories": 210}],
                "calories": 290,
                "calorie_range_low": 250,
                "calorie_range_high": 350,
                "confidence": 0.8,
            },
        },
        {"sample_id": "b", "status": "failed"},
    ]
    report = evaluate_food(annotations, predictions)
    assert report["coverage_pct"] == 50
    assert report["dish_accuracy_all_samples_pct"] == 50
    assert report["item_detection"]["f1_pct"] == 80
    assert report["calorie_range_coverage_pct"] == 100


def test_image_preprocessing_resizes_limits_and_corrects_exif(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    image = Image.new("RGB", (2000, 1000), "blue")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    data = preprocess_image(source)
    assert len(data) <= settings.image_max_bytes
    with Image.open(io.BytesIO(data)) as output:
        assert (
            output.height > output.width
            and max(output.size) <= settings.image_max_dimension
        )
        assert output.format == "JPEG"


def test_invalid_image_is_rejected(tmp_path):
    source = tmp_path / "fake.jpg"
    source.write_text("not an image")
    with pytest.raises(ProcessingError, match="图片"):
        preprocess_image(source)


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, "unauthorized", "vlm_auth"),
        (404, "not loaded", "vlm_model_missing"),
        (500, "CUDA out of memory", "vlm_oom"),
        (429, "rate limit", "vlm_http"),
    ],
)
def test_vlm_inference_error_codes(tmp_path, monkeypatch, status, body, code):
    source = tmp_path / "meal.png"
    Image.new("RGB", (64, 64), "white").save(source)
    mock_client(monkeypatch, lambda request: httpx.Response(status, text=body))
    with pytest.raises(ProcessingError) as error:
        analyze_food(source)
    assert error.value.code == code


def test_vlm_payload_is_preprocessed_and_json_mode_opt_in(tmp_path, monkeypatch):
    source = tmp_path / "meal.png"
    Image.new("RGB", (2000, 1000), "white").save(source)
    captured = []
    data = {
        "dish_name": "meal",
        "calories": 200,
        "protein": 10,
        "carbs": 20,
        "fat": 5,
        "confidence": 0.8,
        "tips": [],
    }

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(data)}}]}
        )

    mock_client(monkeypatch, handler)
    monkeypatch.setattr(settings, "local_vlm_json_mode", True)
    result = analyze_food(source)
    assert result["is_estimate"] and len(result["image_sha256"]) == 64
    assert captured[0]["response_format"] == {"type": "json_object"}
    assert (
        len(captured[0]["messages"][0]["content"][1]["image_url"]["url"])
        < settings.image_max_bytes * 2
    )


def test_deepseek_provider_uses_explicit_vision_configuration(tmp_path, monkeypatch):
    source = tmp_path / "meal.png"
    Image.new("RGB", (64, 64), "white").save(source)
    monkeypatch.setattr(settings, "vlm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "test-deepseek-key")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "deepseek_vision_model", "deepseek-flash")
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "dish_name": "测试餐",
                                    "calories": 100.0,
                                    "protein": 5.0,
                                    "carbs": 10.0,
                                    "fat": 4.0,
                                    "confidence": 0.8,
                                }
                            )
                        }
                    }
                ]
            },
        )

    mock_client(monkeypatch, handler)
    result = analyze_food(source)
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["auth"] == "Bearer test-deepseek-key"
    assert captured["payload"]["model"] == "deepseek-flash"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert result["provider"] == "deepseek"


def test_food_classifier_candidates_constrain_dish_name_prompt(tmp_path, monkeypatch):
    source = tmp_path / "meal.png"
    Image.new("RGB", (64, 64), "white").save(source)
    monkeypatch.setattr(settings, "food_classifier_model", "food101-test")
    monkeypatch.setattr(
        "healthmate_worker.processors.food.classify_food_candidates",
        lambda *args: [{"label": "ramen", "score": 0.8}, {"label": "pho", "score": 0.15}],
    )
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "dish_name": "ramen",
                                    "calories": 320,
                                    "protein": 14,
                                    "carbs": 45,
                                    "fat": 9,
                                    "confidence": 0.7,
                                }
                            )
                        }
                    }
                ]
            },
        )

    mock_client(monkeypatch, handler)
    result = analyze_food(source)
    prompt = captured["messages"][0]["content"][0]["text"]
    assert '"ramen"' in prompt and '"pho"' in prompt
    assert result["dish_name"] == "ramen"


@pytest.mark.parametrize(
    "mode,local_confidence,expected_calls,expected_source",
    [
        ("local_first", 0.9, ["local-vlm"], "local"),
        ("local_first", 0.2, ["local-vlm", "deepseek"], "cloud"),
        ("cloud_first", 0.9, ["deepseek"], "cloud"),
        ("off", 0.9, ["local-vlm"], "local"),
    ],
)
def test_food_ai_modes_are_switchable(
    tmp_path, monkeypatch, mode, local_confidence, expected_calls, expected_source
):
    source = tmp_path / "meal.png"
    Image.new("RGB", (64, 64), "white").save(source)
    monkeypatch.setattr(settings, "ai_mode", mode)
    monkeypatch.setattr(settings, "vlm_provider", "local")
    monkeypatch.setattr(settings, "local_vlm_model", "local-food")
    monkeypatch.setattr(settings, "deepseek_api_key", "cloud-key")
    monkeypatch.setattr(settings, "deepseek_vision_model", "deepseek-flash")
    calls = []

    def infer(data, image_sha256, config, dish_candidates=None):
        calls.append(config["provider"])
        confidence = 0.95 if config["provider"] == "deepseek" else local_confidence
        return {"dish_name": "meal", "confidence": confidence}

    monkeypatch.setattr("healthmate_worker.processors.food._request_food", infer)
    result = analyze_food(source)
    assert calls == expected_calls
    assert result["source"] == expected_source


def test_cloud_first_food_failure_silently_falls_back_local(tmp_path, monkeypatch):
    source = tmp_path / "meal.png"
    Image.new("RGB", (64, 64), "white").save(source)
    monkeypatch.setattr(settings, "ai_mode", "cloud_first")
    monkeypatch.setattr(settings, "vlm_provider", "local")
    monkeypatch.setattr(settings, "local_vlm_model", "local-food")
    monkeypatch.setattr(settings, "deepseek_api_key", "cloud-key")
    calls = []

    def infer(data, image_sha256, config, dish_candidates=None):
        calls.append(config["provider"])
        if config["provider"] == "deepseek":
            raise ProcessingError("vlm_unavailable", "temporary", True)
        return {"dish_name": "meal", "confidence": 0.7}

    monkeypatch.setattr("healthmate_worker.processors.food._request_food", infer)
    result = analyze_food(source)
    assert calls == ["deepseek", "local-vlm"]
    assert result["source"] == "local"


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.2",
        "192.168.1.2",
        "224.0.0.1",
        "::1",
        "0.0.0.0",
    ],
)
def test_dns_private_reserved_and_multicast_rejected(monkeypatch, ip):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443))]
    )
    with pytest.raises(UnsafeDownloadURL):
        validate_download_url("https://media.example/image.jpg")


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example/x",
        "file:///tmp/a",
        "https://user:password@media.example/x",
        "https://media.example:bad/x",
    ],
)
def test_unsafe_schemes_credentials_and_ports_rejected(url):
    with pytest.raises(UnsafeDownloadURL):
        validate_download_url(url)


def public_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))]
    )


def test_download_pins_checked_ip_preserves_tls_sni_and_host(monkeypatch):
    public_dns(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"media")

    mock_client(monkeypatch, handler)
    path = download_media("https://media.example/x.jpg?secret=signed")
    try:
        assert path.read_bytes() == b"media"
        assert (
            seen[0].url.host == "8.8.8.8" and seen[0].headers["Host"] == "media.example"
        )
        assert seen[0].extensions["sni_hostname"] == b"media.example"
    finally:
        path.unlink()


def test_redirect_to_private_host_is_rejected(monkeypatch):
    calls = []

    def resolve(host, *a, **k):
        return [
            (
                2,
                1,
                6,
                "",
                ("127.0.0.1" if host == "internal.example" else "8.8.8.8", 443),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def handler(request):
        calls.append(request)
        return httpx.Response(
            302, headers={"Location": "https://internal.example/private"}
        )

    mock_client(monkeypatch, handler)
    with pytest.raises(UnsafeDownloadURL):
        download_media("https://media.example/x")
    assert len(calls) == 1


def test_redirect_limit(monkeypatch):
    public_dns(monkeypatch)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "/loop"})

    mock_client(monkeypatch, handler)
    with pytest.raises(ProcessingError) as error:
        download_media("https://media.example/x")
    assert error.value.code == "too_many_redirects"
    assert len(calls) == settings.max_redirects + 1


@pytest.mark.parametrize("declared", [True, False])
def test_download_size_limit(monkeypatch, declared):
    public_dns(monkeypatch)
    monkeypatch.setattr(settings, "max_download_mb", 1)
    headers = {"Content-Length": "2000000"} if declared else {}
    mock_client(
        monkeypatch,
        lambda request: httpx.Response(200, headers=headers, content=b"a" * 2000000),
    )
    with pytest.raises(ProcessingError) as error:
        download_media("https://media.example/x")
    assert error.value.code == "media_too_large"


def rows(primary="knee", bad_body=False):
    sequence = [170, 160, 135, 95, 85, 100, 140, 160, 170]
    return [
        {
            "t": index * 0.2,
            "knee": value,
            "elbow": value if primary == "elbow" else 175,
            "hip": 150,
            "trunk": 10,
            "visibility": 0.9,
            "back_visibility": 0.9,
            "back_knee": 90,
            "side": "left",
            "body_line": 140 if bad_body else 180,
            "body_offset": 0.2 if bad_body else 0.01,
        }
        for index, value in enumerate(sequence)
    ]


@pytest.mark.parametrize("exercise", ["squat", "pushup", "lunge"])
def test_motion_analyzers_count_complete_cycles_and_select_real_events(exercise):
    samples = rows("elbow" if exercise == "pushup" else "knee")
    pose, frames = get_analyzer(exercise).analyze(samples, len(samples))
    assert pose["available"] and pose["reps"] == 1
    bottom = next(frame for frame in frames if frame["event"] == exercise + "_bottom")
    assert bottom["timestamp"] == 0.8 and bottom["confidence"] == 0.9
    assert "url" not in bottom


def test_pushup_uses_elbows_not_knee_rules_and_alignment_errors():
    pose, _ = get_analyzer("pushup").analyze(rows(), 9)
    assert pose["reps"] == 0
    pose, _ = get_analyzer("pushup").analyze(rows("elbow", bad_body=True), 9)
    assert any(error["code"] == "body_alignment" for error in pose["errors"])


@pytest.mark.parametrize("exercise", ["squat", "pushup", "lunge"])
def test_motion_insufficient_visibility_is_not_successful_evaluation(exercise):
    samples = rows()
    for row in samples:
        row["visibility"] = 0.1
    pose, frames = get_analyzer(exercise).analyze(samples, 9)
    assert not pose["available"] and "不足以评价" in pose["message"] and not frames


def test_incomplete_cycle_and_occlusion_do_not_fabricate_reps():
    samples = rows()[:5]
    pose, _ = get_analyzer("squat").analyze(samples, 5)
    assert pose["reps"] == 0 and pose["incomplete_cycle"]
    samples = rows()
    samples[-2]["t"] += 5
    samples[-1]["t"] += 5
    pose, _ = get_analyzer("squat").analyze(samples, 9)
    assert pose["reps"] == 0


def test_angle_degenerate_and_real_geometry():
    assert angle((0, 0), (0, 0), (1, 0)) is None
    assert angle((1, 0), (0, 0), (0, 1)) == pytest.approx(90)


def test_motion_score_is_explainable_and_keyframes_have_coaching_text():
    pose, frames = get_analyzer("squat").analyze(rows(), 9)
    score = build_motion_score(pose)
    assert score["available"] and 0 <= score["overall"] <= 100
    assert len(score["basis"]) == 4 and "二维姿态" in score["disclaimer"]
    explained = explain_keyframes(frames, pose)
    assert all(
        frame.get("stage") and frame.get("finding") and frame.get("advice")
        for frame in explained
    )


@pytest.mark.parametrize("status", [401, 403])
def test_cloud_client_auth_failures_are_not_retried_and_never_echo_tokens(
    monkeypatch, status
):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, text="secret-provider-response")

    mock_client(monkeypatch, handler)
    with CloudAPI() as api:
        with pytest.raises(CloudAPIError) as error:
            api.heartbeat()
    assert len(seen) == 1 and "secret-provider-response" not in str(error.value)


@pytest.mark.parametrize("status", [429, 503])
def test_cloud_client_bounded_retry_and_claim_id_stability(monkeypatch, status):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return (
            httpx.Response(status)
            if len(seen) < 3
            else httpx.Response(200, json={"job": None})
        )

    mock_client(monkeypatch, handler)
    monkeypatch.setattr("healthmate_worker.client.time.sleep", lambda _: None)
    with CloudAPI() as api:
        assert api.claim() is None
    assert len(seen) == 3 and len({request["request_id"] for request in seen}) == 1


def test_long_inference_renews_lease_and_cleans_download(tmp_path, monkeypatch):
    import worker

    path = tmp_path / "media.jpg"
    path.write_bytes(b"test media")
    monkeypatch.setattr(worker, "download_media", lambda *a: path)

    def infer(*a, **k):
        time.sleep(1.3)
        return {"test_only": True}

    monkeypatch.setattr(worker, "analyze_food", infer)

    class API:
        def __init__(self):
            self.progress_calls = []
            self.completed = False

        def progress(self, *a):
            self.progress_calls.append(time.monotonic())

        def complete(self, *a):
            self.completed = True

        def fail(self, *a):
            raise AssertionError("unexpected failure")

    api = API()
    assert worker.run_job(
        api,
        {
            "job_id": 1,
            "lease_token": "test-lease",
            "lease_seconds": 3,
            "job_type": "food_vision",
            "source": {"url": "test"},
        },
    )
    assert api.completed and len(api.progress_calls) >= 2 and not path.exists()


def test_worker_expired_download_reports_refresh_without_leaking_url(monkeypatch):
    import worker

    request = httpx.Request("GET", "https://media.example/x?token=private")
    response = httpx.Response(403, request=request)

    def download(*a):
        raise httpx.HTTPStatusError("token=private", request=request, response=response)

    monkeypatch.setattr(worker, "download_media", download)

    class API:
        def progress(self, *a):
            pass

        def fail(self, *a):
            self.failure = a

    api = API()
    assert not worker.run_job(
        api,
        {
            "job_id": 1,
            "lease_token": "lease",
            "job_type": "food_vision",
            "source": {"url": "test"},
        },
    )
    assert api.failure[2] == "media_url_expired" and "private" not in api.failure[3]
