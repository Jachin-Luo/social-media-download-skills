#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音后端适配器 —— jiji262/douyin-downloader。

注意（易踩坑）：PyPI 上确实存在一个叫 `douyin-downloader` 的包，
但它是 HeLiangHIT/douyin_downloader，与本项目使用的 jiji262 项目无关。
**不要改成 pip/uv 安装那个同名包**，会装到错误的项目。见 references/backends.md。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .base import Backend, BackendError


class DouyinBackend(Backend):
    platform = "douyin"
    name = "douyin-downloader"

    CAPS = {
        "url": ["--url", "-u"],
        "path": ["--path", "-p"],
        "config": ["--config", "-c"],
    }

    # 形如 "正在下载 3/9" / "[3/9]" —— 图集类作品的进度常是计数而非百分比
    COUNT_RE = re.compile(r"\[?\s*(\d+)\s*/\s*(\d+)\s*\]?")

    def exe(self) -> list[str]:
        return ["uv", "run", "douyin-dl"]

    def build_cmd(self, url: str, task_dir: Path, ctx: dict) -> list[str]:
        cmd = self.exe() + [self.flag("url"), url, self.flag("path"), str(task_dir)]
        if ctx.get("task_config"):
            cmd += [self.flag("config"), ctx["task_config"]]
        return cmd

    def prepare(self, task_dir: Path) -> dict:
        """派生任务专属配置并强制清空 link。

        为什么必须隔离：上游 CLI 会把 --url **追加**进 config 的 link 列表
        （cli/main.py:227-228）。若直接复用仓库根的 config.yml，而里面残留
        示例链接，就会出现 "Found 2 URL(s)" 并误下载示例账号的作品。
        """
        return {"task_config": self._make_isolated_config(Path(task_dir))}

    def _make_isolated_config(self, task_dir: Path) -> str:
        vendor = self.vendor_dir()
        src = vendor / "config.yml"
        if not src.exists():
            src = vendor / "config.example.yml"
        if not src.exists():
            raise BackendError(self.name, "config", [],
                               hint=f"vendor 缺少 config.yml/config.example.yml：{vendor}")

        text = src.read_text(encoding="utf-8")
        # 多行列表形态：link:\n  - a\n  - b   （允许缩进）
        text = re.sub(r"(?m)^(\s*)link:[ \t]*\n(?:\s*-\s*.*\n?)+",
                      r"\1link: []\n", text)
        # 单行形态：link: xxx（幂等，对已清空的 link: [] 也成立）
        text = re.sub(r"(?m)^(\s*)link:[ \t]*\S.*$", r"\1link: []", text)
        # 完全没有 link 键则补一个
        if not re.search(r"(?m)^\s*link:", text):
            text = "link: []\n" + text

        dst = task_dir / "task-config.yml"
        dst.write_text(text, encoding="utf-8")
        try:
            os.chmod(dst, 0o600)   # 含 Cookie，权限收紧
        except OSError:
            pass
        return str(dst)

    def available(self) -> tuple[bool, str]:
        vendor = self.vendor_dir()
        if not vendor.exists():
            return False, f"vendor 缺失：{vendor}（先跑 init_wizard 启用 douyin 平台）"
        if not (vendor / "config.yml").exists():
            return False, "config.yml 缺失，需按上游 README 从 config.example.yml 创建"
        return True, f"vendor 就绪：{vendor}"

    def login_state(self) -> tuple[bool, str]:
        cfg = self.vendor_dir() / "config.yml"
        if not cfg.exists():
            return False, "无 config.yml，先从 config.example.yml 创建并填 Cookie"
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError as e:
            return False, f"无法读取 config.yml：{e}"
        has = "cookie" in text.lower()
        return has, ("config.yml 有 Cookie 段" if has
                     else "config.yml 无 Cookie，下载大概率被反爬拦（exit 0 但 0 文件）")

    def collect_hint(self) -> str:
        return ("未找到媒体文件。常见原因：① Cookie 过期（最高频，exit 0 但 0 文件）"
                "——重新登录；② 图文作品（URL 含 /note/）被 anti-bot 拦截，"
                "属已知不可解，直接告知用户，不要换后端或降级。")

    def parse_progress(self, line: str) -> float | None:
        p = super().parse_progress(line)
        if p is not None:
            return p
        # 计数型进度：只在明确是下载行为的行里匹配，避免误伤 URL 中的数字
        if any(k in line for k in ("下载", "Download", "download")):
            m = self.COUNT_RE.search(line)
            if m:
                cur, total = int(m.group(1)), int(m.group(2))
                if total > 0 and cur <= total:
                    return cur * 100.0 / total
        return None
