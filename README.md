# HealthMate

微信小程序健康助手：档案、目标、打卡、饮食/运动记录、趋势、周报、待确认计划、DeepSeek 对话，以及本机处理的姿态分析和识餐校正。

```text
微信小程序 → 微信云托管 FastAPI → 持久 MySQL / CloudBase 云文件引用
                                ├─ DeepSeek 文本能力
                                ├─ 识餐云端直连 → 失败时原任务回到 Worker 队列
                                └─ AIJob 队列 ← 本机 Worker 主动轮询公网 HTTPS
                                                ├─ OpenCV / MediaPipe 六类动作规则基线
                                                ├─ 本地 OpenAI-compatible VLM（可选 GPU）
                                                └─ DeepSeek 识餐（按 AI_MODE 切换）
```

生产在线后端使用微信云托管；不依赖阿里云。容器不保存生产 SQLite、媒体原文件或视觉模型。2026-09-27 本地回归：后端 SQLite 161 项、Worker 99 项、小程序 43 项自动测试通过；真实数据报告见 [动作基线](benchmark-results/motion-v1/report.md) 与 [识餐基线](benchmark-results/food-v1/report.json)。MySQL 8.4 仍需在当前迁移 head 上重跑。完整证据和未验证项见 [VERIFICATION](docs/VERIFICATION.md)。

HealthMate 3.0 已新增健康指挥中心首页、证据优先的识餐区间协议和全局低饱和视觉系统，详见 [系统级更新说明](docs/HEALTHMATE_3_SYSTEM_UPDATE.md)。

Health Agent v3.1 已新增主动健康提醒与人在回路反馈：确定性扫描运动断档、睡眠不足、动作表现下滑、体重连升和记录空白；小程序提供“观察依据—保守建议—用户行动—用户反馈”闭环，并把真实反馈纳入运行评测。反馈不会自动训练模型或改写健康记录。真实回答质量另提供双人盲评模板与强制完整性校验。评审分析与后续路线见 [评审视角审计与升级路线](docs/REVIEWER_AUDIT_AND_ROADMAP_2026-09-24.md)。

```text
HealthMate/
├── miniprogram/                 微信原生小程序（现有页面和业务保留）
├── backend/                     FastAPI、Alembic、测试、Dockerfile
├── ai-worker/                   本机 Worker、doctor、独立动作 analyzer
├── docs/                        部署、修复报告、实测记录
├── docker-compose.yml           本地 MySQL 集成环境
└── project.config.json          微信开发者工具导入入口
```

## A. 本地开发

推荐安装 Python 3.12、Node.js 和 Docker Desktop。后端开发/test 可以使用 SQLite；生产配置会拒绝 SQLite。

在仓库根目录的 PowerShell：

```powershell
Set-Location backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

编辑 `.env`。本地填写 `ENV=development`、`DATABASE_URL=sqlite:///./healthmate.db`、`STORAGE_BACKEND=local`。用下面命令分别生成 JWT 密钥、独立凭据加密密钥、Worker Token，填入对应字段（不要把输出提交到仓库）：

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
python scripts/preflight.py
python -m alembic upgrade head
python -m alembic current
$env:PORT='8000'
python -m uvicorn app.main:app --host 0.0.0.0 --port $env:PORT
```

查看 `http://127.0.0.1:8000/docs`、`/health/live`、`/health/ready`。开发者工具使用本地 HTTP 时，显式将小程序 `DEV_LOGIN` 改为 `true`；上云前恢复 `false`。真机不能访问电脑的 `127.0.0.1`，本地测试需要同网段电脑地址和开发者工具调试设置。生产不能关闭域名/SSL 检查。

## B. 微信云托管部署

