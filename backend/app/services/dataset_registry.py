from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DatasetRegistry


def list_datasets(db: Session) -> dict:
    rows = db.scalars(select(DatasetRegistry).order_by(DatasetRegistry.id)).all()
    return {
        "items": [
            {
                "dataset_key": row.dataset_key,
                "name": row.name,
                "homepage_url": row.homepage_url,
                "paper_url": row.paper_url,
                "primary_use": row.primary_use,
                "modalities": json.loads(row.modalities_json or "[]"),
                "access_mode": row.access_mode,
                "license_summary": row.license_summary,
                "redistribution_allowed": row.redistribution_allowed,
                "adoption_status": row.adoption_status,
                "verified_on": row.verified_on,
                "notes": row.notes,
            }
            for row in rows
        ],
        "policy": "registry_metadata_only_no_automatic_download",
        "notice": "采用数据前必须复核官网许可、保存授权记录，并禁止向项目仓库提交受限原始数据。",
    }
