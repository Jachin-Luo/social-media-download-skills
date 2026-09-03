---
name: social-media-download-skills
description: 下载社媒视频和图片并归档到云盘。
version: 0.2.0
author: 雒玉坤 (Jachin), Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [社媒下载, B站, 抖音, 小红书, 百度网盘, 飞书云盘]
    related_skills: [lark-drive]
---

# Social Media Downloader Skill

用于个人下载和归档 B 站、抖音、小红书的公开视频或图片。固定调用指定的上游 CLI/Skill，不实现平台解析逻辑，不做跨项目降级；视频上传百度网盘，图片上传飞书云盘，任务结束后清理本次产生的本地临时文件。注意：Skill 文件可加载不等于运行环境已就绪；每次首次接入或环境变更后，必须先验证固定后端命令、依赖和授权状态。

## When to Use

- 用户提供 B 站、抖音或小红书链接并要求下载。
- 用户要求把下载的视频保存到百度网盘。
- 用户要求把抖音或小红书图片上传到飞书云盘。
- 用户要求清理本次下载产生的服务器文件。

不要用于：绕过付费墙、DRM、登录权限或平台访问控制；批量抓取他人账号；未经授权传播受版权保护的内容。

## 固定后端

| 平台/功能 | 固定后端 |
|---|---|
| B 站视频 | `yutto` 官方 CLI/Agent Skill |
| 抖音视频、图片 | `jiji262/douyin-downloader` |
| 小红书视频、图片 | `JoeanAmier/XHS-Downloader` CLI |
| 视频上传 | `bdpan-storage` 官方 Skill（当前安装名为 `baidu-drive`） |
| 图片上传 | `lark-cli drive +upload` |

不使用备用下载器，不因失败自动切换后端。上游命令失败时，保留平台、阶段、命令退出码和原始错误信息。

## Prerequisites

- Linux 主机。
- `uv`、Python 3.12+ 和 `ffmpeg`/`ffprobe`。
- `yutto` 已安装（B站登录是它自家的 `~/.config/yutto/auth.toml`，`yutto auth status` 有效才算就绪，未登录跑 `yutto auth login` 扫码）；官方 Skill 可按 `npx skills add https://github.com/yutto-dev/yutto --skill bilibili-video-download` 安装。
- 第三方下载库不进 Skill 本体，首次使用跑 `scripts/init_wizard.py --non-interactive --yes` 按 `scripts/requirements.lock` 拉到新数据目录（`$SOCIAL_DL_DATA_DIR`，默认 `~/.local/share/social-media-download-skills/`）：`jiji262/douyin-downloader`、`JoeanAmier/XHS-Downloader`；`yutto`/`bdpan`/`lark-cli` 是系统 CLI，只锁版本。全新安装时 vendor 内只有 `config.example.yml`，按其 README 建出 `config.yml` 并填好 Cookie，否则下载会被反爬拦。
- `douyin-downloader` 的本地 `config.yml` 由编排脚本每次派生隔离版（`link: []`），Cookie 仍按其 README 准备，含冒号的值加引号，文件权限 `600`。
- `XHS-Downloader` 同上由向导拉取；其 `pyproject.toml` 要求 Python 3.12+（用隔离 `uv` 环境）。它的登录是自家的 `<vendor>/Volume/settings.json`（`cookie` 字段），按其 README 登录填好，向导只检查该字段非空、不碰值。
- `bdpan-storage` 官方 `baidu-drive` Skill 已安装，`bdpan` CLI 已安装；首次使用仍需完成百度网盘授权。
- `lark-cli` 已配置并使用 `--as user` 完成飞书云空间授权。
- 需要上传到指定飞书文件夹时，必须取得目标 `folder_token`；未提供时使用用户明确允许的 Drive 根目录。

## How to Run（新架构：Python 编排优先）

