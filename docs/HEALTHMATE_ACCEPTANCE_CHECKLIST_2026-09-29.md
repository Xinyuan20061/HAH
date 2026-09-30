# 动作点评与语音闭环：验收清单

> 版本：2026-09-29；状态：**工程项已本地复验（见 [HEALTHMATE_VERIFICATION_2026-09-29.md](HEALTHMATE_VERIFICATION_2026-09-29.md)），
> 独立集评测 / 云端部署 / 外部语音真实连通 / 真机项仍未达成**。
> `[x]` = 本次独立复验有直接证据；`[ ]` = 未达成或本地不可验证。
> 把规格第 9 节每个维度的"最小交付证据"转成可勾选项；对应"禁止替代口径"一并列出，防止用
> 一次成功演示代替验收。对应协议见 `benchmark/motion-unified-v1/`。

## A. 契约可靠性

- [ ] 统一任务可创建、轮询、断网恢复、取消、重试（仅 terminal failed 且新 pipeline revision）。（创建/轮询/重试已实现并有测试；取消端点未单独提供，待补）
- [x] 422 回归 fixture 通过：缺字段、多字段、帧数量超 4、无效 JPEG、拒识仍打分、版本不匹配。（test_worker_contract_422.py 10 项全过）
- [x] 422 返回结构化 `{code,message,request_id,details.field_path,retryable=false}`，可从 request ID 定位字段。（抽查断言通过；错误回包不含载荷回显）
- [x] 跨用户隔离：A 不能读写 B 的任务/结果/语音配置（404）。（`run.user_id != user.id -> 404`，后端测试套件含隔离用例）
- [x] 迁移 0024 在 SQLite 的 fresh/incremental/repeat + downgrade 0023 再升级三路径通过；MySQL 方言离线 DDL 编译通过。（MySQL 生产库真实三路径待云端执行）
- [ ] 失败注入：Worker 中途退出、网络中断、模型超时后不出现重复计费/重复结果。（ProviderGateway 预算/不盲目重试代码已具备，未单独做失败注入回归）
- [ ] 禁止替代：不得只靠"一次成功演示"勾选本节。

## B. 六类识别

- [ ] 已建立独立受试者冻结集（REHAB24-6 120 段不再当最终测试集），受试者零重叠。
- [ ] 报告全样本准确率（拒识/失败计错）、覆盖率、Macro-F1、逐类召回、拒识矩阵、95% Wilson 区间。
- [ ] 至少含一组未知动作/非锻炼片段（S3/S4）。
- [ ] 禁止替代：不得只报已接受样本准确率或演示样本。

## C. 400 类识别

- [ ] 已定义产品相关目标子集清单与未知类定义（冻结 `target_subset.json`）。
- [ ] 报告 Top-1/Top-5、混淆矩阵、类外误报率、分机位结果。
- [ ] 400 类整体声明有相应完整数据支撑；否则只声明"目标子集"。
- [ ] 禁止替代：不得用 softmax 100% 称"准确率 100%"。

## D. DeepSeek 增益

- [ ] 同一冻结集跑 A/B/C/D 四臂（本地 / 本地+Kinetics / 本地+DeepSeek / 三者融合）。
- [ ] 报告净增益（附 CI）、额外误判逐条、拒识变化（正确拒识 vs 错拒拆开）、P50/P95 延迟、每任务成本。
- [ ] 禁止替代：不得只凭"点评更像人说话"判定增益。

## E. 关键帧/点评

- [ ] 人工标注关键事件容差（草案 ±0.5s）下的事件级 F1 与时间 MAE。
- [ ] 引用正确率（frame_id 存在且支撑该句）、幻觉率（编造数值/医疗结论）。
- [ ] 可执行建议双人盲评（模板 `benchmark/motion-unified-v1/human_review_template.csv`），分歧第三人复核。
- [ ] 模型输出不改变次数/角度/时间等数值事实。
- [ ] 禁止替代：不得把文字流畅度当动作正确率。

## F. 数值评分

- [ ] 六类评价器分别验证：次数 MAE/完全正确率/±1 正确率、关键角度误差（度）。
- [ ] 评分一致性（重复跑波动）与机位边界（正面 vs 侧面）已测。
- [ ] 无评价器动作结果 `score.available=false` 且 reason_code 正确，抽审无"给了分"。
- [ ] 禁止替代：不得让 400 类都显示分数。

## G. 语音

- [x] SDK stub 与错误注入覆盖（后端 218 / Worker 117 / 小程序 79 测试全绿；`voice_verify_checklist.md` V01–V15 逐条勾选待答辩前人工核对）。
- [ ] ASR、TTS 各完成一次最小真实连通验证后停测；`provider_connection_checks` 记录指纹与 RequestId。（未执行：TENCENT_SECRET_ID/KEY 为空，真实调用应返回 503）
- [x] 设置页显示"上次验证时间"，`VOICE_LIVE_VERIFY_ENABLED=false`，不自动 voice-test。（设置页已改为 voice/status + 手动 verify-once，无自动 voice-test）
- [ ] 真机可录音、可顺序播报、拒绝麦克风权限有引导、弱网降级可读。（本地/真机不可验证）
- [ ] 禁止替代：不得反复消耗免费额度做回归。

## H. 安全与隐私

- [x] 云端关键帧脱敏像素级抽审通过（默认纯色骨架图 `render_skeleton_canvas`，`assert_desensitized_canvas` 校验背景占比≥0.88、与原帧相关系数≤0.10）。
- [ ] 越权 404、同意撤回、删除/导出流程通过。（越权 404 代码具备；同意撤回与删除/导出级联清理未单独回归）
- [ ] 健康禁忌文本抽检（诊断/停药/胸痛/晕倒/极端节食）按拒答/就医建议处理。
- [x] `provider_invocations` 无原始音视频/图片/完整 prompt/密钥。（列仅 id,user_id,run_id,provider,operation,request_fingerprint,status,latency_ms,token_or_char_count,provider_request_id,cost_estimate,created_at）
- [ ] 禁止替代：不得只有一句免责声明。

## I. 成本与失败案例（第 8.3 节可观测性）

- [ ] 每任务成本、月预算使用、422 比率、拒识率、DeepSeek 降级率、ASR/TTS 成功率有追踪表
      （`benchmark/motion-unified-v1/cost_tracking_template.csv`）。
- [ ] 失败案例不挑选样本，按错误码/置信度排序展示。
- [ ] 发布语与独立集结果一致：未达标时功能开关保持关闭，旧结果可读，新入口不全量开放。
