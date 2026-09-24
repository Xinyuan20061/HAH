# 当前开发入口

以根目录README的A–H和backend/.env.example、ai-worker/.env.example为准。

后端先preflight，再独立alembic upgrade head，最后启动Uvicorn；本地development/test可SQLite，production必须MySQL/cloud_ref。Worker推荐Python3.12英文路径venv，不复用中文路径或3.13/3.14环境。

离线检查：backend pytest -q、ai-worker pytest -q、node --test miniprogram/tests/jobPolling.test.js、backend/scripts/verify_repository.py、compileall。真实Docker/MySQL与运动协议smoke命令和隔离要求见VERIFICATION.md。

不提交.env/db/uploads/models/日志；不要在小程序存Key。没有VLM时明确识餐OFF，不用固定假结果替代。旧迭代文档位于history/，不覆盖当前部署约束。
