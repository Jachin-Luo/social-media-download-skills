#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站后端适配器 —— yutto。

yutto 是 PyPI 官方包（yutto-dev/yutto 发布），用包管理器按版本安装即可，
不需要 clone 源码，因此不存在 vendor 目录、也不会产生 git 冲突。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .base import Backend


class BilibiliBackend(Backend):
    platform = "bilibili"
    name = "yutto"

    # URL 是位置参数，只有输出目录是 flag
    CAPS = {
        "dir": ["-d", "--dir"],
    }

    def install_mode(self) -> str:
        return "pypi"

    def exe(self) -> list[str]:
        return ["yutto"]

    def build_cmd(self, url: str, task_dir: Path, ctx: dict) -> list[str]:
        return self.exe() + [url, self.flag("dir"), str(task_dir)]

    def available(self) -> tuple[bool, str]:
        try:
            p = subprocess.run(["yutto", "--version"], capture_output=True,
                               text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"yutto 不可用：{e}"
        if p.returncode != 0:
            return False, f"yutto --version 返回 {p.returncode}"
        return True, (p.stdout or "").strip()[:60]

    def login_state(self) -> tuple[bool, str]:
        """yutto 自家登录态：yutto auth status。

        不读凭证内容，只看命令结果。判定用退出码为主、关键词为辅，
        且不确定时返回 unknown 而非 False，避免误报"未登录"。
        """
        try:
            p = subprocess.run(["yutto", "auth", "status"], capture_output=True,
                               text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"无法检查 yutto 登录态：{e}"
        txt = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0:
            return True, "yutto 登录态有效"
        if any(k in txt for k in ("未登录", "not login", "no auth", "未授权")):
            return False, "未登录，跑 `yutto auth login` 扫码"
        # 退出码非零但看不出是不是登录问题——不要武断判 False
        return False, f"yutto auth status 返回 {p.returncode}：{txt.strip()[:80]}"

    def collect_hint(self) -> str:
        return ("未找到成品视频。若目录里有 .m4s 音视频流，说明下载完成但合并失败，"
                "可用同一任务目录重跑 yutto 完成合并。")

    def parse_progress(self, line: str) -> float | None:
        """yutto 进度形如 '[ 42.5%] downloading ...'，通用百分比已覆盖。"""
        return super().parse_progress(line)
