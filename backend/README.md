<p align="center">
  <a href="https://github.com/hauyer/health-assistant">← HealthMate 主仓库</a>
</p>

<h1 align="center">HealthMate Backend</h1>

<p align="center"><strong>HealthMate 健康助手 · 后端 FastAPI 服务（微信云托管部署）</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.1.0-2F81F7?style=flat-square" alt="Version 1.1.0">
  <img src="https://img.shields.io/badge/FastAPI-0.116-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI 0.116">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy 2.0">
  <img src="https://img.shields.io/badge/SQLite%2FMySQL-4479A1?style=flat-square&logo=mysql&logoColor=white" alt="SQLite / MySQL">
  <img src="https://img.shields.io/badge/微信云托管-07C160?style=flat-square" alt="微信云托管">
</p>

<p align="center">
  <a href="#核心功能">核心功能</a> ·
  <a href="#技术栈">技术栈</a> ·
  <a href="#目录结构">目录结构</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#环境变量">环境变量</a> ·
  <a href="#数据库迁移">数据库迁移</a> ·
  <a href="#api-模块">API 模块</a> ·
  <a href="#测试">测试</a> ·
  <a href="#微信云托管部署">部署</a>
</p>

HealthMate Backend 是微信小程序健康助手 HealthMate 的后端服务，基于 FastAPI 构建并部署在微信云托管。
提供用户档案、健康目标、饮食/运动记录、趋势洞察、周报、Agent 对话（DeepSeek + 本地兜底）、
视觉识餐、动作识别（本地 Worker 桥接）与人在回路评测等能力。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 认证与用户 | 微信登录、JWT、头像昵称同步、健康档案与 AI 配置 |
| 健康记录 | 饮食/运动/身体数据记录、打卡、能量收支仪表盘 |
| 智能对话 | DeepSeek 流式对话，display_tokens 分片展示，本地 Qwen 兜底 |
| 视觉识餐 | 云端直连识餐，失败自动回退 Worker 队列 |
| 动作识别 | 本地 Worker 主动轮询，动作/姿态分析（含 Kinetics-400） |
| Agent 编排 | 多专家 Agent、人在回路反馈、主动健康提醒、决策 ID 追踪 |
| RAG 知识 | bge-small-zh 语义检索，健康知识问答 |
| 安全与隐私 | 内容安全、敏感信息脱敏、隐私同意、速率限制、审计 |
| 媒体存储 | 本地 / CloudBase / COS 或 S3 兼容存储后端，URL 安全与签名 |
| 评测体系 | 内置评测、双人盲评、完整性与一致性校验 |

## 技术栈

- **框架**：FastAPI + Uvicorn + Pydantic v2
- **ORM / 迁移**：SQLAlchemy 2.0 + Alembic
- **数据库**：开发 SQLite，生产 MySQL（微信云托管持久化）
- **AI**：DeepSeek（文本）、本地 Qwen2.5 ONNX（兜底）、bge-small-zh（RAG 嵌入）
- **存储**：本地 / CloudBase 云文件 / COS 或 S3 兼容
- **部署**：Docker + entrypoint.sh，微信云托管

## 目录结构

```text
backend/
├── app/
│   ├── api/v1/         19 个路由模块（auth、users、records、agent …）
│   ├── core/           配置、安全、限流、流式、媒体/URL 安全
│   ├── models/         SQLAlchemy 数据模型
│   ├── schemas/        Pydantic 请求/响应模型
│   └── services/       健康、Agent、RAG、视觉、动作、存储等服务
├── migrations/         Alembic 迁移（23 个版本）
├── scripts/            预检、评测、迁移验证等工具脚本
├── tests/              单元与集成测试
├── Dockerfile
├── entrypoint.sh
├── requirements.txt
└── alembic.ini
```

## 快速开始

推荐 Python 3.12。本地开发使用 SQLite。

```powershell
git clone https://github.com/hauyer/healthmate-backend.git
cd healthmate-backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

编辑 `.env`，本地填写 `ENV=development`、`DATABASE_URL=sqlite:///./healthmate.db`、`STORAGE_BACKEND=local`。
生成 JWT 密钥与独立凭据加密密钥并填入对应字段（不要把输出提交到仓库）：

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
python scripts/preflight.py
```

执行迁移并启动：

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

查看 `http://127.0.0.1:8000/docs`、`/health/live`、`/health/ready`。

## 环境变量

