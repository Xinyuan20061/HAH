# 食物视觉识别与校正闭环（v1.0 Competition Edition）

## 生产链路

`CloudBase Storage 图片 → MediaAsset → AIJob(food_vision) → 本地多模态 Worker → 结构化营养估算 → FoodAnalysisSession → 用户校正 → Action Registry 确认 → DietRecord → HealthTimeline`

生产小程序不会把图片 Base64 直接塞进云托管去做大模型视觉推理。图片先进入 CloudBase Storage，只把 `fileID` 与短期 HTTPS 地址注册到后端；本地 Worker 再通过受限下载器读取媒体并调用本机 OpenAI-compatible VLM 服务。

## 为什么保留“人工确认”

视觉模型对份量、隐藏油脂、烹饪方法和遮挡食材存在天然不确定性，因此 AI 输出是 **initial estimate**，不是营养真值。用户可以修改：

- 菜品名、份量、估算重量；
- 烹饪方式；
- 热量、蛋白质、碳水、脂肪、膳食纤维。

原始预测与人工校正分开保存，可用于比赛中的误差分析与迭代评测。

## 本地 VLM

`ai-worker` 默认通过 OpenAI-compatible `/v1/chat/completions` 调用本机视觉模型。模型服务不可用时，Worker 会在启动预检中自动移除 `food_vision` capability，避免领取无法完成的识餐任务。

可使用能够在本机显存范围内运行的量化视觉语言模型。模型名称与地址由 `ai-worker/.env` 配置，不写死在小程序或云端源码中。

## 安全边界

1. 营养数值为估算，不用于疾病诊断或治疗决策；
2. 最终写入饮食记录前必须经过用户确认；
3. Worker 只持有独立 `WORKER_TOKEN`，不持有用户 JWT、数据库密码或微信 AppSecret；
4. 媒体下载器限制大小并校验 URL/重定向目标，避免把 Worker 变成任意 URL 抓取器。

## 旧接口

POST /api/v1/vision/food-analysis 保留迁移提示：production返回409，本地旧直接视觉服务返回501，不能用DeepSeek文本模型替代视觉。当前开发和生产都使用media/food-jobs+本机Worker。VLM真实推理本轮未运行，离线管线与校正/确认协议已验证。
