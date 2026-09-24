from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import decode_subject
from app.models import User

bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    if not credentials:
        raise HTTPException(401, "Missing bearer token")
    sub = decode_subject(credentials.credentials)
    if not sub:
        raise HTTPException(401, "Invalid token")
    try:
        user_id = int(sub)
    except (ValueError, TypeError):
        raise HTTPException(401, "Invalid token subject")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "User not found")
    return user