```bash
# 首次（一次性）：检查依赖+登录+拉库+写 config.local.json
python3 scripts/init_wizard.py --non-interactive --yes
# 日常：下载+校验+归档一站式（远端对账自动做，可 --dest 覆盖）
python3 scripts/social_dl.py run --url "<分享链接>" --cleanup
```

`social_dl.py` 内部做：任务目录（`tempfile.mkdtemp`，名中带任务开始时间）、隔离 task-config、数组传参调子进程、串行上传、远端对账、`try/finally` 清理。不得绕过它手拼 bash；目录路径只由程序生成，不直接接受用户提供的任意删除路径。下面各节是编排器内部调用的后端细节（排障时参考）：

### B 站视频

```bash
yutto "<B站链接>" -d "<task_dir>"
```

当前已验证 yutto 2.3.0 使用 `-d/--dir` 指定输出目录；以后升级版本后仍应以 `yutto --help` 为准，如果命令不接受该参数，先停止并返回帮助输出，不要猜参数。

如果视频预计较大、时长未知但可能超过前台工具等待上限，必须从一开始就在后台执行 yutto，保存独立日志、进程 ID 和任务状态；不能先以前台方式启动再等待超时。不要把前台超时直接当作下载失败。若任务目录中已有完整的 `.m4s` 音视频流，先检查文件大小和文件头，再用同一任务目录重新运行 yutto 完成合并；恢复前记录已有文件，恢复后确认没有重复或覆盖异常，不更换后端、不删除可恢复的流文件。

### 抖音视频或图片

在新数据目录（`$SOCIAL_DL_DATA_DIR/douyin-downloader`）中调用当前 CLI 入口，输出目录必须指向 `<task_dir>`。必须使用任务专属 config（`link: []`），不得直接复用仓库根 `config.yml`（以上全由 `scripts/social_dl.py` 自动做）：

```bash
# 以下由 scripts/social_dl.py 自动做，手工排障才用：
# 从 vendor config.yml 派生任务 config 并清空 link
cd $SOCIAL_DL_DATA_DIR/douyin-downloader
uv run douyin-dl --url "<抖音链接>" --path "<task_dir>" --config <task_dir>/task-config.yml
```

首次接入前运行 `uv run douyin-dl --help`，按当前版本的实际参数构造命令，不得根据旧 README 猜测参数。

> **注意**：CLI 参数为 `-u/--url`（链接）和 `-p/--path`（输出目录），还有 `-c/--config`（配置文件）和 `-t/--thread`（并发数）。

### 小红书视频或图片

```bash
cd $SOCIAL_DL_DATA_DIR/XHS-Downloader
uv run python -c 'from source.CLI.main import cli; cli()' \
  --url "<小红书链接>" \
  --work_path "<task_dir>" \
  --folder_name "media"
```

图文只下载指定图片时，可增加：

```bash
--index "1 3 5"
```

下载前可用 CLI 帮助确认当前参数：
```bash
cd $SOCIAL_DL_DATA_DIR/XHS-Downloader
uv run python -c 'from source.CLI.main import cli; cli()' --help
```

### 飞书云盘图片上传

必须使用 `lark-cli drive +upload`，先阅读 `lark-drive` 的上传规则。`lark-cli` 当前版本强制要求 `--file` 为当前工作目录下的相对路径（绝对路径报 `unsafe file path`），且中文长文件名不得用 `ls` 组装变量（换行会拼成超长文件名报 `file name too long`）。标准做法：复制到 `/tmp/dy-upload-<time>/` 中转目录，`cd` 进去后用 glob 逐个上传：

```bash
mkdir -p /tmp/dy-upload-<time>/ && cp "<task_dir>"/装书/*/*.jpg /tmp/dy-upload-<time>/
cd /tmp/dy-upload-<time>/
lark-cli drive +upload --file "./<local_image>" --folder-token "<folder_token>" --as user --format json
# 批量时用 for f in *_2.jpg *_3.jpg ...（不要用 f=$(ls ...)），逐项等待成功，不并发写同一目标目录
```

上传到指定文件夹：

