"""AI fallback chain end-to-end verification (DeepSeek -> local Qwen -> rules).

Runs the full HTTP stack (FastAPI TestClient) against an isolated SQLite
database migrated to head and proves the three fallback rungs:

  Scenario A (general intent, cloud down, local healthy):
      POST /agent/respond "每天睡几个小时比较好" -> provider == "local"
      (the Qwen2.5-0.5B ONNX engine takes over, user still gets an answer)
  Scenario B (plan intent, cloud down):
      POST /agent/respond "帮我制定本周的训练计划"
      -> provider == "rules-fallback" and a valid plan is returned
  Scenario C (cloud AND local both down, general intent):
      -> provider == "rules-fallback", still a non-empty reply (never a hard 503)

Requires LOCAL_LLM_MODEL_DIR configured (backend/.env) and the real Qwen ONNX
weights for Scenario A. Usage:

  backend/.venv/Scripts/python.exe backend/scripts/verify_ai_fallback.py

Exit code 0 when all three scenarios pass.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["ENV"] = "test"

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import build_engine, get_db  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.services.agent import orchestrator  # noqa: E402


class _BrokenProvider:
    """Simulates an unreachable DeepSeek cloud (always 503)."""

    provider_name = "deepseek-broken"

    async def chat(self, system: str, message: str):
        raise HTTPException(503, "DeepSeek 服务暂时不可用，请稍后重试。")

    async def stream(self, system: str, message: str):
        raise HTTPException(503, "DeepSeek 服务暂时不可用。")
        yield  # pragma: no cover


def _new_user(db: Session) -> int:
    user = User(openid="verify-fallback-" + os.urandom(8).hex())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def main() -> int:
    if not settings.local_llm_model_dir.strip():
        print("BLOCKED: LOCAL_LLM_MODEL_DIR is empty; configure backend/.env first.")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="ai_fallback_verify_"))
    url = "sqlite:///" + (workdir / "api.db").as_posix()

    previous_url = settings.database_url
    settings.database_url = url
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    command.upgrade(config, "head")
    settings.database_url = previous_url
    engine = build_engine(url)

    def dependency():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = dependency

    # Patch the names the orchestrator actually imported.
    original_get_provider = orchestrator.get_provider
    original_get_local_provider = orchestrator.get_local_provider
    orchestrator.get_provider = lambda user=None: _BrokenProvider()

    failures: list[str] = []

    try:
        with Session(engine) as db:
            user_id = _new_user(db)

        with TestClient(app) as client:
            client.headers["Authorization"] = "Bearer " + create_access_token(
                str(user_id)
            )

            # ---- Scenario A: general intent, cloud down, local takes over ----
            resp_a = client.post(
                "/api/v1/agent/respond",
                json={"message": "每天睡几个小时比较好？"},
            )
            if resp_a.status_code != 200:
                failures.append(f"A: status {resp_a.status_code} {resp_a.text[:200]}")
            else:
                body_a = resp_a.json()
                print("A provider =", body_a.get("provider"))
                print("A reply    =", str(body_a.get("reply"))[:160])
                if body_a.get("provider") != "local":
                    failures.append(
                        f"A: expected provider 'local', got {body_a.get('provider')}"
                    )
                if not str(body_a.get("reply", "")).strip():
                    failures.append("A: empty reply")

            # ---- Scenario B: plan intent, cloud down -> rules plan ----
            resp_b = client.post(
                "/api/v1/agent/respond",
                json={"message": "帮我制定本周的训练计划"},
            )
            if resp_b.status_code != 200:
                failures.append(f"B: status {resp_b.status_code} {resp_b.text[:200]}")
            else:
                body_b = resp_b.json()
                print("B provider =", body_b.get("provider"))
                plan = body_b.get("plan")
                if body_b.get("provider") != "rules-fallback":
                    failures.append(
                        "B: expected 'rules-fallback', got "
                        + str(body_b.get("provider"))
                    )
                if not plan or not plan.get("items"):
                    failures.append("B: no fallback plan items")
                else:
                    print(f"B plan     = {plan.get('title')} ({len(plan['items'])} items)")

            # ---- Scenario C: cloud + local both down -> rules reply, no 503 ----
            # Force the local rung to be unavailable too.
            async def _no_local():
                return None

            orchestrator.get_local_provider = _no_local
            resp_c = client.post(
                "/api/v1/agent/respond",
                json={"message": "每天喝多少水合适？"},
            )
            if resp_c.status_code != 200:
                failures.append(f"C: status {resp_c.status_code} {resp_c.text[:200]}")
            else:
                body_c = resp_c.json()
                print("C provider =", body_c.get("provider"))
                if body_c.get("provider") != "rules-fallback":
                    failures.append(
                        "C: expected 'rules-fallback', got "
                        + str(body_c.get("provider"))
                    )
                if not str(body_c.get("reply", "")).strip():
                    failures.append("C: empty reply")
    finally:
        orchestrator.get_provider = original_get_provider
        orchestrator.get_local_provider = original_get_local_provider
        app.dependency_overrides.clear()
        engine.dispose()

    if failures:
        print("\nFAILURES:")
        for item in failures:
            print(" -", item)
        return 1
    print("\nALL FALLBACK SCENARIOS PASSED (A=local, B=rules-plan, C=rules-reply).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
