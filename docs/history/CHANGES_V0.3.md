# HealthMate V0.3 产品化升级

本版本目标不是继续堆“功能入口”，而是把首页、AI 能力、记录与数据洞察组织成一条真正可用的健康管理路径。

## 已完成

### 1. AI 拍照识餐
- 新增 `pages/scan/index`。
- 微信相机 / 相册选择餐食照片。
- 后端 `/api/v1/vision/food-analysis` 接入 DeepSeek `deepseek-flash` 多模态视觉能力。
- 返回菜名、置信度、热量、蛋白质、碳水、脂肪、膳食纤维、份量、食材与改善建议。
- 识别结果可一键写入饮食记录。
- 页面明确提示“图片营养估算 ≠ 医疗结论”。

### 2. AI 智能健身计划
- 新增 `pages/workout/index`。
- 支持目标、水平、器械、每周训练天数、单次时长选择。
- 后端 `/api/v1/insights/workout-plan` 结合用户基础健康档案生成一周训练计划。
- DeepSeek 未配置时保留可用的安全兜底计划。

### 3. 健康周报 + 数据图表
- 新增 `pages/report/index`。
- 后端 `/api/v1/insights/weekly-report` 汇总最近 7 天饮水、睡眠、步数、运动、热量记录与蛋白质。
- 新增健康节奏分数、四项周均指标、运动柱状趋势、饮食记录趋势、周度洞察。

### 4. AI 对话流式输出
- 新增 `/api/v1/chat/stream` NDJSON 流式接口。
- DeepSeek 使用 `stream=true` 接收增量 token。
- 小程序端使用 `wx.request({ enableChunked: true })` 逐块更新回答。
- 对低版本基础库保留普通 `/chat` 自动回退。

### 5. 首次使用引导
- 新增 `pages/onboarding/index` 三屏引导。
- 解释产品价值：统一健康首页、拍照/AI/周报、数据与 API Key 控制。
- 首次进入展示，完成后本地记忆 `onboarding_v3`。

### 6. 视频 / 图片上传能力
- 新增 `pages/media/index`。
- 支持相册和相机选择图片或 60 秒以内视频。
- 后端 `/api/v1/media/upload` 支持 MP4 / MOV / JPG / PNG / WebP，单文件限制 50MB。
- 当前完成基础上传与素材入库接口；动作关键帧、姿态评分与视频纠错作为下一阶段接口方向。

### 7. 首页视觉系统重做
- 从“普通卡片列表”改为健康 Dashboard / Bento Grid。
- 新增 Today Health Pulse 主视觉、AI 快捷入口、数据进度、HealthMate AI 卡片和快速记录区。
- 使用森林绿 + 酸橙绿 + 柔和蓝 / 沙色的克制健康科技配色。
- 强化“下一步行动”而不是把首页变成指标墙。

## 后端新增接口

- `POST /api/v1/vision/food-analysis`
- `POST /api/v1/chat/stream`
- `POST /api/v1/media/upload`
- `GET /api/v1/insights/weekly-report`
- `POST /api/v1/insights/workout-plan`

## 运行注意

1. AI 识餐需要 DeepSeek API Key，可由用户在“小程序 -> AI 设置”配置，或后端通过 `DEEPSEEK_API_KEY` 配置。
2. 视觉识别固定调用 `deepseek-flash`，因为该模型支持图像输入。
3. 微信真机调用后端时需配置合法 HTTPS 域名；本地开发可在开发者工具中临时关闭域名校验。
4. `uploads/` 当前为本地开发存储，生产环境建议替换为 COS / OSS / S3 对象存储。
5. 视频当前完成上传与素材入口，尚未把“视频动作识别”伪装成已完成能力。
