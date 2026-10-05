"""HTTP adapter for consented personal evidence acquisition."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.schemas.errors import ApiException
from app.services.policy_learning.acquisition.contracts import (
    AnswerRequest, NextQuestionRequest, ObservationRepairRequest, RepairRequest,
    RereviewRequest, SessionVersionRequest, StartAcquisitionRequest,
)
from app.services.policy_learning.acquisition.service import (
    AcquisitionError, answer_question, issue_next_question, pause_session,
    observation_repair_targets, propose_rereview, read_certificate, read_history, read_session, rereview_context,
    repair_observation, repair_session, resume_session, rereview_preview,
    start_session,
)

router = APIRouter(prefix="/policy", tags=["policy-acquisition"])


def _write(db: Session, fn):
    try:
        result = fn()
        db.commit()
        return result
    except AcquisitionError as exc:
        db.rollback()
        raise ApiException(exc.status, exc.code, exc.message) from None
    except Exception:
        db.rollback()
        raise


def _read(db: Session, fn):
    try:
        result = fn()
        db.commit()
        return result
    except AcquisitionError as exc:
        db.rollback()
        raise ApiException(exc.status, exc.code, exc.message) from None


@router.post("/episodes/{episode_id}/acquisition/sessions")
def start_acquisition(episode_id: str, body: StartAcquisitionRequest,
                      idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
                      user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: start_session(db, user_id=user.id, episode_id=episode_id,
                                            request=body, idempotency_key=idempotency_key))


@router.get("/acquisition/sessions/{session_id}")
def get_acquisition(session_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return _read(db, lambda: read_session(db, user_id=user.id, session_id=session_id))


@router.post("/acquisition/sessions/{session_id}/next")
def next_question(session_id: str, body: NextQuestionRequest,
                  idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
                  user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: issue_next_question(
        db, user_id=user.id, session_id=session_id,
        expected_session_version=body.expected_session_version,
        expected_episode_version=body.expected_episode_version,
        idempotency_key=idempotency_key))


@router.post("/acquisition/sessions/{session_id}/questions/{question_id}/answer")
def answer(session_id: str, question_id: str, body: AnswerRequest,
           idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
           user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: answer_question(
        db, user_id=user.id, session_id=session_id, question_id=question_id,
        request=body, idempotency_key=idempotency_key))


@router.post("/acquisition/sessions/{session_id}/pause")
def pause(session_id: str, body: SessionVersionRequest,
          idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
          user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: pause_session(
        db, user_id=user.id, session_id=session_id,
        expected_session_version=body.expected_session_version,
        idempotency_key=idempotency_key))


@router.post("/acquisition/sessions/{session_id}/resume")
def resume(session_id: str, body: NextQuestionRequest,
           idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
           user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: resume_session(
        db, user_id=user.id, session_id=session_id,
        expected_session_version=body.expected_session_version,
        expected_episode_version=body.expected_episode_version,
        idempotency_key=idempotency_key))


@router.post("/acquisition/sessions/{session_id}/repair")
def repair(session_id: str, body: RepairRequest,
           idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
           user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: repair_session(
        db, user_id=user.id, session_id=session_id, request=body,
        idempotency_key=idempotency_key))


@router.get("/acquisition/certificates/{certificate_id}")
def certificate(certificate_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return _read(db, lambda: read_certificate(db, user_id=user.id, certificate_id=certificate_id))


@router.get("/episodes/{episode_id}/acquisition/history")
def history(episode_id: str, cursor: int | None = Query(default=None, ge=1),
            limit: int = Query(default=20, ge=1, le=50),
            user=Depends(current_user), db: Session = Depends(get_db)):
    return _read(db, lambda: read_history(db, user_id=user.id, episode_id=episode_id,
                                          cursor=cursor, limit=limit))


@router.post("/episodes/{episode_id}/observation-repairs")
def observation_repair(episode_id: str, body: ObservationRepairRequest,
                       idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
                       user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: repair_observation(
        db, user_id=user.id, episode_id=episode_id, request=body,
        idempotency_key=idempotency_key))


@router.get("/episodes/{episode_id}/observation-repair-targets")
def repair_targets(episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return _read(db, lambda: observation_repair_targets(db, user_id=user.id, episode_id=episode_id))


@router.get("/episodes/{episode_id}/rereview-context")
def get_rereview_context(episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return _read(db, lambda: rereview_context(db, user_id=user.id, episode_id=episode_id))


@router.post("/episodes/{episode_id}/rereview-preview")
def preview_rereview(episode_id: str, body: RereviewRequest,
                     user=Depends(current_user), db: Session = Depends(get_db)):
    return _read(db, lambda: rereview_preview(db, user_id=user.id,
                                              episode_id=episode_id, request=body))


@router.post("/episodes/{episode_id}/rereview-proposal")
def rereview_proposal(episode_id: str, body: RereviewRequest,
                      idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
                      user=Depends(current_user), db: Session = Depends(get_db)):
    return _write(db, lambda: propose_rereview(db, user_id=user.id,
                                              episode_id=episode_id, request=body,
                                              idempotency_key=idempotency_key))
