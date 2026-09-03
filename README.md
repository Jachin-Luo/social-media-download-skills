# social-media-download-skills

个人公开作品下载归档：B 站 / 抖音 / 小红书 → 视频存百度网盘，图片存飞书云盘。

固定后端、不降级、不换源；下载→校验→归档→远端对账→清理，全链路由 Python 编排（`scripts/social_dl.py`），耗时任务全部后台跑（pid + 日志 + 轮询 + 断点续跑）。

> 仅用于下载你有权保存的公开内容。不要绕过付费墙/DRM/登录访问控制，不要批量抓取他人账号，不要传播受版权保护的内容。

## 快速开始

```bash
# 1. 首次初始化：检查依赖与登录，按版本锁拉取下载库，写本地配置
python3 scripts/init_wizard.py --non-interactive --yes

# 2. 一站式：下载 + 校验 + 自动归档（视频→百度盘，图片→飞书）+ 清理
python3 scripts/social_dl.py run --url "<分享链接>" --cleanup

# 3. 后台任务断线后续看 / 续跑（只读轮询，不重复启动）
python3 scripts/social_dl.py poll --task <task_dir>
python3 scripts/social_dl.py run --url "<分享链接>" --task <task_dir> --cleanup
```

各家登录是它们自己的文件，向导只做存在性检查、不读值：
`douyin-downloader` → `<数据目录>/douyin-downloader/config.yml`；
`yutto` → `~/.config/yutto/auth.toml`（`yutto auth login` 扫码）；
`XHS-Downloader` → `<数据目录>/XHS-Downloader/Volume/settings.json`。
百度盘（`bdpan whoami`）与飞书（`lark-cli auth status`，`--as user`）也要就绪。

## 目录结构

```
SKILL.md                  # 完整契约：流程、 pitfalls、校验标准
scripts/social_dl.py      # 唯一编排入口（run / download / poll）
scripts/init_wizard.py    # 首次初始化向导
scripts/requirements.lock # 第三方版本锁 + 升级政策
references/               # 实跑复盘（反爬、后端说明）
```

第三方库不进本仓库，初始化时按锁拉取；`XHS-Downloader` 为 GPL-3.0，只调其 CLI。

## 升级政策

升级前先读上游 changelog，有 breaking change（参数、配置格式、登录逻辑）必须确认后再升，详见 `scripts/requirements.lock`。
