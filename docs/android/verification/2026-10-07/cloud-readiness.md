# 微信云托管只读就绪检查与复查（2026-10-07）

本记录只写检查结论，不复制任何本机 `.env` 值。执行了 HTTPS `GET /health/live`、`GET /health/ready`、`GET /openapi.json`，以及配置预检脚本；没有登录真实用户、写业务数据、运行迁移或部署服务。

| 检查 | 结果 |
| --- | --- |
| 在线服务存活 | HTTP 200 |
| 在线服务就绪 | HTTP 200；运行环境为 production，数据库为 MySQL，媒体后端为 `cloud_ref`，数据库检查通过 |
| 在线 OpenAPI | 共 193 条路径；Android 合同检查覆盖 11 个操作，其中 2 个旧隐私操作存在、9 个新增操作缺失 |
| 本机安全配置摘要 | 当前 `.env` 按旧 `wechat_cloud` 档案读取；Android 登录/上传开关关闭，移动微信、CloudBase 自定义登录、CloudBase 存储管理和 COS 凭据均未配置。仅记录安全布尔值，不读取或复制密钥 |
| 移动端微信登录 | 未部署：缺少 `POST /api/v1/auth/mobile/wechat` |
| 双端身份关联 | 未部署：缺少 `/api/v1/auth/link/start`、`complete`、`unlink` |
| Android 移动媒体会话 | 未部署：缺少 `/api/v1/media/mobile-upload/options` 与 `/sessions` 创建、完成、取消接口 |
| 当前生产配置预检 | 未通过：本机预检仅报告 `CLOUDBASE_STORAGE_SECRET_ID`、`CLOUDBASE_STORAGE_SECRET_KEY` 缺失；因为 Android 开关关闭，移动微信、CloudBase 自定义登录和 COS 配置不参与该旧档案预检。用户已确认开放平台资料准备就绪，但当前服务端环境尚无对应配置 |
| 当前源码迁移链 | 单一 Alembic head：`0044_motion_feedback_idempotency`；线上数据库 revision 未通过公开探针确认 |
| 本地容器镜像 | 未构建：Docker Desktop 已安装，但 `com.docker.service` 仍为 Stopped；WSL Ubuntu 可用但未启用 Docker Desktop 集成，Docker Engine 管道不可用 |
| 生产数据变更 | 未执行；没有运行 Alembic 迁移或发布新服务 |

本机源码包含迁移 `0041` 至 `0044` 和上述路由；线上 OpenAPI 与当前源码不一致。下一次生产发布前，需要由负责人补齐受控环境变量、备份并评审 MySQL 迁移，然后部署并重新检查 OpenAPI。CloudBase/CAM 凭据只配置在服务端密钥管理，不放进客户端或仓库。

本轮复查继续只执行了公开健康探针与 OpenAPI GET：`/health/ready` 返回 production / MySQL / cloud_ref，移动端合同仍为 2/11；配置预检仍只报告 CloudBase 存储管理 Secret ID/Key 缺失。没有访问账户数据、运行迁移或部署。

## 本轮本地构建门禁复核

2026-10-07 本轮未再次请求生产探针或访问生产数据。重新运行 release 门禁，结果仍拒绝正式构建，报告缺少 `VITE_APP_ENV=production`、`VITE_AUTH_MODE=wechat`、`VITE_CLOUDBASE_ENV_ID`、HTTPS 且以 `/api/v1` 结尾的 `VITE_API_BASE_URL`、`WECHAT_MOBILE_APP_ID` 和有效的 `ANDROID_RELEASE_APPLICATION_ID`；当前 bundle 仍包含开发登录入口。Docker Desktop 的 `com.docker.service` 状态为 Stopped，WSL Ubuntu 内也没有 `/var/run/docker.sock`；尝试启动服务时当前 Windows 会话无法打开该服务，因此本轮未构建镜像。关键后端回归共 `96 passed`（231 条依赖/弃用告警）。

## 2026-10-08 本机复查

只核对变量名是否存在，没有读取变量值：当前进程、用户和系统环境均未设置移动微信 AppID/AppSecret、CloudBase 存储管理凭据、COS 凭据及 release applicationId/签名变量。`backend/.env` 仍有 CloudBase 环境 ID，但没有上述服务端登录、管理或 COS 凭据；`mobile/.env.local` 仅配置开发环境、API 地址与登录模式，没有 CloudBase 环境 ID 或微信 AppID。用户此前确认开放平台资料已准备，但尚不清楚是否已存入云托管受控环境。

