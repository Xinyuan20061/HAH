from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import httpx
from healthmate_worker.client import CloudAPI, CloudAPIError
from healthmate_worker.config import settings
from healthmate_worker.downloader import download_media
from healthmate_worker.processors import analyze_food, analyze_motion
from healthmate_worker.capabilities import effective_capabilities, vlm_status
from healthmate_worker.models.semantic_runtime import semantic_model_status
from healthmate_worker.errors import ProcessingError
from healthmate_worker.security import UnsafeDownloadURL

logger = logging.getLogger("healthmate.worker")


def self_check() -> int:
    """Print a machine-readable engine/model/heartbeat summary."""
    if not settings.worker_token or settings.worker_token.startswith("replace-"):
        print(json.dumps({"ok": False, "error": "WORKER_TOKEN 未配置"}, ensure_ascii=False))
        return 2
    caps = effective_capabilities()
    vlm = vlm_status()
    summary = {
        "ok": False,
        "ai_mode": settings.ai_mode,
        "capabilities": caps,
        "models": vlm.get("models", []),
        "vision_provider": vlm.get("provider"),
        "semantic_model": semantic_model_status(),
        "heartbeat_2xx": False,
    }
    try:
        with CloudAPI(caps) as api:
            response = api.heartbeat(
                gpu_name(),
                {"self_check": True, "ai_mode": settings.ai_mode},
            )
        summary["heartbeat_2xx"] = bool(response.get("ok"))
        summary["ok"] = bool(caps and summary["heartbeat_2xx"])
    except (CloudAPIError, ValueError) as exc:
        summary["error"] = str(exc)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


def gpu_name():
    try:
        output = (
            subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            )
            .strip()
            .splitlines()
        )
        return output[0] if output else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def vlm_available() -> bool:
    return vlm_status()["available"]


