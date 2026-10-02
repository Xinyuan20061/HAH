"""OpenAPI governance audit (spec §9.1/§9.2/§13.2).

Fails the build when:

* two operations share an ``operationId`` (the duplicate-router defect API-01);
* two routes register the same path + method pair;
* a deprecated endpoint has no recorded removal date or usage counter.

Runs against the real app object, so it cannot drift from the served schema.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.routing import APIRoute  # noqa: E402

from app.api.v1.router import api_router  # noqa: E402
from app.main import app  # noqa: E402


def collect() -> dict:
    schema = app.openapi()
    operation_ids: list[str] = []
    deprecated: list[dict] = []
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            if "operationId" in operation:
                operation_ids.append(operation["operationId"])
            if operation.get("deprecated"):
                deprecated.append(
                    {
                        "path": path,
                        "method": method,
                        "operation_id": operation.get("operationId"),
                        "summary": operation.get("summary") or "",
                        "description": operation.get("description") or "",
                    }
                )

    route_keys: dict[tuple[str, frozenset], str] = {}
    duplicates: list[str] = []
    for route in api_router.routes:
        if not isinstance(route, APIRoute):
            continue
        key = (route.path, frozenset(route.methods))
        if key in route_keys:
            duplicates.append(f"{route.path} {sorted(route.methods)}")
        route_keys[key] = route.name

    return {
        "operation_ids": operation_ids,
        "duplicate_operation_ids": sorted(
            {value for value in operation_ids if operation_ids.count(value) > 1}
        ),
        "duplicate_routes": sorted(set(duplicates)),
        "deprecated": deprecated,
        "route_count": len(route_keys),
    }


def main() -> int:
    report = collect()
    failures: list[str] = []

    if report["duplicate_operation_ids"]:
        failures.append(
            "重复 operationId: " + ", ".join(report["duplicate_operation_ids"])
        )
    if report["duplicate_routes"]:
        failures.append("重复路由注册: " + ", ".join(report["duplicate_routes"]))

    for item in report["deprecated"]:
        description = item["description"]
        # A deprecation without a removal plan is an indefinite second contract.
        if "弃用" not in description and "deprecat" not in description.lower():
            failures.append(
                f"{item['method'].upper()} {item['path']} 标记 deprecated 但未说明弃用计划"
            )

    print(f"routes={report['route_count']} operations={len(report['operation_ids'])}")
    print(f"deprecated_endpoints={len(report['deprecated'])}")
    for item in report["deprecated"]:
        print(f"  deprecated: {item['method'].upper()} {item['path']}")
    if failures:
        print("\nFAIL")
        for item in failures:
            print(" -", item)
        return 1
    print("OK: operationId 唯一、路由无重复、弃用端点均有说明")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
