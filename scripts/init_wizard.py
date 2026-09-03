#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次初始化向导：询问要启用的平台，安装后端，检查登录，写 config.local.json。

用法
----
    python3 init_wizard.py                       # 交互式：逐项询问要启用哪些平台
    python3 init_wizard.py --platforms all --yes # 非交互：全平台
    python3 init_wizard.py --platforms douyin,xiaohongshu --yes

设计要点
--------
- **启用哪些平台由用户决定**（B-1 修复）：不再默认只装抖音、让用户跑完 init
  才在下小红书时撞见"vendor 缺失"。非交互模式未指定 --platforms 时明确报错。
- **yutto 走 PyPI 官方包**（解耦）：uv tool install 按锁版本安装，不 clone
  源码、不产生 git 冲突。注意 PyPI 上的 douyin-downloader 是另一个项目
  （HeLiangHIT），douyin/XHS 仍走源码 clone，见 references/backends.md。
- vendor 已存在时**校验版本漂移**（P1-13 修复）：HEAD 与锁定的 sha 不一致
  会明确警告，而不是打印一句"已存在"就跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from backends import PLATFORM_ALIASES, all_backends  # noqa: E402

DATA_DIR = Path(os.environ.get(
    "SOCIAL_DL_DATA_DIR",
    str(Path.home() / ".local/share/social-media-download-skills"),
))
CONFIG_PATH = DATA_DIR / "config.local.json"
LOCK_PATH = SCRIPTS_DIR / "requirements.lock"

KNOWN_PLATFORMS = ["bilibili", "douyin", "xiaohongshu"]

DEFAULT_CONFIG = {
    "video_dest": "baidu",
    "baidu_base": "social-media-download",   # 相对 /apps/bdpan
    "image_dest": "feishu",
    "feishu_parent_folder_token": "",        # 空 = 根目录下建任务文件夹
    "name_template": "{date}",               # 归档名模板：{platform} {title} {author} {date} {day}
}

REPOS = {
    "douyin": "https://github.com/jiji262/douyin-downloader.git",
    "xiaohongshu": "https://github.com/JoeanAmier/XHS-Downloader.git",
}
VENDOR_NAME = {"douyin": "douyin-downloader", "xiaohongshu": "XHS-Downloader"}


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
    except (OSError, subprocess.SubprocessError) as e:
        return 126, "%s: %s" % (type(e).__name__, e)


def load_lock() -> dict:
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- 依赖与登录检查

def check_bins():
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


def check_logins(enabled_platforms):
    """只读检查各项目自有登录文件/CLI 授权，不读不写凭证值。"""
    res = {}
    if "douyin" in enabled_platforms:
        cfg = DATA_DIR / "douyin-downloader" / "config.yml"
        if cfg.exists():
            try:
                has = "cookie" in cfg.read_text(encoding="utf-8").lower()
            except OSError:
                has = False
            res["douyin"] = (has, "config.yml 有 Cookie 段" if has
                             else "config.yml 无 Cookie，先按上游 README 登录")
        else:
            res["douyin"] = (False, "无 config.yml，先从 config.example.yml 创建并填 Cookie")
    if "bilibili" in enabled_platforms:
        if shutil.which("yutto"):
            rc, txt = run(["yutto", "auth", "status"])
            if rc == 0:
                res["yutto"] = (True, "yutto 登录态有效")
            else:
                res["yutto"] = (False, "未登录或状态未知，跑 `yutto auth login` 扫码")
        else:
            res["yutto"] = (False, "yutto 未安装")
    if "xiaohongshu" in enabled_platforms:
        s = DATA_DIR / "XHS-Downloader" / "Volume" / "settings.json"
        if s.exists():
            try:
                ok = bool(json.loads(s.read_text(encoding="utf-8")).get("cookie"))
            except (OSError, ValueError):
                ok = False
            res["xhs"] = (ok, "settings.json cookie 已填" if ok
                          else "settings.json cookie 为空，按上游 README 登录")
        else:
            res["xhs"] = (False, "未拉取或未登录（Volume/settings.json 缺失）")
    if shutil.which("bdpan"):
        rc, txt = run(["bdpan", "whoami"])
        res["bdpan"] = (rc == 0, txt.strip().splitlines()[0][:60] if txt.strip() else "")
    else:
        res["bdpan"] = (False, "bdpan missing")
    if shutil.which("lark-cli"):
        rc, _txt = run(["lark-cli", "auth", "status"])
        res["lark"] = (rc == 0, "auth status checked")
    else:
        res["lark"] = (False, "lark-cli missing")
    return res


