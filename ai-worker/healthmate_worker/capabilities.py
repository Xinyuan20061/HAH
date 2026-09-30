"""Probe each independent engine; an absent VLM never disables MediaPipe."""

import importlib
import httpx
from .config import settings
from .processors.food import provider_config

# Kinetics smoke probe result, cached after the first run per process
# (loading the 132 MB checkpoint on every probe would be wasteful).
_KINETICS_SMOKE: dict | None = None


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


def kinetics400_status() -> dict:
    """Probe the optional SlowFast Kinetics-400 recognizer.

    A checkpoint file plus an importable torch is not enough to claim the
    capability: the model must actually load its weights, read the 400-label
    map and complete one forward pass (smoke). The probe runs once per process
    and caches the result because loading the 132 MB checkpoint is expensive.
    """
    global _KINETICS_SMOKE
    if _KINETICS_SMOKE is not None:
        return dict(_KINETICS_SMOKE)
    from pathlib import Path

    path = settings.kinetics400_checkpoint.strip()
    if not path:
        return {"available": False, "reason": "KINETICS400_CHECKPOINT 未配置"}
    if not Path(path).is_file():
        return {"available": False, "reason": "KINETICS400_CHECKPOINT 指向的文件不存在"}
    try:
        import torch  # noqa: F401
    except ImportError:
        return {"available": False, "reason": "PyTorch 未安装，无法运行 SlowFast"}
    try:
        from .models.kinetics_runtime import get_kinetics400

        model = get_kinetics400()
        if model is None:
            return {"available": False, "reason": "SlowFast 权重加载失败"}
        # Small-input smoke: full weight load + label map + one forward pass.
        # A zero clip is a deterministic proxy; real video inference is covered
        # by the standalone kinetics400 job and the fixed-set evaluation.
        clip = torch.zeros(1, 3, 32, 224, 224)
        out = model.predict(clip, topk=1)
        if not out or not out.get("top_label"):
            raise RuntimeError("smoke 推理无输出")
        size_mb = Path(path).stat().st_size // 1024 // 1024
        _KINETICS_SMOKE = {
            "available": True,
            "reason": (
                f"SlowFast Kinetics-400 smoke 通过（{size_mb} MB，"
                f"top={out['top_label']}，候选分值 {out.get('top_probability', 0):.2f}）"
            ),
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": f"SlowFast 加载或推理失败（{type(exc).__name__}），按未就绪处理",
        }
    return dict(_KINETICS_SMOKE)


def effective_capabilities() -> list[str]:
    usable = []
    pose_ok, _ = pose_status()
    if "motion_pose" in settings.capability_list and pose_ok:
        usable.append("motion_pose")
    if "food_vision" in settings.capability_list and vlm_status()["available"]:
        usable.append("food_vision")
    if "kinetics400" in settings.capability_list and kinetics400_status()["available"]:
        usable.append("kinetics400")
    # Unified motion chain: one decode feeds pose + SlowFast + timeline. It only
    # needs MediaPipe pose as the base engine; Kinetics weights may be offline
    # and the chain still degrades to six-action recognition (reported at
    # runtime as kinetics.status="unavailable"). If pose itself is unavailable the
    # worker must not claim unified jobs at all.
    if "motion_unified_v1" in settings.capability_list and pose_ok:
        usable.append("motion_unified_v1")
    return usable
