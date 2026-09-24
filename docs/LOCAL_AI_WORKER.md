# 本地 AI Worker

Worker 通过公网 HTTPS 主动轮询微信云托管任务，电脑不开放入站接口。姿态使用 OpenCV 和 MediaPipe 的 CPU 推理；本地视觉模型是否使用 GPU 由模型服务决定。VLM 不可用不影响 motion_pose。

## 1. Windows 安装

推荐 Python **3.12**（本轮实际 3.12.14，3.11 属于支持目标但未另建环境实测）。MediaPipe 0.10.21 不适合直接用本轮电脑原有的 3.13/3.14 环境。仓库目录可以是中文，但虚拟环境要放在英文路径；本轮实际发现中文 venv 导入成功后原生模型仍报 FileNotFound。

在仓库 `ai-worker/` 目录 PowerShell：

```powershell
py -3.12 -m venv C:\HealthMateRuntime\worker
& C:\HealthMateRuntime\worker\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env 的云端地址和 Token，模型未启动先将 LOCAL_VLM_MODEL 留空
python doctor.py
python worker.py
```

若尚未安装 Python 3.12，先安装并检查 `py -0p`；不能直接复用不兼容版本的 venv。PowerShell 激活被策略阻止时，可以直接调用 `C:\HealthMateRuntime\worker\Scripts\python.exe`，无需改系统策略。

依赖直接锁定：NumPy 1.26.4、opencv-contrib-python 4.11.0.86、MediaPipe 0.10.21、Pillow 11.3.0、httpx 0.28.1、Pydantic 2.11.7、pydantic-settings 2.10.1。只装 **一个提供 cv2 的发行包**：MediaPipe 依赖 contrib，不能同时再装 opencv-python 4.12/NumPy 2。新环境安装后运行 `python -m pip check`。本轮英文路径 Windows Python 3.12 安装、原生 Pose、真实视频推理均通过；依赖安装可能仍需要正常网络/可信证书。

## 2. 环境变量

```env
API_BASE_URL=https://<同一微信云托管公网域名>/api/v1
WORKER_TOKEN=<与云端相同的独立随机Token>
WORKER_ID=healthmate-laptop-01
WORKER_NAME=HealthMate Local Worker
CAPABILITIES=motion_pose,food_vision
AI_MODE=local_first
LOCAL_CONFIDENCE_THRESHOLD=0.6
POLL_INTERVAL_SECONDS=2
HEARTBEAT_INTERVAL_SECONDS=15
REQUEST_TIMEOUT_SECONDS=60
API_MAX_RETRIES=3
MAX_DOWNLOAD_MB=200
MAX_REDIRECTS=5
LOG_LEVEL=INFO
ALLOW_PRIVATE_MEDIA_HOSTS=false
LOCAL_VLM_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_VLM_MODEL=
LOCAL_VLM_API_KEY=
LOCAL_VLM_TIMEOUT_SECONDS=120
LOCAL_VLM_JSON_MODE=false
IMAGE_MAX_DIMENSION=1280
IMAGE_MAX_BYTES=1000000
FOOD_CLASSIFIER_MODEL=
FOOD_CLASSIFIER_TOP_K=3
```

这些值由 `healthmate_worker/config.py` 集中读取。CAPABILITIES 是允许探测的能力，不是无条件宣称已可用。模型 ID 默认留空，不绑定 7B 或任何品牌。Worker 只持有自己的 Token，不需要数据库密码、AppSecret 或用户 JWT。

## 3. doctor 和启动

```powershell
python doctor.py
python doctor.py --offline
python doctor.py --vlm-smoke
python worker.py --self-check
python worker.py --once
python worker.py
# 自动重启守护（PowerShell 或双击 bat）
.\start_worker.ps1
```

