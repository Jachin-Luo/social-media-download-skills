#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书后端适配器 —— JoeanAmier/XHS-Downloader。

GPL-3.0：只通过独立进程调用其 CLI，不复制源码进本仓库。
该上游不在 PyPI，只能源码 clone（见 references/backends.md）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Backend


class XiaohongshuBackend(Backend):
    platform = "xiaohongshu"
    name = "XHS-Downloader"

    CAPS = {
        "url": ["--url"],
        "work_path": ["--work_path"],
        "folder_name": ["--folder_name"],
    }

    COUNT_RE = re.compile(r"\[?\s*(\d+)\s*/\s*(\d+)\s*\]?")

    def exe(self) -> list[str]:
        # 上游以模块方式提供 CLI 入口，没有 console_script
        return ["uv", "run", "python", "-c",
                "from source.CLI.main import cli; cli()"]

    def build_cmd(self, url: str, task_dir: Path, ctx: dict) -> list[str]:
        return self.exe() + [
            self.flag("url"), url,
            self.flag("work_path"), str(task_dir),
            self.flag("folder_name"), "media",
        ]

    def available(self) -> tuple[bool, str]:
        vendor = self.vendor_dir()
        if not vendor.exists():
            return False, f"vendor 缺失：{vendor}（先跑 init_wizard 启用 xiaohongshu 平台）"
        if not (vendor / "source" / "CLI" / "main.py").exists():
            return False, f"vendor 结构异常，缺少 source/CLI/main.py：{vendor}"
        return True, f"vendor 就绪：{vendor}"

    def login_state(self) -> tuple[bool, str]:
        # 上游登录态在自家 Volume/settings.json 的 cookie 字段，只看非空不读值
        s = self.vendor_dir() / "Volume" / "settings.json"
        if not s.exists():
            return False, "Volume/settings.json 缺失（未拉取或未登录）"
        try:
            data = json.loads(s.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return False, f"settings.json 解析失败：{e}"
        ok = bool(data.get("cookie"))
        return ok, ("settings.json cookie 已填" if ok
                    else "settings.json cookie 为空，按上游 README 登录后重试")

    def collect_hint(self) -> str:
        return ("未找到媒体文件。常见原因：① Cookie 过期（最高频）——重新登录；"
                "② 作品需登录可见或已删除。上游为 GPL-3.0，只调 CLI，不换后端。")

    def parse_progress(self, line: str) -> float | None:
        p = super().parse_progress(line)
        if p is not None:
            return p
        if any(k in line for k in ("下载", "Download", "download")):
            m = self.COUNT_RE.search(line)
            if m:
                cur, total = int(m.group(1)), int(m.group(2))
                if total > 0 and cur <= total:
                    return cur * 100.0 / total
        return None
