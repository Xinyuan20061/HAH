from __future__ import annotations
import argparse
import base64
import importlib
import importlib.util
import io
import shutil
import subprocess
import sys
import logging
from urllib.parse import urlsplit
import httpx
from healthmate_worker.config import settings
from healthmate_worker.client import CloudAPI, CloudAPIError
from healthmate_worker.capabilities import pose_status, vlm_status
from healthmate_worker.models.semantic_runtime import semantic_model_status
from healthmate_worker.processors.food import provider_config


def report(level, name, detail=""):
    print(f"[{level}] {name}" + (f": {detail}" if detail else ""))


def cloud_health_url():
    parsed = urlsplit(settings.api_base_url)
    return f"{parsed.scheme}://{parsed.netloc}/health/live"


def smoke_test():
    from PIL import Image

    config = provider_config()
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, format="JPEG")
    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the image color in one word."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(buffer.getvalue()).decode()
                        },
                    },
                ],
            }
        ],
        "max_tokens": 512,
    }
    if config["provider"] == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    headers = (
        {"Authorization": f"Bearer {config['api_key']}"} if config["api_key"] else {}
    )
    with httpx.Client(
        timeout=settings.local_vlm_timeout_seconds, trust_env=False
    ) as client:
        response = client.post(
            config["base_url"] + "/chat/completions",
            headers=headers,
            json=payload,
        )
    return response.is_success and bool(
        response.json()["choices"][0]["message"]["content"]
    )


def main():
    parser = argparse.ArgumentParser(description="HealthMate runtime checks")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip cloud checks; report local engines only",
    )
    parser.add_argument(
        "--vlm-smoke",
        action="store_true",
        help="send a tiny real image to configured VLM",
    )
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    compatible = (3, 11) <= sys.version_info[:2] <= (3, 12)
    report(
        "OK" if compatible else "WARN",
        "Python",
        sys.version.split()[0] + "; recommended 3.12 (MediaPipe wheel compatibility)",
    )
    for name in ["cv2", "mediapipe", "PIL", "httpx", "pydantic", "pydantic_settings"]:
        try:
            module = importlib.import_module(name)
            report("OK", name, getattr(module, "__version__", "imported"))
        except Exception as exc:
            report("OFF", name, f"import failed ({type(exc).__name__})")
    pose_ok, detail = pose_status()
    report("OK" if pose_ok else "OFF", "motion_pose", detail)
    semantic = semantic_model_status()
    report(
        "OK" if semantic["available"] else "WARN",
        "semantic_model",
        semantic.get("reason")
        or f"{semantic.get('model_key')} {semantic.get('version')}",
    )
    report(
        "OK" if shutil.which("ffmpeg") else "WARN",
        "FFmpeg",
        "optional; OpenCV provides video decoding",
    )
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        report("OK", "NVIDIA GPU", output)
    except (OSError, subprocess.SubprocessError):
        report("WARN", "NVIDIA GPU", "not detected; pose uses CPU")
    if importlib.util.find_spec("torch"):
        try:
            import torch

            report(
                "OK" if torch.cuda.is_available() else "WARN",
                "CUDA",
                str(torch.version.cuda),
            )
        except Exception as exc:
            report("WARN", "CUDA", f"torch unavailable ({type(exc).__name__})")
    else:
        report("WARN", "CUDA", "torch not installed; not required by MediaPipe")
    vlm = vlm_status()
    cloud_ok = args.offline
    if not args.offline:
        try:
            with httpx.Client(
                timeout=5, follow_redirects=False, trust_env=False
            ) as client:
                response = client.get(cloud_health_url())
            cloud_ok = response.is_success and response.json().get("status") == "ok"
            report(
                "OK" if cloud_ok else "OFF",
                "Cloud API /health/live",
                f"HTTP {response.status_code}",
            )
        except Exception as exc:
            report("OFF", "Cloud API", f"unreachable ({type(exc).__name__})")
        try:
            capabilities = (["motion_pose"] if pose_ok else []) + (
                ["food_vision"] if vlm["available"] else []
            )
            with CloudAPI(capabilities) as api:
                api.heartbeat(
                    metadata={"doctor": True, "python": sys.version.split()[0]}
                )
            report("OK", "Worker auth / heartbeat")
        except (CloudAPIError, ValueError) as exc:
            cloud_ok = False
            report("OFF", "Worker auth / heartbeat", str(exc))
    report("OK" if vlm["available"] else "OFF", "food_vision", vlm["reason"])
    if vlm["models"]:
        report("OK", "VLM /models", ", ".join(vlm["models"]))
    smoke_ok = not args.vlm_smoke or vlm["available"]
    if args.vlm_smoke and vlm["available"]:
        try:
            ok = smoke_test()
            smoke_ok = ok
            report("OK" if ok else "OFF", "VLM image smoke")
        except Exception as exc:
            smoke_ok = False
            report("OFF", "VLM image smoke", type(exc).__name__)
    return 0 if cloud_ok and smoke_ok and (pose_ok or vlm["available"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