doctor 检查版本、真实依赖导入、Pose 模型加载与一次原生推理、FFmpeg（可选，OpenCV 解码已覆盖）、nvidia-smi、torch 可见时的 CUDA、云端 live、Worker Token/heartbeat、本地 VLM /models、精确模型 ID。`--offline` 跳过云端检查；`--vlm-smoke` 发送真实极小图像，不可用/失败返回非零。正常无 VLM 且姿态可用时 doctor 可以成功，明确显示识餐 OFF。

doctor 的鉴权检查会发送本机 worker_id 的真实 heartbeat；请在 Worker 停止时运行诊断，结束后重新启动 Worker。持续在线状态由 Worker 的周期心跳维护，诊断心跳过了离线阈值会失效。

```text
[OK] cv2: 4.11.0
[OK] mediapipe: 0.10.21
[OK] motion_pose: OpenCV 4.11.0; MediaPipe 0.10.21
[OK] Cloud API /health/live: HTTP 200
[OK] Worker auth / heartbeat
[OFF] food_vision: VLM 无法连接
```

这是本轮本地 API 接口检查的结果示意；项目拥有者的云端还需重跑。普通 Laptop GPU 显存不是固定足够；没有 NVIDIA 也可以进行 MediaPipe CPU 姿态推理。

`--self-check` 输出能力、模型、语义运行时和云端心跳的 JSON 状态；心跳必须是 2xx。`--once` 最多处理一个任务，空队列 0、能力全不可用 2、API 鉴权/领取失败 3、任务失败 4。Ctrl+C 停止轮询；处理中断未完成任务由云端租约到期恢复，不伪造完成结果。watchdog 在异常退出后有界等待并重启，正常 Ctrl+C 不制造完成结果。

## 4. 视觉 VLM Provider

`AI_MODE=local_first` 先调用本地识餐，低于 `LOCAL_CONFIDENCE_THRESHOLD` 或失败时回落 DeepSeek；`cloud_first` 先调用 DeepSeek，失败时回落本地；`off` 完全禁止云端识餐。`VLM_PROVIDER` 只保留旧配置兼容。三个模式使用同一结果结构，云端失败不会破坏可用的本地结果。

本地识餐需启动支持图像输入的 OpenAI-compatible 模型服务（可用 LM Studio、vLLM 或其他实现）；DeepSeek 使用 `DEEPSEEK_VISION_MODEL`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_API_KEY`。本地模式选择适合显存的较小/量化视觉模型，不能仅凭名称保证 8GB 一定能运行。

Nutrition5k 基线热量 MAPE 超过 30% 后，增加了可选的预训练 Food-101 菜名候选层。先按机器安装对应 CUDA/CPU 版本的 torch，再安装 `requirements-food-classifier.txt` 并配置经复核的 Hugging Face 分类器 ID。分类器只提供 Top-K 菜名，VLM 被约束为从候选中选名并估算份量/营养；候选层加载失败会安全退回原识餐路径。

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
# 若服务要求 key，可用 -Headers @{Authorization='Bearer 本地服务Key'}
```

从返回 data[].id 复制准确 ID 到对应模型配置；仅有 /models 返回不证明模型具有视觉能力，应再执行 `python doctor.py --vlm-smoke` 和真实餐食识别。只接受 2xx；401 提示 Key/权限，404 提示 base URL，模型不在列表或 ID 空则禁用 food_vision。代码回归不等于真实云端推理验收，比赛前仍需用实际 Key 和授权餐食图留存 smoke 记录。

图片先经 Pillow verify、EXIF 转正、RGB 转换、最长边缩放和 JPEG 压缩，默认限制约 1MB；不把几十 MB 原图直接 base64 发送。结果使用嵌套 JSON 解码器并经过逐食材模型校验，拒绝多个对象歧义、无效范围和总计冲突。所有结果标记 is_estimate，低置信度加入人工校正提醒，确认后才能创建饮食记录。DeepSeek 模式会将压缩图片发送到其视觉 API，必须在隐私页明确告知用户。

主要错误码：vlm_auth、vlm_model_missing、vlm_oom、vlm_timeout、vlm_invalid_json、vlm_invalid_result、vlm_unavailable、invalid_media。结果不能用无效字段自动置零来冒充成功。端点和模型状态会在每个 food 任务前重新检测；已运行 Worker 后启动 VLM，重启 Worker 重新注册 capability。

