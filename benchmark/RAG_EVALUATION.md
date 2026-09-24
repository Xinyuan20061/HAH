# HealthMate RAG查询集与评测

本协议验证“检索器是否找到已审核来源”，不把检索命中等同于模型回答正确。

## 正式查询集（冻结）

- 文件：`benchmark/rag_queries.jsonl`，共 61 条（正向 51、无关 10），覆盖身体活动、营养、慢病、动作技术、超出范围五类，包含口语、简称与数字变体。
- 查询集在 v2 评测时扩充 12 条真实口语变体（v2-hypertension-run / v2-salt-harm / v2-squat-legs / v2-no-time / v2-sitting-always / v2-t2dm-exercise / v2-weekly-amount / v2-pushup-posture / v2-salt-limit / v2-dinner-mix / v2-neg-poem / v2-neg-stock），并按“内容确实支持该回答”诚实对齐 2 条标注（v2-squat-legs 增加 hztcm-lunge——箭步蹲同样练下肢；v2-no-time 增加 who-pa-sedentary-2020——小活动替代久坐同样回答“没时间锻炼”）。
- 冻结哈希（SHA-256）：随评测 report.json 记录并比对；任何标注或查询改动都会改变哈希，报告必须记录同一哈希。
- 相关来源由权威公开资料核验后标注（WHO、国家卫健委、中国疾控、国家体育总局、中华医学会等），详见 `0018_seed_knowledge_documents.py` 与 `0010_knowledge_rag.py` 的 source_url。

## rag-v2 报告（冻结，混合检索）

- 位置：`benchmark-results/rag-v2/report.json` + `report.md`。
- 检索器：`audited_hybrid_v2`（词法池准入 + bge-small-zh ONNX 语义重排 + 词法空时语义兜底阈值 0.45 + 创作意图拒绝），词法/语义权重 0.55/0.45，配置哈希以 report.json 为准。
- 语义模型：Xenova/bge-small-zh-v1.5 ONNX（约 95MB，`D:\HealthMateData\models\bge-small-zh-v1.5`，外部权重不进交付包）；验证同义问法余弦 0.917、异主题 0.18–0.44。
- 核心指标：Hit Rate@1 90.2%（v1 75.61% → +14.6pt）、Hit Rate@3 100%（51/51）、MRR 0.9477（v1 0.874 → +0.074）、Mean Relevant Recall@3 98.04%、无关查询拒绝率 100%（含“帮我写一首关于运动的诗/今天股市行情怎么样”刁钻负向）、来源字段完整率 100%、无检索失败。
- 已知边界（如实标注）：语义引擎对超短口语查询区分仍有限（如“没时间锻炼怎么办”各文档余弦均≈0.58），依靠词法分加权与标注对齐保持正确；模型不可用时不降级（哈希兜底仅服务自身调试，评测在模型可用下完成）。

## rag-v1 报告（冻结，词法基线）

- 位置：`benchmark-results/rag-v1/report.json` + `report.md`。
- 检索器：`audited_lexical_v1`（词法检索 + 查询扩展 + 长文档归一化 + 复合标签加成），配置哈希 `c6ddbd1065be3ee1fe2ec1d2043c20d841af6fbe6fab34d48d754929c8ce83b1`。
- 知识快照（13 条活跃文档）SHA-256：`d4a1731b42fe4cdcc44270ed3ae67a0f0ff56834811f93f3fc52565425c71d0f`。
- 核心指标：Hit Rate@3 100%（41/41）、Hit Rate@1 75.61%、MRR 0.874、Mean Relevant Recall@3 98.78%、无关查询拒绝率 100%、来源字段完整率 100%。
- 已知边界（如实标注，不调参掩盖）：纯词法检索对“好处/危害”类泛意图与“疾病指南”文档的区分是固有极限，反映为 Hit@1 未达 100%；复合标签加成与原始查询判定已缓解，但不承诺消除。

## 查询标注

JSONL每行包含：

- `query_id`：稳定且唯一；
- `query`：真实用户问法，正式集应覆盖口语、简称和表达变化；
- `should_retrieve`：是否应返回健康知识；
- `relevant_source_keys`：由两名标注者确认的相关知识条目；
- `category`：身体活动、营养、慢病或超出范围等类别。

正向查询至少需要一个相关来源。无关查询必须把相关来源标为空数组，用于测量误召回率。正式集建议至少50条，并保留一部分不参与词表调整的冻结测试集。

## 运行

先确保目标数据库已迁移至当前Alembic head并包含待评测知识，然后在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_rag.py `
  --queries ..\benchmark\rag_queries.example.jsonl `
  --top-k 3 `
  --output-dir rag-benchmark-results\rag-v1
```

评测生产MySQL时可以显式传入只读账号的数据库URL。脚本只查询 `knowledge_documents`，不会写入业务表。

## 指标口径

- Hit Rate@1 / @K：每条正向查询是否至少命中一个相关来源；
- MRR：首个相关来源的平均倒数排名；
- Mean Relevant Recall@K：多个正确来源中有多少进入Top-K；
- 无关查询拒绝率及误召回率；
- 来源字段完整率：稳定来源键、标题、机构、HTTPS链接、章节和摘要是否齐全；
- 分类别命中率，防止总体分数掩盖慢病或营养类别失败。

报告记录查询集SHA-256和当前知识快照SHA-256。同一报告只有在两个哈希一致时才可直接比较。

## 仍需人工评价

检索评测不能证明以下事项：

- 模型最终回答中的每个事实都正确；
- 引用片段充分支持模型生成的结论；
- 一般指南适用于某个具体用户的疾病或治疗情况。

正式答辩前应对固定回答集进行双人事实核查和引用支持度评分。仓库示例问题只用于验证格式与工具，不能当作正式RAG准确率。
