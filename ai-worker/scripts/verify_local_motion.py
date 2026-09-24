"""Real HTTP -> queue -> local Worker -> MediaPipe smoke, without a VLM.

Uses Google's public pose sample repeated into a static two-second video.
This checks decoding/inference/contracts; it does not measure exercise accuracy.
Run with a Python 3.12 worker environment in an ASCII path. All test data is temporary.
"""

from pathlib import Path
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import httpx
from healthmate_worker.downloader import download_media
from healthmate_worker.processors.motion import analyze_motion


def verify_new_actions(root: Path) -> None:
    annotations = root / "benchmark" / "motion_annotations.jsonl"
    rows = [
        json.loads(line)
        for line in annotations.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for exercise_type in ("leg_abduction", "arm_abduction"):
        sample = next(row for row in rows if row["exercise_type"] == exercise_type)
        result = analyze_motion(
            Path(sample["video_path"]),
            exercise_type,
            start_seconds=sample["start_seconds"],
            end_seconds=sample["end_seconds"],
        )
        pose = result["pose"]
        assert pose["available"] and pose["exercise_type"] == exercise_type
        assert pose["sample_count"] > 0
        print(
            f"[OK] real REHAB24-6 {exercise_type}; "
            f"samples={pose['sample_count']} reps={pose['reps']}",
            flush=True,
        )


def stop_server(server):
    if os.name == "nt":
        # Windows venv launchers may spawn another Python process; stop the owned tree.
        subprocess.run(
            ["taskkill", "/PID", str(server.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    else:
        server.terminate()
    server.wait(timeout=10)


def main():
    root = Path(__file__).resolve().parents[2]
    verify_new_actions(root)
    backend = root / "backend"
    backend_python = backend / ".venv/Scripts/python.exe"
    base = "http://127.0.0.1:18082"
    picture = download_media(
        "https://storage.googleapis.com/mediapipe-assets/pose.jpg", "pose.jpg"
    )
    server = None
    try:
        with tempfile.TemporaryDirectory(prefix="healthmate-real-motion-") as directory:
            directory = Path(directory)
            video = directory / "static-pose.mp4"
            image = cv2.imread(str(picture))
            assert image is not None
            height, width = image.shape[:2]
            writer = cv2.VideoWriter(
                str(video), cv2.VideoWriter_fourcc(*"mp4v"), 24, (width, height)
            )
            assert writer.isOpened()
            try:
                for _ in range(48):
                    writer.write(image)
            finally:
                writer.release()
            env = {
                **os.environ,
                "PYTHONUTF8": "1",
                "ENV": "test",
                "PORT": "18082",
                "DATABASE_URL": "sqlite:///" + (directory / "api.db").as_posix(),
                "STORAGE_BACKEND": "local",
                "UPLOAD_DIR": str(directory / "uploads"),
                "PUBLIC_BASE_URL": base,
                "SECRET_KEY": secrets.token_hex(32),
                "CREDENTIALS_ENCRYPTION_KEY": secrets.token_hex(32),
                "WORKER_TOKEN": secrets.token_hex(32),
                "RUN_MIGRATIONS_ON_START": "false",
            }
            subprocess.run(
                [str(backend_python), "-m", "alembic", "upgrade", "head"],
                cwd=backend,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            server = subprocess.Popen(
                [
                    str(backend_python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18082",
                ],
                cwd=backend,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                with httpx.Client(trust_env=False, timeout=10) as client:
                    for _ in range(40):
                        try:
                            if client.get(base + "/health/ready").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise AssertionError("Local API startup failed")
                    token = client.post(
                        base + "/api/v1/auth/dev-login",
                        json={"nickname": "real-motion-audit"},
                    ).json()["access_token"]
                    client.headers["Authorization"] = "Bearer " + token
                    with video.open("rb") as file:
                        response = client.post(
                            base + "/api/v1/media/upload",
                            files={"file": (video.name, file, "video/mp4")},
                        )
                    response.raise_for_status()
                    asset = response.json()
                    response = client.post(
                        base + "/api/v1/media/motion-jobs",
                        json={"media_id": asset["media_id"], "exercise_type": "squat"},
                    )
                    response.raise_for_status()
                    job_id = response.json()["job_id"]
                    worker_env = {
                        **env,
                        "API_BASE_URL": base + "/api/v1",
                        "ALLOW_PRIVATE_MEDIA_HOSTS": "true",
                        "LOCAL_VLM_MODEL": "",
                        "CAPABILITIES": "motion_pose",
                    }
                    result = subprocess.run(
                        [sys.executable, "worker.py", "--once"],
                        cwd=root / "ai-worker",
                        env=worker_env,
                        capture_output=True,
                        text=True,
                        encoding="utf8",
                        timeout=90,
                    )
                    assert result.returncode == 0, (
                        "Real worker failed (inspect runtime separately)"
                    )
                    job = client.get(
                        base + f"/api/v1/media/motion-jobs/{job_id}"
                    ).json()
                    assert job["status"] == "done"
                    pose = job["result"]["pose"]
                    assert (
                        pose["available"]
                        and pose["sample_count"] > 0
                        and pose["reps"] == 0
                    )
                    assert all(
                        "url" not in frame
                        or frame["url"].startswith("data:image/jpeg;base64,")
                        for frame in job["result"]["frames"]
                    )
                    print(
                        f"[OK] real HTTP upload -> job={job_id} -> real worker -> done; samples={pose['sample_count']} valid_rate={pose['keypoint_valid_rate']} reps=0",
                        flush=True,
                    )
            finally:
                stop_server(server)
                server = None
    finally:
        picture.unlink(missing_ok=True)
        if server is not None:
            stop_server(server)


if __name__ == "__main__":
    main()