# ---------------------------------------------------------------- 后端安装

def install_yutto(lock):
    """yutto 走 PyPI 官方包：uv tool install，版本由 requirements.lock 锁定。

    好处：不 clone 源码、无 git 冲突、版本可复现；升级 = 改 lock + 重跑本命令。
    """
    pin = lock.get("pins", {}).get("yutto", {})
    ver = pin.get("version", "")
    spec = "yutto==%s" % ver if ver else "yutto"
    log("[init] 安装 yutto（PyPI，%s）..." % (spec,))
    rc, txt = run(["uv", "tool", "install", "--force", spec], timeout=600)
    if rc != 0:
        log("[init] yutto 安装失败：%s" % txt[-400:])
        return False
    rc, txt = run(["yutto", "--version"])
    log("[init] yutto 就绪：%s" % (txt.strip()[:40] if rc == 0 else "验证失败"))
    return rc == 0


def ensure_repo(platform, pin_sha):
    """clone 源码到 DATA_DIR/<name> 并 checkout 到锁定位；已存在则校验漂移。"""
    name = VENDOR_NAME[platform]
    dest = DATA_DIR / name
    if dest.exists():
        rc, txt = run(["git", "-C", str(dest), "rev-parse", "HEAD"])
        cur = txt.strip()[:12] if rc == 0 else "unknown"
        if pin_sha and rc == 0 and not txt.strip().startswith(pin_sha):
            # P1-13：打印"已存在"就跳过会让人误以为版本没问题——必须点破漂移
            log("[init] 警告：%s 当前版本 %s 与锁定 %s 不一致（版本漂移）。"
                "升级请走 requirements.lock 的 changelog 流程。"
                % (name, cur, pin_sha[:12]))
            return False
        log("[init] %s 已存在（%s），与锁定版本一致，跳过拉取。" % (name, cur))
        return True
    log("[init] clone %s ..." % name)
    rc, txt = run(["git", "clone", "--quiet", REPOS[platform], str(dest)], timeout=600)
    if rc != 0:
        log("[init] clone 失败：%s" % txt[-500:])
        return False
    if pin_sha:
        rc, txt = run(["git", "-C", str(dest), "checkout", "--quiet", pin_sha], timeout=120)
        if rc != 0:
            log("[init] checkout %s 失败：%s（停在默认分支，请人工确认）"
                % (pin_sha[:12], txt[-300:]))
            return False
    if not (dest / "config.yml").exists():
        log("[init] %s 无 config.yml：按其 README 从 config.example.yml 创建并填 Cookie，"
            "否则下载会被反爬拦。" % name)
    else:
        try:
            (dest / "config.yml").chmod(0o600)
        except OSError:
            pass
    log("[init] %s 就绪 @ %s" % (name, (pin_sha or "HEAD")[:12]))
    return True


# ---------------------------------------------------------------- 平台选择

def ask_platforms() -> list:
    """交互式询问用户要启用哪些平台。"""
    labels = {"bilibili": "B 站（yutto，PyPI 安装）",
              "douyin": "抖音（douyin-downloader，源码 clone）",
              "xiaohongshu": "小红书（XHS-Downloader，源码 clone）"}
    log("要启用哪些平台？（回车 = 全部；多选用逗号分隔，如 1,3）")
    for i, p in enumerate(KNOWN_PLATFORMS, 1):
        log("  %d) %s" % (i, labels[p]))
    raw = input("选择 [1,2,3 / all，默认 all]: ").strip().lower()
    if not raw or raw in ("all", "全部", "a"):
        return list(KNOWN_PLATFORMS)
    picked = []
    for tok in re_split(raw):
        if tok in KNOWN_PLATFORMS:
            picked.append(tok)
        elif tok in PLATFORM_ALIASES and PLATFORM_ALIASES[tok] in KNOWN_PLATFORMS:
            picked.append(PLATFORM_ALIASES[tok])
        elif tok.isdigit() and 1 <= int(tok) <= len(KNOWN_PLATFORMS):
            picked.append(KNOWN_PLATFORMS[int(tok) - 1])
    return list(dict.fromkeys(picked)) or list(KNOWN_PLATFORMS)


