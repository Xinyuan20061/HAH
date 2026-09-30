# HealthMate 动作点评与语音闭环：独立验收报告

> 版本：2026-09-29；验收人：独立复验（不实现新业务功能、不改业务代码）。
> 验收口径：
>
> `HEALTHMATE_HARNESS_MOTION_VOICE_ENGINEERING_SPEC_2026-09-29.md`
>
>  第 9 节（8 维度）与第 10 节（发布门禁）。
> 结论先行：
>
> **工程实现（P0-A/B/C/D + P1 Harness + 数据层 + 文档）在本地全部复验通过；**
> **但独立集真实评测、云端部署、外部语音真实连通、真机弱网 / 麦克风权限四项未执行，按规格第 10 节，**
> **新入口不应向所有用户开启、功能开关保持关闭。**



***

## 0. 复验环境与范围



* 后端：`backend\.venv\Scripts\python.exe`（SQLite 临时库，ENV=development）。

* Worker：`ai-worker\.venv\Scripts\python.exe`。

* 小程序：node v22.23.2。

* 本报告只做独立复验 / 交叉核对 / 文档收口，未改任何业务代码；仅更新 docs/ 中 "待实现确认" 标记。

* 临时验证文件（`.verify_migrate.db`、`.verify_*.log`、`.verify_smoke.*` 等）用完即删，未残留。



***

## 1. 全量测试复验（数字如实记录）



| 套件          | 命令                                        | 预期         | 实际                                                                 | 状态 |
| ----------- | ----------------------------------------- | ---------- | ------------------------------------------------------------------ | -- |
| backend     | `.venv\Scripts\python.exe -m pytest -q`   | 218 passed | **218 passed**（664 warnings，48s）                                   | 达成 |
| ai-worker   | `.venv\Scripts\python.exe -m pytest -q`   | 117 passed | **117 passed**（7s）                                                 | 达成 |
| miniprogram | `node --test miniprogram/tests/*.test.js` | 78 passed  | **79 passed / 0 failed**（复跑确认：`# tests 79 / # pass 79 / # fail 0`） | 达成 |

### 1.1 小程序测试说明（陈旧断言已修复）



* 首轮复验时 `settingsPresentation.test.js` 曾失败：旧断言 `assert.match(script, /\/users\/me\/ai-config\/voice-test/)`

  仍要求设置页自动调用旧 `/users/me/ai-config/voice-test`，与规格第 5 节 "移除自动 voice-test、改手动验证入口" 相悖。

* P0-D 工作包已修复该测试：改为断言新行为（设置页调 `voice/status` + 手动 `verify-once`、不自动 voice-test、腾讯云模式无客户端密钥输入），并新增 1 个用例。

* 复跑确认：**79 tests / 79 pass / 0 fail**（原 78 项 + 新增 1 项）。至此三端测试全绿。



***

## 2. 迁移三路径（SQLite 临时库，已清理）

在 `backend/` 用 `DATABASE_URL=sqlite:///./.verify_migrate.db`、`ENV=development`：



| 路径          | 操作                                         | 结果                                            |
| ----------- | ------------------------------------------ | --------------------------------------------- |
| fresh       | 删库后 `alembic upgrade head`                 | 到达 `0024_motion_voice_harness (head)`，五新表全部存在 |
| repeat      | 再 `alembic upgrade head`                   | 幂等；且 0023→0024 可重复执行                          |
| downgrade   | `alembic downgrade 0023_user_voice_config` | 五表与两列全部 drop，inspect 确认无残留                    |
| incremental | 回退后再 `alembic upgrade head`                | 0023→0024 升级成功，`current=0024 (head)`          |



* 五表：`motion_analysis_runs / motion_analysis_feedback / provider_invocations / provider_connection_checks / voice_usage_daily` 全部创建成功。

* `provider_invocations` 实际列（PRAGMA）：`id, user_id, run_id, provider, operation, request_fingerprint, status, latency_ms, token_or_char_count, provider_request_id, cost_estimate, created_at` —— 与规格 8.1 脱敏字段清单**逐字段一致**。

