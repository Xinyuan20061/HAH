# 微信云托管部署

本指南对应修复后的代码。唯一在线后端为微信云托管 FastAPI，业务和 AIJob 存储使用持久 MySQL，原媒体使用 CloudBase。当前已经验证本地 Docker/MySQL，不代表已经发布到你的微信环境。

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
SECRET_KEY=<独立随机值至少32字符>
CREDENTIALS_ENCRYPTION_KEY=<另一个独立随机值至少32字符>
WORKER_TOKEN=<独立随机值至少48字符>
WECHAT_APP_ID=<自己的AppID>
WECHAT_APP_SECRET=<自己的AppSecret>
CLOUDBASE_ENV_ID=<自己的环境ID>
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

4. 确认 `0018_seed_knowledge_documents (head)`。对新库和已有 Alembic 旧库同样执行 upgrade，不要 stamp 掩盖缺表问题。
5. 发布默认启动命令的 Web 版本；先单实例检查，再扩容。
6. 验证 live 200、ready 200，并检查 `database_backend=mysql`、`storage_backend=cloud_ref`。
7. 日志应含脱敏摘要 env/db/storage/port/worker；不能出现 SQLiteImpl 或每次重启 Running upgrade。

`RUN_MIGRATIONS_ON_START=true` 仅供明确控制为单实例的一次性便利操作，默认 false。它没有分布式迁移锁，不适合扩容时开启；完成后恢复 false。

## 5. 持久性和迁移影响

`0002/0007` 去掉不兼容的 TEXT DEFAULT；source_url 先可空添加、回填旧行再设非空。`0008` 增加任务去重键、领取请求键、重试时间、调度索引，并扩大 storage_key 和 MySQL result_json 容量。已有任务的新增去重键为空，不会删除旧任务；迁移后创建的任务使用唯一键去重。旧不可恢复 URL 失败改为等待刷新。DATETIME 仍存 naive UTC，API 输出 Z，不更换整库时间类型。

MySQL 8.4 实测新库/增量/重复升级通过；没有运行 5.7/8.0 实例，不能承诺实测兼容。源码使用 CAS UPDATE 领取任务而非 SKIP LOCKED，去掉 TEXT DEFAULT，避免已知 5.7 语法限制；如果比赛环境实际为 5.7，必须在该版本的备份副本上验收。

云容器重启或扩容不会执行 create_all、不会复制本地 db/uploads。数据库短暂断开时 live 保持 200，ready 503，恢复后重新就绪。

## 6. 公网入口、登录和 Worker

开启**同一云托管服务**的 HTTPS 公网访问供电脑主动轮询。Worker 配置 `API_BASE_URL=https://该域名/api/v1` 与独立 `WORKER_TOKEN`。云端不访问电脑、不需要开放电脑端口，不把数据库密码发给 Worker。

公网请求头可以伪造，所以 `CLOUD_HEADER_LOGIN_ENABLED=false`；小程序 `wx.login` code 在服务端调用微信换 openid，签发 HealthMate JWT。生产 dev-login 禁用。Worker 使用 `X-Worker-Token`；领取后还需相同 worker_id 和 lease_token。限制 Worker Token 的分发范围，泄露后云端和本机同步轮换。

## 7. 云存储和隐私

配置 CloudBase **仅创建者可读写**，并用两个真实账号验证上传、读链接、覆盖、删除的隔离。当前官方存储规则变量是 `resource.openid`，请以控制台规则预览为准：

```json
{
  "read": "auth != null && resource.openid == auth.openid",
  "write": "auth != null && resource.openid == auth.openid"
}
```

前端先 uploadFile，再 getTempFileURL，然后 register-cloud。稳定 fileID 必须属于配置环境，路径必须是当前用户 `healthmate/u<ID>/{image|video}/...`；URL 必须指向相应腾讯云路径，声明大小/类型有校验。**路径和客户端大小声明不等于平台认证过的所有权/原文件大小**，必须依靠创建者权限，Worker 还会限制实际下载字节并解码验证素材。

链接过期进入 waiting_source_refresh；小程序重新取链接，PUT `/media/{id}/refresh-source` 恢复原任务，不创建重复媒体或任务。电脑只下载签名 HTTPS，DNS 全部地址检查、IP 固定、每跳重新验证、最大 200MB、最多 5 跳，SSL 校验保留。

隐私删除是客户端/后端协调：GET `/privacy/cloud-media` → 小程序逐文件 `wx.cloud.deleteFile` 确认 → DELETE `/privacy/account` 提交 `confirmation=DELETE MY DATA` 及 `cloud_files_deleted` → 后端清理数据库。后端仅能检查回执 ID 集合一致，不能从这个请求证明平台删除成功；响应明确 `client_reported`。云文件删除失败则不清空数据库。上传成功但注册响应丢失、客户端卸载前的孤立云文件需由项目拥有者对账清理，当前没有服务端全桶扫描或 CloudBase 管理凭据。

## 8. 发布后人工验收

真实微信登录；普通记录/目标/图表；真机 CloudBase 上传和临时 URL；在线姿态 Worker；真实 VLM 识餐/校正/确认；停 Worker 保留队列；过期链接原地恢复；隐私导出与实际云文件删除。完整检查见 DELIVERY_CHECKLIST.md。

平台参考（2026-09-18 查询）：[运行/PORT](https://docs.cloudbase.net/run/develop/developing-guide)、[MySQL 集成](https://docs.cloudbase.net/run/develop/resource-integration/mysql)、[存储权限规则](https://docs.cloudbase.net/storage/security-rules)。控制台选项变化时以平台当前配置为准。
