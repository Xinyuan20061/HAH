from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.models import HealthProfile
from app.schemas.user import ProfileIn, ProfileOut

router = APIRouter(prefix="/users", tags=["users"])


class MeUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=30)
    avatar_url: str | None = Field(default=None, max_length=500)

    @field_validator("nickname")
    @classmethod
    def _nickname(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("昵称不能为空")
        if len(value) > 30:
            raise ValueError("昵称不能超过 30 字")
        return value

    @field_validator("avatar_url")
    @classmethod
    def _avatar(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            return value
        if not (
            value.startswith("cloud://")
            or value.startswith("http://")
            or value.startswith("https://")
            or value.startswith("/uploads/")
        ):
            raise ValueError("头像地址格式不受支持")
        return value


@router.get("/me")
def me(user=Depends(current_user)):
    return {"id": user.id, "nickname": user.nickname, "avatar_url": user.avatar_url}


@router.put("/me")
def update_me(
    body: MeUpdate, user=Depends(current_user), db: Session = Depends(get_db)
):
    data = body.model_dump(exclude_none=True)
    if "nickname" in data:
        user.nickname = data["nickname"]
    if "avatar_url" in data:
        user.avatar_url = data["avatar_url"]
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "nickname": user.nickname, "avatar_url": user.avatar_url}


@router.get("/me/health-profile")
def get_profile(user=Depends(current_user)):
    return ProfileOut.model_validate(user.profile) if user.profile else None


@router.put("/me/health-profile")
def save_profile(
    body: ProfileIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    p = user.profile or HealthProfile(user_id=user.id)
    [setattr(p, k, v) for k, v in body.model_dump().items()]
    db.add(p)
    db.commit()
    db.refresh(p)
    return ProfileOut.model_validate(p)
