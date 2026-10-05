# Knowledge RAG 双模式评测（冻结，可复现）

## 目录

```
knowledge-rag/
├── run_dual_backend_evaluation.py   # 双模式评测（隔离 SQLite 子进程，永不触碰生产库）
└── results/seed-20261005/
    ├── dual_backend_report.json     # 两种后端的 Hit@1 / Hit@3 / 无关拒绝
    └── summary.json                 # 汇总 + query_hash
```

## 复现

```bash
cd backend
set PYTHONPATH=%CD%
.venv\Scripts\python.exe ..\benchmark\knowledge-rag\run_dual_backend_evaluation.py
```

**关键护栏**：
- 脚本以子进程 + `DATABASE_URL/ENV=test` 环境变量隔离运行，绝不触碰生产库（事故教训见 P3 文档）。
- 两种后端必须在同进程分别建库评测；第二次评测前先 `embeddings.embed.cache_clear()`，否则会命中第一模式的 ONNX 向量得到假分数。

## 冻结事实（2026-10-05）

- 活跃知识 13 条、61 条冻结查询，query_hash=`c77f61e673ce2fb70378c08290b773670aff7a7cbcfc85fbfdd526d714537460`
- ONNX (onnx_bge)：Hit@1=90.2% / Hit@3=100% / 无关拒绝=100%
- 哈希后备 (hash_fallback)：Hit@1=82.35% / Hit@3=98.04% / 无关拒绝=100%

## 声明边界

- 后备降级是**真实能力差**，任何对外展示都不得引用 ONNX 成绩顶替哈希后备成绩。
- 检索质量 ≠ 最终回答质量：最终回答需双人盲评（人工步骤，未完成）。