### 2.1 MySQL 方言离线 DDL 编译

`alembic upgrade 0023:0024 --sql` 配 `mysql+pymysql://...`：



* Context impl MySQLImpl，编译出合法 MySQL DDL（`INTEGER AUTO_INCREMENT`、`VARCHAR(n)`、`DATETIME`/`DATE`、外键、唯一约束 `uq_provider_conn_check`/`uq_voice_usage_daily`、3+3 索引、两条 `ALTER TABLE user_ai_configs ADD COLUMN`），无语法错误。

* **未达成**：MySQL 生产库的真实 fresh/incremental/repeat 三路径未执行（云端 MySQL 仍在 0023），按部署手册需在云端备份后执行。



***

## 3. 隐私合规抽查



| 检查项                                          | 证据                                                                                                                                                                                | 结论                 |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| `provider_invocations` 不存 prompt/base64 / 密钥 | 列清单见 §2；`provider_gateway.py:record()` 仅写 `request_fingerprint[:128]/status/latency/tokens/provider_request_id`；`voice.py:518` 注释 "fingerprint=trace id only"                     | 达成                 |
| 不把 image base64 写日志                          | 对 7 个新文件 grep `log.*(secret\|base64\|image_b64\|prompt\|audio)` 零命中                                                                                                               | 达成                 |
| 云端关键帧默认纯色画布                                  | `visualize.render_skeleton_canvas()` 用 `np.full(...,CANVAS_BGR_BG=(26,34,30))`，**从不接收 / 拷贝原帧像素**；`processors/motion_unified.py:273,276` 调 canvas 后立即 `assert_desensitized_canvas` | 达成                 |
| 像素级脱敏检查                                      | `assert_desensitized_canvas`：背景占比 `<0.88` 或与原帧相关系数 `>0.10` 即抛 `ProcessingError(retryable=False)`                                                                                  | 达成                 |
| 真实帧路径不用于云端默认                                 | 统一链未调用 `render_motion_preview`/`annotate_keyframes`（原帧 + 人脸模糊仅为 legacy 路径）                                                                                                        | 达成                 |
| TENCENT 密钥不落小程序 / DB                         | `.env` 中 `TENCENT_SECRET_ID=<EMPTY>`、`TENCENT_SECRET_KEY=<EMPTY>`；`user_ai_configs` 不存密钥                                                                                          | 达成（且真实调用应 503 未配置） |



***

## 4. 契约交叉核对



| # | 核对                                      | 证据                                                                                                                                                                                                                                                                                                                                                                               | 结论            |
| - | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| a | 前端 `motionUnifiedView.js` 读取字段 ⊆ 后端物化结果 | 前端读 `analysis_id/pipeline_version/recognition.{state,label_id,label_zh,reason,sources,review_status,candidate_score,calibrated_confidence}/score.{available,overall,reps,completeness,stability,rhythm_control,reason_code}/summary.{text,degraded}/keyframes[].{id,t_ms,phase,finding,advice,image_url,evidence_type}/limitations/trace_id`；`orchestrator._build_result()` 全部产出 | 达成（前端读取字段均存在） |
| b | 前端 POST body == `MotionAnalysesIn`      | `media.py:422` `MotionAnalysesIn{media_id:int, requested_exercise:pattern, consent_deepseek_frames:bool=False, pipeline_version:str=默认, analysis_revision:bool=False}`                                                                                                                                                                                                           | 达成            |
| c | 后端 ai\_jobs payload == worker 读取字段      | 后端发 `{media_url,media_sha256,requested_exercise,consent_deepseek_frames,analysis_id,pipeline_version}`（`orchestrator:153`）；`worker.py:143` 读 `requested_exercise/consent_deepseek_frames`（所需字段均在）；其余为后端簿记                                                                                                                                                                        | 达成            |
| d | job\_type 与能力门控一致                       | `ai_jobs.claim_next_job` 仅当 worker 声明 `motion_unified_v1` 才把 `motion_unified` 加入可领取类型；`capabilities.py:156` 姿态可用才声明该能力                                                                                                                                                                                                                                                           | 达成            |
| e | 旧接口 deprecated                          | `/media/motion-jobs(+/{id})`、`/media/kinetics-jobs(+/{id})`、`/media/analyze-motion` 均 `@router(...,deprecated=True)`（冒烟枚举确认 5 条）                                                                                                                                                                                                                                                 | 达成            |