def re_split(s):
    return [t for t in (x.strip() for x in s.replace("，", ",").split(",")) if t]


def resolve_platforms(raw: str, non_interactive: bool) -> list:
    """把 --platforms 参数解析为平台列表；未指定且非交互时明确报错（B-1）。"""
    if raw:
        if raw.lower() in ("all", "全部"):
            return list(KNOWN_PLATFORMS)
        picked = []
        for tok in re_split(raw):
            if tok in KNOWN_PLATFORMS:
                picked.append(tok)
            elif PLATFORM_ALIASES.get(tok) in KNOWN_PLATFORMS:
                picked.append(PLATFORM_ALIASES[tok])
            else:
                log("[init] 未识别的平台：%s（可用：%s 或 all）"
                    % (tok, ",".join(KNOWN_PLATFORMS)))
        return list(dict.fromkeys(picked))
    if non_interactive:
        log("[init] 非交互模式必须用 --platforms 显式指定（如 --platforms all "
            "或 --platforms douyin,xiaohongshu），不再默认只装抖音。")
        return []
    return ask_platforms()


# ---------------------------------------------------------------- 主流程

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="social-media-download-skills 首次初始化")
    ap.add_argument("--non-interactive", action="store_true")
    ap.add_argument("--yes", action="store_true", help="确认执行安装与写配置")
    ap.add_argument("--platforms", default="",
                    help="逗号分隔：bilibili,douyin,xiaohongshu；或 all。"
                         "非交互模式必填；交互模式留空则逐项询问")
    ap.add_argument("--video-dest", default="baidu", choices=["baidu", "feishu"])
    ap.add_argument("--baidu-base", default="social-media-download")
    ap.add_argument("--feishu-parent", default="")
    ap.add_argument("--name-template", default=DEFAULT_CONFIG["name_template"])
    args = ap.parse_args(argv)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass

    enabled = resolve_platforms(args.platforms, args.non_interactive)
    if not enabled:
        return 1
    log("[init] 启用平台：%s" % ", ".join(enabled))

    log("== 1/4 检查系统依赖 ==")
    for name, ok, detail in check_bins():
        log("  [%s] %s: %s" % ("OK" if ok else "MISS", name, detail))

    log("== 2/4 检查登录（只读） ==")
    for k, (ok, detail) in check_logins(enabled).items():
        log("  [%s] %s: %s" % ("OK" if ok else "--", k, detail))

    log("== 3/4 安装启用的后端 ==")
    lock = load_lock()
    pins = lock.get("pins", {})
    ok_all = True
    if "bilibili" in enabled:
        ok_all &= install_yutto(lock)
    if "douyin" in enabled:
        ok_all &= ensure_repo("douyin", pins.get("douyin-downloader", {}).get("sha", ""))
    if "xiaohongshu" in enabled:
        ok_all &= ensure_repo("xiaohongshu", pins.get("XHS-Downloader", {}).get("sha", ""))

    log("== 4/4 写 config.local.json ==")
    if args.non_interactive and not args.yes:
        log("  --non-interactive 未带 --yes：只检查不写配置。")
        return 0 if ok_all else 1
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            log("  已有配置，做合并保留。")
        except (OSError, ValueError):
            pass
    cfg["platforms"] = enabled
    cfg["video_dest"] = args.video_dest
    cfg["baidu_base"] = args.baidu_base
    cfg["feishu_parent_folder_token"] = args.feishu_parent
    cfg["name_template"] = args.name_template
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    log("  已写入 %s" % CONFIG_PATH)

    log("== 完成 ==")
    log("下一步：跑 `python3 scripts/social_dl.py doctor` 做契约自检，"
        "确认各后端可用且登录就绪。")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
