from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.services.rag.service import RETRIEVER_VERSION, search_knowledge


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/search")
def knowledge_search(
    query: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=3, ge=1, le=5),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return {
        "items": search_knowledge(db, query, limit),
        "retrieval": RETRIEVER_VERSION,
        "disclaimer": "资料用于一般健康教育，不能替代个体化医疗诊断或治疗。",
    }