***

## 5. 422 契约复验



* `backend/tests/test_worker_contract_422.py`：**10 passed**。

* 抽查 3 条断言体：

1. `test_unknown_schema_version_is_rejected_with_path`：422 + `code=="MOTION_RESULT_SCHEMA_INVALID"` + `retryable is False` + `details.field_path=="schema_version"` + `request_id` 非空。

2. `test_invalid_receipts_return_structured_422_with_field_path`（缺 `pose.available` / 外部 `url` / 非 JPEG）：422 + code + retryable=False + `details.field_path` 含 `pose`/`frames[0]`。

3. `test_422_body_never_echoes_payload`：把 `SECRET-DO-NOT-LEAK-...` 塞进 `image_b64`，422 回包文本**不含该秘密串**（无载荷回显）。

* 结论：达成。



***

## 6. 后端启动冒烟



* TestClient 导入 `app.main:app`（新模型 / 路由下无 ImportError）。

* `GET /health/live` → **200** `{"status":"ok","service":"healthmate-api"}`。

* 路由枚举：7 个 `/api/v1/media/motion-analyses*` + 4 个 `/api/v1/harness/voice/{status,synthesize,transcribe,verify-once}` 全部注册。

* 5 个旧接口 `deprecated=True`。

* 结论：达成。



***

## 7. 第 9 节 8 维度逐项状态



| 维度          | 状态           | 证据 / 说明                                                                                                                 |
| ----------- | ------------ | ----------------------------------------------------------------------------------------------------------------------- |
| 契约可靠性       | **部分达成**     | 422 fixture / 结构化错误 / 跨用户 404 / 迁移三路径 / 不盲目重试均有代码与测试；但 "失败注入（中途退出 / 超时不重复计费）" 未单独回归，cancel 端点未提供                        |
| 六类识别        | **未达成（待评测）** | 代码 / 评分门控就绪；REHAB24-6 120 段已声明不作最终独立集；独立受试者冻结集、全样本准确率 / Macro-F1 / 拒识矩阵 / 95% CI 未跑                                     |
| 400 类识别     | **未达成（待评测）** | Kinetics top-k 已接入候选；目标子集冻结、Top-1/Top-5 / 混淆 / 类外误报未跑；对外不得宣称 "400 类准确率"                                                 |
| DeepSeek 增益 | **未达成（待评测）** | 视觉复核 + 文本点评链路与降级 / 缓存就绪；A/B/C/D 四臂消融、净增益 / 额外误判 / 成本未跑                                                                  |
| 关键帧 / 点评    | **部分达成**     | 3–4 关键帧、frame:id 引用校验、不编造数值已实现；人工事件容差 / 引用正确率 / 双人盲评未做                                                                  |
| 数值评分        | **部分达成**     | 非六类 / 姿态不可用一律 `score.available=false` + reason\_code（`NO_VALIDATED_SCORER` 等）；六类评分器角度 / 次数误差与机位边界未独立验证                  |
| 语音          | **部分达成**     | VoiceProvider/TencentCloudVoiceProvider/ 分段 TTS/SDK stub 测试全绿；**ASR/TTS 真实连通未执行（密钥空→503）**；真机录音 / 播报 / 麦克风权限 / 弱网本地不可验证 |
| 安全与隐私       | **部分达成**     | 云端关键帧纯色画布 + 像素检查、provider\_invocations 脱敏列、越权 404 代码就绪；同意撤回级联清理、删除 / 导出、健康禁忌文本抽检未单独回归                                   |



***

## 8. 第 10 节 发布门禁逐项状态



