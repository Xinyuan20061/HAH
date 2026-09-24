# HealthMate v0.4.0 — Motion & Insight Upgrade

## 1. 启动白屏 / onboarding 页面不存在修复

本版本不再通过 `home -> redirectTo('/pages/onboarding/index')` 跳转到独立引导页。

首次使用引导改为 **首页内置 bottom-sheet 引导层**，因此即使开发者工具执行增量编译、忽略未引用文件或导入目录方式不同，也不会再出现：

`/pages/onboarding/index.wxml not found`

同时将 `project.private.config.json -> setting.ignoreDevUnusedFiles` 设为 `false`，避免开发阶段因为增量裁剪造成页面资源缺失。

## 2. 新增功能

- 动作视频关键帧分析
  - 上传 MP4 / MOV
  - FFmpeg / FFprobe 获取视频时长
  - 5–6 个关键帧抽取
  - Pillow 灰度帧差计算动作变化强度
  - 时间轴、峰值变化、关键帧预览
- 首页微动效
  - 健康评分呼吸动效
  - 快捷入口浮动
  - 状态点 ping 动效
  - 首次进入 sheet 动画
- 骨架屏
  - 首页
  - 7 日趋势
  - 健康周报
  - 健康目标
- 拍照识餐扫描动画
  - 动态扫描线
  - AI 分析状态浮层
- 训练动作详情
  - 动作步骤
  - 目标肌群
  - 呼吸节奏
  - 常见错误
  - 安全提示
- 周报 AI 总结
  - `POST /api/v1/insights/weekly-report/ai-summary`
  - DeepSeek 可用时生成结构化总结
  - 无模型配置时仍有 deterministic fallback
- 连续打卡 Streak
  - 当前连续天数
  - 最长连续天数
  - 最近 7 天点亮状态
- 健康目标体系
  - 饮水 / 睡眠 / 运动 / 步数 / 蛋白质 / 热量 / 每周打卡目标
  - 保存后同步首页进度与周报计算
- Canvas 交互图表
  - 自研 Canvas 2D 折线图
  - 多指标切换
  - 触摸吸附最近数据点
  - 动态 tooltip 与辅助线
  - Retina / DPR 适配

## 3. 后端新增依赖

- `Pillow==11.3.0`
- Docker 镜像安装 `ffmpeg`

如果不使用 Docker，本机后端需要能在 PATH 中找到：

```bash
ffmpeg
ffprobe
```

## 4. 新增 API

- `GET /api/v1/health/streak`
- `GET /api/v1/health/goals`
- `PUT /api/v1/health/goals`
- `POST /api/v1/media/analyze-motion`
- `POST /api/v1/insights/weekly-report/ai-summary`

## 5. 数据库

新增 `health_goal_settings` 表，迁移：

```bash
cd backend
python -m alembic upgrade head
```

开发环境中 `Base.metadata.create_all()` 也可自动创建新增表，但正式环境仍建议执行 Alembic 迁移。
