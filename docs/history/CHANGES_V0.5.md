# HealthMate v0.5 — 稳定底座与姿态分析

## 本版完成

### P0 后端工程化
- 开发/生产环境分离：开发默认 SQLite；生产 Docker Compose 使用 MySQL 8.4。
- Redis + RQ 后台任务队列：视频动作分析不再阻塞 FastAPI 请求。
- 媒体存储抽象：`STORAGE_BACKEND=local|s3`；生产 Compose 默认 MinIO(S3 兼容)。
- 统一错误结构：`code/message/data/request_id`，保留现有成功响应以兼容 v0.4 前端。
- `/health` 升级为数据库、Redis、FFmpeg 诊断；新增 `/api/v1/system/diagnostics`，额外检查 DeepSeek。
- 每个 HTTP 请求自动注入 `X-Request-ID`，方便日志关联与答辩演示故障定位。
- Alembic `0004_platform_foundation` 新增 HealthTimeline、媒体资产、动作分析任务表。
- pytest 工程配置补齐，避免因模块路径导致测试无法收集。

### P0 姿态分析
- 新链路：视频 → RQ worker → FFmpeg 关键帧 → MediaPipe Pose → 关节角 → 动作次数 → 规则纠错。
- 当前首个规则模板：深蹲。
- 已实现：膝角、髋角、躯干倾角、动作次数、下蹲不足/躯干前倾提示。
- MediaPipe 不可用时自动退化为 FFmpeg 分析，不影响核心项目启动。
- 派生关键帧回写媒体存储，避免生产 worker 临时目录丢失结果。

### P0 统一健康数据中心
- 新增 `HealthTimelineEvent`。
- 饮食、运动、每日打卡写入记录时同步写入 Timeline。
- 新增 `GET /api/v1/timeline?days=7`，后续首页、周报、Agent 统一从该数据层扩展。

### 小程序可靠性
- GET 成功数据自动离线缓存（默认 6 小时）。
- 网络失败最多 2 次指数退避重试；GET 重试仍失败时返回缓存。
- 上传失败自动重试。
- 请求层支持新的统一后端错误格式和 request id。
- 视频页改为异步 job 轮询，并展示姿态次数、角度和错误证据。

## 尚未伪装成“已完成”的部分
- 膝内扣需要正面视角下更严谨的髋/膝/踝横向几何与相机视角判断，当前未做假判断。
- 标准动作模板对齐、DTW/时序相似度、多人/遮挡鲁棒性仍属于 v0.6。
- Health Agent 的“自动加入本周计划”与动态目标算法尚未在本版强行塞入，接口应建立在 Timeline 稳定后。
- S3/MinIO 正式部署时应配置可被小程序访问的 `S3_PUBLIC_BASE_URL`（对象存储域名/CDN），不能使用容器内部 `minio:9000` 地址。
