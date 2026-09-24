from datetime import datetime, timedelta, timezone
import base64, hashlib, hmac, json
from app.core.config import settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()
        ),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(
        settings.secret_key.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64(sig)}"


def decode_subject(token: str):
    try:
        h, p, s = token.split(".")
        if json.loads(_unb64(h)).get("alg") != "HS256":
            return None
        expected = _b64(
            hmac.new(
                settings.secret_key.encode(), f"{h}.{p}".encode(), hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(expected, s):
            return None
        payload = json.loads(_unb64(p))
        if int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload.get("sub")
    except Exception:
        return None