详细步骤见 [WECHAT_CLOUD_RUN_DEPLOY](docs/WECHAT_CLOUD_RUN_DEPLOY.md)。构建目录 **backend/**，Dockerfile **Dockerfile**；从根目录本地构建是 `docker build -t healthmate-api ./backend`。

云环境设置 `ENV=production`、MySQL `DATABASE_URL`、`STORAGE_BACKEND=cloud_ref`、三个独立随机密钥、真实微信 AppID/AppSecret、CloudBase 环境 ID、服务名及公网 HTTPS 地址。保留 `CLOUD_HEADER_LOGIN_ENABLED=false`、`RUN_MIGRATIONS_ON_START=false`。镜像默认启动 API，监听 `0.0.0.0` 和平台注入的 `PORT`（未注入默认 8000）。先独立迁移，再启动/发布 Web 版本，再扩容。

## C. MySQL 初始化/迁移

创建持久数据库 `healthmate` 和应用账号，字符集 `utf8mb4`。用控制台真实连接参数填写：

```env
DATABASE_URL=mysql+pymysql://USER:URL_ENCODED_PASSWORD@HOST:3306/healthmate?charset=utf8mb4
```

密码含 `@`、`:`、`/`、`%` 时必须 URL 编码。在能连接同一数据库的受控环境设置生产变量，执行 `python scripts/preflight.py`、`alembic upgrade head`、`alembic current`，当前 head 是 `0022_agent_decision_id`。迁移前备份，迁移账号允许 DDL；迁移完成后 Web 账号可限制为业务 DML 权限。不要把 CloudBase 文档数据库当作 MySQL。

本地 Docker MySQL 集成：在根目录 `.env` 填写随机十六进制 `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`SECRET_KEY`、独立 `CREDENTIALS_ENCRYPTION_KEY`、`WORKER_TOKEN`（没有默认密码）。然后：

```powershell
docker compose up -d mysql
docker compose run --rm migrate
docker compose up -d --build api
```

MySQL 映射 `127.0.0.1:3307`，API 为 `127.0.0.1:8000`。不要执行 `docker compose down -v` 清掉持久卷。Docker Web 重启不执行迁移。已有 Alembic 库直接升级；无版本表的旧 SQLite 先备份并运行 `scripts/adopt_dev_database.py`，脚本仅在 development/test 且结构精确匹配已知迁移时 stamp，不会猜测生产库版本。

## D. 小程序配置

微信开发者工具导入仓库根目录，`project.config.json` 指向 `miniprogram/`。核对自己的 AppID。编辑 `miniprogram/config/index.js`：真实 `CLOUD_ENV_ID`、`CLOUDRUN_SERVICE_NAME`；`DEV_LOGIN=false`。配置同一服务的公网 HTTPS `PUBLIC_API_BASE_URL` 后可保持 `USE_STREAMING=true`，让已完成安全校验的回答逐 token 呈现；没有公网入口时设为 `false`，自动通过 `wx.cloud.callContainer` 获取完整结果。本机 Worker 使用同一服务的公网 HTTPS 地址。

CloudBase 云存储权限设置为仅文件创建者读写，并完成两个不同微信账号的隔离验证。用户媒体路径为 `healthmate/u<后端用户ID>/{image|video}/...`，不能把数字目录校验当成云端权限规则。补齐微信后台隐私指引、相机/相册用途声明和发布要求。

## E. 本地 AI Worker

详见 [LOCAL_AI_WORKER](docs/LOCAL_AI_WORKER.md)。推荐 Python 3.12，并将 Worker **虚拟环境放在英文路径**；本轮发现 MediaPipe 0.10.21 原生模型不能从中文虚拟环境路径加载。

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
# 只领取最多一个任务；空队列时正常退出，任务失败返回非零
python worker.py --once
# 需要自动重启时
.\start_worker.ps1
```

Worker 不需要数据库密码或用户 JWT。`AI_MODE=local_first` 优先本地并在低分时回落云端，`cloud_first` 优先云端，`off` 禁止云端识餐；三个模式保留同一结果协议。`ALLOW_PRIVATE_MEDIA_HOSTS=false` 保持生产下载安全边界；只在显式本地 HTTP 联调时设置 true。MediaPipe 不可用时禁用姿态能力；VLM 不可用时只禁用识餐。

## F. 本地 VLM

自行启动支持图像输入的 OpenAI-compatible 服务；代码不绑定模型品牌、7B 或指定显卡。使用适合自己显存的小型/量化视觉模型，并实测质量与延迟。

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

从返回 `data[].id` 复制真实视觉模型 ID 到 `LOCAL_VLM_MODEL`，设置正确 `LOCAL_VLM_BASE_URL` 和本地服务要求的 `LOCAL_VLM_API_KEY`。没有模型时留空 ID，识餐 OFF。运行 `python doctor.py --vlm-smoke` 检查真实图像请求；然后用真实餐食验收校正/确认闭环。只有 endpoint 支持时才开启 `LOCAL_VLM_JSON_MODE=true`。

## G. 端到端验证

先按 [VERIFICATION](docs/VERIFICATION.md) 重跑离线测试。现场用真实微信账号完成：登录 → 确认训练部位与目标 → 云上传 → 注册稳定 fileID → 创建任务 → 本机领取 → 进度/完成 → 动作效果语义与意图匹配。识餐必须确认后入库，可以先校正；当前视频规则基线支持深蹲、俯卧撑、弓步蹲、腿外展、手臂侧平举和手臂 V/W，不确定时拒识。固定 120 段真实测试的全样本准确率仅 41.67%，因此它是可运行基线，不是高精度模型。最终路线和数据集治理见 [最终的项目构想](最终的项目构想.md)，识别协议见 [AUTO_MOTION_RECOGNITION](docs/AUTO_MOTION_RECOGNITION.md)。

可训练的骨骼多标签模型、骨骼提取、阈值校准、模型卡和Worker安全回退见 [SKELETON_MODEL_TRAINING](docs/SKELETON_MODEL_TRAINING.md)。仓库不包含第三方原始数据或虚构训练权重。

停 Worker 时，默认云端识餐仍可完成；显式 `route=worker` 的任务会保留排队，重启后继续领取。临时 URL 过期应进入 `waiting_source_refresh`，原地刷新同一媒体与同一任务；页面最多轮询约 300 秒并提示稍后重试，不无限等待。关闭本地 VLM，仍可演示档案、目标、记录、图表、已配置的 DeepSeek 和在线姿态 Worker。关键帧预览为受限 Data URI，结构化事件保留、预览按保留期清理。

隐私删除：小程序先逐文件确认 `wx.cloud.deleteFile`，再提交 fileID 确认清理数据库；文件删除失败保留账户供重试。后端记录的是客户端删除回执，不是服务端验证过的 CloudBase 删除证明。

## H. 常见故障

|现象|处理|
|---|---|
|生产预检失败|按字段补齐 MySQL、cloud_ref、独立密钥、真实微信参数、HTTPS 地址；不要改回 SQLite|
|live 200 / ready 503|检查数据库网络、连接参数、权限、Alembic head；不要将 Worker/DeepSeek 纳入 readiness|
|日志 SQLiteImpl 出现在生产|确认 ENV=production 和 DATABASE_URL 实际注入；开发默认值不适用于云端|
|MediaPipe 能导入却找不到模型|重建英文路径 Python 3.12 虚拟环境；不要复用中文路径环境|
|food_vision OFF / 401 / 404|启动真实视觉模型；检查 key、含 /v1 的地址和 /models 中的精确 ID|
|Worker 401/403|云端与本机 Token 相同；不可用用户 JWT 替代；更新 Token 后重启 Worker|
|任务一直 queued|查看对应 motion_online/food_online 能力和本机心跳；队列保留，前端有等待上限|
|waiting_source_refresh|回到页面刷新临时链接，不重新上传/创建重复任务；确认云文件仍存在|
|DeepSeek 不可用|普通记录继续工作；对话明确 503，周报/计划明确为规则降级|
|升级后用户 Key 不能解密|恢复原 CREDENTIALS_ENCRYPTION_KEY；更换加密密钥前需要迁移密文|
|安装镜像源 TLS 报错|修复系统证书/网络，或使用官方 PyPI；不要关闭 SSL 校验|

HealthMate 2.0 动作评分、匿名骨骼关键帧、动态训练约束和可信资源库说明见 [HEALTHMATE_2_PHASE1](docs/HEALTHMATE_2_PHASE1.md)。正式动作数据集的标注和自动评测见 [benchmark/README](benchmark/README.md)，RAG查询集与检索评测见 [RAG_EVALUATION](benchmark/RAG_EVALUATION.md)，Agent 固定集评测见 [AGENT_EVALUATION](benchmark/AGENT_EVALUATION.md)；所有示例结果都不得当作比赛准确率，离线评测不代表真实 DeepSeek 在线质量。修复详情见 [FIX_REPORT](docs/FIX_REPORT.md)。历史迭代材料保留在 `docs/history/`，不作为当前部署验收结论。
