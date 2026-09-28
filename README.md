<p align="center">
  <strong>简体中文</strong>
</p>

<h1 align="center">HealthMate</h1>

<p align="center"><strong>微信小程序健康助手 · 档案 / 目标 / 记录 / 洞察 / Agent 对话 / 姿态与识餐</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/微信小程序-原生-07C160?style=flat-square&logo=wechat&logoColor=white" alt="微信小程序">
  <img src="https://img.shields.io/badge/FastAPI-0.116-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI 0.116">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/MySQL-4479A1?style=flat-square&logo=mysql&logoColor=white" alt="MySQL">
  <img src="https://img.shields.io/badge/DeepSeek-536DFE?style=flat-square" alt="DeepSeek">
  <img src="https://img.shields.io/badge/微信云托管-07C160?style=flat-square" alt="微信云托管">
</p>

<p align="center">
  <a href="#核心功能">核心功能</a> ·
  <a href="#目录结构">目录结构</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#微信云托管部署">部署</a> ·
  <a href="#小程序配置">小程序配置</a> ·
  <a href="#本地-ai-worker-与-vlm">AI Worker / VLM</a> ·
  <a href="#测试与验证">测试与验证</a> ·
  <a href="#常见故障">常见故障</a>
</p>

HealthMate 是一个微信小程序健康助手，覆盖健康档案、目标管理、打卡、饮食/运动记录、
趋势与周报、待确认计划、DeepSeek 对话，以及本机处理的姿态分析和识餐校正。

```text
微信小程序 → 微信云托管 FastAPI → 持久 MySQL / CloudBase 云文件引用
                                ├─ DeepSeek 文本能力
                                ├─ 识餐云端直连 → 失败时原任务回到 Worker 队列
                                └─ AIJob 队列 ← 本机 Worker 主动轮询公网 HTTPS
                                                ├─ OpenCV / MediaPipe 六类动作规则基线
                                                ├─ 本地 OpenAI-compatible VLM（可选 GPU）
                                                └─ DeepSeek 识餐（按 AI_MODE 切换）
```

生产在线后端使用微信云托管，不依赖阿里云；容器不保存生产 SQLite、媒体原文件或视觉模型。
HealthMate 3.0 已新增健康指挥中心首页、证据优先的识餐区间协议和全局低饱和视觉系统；
Health Agent v3.1 已新增主动健康提醒与人在回路反馈闭环。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 健康档案 | 微信登录、头像昵称同步、健康档案与 AI 配置 |
| 目标与打卡 | 减脂 / 保持 / 增肌目标、动态目标调整、每日打卡 |
| 饮食/运动记录 | 饮食/运动/身体数据记录、趋势、周报、能量收支仪表盘 |
| 智能对话 | DeepSeek 流式对话、逐 token 呈现、本地 Qwen 兜底 |
| 视觉识餐 | 云端直连识餐，失败回退 Worker 队列，确认后入库 |
| 动作识别 | 本机 Worker 姿态/动作分析（MediaPipe 六类基线 + Kinetics-400） |
| Health Agent | 多专家 Agent、主动健康提醒、人在回路反馈、决策追踪 |
| 安全与隐私 | 内容安全、敏感脱敏、隐私删除、速率限制、审计 |
| 媒体存储 | 本地 / CloudBase / S3 兼容存储后端，URL 安全与签名 |
| 评测体系 | 离线评测、双人盲评、真实数据基线 |

## 目录结构

```text
HealthMate/
├── miniprogram/                 微信原生小程序（页面、组件、业务）
├── backend/                     FastAPI、Alembic、测试、Dockerfile
├── ai-worker/                   本机 Worker、doctor、独立动作 analyzer
├── docs/                        部署、修复报告、实测记录
├── docker-compose.yml           本地 MySQL 集成环境
└── project.config.json          微信开发者工具导入入口
```

## 快速开始

推荐安装 Python 3.12、Node.js 和 Docker Desktop。后端开发/测试可以使用 SQLite；生产配置会拒绝 SQLite。

在仓库根目录的 PowerShell：

```powershell
Set-Location backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

编辑 `.env`，本地填写 `ENV=development`、`DATABASE_URL=sqlite:///./healthmate.db`、`STORAGE_BACKEND=local`。
分别生成 JWT 密钥、独立凭据加密密钥、Worker Token 并填入（不要把输出提交到仓库）：

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
python scripts/preflight.py
python -m alembic upgrade head
python -m alembic current
$env:PORT='8000'
python -m uvicorn app.main:app --host 0.0.0.0 --port $env:PORT
```

查看 `http://127.0.0.1:8000/docs`、`/health/live`、`/health/ready`。开发者工具使用本地 HTTP 时，
显式将小程序 `DEV_LOGIN` 改为 `true`，上云前恢复 `false`。

## 微信云托管部署

