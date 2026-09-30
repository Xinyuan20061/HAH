from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    app_name: str = "HealthMate API"
    env: str = "development"
    api_v1_prefix: str = "/api/v1"
    secret_key: str = "change-me"
    credentials_encryption_key: str = ""
    access_token_expire_minutes: int = 10080

    # Cloud API / database
    database_url: str = ""
    cors_origins: str = "*"
    port: int = Field(default=8000, ge=1, le=65535)
    run_migrations_on_start: bool = False
    public_base_url: str = "http://127.0.0.1:8000"

    # LLM
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_vision_model: str = "deepseek-flash"
    food_route_default: str = "cloud"
    food_cloud_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    food_image_max_bytes: int = Field(default=4 * 1024 * 1024, ge=10000, le=20 * 1024 * 1024)

    # RAG semantic embedding (bge-small-zh ONNX, external model directory)
    embedding_model_dir: str = ""

    # Local fallback LLM (Qwen2.5-0.5B q4f16 ONNX, external model directory)
    local_llm_model_dir: str = ""
    local_llm_max_new_tokens: int = Field(default=200, ge=16, le=512)
    local_llm_timeout_seconds: float = Field(default=45.0, ge=5, le=180)

    # Optional OpenAI-compatible speech gateway used by voice-capable agents.
    # Text chat remains available when this is not configured.
    voice_api_key: str = ""
    voice_api_base_url: str = ""
    voice_stt_model: str = "whisper-1"
    voice_tts_model: str = "tts-1"
    voice_tts_voice: str = "alloy"
    voice_max_audio_bytes: int = Field(default=8 * 1024 * 1024, ge=10000, le=20 * 1024 * 1024)

    # --- Tencent Cloud voice (spec section 7) -------------------------------------
    # provider: "tencent_cloud" | "openai_compatible" | "off"
    # Secrets are read ONLY from backend environment variables; the miniprogram /
    # worker never holds them and they are never written to user_ai_configs.
    voice_provider: str = "off"
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    tencent_region: str = "ap-shanghai"
    # SentenceRecognition engine service type, e.g. "16k_zh".
    tencent_asr_engine: str = "16k_zh"
    # TextToVoice VoiceType (console-verified base/premium voice id), e.g. 101001.
    tencent_tts_voice_type: int = 101001
    # Local hard limits / budgets (spec 7.1). These are configuration, not a
    # permanent price quote and not the Tencent console quota.
    voice_max_audio_seconds: int = Field(default=60, ge=5, le=60)
    voice_tts_segment_chars: int = Field(default=120, ge=20, le=200)
    voice_monthly_asr_budget: int = Field(default=100, ge=1)
    voice_monthly_tts_chars_budget: int = Field(default=30000, ge=100)
    # External live connectivity verification is opt-in and only runs manually via
    # POST /harness/voice/verify-once once per config fingerprint (spec 7.3).
    voice_live_verify_enabled: bool = False

    # WeChat / CloudBase
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    # Only enable direct identity-header login when the service has NO public ingress.
    # v1.0 competition deployment keeps this false because the local GPU worker needs HTTPS ingress.
    cloud_header_login_enabled: bool = False
    cloudbase_env_id: str = ""
    cloudrun_service_name: str = "healthmate-api"

    # Media
    # local: development only; cloud_ref: mini program uploads to wx.cloud storage and
    # sends fileID + temporary https URL to this API.
    storage_backend: str = "local"
    upload_dir: str = "uploads"
    max_upload_mb: int = 200
    cloud_media_url_ttl_seconds: int = 7200

    # Legacy S3 adapter is retained for teams that later migrate to COS/MinIO.
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "healthmate-media"
    s3_region: str = "us-east-1"
    s3_public_base_url: str = ""

    # Local GPU AI worker bridge. The worker polls the public HTTPS endpoint.
    worker_token: str = ""
    worker_lease_seconds: int = Field(default=180, ge=10)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    worker_offline_after_seconds: int = Field(default=45, ge=10)
    motion_preview_retention_days: int = Field(default=7, ge=1, le=30)

    request_id_header: str = "X-Request-ID"
    diagnostics_timeout_seconds: float = 2.5
    log_level: str = "INFO"
    retain_sensitive_audit_excerpt: bool = False
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = Field(default=30, ge=5, le=10000)
    rate_limit_ai_per_minute: int = Field(default=20, ge=1, le=10000)
    rate_limit_media_per_minute: int = Field(default=30, ge=1, le=10000)
    rate_limit_worker_per_minute: int = Field(default=300, ge=10, le=100000)

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    @property
    def is_production(self):
        return self.env.lower() == "production"

    @property
    def cors_origin_list(self):
        return (
            ["*"]
            if self.cors_origins.strip() == "*"
            else [x.strip() for x in self.cors_origins.split(",") if x.strip()]
        )

    @property
    def use_local_storage(self):
        return self.storage_backend.lower() == "local"

    @property
    def worker_enabled(self):
        return bool(self.worker_token.strip())

    @property
    def tencent_voice_configured(self) -> bool:
        """True only when both backend env credentials are present (spec 7.1)."""
        return bool(self.tencent_secret_id.strip() and self.tencent_secret_key.strip())

    @property
    def active_voice_provider(self) -> str:
        """System-level default provider. Per-user choice may override it."""
        provider = self.voice_provider.strip().lower()
        if provider not in {"tencent_cloud", "openai_compatible", "off"}:
            return "off"
        return provider

    @property
    def effective_database_url(self) -> str:
        if self.database_url.strip():
            return self.database_url
        if self.env.lower() in {"development", "test"}:
            return "sqlite:///./healthmate.db"
        # No connection is made during import. Startup/preflight reports the missing field.
        return "mysql+pymysql://invalid-configuration/healthmate"

    def configuration_errors(self) -> list[str]:
        errors = []
        if self.env.lower() not in {"development", "test", "production"}:
            errors.append("ENV: 使用 development、test 或 production。")
        if self.storage_backend.lower() not in {"local", "cloud_ref", "s3"}:
            errors.append("STORAGE_BACKEND: 使用 local、cloud_ref 或 s3。")
        if self.food_route_default.lower() not in {"cloud", "worker"}:
            errors.append("FOOD_ROUTE_DEFAULT: 使用 cloud 或 worker。")
        if bool(self.voice_api_key.strip()) != bool(self.voice_api_base_url.strip()):
            errors.append("VOICE_API_KEY 与 VOICE_API_BASE_URL 必须同时配置或同时留空。")
        if self.is_production and self.voice_api_base_url.strip() and not self.voice_api_base_url.startswith("https://"):
            errors.append("VOICE_API_BASE_URL: production 必须使用 HTTPS。")
        try:
            dialect = make_url(self.effective_database_url).get_backend_name()
            driver = make_url(self.effective_database_url).get_driver_name()
        except Exception:
            dialect = "invalid"
            driver = "invalid"
            errors.append("DATABASE_URL: 请填写合法 SQLAlchemy 数据库连接 URL。")
        if self.is_production:
            if (
                not self.database_url.strip()
                or dialect != "mysql"
                or driver != "pymysql"
            ):
                errors.append(
                    "DATABASE_URL: production 必须显式配置 mysql+pymysql://...?...charset=utf8mb4，禁止 SQLite。"
                )
            if self.storage_backend.lower() != "cloud_ref":
                errors.append(
                    "STORAGE_BACKEND: production 必须设置 cloud_ref，媒体上传到 CloudBase。"
                )
            for field, minimum in [
                ("secret_key", 32),
                ("credentials_encryption_key", 32),
                ("worker_token", 48),
            ]:
                value = getattr(self, field)
                if len(value) < minimum or any(
                    x in value.lower()
                    for x in [
                        "change-me",
                        "replace-with",
                        "your-",
                        "dev-secret",
                        "dev-credential",
                    ]
                ):
                    errors.append(
                        f"{field.upper()}: 配置独立随机密钥，至少 {minimum} 字符。"
                    )
            if self.credentials_encryption_key == self.secret_key:
                errors.append("CREDENTIALS_ENCRYPTION_KEY: 必须与 SECRET_KEY 不同。")
            for field in [
                "wechat_app_id",
                "wechat_app_secret",
                "cloudbase_env_id",
                "cloudrun_service_name",
            ]:
                if not getattr(self, field).strip():
                    errors.append(f"{field.upper()}: 填写微信云环境的真实配置。")
            if not self.public_base_url.startswith("https://"):
                errors.append("PUBLIC_BASE_URL: 填写云托管公网 HTTPS 域名。")
            if self.cloud_header_login_enabled:
                errors.append(
                    "CLOUD_HEADER_LOGIN_ENABLED: 公网 Worker 架构必须为 false，用户使用 wx.login。"
                )
        return errors

    def validate_configuration(self) -> None:
        errors = self.configuration_errors()
        if errors:
            raise ValueError("配置预检失败：\n" + "\n".join(errors))

    def safe_summary(self) -> dict:
        return {
            "env": self.env,
            "db_dialect": make_url(self.effective_database_url).get_backend_name(),
            "storage_backend": self.storage_backend,
            "port": self.port,
            "worker_enabled": self.worker_enabled,
            "voice_enabled": bool(self.voice_api_key.strip() and self.voice_api_base_url.strip()),
            "run_migrations_on_start": self.run_migrations_on_start,
        }


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
