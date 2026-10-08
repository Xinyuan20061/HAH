# Android 迁移验收记录（2026-10-07）

## 已完成的自动验收

| 检查 | 结果 |
| --- | --- |
| 后端完整测试 | 最新代码 SQLite 全量回归 `594 passed`（2301 条依赖/弃用告警）；临时独立 MySQL 8.0.45 全量回归 `590 passed`（2072 条告警）是在本轮用户文案改动之前，迁移 head 为 0044。动作提示定向回归 `22 passed`；任务错误提示、CloudFood 回退与 Worker 失败链路定向回归 `15 passed` |
| AI Worker 完整测试 | `279 passed`（2 条依赖弃用告警）；只验证本地测试，不代表线上 Worker 已部署或真实模型已联调 |
| 本轮媒体签名异常与存储定向回归 | `test_motion_previews_api.py`、`test_media_storage.py` 共 17 项通过；过期的异常类型导入回退已删除，路由现在直接依赖实际实现的签名异常类 |
| 本轮 SQLite 增量迁移回归 | `scripts/verify_migrations.py` 通过：在 0003 建立并填入旧用户与饮食记录后迁移到 `0044_motion_feedback_idempotency` head，重复升级不变，记录保留且 `PRAGMA foreign_key_check` 无违规。此前 SQLite 外键阻止 0041 批量重建 `users` 的问题已修复 |
| 本轮 MySQL 增量迁移回归 | `scripts/verify_migrations.py` 使用新建的临时 MySQL 8.0.45 实例通过：旧用户与饮食行从 0003 保留到当前 `0044`，重复 upgrade 后 head 不变。该临时服务已停止；未连接项目 `.env` 数据库。隔离测试目录仍保留在系统临时目录；仓库 Compose 目标为 MySQL 8.4，云端版本未知，因此目标版本验收仍待完成 |
| 本轮 MySQL 全量后端回归（用户文案改动前） | 临时独立 MySQL 8.0.45 上执行 `pytest tests -q`，当时源码 `590 passed`（2072 条依赖/弃用告警）。此前 6 个动作队列/识别链路失败来自无小数秒 `DATETIME` 对入队时间的舍入；按列精度规范化“立即可运行”与租约/重试截止时间后，旧版 582 项和当时 590 项全量回归均通过。实例已关闭。此结果不代表 Compose MySQL 8.4 或云数据库版本已验收 |
| 本轮 CloudBase 登录回归 | 使用锁定的 `@cloudbase/js-sdk` 3.10.1 结构验证 `ILoginState.user`；有效身份可上传并注册、身份不匹配时退出且拒绝写文件、退出后才返回的 Ticket 被丢弃、上传中切换账号会清理未登记对象 |
| 文案与评测定向回归 | 最后调整评测说明后，`test_agent_evaluation.py`、`test_agent_experiments.py`、`test_user_facing_copy.py` 共 18 项通过 |
| 本轮界面文案清理 | 通过；清理页面模板式英文眉题和工程说明，正式环境隐藏连接状态与环境标记；健康提醒无质量数据时不再显示只有标题的空卡片 |
| 动作分析结果文案回归 | `test_user_facing_copy.py`、`test_motion_unified_chain.py` 共 22 项通过；不确定结果不再显示“AI 视觉复核/本地候选”等内部术语，讲解不可用时显示简短状态与结果边界 |
| Worker 失败提示与诊断隔离 | `test_user_facing_copy.py`、`test_cloud_food.py`、`test_worker_fail_links_run.py` 共 15 项通过；客户端不再收到 Worker 原始错误文本，保留便于用户操作的任务提示 |
| COS/S3 上传、CloudBase 存储、可靠性定向回归 | `64 passed`；包含隔离区签名、文件头核验、重试、延迟清理和账号删除 |
| COS 部署配置预检 | 新增生产桶名 `BucketName-APPID` 与标准端点/`S3_REGION` 一致性检查；非法桶名（含非 ASCII 数字 APPID）和地域错配覆盖通过；`test_additional_safety.py` 与 `test_mobile_media_upload.py` 合计 `45 passed` |
| 移动端 Vitest | `51 passed`（19 个测试文件：单元、API 会话恢复、原生媒体/图片选择器、路由/API 契约与组件；覆盖首页、伙伴选择、按住录音→识别→Agent 对话、仪表盘全部五个主入口和八个快捷入口、账号关联状态/退出登录、计划确认、识餐保存重试、动作纠正幂等键、聊天停止后有/无 run_id 和取消超时的处理、首页语音切后台后的服务端取消，以及启动健康检查/恢复会话连接超时） |
| 识餐纠正与确认重试 | 通过；保存期间禁用估算输入；纠正成功而最终保存失败后，修改的数值会再次提交，未修改时不会重复写纠正；从服务端恢复已纠正草稿时保留用户输入的重量 |
| 动作纠正与重分析幂等 | 通过；相同请求键重试会复用同一分析子任务和反馈记录，改动后的请求内容返回 409；`motion_user_feedback` 的新幂等键列和唯一索引已通过 SQLite 旧数据升级验证 |
| Agent 完整响应模式 | 通过合同测试；`VITE_AGENT_RESPONSE_MODE=full` 只请求一次 `/agent/respond`，默认 NDJSON 模式继续使用 `/agent/respond/stream`；不会在流失败后自动重发请求 |
| Agent 请求停止与服务端取消 | 通过；聊天停止及首页语音切后台会立即 abort 当前连接；已收到 run_id 时异步 POST 取消并重读状态，取消请求失败仍会尝试读取；无 run_id 时不调用服务端取消、不自动重试。后端只能阻止未开始的后续阶段，已发出的模型请求无法撤回 |
| 移动端 ESLint / TypeScript 类型检查 | 通过 |
| Vite development 构建和 Capacitor Android 同步 | 通过 |
| Android 16 模拟器全路由冒烟 | 2026-10-08 最新常规配置 APK 连接独立 SQLite（迁移至 `0044`）和临时 FastAPI；28 条小程序页面与 1 条账号关联页共 29 条路由全部渲染，0 路由错配、0 JavaScript 异常。最新逐页截图、可见文案与报告保存在 [`../2026-10-08/route-smoke/`](../2026-10-08/route-smoke/)；此前验收包证据仍保留在 `route-smoke/` |
| 最新 debug 包联调 | 2026-10-08 最新常规包 SHA-256 `22fa53543daed98b860335487c16aaf1b40558e16b05f3bb6a3ba955b5456354` 已构建、校验、打包并安装至 Android 16 模拟器；设备内 APK 哈希与交付包一致。无本机 API 响应时，启动页约 12 秒后显示中文超时提示并允许重试，截图见 `screenshots/android16-latest-debug-2026-10-08.png`；随后独立 SQLite 服务的 `/health/ready` 为 `ok`，开发登录和 29/29 路由检查通过。本次只使用临时数据库；常规 API 此前报告 `migration_required`，没有修改其数据库 |
| 识餐保存修复后 Android 路由复验 | 此前通过；当时重建并安装 debug APK 后运行 29 条路由，29/29 渲染、0 路由错配、0 JavaScript 异常；已保留 29 张截图与可见文案 |
| Android 仪表盘导航实屏检查 | 通过；Android 16 模拟器实屏确认底部仪表盘展开五个主入口，快速开始面板展示八个操作；组件测试验证入口跳转，截图为 `screenshots/android16-dashboard-menu.png` 与 `screenshots/android16-dashboard-quick-actions.png` |
| 微信云托管 OpenAPI 合同核对 | 本地源码 11/11 操作通过；当前线上 2/11，缺少 9 个新增移动登录、身份关联和媒体会话操作。新增只读命令 `mobile/scripts/verify-cloud-contract.mjs`，线上部署后可重复验收 |
| Android Gradle `assembleDebug` | 通过 |
| Android 安全区启动处理 | 通过；Capacitor 使用系统原生 inset 并提示 `viewport-fit=cover`；最新 APK 在模拟器冷启动后不再输出安全区 CSS 注入错误 |
| Android app 前后台轮询门禁 | 单元测试通过；应用在后台时任务轮询等待，恢复前台后继续使用已保存的任务编号；页面销毁会移除监听。真实设备切后台恢复仍待验收 |
| Android 录音音频焦点 | 原生录音插件申请临时语音焦点；焦点丢失时释放录音并通知页面，前端可继续文字输入；原生构建通过，真机来电/其他应用打断仍待验收 |
| Android Gradle `lintDebug` | 通过；无 lint 错误，Gradle 报告仅含模板/依赖警告 |
| Android 模块设备仪器测试 | `:app:connectedDebugAndroidTest` 5 项通过；覆盖 debug 模拟器 HTTP 上传 URL 白名单、HTTPS、凭据/片段拒绝、包名和 Capacitor JSON 整数文件大小解析 |
| Android 根级设备仪器测试聚合 | 最新构建本轮再次执行 `connectedDebugAndroidTest` 通过，app 模块 5 项通过；根级任务聚合全部 Android 子模块完成。另 `lintDebug` 再次通过；Cordova 兼容模块的旧 Kotlin JDK7/JDK8 工件已统一到 Kotlin 1.8 兼容元数据版本 |
| 此前 Android 16 模拟器安装与运行 | 通过；Pixel 6 profile / API 36，1080×2400；此前构建的 debug APK 安装启动、开发登录和本机 API 连接通过；首页日期、七日记录、双伙伴、角色场景、按住说话入口、状态入口与新仪表盘导航均实际显示；计划读取、Agent NDJSON 响应通过。最新包的安装与断网表现见上表 |
| Android 模拟器离线状态 | 通过；API 停止后登录页显示本地化网络提示，开发登录入口仍可见 |
| Android 模拟器 401 会话恢复 | 通过；轮换隔离测试服务密钥使 Token 失效，受保护计划读取返回 401 后清理会话并显示重新登录提示；没有重放原请求 |
| Android 打卡真实 API 持久性 | 通过；Android 页面连接本机 FastAPI 和独立 SQLite（迁移到 `0043_mobile_media_upload_sessions`），读取今日记录、保存水 750 ml / 睡眠 7.5 小时 / 体重 68.4 kg / 步数 8400 / 心情“疲惫”，再重新打开页面读取均一致；接口 GET/PUT/GET 均返回 200。返回首页后命令中心显示今日已打卡和 1 天连续记录。仅验证本机开发后端，不代表生产云托管验收 |
| Android 隐私数据导出 | 通过；隐私页读取本机 API 导出预览并下载 ZIP，原生 Filesystem 写入 Android 缓存后成功打开系统分享面板。取出的隔离测试 ZIP 校验完整，包含 1 条本次打卡且没有 API Key 字段；关闭分享面板后应用缓存文件已清理。只覆盖本机开发账号与系统分享入口 |
| Android 计划确认端到端 | 通过本机隔离服务验证；Android UI 读取绑定当前测试能力安装的合成 Agent run。打开草案和确认弹窗前后，SQLite 均为 0 个计划、0 条 apply proposal、0 条 execution。用户点击最终“确认并加入”后，真实 FastAPI apply 返回 200、状态重读返回 200；本周计划读取成功，完成任务写入 PUT 返回 200。最终 SQLite 核实为 1 个计划、1 条计划项（done）、1 条 proposal、1 条 execution。计划内容由测试 fixture 预置，不代表模型/Agent 生成已验收 |
| 计划确认自动化门禁 | 通过；PlanPage 组件测试证明打开草案和取消均不调用写入，二次确认才调用 apply，成功后重读已应用状态，失败时可重新加载草案。后端 `test_action_protocol_unification.py` 验证重复确认只创建一个计划和一条实际执行审计 |
| Android 隐私账户删除端到端 | 通过本机隔离服务的无云媒体场景；在 Android 隐私页输入 `DELETE MY DATA`，依次通过两个原生确认框，删除 API 返回 200，成功提示后退出至登录页。SQLite 核实用户、计划、打卡及 proposal 均为 0。该测试账号没有 CloudBase/COS 资产，不覆盖外部云对象删除 |
| 双用户数据隔离与删除 | 通过独立后端集成测试 `backend/tests/test_user_isolation.py`；使用两名测试 fixture 用户和各自 JWT，分别保存不同的打卡并确认读取内容与记录 ID 隔离；删除用户甲后甲的 JWT 返回 401，用户甲及其数据被清除，用户乙及其打卡仍存在且可读。此测试不使用共享 `/auth/dev-login`，也不替代真实微信双账号真机验收 |
| Android 系统照片选择器 | 通过；Scan 页面可打开 Android 16 系统照片选择器，授权选中图片后原生 URI 返回并在页面预览 |
| Android 模拟相机拍摄 | 通过；系统相机打开，拍摄并确认模拟器虚拟场景后，图片 URI 返回并在 Scan 页面预览；这不是实体摄像头测试 |
| Android 麦克风录音插件 | 通过；授予运行时权限后原生录音开始/停止正常，并发起语音识别请求；隔离后端因未配置腾讯云语音凭据返回 503，识别文本和语音聊天未完成 |
| Android 照片开发上传与任务创建 | 通过；照片经本地开发媒体上传 API 保存，`/vision/food-jobs` 创建任务且查询返回 200；因隔离后端未启用 Worker/外部 AI，任务保持处理中，未验证识别结果与保存闭环 |
| Android 原生视频录制、选择和上传 | 通过本机隔离接收端验证：Android 16 系统相机录制并确认视频后，原生 `content://` 文件进入动作分析页；系统文档选择器选择视频也成功。两条路径都由原生插件读取并 PUT 完整字节，接收字节数分别与上传会话声明的 `1,966,593` 和 `826,442` 一致，`/complete` 返回 200，动作分析任务创建/查询返回 201/200。测试接收端没有动作分析 Worker，页面按预期显示任务失败；此结果不代表真实 COS、生产 API 或视觉分析已验收 |
| Android release Java/资源编译与 Web release 门禁 | 通过；使用隔离占位包名核对 Capacitor ID、Manifest 包名/微信回调、FileProvider 与正式应用名一致，不代表正式签名/发布验收 |
| Release 占位配置拒绝 | 通过；release 校验拒绝示例 API/CloudBase/AppID/包名，避免把模板值打进正式包 |
| 当前本机正式版门禁 | 未通过，正确拒绝生成 release；缺少 production 环境标记、微信登录模式、CloudBase 环境 ID、HTTPS 生产 API、微信 AppID 和注册包名。当前 `mobile/dist` 是开发构建，含仅开发模式加载的开发登录模块。release Java 编译单独通过，但没有生成或签署 release APK |
| 调试包签名、包名和 SHA-256 校验 | 通过；包名为 `com.hah.healthmate.dev`；当前常规配置包 SHA-256：`22fa53543daed98b860335487c16aaf1b40558e16b05f3bb6a3ba955b5456354` |
| 当前 debug APK 安装与启动 | 通过；最新常规配置包安装至 Android 16 / API 36 模拟器并启动，系统可查到包路径、前台 Activity 与应用进程。无后端连接时显示超时与重试；独立 SQLite 临时 API 就绪后，开发登录与 29/29 路由均通过。常规 API `/health/ready` 曾报告 `migration_required`，该服务端数据库没有擅自升级 |
| 打包清单、安装说明、权限说明与校验和 | 已生成 |
| APK 声明权限 | `INTERNET`、`RECORD_AUDIO` |
| Git diff whitespace 检查 | 通过；仅有 Windows 行尾格式提示 |
| 本轮关键后端回归复核 | `test_mobile_auth.py`、`test_mobile_media_upload.py`、`test_cloudbase_storage.py`、`test_user_isolation.py`、`test_media_storage.py`、`test_api_contract_governance.py`、`test_reliability.py` 共 `96 passed`，231 条依赖/弃用告警，无失败 |
| 本轮正式版构建门禁 | 当前真实配置下的正式构建在 Vite 门禁处正确拒绝。另以仅对构建进程生效的非真实占位参数和 `.invalid` API 地址运行 production build，`npm run verify:release` 通过，确认生产 JavaScript 不含开发登录端点、服务端密钥标记或本地/内网开发服务器地址；release 检查也拒绝带账号密码的 API URL。随后已恢复 debug 构建。该探针不验证真实登录、云服务或签名；真实 release 仍需配置正式参数、包名和签名材料 |
| 本轮本地 API 云合同检查 | `HEALTHMATE_CLOUD_ORIGIN=http://127.0.0.1:8000 npm run verify:cloud-contract`：本地 FastAPI OpenAPI 的 11/11 Android 合同操作全部存在。该检查只读本机公开 OpenAPI，不证明线上云托管已部署 |
| 本轮容器构建环境 | 未就绪：Windows Docker Desktop 服务 `com.docker.service` 为 Stopped；WSL Ubuntu 中没有 Docker socket，因此本轮无法构建云托管镜像 |
| 原小程序全量回归 | `node --test miniprogram/tests/*.test.js`：`157 passed`。新增账号关联行为测试覆盖重复点击与剪贴板不可用回退 |
| 原小程序严格 UI 审计 | 通过：审计脚本已识别 `@import` 共用样式并排除条件表达式的数据字面量；补齐共用紧凑按钮与媒体禁用态样式、54 个可点元素的反馈和偏离刻度的间距。原 72 项降至 7 项无渲染影响的死 CSS 提示（0 high、1 medium、6 low），`--strict` 退出码 0；完整 JSON 在 `miniprogram-ui-audit-2026-10-08.json`。小程序真实动效观感仍需开发者工具/真机检查 |
| 本轮移动端质量检查 | Android Vitest `51 passed`（19 个测试文件）；`npm run lint`、`npm run typecheck`、`npm run build:debug`、Android `assembleDebug` 与 `git diff --check` 通过 |
| MySQL 方言迁移验收 | 已通过 MySQL 8.0.45 隔离实例完成当前 `0044` head 的旧数据增量迁移与重复升级；仓库 MySQL 8.4 及云端实际版本尚未验证 |

