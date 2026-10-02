HISTORICAL: 本文是 2026-09 审计轮的修复记录，保留当时的迁移号（如 0016_food_item_evidence）作为历史事实，不作为当前 head 或已上线证明。当前 head 以 `alembic heads` 命令结果为准。

# HealthMate 审计与修复记录

## 初始问题清单（2026-09-18，修复前）

|级别|问题|根因 / 风险|
|---|---|---|
|P0|Docker 每次启动执行迁移|扩容实例争抢 DDL；数据库故障引发循环启动|
|P0|配置分散，缺失独立凭据加密密钥仍可运行|生产默认值、JWT 密钥复用；错误提示不集中|
|P0|迁移 TEXT DEFAULT|MySQL 5.7 和部分 8.0 语法拒绝，SQLite 测试无法发现|
|P0|Worker 无后台心跳/续租|下载和 VLM 请求期间租约过期，结果丢失|
|P0|临时媒体 URL 失效永久 failed|稳定 fileID 仍有效却不能原地恢复任务|
|P0|Worker OpenCV/MediaPipe 依赖冲突|NumPy 2 与 NumPy <2 冲突；缺少 MediaPipe 时导入崩溃|
|P1|VLM 用 HTTP <500 判断在线|401/404、模型未加载仍注册能力|
|P1|食物解析宽松、原图直接发送|无效数值被静默置零，字符串 tips 被拆成字符，资源浪费|
|P1|动作共用膝角规则、均匀关键帧|俯卧撑和弓步没有独立指标，关键帧没有动作事件证据|
|P1|登录/AI 错误与降级不完整|固定 Demo 回复可能被误解成 AI 完成，URL 校验无 DNS 检查|
|P1|CloudBase 注册缺少严格归属/类型与并发处理|客户端可提交其他用户的 fileID；storage_key 长度不一致|
|P1|隐私删除成功判定过宽|未确认每个云文件删除，界面可能宣称原文件已删除|
|P2|健康检查连接未关闭、UTC 弃用调用|连接泄漏、API 时间缺少 UTC 标识|
|P2|现有验收文档仅 10 个测试|缺少 API 闭环、任务竞态、下载和 VLM 离线验证|

本目录没有 `.git` 元数据；本轮按当前文件审计，无法检查历史提交中的秘密。

修复明细、迁移影响和实测结果如下；实际执行记录见 VERIFICATION.md。


## 修复明细

