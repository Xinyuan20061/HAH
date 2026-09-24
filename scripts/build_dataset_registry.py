"""Build the local dataset registry without copying external datasets.

Directory hashes are deterministic inventory hashes over relative path and byte
size. Individual reference files and model weights use content SHA-256.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path


DATA_ROOT = Path(r"D:\HealthMateData")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "benchmark" / "dataset_registry.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_inventory(path: Path) -> dict[str, int | str]:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    digest = hashlib.sha256()
    total_bytes = 0
    for item in files:
        size = item.stat().st_size
        total_bytes += size
        relative = item.relative_to(path).as_posix()
        digest.update(f"{relative}\t{size}\n".encode("utf-8"))
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "inventory_sha256": digest.hexdigest(),
        "hash_scope": "sorted relative path + byte size; not a content digest",
    }


def directory_entry(
    dataset_id: str,
    path: Path,
    purpose: str,
    license_note: str,
) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "path": str(path),
        "kind": "directory",
        **directory_inventory(path),
        "license": {
            "status": "review_required_before_redistribution",
            "note": license_note,
        },
        "purpose": purpose,
        "included_in_delivery": False,
    }


def file_entry(
    dataset_id: str,
    path: Path,
    purpose: str,
    license_note: str,
) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "path": str(path),
        "kind": "file",
        "total_bytes": path.stat().st_size,
        "content_sha256": file_sha256(path),
        "hash_scope": "full file content",
        "license": {
            "status": "review_required_before_redistribution",
            "note": license_note,
        },
        "purpose": purpose,
        "included_in_delivery": False,
    }


def main() -> None:
    motion = DATA_ROOT / "motion"
    food = DATA_ROOT / "food"
    checkpoint = next((motion / "pretrained").glob("slowfast_*.pth"))
    entries = [
        directory_entry(
            "rehab24-6-videos",
            motion / "raw" / "rehab24-6",
            "六类动作固定测试集与后续微调候选数据",
            "本地副本未包含许可正文；提交或展示原视频前须按来源页面复核。",
        ),
        file_entry(
            "rehab24-6-segmentation",
            motion / "REHAB24-6_Segmentation.csv",
            "动作区间真值与受试者级划分",
            "与 REHAB24-6 视频使用相同许可，发布前复核。",
        ),
        directory_entry(
            "rehab24-6-2d-joints",
            motion / "raw" / "rehab24-6-2d",
            "骨骼序列训练/验证候选输入",
            "派生数据仍受原数据条款约束，发布前复核。",
        ),
        directory_entry(
            "squat-quality-images",
            motion / "raw" / "squat",
            "深蹲质量规则的离线参考，不进入六类分类准确率",
            "本地副本未包含许可正文；未随交付包分发。",
        ),
        file_entry(
            "slowfast-r50-kinetics400-pretrained",
            checkpoint,
            "六类视频模型的预训练初始化候选，禁止当作已微调模型",
            "第三方预训练权重；使用和再分发前复核上游 MMAction2 条款。",
        ),
        directory_entry(
            "nutrition5k-local-evaluation-subset",
            food,
            "42 张 RGB 样本及营养真值的识餐基线评估",
            "本地副本未包含许可正文；评估脚本只读，数据不进入交付包。",
        ),
    ]
    payload = {
        "schema_version": 1,
        "generated_on": date.today().isoformat(),
        "data_root": str(DATA_ROOT),
        "delivery_policy": "D:\\HealthMateData is external and excluded from packages",
        "entries": entries,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(entries)} entries)")


if __name__ == "__main__":
    main()

