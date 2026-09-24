"""Probe each independent engine; an absent VLM never disables MediaPipe."""

import importlib
import httpx
from .config import settings
from .processors.food import provider_config


def pose_status() -> tuple[bool, str]:
    try:
        cv2 = importlib.import_module("cv2")
        mp = importlib.import_module("mediapipe")
        if not hasattr(mp, "solutions") or not hasattr(mp.solutions, "pose"):
            return (
                False,
                "MediaPipe solutions.pose 不可用，请安装 requirements.txt 中的兼容版本",
            )
        # Import success alone does not verify the bundled model/native runtime.
        with mp.solutions.pose.Pose(model_complexity=1) as pose:
            import numpy as np

            pose.process(np.zeros((64, 64, 3), dtype=np.uint8))
        return True, f"OpenCV {cv2.__version__}; MediaPipe {mp.__version__}"
    except Exception as exc:
        if (
            isinstance(exc, FileNotFoundError)
            and not str(getattr(locals().get("mp"), "__file__", "")).isascii()
        ):
            return (
                False,
                "MediaPipe 原生库无法读取中文安装路径，请在 C:\\HealthMate\\.venv 等纯英文目录安装环境",
            )
        return False, f"Pose 引擎不可用 ({type(exc).__name__})"


def vlm_status() -> dict:
    config = provider_config()
    result = {
        "available": False,
        "models": [],
        "provider": config["provider"],
        "reason": "VLM 未配置",
    }
    if not config["base_url"]:
        return result
    if config["provider"] == "deepseek" and not config["api_key"]:
        result["reason"] = "DEEPSEEK_API_KEY 未配置"
        return result
    headers = (
        {"Authorization": f"Bearer {config['api_key']}"} if config["api_key"] else {}
    )
    try:
        with httpx.Client(timeout=4, trust_env=False) as client:
            response = client.get(config["base_url"] + "/models", headers=headers)
        if not response.is_success:
            reason = {
                401: "VLM API key/token 无效",
                403: "VLM 访问权限不足",
                404: "VLM Base URL 错误，请检查 /v1",
            }.get(response.status_code, "VLM HTTP 请求失败")
            result["reason"] = f"{reason} (HTTP {response.status_code})"
            return result
        body = response.json()
        result["models"] = [
            x["id"]
            for x in body["data"]
            if isinstance(x, dict) and isinstance(x.get("id"), str)
        ]
        if not config["model"]:
            result["reason"] = "视觉模型 ID 为空，请从 /models 选择模型"
        elif config["model"] not in result["models"]:
            result["reason"] = "配置的视觉模型不在 /models 中"
        else:
            result.update(
                available=True,
                reason="VLM endpoint 和模型 ID 可用；图像能力需实际推理验证",
            )
    except httpx.TimeoutException:
        result["reason"] = "VLM 探测超时"
    except httpx.HTTPError:
        result["reason"] = "VLM 无法连接"
    except (ValueError, KeyError, TypeError):
        result["reason"] = "VLM /models 返回格式无效"
    return result


def effective_capabilities() -> list[str]:
    usable = []
    if "motion_pose" in settings.capability_list and pose_status()[0]:
        usable.append("motion_pose")
    if "food_vision" in settings.capability_list and vlm_status()["available"]:
        usable.append("food_vision")
    return usable