|级别|原问题 / 根因|实际修复|主要文件|数据迁移影响 / 验证|
|---|---|---|---|---|
|P0|生产可能误带 SQLite/local 默认值|集中 production 校验，必须显式 MySQL PyMySQL/cloud_ref；缺失字段预检非零，导入不建立 SQLite fallback；摘要不含密钥|backend/app/core/config.py、database.py、scripts/preflight.py|无 schema 变化；配置正反例、真实预检、Docker production fixture|
|P0|每次 Web 启动跑 DDL、固定端口|entrypoint 预检后只启动 API，显式开关默认 false，读 PORT；独立 migrate 服务|backend/entrypoint.sh、Dockerfile、docker-compose.yml|迁移由单次受控命令运行；实测 PORT=8091、API 重启不跑迁移|
|P0|健康探针混入外部 AI / 未关闭 DB 连接|live 无外部访问；ready 必须 DB 连通且 Alembic head；pool_pre_ping/recycle/timeout/utf8mb4|backend/app/main.py、core/database.py、diagnostics.py|实测 MySQL 停止 live200/ready503，重启恢复200；VLM OFF 不影响 ready|
|P0|SQLite 放过 TEXT DEFAULT / MySQL alter 差异|检查 0001–0008；0002/0007 去掉 TEXT DEFAULT；source_url 先加可空并回填；0008 result_json MEDIUMTEXT|backend/migrations/versions/、migrations/env.py|新 MySQL8.4、0003旧行增量、重复 head；新 SQLite/增量均实测；env finally dispose 修复 Windows SQLite 文件锁|
|P0|长任务 lease 到期丢结果 / claim 竞态|CAS UPDATE 领取，唯一 claim_request_id 防丢响应重复领取；后台续租覆盖下载/阻塞推理/完成；过期恢复、有限退避、旧 lease409|backend/app/services/ai_jobs.py、api/v1/worker.py、ai-worker/worker.py、client.py|0008新增 nullable 去重/领取键、重试时间/索引；测试四 Worker 竞争同一任务，长推理续租和超时恢复|
|P0|临时 URL 过期变永久失败|waiting_source_refresh；稳定 fileID 注册幂等；刷新同一媒体后恢复同一任务，URL 下载失败不消耗推理次数|backend/app/api/v1/media.py、services/ai_jobs.py、miniprogram/utils/jobPolling.js、cloudMedia.js|0008恢复旧 URL 失败任务；原 jobID 重用、刷新次数有上限，SQLite/MySQL 测试|
|P0|Worker 在 Windows 安装/运行失败|NumPy1.26.4+contrib OpenCV4.11+MediaPipe0.10.21；lazy heavy import，真实 Pose 探测；英文 venv 路径说明|ai-worker/requirements.txt、healthmate_worker/capabilities.py、doctor.py|无 DB 变化；英文路径 Python3.12.14 安装、pip check、真实 Pose/视频 smoke|
|P1|VLM401/404/模型不存在当 online|严格 2xx + data[].id + 非空精确模型ID；food 和 motion 独立；可选真实图像 smoke|ai-worker/healthmate_worker/capabilities.py、doctor.py、config.py、.env.example|401/404/空ID/缺失模型/pose缺失等离线测试；本轮真实VLM未运行|
|P1|原图大、JSON贪婪、无效字段置零|Pillow校验/EXIF/RGB/缩放/压缩；嵌套JSON解码拒绝歧义；共享严格食品schema、有限非负数值、估算/低置信度提示|ai-worker/healthmate_worker/processors/food.py、results.py、backend/app/schemas/ai_results.py|共享schema一致性测试；预处理/解析/非法数值/超时/OOM/鉴权测试；完成结果用户校正/确认保存闭环|
|P1|三种动作共用深蹲规则 / 均匀关键帧|Squat/Pushup/Lunge 独立 analyzer，阈值集中；周期迟滞、遮挡打断；真实事件时间/角度/confidence，关键帧无虚假URL|ai-worker/healthmate_worker/processors/analyzers.py、motion.py、miniprogram/pages/media/|无 schema 变化；三类周期/不同指标/低可见度/缺完整周期单测；真实HTTP→Worker→MediaPipe→done 静态视频测试|
|P1|云媒体归属/类型/长度与重复注册风险|当前用户/环境目录校验、腾讯域名/路径、size≥1与上限、extension/MIME分类；唯一键/事务处理；storage_key扩大700|backend/app/api/v1/media.py、models/platform.py、0008迁移|保留旧媒体，700字符UTF8MB4键约2800字节（MySQL8.4通过；旧5.7需DYNAMIC/大索引支持）；幂等/跨用户拒绝测试|
|P1|SSRF只看字符串、DNS重绑定/重定向|下载校验所有DNS地址非私网/保留/多播；固定IP保留Host/SNI和SSL、每跳验证、限大小；DeepSeek生产URL同类保护|ai-worker/security.py、downloader.py、backend/core/url_security.py、services/ai/gateway.py|不变更DB；私网DNS/重定向/实际字节限制/SSL路径测试，真实公共HTTPS样本下载|
|P1|AI缺配置用固定Demo当完成 / 失败影响业务|删除Demo提供者，普通对话明确503；规则周报/计划标记degraded/provider；非流式默认可靠，stream先获得完整上游再发送NDJSON|backend/services/ai/gateway.py、api/v1/chat.py、insights.py、services/agent/orchestrator.py、miniprogram/utils/request.js|无 schema；401/429/500的统一安全错误与普通记录仍可用测试；未调用真实DeepSeek|
|P1|JWT无效subject/expiry报500、微信异常不清晰|无效/过期/非法subject统一401；生产dev/header登录禁用；微信code接口异常安全503|backend/core/security.py、api/v1/auth.py、deps.py|鉴权测试与测试传输的code→JWT→资料；真实微信未验收|
|P1|凭据密钥复用、解密失败被静默吞掉|生产要求独立encryption key，Fernet密文，接口只返回提示；解密失败明确503可重新填Key；URL防SSRF|backend/core/crypto.py、api/v1/ai_config.py、services/ai/gateway.py|不更换现有加密算法；不要轮换原key而不迁移密文；错误密钥及普通业务隔离测试|
|P1|polling无限/缓存陈旧/离开页面丢任务|300秒最大时间、1–5秒退避、有限刷新、unload取消；按用户/API保存job指针而非签名URL；恢复时获取已校正识餐结果|miniprogram/utils/request.js、jobPolling.js、pendingJobs.js、pages/scan/index.js、pages/media/index.js|缓存不是生产事实源；JS9测试和30文件语法；待真机UI验证|
|P1|网络自动重试非幂等POST产生重复记录|仅GET和媒体/任务幂等端点重试；上传不做失败自动重传、401只重登录一次|小程序 utils/request.js|后端去重键；网络回执丢失仍需查看记录后手动重试，不能无条件再次POST|
|P1|识餐重复确认、删除饮食FK失败|finalize行锁+CAS状态保证幂等，confirmed默认false；删除饮食先解除识餐引用并标记record_deleted|backend/app/api/v1/vision.py、records.py、schemas/vision.py|保留识餐历史且不能错误再次保存；MySQL外键/确认幂等/无显式确认不入库测试|
|P1|CloudBase删除回执缺失仍当成功|小程序逐fileID确认删除再DB清理，后端检查回执集合，失败保留账户；本地/对象删除失败明确503|miniprogram/utils/cloudMedia.js、pages/settings/privacy/、backend/app/api/v1/privacy.py、services/privacy.py|后端明确client_reported，不声称服务器已验证CloudBase；删除闭环/FK测试|
|P1|业务字段与MySQL约束不一致|资料/记录字符串上限与列宽一致，宏量营养素有有限上限，非法输入422而非MySQL写入500|backend/app/schemas/user.py、records.py、ai_config.py、auth.py|无迁移；超长字段/超大数值API测试在SQLite/MySQL均通过|
|P2|UTC弃用与时区混用|统一utc_now/naive_utc/utc_iso，DATETIME仍存UTC，输出Z；健康日按北京时间边界聚合|backend/app/core/time.py、models/、schemas/、services/|无整库时间类型迁移；北京时间跨日、offset输入、租约/URL/heartbeat测试；无业务utcnow调用|
|P2|连接泄漏、难读一行代码、敏感日志|CloudAPI context/close、下载finally删除；格式化现有Python，request_id/安全header；禁httpx INFO URL日志|backend/app/main.py、core/errors.py、ai-worker/client.py、worker.py|Ruff语法/未定义变量检查、compileall；不是框架迁移|
|P2|未验证部署被文档声称完成|重写README A–H/部署/Worker/验证；上线清单将真实云端项留给人工验证|README.md、docs/|完整实际结果见VERIFICATION；没有Git无法给提交级diff|

