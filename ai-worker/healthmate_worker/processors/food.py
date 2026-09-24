from __future__ import annotations

import base64
import hashlib
import io
import json
import warnings
from pathlib import Path

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from ..config import settings
from ..errors import ProcessingError
from ..models.food_classifier import classify_food_candidates
from ..results import FoodResult

PROMPT = """你是谨慎的餐食视觉分析器。只根据图片中可见证据返回一个 JSON 对象，不要 Markdown。
必需字段：dish_name, portion, cooking_method, estimated_weight_g, calories, protein, carbs, fat, fiber, confidence, tips,
visible_items, portion_basis, calorie_range_low, calorie_range_high, uncertainty_reasons, items。
营养值为图片中整份餐食的估算，热量 kcal，重量/营养素 g，所有数值非负，confidence 0~1。
items 必须逐项列出图片中的主要食材或菜品，每项包含 name, portion, portion_basis, weight_g, calories,
calorie_range_low, calorie_range_high, protein, carbs, fat, fiber, confidence, evidence, in_image。
每项 evidence 用一句话说明可见依据；in_image 仅在图片中确实可见时为 true。整份 calories 必须接近 items 的 calories 合计，
整份热量区间必须覆盖逐项区间之和。不要虚构被遮挡的配料；无法拆分时只返回一个低置信度整体项并解释原因。
visible_items 列出实际看见的主要食材或菜品；portion_basis 简述餐具、面积或常见份量依据；热量点估计必须位于热量区间内。
份量估计优先参考标准餐盘、饭碗、汤勺、手掌和画面占比；没有可靠比例尺时必须明确说明。
无法从图片确定的配料、油盐和重量必须写入 uncertainty_reasons 并降低 confidence，禁止把猜测写成事实。
tips 最多 3 条，只给日常、可执行的饮食建议。不做疾病诊断或治疗建议。"""


def provider_config(provider: str | None = None) -> dict:
    """Resolve one explicit provider while preserving legacy DeepSeek config."""
    selected = (provider or settings.effective_vlm_provider).strip().lower()
    if selected == "deepseek":
        base_url = settings.deepseek_base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        return {
            "provider": "deepseek",
            "base_url": base_url,
            "model": settings.deepseek_vision_model.strip(),
            "api_key": settings.deepseek_api_key or settings.local_vlm_api_key,
            "json_mode": True,
        }
    return {
        "provider": "local-vlm",
        "base_url": settings.local_vlm_base_url.rstrip("/"),
        "model": settings.local_vlm_model.strip(),
        "api_key": settings.local_vlm_api_key,
        "json_mode": settings.local_vlm_json_mode,
    }


def _extract_json(text: str) -> dict:
    if not isinstance(text, str) or len(text) > 100000:
        raise ProcessingError("vlm_invalid_json", "模型 JSON 输出无效")
    decoder = json.JSONDecoder()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    # raw_decode understands nested braces and escaped strings; do not use greedy regex.
    candidates = []
    position = 0
    while position < len(text):
        start = text.find("{", position)
        if start < 0:
            break
        try:
            value, length = decoder.raw_decode(text[start:])
        except ValueError:
            position = start + 1
            continue
        if isinstance(value, dict):
            candidates.append(value)
        position = start + length
    matching = [value for value in candidates if "dish_name" in value]
    if len(matching) == 1:
        return matching[0]
    if not matching and len(candidates) == 1:
        return candidates[0]
    raise ProcessingError("vlm_invalid_json", "模型 JSON 缺失或存在歧义")


