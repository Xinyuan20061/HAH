from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class WorkerSettings(BaseSettings):
    api_base_url: str = "http://127.0.0.1:8000/api/v1"
    worker_token: str = ""
    worker_id: str = "healthmate-local-worker"
    worker_name: str = "HealthMate Local Worker"
    poll_interval_seconds: float = Field(default=2.0, ge=0.1)
    heartbeat_interval_seconds: float = Field(default=15.0, ge=1)
    request_timeout_seconds: float = Field(default=20.0, ge=1)
    max_download_mb: int = Field(default=200, ge=1, le=1000)
    max_redirects: int = Field(default=5, ge=0, le=10)
    api_max_retries: int = Field(default=3, ge=0, le=8)
    log_level: str = "INFO"
    # Declares motion_unified_v2 out of the box; motion_pose/food_vision/kinetics400
    # remain for backward compatibility. Operators can override via the CAPABILITIES
    # env var; a worker without pose_ok still won't advertise motion_unified_v2 at
    # runtime (see capabilities.effective_capabilities).
    capabilities: str = "motion_pose,food_vision,kinetics400,motion_unified_v2"
    local_vlm_base_url: str = "http://127.0.0.1:1234/v1"
    local_vlm_model: str = ""
    local_vlm_api_key: str = ""
    local_vlm_timeout_seconds: float = Field(default=120.0, ge=1, le=1800)
    local_vlm_json_mode: bool = False
    vlm_provider: str = "local"
    deepseek_vision_model: str = "deepseek-flash"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    ai_mode: str = Field(default="local_first", pattern="^(local_first|cloud_first|off)$")
    local_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    image_max_dimension: int = Field(default=1280, ge=128, le=4096)
    image_max_bytes: int = Field(default=1024 * 1024, ge=10000, le=4 * 1024 * 1024)
    food_classifier_model: str = ""
    food_classifier_top_k: int = Field(default=3, ge=1, le=5)
    allow_private_media_hosts: bool = False
    semantic_model_path: str = ""
    semantic_model_sha256: str = ""
    semantic_model_min_confidence: float = Field(default=0.45, ge=0, le=1)
    # Local SlowFast Kinetics-400 recognizer (400 classes, runs on CPU).
    # When the checkpoint path is empty/missing the feature is auto-disabled.
    kinetics400_checkpoint: str = ""
    kinetics400_min_confidence: float = Field(default=0.5, ge=0, le=1)
    kinetics400_strong_confidence: float = Field(default=0.7, ge=0, le=1)
    kinetics400_device: str = "cpu"
    # Model governance gate: Kinetics-400 remains a candidate layer until the
    # registered benchmark gates it as "active". When False (default), motion
    # auto mode records the 400-class candidates but never overrides the rule
    # result; set True only after a passing same-set evaluation (see plan §3.3).
    kinetics400_override_enabled: bool = False
    # --- Motion-unified V2 evidence pipeline (spec §5.1 / contract §5) ---------
    # These used to be hard-coded inside motion_unified.py. They are deliberately
    # SEPARATE knobs: pose sampling FPS, generic candidate FPS, on-screen preview
    # count and per-frame byte budget are different parameters and must NOT share
    # a single MAX_PREVIEWS limit.
    motion_pose_sample_fps: float = Field(default=7.0, ge=2.0, le=15.0)
    motion_candidate_fps: float = Field(default=2.0, ge=0.5, le=6.0)
    motion_preview_long_edge: int = Field(default=720, ge=320, le=1280)
    motion_max_duration_seconds: float = Field(default=60.0, ge=5.0, le=120.0)
    motion_display_preview_count: int = Field(default=8, ge=1, le=16)
    motion_preview_max_bytes: int = Field(default=100 * 1024, ge=20 * 1024, le=300 * 1024)
    motion_evidence_pool_max_frames: int = Field(default=64, ge=8, le=200)
    # Preview byte upload to the backend signed-URL store. When on (default), the
    # worker PUTs rendered JPEG previews to the backend after analysis; upload
    # failures degrade to a warning (local staging kept) and never block the
    # receipt. Local dev points at the same api_base_url (本机后端).
    preview_upload_enabled: bool = True
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    @property
    def capability_list(self):
        return [x.strip() for x in self.capabilities.split(",") if x.strip()]

    @property
    def effective_vlm_provider(self) -> str:
        if self.ai_mode == "off":
            return "local"
        if self.ai_mode == "cloud_first":
            return "deepseek"
        if self.vlm_provider.strip().lower() == "deepseek":
            return "deepseek"
        if self.local_vlm_model.strip():
            return "local"
        return "deepseek" if self.deepseek_api_key.strip() else "local"


@lru_cache
def get_settings():
    return WorkerSettings()


settings = get_settings()
