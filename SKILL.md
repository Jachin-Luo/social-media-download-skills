---
name: social-media-download-skills
description: >
  下载 B站/抖音/小红书的公开视频或图片并归档到云盘：上传目的地可配置
  （默认视频存百度网盘、图片存飞书云盘，可在 init 时询问确定、改配置或
  运行时用 --video-dest/--image-dest 覆盖），自动建归档目录、写溯源 manifest、
  远端对账、清理本地临时文件。
  当用户提供这三类平台的分享链接（含 b23.tv / v.douyin.com / xhslink.com 短链）
  并要求下载、保存、归档、备份时使用。不用于绕过付费墙、DRM、登录权限，
  不用于批量抓取他人账号，不用于传播受版权保护的内容。
version: 0.3.2
author: 雒玉坤 (Jachin), Hermes Agent
license: MIT
allowed-tools: Bash, Read, Write
platforms: [linux]
metadata:
  hermes:
    tags: [社媒下载, B站, 抖音, 小红书, 百度网盘, 飞书云盘]
    related_skills: [lark-drive, baidu-drive]
---

# Social Media Downloader Skill

个人下载归档 B 站、抖音、小红书的公开内容。固定后端、不降级、不换源；全链路由 Python 编排（`scripts/social_dl.py`），与上游 CLI 通过**适配器 + 参数协商**解耦——上游改参数名会自动适配或明确报错，不会静默下载错误内容。

## When to Use

- 用户提供 B 站、抖音或小红书链接并要求下载。
- 用户要求把下载内容归档到百度网盘或飞书云盘（目的地可配置：默认视频→百度、
  图片→飞书，也可全部走其中一侧）。

不要用于：绕过付费墙/DRM/登录权限；批量抓取他人账号；未经授权传播受版权保护的内容。

## 固定后端

| 平台/功能 | 固定后端 | 安装方式 |
|---|---|---|
| B 站视频 | `yutto` | PyPI 官方包，`uv tool install`（不 clone 源码） |
| 抖音视频、图片 | `jiji262/douyin-downloader` | 源码 clone（PyPI 同名包是另一个项目，勿用） |
| 小红书视频、图片 | `JoeanAmier/XHS-Downloader` | 源码 clone（不在 PyPI） |
| 视频上传 | `bdpan-storage` 官方 Skill | 系统已装 |
| 图片上传 | `lark-cli drive +upload` | 系统已装 |

上游参数不硬编码：适配器声明"能力 → 候选参数"，运行时用 `--help` 协商取值；上游改名自动命中新参数，真改没了则抛 `BackendError` 明确报错。升级上游后跑 `doctor` 自检。

## How to Run

```bash
# 0. 契约自检（首次接入 / 升级上游后必跑）：后端可用性 + 登录态 + 参数协商
python3 scripts/social_dl.py doctor

# 1. 首次初始化：交互式询问要启用哪些平台（非交互必须 --platforms 显式指定）
python3 scripts/init_wizard.py                          # 交互式
python3 scripts/init_wizard.py --platforms all --yes    # 非交互全平台

# 2. 日常：下载 + 校验 + 溯源 manifest + 归档 + 远端对账 + 清理
python3 scripts/social_dl.py run --url "<分享链接>" --cleanup

# 归档名默认时间戳；可用模板生成可读名（占位符 {platform} {title} {author} {date} {day}）
python3 scripts/social_dl.py run --url "<链接>" --cleanup \
    --name-template "{platform}_{title}_{day}"

# 3. 后台任务续看 / 单独重传
python3 scripts/social_dl.py poll --task <task_dir>
python3 scripts/social_dl.py upload-feishu --task <task_dir> --name "<归档名>"
python3 scripts/social_dl.py upload-baidu  --task <task_dir> --name "<归档名>"
```

编排器职责：任务目录、隔离 task-config、数组传参、按媒体类型分流上传（目的地由配置决定，见下）、远端对账、`finally` 清理。不要绕过它手拼 bash。日志走 stderr，stdout 只有一份 JSON 结果（可直接 `json.loads`）。

## Procedure

