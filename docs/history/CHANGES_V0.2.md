# HealthMate v0.2.0 Product Beta 变更清单

## 本次直接可用的新功能

1. 首页重构：今日生活完成度、计划进度、饮水/睡眠/运动/蛋白质卡片、AI 今日建议、快速入口。
2. 健康打卡：饮水量、睡眠、体重、步数、心情；每日一条，重复保存为更新。
3. 7 日趋势：饮水、睡眠、运动、步数平均值与每日进度，展示体重数据。
4. 今日计划：3 项任务可勾选完成，后端按用户和日期持久化。
5. 饮食记录：快捷模板、连续新增、最近记录、删除。
6. 运动记录：快捷模板、连续新增、最近记录、删除。
7. AI 聊天升级：显示实际 Provider，并可从聊天页进入模型设置。
8. 用户 DeepSeek：用户自己的 API Key / Base URL / Model / 启停 / 测试连接 / 恢复系统默认。
9. Key 安全：服务端加密落库、查询不返回明文、Base URL 做基础 SSRF 防护。
10. Provider 回退：用户 DeepSeek → 系统 DeepSeek → Demo。

## 新增核心文件

- `backend/app/core/crypto.py`
- `backend/app/api/v1/ai_config.py`
- `backend/app/schemas/ai_config.py`
- `backend/app/schemas/health_extra.py`
- `backend/migrations/versions/0002_product_features.py`
- `miniprogram/pages/checkin/*`
- `miniprogram/pages/trends/*`
- `miniprogram/pages/settings/ai/*`

## 验证结果

- `pytest`: passed
- FastAPI 功能冒烟测试: passed
- Python compileall: passed
- 小程序 JSON parse: passed
- 小程序 JS `node --check`: passed
- Alembic `0001 -> 0002`: passed

## 正式上线前仍应完成

- 配置强随机 `SECRET_KEY`，不使用默认值。
- 微信合法域名 + HTTPS 后端部署。
- 接入生产数据库与备份策略。
- 增加 API 限流、敏感日志脱敏、异常监控。
- 若开放任意自定义 Base URL，进一步增加 DNS/出口网络级 SSRF 防护；也可生产环境仅白名单 DeepSeek 官方域名。
- 健康与医疗免责声明、隐私政策、用户协议按上线地区合规要求完善。
