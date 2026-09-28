# 上线 / 比赛验收清单

本轮代码与本地测试结果见VERIFICATION.md；下列未勾选项需要项目拥有者在实际微信环境验证，不能以接口实现代替上线验证。

## 代码本地验收

- [x] 配置保护、独立迁移、PORT、live/ready、Docker数据库异常恢复
- [x] SQLite/MySQL8.4迁移和队列/API自动测试
- [x] Worker离线测试、真实MediaPipe/HTTP任务闭环（静态样本）
- [x] JSON/JS语法、源码秘密模式检查、文档操作步骤

## 资源和发布

- [ ] 持久MySQL、utf8mb4、网络权限、备份、迁移到 `0022_agent_decision_id`（当前唯一 head）
- [ ] 生产变量与三个独立随机密钥，保留凭据加密密钥备份
- [ ] 构建目录backend/Dockerfile；默认启动不迁移；探针live/ready分别设置
- [ ] 正式云托管发布、重启/扩容无DDL、ready=mysql/cloud_ref
- [ ] 同一服务HTTPS公网域名供Worker；CLOUD_HEADER_LOGIN_ENABLED=false
- [ ] 小程序AppID/环境ID/服务名，DEV_LOGIN=false，发布隐私指引
- [ ] CloudBase仅创建者读写，两个不同微信账号权限隔离验证

## 真机 / 真实AI

- [ ] wx.login→JWT→资料/目标/记录/图表/周报/待确认计划
- [ ] 首页健康提醒→依据与建议→具体行动→健康助手解释→用户确认计划
- [ ] 用真实在线回答填入 33 案例双人评审表；两名评审独立完成并生成正式报告
- [ ] uploadFile→tempURL→register-cloud→原job轮询→完成
- [ ] 源URL过期→waiting_source_refresh→同job刷新恢复
- [ ] 三种真实动作视频各验证次数/低可见度/事件时间
- [ ] 真VLM models/图像smoke/餐食校正/确认后入库
- [ ] 关闭VLM：姿态和普通业务仍工作，识餐明确OFF
- [ ] 停Worker：任务排队、有等待上限；恢复后续租/完成
- [ ] 配置真实DeepSeek，分别验证无Key/用户Key/系统Key/限流
- [ ] 真实数据导出不带秘密/签名URL；逐云文件确认删除后清DB
- [ ] 对账上传成功注册失败的孤立文件；留存真实演示视频备份

事件关键帧已有受限、脸部模糊、7天保留的任务内预览；长期对象存储、真实上游token流式、自动服务端云文件删除证明仍是deferred。全部真实限制见FIX_REPORT.md。
