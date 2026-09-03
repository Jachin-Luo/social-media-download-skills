#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台后端注册表。

编排器通过这里拿适配器，不直接 import 具体后端模块——新增平台只需
写一个适配器并加进 BACKENDS，编排器零改动。
"""
from __future__ import annotations

from pathlib import Path

from .base import Backend, BackendError, IMAGE_EXT, VIDEO_EXT, PARTIAL_SUFFIX, is_partial
from .bilibili import BilibiliBackend
from .douyin import DouyinBackend
from .xiaohongshu import XiaohongshuBackend

BACKENDS: list[type[Backend]] = [BilibiliBackend, DouyinBackend, XiaohongshuBackend]

# 用户输入 -> 规范平台名。用户说"b站""小红书"也能识别
PLATFORM_ALIASES = {
    "bilibili": "bilibili", "bili": "bilibili", "b站": "bilibili",
    "douyin": "douyin", "dy": "douyin", "抖音": "douyin",
    "xiaohongshu": "xiaohongshu", "xhs": "xiaohongshu", "red": "xiaohongshu",
    "小红书": "xiaohongshu", "rednote": "xiaohongshu",
}

# 平台显示名（用于归档目录命名模板）
PLATFORM_LABEL = {
    "bilibili": "B站",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}

# 域名 -> 平台。短链域名也算，因为短链域名本身可识别平台
DOMAIN_MAP = (
    (("bilibili.com", "b23.tv"), "bilibili"),
    (("douyin.com", "iesdouyin.com"), "douyin"),
    (("xiaohongshu.com", "xhslink.com", "xhs.link"), "xiaohongshu"),
)


def normalize_platform(raw: str) -> str:
    """把用户输入的平台标识规范化，无法识别返回空串。"""
    key = (raw or "").strip().lower()
    return PLATFORM_ALIASES.get(key, "")


def platform_label(platform: str) -> str:
    return PLATFORM_LABEL.get(platform, platform)


def detect_platform(url: str) -> str:
    """从 URL 识别平台，无法识别返回空串（调用方据此报 invalid_input）。"""
    u = (url or "").lower()
    for domains, platform in DOMAIN_MAP:
        if any(d in u for d in domains):
            return platform
    return ""


def get_backend(platform: str, data_dir: Path) -> Backend:
    platform = normalize_platform(platform) or platform
    for cls in BACKENDS:
        if cls.platform == platform:
            return cls(data_dir)
    raise BackendError("registry", "platform", [],
                       hint=f"未知平台 {platform!r}，可用："
                            f"{', '.join(b.platform for b in BACKENDS)}")


def all_backends(data_dir: Path) -> list[Backend]:
    return [cls(data_dir) for cls in BACKENDS]


__all__ = [
    "Backend", "BackendError", "BACKENDS", "PLATFORM_ALIASES", "PLATFORM_LABEL",
    "IMAGE_EXT", "VIDEO_EXT", "PARTIAL_SUFFIX", "is_partial",
    "normalize_platform", "platform_label", "detect_platform",
    "get_backend", "all_backends",
]