def preprocess_image(path: Path) -> bytes:
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ProcessingError("invalid_media", "图片不存在或超过 20MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.width * source.height > 25_000_000:
                    raise ProcessingError("invalid_media", "图片像素过多")
                source.verify()
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail(
                    (settings.image_max_dimension, settings.image_max_dimension)
                )
                for _ in range(8):
                    for quality in (85, 70, 55):
                        buffer = io.BytesIO()
                        image.save(
                            buffer, format="JPEG", quality=quality, optimize=True
                        )
                        data = buffer.getvalue()
                        if len(data) <= settings.image_max_bytes:
                            return data
                    image.thumbnail(
                        (
                            max(64, int(image.width * 0.75)),
                            max(64, int(image.height * 0.75)),
                        )
                    )
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ProcessingError("invalid_media", "无效或不安全的图片") from None
    raise ProcessingError("image_too_large", "图片压缩后仍超过 VLM 请求上限")


def _request_food(
    data: bytes,
    image_sha256: str,
    config: dict,
    dish_candidates: list[dict] | None = None,
) -> dict:
    if not config["model"]:
        setting_name = (
            "DEEPSEEK_VISION_MODEL"
            if config["provider"] == "deepseek"
            else "LOCAL_VLM_MODEL"
        )
        raise ProcessingError("vlm_model_missing", f"请先配置 {setting_name}")
    if config["provider"] == "deepseek" and not config["api_key"]:
        raise ProcessingError("vlm_auth", "请先配置 DEEPSEEK_API_KEY")
    prompt = PROMPT
    if dish_candidates:
        candidate_names = [str(item["label"]) for item in dish_candidates]
        prompt += (
            "\n菜名候选由已训练的 Food-101 分类器提供："
            + json.dumps(candidate_names, ensure_ascii=False)
            + "。dish_name 必须从这些候选中选择；你只负责按可见画面估算份量和营养，不另猜菜名。"
        )
    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(data).decode("ascii")
                        },
                    },
                ],
            }
        ],
        "temperature": 0.15,
        "max_tokens": 3072,
    }
    if config["json_mode"]:
        payload["response_format"] = {"type": "json_object"}
    if config["provider"] == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    headers = (
        {"Authorization": f"Bearer {config['api_key']}"} if config["api_key"] else {}
    )
    try:
        with httpx.Client(
            timeout=settings.local_vlm_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                config["base_url"] + "/chat/completions",
                headers=headers,
                json=payload,
            )
        if not response.is_success:
            status = response.status_code
            raw = response.text.lower()[:4000]
            if "out of memory" in raw or "cuda oom" in raw:
                raise ProcessingError(
                    "vlm_oom", "视觉模型显存不足，请选择较小或量化模型"
                )
            if status in {401, 403}:
                raise ProcessingError("vlm_auth", "视觉模型 API key/token 或权限错误")
            if status == 404:
                raise ProcessingError(
                    "vlm_model_missing", "检查视觉模型 Base URL 与模型 ID"
                )
            raise ProcessingError(
                "vlm_http",
                f"视觉模型 HTTP {status}",
                status in {408, 429} or status >= 500,
            )
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
        result = _extract_json(content)
        result.update(provider=config["provider"], model=config["model"])
        if dish_candidates:
            result["dish_candidates"] = dish_candidates
        result["image_sha256"] = image_sha256
        return FoodResult.model_validate(result).model_dump()
    except httpx.TimeoutException:
        raise ProcessingError("vlm_timeout", "视觉模型推理超时", True) from None
    except httpx.TransportError:
        raise ProcessingError("vlm_unavailable", "无法连接视觉模型服务", True) from None
    except (ValidationError, ValueError, KeyError, IndexError, TypeError):
        raise ProcessingError(
            "vlm_invalid_result", "视觉模型返回字段或数值无效，请校正或手动记录"
        ) from None


def analyze_food(image_path: Path, progress=None) -> dict:
    if progress:
        progress(35, "vlm_prepare")
    data = preprocess_image(image_path)
    image_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
    dish_candidates = []
    if settings.food_classifier_model.strip():
        try:
            dish_candidates = classify_food_candidates(
                image_path,
                settings.food_classifier_model,
                settings.food_classifier_top_k,
            )
        except (RuntimeError, OSError, ValueError):
            # Candidate generation is an optional quality layer. Its absence
            # must never prevent the configured VLM fallback from running.
            dish_candidates = []
    if progress:
        progress(55, "vlm_inference")
    mode = settings.ai_mode
    if mode == "local_first" and settings.vlm_provider.strip().lower() == "deepseek":
        # Preserve deployments that explicitly selected DeepSeek before AI_MODE.
        mode = "cloud_first"
    legacy_deepseek = settings.vlm_provider.strip().lower() == "deepseek"
    local_ready = bool(settings.local_vlm_model.strip()) and not legacy_deepseek
    cloud_key = settings.deepseek_api_key or (settings.local_vlm_api_key if legacy_deepseek else "")
    cloud_ready = bool(cloud_key.strip() and settings.deepseek_vision_model.strip())
    if mode == "off":
        order = ["local"]
    elif mode == "cloud_first":
        order = ["deepseek", "local"]
    else:
        order = ["local", "deepseek"]
    order = [
        provider
        for provider in order
        if (provider == "local" and local_ready) or (provider == "deepseek" and cloud_ready)
    ]
    if not order:
        raise ProcessingError("vlm_model_missing", "没有可用的本地识餐引擎")

    results = []
    last_error = None
    for index, provider in enumerate(order):
        if progress and index:
            progress(60, "vlm_fallback")
        try:
            result = _request_food(
                data,
                image_sha256,
                provider_config(provider),
                dish_candidates,
            )
            result["source"] = "cloud" if provider == "deepseek" else "local"
            results.append(result)
            # Cloud-first returns immediately on a valid cloud result. Local-first
            # spends cloud tokens only when the local estimate is below threshold.
            if mode == "cloud_first" or mode == "off":
                return result
            if provider == "local" and float(result.get("confidence") or 0) >= settings.local_confidence_threshold:
                return result
        except ProcessingError as exc:
            last_error = exc
            continue
    if results:
        return max(results, key=lambda item: float(item.get("confidence") or 0))
    raise last_error or ProcessingError("vlm_unavailable", "识餐引擎暂不可用", True)