```bash
lark-cli drive +upload \
  --file "<local_image>" \
  --folder-token "<folder_token>" \
  --as user \
  --format json
```

上传到根目录时省略 `--folder-token`，不要传空字符串。每个图片文件单独上传，并根据 JSON 结果确认上传成功后再继续。

### 百度网盘视频上传

把本地视频交给 `bdpan-storage` 官方 Skill，目标目录使用用户指定的百度网盘路径。后台上传时必须把 stdout/stderr 分别重定向到本次任务目录的独立日志，并记录进程 ID；等待官方 Skill 返回成功结果，并记录远程文件路径。上传进行中查询远端目录只用于观察，不得因文件暂时不可见而重复启动上传或直接判定失败；不要自行实现百度网盘 API，也不要输出 Token、Cookie 或密码。

## Procedure

1. **解析输入**：清洗分享文本，提取一个或多个合法 URL；识别平台和媒体类型。无法识别时直接返回 `invalid_input`，不尝试其他平台。
2. **创建临时目录**：使用任务唯一目录，并在创建前记录任务开始时间；任务归档文件夹名称必须使用这个开始时间，而不是超时恢复或上传时重新取的时间。完成标准：目录存在且不属于已有用户目录。
3. **执行固定下载器**：只调用对应后端的当前 CLI/Skill。保存 stdout、stderr 和退出码；退出码非零或没有生成目标文件时进入错误结果。
4. **验证文件**：确认文件存在、大小大于 0，并按扩展名和 MIME/文件头做基本校验。完成标准：每个待上传文件均可读。
5. **创建任务归档目录**：图片任务在用户指定的目标文件夹下，先用 `lark-cli drive +create-folder --folder-token <parent_folder_token> --name "<YYYY-MM-DD_HH-mm-ss>" --as user --format json` 创建本次任务专属子文件夹；文件夹名称使用任务开始时记录的本地时间，精确到秒。
6. **上传视频或图片**：视频调用 `bdpan-storage`，图片调用 `lark-cli drive +upload`，图片目标使用上一步返回的新建任务文件夹 token。逐项等待上传成功，不并发写入同一个目标目录。
7. **验证远程结果**：记录百度网盘远程路径，或读取 `lark-cli` JSON 中的文件夹/文件 token 和链接；再用 `drive files list` 读取该任务文件夹，确认所有文件的 `parent_token` 都是本次任务文件夹 token。远端结果缺失时视为上传失败。
8. **最终清理**：无论下载或上传成功、失败，都执行一次清理，只删除本任务创建的临时目录及其内容。不得删除用户原有目录或文件。完成标准：目录不存在；若删除失败，记录残留路径和错误。
9. **返回结果**：成功时列出平台、标题（若可得）、媒体类型、任务文件夹名称/链接、文件远程链接和本地清理状态；失败时列出失败阶段、固定后端、退出码和原始错误信息。

### 后台模式（全部任务强制）

下载与上传文件一律走 `exec_bg`：后台脱离启动 + `<log>.pid` + 独立日志 + 5 秒轮询。调用方超时（返回 124）时进程留后台继续跑，凭 task 目录用 `social_dl.py poll --task <dir>` 只读续看（pid 存活 + 日志尾 + 文件数），绝不重复启动；完成后再用 `run --url <链接> --task <dir>` 断点续跑（日志有 `[exit 0]` 且文件在则跳过下载）。秒级元数据调用（建文件夹、列表）保持同步。进程消失但无 `[exit]` 标记时，先检查最终文件和临时流文件，再决定恢复或失败；不能仅凭进程消失判定成功。任务完成或失败后，按清理规则删除本次临时目录，不要求建立持久化任务队列或跨会话自动接管。

## Error Handling

不做降级、不换后端、不静默吞掉错误。错误格式：

```text
[失败阶段] <解析|下载|上传|清理>
平台：<平台>
后端：<固定项目>
退出码：<code 或 N/A>
错误：<原始 stderr 或 API 错误>
本地清理：<已完成|失败及残留路径>
```

