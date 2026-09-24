# DeepSeek 设置页说明

页面实现：`index.js / index.wxml / index.wxss`

功能：
- 启用/关闭用户自定义模型；
- API Key 密码输入；
- Base URL；
- 模型 ID 自由输入 + 常用快捷项；
- 测试连接；
- 保存应用；
- 删除用户配置并恢复系统默认 Provider。

注意：已保存的 API Key 不会重新返回给前端，因此页面只展示脱敏提示。用户留空 Key 保存时表示沿用原 Key；输入新 Key 才会覆盖。
