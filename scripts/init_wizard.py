#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次初始化向导：拉取第三方库、检查登录、写 config.local.json。

用法（agent 非交互优先）：
    python3 init_wizard.py --non-interactive --yes
    python3 init_wizard.py   # 交互式（有 tty 时逐项确认）

只写新数据目录（默认 ~/.local/share/social-media-download-skills）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get(
    "SOCIAL_DL_DATA_DIR",
    str(Path.home() / ".local/share/social-media-download-skills"),
))
CONFIG_PATH = DATA_DIR / "config.local.json"
LOCK_PATH = Path(__file__).resolve().parent / "requirements.lock"

DEFAULT_CONFIG = {
    "platforms": ["douyin"],
    "douyin_repo": "https://github.com/jiji262/douyin-downloader.git",
    "xhs_repo": "https://github.com/JoeanAmier/XHS-Downloader.git",
    # yutto / bdpan / lark-cli 是系统级 CLI，只锁版本不 clone
    "video_dest": "baidu",
    "baidu_base": "social-media-download",  # 相对 /apps/bdpan
    "image_dest": "feishu",
    "feishu_parent_folder_token": "",  # 空 = 根目录下建任务文件夹
    "task_prefix": "social-media-",
}


def log(*a):
    print(*a, flush=True)


def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "command not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "timeout: %s" % " ".join(cmd)


def check_bins():
    """返回 [(name, ok, detail)]。"""
    out = []
    for name, probe in [
        ("uv", ["uv", "--version"]),
        ("ffmpeg", ["ffmpeg", "-version"]),
        ("ffprobe", ["ffprobe", "-version"]),
        ("yutto", ["yutto", "--version"]),
        ("bdpan", ["bdpan", "version"]),
        ("lark-cli", ["lark-cli", "--version"]),
        ("git", ["git", "--version"]),
    ]:
        if shutil.which(name) is None:
            out.append((name, False, "not in PATH"))
            continue
        rc, txt = run(probe)
        first = (txt.strip().splitlines() or [""])[0][:80]
        out.append((name, rc == 0, first))
    return out


def check_logins():
    """各项目自有登录文件，只读检查存在性/有效性，不读不写凭证值。

    - douyin-downloader: <vendor>/config.yml（含 Cookie 段）
    - yutto: ~/.config/yutto/auth.toml（`yutto auth status` 判定）
    - XHS-Downloader: <vendor>/Volume/settings.json（cookie 字段非空）
    - bdpan / lark-cli: 网盘与飞书授权
    """
    res = {}
    dy_cfg = DATA_DIR / "douyin-downloader" / "config.yml"
    if dy_cfg.exists():
        try:
            has_cookie = "cookie" in dy_cfg.read_text(encoding="utf-8").lower()
        except OSError:
            has_cookie = False
        res["douyin"] = (has_cookie, "config.yml 有 Cookie 段" if has_cookie else "config.yml 无 Cookie，先按 README 登录")
    else:
        res["douyin"] = (False, "无 config.yml，先从 config.example.yml 创建并登录")
    if shutil.which("yutto"):
        rc, txt = run(["yutto", "auth", "status"])
        res["yutto"] = (rc == 0 and "有效" in txt, (txt.strip().splitlines() or [""])[0][:80])
        if not res["yutto"][0]:
            res["yutto"] = (False, "未登录，跑 `yutto auth login` 扫码（~/.config/yutto/auth.toml）")
    else:
        res["yutto"] = (False, "yutto missing")
    xhs_settings = DATA_DIR / "XHS-Downloader" / "Volume" / "settings.json"
    if xhs_settings.exists():
        try:
            logged = bool(json.loads(xhs_settings.read_text(encoding="utf-8")).get("cookie"))
        except (OSError, ValueError):
            logged = False
        res["xhs"] = (logged, "settings.json cookie 已填" if logged else "settings.json cookie 为空，按 README 登录")
    else:
        res["xhs"] = (False, "未拉取或未登录（Volume/settings.json 缺失）")
    if shutil.which("bdpan"):
        rc, txt = run(["bdpan", "whoami"])
        res["bdpan"] = (rc == 0 and "已登录" in txt, txt.strip().splitlines()[0][:80] if txt.strip() else "")
    else:
        res["bdpan"] = (False, "bdpan missing")
    if shutil.which("lark-cli"):
        rc, txt = run(["lark-cli", "auth", "status"])
        ok = rc == 0 and ("ready" in txt or "valid" in txt)
        res["lark"] = (ok, "auth status checked")
    else:
        res["lark"] = (False, "lark-cli missing")
    return res


