# 当前开发入口

以根目录README的A–H和backend/.env.example、ai-worker/.env.example为准。

后端先preflight，再独立alembic upgrade head，最后启动Uvicorn；本地development/test可SQLite，production必须MySQL/cloud_ref。Worker推荐Python3.12英文路径venv，不复用中文路径或3.13/3.14环境。

离线检查：backend pytest -q、ai-worker pytest -q、node --test miniprogram/tests/*.test.js、backend/scripts/verify_repository.py、compileall。真实Docker/MySQL与运动协议smoke命令和隔离要求见VERIFICATION.md。

前端改动前先跑 UI 审计（死 CSS、跨页类名、缺过渡、旧强调色、被禁用的浏览器 API），改完重跑：`node scripts/audit_miniprogram_ui.mjs`（`--page=<名>` 只看一页，`--strict` 有 HIGH 问题退出码 1，`--json` 机器可读）。小程序交互动画规范、色板 token 与现有动画清单见 skill `.agents/skills/healthmate-motion/`；动效观感必须在微信开发者工具中肉眼确认，Node 测试不能替代。

不提交.env/db/uploads/models/日志；不要在小程序存Key。没有VLM时明确识餐OFF，不用固定假结果替代。旧迭代文档位于history/，不覆盖当前部署约束。