工作站重启后曾没有连接中的 Android 模拟器；现已重新启动并安装最新 debug APK。Docker Desktop 服务仍为 Stopped；本轮再次尝试启动时 Windows 返回“Cannot open service”，Docker Engine 不可用，因此无法构建本地云托管镜像。没有访问线上 OpenAPI、登录生产账号、迁移数据库或部署服务；线上 2/11 合同数仍是 2026-10-07 最近一次只读检查结果。

用户已确认微信开放平台资料准备就绪；尚未确认资料是否已放入开发电脑项目配置或云托管受控环境变量。请通过本机安全的配置渠道补齐配置，不要将 Secret 或签名密码发送到聊天或提交到仓库。生产迁移、部署和账号数据访问均未执行。

随后用户表示不清楚资料是否已配置。当前构建进程未加载 release 所需变量；`mobile/.env.local` 仅有 API 地址，没有 CloudBase 环境 ID；`backend/.env` 没有移动 AppID/AppSecret、CloudBase 存储管理凭据、COS 密钥或 Android 功能开关。检查只核对变量是否存在，没有读取或记录值。正式门禁因此继续拒绝 release。

本轮对 `http://127.0.0.1:8000/openapi.json` 运行 `mobile/scripts/verify-cloud-contract.mjs`，本地 FastAPI 的 11/11 Android 操作全部存在。该结果只代表本地运行服务；本轮没有访问线上 OpenAPI，线上最近一次只读结果仍为 2/11。

最初 Windows `MySQL80` 服务的无密码 root 握手被拒绝；之后改用 MySQL `8.0.45` 服务端程序创建全新临时数据目录和隔离实例，成功执行旧数据从 0003 到 0043 的迁移与重复升级验证，随后关闭实例。没有使用项目 `.env` 中的非本机数据库 URL。仓库 Compose 固定 MySQL `8.4`，此项只证明 MySQL 8.0.45 方言路径，云端数据库版本和目标版本回归仍待确认。

随后在同一隔离 MySQL 8.0.45 实例上运行完整后端套件，前序版本 `582 passed`（2071 条依赖/弃用告警）。全量回归暴露了无小数秒 `DATETIME` 将动作任务入队时间舍入到未来一秒的边界；代码已按列精度规范化入队、租约与重试时间，6 项此前失败的定向测试及完整套件现均通过。测试实例已再次关闭；这不替代仓库目标 MySQL 8.4 或云数据库版本验收。

本轮使用全新、隔离的 MySQL 8.0.45 数据目录执行当前迁移链验证，旧数据从 0003 保留升级到 `0044_motion_feedback_idempotency`，重复升级保持在 head。随后在该隔离实例的新审计数据库上运行当前源码完整后端套件，`590 passed`（2072 条依赖/弃用告警）。测试服务已停止；没有使用项目 `.env` 中的数据库 URL。这不代表 MySQL 8.4 或线上数据库已验收。

自动文件系统策略拒绝清除已核实的临时 MySQL 目录，服务已停止，数据文件仍保留在 `%TEMP%\hah-mysql-0044-59e8e985c8d44a5a81e5ca602222f393`。

本轮继续运行移动端测试、ESLint、TypeScript 检查和 Android debug 构建，均通过；重新安装到 Android 16 模拟器后，29/29 路由渲染且无运行时异常。使用当前真实本机配置的 production 构建在 Vite 门禁处被拒绝。随后以仅作用于构建进程的非真实占位参数和 `.invalid` API 地址验证 production bundle，静态 release 检查通过；已恢复 debug 构建，未生成 release APK。该探针不代表真实微信登录、云端部署或正式签名验收。只读环境状态复核显示：开发电脑后端配置存在 CloudBase 环境 ID，但移动微信 AppID/Secret、CloudBase 自定义登录与管理凭据、COS 凭据未配置；Android 客户端环境没有注入 CloudBase 环境 ID。另仅检查了 Windows 进程、用户和系统环境变量是否存在所需移动微信、release 包名/签名和 COS 配置；这些变量均未设置，未读取任何值。当前客户端 API 地址是本机 HTTP 开发目标，因此本轮未刷新线上 OpenAPI；线上 2/11 是最近一次公开只读结果。`App.vue` 已在正式环境隐藏开发环境标记和连接诊断。

补充 COS 部署前检查：生产 Android COS 配置现在会拒绝不符合 `BucketName-APPID` 的桶名，并在腾讯标准地域端点下拒绝 `S3_REGION` 与 endpoint 地域不一致；配置示例和云端 CORS 验收说明已同步更正。相关后端定向回归 `45 passed`。这只是启动配置检查，不代表真实桶的 CORS、CAM、签名 PUT 或对象复制已联通。
