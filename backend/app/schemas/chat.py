from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: int | None = None


class ChatOut(BaseModel):
    session_id: int
    reply: str
    provider: str
    safety_level: str = "normal"
