# 配置与登录准备

> 各家登录是它们自己的文件，向导只做存在性检查、不读不写凭证值。

## 数据目录

`$SOCIAL_DL_DATA_DIR`，默认 `~/.local/share/social-media-download-skills/`。权限 700。

```
~/.local/share/social-media-download-skills/
├── douyin-downloader/      # 源码 clone（启用 douyin 平台时）
│   └── config.yml          # Cookie 在这里
├── XHS-Downloader/         # 源码 clone（启用 xiaohongshu 平台时）
│   └── Volume/settings.json
└── config.local.json       # 本 skill 自己的配置（视频/图片目标、归档模板等）
```

## 登录状态一览

| 后端 | 登录文件/方式 | 向导检查方式 |
|---|---|---|
| douyin-downloader | `<vendor>/config.yml`（含 Cookie 段） | 检查文件存在且含 cookie 字段 |
| yutto | `~/.config/yutto/auth.toml` | `yutto auth status` |
| XHS-Downloader | `<vendor>/Volume/settings.json` 的 `cookie` 字段 | 只查该字段非空 |
| 百度盘 | `bdpan` CLI 授权 | `bdpan whoami` |
| 飞书 | `lark-cli` 授权（`--as user`） | `lark-cli auth status` |

## douyin config.yml 的 YAML 冒号陷阱

`cookies` 段部分 Cookie 值以 `n:` 或 `rd:` 开头（如 `passport_csrf_token`、`sid_guard`），YAML 会把冒号误判为嵌套映射键：

```
yaml.scanner.ScannerError: mapping values are not allowed here
  in "config.yml", line 140, column 25
```

修复：用引号包裹整个值：

```yaml
cookies:
  passport_csrf_token: "n: <你的值，含冒号必须加引号>"
  sid_guard: "rd: <你的值，含冒号必须加引号>"
```

## 凭证安全红线

- Cookie 属登录凭证：禁止在聊天中索取、接收、回显或写入命令参数。
- 通过 SCP/SFTP 传输配置到服务器，随后 `chmod 600 config.yml`；只验证下载结果。
- 百度网盘 Token、Cookie、飞书授权信息不得写入 Skill、命令行日志或最终回复（编排器已对落盘命令自动脱敏）。
- 服务器无图形界面时，不要假设会弹出可交互浏览器；本地完成登录后通过 SCP/SFTP 传输配置。
- 认证失败直接返回错误，不尝试绕过验证码。

## 启用平台说明

初始化时向导会**询问要启用哪些平台**（或用 `--platforms douyin,bilibili,xiaohongshu` / `all` 显式指定）。只启用的平台才会安装对应后端；未启用平台的下载请求会在后端检查阶段得到明确报错并提示补跑 init。
