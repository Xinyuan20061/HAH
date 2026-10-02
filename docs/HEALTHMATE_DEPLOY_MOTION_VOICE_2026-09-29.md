HISTORICAL: 本文是特定轮次的交付/验证快照，其中引用的迁移 head 是当时的事实，不是当前 head。当前 head 以 `alembic heads` 命令结果为准。

# HealthMate 动作点评与语音闭环部署手册

> 版本：2026-09-29；状态：**工程已实现并本地复验，待云端部署与外部连通验证**。本手册描述统一动作链上线时需要执行的
> 部署与升级步骤；新接口、新 provider、新表均已落地并通过本地测试，云端部署、生产 MySQL 三路径真实执行、
> 腾讯云 ASR/TTS 各一次真实连通仍待按本文执行（见第 6 节与验收报告）。
> 基础云托管流程沿用 [WECHAT_CLOUD_RUN_DEPLOY.md](WECHAT_CLOUD_RUN_DEPLOY.md)，本文只列增量。

## 1. 本次新增内容（相对已验证基线）

| 项 | 落点 | 说明 |
| --- | --- | --- |
| 数据库迁移 0024 | `backend/migrations/versions/0024_motion_voice_harness.py` | 五新表 + `user_ai_configs` 两新列，可回滚；baseline=0023 |
| 统一动作任务接口 | `backend/app/api/v1/media.py`（已实现：7 个 `/media/motion-analyses*`，冒烟已确认路由注册） | `/media/motion-analyses` 等（规格第 5 节） |
| 腾讯云语音 provider | `backend/app/harness/voice.py`（已实现：VoiceProvider 协议 + TencentCloudVoiceProvider + VoiceGateway） | `tencent_cloud` provider，SDK 锁 `tencentcloud-sdk-python==3.1.183` |
| 本地 Worker 新能力 | `ai-worker/healthmate_worker/processors/motion_unified.py`（已实现） | `motion_unified_v1`：一次解码复用姿态/Kinetics/时间轴 |

## 2. 迁移 0024：执行与回滚

0024 创建的对象：

- 表 `motion_analysis_runs`（统一任务主表，含 `dedupe_key` 唯一、user/media/status 索引）
- 表 `motion_analysis_feedback`（每 run 一条结构化结果；图片放短期存储，不放长 JSON）
- 表 `provider_invocations`（脱敏调用账本；不存原始音视频/图片/完整 prompt/密钥）
- 表 `provider_connection_checks`（配置指纹 + 方向唯一约束，支撑"连通后停测"）
- 表 `voice_usage_daily`（按用户/日/provider 的 ASR 次数、TTS 字符、失败数）
- `user_ai_configs` 新增列 `voice_provider`（默认 `off`）、`voice_preferences_json`

### 2.1 升级（生产）

沿用云托管"独立迁移、Web 不自动迁移"的方式（`RUN_MIGRATIONS_ON_START=false`）：

1. 先备份生产 MySQL（0024 新建表，不破坏旧数据，但仍按流程备份）。
2. 在能连生产库的受控环境注入生产变量，在 `backend/` 执行：

```powershell
python scripts/preflight.py
python -m alembic upgrade head
python -m alembic current
```

或用已构建镜像：

```powershell
docker run --rm --env-file .\production.env healthmate-api python scripts/preflight.py
docker run --rm --env-file .\production.env healthmate-api alembic upgrade head
docker run --rm --env-file .\production.env healthmate-api alembic current
```

3. 确认 `alembic current` 为 `0024_motion_voice_harness (head)`；不得用 `stamp` 掩盖缺表。
4. 迁移需验证三条路径（本地已复验通过：SQLite fresh / incremental（0023→0024）/ repeat，
   以及 downgrade 0023 再 upgrade head；MySQL 方言离线 DDL 编译通过）。
   MySQL 生产目标的真实三路径仍待云端执行；真实库先备份、灰度读兼容再切写入。
5. 就绪探针 `GET /health/ready` 在迁移到 head 后才返回 200。

### 2.2 回滚

```powershell
python -m alembic downgrade 0023_user_voice_config
```

注意：回滚会删除 0024 新建的五表与两列；仅在功能开关关闭、无线上数据依赖时执行。

## 3. 云托管服务 healthmate-api 升级