| 门禁项                         | 状态          | 说明                                                                   |
| --------------------------- | ----------- | -------------------------------------------------------------------- |
| 后端 / Worker / 小程序现有测试通过     | **达成**      | backend 218、worker 117、小程序 79 全过（小程序陈旧断言已修复为断言新行为，见 §1.1）            |
| 新契约 / 迁移 / 权限 / 隐私 / 成本限额通过 | **本地达成**    | 见 §2/§3/§4/§5；真实云 / MySQL 未跑                                         |
| 真机弱网与未授权麦克风处理               | **本地不可验证**  | 列待真机                                                                 |
| 外部语音各一次连通后停测                | **未执行**     | TENCENT 密钥为空，`VOICE_LIVE_VERIFY_ENABLED=false`                       |
| DeepSeek 阶段状态与降级有明确 UI      | **达成**      | `review_status=used/unavailable/skipped`、`summary.degraded`、前端 badge |
| 独立集结果与对外文字一致                | **达成（口径侧）** | 能力表明确禁止 "准确率 XX%/ 医疗级 / 400 类全能"；无独立集结果故不宣称数字                        |
| 未达标则功能开关关闭、旧结果可读、新入口不全量     | **应保持**     | 云端仍在 healthmate-api-021、MySQL 仍在 0023；本次未部署、未开启新入口                   |



***

## 9. 遗留待配置 / 未达标项（如实列出）



1. **云端部署未执行**：云端仍 healthmate-api-021，MySQL 仍在 0023；生产 `alembic upgrade head`、灰度切写未做。

2. **外部语音真实连通未执行**：`TENCENT_SECRET_ID/KEY` 为空 → `configured=false`，真实 ASR/TTS 返回 503；ASR/TTS 各一次最小连通 + `provider_connection_checks` 落账未做。

3. **真机项不可本地验证**：录音 / 分段播报、麦克风权限拒绝引导、弱网降级。

4. **独立集真实评测未执行**：六类 / 400 类准确率、DeepSeek 四臂消融、关键帧人工盲评、评分器误差均未跑；对外不得宣称准确率。

5. ~~小程序 1 个陈旧测试失败~~ **已解决**（§1.1）：`settingsPresentation.test.js` 已改为断言新行为（voice/status + 手动 verify-once、无自动 voice-test），复跑 79/0。

6. 同意撤回 / 删除导出级联清理、健康禁忌文本抽检、失败注入回归未单独执行。



***

## 10. 改动文件总清单（git status，只读未提交）

修改（M）：



* ai-worker：`healthmate_worker/{capabilities,client,visualize}.py`、`models/{kinetics_clip,kinetics_runtime}.py`、`processors/__init__.py`、`worker.py`、`tests/test_worker.py`

* backend：`app/api/v1/{ai_config,harness,media,worker}.py`、`app/core/{config,errors}.py`、`app/harness/{collaboration,tools,voice}.py`、`app/models/{__init__,models}.py`、`app/schemas/{ai_config,worker}.py`、`app/services/ai_jobs.py`、`requirements.txt`、`tests/test_harness_kernel.py`

* miniprogram：`pages/home/index.js`、`pages/media/index.{js,wxml,wxss}`、`pages/settings/ai/index.{js,wxml}`、`utils/request.js`

新增（??）：



* ai-worker：`healthmate_worker/processors/motion_unified.py`、`healthmate_worker/result_contract.py`、`tests/{test_motion_unified,test_worker_client_contract}.py`

* backend：`app/harness/motion_evidence.py`、`app/services/motion/{decision,orchestrator,provider_gateway,text_summary,vision_review}.py`、`migrations/versions/0024_motion_voice_harness.py`、`tests/{test_harness_motion_tools,test_motion_unified_chain,test_voice_tencent,test_worker_contract_422}.py`

* benchmark：`benchmark/motion-unified-v1/`

* docs：本批次 6 份 `HEALTHMATE_*_2026-09-29.md`（含本报告）

* miniprogram：`tests/mediaUnifiedFlow.test.js`、`utils/motionUnifiedView.js`

> 说明：未执行 git commit/push；临时验证脚本与临时 db 已全部清理。