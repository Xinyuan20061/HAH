"""Direct cloud food recognition with strict structured-output validation."""

from __future__ import annotations

import base64
import hashlib
import io
import json

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.ai_results import FoodResult


FOOD_PROMPT = """你是谨慎的餐食视觉分析器。只根据图片中可见内容返回一个 JSON 对象，不要 Markdown。
必需字段：dish_name, portion, cooking_method, estimated_weight_g, calories, protein, carbs, fat, fiber,
confidence, tips, visible_items, portion_basis, calorie_range_low, calorie_range_high, uncertainty_reasons, items。
营养值为整份餐食估算：热量 kcal，重量和营养素 g，数值非负，confidence 为 0~1。
items 逐项列出主要可见食材或菜品，每项包含 name, portion, portion_basis, weight_g, calories,
calorie_range_low, calorie_range_high, protein, carbs, fat, fiber, confidence, evidence, in_image。
用餐具、手掌、常见容器和画面占比作为份量锚点；看不到比例尺时明确写入 uncertainty_reasons。
整份 calories 应接近逐项合计，热量区间覆盖逐项区间之和。不得虚构遮挡配料；无法拆分时只返回一个低置信整体项。
tips 最多 3 条，只提供日常可执行建议，不做疾病诊断或治疗建议。"""


class CloudFoodError(RuntimeError):
    """A safe, provider-detail-free error that should fall back to the Worker."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _prepare_image(image_bytes: bytes) -> bytes:
    if not image_bytes or len(image_bytes) > settings.food_image_max_bytes:
        raise CloudFoodError("invalid_media", "图片为空或超过云端识别大小限制")
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if image.width * image.height > 25_000_000:
                raise CloudFoodError("invalid_media", "图片像素过多")
            image.thumbnail((1280, 1280))
            for quality in (85, 70, 55):
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                if len(output.getvalue()) <= 1024 * 1024:
                    return output.getvalue()
    except CloudFoodError:
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        raise CloudFoodError("invalid_media", "图片格式无效") from None
    raise CloudFoodError("image_too_large", "图片压缩后仍超过云端识别限制")


def _extract_json(content) -> dict:
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    if not isinstance(content, str) or len(content) > 100_000:
        raise CloudFoodError("vlm_invalid_json", "云端返回格式无效")
    try:
        value = json.loads(content)
    except ValueError:
        decoder = json.JSONDecoder()
        start = content.find("{")
        if start < 0:
            raise CloudFoodError("vlm_invalid_json", "云端返回格式无效") from None
        try:
            value, _ = decoder.raw_decode(content[start:])
        except ValueError:
            raise CloudFoodError("vlm_invalid_json", "云端返回格式无效") from None
    if not isinstance(value, dict):
        raise CloudFoodError("vlm_invalid_json", "云端返回格式无效")
    return value


def analyze_food_cloud(image_bytes: bytes) -> dict:
    """Analyze one image through DeepSeek; callers own silent Worker fallback."""
    if not settings.deepseek_api_key.strip():
        raise CloudFoodError("vlm_auth", "云端识别未配置")
    data = _prepare_image(image_bytes)
    base_url = settings.deepseek_base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    payload = {
        "model": settings.deepseek_vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": FOOD_PROMPT},
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
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    try:
        with httpx.Client(
            timeout=settings.food_cloud_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                json=payload,
            )
        if response.status_code in {401, 403}:
            raise CloudFoodError("vlm_auth", "云端识别权限不可用")
        if response.status_code == 404:
            raise CloudFoodError("vlm_model_missing", "云端识别模型不可用")
        if not response.is_success:
            raise CloudFoodError("vlm_http", f"云端识别暂不可用（HTTP {response.status_code}）")
        body = response.json()
        value = _extract_json(body["choices"][0]["message"]["content"])
        value.update(
            provider="deepseek",
            model=settings.deepseek_vision_model,
            source="cloud",
            image_sha256=hashlib.sha256(image_bytes).hexdigest(),
        )
        return FoodResult.model_validate(value).model_dump()
    except CloudFoodError:
        raise
    except httpx.TimeoutException:
        raise CloudFoodError("vlm_timeout", "云端识别超时") from None
    except httpx.TransportError:
        raise CloudFoodError("vlm_unavailable", "云端识别暂不可用") from None
    except (ValidationError, ValueError, KeyError, IndexError, TypeError):
        raise CloudFoodError("vlm_invalid_result", "云端识别结果格式无效") from None