如果上传失败但本地清理成功，必须明确说明远端没有确认成功，不能把任务报告为成功。

## Pitfalls

- `yutto` 和 `XHS-Downloader` 当前都要求 Python 3.12+；使用隔离的 `uv` 环境，不修改系统 Python。
- `jiji262/douyin-downloader` 的 CLI 会把 `--url` 追加到 config 的 `link` 列表（`cli/main.py:227-228`），直接复用含示例用户链接的 `config.yml` 会出现 `Found 2 URL(s)` 并误下载示例账号作品。每次任务必须派生 `<task_dir>/task-config.yml` 并清空为 `link: []`。
- `lark-cli drive +upload` 的 `--file` 必须是相对路径（`./name`），绝对路径直接报 `unsafe file path`；中转目录建议用 `/tmp/dy-upload-<time>/`，`cd` 进去再传。批量上传禁止 `f=$(ls ...)` 组装，必须用 shell glob（`for f in *_2.jpg ...`），否则换行符拼成一个超长文件名。
- `jiji262/douyin-downloader` 的 `config.yml` 中 Cookie 值若包含冒号（如 `passport_csrf_token: n: <…>`、`sid_guard: rd: <…>`），YAML 解析器会将冒号视为嵌套映射键而报错。修复方法：用引号包裹整个值，如 `passport_csrf_token: "n: <你的值>"`。
- `jiji262/douyin-downloader` 的 CLI 参数可能随版本变化；使用当前版本 `--help`，不要照抄旧命令。
- 抖音图文作品（note 类型，URL 含 `/note/`）不要预判死：先跑固定后端，成功就收；只有 `/aweme/v1/web/aweme/detail/` 返回空响应（anti-bot 拦截）且重试无效时，才告知用户后端无法解析，不尝试 curl/浏览器等降级方案。`yt-dlp` 不支持抖音 note URL，不换后端。
- `XHS-Downloader` 是 GPL-3.0，直接调用其 CLI，不复制源码到本 Skill；保留上游许可证和版权信息。
- 上游下载器可能需要 Cookie、扫码登录或浏览器辅助；认证失败直接返回错误，不尝试绕过验证码。
- 服务器无图形界面时，不要假设会弹出可交互浏览器；应在本地完成登录后通过 SCP/SFTP 传输配置，或由用户自行在服务器配置远程桌面。
- Cookie 属于登录凭证，禁止在聊天中索取、接收、回显或写入命令参数；通过 SCP/SFTP 传输后将 `config.yml` 权限设为 `600`，只验证下载结果。
- 飞书上传使用 `--folder-token` 时必须传真实 folder token；上传根目录时省略该参数。
- 百度网盘 Token、Cookie、飞书授权信息不得写入 Skill、命令行日志或最终回复。
- `bdpan upload` 接受本地绝对路径且会自动建中间目录；`bdpan rm` 默认要交互确认，脚本里删东西必须带 `-f`。
- 清理只能针对自动生成的任务目录；禁止使用 `rm -rf /tmp/*`、`rm -rf ~/Downloads/*` 等宽范围命令。
- 用户未明确授权或内容受付费/权限保护时，停止任务并说明原因。
- 默认不对下载的图片/视频做 vision 识别（省 token），只做文件级校验（存在、>0、ffprobe/文件头）；用户明确要求看图时才看。

## Verification

完成任务前必须确认：

- 固定后端命令实际执行过，且退出码已记录。
- 下载文件存在、大小大于零；视频可由 FFprobe 读取，图片可由图像工具读取。
- 百度网盘回执包含远端路径或文件 ID，并通过列表/查询确认文件存在。
- 飞书云盘回执包含文件 token 或链接，并确认目标位置正确。
- 最终任务目录已删除，或明确报告残留路径和删除错误。
- 最终报告分别列出：解析状态、下载状态、上传状态、清理状态；不合并成一个未经验证的“成功”。
