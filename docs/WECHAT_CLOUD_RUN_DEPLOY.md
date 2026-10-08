# 微信云托管部署

本指南对应当前源码。唯一在线后端为微信云托管 FastAPI，业务和 AIJob 存储使用持久 MySQL，原媒体使用 CloudBase。Android 本机验收和测试状态见 [`docs/android/development-status.md`](android/development-status.md)；本指南不代表服务已发布到你的微信环境。

## 1. 准备资源

在自己的微信/CloudBase 环境准备云托管服务 `healthmate-api`、云托管可达的持久 MySQL、云存储、AppID/AppSecret，以及本机 Worker。按控制台实际网络能力配置 MySQL 连接，优先使用云内连接、限制数据库暴露范围并开备份。CloudBase 文档数据库不能替代这里的 MySQL。

## 2. 构建与入口

|项目|配置|
|---|---|
|源码构建目录|`backend/`|
|Dockerfile|构建目录内 `Dockerfile`|
|本地构建|根目录 `docker build -t healthmate-api ./backend`|
|默认命令|`sh entrypoint.sh`，先配置预检，然后启动 Uvicorn|
|监听|`0.0.0.0:${PORT:-8000}`|
|数据库迁移|独立 `alembic upgrade head`，Web 默认不执行|
|存活探针|`GET /health/live`|
|就绪探针|`GET /health/ready`，数据库连通且迁移到 head 才返回 200|

镜像构建不访问数据库。镜像不包含 `.env`、SQLite、uploads、视觉依赖/模型或本地虚拟环境。不要在平台设置中覆盖为旧的“迁移 && 启动”命令。`EXPOSE 8000` 是镜像说明，不会覆盖注入的 PORT。

若平台分别支持存活/就绪检查，分别使用 live/ready；若只有一个检查用于判定是否需要重启，选择 live，另用 ready 验证可服务状态，避免数据库暂不可用导致进程循环重启。请按自己的控制台实际选项设置。

## 3. 生产变量

下列是字段模板，所有秘密应从控制台受控配置注入，禁止提交真实 `.env`：

```env
ENV=production
DATABASE_URL=mysql+pymysql://USER:URL_ENCODED_PASSWORD@HOST:3306/healthmate?charset=utf8mb4
STORAGE_BACKEND=cloud_ref
DEPLOYMENT_PROFILE=dual_client_cloud
MOBILE_AUTH_PROVIDER=wechat_open_platform
MOBILE_AUTH_ENABLED=true
MOBILE_UPLOAD_ENABLED=true
MOBILE_UPLOAD_BACKEND=s3
SECRET_KEY=<独立随机值至少32字符>
CREDENTIALS_ENCRYPTION_KEY=<另一个独立随机值至少32字符>
WORKER_TOKEN=<独立随机值至少48字符>
WECHAT_APP_ID=<自己的AppID>
WECHAT_APP_SECRET=<自己的AppSecret>
MOBILE_WECHAT_APP_ID=<开放平台移动应用AppID>
MOBILE_WECHAT_APP_SECRET=<开放平台移动应用AppSecret>
CLOUDBASE_ENV_ID=<自己的环境ID>
# CloudBase custom-login signing key JSON; backend secret only.
CLOUDBASE_CUSTOM_LOGIN_CREDENTIALS_JSON=<环境ID、private_key_id、private_key组成的JSON>
# CAM 子账号密钥，仅授予当前 CloudBase 环境所需云存储删除权限，存入云托管密钥管理
CLOUDBASE_STORAGE_SECRET_ID=<受限子账号SecretId>
CLOUDBASE_STORAGE_SECRET_KEY=<受限子账号SecretKey>
# 使用临时 CAM 凭据时填写 SessionToken；固定密钥留空
CLOUDBASE_STORAGE_SESSION_TOKEN=
# Android direct uploads use a private S3-compatible Tencent COS bucket.
S3_ENDPOINT_URL=https://cos.<region>.myqcloud.com
S3_ACCESS_KEY=<受限 COS SecretId>
S3_SECRET_KEY=<受限 COS SecretKey>
S3_BUCKET=<BucketName-腾讯云APPID，例如 healthmate-media-1250000000>
S3_REGION=<存储桶地域>
# 兼容既有变量名，值为微信云托管服务名
CLOUDRUN_SERVICE_NAME=healthmate-api
PUBLIC_BASE_URL=https://<同一云托管公网域名>
CLOUD_HEADER_LOGIN_ENABLED=false
RUN_MIGRATIONS_ON_START=false
CORS_ORIGINS=*
WORKER_LEASE_SECONDS=180
WORKER_MAX_ATTEMPTS=3
WORKER_OFFLINE_AFTER_SECONDS=45
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
LOG_LEVEL=INFO
```

