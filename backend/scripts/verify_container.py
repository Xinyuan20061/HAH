"""Local Docker smoke test. Uses only the explicitly created audit MySQL container.

Requires image healthmate-api and healthmate-audit-mysql listening on host port 13307.
The test briefly stops that audit database, restarts it in finally, and removes its API container.
These credentials and WeChat fields are local test fixtures, not a real cloud deployment.
"""

import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

import httpx

API_CONTAINER = "healthmate-audit-api"
MYSQL_CONTAINER = "healthmate-audit-mysql"
BASE = "http://127.0.0.1:18080"


def docker(*args):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, encoding="utf8"
    )
    if result.returncode:
        raise RuntimeError(
            f"Docker operation failed: {args[0]} (inspect local Docker logs)"
        )
    return result.stdout.strip()


def request(method, path, **kwargs):
    with httpx.Client(trust_env=False) as client:
        return client.request(method, BASE + path, **kwargs)


def health(expected_ready, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            live = request("GET", "/health/live", timeout=3)
            ready = request("GET", "/health/ready", timeout=15)
            if live.status_code == 200 and ready.status_code == expected_ready:
                print(
                    f"[OK] live=200 ready={expected_ready} backend={ready.json()['database_backend']}",
                    flush=True,
                )
                return ready.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise AssertionError("Health endpoints did not reach expected state")


def main():
    docker("inspect", MYSQL_CONTAINER)
    existing = docker(
        "ps",
        "-a",
        "--filter",
        "name=^/" + API_CONTAINER + "$",
        "--format",
        "{{.Names}}",
    )
    if existing:
        raise ValueError(
            "Audit API container already exists; inspect it before retrying"
        )
    values = {
        "ENV": "production",
        "DATABASE_URL": "mysql+pymysql://root:audit-local-only@host.docker.internal:13307/healthmate?charset=utf8mb4",
        "STORAGE_BACKEND": "cloud_ref",
        "PORT": "8091",
        "RUN_MIGRATIONS_ON_START": "false",
        "SECRET_KEY": secrets.token_hex(32),
        "CREDENTIALS_ENCRYPTION_KEY": secrets.token_hex(32),
        "WORKER_TOKEN": secrets.token_hex(32),
        "WECHAT_APP_ID": "wx-audit-fixture",
        "WECHAT_APP_SECRET": "audit-fixture-not-real",
        "CLOUDBASE_ENV_ID": "audit-fixture",
        "CLOUDRUN_SERVICE_NAME": "healthmate-api",
        "PUBLIC_BASE_URL": "https://audit.example",
    }
    started = False
    try:
        with tempfile.TemporaryDirectory(prefix="healthmate-container-") as directory:
            env_file = Path(directory) / "audit.env"
            env_file.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()),
                encoding="utf8",
            )
            docker(
                "run",
                "--rm",
                "--env-file",
                str(env_file),
                "healthmate-api",
                "alembic",
                "upgrade",
                "head",
            )
            print("[OK] independent migration command", flush=True)
            docker(
                "run",
                "-d",
                "--name",
                API_CONTAINER,
                "--env-file",
                str(env_file),
                "-p",
                "127.0.0.1:18080:8091",
                "healthmate-api",
            )
            started = True
            health(200)
            assert (
                request(
                    "POST",
                    "/api/v1/worker/heartbeat",
                    json={"worker_id": "audit-worker", "capabilities": []},
                ).status_code
                == 401
            )
            response = request(
                "POST",
                "/api/v1/worker/heartbeat",
                headers={"X-Worker-Token": values["WORKER_TOKEN"]},
                json={"worker_id": "audit-worker", "capabilities": []},
            )
            assert response.status_code == 200
            print("[OK] Worker token: missing=401 valid=200", flush=True)
            worker_python = (
                Path.home()
                / ".codex/runtimes/healthmate-worker-py312/Scripts/python.exe"
            )
            if worker_python.exists():
                worker_env = {
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "API_BASE_URL": BASE + "/api/v1",
                    "WORKER_TOKEN": values["WORKER_TOKEN"],
                    "ALLOW_PRIVATE_MEDIA_HOSTS": "true",
                    "LOCAL_VLM_MODEL": "",
                    "WORKER_ID": "audit-real-worker",
                }
                worker_dir = Path(__file__).resolve().parents[2] / "ai-worker"
                for arguments in [["doctor.py"], ["worker.py", "--once"]]:
                    result = subprocess.run(
                        [str(worker_python), *arguments],
                        cwd=worker_dir,
                        env=worker_env,
                        capture_output=True,
                        text=True,
                        encoding="utf8",
                        timeout=60,
                    )
                    if result.returncode:
                        raise AssertionError(f"Worker smoke failed: {arguments[0]}")
                    print(result.stdout, flush=True)
                    print(f"[OK] real runtime {arguments}", flush=True)
            docker("stop", "-t", "5", MYSQL_CONTAINER)
            health(503)
            docker("start", MYSQL_CONTAINER)
            health(200)
            docker("restart", API_CONTAINER)
            health(200)
            logs = docker("logs", API_CONTAINER)
            assert (
                "Running upgrade" not in logs
                and "Explicit RUN_MIGRATIONS_ON_START" not in logs
            )
            # Docker sends uvicorn logs to stderr; inspect both streams without printing secrets.
            full_logs = (
                subprocess.run(
                    ["docker", "logs", API_CONTAINER], capture_output=True, text=True
                ).stderr
                + logs
            )
            assert "Running upgrade" not in full_logs
            assert "0.0.0.0:8091" in full_logs
            assert not any(
                value in full_logs
                for key, value in values.items()
                if key in {"SECRET_KEY", "CREDENTIALS_ENCRYPTION_KEY", "WORKER_TOKEN"}
            )
            print(
                "[OK] injected PORT=8091; restart executes no DDL; startup logs redact keys",
                flush=True,
            )
    finally:
        docker("start", MYSQL_CONTAINER)
        if started:
            docker("rm", "-f", API_CONTAINER)


if __name__ == "__main__":
    main()
