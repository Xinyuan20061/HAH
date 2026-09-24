import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings


def _fernet() -> Fernet:
    if settings.is_production and (
        not settings.credentials_encryption_key
        or settings.credentials_encryption_key == settings.secret_key
    ):
        raise ValueError("CREDENTIALS_ENCRYPTION_KEY 必须独立配置")
    material = settings.credentials_encryption_key or settings.secret_key
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8") if value else ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise ValueError(
            "凭据无法解密，请恢复原 CREDENTIALS_ENCRYPTION_KEY 或重新配置用户 Key"
        )
