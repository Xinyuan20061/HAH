<p align="center">
  <img src="miniprogram/assets/icons/spark.png" alt="HealthMate" width="96">
</p>

<h1 align="center">HealthMate</h1>

<p align="center"><strong>懂你记录的微信健康助手</strong></p>

<p align="center">
  <a href="#运行预览">运行预览</a> ·
  <a href="#能做什么">能做什么</a> ·
  <a href="#如何使用">如何使用</a> ·
  <a href="#安装与启动">安装与启动</a>
</p>

HealthMate 是一款微信小程序健康助手。你只要随手记录每天的饮食、运动和身体状态，
它就会帮你把能量收支、变化趋势和健康建议整理清楚，让每一次调整都有据可依。

## 运行预览

<table align="center">
  <tr>
    <td align="center"><img src="docs/assets/screens/home.png" width="170" alt="首页"><br><sub>首页 · 坚持打卡</sub></td>
    <td align="center"><img src="docs/assets/screens/chat.png" width="170" alt="对话"><br><sub>对话 · 智能问答</sub></td>
    <td align="center"><img src="docs/assets/screens/records.png" width="170" alt="记录"><br><sub>记录 · 能量收支</sub></td>
    <td align="center"><img src="docs/assets/screens/profile.png" width="170" alt="我的"><br><sub>我的 · 健康档案</sub></td>
  </tr>
</table>

## 能做什么

| 功能 | 说明 |
| --- | --- |
| 拍照识餐 | 拍下餐食，自动估算热量并记入当天 |
| 记录运动 | 记录训练和身体状态，跟踪每天消耗 |
| 能量仪表盘 | 一眼看清每天吃进多少、消耗多少 |
| 智能对话 | 有疑问直接问 HealthMate，结合你的真实记录给建议 |
| 主动提醒 | 运动断档、睡眠不足、体重上升时主动提醒你 |
| 目标与打卡 | 设定目标、每日打卡，看见坚持 |

## 如何使用

1. 在微信中打开 HealthMate 小程序，授权登录；
2. 完善健康档案：填写年龄、身高、体重，选择你的目标（减脂 / 保持 / 增肌）；
3. 吃饭时拍照识餐，运动后随手记录；
4. 在「记录」页查看能量收支和七日趋势；
5. 有健康疑问，在「对话」页直接问 HealthMate；
6. 跟着建议慢慢调整，别忘了打卡坚持。

## 安装与启动

本项目由三部分组成：微信小程序、后端服务（FastAPI）、可选的本地 AI 识别 Worker。
本地运行需要 Python 3.12、Node.js 和微信开发者工具。

**1. 启动后端**

```powershell
git clone https://github.com/hauyer/health-assistant.git
cd health-assistant/backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

本地开发默认用 SQLite，在 `.env` 中填 `ENV=development`、`DATABASE_URL=sqlite:///./healthmate.db`。

**2. 打开小程序**

用微信开发者工具导入仓库根目录（`project.config.json` 已指向 `miniprogram/`），填入你的 AppID，
并在 `miniprogram/config/index.js` 中配置后端地址。

**3. 部署上线**

生产环境通过微信云托管部署，构建目录为 `backend/`；完整部署与本地 AI Worker 配置见 `docs/` 目录。

> 健康建议不替代医生诊断。
