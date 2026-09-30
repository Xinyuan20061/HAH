# -*- coding: utf-8 -*-
"""Motion V2 preview/evidence storage (contract §8 "证据存储", §7 DDL).

Ownership (work package B,复验 1/3 字节链路):
  * Signed-URL upload + read: the backend mints short-lived HMAC-signed URLs that
    bind user_id + run_id + asset_prefix + exp; the worker PUTs preview bytes to
    the upload URL and the user GETs the read URL. The worker can never choose an
    arbitrary external URL.
  * The receipt carries only ``preview_asset_id + hash + timestamp + dimensions``
    (no image bytes); this service owns the actual bytes on private storage.
  * Preview access is private to the owning user: signature validation enforces
    the bound user_id; another user's signature is rejected (403/400).

Object-storage deployment note (this round ships the disk LocalPreviewStore):
    The method signatures here are the contract F/D code against; cloud object
    storage (S3/CloudBase) swaps :class:`PreviewStore` for a signed-PUT
    implementation without changing mint/validate/save/read callers. Upload URLs
    then point at the object-store presigned PUT instead of the internal path.

The ``motion_evidence_frames`` table is created by migration 0025 (package E).
This module encodes the contract DDL in :class:`SqlEvidenceFrameStore` but never
imports/edits ``models.py``. Tests inject :class:`InMemoryEvidenceFrameStore`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import quote, urlencode, urlparse, parse_qs

DEFAULT_PREVIEW_TTL_SECONDS = 6 * 60 * 60  # short-lived: 6h
DEFAULT_UPLOAD_URL_TTL_SECONDS = 15 * 60
PREVIEW_BYTES_BUDGET = 100 * 1024  # per frame, matches worker contract

# Signing secret config key: ``settings.preview_signing_secret``. Local dev uses a
# fixed default; production MUST override it (never ship the dev secret).
DEV_DEFAULT_SECRET = "motion-preview-dev-secret"

# These are FULL API paths (include the /api/v1 prefix). The worker PUTs to
# ``origin + upload_url`` where origin is parsed from API_BASE_URL (path-less),
# so the real routes are: PUT  /api/v1/media/previews/{asset_id} and
# GET  /api/v1/media/motion-analyses/{run_id}/previews/{frame_id}.
_UPLOAD_PATH = "/api/v1/media/previews"
_READ_PATH = "/api/v1/media/motion-analyses"


def _to_dt(ts: float) -> "datetime":
    """Convert a Unix epoch float to a timezone-aware UTC datetime (DB DDL)."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


class PreviewNotFound(LookupError):
    pass


class PreviewExpired(LookupError):
    pass


class PreviewForbidden(PermissionError):
    pass


class InvalidPreviewSignature(Exception):
    """Raised when a signed preview URL fails validation.

    F maps this to 403 (bad sig / wrong user) or 400 (missing params / expired).
    """


@dataclass
class PreviewRecord:
    asset_id: str
    user_id: int
    run_id: int
    frame_id: str = ""
    timestamp_ms: int = 0
    sha256: str = ""
    width: int = 0
    height: int = 0
    nbytes: int = 0
    created_at: float = 0.0
    expires_at: float = 0.0


class PreviewStore(Protocol):
    """Private byte storage back-end (local filesystem / later object storage)."""

    def save(self, data: bytes, rec: PreviewRecord) -> None: ...

    def read(self, rec: PreviewRecord) -> bytes: ...

    def delete(self, rec: PreviewRecord) -> None: ...