def ensure_repo(name, url, pin_sha):
    """clone 到 DATA_DIR/<name>（不存在才拉），并 checkout 到锁定位。已存在则只校验。"""
    dest = DATA_DIR / name
    if dest.exists():
        rc, txt = run(["git", "-C", str(dest), "rev-parse", "HEAD"])
        cur = txt.strip()[:12] if rc == 0 else "unknown"
        log(f"[init] {name} 已存在（{cur}），跳过拉取。如需更新看 requirements.lock 的 changelog 流程。")
        return True
    log(f"[init] clone {name} <- {url} ...")
    rc, txt = run(["git", "clone", "--quiet", url, str(dest)], timeout=600)
    if rc != 0:
        log(f"[init] clone 失败：{txt[-500:]}")
        return False
    if pin_sha:
        rc, txt = run(["git", "-C", str(dest), "checkout", "--quiet", pin_sha], timeout=120)
        if rc != 0:
            log(f"[init] checkout {pin_sha[:12]} 失败：{txt[-300:]}（停在默认分支，请人工确认）")
            return False
    if not (dest / "config.yml").exists():
        log(f"[init] {name} 无 config.yml：按其 README 从 config.example.yml 创建并填 Cookie，否则下载会被反爬拦。")
    else:
        try:
            (dest / "config.yml").chmod(0o600)
        except OSError:
            pass
    log(f"[init] {name} 就绪 @ {pin_sha[:12] if pin_sha else 'HEAD'}")
    return True


def load_lock():
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="social-media-download-skills 首次初始化")
    ap.add_argument("--non-interactive", action="store_true")
    ap.add_argument("--yes", action="store_true", help="确认执行 clone 与写配置")
    ap.add_argument("--platforms", default="douyin", help="逗号分隔：douyin,bilibili,xiaohongshu")
    ap.add_argument("--video-dest", default="baidu", choices=["baidu", "feishu"])
    ap.add_argument("--baidu-base", default="social-media-download")
    ap.add_argument("--feishu-parent", default="")
    args = ap.parse_args(argv)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass

    log("== 1/4 检查依赖 ==")
    for name, ok, detail in check_bins():
        log(f"  [{'OK' if ok else 'MISS'}] {name}: {detail}")

    log("== 2/4 检查登录（只读） ==")
    for k, (ok, detail) in check_logins().items():
        log(f"  [{'OK' if ok else '--'}] {k}: {detail}")

    log("== 3/4 拉取第三方库 ==")
    lock = load_lock()
    pins = lock.get("pins", {})
    cfg_platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    ok_all = True
    if "douyin" in cfg_platforms:
        if not (args.non_interactive and not args.yes):
            ok_all &= ensure_repo("douyin-downloader",
                                  DEFAULT_CONFIG["douyin_repo"],
                                  pins.get("douyin-downloader", {}).get("sha", ""))
        elif args.yes:
            ok_all &= ensure_repo("douyin-downloader",
                                  DEFAULT_CONFIG["douyin_repo"],
                                  pins.get("douyin-downloader", {}).get("sha", ""))
    if "xiaohongshu" in cfg_platforms or "xhs" in cfg_platforms:
        if args.yes or not args.non_interactive:
            ok_all &= ensure_repo("XHS-Downloader",
                                  DEFAULT_CONFIG["xhs_repo"],
                                  pins.get("XHS-Downloader", {}).get("sha", ""))
    # yutto / bdpan / lark-cli 不 clone，只锁版本（见 requirements.lock）

    log("== 4/4 写 config.local.json ==")
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        old = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.update(old)
        log("  已有配置，做合并保留。")
    cfg["platforms"] = cfg_platforms
    cfg["video_dest"] = args.video_dest
    cfg["baidu_base"] = args.baidu_base
    cfg["feishu_parent_folder_token"] = args.feishu_parent
    if args.non_interactive and not args.yes:
        log("  --non-interactive 未带 --yes：只检查不写配置。")
        return 0 if ok_all else 1
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    log(f"  已写入 {CONFIG_PATH}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