1. **解析输入**：提取合法 URL，识别平台。无法识别直接返回 `invalid_input`。
2. **后端检查**：`available()` 确认后端已装；`prepare()` 做任务准备（抖音派生 `link: []` 的隔离 config）。
3. **下载**：`exec_bg` 后台执行（pid + 日志 + 轮询）。退出码非零或 0 文件即失败；0 文件时自动查登录态并给出可行动提示（Cookie 过期是 exit 0 但 0 文件的首要原因）。
4. **验证文件**：存在、>0；视频 ffprobe 校验（环境缺 ffprobe 时跳过视频探测并明确告警，不判坏），图片做头+尾完整性校验（截断的 JPEG 头是完整的，只验头会放过）。
5. **溯源 manifest**：在任务目录写 `manifest.json`（来源 URL、平台、标题、作者、下载时间、文件清单），随归档一起进云盘。
6. **归档命名**：默认时间戳；`--name-template` 可用 `{platform}` `{title}` 等占位符。模板清洗非法字符，标题缺失回退"无标题"。模板里的未知占位符直接报错（可用：`{platform}` `{title}` `{author}` `{date}` `{day}`）。
7. **分流上传**：auto 模式按配置分流——视频→`video_dest`（默认百度）、图片→`image_dest`（默认飞书），混合媒体绝不混装。目的地优先级：`--dest baidu/feishu`（全部走一侧）> `--video-dest`/`--image-dest`（单次覆盖）> `config.local.json` > 默认值；非法值明确报错。init 时向导会逐项询问用户。飞书先建任务文件夹再串行上传（同名文件自动去重）；百度上传后 `--json` 对账。
8. **远端对账**：飞书逐文件比对 `parent_token`；百度数非目录项。对账不过即失败，明确报告"远端未确认成功"。
9. **清理与报告**：`--cleanup` 时在 `finally` 里删除任务目录并如实上报 `cleaned`/`residue`；不带 `--cleanup` 则保留目录（供复查/重传）并上报路径。成功列出平台、标题、归档名、远程位置、清理状态；失败列出阶段、后端、退出码、原始错误和可行动的 hint。

## Error Handling

不做降级、不换后端、不静默吞错。JSON 错误结果必含：`stage`（parse/download/upload/cleanup/backend）、`platform`、`backend`、退出码、原始错误、`hint`（可行动建议）。上传失败但清理成功时必须明确说明远端未确认成功，不得报为成功。`BackendError`（参数协商失败）表示上游破坏性变更，先跑 `doctor`、确认后更新适配器 CAPS，绝不猜参数。

## Pitfalls（精选）

- 抖音 CLI 会把 `--url` 追加进 config 的 `link` 列表——复用含示例链接的 config 会误下载示例账号。隔离 task-config（`link: []`）由编排器自动完成。
- Cookie 过期 → 下载器 exit 0 但 0 文件。看到 `no_media_files` 先看结果里的 `login_ok`/`hint`，不要怀疑链接。
- `lark-cli drive +upload` 的 `--file` 必须是相对路径（staging + `./name`），批量禁止 `f=$(ls ...)`（换行拼成超长文件名），用 shell glob。
- douyin config 的 Cookie 值含冒号（`passport_csrf_token`、`sid_guard`）必须加引号，否则 YAML 解析报错。
- 抖音图文（URL 含 `/note/`）被 anti-bot 拦截属已知不可解：直接告知用户，不降级不换后端。
- yutto 合并失败会留下完整 `.m4s` 音视频流：编排器能识别它们，用同一任务目录重跑 yutto 即可恢复合并。
- Cookie/Token 属登录凭证：禁止聊天中索取、回显或写入命令参数；config 权限 600；命令落盘日志已自动脱敏。
- 清理只针对自动生成的任务目录；禁止 `rm -rf /tmp/*` 等宽范围命令。
- `XHS-Downloader` 是 GPL-3.0：只调其 CLI，不复制源码；保留上游许可证。**PyPI 上的 `douyin-downloader` 是 HeLiangHIT 的项目，与 jiji262 无关**，改 pip 安装会装错——详见 `references/github-backends.md`。

## Verification

完成前必须确认：固定后端实际执行过且退出码已记录；文件存在且通过完整性校验；远端回执含路径/token 且对账通过；manifest 已随归档上传；任务目录已删或如实报告残留。最终报告分别列出：解析、下载、上传、清理状态——不合并成一个未经验证的"成功"。
