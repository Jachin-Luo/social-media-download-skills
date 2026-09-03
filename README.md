# social-media-download-skills

个人公开作品下载归档：B 站 / 抖音 / 小红书 → 视频存百度网盘，图片存飞书云盘。

固定后端、不降级、不换源；下载→校验→溯源 manifest→归档→远端对账→清理，全链路由 Python 编排（`scripts/social_dl.py`），耗时任务全部后台跑（pid + 日志 + 轮询）。

与上游 CLI 通过**适配器 + 参数协商**解耦（`scripts/backends/`）：参数名不硬编码，运行时用 `--help` 协商——上游改名自动适配，真改没了明确报错。升级上游后跑 `doctor` 一次即可发现破坏性变更。

> 仅用于下载你有权保存的公开内容。不要绕过付费墙/DRM/登录访问控制，不要批量抓取他人账号，不要传播受版权保护的内容。

## 快速开始

```bash
# 1. 首次初始化：交互式询问要启用哪些平台，检查依赖与登录，安装后端
python3 scripts/init_wizard.py

#    非交互模式必须显式指定平台（--platforms all 或 douyin,bilibili,xiaohongshu）
python3 scripts/init_wizard.py --platforms all --yes

# 2. 契约自检：后端可用性 + 登录态 + 参数协商
python3 scripts/social_dl.py doctor

# 3. 一站式：下载 + 校验 + 溯源 manifest + 归档（视频→百度盘，图片→飞书）+ 清理
python3 scripts/social_dl.py run --url "<分享链接>" --cleanup

#    归档名默认时间戳；想要可读名用模板（{platform} {title} {author} {date} {day}）
python3 scripts/social_dl.py run --url "<链接>" --cleanup \
    --name-template "{platform}_{title}_{day}"

# 4. 后台任务续看 / 单独重传（只读轮询，不重复启动）
python3 scripts/social_dl.py poll --task <task_dir>
python3 scripts/social_dl.py upload-feishu --task <task_dir> --name "<归档名>"
python3 scripts/social_dl.py upload-baidu  --task <task_dir> --name "<归档名>"
```

各家登录是它们自己的文件，向导只做存在性检查、不读值：
`douyin-downloader` → `<数据目录>/douyin-downloader/config.yml`；
`yutto` → `~/.config/yutto/auth.toml`（`yutto auth login` 扫码）；
`XHS-Downloader` → `<数据目录>/XHS-Downloader/Volume/settings.json`。
百度盘（`bdpan whoami`）与飞书（`lark-cli auth status`，`--as user`）也要就绪。
详见 `references/config-setup.md`。

## 目录结构

```
SKILL.md                          # 完整契约：流程、pitfalls、校验标准
scripts/social_dl.py              # 唯一编排入口（run/download/poll/upload-*/doctor）
scripts/init_wizard.py            # 首次初始化向导（询问平台 + 安装后端）
scripts/backends/                 # 平台适配器（参数协商、登录态、进度解析）
scripts/requirements.lock         # 版本锁 + 升级政策 + PyPI 安装边界
tests/test_core.py                # 核心逻辑单测（python3 -m unittest discover -s tests）
references/troubleshooting.md     # 手工排障（各平台手动命令与坑）
references/config-setup.md        # 配置与登录准备、凭证安全红线
references/github-backends.md     # 后端选型调研 + PyPI 同名陷阱
```

## 安装方式与解耦

| 后端 | 安装方式 | 原因 |
|---|---|---|
| yutto | PyPI（`uv tool install yutto==<锁版本>`） | 官方发布，版本化，不 clone 无 git 冲突 |
| douyin-downloader | 源码 clone + 锁定 SHA | **PyPI 同名包是另一个项目**（HeLiangHIT），勿 pip |
| XHS-Downloader | 源码 clone + 锁定 SHA | 不在 PyPI |

第三方库不进本仓库；上游参数由适配器运行时协商，升级不冲突；对账不过即失败，绝不静默交付。

## 升级政策

升级前先读上游 changelog，有 breaking change（参数、配置格式、登录逻辑）必须确认后再升；升级后跑 `python3 scripts/social_dl.py doctor` 验证契约。详见 `scripts/requirements.lock`。