- 构建目录仍为 `backend/`，Dockerfile 不变；镜像不包含 `.env`、SQLite、uploads、模型权重。
- 发布顺序：先按第 2 节把数据库升到 head，再发布新 Web 镜像；先单实例观察 ready=200，再扩容。
- `RUN_MIGRATIONS_ON_START` 保持 `false`；扩容多实例时禁止打开（无分布式迁移锁）。
- 新环境变量（腾讯云/语音）从云托管控制台受控配置注入，禁止提交真实密钥到仓库。

## 4. 本地 AI Worker 部署

沿用既有 watchdog 脚本 `ai-worker/start_worker.ps1`：

```powershell
cd ai-worker
.\start_worker.ps1
```

要点（已在脚本内实现）：

- 自动使用 `.venv`；若 venv 路径含中文（MediaPipe 原生加载限制），脚本自动建立
  ASCII junction（`%LOCALAPPDATA%\HealthMate\venvlink`）绕过。
- Worker 退出后 5 秒自动重启。

本次增量（已实现，待云端/真机环境验证）：

- Worker 需声明 `motion_unified_v1` 能力；未声明时服务端不派发该任务。
- 并发/资源由 Worker 侧信号量控制，避免 CPU 同时跑多个 SlowFast 任务。
- Worker 配置 `API_BASE_URL` 与 `WORKER_TOKEN`（独立随机值），不持有数据库密码、
  不持有腾讯云/DeepSeek 系统密钥。
- Kinetics 权重不在线时 Worker 返回 `kinetics: unavailable` 并继续六类；姿态与 Kinetics
  都不可用时明确失败，不输出伪结果。

## 5. 环境变量清单

### 5.1 既有生产变量（见 WECHAT_CLOUD_RUN_DEPLOY.md，不重复）

`ENV / DATABASE_URL / STORAGE_BACKEND / SECRET_KEY / CREDENTIALS_ENCRYPTION_KEY / WORKER_TOKEN /
WECHAT_APP_ID / WECHAT_APP_SECRET / CLOUDBASE_ENV_ID / CLOUDRUN_SERVICE_NAME / PUBLIC_BASE_URL /
CLOUD_HEADER_LOGIN_ENABLED / RUN_MIGRATIONS_ON_START / CORS_ORIGINS / WORKER_LEASE_SECONDS /
WORKER_MAX_ATTEMPTS / WORKER_OFFLINE_AFTER_SECONDS / DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL /
DEEPSEEK_MODEL / LOG_LEVEL`

### 5.2 本次新增（腾讯云语音，待配置）

```env
VOICE_PROVIDER=tencent_cloud
TENCENT_SECRET_ID=<待配置：云托管控制台密钥管理，禁止入库>
TENCENT_SECRET_KEY=<待配置：同上>
TENCENT_REGION=ap-shanghai
TENCENT_ASR_ENGINE=16k_zh
TENCENT_TTS_VOICE_TYPE=<待配置：控制台核对后的基础/精品音色 ID>
VOICE_MAX_AUDIO_BYTES=2500000
VOICE_MONTHLY_ASR_BUDGET=100
VOICE_MONTHLY_TTS_CHARS_BUDGET=30000
VOICE_LIVE_VERIFY_ENABLED=false
```

### 5.3 本次新增（DeepSeek 视觉，待配置/待锁定）

```env
DEEPSEEK_VISION_MODEL=<待配置：发布时锁定实际型号与回包契约，如 deepseek-flash>
```

> 填写位置与待办详见 [HEALTHMATE_PENDING_CONFIG_ITEMS_2026-09-29.md](HEALTHMATE_PENDING_CONFIG_ITEMS_2026-09-29.md)。

## 6. 上线后人工验收（与发布门禁对齐）

- [ ] 一次点击仅创建一个统一任务；重复点击/断网恢复复用同一任务。
- [ ] 视频只解码一次；六类与 400 候选进入同一结果。
- [ ] 结果页无"100 分=准确率"表述；候选分值进折叠详情并标注。
- [ ] 无评价器动作 `score.available=false`。
- [ ] ASR → Harness → TTS 闭环可走通；长回复分段播报。
- [ ] 腾讯云密钥只在后端；设置页只显示状态与"上次验证时间"。
- [ ] 越权访问他人分析/语音返回 404。
- 完整可勾选项见 [HEALTHMATE_ACCEPTANCE_CHECKLIST_2026-09-29.md](HEALTHMATE_ACCEPTANCE_CHECKLIST_2026-09-29.md)。