def run_job(api: CloudAPI, job: dict) -> bool:
    job_id, lease = int(job["job_id"]), job["lease_token"]
    source = job.get("source") or {}
    started = time.perf_counter()
    local_path: Path | None = None
    stop = threading.Event()
    lost = threading.Event()
    state = {"progress": 1, "stage": "download"}
    lock = threading.Lock()

    def progress(value, stage):
        if lost.is_set():
            raise ProcessingError("lease_lost", "任务租约已失效", False)
        with lock:
            state.update(
                progress=max(state["progress"], min(99, int(value))), stage=stage
            )

    def renew():
        interval = max(1.0, min(15.0, float(job.get("lease_seconds", 180)) / 3))
        while not stop.is_set():
            with lock:
                value, stage = state["progress"], state["stage"]
            try:
                api.progress(job_id, lease, value, stage)
            except CloudAPIError as exc:
                logger.warning("job_id=%s stage=lease status=%s", job_id, exc.status)
                if exc.status in {401, 403, 404, 409}:
                    lost.set()
                    return
            except Exception as exc:
                logger.warning(
                    "job_id=%s stage=lease error_type=%s", job_id, type(exc).__name__
                )
            if stop.wait(interval):
                return

    renewal = threading.Thread(target=renew, name="lease-renewal", daemon=True)
    renewal.start()
    try:
        progress(5, "download")
        local_path = download_media(
            source.get("url") or "", source.get("original_name") or "media.bin"
        )
        progress(20, "inference")
        if job["job_type"] == "motion_pose":
            result = analyze_motion(
                local_path,
                (job.get("payload") or {}).get("exercise_type", "squat"),
                progress=progress,
            )
        elif job["job_type"] == "food_vision":
            result = analyze_food(local_path, progress=progress)
        elif job["job_type"] == "kinetics400":
            from healthmate_worker.processors.kinetics import analyze_kinetics400

            result = analyze_kinetics400(local_path, progress=progress)
        else:
            raise ProcessingError("unsupported_job", "不支持的任务类型")
        progress(95, "upload_result")
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        api.complete(job_id, lease, result, {"latency_ms": elapsed})
        logger.info(
            "job_id=%s stage=complete type=%s latency_ms=%s",
            job_id,
            job["job_type"],
            elapsed,
        )
        return True
    except KeyboardInterrupt:
        # Leave the persistent job under lease; expiry recovers it if shutdown interrupts a POST.
        logger.info("job_id=%s stage=interrupted; lease will recover", job_id)
        raise
    except Exception as exc:
        stage = state["stage"]
        if isinstance(exc, ProcessingError):
            code, message, retryable = exc.code, str(exc), exc.retryable
        elif isinstance(exc, UnsafeDownloadURL):
            code, message, retryable = (
                "unsafe_media_url",
                "媒体 URL 安全校验失败",
                False,
            )
        elif isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            code = (
                "media_url_expired"
                if stage == "download" and status in {401, 403, 404, 410}
                else "media_http"
            )
            message, retryable = (
                f"媒体下载 HTTP {status}",
                status in {408, 429} or status >= 500,
            )
        elif isinstance(exc, CloudAPIError):
            code, message, retryable = "cloud_api_error", str(exc), exc.retryable
        else:
            code = (
                "invalid_media"
                if isinstance(exc, (ValueError, FileNotFoundError))
                else "worker_error"
            )
            message, retryable = (
                f"处理失败 ({type(exc).__name__})",
                code != "invalid_media",
            )
        logger.error(
            "job_id=%s stage=%s code=%s error_type=%s",
            job_id,
            stage,
            code,
            type(exc).__name__,
        )
        if not lost.is_set():
            try:
                api.fail(job_id, lease, code, message, retryable)
            except Exception as report_exc:
                logger.error(
                    "job_id=%s stage=report_fail error_type=%s; lease will recover",
                    job_id,
                    type(report_exc).__name__,
                )
        return False
    finally:
        stop.set()
        renewal.join(timeout=settings.request_timeout_seconds + 1)
        if local_path:
            local_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="HealthMate local AI worker")
    parser.add_argument(
        "--once", action="store_true", help="claim at most one job then exit"
    )
    parser.add_argument(
        "--self-check", action="store_true", help="print capabilities, models and heartbeat status"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.self_check:
        return self_check()
    if not settings.worker_token or settings.worker_token.startswith("replace-"):
        logger.error("WORKER_TOKEN is required; configure ai-worker/.env")
        return 2
    caps = effective_capabilities()
    if not caps:
        logger.error("No usable capability; run python doctor.py")
        return 2
    gpu = gpu_name()
    stop = threading.Event()
    fatal = threading.Event()
    with CloudAPI(caps) as api:
        logger.info(
            "Worker=%s GPU=%s capabilities=%s",
            settings.worker_id,
            gpu or "CPU",
            ",".join(caps),
        )
        try:
            api.heartbeat(
                gpu,
                {
                    "pid": os.getpid(),
                    "python": sys.version.split()[0],
                    "vlm_provider": settings.effective_vlm_provider,
                    "semantic_model": semantic_model_status(),
                },
            )
        except CloudAPIError as exc:
            logger.error("startup heartbeat status=%s", exc.status)
            if exc.status in {401, 403} or args.once:
                return 3

        def heartbeat_loop():
            while not stop.wait(settings.heartbeat_interval_seconds):
                try:
                    api.heartbeat(
                        gpu,
                        {
                            "pid": os.getpid(),
                            "vlm_provider": settings.effective_vlm_provider,
                        },
                    )
                except CloudAPIError as exc:
                    logger.warning("stage=heartbeat status=%s", exc.status)
                    if exc.status in {401, 403}:
                        fatal.set()
                        return

        heartbeat = threading.Thread(
            target=heartbeat_loop, name="heartbeat", daemon=True
        )
        heartbeat.start()
        try:
            while not fatal.is_set():
                try:
                    job = api.claim()
                except CloudAPIError as exc:
                    logger.warning("stage=claim status=%s", exc.status)
                    if exc.status in {401, 403} or args.once:
                        return 3
                    stop.wait(min(10.0, settings.poll_interval_seconds * 2))
                    continue
                if job:
                    logger.info(
                        "job_id=%s stage=claimed type=%s",
                        job["job_id"],
                        job["job_type"],
                    )
                    succeeded = run_job(api, job)
                    if args.once:
                        return 0 if succeeded else 4
                elif args.once:
                    logger.info("No queued job")
                    return 0
                else:
                    stop.wait(settings.poll_interval_seconds)
            return 3
        except KeyboardInterrupt:
            logger.info("Worker stopped")
            return 0
        finally:
            stop.set()
            heartbeat.join(timeout=settings.request_timeout_seconds + 1)


if __name__ == "__main__":
    raise SystemExit(main())