完整调试包位于 [`../../../../dist/android/1.0/`](../../../../dist/android/1.0/)，APK 为 `HAH-1.0-debug.apk`。本次 SHA-256：

```text
22fa53543daed98b860335487c16aaf1b40558e16b05f3bb6a3ba955b5456354
```

此处“本机”指运行项目的 Windows 开发电脑；Android 模拟器通过 `10.0.2.2` 访问这台电脑上的 FastAPI 与隔离 SQLite。这是本地联调，不是微信云托管。debug 网络安全配置仅允许 `10.0.2.2` 使用 HTTP，其他目标仍要求 HTTPS，正式主清单保持 `usesCleartextTraffic=false`。模拟器证据：[`screenshots/`](screenshots/) 包含登录、首页、计划、Agent 对话、打卡、隐私导出、隐私删除、系统选图、模拟相机、视频上传和录音等流程。计划截图为合成草案：`android16-plan-preview-seeded.png`、`android16-plan-confirm-modal.png`、`android16-plan-applied.png`、`android16-plan-current-item-done.png`；删除截图为 `android16-privacy-delete-confirm.png`、`android16-privacy-deleted.png`。视频 PUT 使用仅绑定本机 `10.0.2.2:8000` 的临时接收端；没有把文件发送到 COS。隔离测试 SQLite 数据库已通过 Alembic 升级到 `0043_mobile_media_upload_sessions`；本次测试使用本地开发账号和本地安全回复路径，不访问云托管或外部 AI。

