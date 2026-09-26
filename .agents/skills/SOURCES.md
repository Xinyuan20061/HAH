# Vendored skill sources

## skyline-skills (7 skills)

- 来源: https://github.com/wechat-miniprogram/skyline-skills
- 发布方: wechat-miniprogram (腾讯/微信官方)
- commit: 050bb071e091c2d0f7ac3e294f1e1514382b3978
- 安装日期: 2026-09-25
- 许可: MIT (见 skyline-skills-LICENSE.txt)
- 安装方式: git clone --depth 1, 将 skills/<name>/ 平铺复制到 .agents/skills/<name>/

技能清单: skyline-overview, skyline-config, skyline-components, skyline-wxss,
skyline-worklet, skyline-scroll-api, skyline-route

更新方式:
```powershell
git clone --depth 1 https://github.com/wechat-miniprogram/skyline-skills.git $env:TEMP\skyline-skills
# 复核 SKILL.md 差异后，重新平铺复制
```