class LocalPreviewStore:
    """Development/test filesystem store under ``<root>/<user_id>/<run_id>/``."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, rec: PreviewRecord) -> Path:
        # Isolation is by run_id: ownership of a run is enforced upstream
        # (_owned_run / signed user_id), and motion_evidence_frames has no
        # user_id column, so the byte store must not depend on one.
        return self.root / str(rec.run_id) / f"{rec.asset_id}.jpg"

    def save(self, data: bytes, rec: PreviewRecord) -> None:
        path = self._path(rec)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, rec: PreviewRecord) -> bytes:
        path = self._path(rec)
        if not path.is_file():
            raise PreviewNotFound(rec.asset_id)
        return path.read_bytes()

    def delete(self, rec: PreviewRecord) -> None:
        path = self._path(rec)
        if path.is_file():
            path.unlink()


class EvidenceFrameStore(Protocol):
    """Rows of ``motion_evidence_frames`` (contract DDL, (run_id, frame_id) unique)."""

    def upsert(self, row: dict) -> None: ...

    def list_for_run(self, run_id: int) -> list[dict]: ...

    def find_by_asset(self, asset_id: str) -> Optional[dict]: ...

    def find_frame(self, run_id: int, frame_id: str) -> Optional[dict]: ...

    def delete_expired(self, now: float) -> int: ...


class InMemoryEvidenceFrameStore:
    """Test double for the evidence-frame repository (no DB needed)."""

    def __init__(self):
        self._rows: dict[tuple[int, str], dict] = {}

    def upsert(self, row: dict) -> None:
        key = (int(row["run_id"]), str(row["frame_id"]))
        self._rows[key] = dict(row)

    def list_for_run(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self._rows.values() if int(r["run_id"]) == run_id]

    def all_rows(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]

    def find_by_asset(self, asset_id: str) -> Optional[dict]:
        for row in self._rows.values():
            if row.get("preview_asset_id") == asset_id:
                return dict(row)
        return None

    def find_frame(self, run_id: int, frame_id: str) -> Optional[dict]:
        row = self._rows.get((int(run_id), str(frame_id)))
        return dict(row) if row else None

    def delete_expired(self, now: float) -> int:
        stale = [
            key
            for key, row in self._rows.items()
            if float(row.get("expires_at", 0)) <= now
        ]
        for key in stale:
            del self._rows[key]
        return len(stale)


class SqlEvidenceFrameStore:
    """SQLAlchemy-Core read/write for motion_evidence_frames.

    The table is created by migration 0025; this class maps the contract DDL and
    does NOT touch models.py. It is wired in by the app when the migration has
    landed; tests use :class:`InMemoryEvidenceFrameStore` instead.
    """

    def __init__(self, db, table):
        self.db = db
        self.table = table

    @staticmethod
    def _norm(row) -> dict:
        """Normalize a SQLAlchemy Row to the dict contract (floats for time)."""
        if row is None:
            return None
        mapping = dict(row._mapping)
        for key in ("expires_at", "created_at"):
            val = mapping.get(key)
            if val is not None:
                mapping[key] = float(val.timestamp())
        return mapping

    def upsert(self, row: dict) -> None:
        from sqlalchemy import select

        existing = self.db.execute(
            select(self.table).where(
                self.table.c.run_id == row["run_id"],
                self.table.c.frame_id == row["frame_id"],
            )
        ).scalar_one_or_none()
        if existing is None:
            self.db.execute(self.table.insert().values(**row))
        else:
            self.db.execute(
                self.table.update()
                .where(
                    self.table.c.run_id == row["run_id"],
                    self.table.c.frame_id == row["frame_id"],
                )
                .values(**row)
            )

    def list_for_run(self, run_id: int) -> list[dict]:
        from sqlalchemy import select

        rows = self.db.execute(
            select(self.table).where(self.table.c.run_id == int(run_id))
        ).all()
        return [self._norm(r) for r in rows]

    def all_rows(self) -> list[dict]:
        from sqlalchemy import select

        rows = self.db.execute(select(self.table)).all()
        return [self._norm(r) for r in rows]

    def find_by_asset(self, asset_id: str) -> Optional[dict]:
        from sqlalchemy import select

        row = self.db.execute(
            select(self.table).where(self.table.c.preview_asset_id == str(asset_id))
        ).first()
        return self._norm(row)

    def find_frame(self, run_id: int, frame_id: str) -> Optional[dict]:
        from sqlalchemy import select

        row = self.db.execute(
            select(self.table).where(
                self.table.c.run_id == int(run_id),
                self.table.c.frame_id == str(frame_id),
            )
        ).first()
        return self._norm(row)

    def delete_expired(self, now: float) -> int:
        from sqlalchemy import delete

        result = self.db.execute(
            delete(self.table).where(self.table.c.expires_at <= _to_dt(now))
        )
        return int(result.rowcount or 0)


class MediaStorage:
    def __init__(
        self,
        store: PreviewStore,
        evidence: EvidenceFrameStore,
        *,
        secret: str = DEV_DEFAULT_SECRET,
        preview_ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
        upload_url_ttl_seconds: int = DEFAULT_UPLOAD_URL_TTL_SECONDS,
    ):
        self._store = store
        self._evidence = evidence
        self._secret = secret or DEV_DEFAULT_SECRET
        self._preview_ttl = int(preview_ttl_seconds)
        self._upload_ttl = int(upload_url_ttl_seconds)

    @staticmethod
    def _now() -> float:
        return time.time()

    # -- HMAC signing --------------------------------------------------------
    def _canonical(self, params: dict) -> str:
        """Canonical signing string: params sorted by name, k=v joined by &.

        The ``sig`` parameter is excluded. Values are percent-encoded the same way
        urlencode does, so the endpoint can recompute the exact string.
        """
        items = sorted(
            (str(k), "" if v is None else str(v))
            for k, v in params.items()
            if k != "sig"
        )
        return "&".join(f"{k}={quote(v, safe='')}" for k, v in items)

    def _sign(self, params: dict) -> str:
        return hmac.new(
            self._secret.encode(), self._canonical(params).encode(), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _query(params: dict) -> dict:
        return {k: str(v) for k, v in params.items()}

    # -- upload URL (worker PUTs bytes here) ---------------------------------
    def mint_upload_url(
        self,
        asset_id: str,
        *,
        user_id: int,
        run_id: int,
        asset_prefix: str,
        expiry_ts: int,
    ) -> str:
        """Mint a signed PUT upload URL binding user_id/run_id/asset_prefix/exp."""
        params = {
            "asset_id": str(asset_id),
            "user_id": int(user_id),
            "run_id": int(run_id),
            "asset_prefix": str(asset_prefix),
            "exp": int(expiry_ts),
        }
        query = urlencode({**self._query(params), "sig": self._sign(params)})
        return f"{_UPLOAD_PATH}/{quote(str(asset_id), safe='')}?{query}"

    def validate_upload_signature(self, params: dict) -> dict:
        """Validate a worker upload request; return bound {user_id, run_id, asset_id}."""
        sig = params.get("sig")
        if not sig:
            raise InvalidPreviewSignature("missing_sig")
        try:
            exp = int(params.get("exp", 0))
        except (TypeError, ValueError):
            raise InvalidPreviewSignature("bad_exp") from None
        if exp <= int(self._now()):
            raise InvalidPreviewSignature("expired")
        expected = self._sign(params)
        if not hmac.compare_digest(expected, str(sig)):
            raise InvalidPreviewSignature("bad_signature")
        try:
            return {
                "user_id": int(params["user_id"]),
                "run_id": int(params["run_id"]),
                "asset_id": str(params["asset_id"]),
            }
        except (KeyError, TypeError, ValueError):
            raise InvalidPreviewSignature("missing_binding") from None

    # -- read URL (user GETs here) -------------------------------------------
    def build_read_url(
        self, run_id: int, frame_id: str, *, user_id: int, expiry_ts: int
    ) -> str:
        """Mint a signed GET read URL for a frame; resolves asset_id server-side."""
        if self._evidence.find_frame(int(run_id), str(frame_id)) is None:
            raise PreviewNotFound(f"{run_id}/{frame_id}")
        params = {
            "run_id": int(run_id),
            "frame_id": str(frame_id),
            "user_id": int(user_id),
            "exp": int(expiry_ts),
        }
        query = urlencode({**self._query(params), "sig": self._sign(params)})
        return f"{_READ_PATH}/{int(run_id)}/previews/{quote(str(frame_id), safe='')}?{query}"

    def validate_read_signature(self, params: dict, *, run_id: int, frame_id: str) -> int:
        """Validate a user read request; return the bound user_id."""
        sig = params.get("sig")
        if not sig:
            raise InvalidPreviewSignature("missing_sig")
        try:
            exp = int(params.get("exp", 0))
        except (TypeError, ValueError):
            raise InvalidPreviewSignature("bad_exp") from None
        if exp <= int(self._now()):
            raise InvalidPreviewSignature("expired")
        if int(params.get("run_id", -1)) != int(run_id):
            raise InvalidPreviewSignature("run_id_mismatch")
        if str(params.get("frame_id", "")) != str(frame_id):
            raise InvalidPreviewSignature("frame_id_mismatch")
        expected = self._sign(params)
        if not hmac.compare_digest(expected, str(sig)):
            raise InvalidPreviewSignature("bad_signature")
        try:
            return int(params["user_id"])
        except (KeyError, TypeError, ValueError):
            raise InvalidPreviewSignature("missing_user") from None

    # -- bytes in/out --------------------------------------------------------
    def save_preview(
        self,
        asset_id: str,
        body_bytes: bytes,
        *,
        user_id: int,
        run_id: int,
        frame_id: Optional[str] = None,
        timestamp_ms: int = 0,
        expires_at: Optional[float] = None,
        subject_id: Optional[str] = None,
        observation: Optional[dict] = None,
    ) -> dict:
        """Write preview bytes to the private store and bind the evidence row.

        ``frame_id`` is optional: the F endpoint passes it so build_read_url can
        resolve asset_id later. Bytes are isolated under user_id/run_id on disk.
        """
        if len(body_bytes) > PREVIEW_BYTES_BUDGET:
            raise ValueError("preview_oversized")
        now = self._now()
        exp = float(expires_at) if expires_at is not None else now + self._preview_ttl
        rec = PreviewRecord(
            asset_id=str(asset_id),
            user_id=int(user_id),
            run_id=int(run_id),
            frame_id=str(frame_id or asset_id),
            timestamp_ms=int(timestamp_ms),
            sha256=hashlib.sha256(body_bytes).hexdigest(),
            nbytes=len(body_bytes),
            created_at=now,
            expires_at=exp,
        )
        self._store.save(body_bytes, rec)
        # NOTE: motion_evidence_frames has NO user_id column (ownership follows
        # run_id -> motion_analysis_runs.user_id). Writing user_id here would
        # fail on the real DDL; ownership is enforced upstream via _owned_run.
        # The DDL column is DateTime, so floats are converted here (the local
        # byte store keeps the float form for expiry comparison).
        self._evidence.upsert(
            {
                "run_id": rec.run_id,
                "frame_id": rec.frame_id,
                "timestamp_ms": rec.timestamp_ms,
                "preview_asset_id": rec.asset_id,
                "subject_id": subject_id,
                "observation_json": json.dumps(observation or {}, ensure_ascii=False),
                "expires_at": _to_dt(rec.expires_at),
                "created_at": _to_dt(now),
            }
        )
        return {
            "preview_asset_id": rec.asset_id,
            "sha256": rec.sha256,
            "bytes": rec.nbytes,
            "expires_at": rec.expires_at,
        }

    def read_preview_bytes(self, asset_id: str) -> bytes:
        """Return raw JPEG bytes; raise PreviewNotFound when absent."""
        row = self._evidence.find_by_asset(asset_id)
        if row is None:
            raise PreviewNotFound(asset_id)
        rec = PreviewRecord(
            asset_id=asset_id,
            user_id=int(row.get("user_id", 0) or 0),
            run_id=int(row["run_id"]),
            frame_id=str(row.get("frame_id", "")),
        )
        return self._store.read(rec)

    # -- lifecycle -----------------------------------------------------------
    def purge_expired(self, now: Optional[float] = None) -> int:
        """Delete expired preview bytes + evidence rows. Returns removed rows."""
        now = float(now if now is not None else self._now())
        expired = [
            r
            for r in self._evidence.all_rows()
            if float(r.get("expires_at", 0)) <= now
        ]
        for row in expired:
            rec = PreviewRecord(
                asset_id=row["preview_asset_id"],
                user_id=int(row.get("user_id", 0) or 0),
                run_id=int(row["run_id"]),
            )
            self._store.delete(rec)
        return self._evidence.delete_expired(now)
