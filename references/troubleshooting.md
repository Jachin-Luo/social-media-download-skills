# 手工排障参考

> 编排器（`social_dl.py`）已自动完成下列大部分操作。本页仅在**手工排障**时参考。
> 上游 CLI 参数可能随版本变化：先跑对应 `--help`（或 `social_dl.py doctor`）核对，不要照抄本文。

## B 站（yutto）

```bash
yutto "<B站链接>" -d "<task_dir>"
```

当前已验证 yutto 2.3.0 用 `-d/--dir` 指定输出目录。升级版本后以 `yutto --help` 为准；参数协商失败时编排器会抛 `BackendError`，此时人工确认新参数并更新 `scripts/backends/bilibili.py` 的 `CAPS`。

大视频/时长未知：从一开始就后台执行（编排器已强制），保存日志、pid 与任务状态。前台超时（exit 124）不代表失败，进程仍在后台跑，用 `poll --task` 续看。若任务目录已有完整 `.m4s` 音视频流，先查文件大小和文件头，再用同一任务目录重跑 yutto 完成合并；恢复前记录已有文件，恢复后确认无重复或覆盖异常，不换后端、不删可恢复的流文件。

## 抖音（douyin-downloader）

```bash
# 编排器自动做：从 vendor config.yml 派生任务 config 并清空 link
# 手工排障才用：
cd $SOCIAL_DL_DATA_DIR/douyin-downloader
uv run douyin-dl --url "<抖音链接>" --path "<task_dir>" --config <task_dir>/task-config.yml
```

- CLI 会把 `--url` 追加进 config 的 `link` 列表（`cli/main.py:227-228`），直接复用含示例链接的 `config.yml` 会出现 `Found 2 URL(s)` 误下载示例账号。每次任务必须派生 `link: []` 的隔离 config。
- 图文作品（URL 含 `/note/`）：先跑固定后端，成功就收；`/aweme/v1/web/aweme/detail/` 返回空响应（anti-bot）且重试无效时，直接告知用户后端无法解析，不尝试 curl/浏览器等降级。`yt-dlp` 不支持抖音 note URL，不换后端。
- Cookie 值含冒号必须加引号（见 `config-setup.md`）。

## 小红书（XHS-Downloader）

```bash
cd $SOCIAL_DL_DATA_DIR/XHS-Downloader
uv run python -c 'from source.CLI.main import cli; cli()' \
  --url "<小红书链接>" --work_path "<task_dir>" --folder_name "media"

# 图文只下载指定图片
uv run python -c 'from source.CLI.main import cli; cli()' \
  --url "<链接>" --work_path "<task_dir>" --folder_name "media" --index "1 3 5"
```

登录态在 `<vendor>/Volume/settings.json` 的 `cookie` 字段，按上游 README 登录填写。

## 飞书云盘上传（lark-cli）

当前版本强制要求 `--file` 为当前工作目录下的相对路径（绝对路径报 `unsafe file path`），且中文长文件名不得用 `ls` 组装变量（换行会拼成超长文件名报 `file name too long`）。标准做法：

```bash
mkdir -p /tmp/dy-upload-<time>/ && cp "<task_dir>"/*/*.jpg /tmp/dy-upload-<time>/
cd /tmp/dy-upload-<time>/
lark-cli drive +upload --file "./<local_image>" --folder-token "<folder_token>" --as user --format json
# 批量用 for f in *_2.jpg *_3.jpg ...（不要用 f=$(ls ...)），逐项等待成功，不并发写同一目标目录
```

- 上传到根目录时省略 `--folder-token`，不要传空字符串。
- 每个文件单独上传，确认 JSON 结果成功后再继续。

## 百度网盘上传（bdpan）

- `bdpan upload` 接受本地绝对路径且自动建中间目录。
- `bdpan rm` 默认要交互确认，脚本里删东西必须带 `-f`。
- 后台上传时把 stdout/stderr 重定向到任务目录的独立日志并记录 pid（编排器已强制）。
- 上传进行中查询远端只用于观察，不得因文件暂时不可见而重复启动上传或直接判定失败。
- 不自行实现百度网盘 API；不输出 Token、Cookie 或密码。