PORT 由平台注入；本地未注入默认 8000。`CORS_ORIGINS=*` 时代码关闭 credentials。DeepSeek 系统 Key 可留空，由用户配置自己的 Key；无法提供文本 AI 时对话明确返回 503，周报/计划用标记过的规则降级。

用 `python -c "import secrets; print(secrets.token_hex(32))"` 分别生成三个随机值。不要复用 JWT/凭据加密密钥。保留原加密密钥备份，直接更换会使已有用户 Key 无法解密。

`CLOUDBASE_CUSTOM_LOGIN_CREDENTIALS_JSON` 与 `CLOUDBASE_STORAGE_SECRET_ID/KEY` 用途不同：前者由 Android 登录后端签发短时 CloudBase 自定义登录票据，以访问本人已有的私有历史文件；后者只用于服务端调用[官方文件存储管理 API](https://docs.cloudbase.net/api-reference/openapi/storage)删除文件，按[CloudBase Open API 签名规范](https://docs.cloudbase.net/api-reference/openapi/introduction)签名并读取逐文件平台回执。两者均为服务端凭据，不要放进 Android、小程序或 `VITE_` 环境变量。CloudBase 管理密钥应使用受限 CAM 子账号，仅授予目标环境所需的文件删除操作。删除失败的文件引用会加密保留在删除台账中，由云托管后端按退避时间重试。

## 4. 迁移和上线顺序

1. 为 MySQL 创建 `healthmate` 库（utf8mb4）和受限应用账号，启用备份；旧库先备份。
2. 在能够连接该数据库的临时受控迁移环境，注入同一生产配置。
3. 在 `backend/` 执行：

```powershell
python scripts/preflight.py
python -m alembic upgrade head
python -m alembic current
```

或使用已经构建的镜像与不提交的受控变量文件：

```powershell
docker run --rm --env-file .\production.env healthmate-api python scripts/preflight.py
docker run --rm --env-file .\production.env healthmate-api alembic upgrade head
docker run --rm --env-file .\production.env healthmate-api alembic current
```

连接云内数据库需要相应网络路径；本机 Docker 不会自动获得云内访问能力。可使用平台当前支持的单次执行环境或受控同网段主机运行迁移，不假定控制台提供某个具体按钮。

4. 用 `python -m alembic heads` 查看源码 head；迁移完成后用 `python -m alembic current` 确认目标数据库 revision 与源码 head 一致。当前源码单一 head 为 `0045_motion_preview_shared_store`。对新库和已有 Alembic 旧库都执行 upgrade，不要用 stamp 掩盖缺表问题。该迁移新增跨云托管实例共享的短期关键帧图片表；必须先迁移、再发布新版 API 和 Worker。旧任务若从未成功上传图片，需要重新分析原视频才能生成真实关键帧。
5. 发布默认启动命令的 Web 版本；先单实例检查，再扩容。
6. 验证 live 200、ready 200，并检查 `deployment_profile=dual_client_cloud`、两个移动开关为 true、`database_backend=mysql`、`storage_backend=cloud_ref`、`mobile_upload_backend=s3`。小程序继续使用 CloudBase；Android 新上传走独立 COS 路由。
7. 日志应含脱敏摘要 env/db/storage/port/worker；不能出现 SQLiteImpl 或每次重启 Running upgrade。

`RUN_MIGRATIONS_ON_START=true` 仅供明确控制为单实例的一次性便利操作，默认 false。它没有分布式迁移锁，不适合扩容时开启；完成后恢复 false。

## 5. 持久性和迁移影响

`0002/0007` 去掉不兼容的 TEXT DEFAULT；source_url 先可空添加、回填旧行再设非空。`0008` 增加任务去重键、领取请求键、重试时间、调度索引，并扩大 storage_key 和 MySQL result_json 容量。已有任务的新增去重键为空，不会删除旧任务；迁移后创建的任务使用唯一键去重。旧不可恢复 URL 失败改为等待刷新。DATETIME 仍存 naive UTC，API 输出 Z，不更换整库时间类型。

本工作包已在隔离 SQLite 和临时独立 MySQL 8.0.45 实例验证从旧数据基线建立旧用户/饮食行、迁移到当前 head、重复升级及旧数据保留；MySQL 验收命令为 `MIGRATION_TEST_DATABASE_URL=mysql+pymysql://.../healthmate_incremental?charset=utf8mb4 python scripts/verify_migrations.py`。测试服务使用全新的临时数据目录，未连接项目 `.env` 中的非本机数据库，执行后已关闭。该结果证明迁移脚本在 MySQL 8.0.45 方言下可运行，但仓库 Compose 固定 MySQL 8.4，线上云数据库的精确版本也尚未确认，因此仍须在目标版本的独立测试库/备份副本验证升级、重复升级、关键旧数据保留和恢复方案；不得把 SQLite 或 MySQL 8.0.45 结果写成目标云数据库验收通过。

云容器重启或扩容不会执行 create_all、不会复制本地 db/uploads。数据库短暂断开时 live 保持 200，ready 503，恢复后重新就绪。

## 6. 公网入口、登录和 Worker

开启**同一云托管服务**的 HTTPS 公网访问供电脑主动轮询。Worker 配置 `API_BASE_URL=https://该域名/api/v1` 与独立 `WORKER_TOKEN`。云端不访问电脑、不需要开放电脑端口，不把数据库密码发给 Worker。

公网请求头可以伪造，所以 `CLOUD_HEADER_LOGIN_ENABLED=false`；小程序 `wx.login` code 在服务端调用微信换 openid，签发 HealthMate JWT。生产 dev-login 禁用。Worker 使用 `X-Worker-Token`；领取后还需相同 worker_id 和 lease_token。限制 Worker Token 的分发范围，泄露后云端和本机同步轮换。

## 7. 云存储和隐私

微信云托管保持 `STORAGE_BACKEND=cloud_ref` 管理小程序历史和新媒体，Android 新上传通过 `MOBILE_UPLOAD_BACKEND=s3` 选择私有 COS 桶。两种素材统一登记到 `MediaAsset`，下载、播放、删除和隐私导出按资产自己的 `storage_backend` 分派。Android 上传使用短时限的 S3 兼容预签名 PUT；启动预检要求 COS 桶名为 `BucketName-APPID`，标准地域端点须与桶地域一致，上传 `Content-Type` 由签名约束。按 Android WebView 实际 Origin 配置 CORS（含协议和非默认端口），放行 `PUT`、`GET`、`HEAD` 和实际请求头；`OPTIONS` 由 COS 自动处理预检。桶保持私有读写，COS 凭据按最小权限限制。真实 COS 互通仍需在目标桶验证。[桶命名规范](https://cloud.tencent.com/document/product/436/13312) · [CORS 配置](https://cloud.tencent.com/document/product/436/13318)

配置 CloudBase **仅创建者可读写**，并用两个真实账号验证上传、读链接、覆盖、删除的隔离。当前官方存储规则变量是 `resource.openid`，请以控制台规则预览为准：

```json
{
  "read": "auth != null && resource.openid == auth.openid",
  "write": "auth != null && resource.openid == auth.openid"
}
```

前端先 uploadFile，再 getTempFileURL，然后 register-cloud。稳定 fileID 必须属于配置环境，路径必须是当前用户 `healthmate/u<ID>/{image|video}/...`；URL 必须指向相应腾讯云路径，声明大小/类型有校验。**路径和客户端大小声明不等于平台认证过的所有权/原文件大小**，必须依靠创建者权限，Worker 还会限制实际下载字节并解码验证素材。

链接过期进入 waiting_source_refresh；小程序重新取链接，PUT `/media/{id}/refresh-source` 恢复原任务，不创建重复媒体或任务。电脑只下载签名 HTTPS，DNS 全部地址检查、IP 固定、每跳重新验证、最大 200MB、最多 5 跳，SSL 校验保留。

隐私删除由服务端按资产后端执行。调用 DELETE `/privacy/account` 前，Android 要求用户输入 `DELETE MY DATA` 并再次确认；配置 CloudBase 管理凭据后，服务端对账本中的每个历史 `fileID` 调用官方删除 API，并按逐文件回执记录 `server_verified`、`pending` 或 `partial`。失败项保留加密对象引用并自动重试。未配置管理凭据时，兼容路径允许小程序逐文件 `wx.cloud.deleteFile` 后提交 `cloud_files_deleted`；这只记为 `client_reported`，不能宣称平台验证成功。COS/S3 正式对象和未登记上传暂存对象也由服务端删除台账处理。当前没有 CloudBase/COS 全桶枚举能力，因此注册响应丢失造成的孤立对象仍需受控对账；不要把“没有发现”写成“已证明不存在”。

## 8. 发布后验收顺序

先在隔离的微信云托管测试服务、MySQL 测试库和私有 COS 桶完成验收，再安排生产发布。每项保存环境、包名、设备、测试时间和结果；不得用模拟器、本地接收端或用户自报回执代替真实云端/真机证据。

1. 查看 `/health/live` 与 `/health/ready`，确认 ready 200、`database_backend=mysql`、`storage_backend=cloud_ref`、`mobile_upload_backend=s3`、`deployment_profile=dual_client_cloud`，并核对两个移动开关已按计划启用。
2. 在 `mobile/` 设置只含服务 origin 的 HTTPS 环境变量并检查线上公开 OpenAPI；此检查不登录、不写业务数据：

   ```powershell
   $env:HEALTHMATE_CLOUD_ORIGIN = 'https://<云托管服务域名>'
   npm run verify:cloud-contract
   ```

   脚本必须显示 11 项 Android 云端合同全部已部署；只检查路径，不证明凭据、数据库、存储权限或业务流程可用。
3. 使用开放平台已登记的正式测试包名、AppID 与匹配签名真机测试授权成功、用户取消、未安装微信、拒绝授权和服务端 code 交换；验证小程序生成的十分钟关联码只能消费一次、错误/过期码被拒，以及已有移动健康数据不会被静默合并。
4. 使用两个真实测试账号验证记录、计划、会话、媒体和导出互相隔离；分别完成小程序身份与 Android 身份关联、解绑和重新登录。
5. 用真实 COS 桶验证图片与视频的预签名 PUT、Content-Type、CORS、大小/文件头拒绝、complete 幂等、过期暂存清理、播放续签以及断网/取消恢复；同时验证 CloudBase 历史文件可读、链接续期与服务端逐文件删除回执。
6. 验证真实识餐、用户核对/校正后才入账、动作 Worker 的任务恢复、真实设备录音/STT/TTS、后台暂停/恢复；先停止 Worker 再确认队列可恢复。
7. 用真实云媒体完成隐私导出和账户删除；核对每种资产的 `server_verified`、`pending` 或 `partial` 状态，并验证删除失败的安全重试。完成后在目标数据库备份副本演练恢复。
8. 配置正式 API、CloudBase 环境 ID、微信 AppID、正式注册包名和正式签名后运行 `npm run android:release`；检查产物证书、包名、HTTPS-only、权限、无 dev-login/密钥，并在真机做安装、覆盖升级及微信回调验收。

当前云端只读状态和未通过项见 [`docs/android/verification/2026-10-07/cloud-readiness.md`](android/verification/2026-10-07/cloud-readiness.md)，本机/模拟器测试结果见 [`docs/android/verification/2026-10-07/test-summary.md`](android/verification/2026-10-07/test-summary.md)。它们是当前状态记录，不替代上述发布后验收。

平台参考（2026-10-07 复核）：[CloudBase OpenAPI 文件批量删除与逐文件回执](https://docs.cloudbase.net/api-reference/openapi/storage)、[CloudBase OpenAPI TC3 签名](https://docs.cloudbase.net/api-reference/openapi/introduction)、[微信云托管运行/PORT](https://docs.cloudbase.net/run/develop/developing-guide)、[MySQL 集成](https://docs.cloudbase.net/run/develop/resource-integration/mysql)、[存储权限规则](https://docs.cloudbase.net/storage/security-rules)。控制台选项变化时以平台当前配置为准。
