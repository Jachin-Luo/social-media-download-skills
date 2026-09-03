#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后端适配器基类。

解耦目标
--------
把上游 CLI 的一切差异锁在适配器内部：可执行名、参数名、配置文件格式、
输出目录布局、登录态文件位置、进度输出格式。编排器只依赖本文件定义的接口，
上游升级时只需改对应适配器；若上游发生破坏性变更而无法协商出合法命令，
抛出 BackendError 让人确认，绝不拿猜出来的参数去跑。

参数协商（避免"上游一更新代码就冲突"的关键）
--------------------------------------------
适配器不为参数名硬编码，而是声明"能力 -> 候选参数名"：

    CAPS = {"url": ["--url", "-u"], "path": ["--path", "-p"]}

运行时先跑 `<exe> --help` 拿到当前版本实际支持的参数集合，再从候选里挑
第一个命中的。上游把 `--url` 改成 `-u` → 自动命中 `-u`，编排器零改动。
两边都对不上 → 抛 BackendError，明确告诉人是哪个能力、试过哪些参数。

这比"锁死参数 + 祈祷上游不改"稳：改了能自动适配，真改没了能立刻发现。
"""
from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path


class BackendError(RuntimeError):
    """适配器无法与当前上游版本协商出合法命令。

    这是"上游破坏性变更"的显式信号。宁可停下来让人确认，也不能拿猜出来的
    参数去跑——后者会静默下载错误内容，比直接失败危险得多。
    """

    def __init__(self, backend: str, capability: str, tried: list[str], hint: str = ""):
        self.backend = backend
        self.capability = capability
        self.tried = tried
        self.hint = hint
        msg = (f"[{backend}] 无法为能力 {capability!r} 找到可用参数"
               f"（已尝试：{' / '.join(tried) or '无候选'}）。"
               f"上游可能已改名或移除该参数，请人工确认后更新适配器。")
        if hint:
            msg += f" 诊断：{hint}"
        super().__init__(msg)


# ---- 媒体文件识别 ----
# P0-6：补全 .m4s —— yutto 下载高清视频先落音视频流再合并，合并失败时
# 编排器必须能"看见"这些流，否则 SKILL.md 里写的恢复流程无从触发。
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif", ".bmp")
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".m4s", ".flv", ".mov", ".avi", ".ts")
# 未完成的中间产物，收集时必须排除（否则会把下载一半的文件当成品上传）
PARTIAL_SUFFIX = (".tmp", ".part", ".!ut", ".crdownload", ".download", ".ytdl")

# 通用进度模式：绝大多数下载器都会输出形如 " 42.5%" 的百分比
GENERIC_PROGRESS_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def is_partial(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(s) for s in PARTIAL_SUFFIX)


class Backend(ABC):
    """单个平台下载后端的适配器。"""

    platform: str = ""      # bilibili / douyin / xiaohongshu
    name: str = ""          # 上游项目名
    # 能力 -> 候选参数名（按优先级）。上游改名时往这里加候选即可，编排器不动。
    CAPS: dict[str, list[str]] = {}

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._help_flags: set[str] | None = None
        # 未经 --help 验证就采用的参数（探测失败时的降级），doctor 会告警
        self.unverified: list[str] = []

    # ============ 子类必须实现 ============

    @abstractmethod
    def exe(self) -> list[str]:
        """调用前缀，如 ['yutto'] 或 ['uv', 'run', 'douyin-dl']。"""

    @abstractmethod
    def build_cmd(self, url: str, task_dir: Path, ctx: dict) -> list[str]:
        """构造下载命令（数组形式，绝不拼 shell 字符串）。"""

    # ============ 子类可选覆盖 ============

    def vendor_dir(self) -> Path:
        """源码安装的 vendor 目录（PyPI 安装的后端无此目录）。"""
        return self.data_dir / self.name

    def install_mode(self) -> str:
        """'pypi'（包管理器安装）或 'vendor'（源码 clone）。"""
        return "vendor"

    def available(self) -> tuple[bool, str]:
        """后端是否已安装可用。默认检查 exe 首个 token 是否在 PATH。"""
        head = self.exe()[0]
        try:
            p = subprocess.run([head, "--version"], capture_output=True,
                               text=True, timeout=30)
            if p.returncode == 0:
                return True, (p.stdout or p.stderr).strip().splitlines()[0][:60]
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"{head} 不可用：{e}"
        return False, f"{head} 返回非零"

    def prepare(self, task_dir: Path) -> dict:
        """下载前准备（如派生隔离配置）。返回值并入 build_cmd 的 ctx。"""
        return {}

    def login_state(self) -> tuple[bool, str]:
        """登录态检查。返回 (是否就绪, 说明)。默认未知。"""
        return False, "该后端未实现登录态检查"

    def parse_progress(self, line: str) -> float | None:
        """从一行日志解析进度百分比（0-100），解析不出返回 None。"""
        m = GENERIC_PROGRESS_RE.search(line)
        if m:
            try:
                v = float(m.group(1))
                return v if 0.0 <= v <= 100.0 else None
            except ValueError:
                return None
        return None

    def collect_hint(self) -> str:
        """无媒体文件时的针对性提示（各后端失败原因不同）。"""
        return ""

    # ============ 参数协商 ============

    def help_flags(self) -> set[str]:
        """当前版本 --help 中出现的参数集合（结果缓存）。"""
        if self._help_flags is None:
            self._help_flags = self._probe_help()
        return self._help_flags

    def _probe_help(self) -> set[str]:
        try:
            p = subprocess.run(self.exe() + ["--help"], capture_output=True,
                               text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return set()
        # 只取行首的 -x / --xxx，避免把参数值里的词当参数
        return set(re.findall(r"(?m)^\s{0,8}(--?[A-Za-z][\w-]*)", p.stdout or ""))

    def flag(self, cap: str) -> str:
        """为能力挑一个当前版本实际支持的参数名。

        协商失败（--help 探测不到且无候选）时抛 BackendError，
        让调用方拿到明确信号而不是静默用错参数。
        """
        candidates = self.CAPS.get(cap, [])
        if not candidates:
            raise BackendError(self.name, cap, [], hint="适配器未声明任何候选参数")

        flags = self.help_flags()
        if flags:
            for c in candidates:
                if c in flags:
                    return c
            raise BackendError(
                self.name, cap, candidates,
                hint=f"--help 中未找到任何候选（上游可能已改名）")

        # --help 探测失败（uv run 首次拉依赖超时、或上游不支持 --help）：
        # 降级用第一个候选，但记进 unverified，doctor 会告警
        self.unverified.append(cap)
        return candidates[0]

    def negotiate_all(self) -> dict[str, str]:
        """把所有能力都协商一遍，返回 {能力: 参数名}。doctor 用。"""
        return {cap: self.flag(cap) for cap in self.CAPS}