关键配置见 `.env.example`：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 开发 `sqlite:///./healthmate.db`；生产 MySQL 连接串 |
| `SECRET_KEY` | JWT 签名密钥 |
| `CREDENTIALS_ENCRYPTION_KEY` | 第三方凭据加密密钥 |
| `DEEPSEEK_API_KEY` | DeepSeek 文本能力密钥 |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | 微信小程序登录凭据 |
| `DEPLOYMENT_PROFILE` | `local_dev` / `wechat_cloud` / `mobile_cloud` / `dual_client_cloud`；旧 production 未配置时按小程序档案兼容 |
| `MOBILE_AUTH_PROVIDER` / `MOBILE_AUTH_ENABLED` | 当前只支持 `wechat_open_platform`；正式移动登录、CloudBase 票据与账号关联需显式启用 |
| `MOBILE_WECHAT_APP_ID` / `MOBILE_WECHAT_APP_SECRET` | 微信开放平台移动应用登录凭据；与小程序凭据分开配置 |
| `CLOUDBASE_ENV_ID` | CloudBase 环境 ID；Android release 包也需通过 `VITE_CLOUDBASE_ENV_ID` 指定同一环境 |
| `CLOUDBASE_CUSTOM_LOGIN_CREDENTIALS_JSON` | CloudBase 自定义登录凭据 JSON（含 `env_id`、`private_key_id`、`private_key`）；仅存入云托管 Secret Manager，禁止放进 Android/Vite 环境变量或仓库 |
| `CLOUDBASE_STORAGE_SECRET_ID` / `CLOUDBASE_STORAGE_SECRET_KEY` | 受限 CAM 子账号的管理端凭据，用于服务端删除 CloudBase 媒体；生产环境必填，仅存入云托管 Secret Manager |
| `CLOUDBASE_STORAGE_SESSION_TOKEN` | 临时 CAM 凭据的 SessionToken；固定子账号密钥留空 |
| `STORAGE_BACKEND` | `local` / `cloud_ref` / `s3` |
| `MOBILE_UPLOAD_BACKEND` | Android 新上传路由：`local` / `cloud_ref` / `s3`；启用 Android 上传的生产档案必须显式设为 `s3`，可与小程序 `STORAGE_BACKEND=cloud_ref` 并行 |
| `MOBILE_UPLOAD_ENABLED` | Android 媒体上传接口开关；开发可用本地存储，production 启用时要求 `MOBILE_UPLOAD_BACKEND=s3` |
| `S3_ENDPOINT_URL`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_BUCKET`、`S3_REGION` | Android COS/S3 兼容直传；生产 `MOBILE_UPLOAD_BACKEND=s3` 时必填，COS 桶名必须是 `BucketName-APPID`，标准 COS 端点地域须与桶地域匹配；凭据仅存入云托管 Secret Manager |
| `WORKER_TOKEN` | 本地 GPU Worker 桥接令牌（与 ai-worker/.env 保持一致） |
| `RUN_MIGRATIONS_ON_START` | 是否启动时自动迁移（生产建议 `false`） |

## 数据库迁移

```powershell
python -m alembic upgrade head
python -m alembic current
```

生产副本上线前显式执行迁移（`RUN_MIGRATIONS_ON_START=false`）。

## API 模块

统一前缀 `/api/v1`，包含 19 个路由模块：

| 模块 | 说明 |
| --- | --- |
| auth / users | 微信登录、用户档案、头像昵称同步 |
| records / health | 饮食运动记录、趋势、能量仪表盘、打卡 |
| chat / agent | 流式对话、Agent 编排与决策追踪 |
| vision / media | 识餐、媒体上传与存储 |
| insights / timeline | 洞察、时间线、周报 |
| evaluation / safety / privacy | 评测、内容安全、隐私同意 |
| worker / resources / fitness / knowledge | Worker 桥接、资源、健身、知识库 |
| ai_config / system | AI 配置、系统与健康检查 |

## 测试

```powershell
python -m pytest
```

覆盖健康数学、能量仪表盘、Agent 决策与评测、人在回路、RAG、流式展示、Kinetics-400、
本地 LLM 兜底、迁移采纳、信任与安全等模块。

## 微信云托管部署

- 构建目录 `backend/`，Dockerfile `Dockerfile`，启动入口 `entrypoint.sh`
- 容器 `PORT` 由云托管注入，本地默认 8000
- 生产使用 MySQL 持久化，不保存本地 SQLite / 媒体原文件 / 视觉模型
- Android 云媒体使用 `/auth/cloudbase/ticket` 的十分钟 RS256 自定义票据；客户端只拿到短时票据，CloudBase 私钥必须放在云托管密钥环境变量中
- Android 新媒体可通过 `/media/mobile-upload/sessions` 申请 5 分钟 COS/S3 预签名 PUT，签名绑定用户目录、Content-Type 与确切文件长度，直传后由后端核验对象元数据；正式桶需仅允许 Android origin 的 `PUT/GET/HEAD/OPTIONS` CORS，并用 CAM 限制服务端密钥范围。参考 [COS 简单上传](https://cloud.tencent.com/document/product/436/14113) 与 [COS CORS 配置](https://cloud.tencent.com/document/product/436/13318)。小程序历史媒体仍可保留 CloudBase `fileID` 路径
- CloudBase 存储规则需按自定义身份 `hm_user_{HealthMate 用户编号}` 限制 Android 的 `healthmate/hm_user_{编号}/{image|video}/...` 目录；保留小程序历史媒体时，还需验证关联账号对 `healthmate/u{编号}/...` 的读取/删除权限
- 在 CloudBase 控制台启用 Web SDK 所需的存储 HTTP API 访问策略，并把实际 Android WebView origin（Capacitor 默认 `https://localhost`）加入安全域；上线前用 Android release 包验证上传、临时 URL、删除与 CORS/域名策略
- Android 构建环境设置 `VITE_CLOUDBASE_ENV_ID`（公开环境标识）与 `WECHAT_MOBILE_APP_ID`（开放平台 AppID）；AppSecret 和 CloudBase 私钥只进云托管 secret，不进入 APK

```powershell
docker build -t healthmate-api ./backend
```

## 相关仓库

- [health-assistant](https://github.com/hauyer/health-assistant) — 主仓库（小程序 + 后端 + Worker + 文档）
- ai-worker — 本地 GPU Worker（动作/姿态分析），位于主仓库 `ai-worker/`
