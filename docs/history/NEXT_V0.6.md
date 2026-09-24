# v0.6 建议实施顺序

1. **HealthTimeline 完整化**：睡眠、饮水、步数、体重、目标变更、计划完成、视频训练结果全部事件化；补每日聚合表，避免周报每次扫明细。
2. **Health Agent 工具层**：`get_profile / get_timeline / get_goals / create_week_plan / complete_task`，LLM 只做意图理解与解释，写操作必须经过工具 schema。
3. **动态目标引擎**：按 7/14/30 天完成率、连续完成天数和失败原因做确定性调整；所有调整保存 before/after/reason。
4. **识餐闭环**：识别结果支持菜名、重量、份量、烹饪方式人工修正；保存 `ai_prediction + user_correction`，为后续置信度标定和数据集积累服务。
5. **周报事实层**：建立 `WeeklyHealthFacts`，程序计算同比/环比、完成率和 streak，再把 JSON 交给 DeepSeek 解释。
6. **动作 v2**：增加正面/侧面视角识别、膝内扣、标准动作时序模板、DTW 相似度、关键点抖动滤波与置信度。
7. **量化评测**：建立 `benchmark/`，至少记录关键点可用率、计数 MAE、错误检测 precision/recall、P50/P95 视频耗时、AI TTFT/总耗时、识餐人工修正率。
8. **隐私安全**：数据导出/删除、API Key 脱敏、日志 redaction、上传文件生命周期、AI 医疗边界与高风险输入分流。