## 数据和升级边界

- 最新 revision `0016_food_item_evidence`；已有 Alembic 库直接 upgrade。未删除业务表/健康记录，新增饮食逐项证据字段；此前训练意图、动作语义、数据集登记和模型治理表继续保留。
- 已创建的历史 AIJob 新去重键为 NULL；新请求用规范化payload生成去重键，已有旧payload格式/重复任务不自动合并。不要宣称去重迁移清理了所有历史脏数据。
- 0008 downgrade 移除新键/调度字段，但不缩小已扩展字符串/MEDIUMTEXT，避免截断；回滚应用前请备份并审查兼容性，不把 downgrade 当成无风险恢复。
- 无版本表的旧 create_all 数据库不能凭表名猜版本；adopt脚本仅接受开发SQLite精确结构匹配（columns/nullability/FK/unique/index）。不匹配时拒绝，人工比较备份，不会自动清库。
- 本轮新旧数据验证使用专用本地审计库/临时数据库，未清空用户已有业务库。

## 当前明确 deferred 的能力

|能力|当前真实状态|后续需要|
|---|---|---|
|真实微信登录/CloudBase上传/云托管发布|接口实现，测试传输和本地Docker已验证；缺项目凭据，未现场发布|由拥有者填资源和权限，真机验收|
|真实VLM餐食推理/营养准确率|管线和校正协议测试通过；本地VLM未运行|加载真实视觉模型，极小图像smoke后用人工标注餐食评估|
|三种动作真实视频准确率|独立analyzer单测与静态姿态真实推理通过|不同机位/遮挡真实运动视频和人工rep标签|
|事件关键帧长期对象存储|已有最多4张、单张80KB、脸部模糊的任务内预览，默认7天清图并降级骨骼画布|若需要长期高清留存，再接下述受控artifact存储|
|自动服务端云文件删除证明/孤立文件对账|客户端删除回执协调，无管理SDK凭据|有授权后接平台文件查询/删除并对账，不能用客户端声称当证明|
|真正上游token流式聊天|默认非流式；/stream缓冲后输出NDJSON|平台分块链路真机验证后再开启|
|MySQL5.7/8.0、Python3.11|语法/支持目标审计，未运行这些版本|按最终部署版本额外验收；实测为MySQL8.4/Python3.12Worker|
|第三方Starlette/AnyIO弃用warning|1条第三方TestClient告警，不来自业务UTC调用|后续依赖升级中处理，未抑制告警或改框架|

生产代码（backend/app、migrations、Worker包、小程序）TODO/FIXME扫描无未完成标记。上述 deferred 是明确工程范围，不会以固定数据填充功能。

## 事件图片长期存储设计（deferred）

当前比赛预览采用任务内受限 JPEG：后端验证 worker_id/lease、Base64、MIME、大小与摘要，最多4张，并在7天后去除图片字段；事件表不重复存图。若后续需要长期高清证据，API 应签发短时、单用途的 CloudBase/COS 上传授权，限制 `healthmate/u<id>/artifacts/<job>/<event>.jpg`、大小和MIME；Worker直传JPEG，API使用受支持的管理接口确认对象路径/大小/摘要，再写小型artifact元数据，读取时生成短期URL。不得把永久bucket密钥发给Worker，也不接受任意客户端URL作为“已上传”的证明。