详细步骤见 [WECHAT_CLOUD_RUN_DEPLOY](docs/WECHAT_CLOUD_RUN_DEPLOY.md)。构建目录 **backend/**，Dockerfile **Dockerfile**：

```powershell
docker build -t healthmate-api ./backend
```

云环境设置 `ENV=production`、MySQL `DATABASE_URL`、`STORAGE_BACKEND=cloud_ref`、三个独立随机密钥、
真实微信 AppID/AppSecret、CloudBase 环境 ID、服务名及公网 HTTPS 地址。保留
`CLOUD_HEADER_LOGIN_ENABLED=false`、`RUN_MIGRATIONS_ON_START=false`。镜像默认监听 `0.0.0.0` 和平台注入的
`PORT`（未注入默认 8000）。先独立迁移，再启动/发布 Web 版本，再扩容。

## 小程序配置

微信开发者工具导入仓库根目录，`project.config.json` 指向 `miniprogram/`。核对自己的 AppID。编辑
`miniprogram/config/index.js`：真实 `CLOUD_ENV_ID`、`CLOUDRUN_SERVICE_NAME`；`DEV_LOGIN=false`。配置同一服务的
公网 HTTPS `PUBLIC_API_BASE_URL` 后可保持 `USE_STREAMING=true`，让已完成安全校验的回答逐 token 呈现。

CloudBase 云存储权限设置为仅文件创建者读写。用户媒体路径为 `healthmate/u<后端用户ID>/{image|video}/...`。
补齐微信后台隐私指引、相机/相册用途声明和发布要求。

## 本地 AI Worker 与 VLM

详见 [LOCAL_AI_WORKER](docs/LOCAL_AI_WORKER.md)。推荐 Python 3.12，并将 Worker **虚拟环境放在英文路径**：

```powershell
Set-Location ai-worker
py -3.12 -m venv C:\HealthMateRuntime\worker
& C:\HealthMateRuntime\worker\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 API_BASE_URL=https://同一云托管公网域名/api/v1 和相同 WORKER_TOKEN
python doctor.py
python worker.py --self-check
python worker.py
```

Worker 不需要数据库密码或用户 JWT。`AI_MODE=local_first` 优先本地并在低分时回落云端，`cloud_first` 优先云端，
`off` 禁止云端识餐。本地 VLM 启动支持图像输入的 OpenAI-compatible 服务，从 `http://127.0.0.1:1234/v1/models`
返回的 `data[].id` 复制真实模型 ID 到 `LOCAL_VLM_MODEL`，运行 `python doctor.py --vlm-smoke` 检查真实图像请求。

## 测试与验证

先按 [VERIFICATION](docs/VERIFICATION.md) 重跑离线测试，再用真实微信账号走通端到端闭环：登录 → 确认训练部位与目标
→ 云上传 → 注册稳定 fileID → 创建任务 → 本机领取 → 进度/完成 → 动作效果语义与意图匹配。

```powershell
# 后端（backend/）
python -m pytest
# 小程序（miniprogram/）
npm test
# Worker（ai-worker/）
python -m pytest
```

动作规则基线支持深蹲、俯卧撑、弓步蹲、腿外展、手臂侧平举和手臂 V/W，不确定时拒识；固定 120 段真实测试的全样本
准确率约 41.67%，是可运行基线而非高精度模型。真实数据报告见 [动作基线](benchmark-results/motion-v1/report.md) 与
[识餐基线](benchmark-results/food-v1/report.json)。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| 生产预检失败 | 按字段补齐 MySQL、cloud_ref、独立密钥、真实微信参数、HTTPS 地址 |
| live 200 / ready 503 | 检查数据库网络、连接参数、权限、Alembic head |
| 日志 SQLiteImpl 出现在生产 | 确认 `ENV=production` 和 `DATABASE_URL` 实际注入 |
| MediaPipe 能导入却找不到模型 | 重建英文路径 Python 3.12 虚拟环境 |
| food_vision OFF / 401 / 404 | 启动真实视觉模型，检查 key 与 `/models` 中的精确 ID |
| Worker 401/403 | 云端与本机 Token 相同，不可用用户 JWT 替代 |
| 任务一直 queued | 查看 motion_online/food_online 能力和本机心跳 |
| waiting_source_refresh | 回到页面刷新临时链接，不重新上传 |
| DeepSeek 不可用 | 普通记录继续工作，对话明确 503，周报/计划规则降级 |
| 升级后用户 Key 不能解密 | 恢复原 `CREDENTIALS_ENCRYPTION_KEY` |
| 安装镜像源 TLS 报错 | 修复系统证书/网络，或使用官方 PyPI |

## 相关文档

- [系统级更新说明](docs/HEALTHMATE_3_SYSTEM_UPDATE.md) · [评审审计与升级路线](docs/REVIEWER_AUDIT_AND_ROADMAP_2026-09-24.md)
- [动作识别协议](docs/AUTO_MOTION_RECOGNITION.md) · [骨骼模型训练](docs/SKELETON_MODEL_TRAINING.md)
- [RAG 评测](benchmark/RAG_EVALUATION.md) · [Agent 评测](benchmark/AGENT_EVALUATION.md) · [动作数据集](benchmark/README.md)
- [本地 AI Worker](docs/LOCAL_AI_WORKER.md) · [修复报告](docs/FIX_REPORT.md) · [完整验证](docs/VERIFICATION.md)
