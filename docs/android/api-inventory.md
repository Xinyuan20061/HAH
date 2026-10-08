# Android API 清单（实施基线）

API 根路径是 `VITE_API_BASE_URL`，必须且只包含一次 `/api/v1`。健康探针是 API 根目录的兄弟路径，例如 `https://api.example/health/live`。

| 用途 | HTTP | 路径 | 当前状态 |
| --- | --- | --- | --- |
| 存活探针 | GET | `/health/live`（API 前缀外） | 已有 |
| 就绪探针 | GET | `/health/ready`（API 前缀外） | 已有；连接数据库与迁移 head |
| 开发登录 | POST | `/auth/dev-login` | 已有；非生产，仅一个 `dev-user` |
| 小程序正式登录 | POST | `/auth/wechat` | 已有；使用 `jscode2session` |
| Android 正式登录 | POST | `/auth/mobile/wechat` | 已实现；使用开放平台 OAuth code 与独立服务端凭据，需真实配置验证 |
| CloudBase 用户票据 | POST | `/auth/cloudbase/ticket` | 已实现；需要 Android bearer 登录；短时 RS256 ticket 由后端使用服务账号签发，私钥不下发客户端 |
| 当前登录用户 | GET | `/auth/me` | 已实现；用于恢复会话验证 |
| 从小程序发起账号关联 | POST | `/auth/link/start` | 已实现；仅小程序身份可用，生成十分钟单次码 |
| Android 完成账号关联 | POST | `/auth/link/complete` | 已实现；移动账号须无既有健康数据，禁止静默合并 |
| 查询登录身份类型 | GET | `/auth/me` | 已实现；不返回外部身份 subject |
| 解绑 Android 登录 | POST | `/auth/link/unlink` | 已实现；保留至少一种其他身份，成功后客户端退出 |
| Agent 回答 | POST | `/agent/respond/stream` | 已有；先生成再发送 NDJSON 展示 |
| Agent run 详情 | GET | `/agent/runs/{run_id}` | 已有；含授权范围内的 plan_preview |
| 确认应用计划 | POST | `/agent/runs/{run_id}/apply-plan` | 已有；必须由用户明确确认后调用 |
| 当前计划 | GET | `/agent/plans/current` | 已有 |
| 计划任务状态 | PUT | `/agent/plans/items/{item_id}` | 已有；body `{ "done": boolean }` |
| 首页打卡连续记录 | GET | `/health/command-center` | 已有；客户端读取真实 `streak`，不在本地编造数据 |
| 每日健康打卡 | GET/PUT | `/health/checkin/today` | 已有；保存水、睡眠、体重、步数与心情 |
| 今日摘要 | GET | `/health/today` | 已有；目标页展示真实记录 |
| 七日趋势 | GET | `/health/trends/7d` | 已有；客户端保留缺失值，不把未记录显示为零 |
| 健康目标 | GET/PUT | `/health/goals` | 已有；动态建议使用 `/health/goals/dynamic/*` 并显式确认应用 |
| 个人资料 | GET/PUT | `/users/me`、`/users/me/health-profile` | 已有；昵称与健康档案由客户端编辑 |
| Android 媒体后端选择 | GET | `/media/mobile-upload/options` | 已实现；返回当前开发/生产存储模式与服务端大小限制 |
| Android COS/S3 上传会话 | POST | `/media/mobile-upload/sessions` | 已实现；为随机隔离对象签发 5 分钟 PUT，会话绑定用户、用途、类型、大小和幂等请求号 |
| Android COS/S3 完成或取消 | POST/DELETE | `/media/mobile-upload/sessions/{media_id}/complete`、`/media/mobile-upload/sessions/{media_id}` | 已实现；校验对象长度、类型和文件头，再由服务端复制到私有正式键；过期后由持久删除账本清理隔离对象 |
| CloudBase 历史媒体登记/续期 | POST/PUT | `/media/register-cloud`、`/media/{media_id}/refresh-source` | 已有；小程序历史 `fileID` 保持兼容，Android 仅在 CloudBase 媒体模式直传 |
| 餐食照片任务 | POST/GET | `/vision/food-jobs`、`/vision/food-jobs/{job_id}` | 已有；Android 轮询任务，需续期时刷新 CloudBase 临时链接 |
| 餐食校正/确认入账 | GET/PUT/POST | `/vision/food-analysis/{analysis_id}`、`/correct`、`/finalize` | 已有；本人核对后显式保存餐次和营养估算 |
| 动作视频分析 | POST/GET | `/media/motion-analyses`、`/media/motion-analyses/{analysis_id}` | 已有；支持幂等任务、视频链接续期及长任务轮询；纠正/重分析重试会复用稳定请求键 |
| 动作证据、纠错、反馈 | GET/POST | `/media/motion-analyses/{analysis_id}/evidence`、`/confirm-label`、`/reanalyze`、`/feedback`、`/trace` | 已有；标签确认和重分析支持幂等重试，同键不同内容返回冲突；仅返回本人可见结果与授权范围内证据 |
| 动作训练偏好 | GET/PUT | `/fitness/training-intent`、`/fitness/motion-capabilities` | 已有；保存动作类别、训练目标和限制 |
| 动作播放地址 | GET | `/media/{media_id}/playback` | 已有；仅返回当前用户视频的短期播放地址 |
| 本地开发上传 | POST | `/media/upload` | 已有；production 拒绝 |
| 语音转写 | POST | `/harness/voice/transcribe` | 已有；请求使用 base64 |
| 语音合成 | POST | `/harness/voice/synthesize` | 已有；响应含 base64 音频段 |
| Export | GET/POST | `/privacy/export/preview`、`/privacy/export` | 已有；Android 已接入原生分享/保存，仍需设备验收 |
| 账号永久删除 | DELETE | `/privacy/account` | 已接入；COS/CloudBase 由服务端按平台回执删除，失败时只保留加密对象引用并自动退避重试；未完成时明确返回 pending/partial |

客户端请求统一附带 `Authorization: Bearer <access_token>` 与 `X-Client-Platform: android`。平台头不是身份凭据。业务错误以当前后端的 `{error:{code,message,retryable,request_id,details}}` 为基线。
