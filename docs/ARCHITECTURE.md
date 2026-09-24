# HealthMate 当前架构

微信小程序通过callContainer访问微信云托管FastAPI，登录使用一次性wx.login code换openid再签发JWT。公网入口供本机Worker主动轮询；CLOUD_HEADER_LOGIN_ENABLED=false，不能信任公网身份头。

在线数据库为持久MySQL，生产storage=cloud_ref。小程序直传CloudBase，后端持久稳定fileID和临时URL；容器没有原媒体持久目录。DeepSeek负责文本对话/计划/摘要，也可由 Worker 作为识餐视觉 provider；图片会先校验、转正和压缩，Key仅通过 Worker 环境变量或后端密文配置使用。

AIJob状态：queued → processing → done/failed。签名URL缺失/过期进入waiting_source_refresh，原媒体刷新后同任务重新queued。领取使用CAS UPDATE + requestID幂等，租约失效恢复queued或达到尝试上限failed；可重试错误带next_attempt_at退避。后台heartbeat和续租覆盖阻塞推理。

本机Worker真实探测OpenCV/MediaPipe，姿态在CPU上运行；`VLM_PROVIDER` 可显式选择本地 OpenAI-compatible VLM 或 DeepSeek 视觉服务。三动作独立 analyzer 输出二维启发指标、动作评分、事件时刻和匿名骨骼坐标；最多四张事件预览在本机模糊脸部、叠加骨骼并压缩，后端校验摘要与大小，默认7天后清除图片，只保留结构化事件。评分写入历史后聚合为30日动作画像并进入Agent只读上下文。VLM结果按逐食材证据校验和汇总，用户校正、确认后才入库。

Web默认启动不执行DDL。独立Alembic迁移到0016_food_item_evidence。live不访问外部；ready只要求数据库连通且迁移head，不依赖Worker/DeepSeek。UTC DATETIME保留，API输出Z，健康日按北京时间。

## 权威知识检索

`knowledge_documents` 保存人工审核的知识摘要及标题、机构、来源URL、发布日期、章节、标签和审核时间。Agent先通过 `audited_lexical_v1` 检索得到K1/K2片段，再将片段连同结构化用户事实交给模型；来源URL不进入模型输出链路，而是由API直接返回给小程序展示。当前版本是可解释的词法RAG，不声称已经使用Embedding或向量数据库。

## 动态训练计划约束

Agent工具层先从今日打卡、近7天记录和30天动作画像生成 `training_adjustment`。模型可据此生成建议，但最终计划还要经过确定性Guardrail：恢复优先时替换当日训练、限制单次时长，并将有足够样本支持的动作薄弱项加入训练说明。低质量或不足样本只降低个性化置信度，不用于推断疲劳、损伤或能力下降。所有计划仍须用户确认后经Action Registry写入。

隐私删除使用前端CloudBase逐文件删除回执后DB清理；后端client_reported不是平台删除证明。文件权限必须仅创建者读写，历史孤立文件对账和服务端管理删除尚未实现。

安装部署见README A–H、WECHAT_CLOUD_RUN_DEPLOY、LOCAL_AI_WORKER；数据迁移/限制见FIX_REPORT；真实测试见VERIFICATION。docs/history保留历史记录，不作为当前架构或已上线证明。
