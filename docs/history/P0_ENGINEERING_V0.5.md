# P0 工程化部署说明（v0.5）

## 1. 两种运行模式

### 开发模式
`.env`：
```env
ENV=development
DATABASE_URL=sqlite:///./healthmate.db
REDIS_URL=redis://localhost:6379/0
STORAGE_BACKEND=local
```
开发环境允许 Redis 不在线时同步执行视频分析作为兜底，因此前端调试不会因为队列未启动完全失效。

### 生产模式
推荐直接：
```bash
docker compose up --build
```
生产默认组件：FastAPI + MySQL + Redis + RQ worker + MinIO。
生产模式不再调用 `Base.metadata.create_all`，只允许 Alembic 管理数据库结构。

## 2. 数据库迁移
```bash
cd backend
alembic upgrade head
```
当前 head：`0004_platform_foundation`。

## 3. 视频任务
1. `POST /api/v1/media/upload`
2. 返回 `media_id`
3. `POST /api/v1/media/motion-jobs`
4. worker 从对象存储拉取视频
5. MediaPipe/FFmpeg 分析
6. 关键帧回写对象存储
7. `GET /api/v1/media/motion-jobs/{job_id}` 查询状态和结果

## 4. 诊断
- `GET /health`：无需登录，检查 database / redis / ffmpeg。
- `GET /api/v1/system/diagnostics`：登录后进一步检查 DeepSeek。

推荐部署平台把 `/health` 配置成容器健康检查和监控探针。

## 5. MediaPipe 运行环境
姿态依赖放在 `requirements-pose.txt`。Docker 基础镜像固定 Python 3.12，是为了避开部分本机 Python 3.13 下 MediaPipe wheel 兼容问题。

## 6. 对象存储
- 本地调试：`STORAGE_BACKEND=local`
- 生产：`STORAGE_BACKEND=s3`
- MinIO/AWS OSS 类服务均通过 S3 API 适配。
- 正式小程序需要 HTTPS 合法域名，因此生产必须把 `S3_PUBLIC_BASE_URL` 指到 CDN/对象存储公网域名，并加入微信小程序 downloadFile 合法域名。

## 7. 为什么没有让大模型直接看视频
动作几何事实由姿态模型和规则算法决定，大模型只应负责解释。这样比赛答辩时可以给出可复现的角度、关键点、帧和错误证据，而不是不可验证的生成式判断。
