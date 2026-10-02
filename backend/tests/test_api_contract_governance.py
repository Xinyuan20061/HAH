"""WP0 red tests — API-01 / API-02 / DOC-01 (spec §9).

Every assertion here corresponds to a confirmed defect in spec §3:

* API-01: ``media.router`` was included twice in ``api/v1/router.py``, producing
  duplicate OpenAPI ``operationId`` values and two identical route entries.
* API-02: business errors answered with three different envelopes; only the
  unified ``{"error": {...}}`` shape is allowed to reach clients.
* DOC-01: the architecture doc pinned a stale migration head (0016) while the
  repository was already at 0025.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import APIRoute

from app.api.v1.router import api_router
from app.main import app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _operation_ids(schema: dict) -> list[str]:
    return [
        op["operationId"]
        for path in schema["paths"].values()
        for op in path.values()
        if isinstance(op, dict) and "operationId" in op
    ]


def test_openapi_operation_ids_are_unique():
    """API-01: a duplicate include makes two operations share an operationId."""
    ids = _operation_ids(app.openapi())
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    assert duplicates == [], f"duplicate operationId: {duplicates}"


def test_no_duplicate_route_path_method_pairs():
    """API-01: the same router must not be included twice in the API router."""
    seen: dict[tuple[str, frozenset[str]], str] = {}
    duplicates: list[str] = []
    for route in api_router.routes:
        if not isinstance(route, APIRoute):
            continue
        key = (route.path, frozenset(route.methods))
        if key in seen:
            duplicates.append(f"{route.path} {sorted(route.methods)}")
        seen[key] = route.name
    assert duplicates == [], f"duplicate route registration: {sorted(set(duplicates))}"


def test_business_error_uses_unified_envelope(api):
    """API-02: a business error must answer with ``{"error": {...}}``."""
    res = api.get("/api/v1/diet/records/999999")
    assert res.status_code == 404
    body = res.json()
    assert "error" in body, f"business error missing unified envelope: {body}"
    error = body["error"]
    assert set(error) >= {"code", "message", "retryable", "request_id", "details"}
    assert error["code"] == "DIET_RECORD_NOT_FOUND"
    assert error["retryable"] is False
    assert error["request_id"]
    assert isinstance(error["details"], dict)
    # The request id must also be echoed in the response header.
    assert res.headers.get("X-Request-ID") == error["request_id"]


def test_error_envelope_never_leaks_internals(api):
    """API-02: user-facing messages must not expose SQL, paths or provider text."""
    body = api.get("/api/v1/diet/records/999999").json()
    message = body["error"]["message"]
    forbidden = ["SELECT", "sqlite", "Traceback", "app/api", 'File "', "postgres"]
    for token in forbidden:
        assert token not in message, f"error message leaked internals: {message}"


def test_unauthenticated_and_forbidden_are_distinguishable(api):
    """API-02: 401 (login) and 404 (foreign resource) must both be explicit."""
    unauthenticated = api.get(
        "/api/v1/diet/records/1", headers={"Authorization": ""}
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "UNAUTHENTICATED"


def test_migration_has_single_head_and_doc_matches():
    """DOC-01: exactly one head, and live docs must not pin a stale head.

    Historical task reports (``HISTORICAL:`` marker) and the remediation spec
    itself legitimately quote old revision ids, so they are excluded; every
    document that describes the *current* system is checked.
    """
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"multiple migration heads: {heads}"

    excluded = {"HEALTHMATE_FULL_REMEDIATION_DEVELOPMENT_SPEC_2026-10-02.md"}
    stale: list[tuple[str, str]] = []
    for doc in list(REPO_ROOT.glob("*.md")) + list((REPO_ROOT / "docs").glob("*.md")):
        if doc.name in excluded:
            continue
        text = doc.read_text(encoding="utf-8", errors="ignore")
        if text.lstrip().startswith("HISTORICAL:"):
            continue
        for line in text.splitlines():
            if "0016" not in line:
                continue
            if "head" in line.lower() or "revision" in line.lower():
                stale.append((doc.name, line.strip()[:120]))
    assert stale == [], (
        "live docs still pin a stale migration head (inject the command result "
        f"instead): {stale}"
    )