## 5. 动作分析

|动作|主要指标|动作证据|
|---|---|---|
|深蹲|膝角、髋角、躯干倾角|完整下蹲/起立周期，深度提示，前倾提示|
|俯卧撑|肘角，肩/髋/踝角及相对直线偏移|下放/撑起周期，浅下放，臀部抬高/塌陷近似提示|
|弓步|前腿膝角、后腿关键点、躯干、前腿侧别|独立阈值，前腿选择，完整周期和后腿可见度|
|腿外展|双腿夹角、膝角、躯干|外展/回收周期与幅度提示|
|手臂侧平举|肩外展角、肘部伸展度|抬起/放下周期与抬举幅度提示|
|手臂 V/W|肘角与肩部位置|V/W 变化周期与幅度提示|

阈值集中在 processors/analyzers.py，均为二维健身辅助启发规则。默认视频最长 120 秒、最多 30000 帧，约 8 次/秒姿态采样（事件基于实际采样关键点，不是固定均匀返回 6 张图）。完整“伸展 → 下放 → 伸展”才计数；中间可见数据间隔 >0.75 秒打断周期，关键点不足返回 available=false/不足以评价。

事件包括最高位、每次最低位/起立完成、最深位、最大躯干角与最大风险时刻，带真实 timestamp/metrics/confidence。最多四张事件图在本机截取、模糊脸部、叠加骨骼并压缩到每张80KB以内；后端校验后随任务结果展示，默认7天后删除图片，结构化事件继续保留。无预览时前端降级到匿名骨骼画布。没有3D、医学精度或实时反馈承诺。机位、遮挡、光照会影响质量，比赛前应分别用真实完整运动视频验收。

## 6. 任务和安全

后台 heartbeat 与 claim 分离；下载和阻塞推理期间后台持续 lease renewal。claim request_id 在同一次网络重试中复用；完成回执可幂等重发。网络/429/5xx 使用有界退避，401/403 不无限重试。失效 lease 的回执返回 409；本机清理临时文件，云端到期恢复；不能覆盖另一 Worker 的任务结果。

源链接失效对应 media_url_expired/media_url_missing → waiting_source_refresh；不消耗真实推理尝试。小程序刷新 URL 后原任务重新 queued。其他可重试错误有最大尝试次数与 next_attempt_at 退避，不可重试错误直接 failed。

生产媒体要求 HTTPS、全部 DNS 地址为公网非保留、固定已校验 IP 并保留 Host/TLS SNI、每次跳转重新校验、限制响应体实际字节。不会关闭 SSL。仅本地 HTTP 联调显式 `ALLOW_PRIVATE_MEDIA_HOSTS=true`，上云必须 false；这也是 CloudAPI 允许 HTTP 的开发开关。

日志只记 job_id、stage、error_code/HTTP 状态，不输出媒体签名 URL/Authorization/原始健康文本。临时素材在 finally 删除。已经下载到本机的健康媒体仍需由获授权的项目电脑处理，勿分享 Worker Token。

## 7. 可复现验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
# 英文路径 Worker Python + 已安装 backend/.venv；下载公共姿态图，生成临时静态视频
python scripts/verify_local_motion.py
```

本轮 Worker 90 passed；真实 HTTP 上传→任务→实际 Worker→MediaPipe→done 的 smoke 已执行，并额外读取 REHAB24-6 的腿外展和手臂侧平举片段。六类固定测试集共 120 段全部完成，指标与限制见 `benchmark-results/motion-v1/report.md`。smoke 只证明解码和协议，准确率只对登记固定集有效；测试中的 MockTransport/模拟关键点不替代真实功能。

MediaPipe 固定版本依据：[PyPI 0.10.21](https://pypi.org/project/mediapipe/0.10.21/)。实测环境和限制见 VERIFICATION.md。
