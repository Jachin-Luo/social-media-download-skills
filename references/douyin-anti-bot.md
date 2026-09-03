# 抖音 Anti-Bot 与 Config 修复记录

调研时间：2026-09-01

## config.yml YAML 语法陷阱

`config.yml` 的 `cookies` 段中，部分 Cookie 值以 `n:` 或 `rd:` 开头（如 `passport_csrf_token` 和 `sid_guard`），YAML 解析器将其误判为嵌套映射键。

**错误表现**：
```
yaml.scanner.ScannerError: mapping values are not allowed here
  in "config.yml", line 140, column 25
```

**修复**：用引号包裹值：
```yaml
cookies:
  passport_csrf_token: "n: <你的值，含冒号必须加引号>"
  sid_guard: "rd: <你的值，含冒号必须加引号>"
```

## 抖音图文作品（note）API 受阻

**现象**：`/aweme/v1/web/aweme/detail/` 接口对图文类作品（note 类型，aweme_id 如 `7678644254981340915`）返回空 200 响应（content-length: 0），3 次重试均失败。

**其他尝试均失败**：
- `iesdouyin.com/share/note/` 页面 SSR 数据不含图片 URL（客户端加载）
- `iesdouyin.com/web/api/v2/aweme/iteminfo/` 同样返回空
- `yt-dlp`（v2026.08.19 最新版）不支持抖音 note URL，抛出 `UnsupportedError`
- curl 直接请求 API 返回空

**结论**：抖音图文作品的反爬策略比视频更严格。Cookie 有效但 API 层面拦截。当前固定后端（douyin-downloader）无法解析此类作品，应直接告知用户。

## yt-dlp 与抖音

即使升级到最新版 yt-dlp（2026.08.19），抖音 note URL 仍不被支持。`generic` extractor 无法解析 `douyin.com/note/` 路径。抖音视频（非 note）的兼容性未在本次测试。