## 当前未执行的验收

- 云托管只读检查见 [`cloud-readiness.md`](cloud-readiness.md)：线上服务可用，但 OpenAPI 仍是旧部署；源码新增的移动微信登录、身份关联及 Android 媒体会话接口尚未发布。本机当前按旧 `wechat_cloud` 档案运行，Android 开关关闭；安全摘要显示移动微信、CloudBase 自定义登录、CloudBase 存储管理和 COS 凭据尚未配置。预检因 CloudBase 存储管理 ID/Secret 缺失而失败，没有对生产数据库迁移或写入。
- Docker Desktop 已安装但其 `com.docker.service` 仍停止，当前 shell 无法打开该服务且 Docker Engine 不响应；已确认 WSL Ubuntu 存在，但 Docker Desktop WSL 集成未启用，容器镜像仍未构建。当前未发现 `tcb`/`cloudbase` 发布 CLI。生产迁移与发布尚未执行。
- 尚未连接 Android 真机，因此未验证实体机摄像头、实体麦克风、语音播报、后台生命周期、微信跳转和真实设备视觉表现。模拟器原生录音已启动/停止，但语音识别请求因后端无腾讯云凭据而返回 503，TTS 与语音对话未完成；系统模拟相机也不替代实体机传感器验收。
- 微信开放平台资料已由用户准备；本机安全摘要显示开放平台 AppID/Secret 尚未配置。App ID、服务端凭据、包名与签名需在目标应用及云托管环境核对后，才能执行真实 OAuth 登录与双端关联。
- 本轮 APK 构建清单显示 CloudBase 环境 ID 与 Android 微信 AppID 均未注入；当前 APK 适合代码和页面调试，不能完成真实云存储或微信登录。
- 真实 CloudBase、COS 桶、CAM 权限及浏览器来源 CORS 尚未配置，无法验证云端身份、媒体 PUT/GET、历史媒体访问和隐私删除。
- 没有正式签名材料及生产 API/云托管密钥，因此当前产物是 debug 包，不是可上架的 release APK。

上述项目需要在对应云环境与测试设备中完成；自动化测试和本地打包不替代这些集成验收。
