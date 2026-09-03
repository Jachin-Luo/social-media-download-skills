# GitHub 后端调研记录

调研时间：2026-08-28。指标来自 GitHub 仓库/API；“活跃”表示仓库未归档且近期有提交，不代表每个链接都能稳定解析。

## 最终固定组合

| 能力 | 项目 | 当前观察 | 许可证 | 采用方式 |
|---|---|---|---|---|
| B 站视频 | https://github.com/yutto-dev/yutto | 约 2,000 Star；2026-08-27 有提交；未归档；纯 Python CLI；自带 Agent Skill | GPL-3.0 | 使用官方 CLI/Skill |
| 抖音视频/图片 | https://github.com/jiji262/douyin-downloader | 约 9,500 Star；2026-08-26 有提交；2026-08-28 更新；未归档；支持视频、图集、合集和 CLI | MIT | 使用其当前 CLI |
| 小红书视频/图片 | https://github.com/JoeanAmier/XHS-Downloader | 约 1.25 万 Star；2026-08-22 提交；未归档；约 500 次提交；自带 CLI | GPL-3.0 | 使用 CLI，不复制源码 |
| 百度网盘 | https://github.com/baidu-netdisk/bdpan-storage | 约 215 Star；近期持续更新；官方 Agent Skill，支持上传 | Apache-2.0 | 直接复用官方 Skill |
| 飞书云盘 | https://github.com/larksuite/oapi-sdk-python | 飞书官方 Python SDK；2026-08-19 有提交；未归档 | MIT | 本项目优先使用已配置的 `lark-cli drive +upload` |

## 已评估但不采用

- `ScottSloan/Bili23-Downloader`：虽有 MCP，但需要 Bili23 Qt 桌面主程序在后台常驻；Linux 无界面运行还需要 offscreen/Xvfb，不适合本项目。
- `menghuanshiguang/bilibili-downloader-cli`：可执行且支持 CLI，但只有约 2 Star、提交较少，成熟度不如 yutto。
- `nilaoda/BBDown`：已于 2026-05-14 归档。
- `public-clis/bilibili-cli`：自带 Skill，但主要用于 B 站信息、搜索、字幕和互动，不是本项目的视频文件下载后端。
- `Johnserf-Seed/f2`：可作为多平台下载工具，但本项目明确不使用降级后端。
- `Andy-SoulShell/xhs-downloader`：有 CLI/API/MCP，但 Python 3.12+、社区规模很小；本项目优先使用成熟度更高的 JoeanAmier 项目。
- `Kavun-Sama/rednote-scraper`：CLI 技术路线清晰但项目很新、AGPL、仅少量提交；不替换 XHS-Downloader。
- `hostinger-bot/btch-downloader`：更像依赖远程服务的 JS/TS SDK，不采用。

## 使用边界

- Skill 不做跨项目 fallback；固定后端失败时直接返回平台、阶段、退出码和原始错误。
- GPL 项目通过独立 CLI/进程调用，不复制源码到本 Skill；保留上游许可证和版权信息。
- 每次使用前可检查后端版本和帮助；平台登录、Cookie、验证码、代理和风控问题直接反馈。
- 下载内容仅限用户有权保存或使用的内容，不绕过付费、DRM、访问控制或验证码。

## PyPI 安装边界（2026-09-03 实测）

| 上游 | PyPI 包 | 结论 |
|---|---|---|
| yutto-dev/yutto | `yutto`（官方发布，来源一致） | **可放心 `uv tool install yutto==<锁版本>`**，不 clone 源码、无 git 冲突 |
| jiji262/douyin-downloader | `douyin-downloader` 1.0.4 | **同名陷阱**：PyPI 上是 HeLiangHIT/douyin_downloader，与 jiji262 无关。改成 pip 安装会装到错误项目。只能源码 clone |
| JoeanAmier/XHS-Downloader | 无（404） | 不在 PyPI，只能源码 clone |

> 有人想"简化成 pip 安装"时先查这张表。douyin 的同名包陷阱已同步写进 `scripts/requirements.lock` 的 note。