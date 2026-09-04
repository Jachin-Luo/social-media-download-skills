#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""social_dl.py — 社媒下载统一编排入口。

架构（与上游解耦后）
--------------------
- 本文件**不认识**任何上游 CLI：可执行名、参数名、配置格式、登录态文件
  全部封装在 `backends/` 的适配器里。新增平台 = 加一个适配器，本文件不动。
- 上游参数由适配器跑 `--help` 协商得出（`Backend.flag()`）。上游把 `--url`
  改成 `-u` 会自动命中；两边都对不上则抛 `BackendError` 明确报错，而不是拿
  猜出来的参数去跑（后者会静默下载错误内容）。
- 升级上游后跑 `doctor` 做契约自检，能立刻发现破坏性变更。

用法
----
    python3 social_dl.py doctor
    python3 social_dl.py run       --url "<链接>" --cleanup
    python3 social_dl.py download  --url "<链接>"
    python3 social_dl.py poll      --task <task_dir>
    python3 social_dl.py upload-feishu --task <task_dir> --name "归档名"
    python3 social_dl.py upload-baidu  --task <task_dir> --name "归档名"

设计红线
--------
- 数组传参调子进程，绝不拼 shell（中文长文件名/空格/! 安全）。
- 日志走 stderr，结构化结果走 stdout（可被程序直接 json.loads）。
- 同目录上传串行、跨目标按类型分流；远端对账通过才算成功。
- 无论成功失败都在 finally 里清理本次任务目录，并如实上报残留。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from backends import (  # noqa: E402
    IMAGE_EXT, VIDEO_EXT, is_partial,
    all_backends, detect_platform, get_backend, platform_label,
)
from backends.base import BackendError  # noqa: E402

SKILL_DIR = SCRIPTS_DIR.parent
DATA_DIR = Path(os.environ.get(
    "SOCIAL_DL_DATA_DIR",
    str(Path.home() / ".local/share/social-media-download-skills"),
))
CONFIG_PATH = DATA_DIR / "config.local.json"

# 各平台默认超时（秒）。B 站大视频给足时间；0 表示采用这里的值
PLATFORM_TIMEOUT = {"bilibili": 1800, "douyin": 600, "xiaohongshu": 600}
POLL_INTERVAL = 5

# 归档名模板占位符
DEFAULT_NAME_TEMPLATE = "{date}"
ARCHIVE_PLACEHOLDERS = ("platform", "title", "author", "date", "day")
INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def log(*a):
    """日志一律走 stderr，保证 stdout 只有一份可解析的 JSON。"""
    print(*a, file=sys.stderr, flush=True)


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- 工具

def clean_name(s: str, maxlen: int = 60) -> str:
    """清洗成安全的目录/文件名片段。"""
    s = INVALID_CHARS.sub("_", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = re.sub(r"_{2,}", "_", s)
    return s[:maxlen].strip("_ ")


def build_archive_name(template: str, platform: str, meta: dict, started: str) -> str:
    """按模板生成归档名。

    默认 `{date}` 即纯时间戳（向后兼容）。用户可用
    `--name-template "{platform}_{title}_{day}"` 换成可读形式。
    """
    meta = meta or {}
    name = template or DEFAULT_NAME_TEMPLATE
    # P2：模板里的未知占位符直接报错——否则 "{foo}" 会原样残留在目录名里。
    # 只校验模板本身，不校验替换结果（标题里自带 "{x}" 文字不受影响）。
    unknown = set(re.findall(r"\{([A-Za-z_]+)\}", name)) - set(ARCHIVE_PLACEHOLDERS)
    if unknown:
        raise ValueError("归档模板含未知占位符 %s（可用：%s）"
                         % (sorted(unknown), ", ".join("{%s}" % p for p in ARCHIVE_PLACEHOLDERS)))
    values = {
        "platform": platform_label(platform),
        "title": clean_name(meta.get("title") or "", 40) or "无标题",
        "author": clean_name(meta.get("author") or meta.get("uploader") or "", 20),
        "date": started,
        "day": started[:10],
    }
    for k, v in values.items():
        name = name.replace("{%s}" % k, v)
    return clean_name(name, 80) or started


def run_capture(cmd: list, timeout: int = 120, cwd=None):
    """跑短命令，异常不外抛，统一返回 (rc, stdout, stderr)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=str(cwd) if cwd else None)
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "command not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout: %s" % " ".join(cmd)
    except (OSError, subprocess.SubprocessError) as e:
        return 126, "", "%s: %s" % (type(e).__name__, e)


def log_tail(logfile, n: int = 15) -> list:
    try:
        return Path(logfile).read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
    except OSError:
        return []


def log_exit(logfile):
    for line in reversed(log_tail(logfile, 5)):
        m = re.search(r"\[exit (-?\d+)\]", line)
        if m:
            return int(m.group(1))
    return None


def read_pid(logfile) -> int:
    try:
        return int(Path(str(logfile) + ".pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return -1


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def read_progress(backend, logfile, tail_lines: int = 40):
    """从日志尾部解析最新进度（0-100），解析不出返回 None。"""
    for line in reversed(log_tail(logfile, tail_lines)):
        p = backend.parse_progress(line)
        if p is not None:
            return round(p, 1)
    return None


# ---------------------------------------------------------------- 后台执行

SECRET_RE = re.compile(r"(--(?:folder-token|api-key|cookie|token|password|secret|key|auth)\S*)([= ])(\S+)", re.I)


def mask_secrets(s: str) -> str:
    """命令落盘前脱敏：SKILL.md 明确禁止凭证进入命令行日志。"""
    return SECRET_RE.sub(lambda m: "%s%s***" % (m.group(1), m.group(2)), s)


def exec_bg(cmd, cwd, logfile, timeout=600, interval=POLL_INTERVAL):
    """耗时外部命令统一入口：后台脱离启动 + pid + 独立日志 + 轮询。

    超时返回 (124, 日志尾)，进程留后台继续跑，凭 task 目录用 `poll` 续看，
    绝不重复启动。
    """
    Path(logfile).parent.mkdir(parents=True, exist_ok=True)
    lf = open(logfile, "a", encoding="utf-8")
    lf.write("$ %s\n" % mask_secrets(" ".join(cmd)))
    lf.flush()
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True)
    except (OSError, ValueError) as e:
        lf.write("\n[spawn failed] %s\n" % e)
        lf.close()
        return 127, ["[spawn failed] %s" % e]
    Path(str(logfile) + ".pid").write_text(str(p.pid), encoding="utf-8")
    log("[bg] pid=%s log=%s" % (p.pid, logfile))
    waited = 0
    while waited < timeout:
        code = p.poll()
        if code is not None:
            lf.write("\n[exit %s]\n" % code)
            lf.close()
            return code, log_tail(logfile, 200)
        time.sleep(interval)
        waited += interval
    lf.close()
    return 124, log_tail(logfile, 50)


# ---------------------------------------------------------------- 媒体收集与校验

def collect_media(task_dir: Path):
    """返回 ([图片], [视频])，排除未完成的中间产物。

    P0-6：扩展名包含 .m4s —— yutto 下载高清视频先落音视频流再合并，
    合并失败时编排器必须能"看见"这些流，否则 SKILL.md 写的恢复流程无从触发。
    """
    imgs, vids = [], []
    for p in sorted(Path(task_dir).rglob("*")):
        if not p.is_file() or is_partial(p):
            continue
        suf = p.suffix.lower()
        if suf in IMAGE_EXT:
            imgs.append(str(p))
        elif suf in VIDEO_EXT:
            vids.append(str(p))
    return imgs, vids


def _image_complete(p: Path) -> bool:
    """图片完整性：文件头 + 文件尾双校验。

    P1-9：只验文件头会放过截断的 JPEG（它的头是完整的），必须验尾标记。
    """
    try:
        if p.stat().st_size < 12:
            return False
        with open(p, "rb") as fh:
            head = fh.read(12)
        if head[:3] == b"\xff\xd8\xff":                       # JPEG
            with open(p, "rb") as fh:
                fh.seek(-2, os.SEEK_END)
                return fh.read(2) == b"\xff\xd9"
        if head[:8] == b"\x89PNG\r\n\x1a\n":                  # PNG
            with open(p, "rb") as fh:
                fh.seek(-8, os.SEEK_END)
                return fh.read(8) == b"IEND\xaeB`\x82"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":     # WEBP
            return True
        if head[:6] in (b"GIF87a", b"GIF89a"):                # GIF
            return True
        return True                                            # 其余仅做基础校验
    except OSError:
        return False


def verify_local(files) -> list:
    bad = []
    want_video = any(Path(f).suffix.lower() in VIDEO_EXT for f in files)
    probe = shutil.which("ffprobe")
    if want_video and probe is None:
        # P1：环境缺 ffprobe 时原来会把每个视频都判坏（rc=127），报错却是
        # "文件不完整"——用户会去查文件，其实是缺二进制。此时只做存在性
        # 检查并明确告警，不判坏。
        log("警告：未找到 ffprobe，跳过视频完整性探测（仅检查存在性与大小）")
    for f in files:
        p = Path(f)
        if not p.exists() or p.stat().st_size == 0:
            bad.append(str(p))
            continue
        suf = p.suffix.lower()
        if suf in VIDEO_EXT:
            if probe is None:
                continue
            rc, _, _ = run_capture([probe, "-v", "quiet", "-show_format", str(p)], 60)
            if rc != 0:
                bad.append(str(p))
        elif suf in IMAGE_EXT:
            if not _image_complete(p):
                bad.append(str(p))
    return bad


# ---------------------------------------------------------------- 溯源 manifest

def _safe_size(f) -> int:
    try:
        return Path(f).stat().st_size
    except OSError:
        return 0


def build_manifest(url, platform, backend_name, started, imgs, vids, meta) -> dict:
    """溯源信息：记录内容从哪来。随归档一起进云盘，便于日后回查出处。"""
    meta = meta or {}
    files = [{"name": Path(f).name, "type": "image", "size": _safe_size(f)} for f in imgs]
    files += [{"name": Path(f).name, "type": "video", "size": _safe_size(f)} for f in vids]
    return {
        "schema": "social-media-download-skills/manifest@1",
        "source_url": url,
        "platform": platform,
        "platform_label": platform_label(platform),
        "backend": backend_name,
        "downloaded_at": started,
        "title": meta.get("title", ""),
        "author": meta.get("author") or meta.get("uploader") or "",
        "counts": {"images": len(imgs), "videos": len(vids)},
        "files": files,
        "note": "本文件由 social-media-download-skills 生成，用于记录内容来源。",
    }


def write_manifest(task_dir: Path, data: dict) -> str:
    p = Path(task_dir) / "manifest.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def read_upstream_manifest(task_dir: Path) -> dict:
    """上游下载器产出的 download_manifest.jsonl：取首个作品的元数据。"""
    mp = Path(task_dir) / "download_manifest.jsonl"
    if not mp.exists():
        return {}
    try:
        for line in mp.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                return json.loads(line)
    except (OSError, ValueError):
        pass
    return {}


# ---------------------------------------------------------------- 飞书

def run_json(cmd, cwd=None, timeout: int = 120):
    rc, out, err = run_capture(cmd, timeout=timeout, cwd=cwd)
    txt = out.strip()
    try:
        return rc, json.loads(txt[txt.index("{"):])
    except (ValueError, IndexError):
        return rc, {"ok": False, "raw": txt[-500:], "stderr": err[-300:]}


def feishu_mkdir(name: str, parent: str = ""):
    cmd = ["lark-cli", "drive", "+create-folder", "--name", name,
           "--as", "user", "--format", "json"]
    if parent:
        cmd += ["--folder-token", parent]
    rc, data = run_json(cmd, cwd=str(DATA_DIR))
    if rc == 0 and data.get("ok") and (data.get("data") or {}).get("folder_token"):
        return data["data"]["folder_token"], data
    return "", data


def feishu_upload_serial(files, folder_token: str):
    """中转到 staging 后，cwd=staging + ./相对路径 串行上传。

    P0-1 修复：不同子目录的同名文件（社媒下载极常见，如多个 1.jpg）直接复制
    到同一 staging 会互相覆盖，导致上传错误内容、而数量仍然"对得上"——
    这是最危险的一类 bug：不报错，但东西是错的。这里做同名去重。
    """
    staging = Path(tempfile.mkdtemp(prefix="feishu-stage-", dir="/tmp"))
    results, used, mapping = [], {}, []
    try:
        for src in files:
            name = Path(src).name
            if name in used:                      # 同名 -> 加序号后缀
                used[name] += 1
                name = "%s__%d%s" % (Path(name).stem, used[name], Path(name).suffix)
            else:
                used[name] = 0
            try:
                shutil.copy(src, staging / name)
            except OSError as e:
                results.append({"file": Path(src).name, "ok": False, "file_token": "",
                                "raw": "staging copy failed: %s" % e})
                return results
            mapping.append((src, name))
        for src, name in mapping:
            cmd = ["lark-cli", "drive", "+upload", "--file", "./" + name,
                   "--folder-token", folder_token, "--as", "user", "--format", "json"]
            rc, data = run_json(cmd, cwd=str(staging))
            ok = rc == 0 and data.get("ok")
            results.append({
                "file": Path(src).name, "ok": ok,
                "file_token": ((data.get("data") or {}).get("file_token", "") if ok else ""),
                "raw": "" if ok else json.dumps(data, ensure_ascii=False)[:300],
            })
            if not ok:
                break                              # 同目录串行，失败即停
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return results


def feishu_verify(folder_token: str):
    """返回 (总数, parent_token 匹配数, 不匹配的文件名)。

    P1-4：SKILL.md 要求逐个比对 parent_token，而不是只数个数。
    P2：单页取 200（上限）。刻意不用 --page-all——它输出每页一个 JSON
    对象 + 进度行，run_json 解析必炸；超大图集命中 200 上限时明确告警。
    """
    cmd = ["lark-cli", "drive", "files", "list",
           "--params", json.dumps({"folder_token": folder_token, "page_size": 200}),
           "--format", "json"]
    rc, data = run_json(cmd, cwd=str(DATA_DIR))
    if rc != 0 or not data.get("ok"):
        return -1, -1, []
    files = (data.get("data") or {}).get("files", []) or []
    if len(files) >= 200:
        log("警告：飞书目录文件数已达单页上限 200，对账可能截断，"
            "请拆小图集或手动确认远端")
    bad = [f.get("name", "?") for f in files if f.get("parent_token") != folder_token]
    return len(files), len(files) - len(bad), bad


# ---------------------------------------------------------------- 百度盘

def baidu_mkdir(remote_dir: str) -> bool:
    rc, out, err = run_capture(["bdpan", "mkdir", remote_dir], 60)
    return rc == 0 or "已存在" in (out + err)


def baidu_upload(local_file: str, remote_file: str, task_dir: str = "") -> tuple:
    logdir = Path(task_dir) / "logs" if task_dir else Path(tempfile.mkdtemp(prefix="bd-", dir="/tmp"))
    logdir.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"\W+", "_", Path(local_file).name)[:40]
    rc, tail = exec_bg(["bdpan", "upload", local_file, remote_file],
                       cwd=task_dir or "/tmp",
                       logfile=str(logdir / ("upload-%s.log" % tag)), timeout=1800)
    # P1：中文关键词只做诊断备注，不做门控（措辞可能随 bdpan 版本变化，
    # 且"成功"二字可能被进度输出挤出尾部）。成功与否以退出码为准，
    # 最终以 baidu_final_ok 里的远端对账为准。
    joined = "\n".join(tail)
    kw = ("上传成功" in joined) or ("成功" in "\n".join(tail[-3:]))
    note = "" if kw else "\n[注：日志尾未见成功关键词，以退出码+远端对账为准]"
    return (rc == 0), rc, (joined[-500:] + note)


def baidu_verify(remote_dir: str) -> int:
    """--json 结构化对账：数非目录项。不可用返回 -1（表示"未验证"而非 0）。"""
    rc, out, _ = run_capture(
        ["bdpan", "ls", "/apps/bdpan/" + remote_dir.lstrip("/"), "--json"], 60)
    if rc != 0:
        return -1
    try:
        items = json.loads(out.strip())
        return sum(1 for it in items if not it.get("isdir", False))
    except (ValueError, TypeError, AttributeError):
        return -1


def unique_remote_dir(base: str, name: str) -> str:
    """P1-7：同秒并发会撞同名远端目录，导致文件串目录且对账误判成功。

    已存在且非空时追加 -2/-3...
    """
    rdir = "%s/%s" % (base, name)
    i = 2
    while baidu_verify(rdir) > 0 and i < 20:
        rdir = "%s/%s-%d" % (base, name, i)
        i += 1
    return rdir


# ---------------------------------------------------------------- 上传编排

def upload_to_feishu(files, name, cfg, task_dir, result) -> bool:
    token, raw = feishu_mkdir(name, cfg.get("feishu_parent_folder_token", ""))
    if not token:
        result.setdefault("errors", []).append(
            {"stage": "mkdir-feishu",
             "detail": json.dumps(raw, ensure_ascii=False)[:300]})
        return False
    res = feishu_upload_serial(files, token)
    total, matched, bad = feishu_verify(token)
    result["feishu"] = {"folder_token": token,
                        "uploaded": sum(1 for r in res if r["ok"]),
                        "expected": len(files),
                        "remote_count": total,
                        "parent_matched": matched,
                        "mismatched": bad}
    ok = all(r["ok"] for r in res) and matched >= len(files)
    if not ok:
        result.setdefault("errors", []).append(
            {"stage": "upload-feishu",
             "detail": "上传 %d/%d，远端匹配 %s/%d" %
                       (sum(1 for r in res if r["ok"]), len(files), matched, len(files)),
             "failed": [r for r in res if not r["ok"]][:5]})
    return ok


def baidu_final_ok(ok_all: bool, remote_count: int, expected: int) -> bool:
    """百度上传最终判定：远端对账为准，回执为辅。

    - remote_count >= 0（对账可用）：远端文件数 >= 预期即成功。
      回执里的中文关键词只是辅助信号（措辞可能随版本变化），不做门控。
    - remote_count == -1（对账不可用，如 bdpan 不支持 --json）：
      回退到逐文件回执判定。
    """
    if remote_count >= 0:
        return remote_count >= expected
    return ok_all


def upload_to_baidu(files, name, cfg, task_dir, result) -> bool:
    base = cfg.get("baidu_base", "social-media-download")
    rdir = unique_remote_dir(base, name)
    baidu_mkdir(rdir)
    ups, ok_all = [], True
    for f in files:
        ok, rc, tail = baidu_upload(f, "%s/%s" % (rdir, Path(f).name), task_dir)
        ups.append({"file": Path(f).name, "ok": ok, "exit": rc,
                    "raw": "" if ok else tail[-200:]})
        if not ok:
            ok_all = False
            log("[baidu] 上传失败 %s: %s" % (Path(f).name, tail[-200:]))
            break
    n = baidu_verify(rdir)
    result["baidu"] = {"remote_dir": rdir,
                       "uploaded": sum(1 for x in ups if x["ok"]),
                       "expected": len(files),
                       "remote_count": n,
                       "verified": n >= 0}
    ok = baidu_final_ok(ok_all, n, len(files))
    if not ok:
        result.setdefault("errors", []).append(
            {"stage": "upload-baidu",
             "detail": "远端未确认成功：回执 %d/%d，远端 %s/%d（目录 %s）" %
                       (sum(1 for x in ups if x["ok"]), len(files), n, len(files), rdir),
             "failed": [x for x in ups if not x["ok"]][:5]})
    # remote_count == -1 表示对账不可用（如 bdpan 不支持 --json），
    # 此时只以上传回执为准，不因此判失败
    return ok


VALID_DESTS = ("baidu", "feishu")


def resolve_dest(value, default):
    """归一化上传目的地；非法值抛 ValueError，绝不静默回退默认。"""
    v = str(value or default or "").strip().lower()
    if v not in VALID_DESTS:
        raise ValueError("非法上传目的地 %r（可选：baidu / feishu）" % value)
    return v


def plan_uploads(dest: str, imgs, vids, cfg):
    """媒体按类型分流；auto 模式下目的地由配置决定，用户可配可覆盖。

    优先级：--dest 显式指定 > CLI --video-dest/--image-dest > config.local.json > 默认值。
    - dest=baidu/feishu：全部媒体走单一目标（用户显式指定，优先级最高）
    - dest=auto（默认）：视频→cfg["video_dest"]（默认 baidu），
      图片→cfg["image_dest"]（默认 feishu）。两键在 init 时询问用户，
      运行时可用 --video-dest/--image-dest 单次覆盖。
    混合媒体始终按类型分流，绝不混装到同一目标。
    """
    if dest == "baidu":
        return [("baidu", list(vids) + list(imgs))] if (vids or imgs) else []
    if dest == "feishu":
        return [("feishu", list(vids) + list(imgs))] if (vids or imgs) else []
    video_dest = resolve_dest(cfg.get("video_dest"), "baidu")
    image_dest = resolve_dest(cfg.get("image_dest"), "feishu")
    plan = []
    if vids:
        plan.append((video_dest, list(vids)))
    if imgs:
        plan.append((image_dest, list(imgs)))
    return plan


# ---------------------------------------------------------------- 主流程

def _run_inner(args, task_dir, started, result) -> int:
    platform = detect_platform(args.url)
    if not platform:
        result.update(ok=False, stage="parse", error="invalid_input",
                      hint="URL 未识别为 B站/抖音/小红书链接")
        return 1

    backend = get_backend(platform, DATA_DIR)
    result.update(platform=platform, backend=backend.name,
                  install_mode=backend.install_mode())
    # 记录平台元信息，供 poll 推断后端以解析进度、给出可恢复命令。
    # P1：同时记录 url——断点续跑必须校验是同一个链接，否则会把旧目录的
    # 成品当成新链接的内容上传（静默传错）。老目录没有 url 键则跳过校验。
    meta_file = Path(task_dir) / "run_meta.json"
    prev_url = ""
    if meta_file.exists():
        try:
            prev_url = json.loads(meta_file.read_text(encoding="utf-8")).get("url", "") or ""
        except (OSError, ValueError):
            prev_url = ""
    try:
        meta_file.write_text(
            json.dumps({"platform": platform, "backend": backend.name,
                        "url": args.url}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    # 后端可用性：比"vendor 缺失"那种模糊报错更早、更明确
    avail, detail = backend.available()
    if not avail:
        result.update(ok=False, stage="backend", error="backend_unavailable",
                      detail=detail,
                      hint="先跑 `python3 scripts/init_wizard.py` 启用该平台")
        return 1

    timeout = args.timeout or PLATFORM_TIMEOUT.get(platform, 600)
    logpath = str(Path(task_dir) / "download.log")

    # --- 下载（断点续跑沿用原逻辑：上次 exit 0 且已有成品文件则跳过） ---
    if args.task and log_exit(logpath) == 0:
        if prev_url and prev_url != args.url:
            result.update(ok=False, stage="parse", error="task_url_mismatch",
                          detail="任务目录 %s 上次下载的是另一链接" % task_dir,
                          hint="换 --task 新目录跑本次链接，或用原链接 resume："
                               "social_dl.py run --url %s --task %s" % (prev_url, task_dir))
            return 1
        pre_imgs, pre_vids = collect_media(task_dir)
        if pre_imgs or pre_vids:
            log("[run] 断点续跑：沿用已完成的下载")
            rc, tail = 0, log_tail(logpath, 50)
        else:
            rc, tail = -1, log_tail(logpath, 50)
    else:
        try:
            ctx = backend.prepare(Path(task_dir))      # 如抖音的隔离 config
            cmd = backend.build_cmd(args.url, Path(task_dir), ctx)
        except BackendError as e:
            result.update(ok=False, stage="backend", error="contract_break",
                          capability=e.capability, detail=str(e),
                          hint="上游 CLI 参数可能已变更，跑 `doctor` 确认后更新适配器 CAPS")
            return 1
        rc, tail = exec_bg(cmd, cwd=str(backend.run_cwd(Path(task_dir))),
                           logfile=logpath, timeout=timeout)

    if rc == 124:
        result.update(ok=False, stage="download", status="running",
                      progress=read_progress(backend, logpath),
                      poll="social_dl.py poll --task %s" % task_dir,
                      hint="进程仍在后台运行，凭 task 目录续看，不要重复启动")
        return 124
    if rc != 0:
        result.update(ok=False, stage="download", exit=rc, log=logpath,
                      tail=tail[-8:], hint=backend.collect_hint())
        return 1

    imgs, vids = collect_media(task_dir)
    if not imgs and not vids:
        # B-4：这是最高频的失败（Cookie 过期 -> exit 0 但 0 文件），
        # 必须给出可行动的提示，而不是干巴巴的 no_media_files
        login_ok, login_msg = backend.login_state()
        hint = backend.collect_hint()
        if not login_ok:
            hint = ("登录态可疑（%s）——这是 exit 0 但 0 文件的首要原因。%s"
                    % (login_msg, hint))
        result.update(ok=False, stage="download", error="no_media_files",
                      log=logpath, tail=tail[-8:],
                      login_ok=login_ok, login_detail=login_msg, hint=hint)
        return 1

    bad = verify_local(imgs + vids)
    if bad:
        result.update(ok=False, stage="verify", bad=bad[:10],
                      hint="文件不完整（空文件/截断/损坏），多为下载中断")
        return 1

    meta = read_upstream_manifest(task_dir)
    result.update(ok=True, stage="downloaded", title=meta.get("title", ""),
                  images=len(imgs), videos=len(vids), manifest=meta)

    # --- 溯源 manifest：随归档一起进云盘 ---
    manifest_path = write_manifest(Path(task_dir), build_manifest(
        args.url, platform, backend.name, started, imgs, vids, meta))

    if args.no_upload:
        result["local_manifest"] = manifest_path
        return 0

    name = args.name or ""
    if not name:
        try:
            name = build_archive_name(args.name_template, platform, meta, started)
        except ValueError as e:
            result.update(ok=False, stage="upload", error="invalid_archive_name",
                          hint=str(e))
            return 1
    cfg = load_config()
    # CLI 覆盖 > 配置 > 默认；非法值明确报错，绝不静默回退
    try:
        if getattr(args, "video_dest", ""):
            cfg["video_dest"] = resolve_dest(args.video_dest, None)
        if getattr(args, "image_dest", ""):
            cfg["image_dest"] = resolve_dest(args.image_dest, None)
        plan = plan_uploads(args.dest, imgs, vids, cfg)
    except ValueError as e:
        result.update(ok=False, stage="upload", error="invalid_upload_dest",
                      hint=str(e))
        return 1
    result["archive_name"] = name
    if not plan:
        result.update(ok=False, stage="upload", error="nothing_to_upload")
        return 1

    ok_all, expected_total = True, 0
    for dest, files in plan:
        # manifest.json 随每一组分流一起归档，保证任一侧都能追溯来源
        group = [manifest_path] + list(files)
        ok = (upload_to_feishu(group, name, cfg, task_dir, result) if dest == "feishu"
              else upload_to_baidu(group, name, cfg, task_dir, result))
        ok_all = ok_all and ok
        expected_total += len(group)

    result["dest"] = "auto-split" if len(plan) > 1 else (plan[0][0] if plan else "")
    result["upload_expected"] = expected_total
    if not ok_all:
        result["ok"] = False
        result.setdefault("stage", "upload")
        return 1
    return 0


def cmd_run(args) -> int:
    """外层：统一清理 + 统一输出，保证 cleaned 字段一定带出（P0-2/P0-3）。"""
    started = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    task_dir = args.task or tempfile.mkdtemp(prefix="social-media-", dir="/tmp")
    os.makedirs(task_dir, exist_ok=True)
    result = {"ok": False, "task_dir": task_dir, "started": started}
    code = 1
    try:
        code = _run_inner(args, task_dir, started, result)
    except BackendError as e:
        result.update(ok=False, stage="backend", error="contract_break",
                      backend=e.backend, capability=e.capability, detail=str(e))
        code = 1
    except Exception as e:                                     # noqa: BLE001
        result.update(ok=False, stage="unexpected",
                      error="%s: %s" % (type(e).__name__, e))
        code = 1
    finally:
        if args.cleanup:
            try:
                shutil.rmtree(task_dir)
                result["cleaned"] = True
            except OSError as e:
                result["cleaned"] = False
                result["residue"] = task_dir
                result["clean_error"] = str(e)
        print(json.dumps(result, ensure_ascii=False))
    return code


def cmd_poll(args) -> int:
    """只读轮询：pid 存活 + 日志尾 + 文件数 + 进度。不做任何写入/启动。"""
    task_dir = Path(args.task)
    logpath = str(task_dir / "download.log")
    pid = read_pid(logpath)
    code = log_exit(logpath)
    imgs, vids = collect_media(task_dir)
    platform, progress, task_url = "", None, ""
    meta_file = task_dir / "run_meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            platform = meta.get("platform", "") or ""
            task_url = meta.get("url", "") or ""
        except (OSError, ValueError):
            pass
    if platform:
        try:
            progress = read_progress(get_backend(platform, DATA_DIR), logpath)
        except BackendError:
            pass
    # P1：超时返回（124）后进程若又退出了，日志里永远不会补 [exit N]，
    # 此时 exit 恒为 null、resume 恒为空——必须显式报出这种"结束但无标记"态。
    alive = pid_alive(pid)
    if code is None and pid > 0 and not alive and (task_dir / "download.log").exists():
        state = "ended_unmarked"
        state_hint = ("下载进程已结束，但日志缺少退出标记（多为此前超时返回后"
                      "后台跑完）。目录内若已有成品文件，可用原链接 resume 重跑"
                      "完成后半程（下载→校验→上传）。")
    elif code is None and alive:
        state, state_hint = "running", "进程仍在后台运行，凭 task 目录续看，不要重复启动"
    elif code == 0:
        state, state_hint = "done", ""
    elif code is None:
        state, state_hint = "unknown", "无退出标记且进程不在运行，可能从未启动或日志被清理"
    else:
        state, state_hint = "failed", ""
    resume = ""
    if task_url and state in ("done", "ended_unmarked"):
        resume = "social_dl.py run --url %s --task %s" % (task_url, task_dir)
    print(json.dumps({
        "task_dir": str(task_dir),
        "download": {"pid": pid, "alive": alive,
                     "exit": code, "state": state, "state_hint": state_hint,
                     "tail": log_tail(logpath, 8)},
        "progress": progress,
        "media": {"images": len(imgs), "videos": len(vids)},
        "resume": resume,
    }, ensure_ascii=False))
    return 0


def _collect_for_upload(task_dir: Path):
    imgs, vids = collect_media(task_dir)
    files = vids + imgs
    mp = task_dir / "manifest.json"
    if mp.exists():
        files = [str(mp)] + files
    return files


def cmd_upload_feishu(args) -> int:
    """从已存在的 task_dir 上传（下载成功后单独重传）。

    P1-1：docstring 里早就写了这两个子命令，但此前从未注册，属于幽灵命令。
    """
    task_dir = Path(args.task)
    if not task_dir.exists():
        print(json.dumps({"ok": False, "error": "task_dir 不存在: %s" % task_dir},
                         ensure_ascii=False))
        return 1
    files = _collect_for_upload(task_dir)
    if not files:
        print(json.dumps({"ok": False, "error": "no_media_files"}, ensure_ascii=False))
        return 1
    name = args.name or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    result = {"ok": False, "task_dir": str(task_dir), "archive_name": name}
    ok = upload_to_feishu(files, name, load_config(), str(task_dir), result)
    result["ok"] = ok
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 1


def cmd_upload_baidu(args) -> int:
    task_dir = Path(args.task)
    if not task_dir.exists():
        print(json.dumps({"ok": False, "error": "task_dir 不存在: %s" % task_dir},
                         ensure_ascii=False))
        return 1
    files = _collect_for_upload(task_dir)
    if not files:
        print(json.dumps({"ok": False, "error": "no_media_files"}, ensure_ascii=False))
        return 1
    name = args.name or args.remote_dir or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    result = {"ok": False, "task_dir": str(task_dir), "archive_name": name}
    ok = upload_to_baidu(files, name, load_config(), str(task_dir), result)
    result["ok"] = ok
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 1


def cmd_doctor(args) -> int:
    """契约自检：确认各后端仍能协商出合法命令。升级上游后跑一次。"""
    report, all_ok = [], True
    for b in all_backends(DATA_DIR):
        entry = {"platform": b.platform, "backend": b.name,
                 "install_mode": b.install_mode(), "warnings": []}
        avail, detail = b.available()
        entry["available"] = avail
        entry["detail"] = detail
        if avail:
            try:
                entry["negotiated_flags"] = b.negotiate_all()
            except BackendError as e:
                entry["error"] = str(e)
                entry["action"] = "上游可能已变更参数，确认后更新适配器 CAPS"
                all_ok = False
            if getattr(b, "unverified", None):
                entry["warnings"].append(
                    "以下能力未能用 --help 验证，已回退到首选参数：%s" % b.unverified)
            try:
                lg_ok, lg_msg = b.login_state()
                entry["login"] = {"ok": lg_ok, "detail": lg_msg}
            except Exception as e:                              # noqa: BLE001
                entry["login"] = {"ok": False,
                                  "detail": "%s: %s" % (type(e).__name__, e)}
        else:
            all_ok = False
        report.append(entry)
    print(json.dumps({"doctor": report, "all_ok": all_ok}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


def main(argv=None) -> int:
    from init_wizard import main as init_main

    ap = argparse.ArgumentParser(description="social-media-download-skills 编排入口")
    ap.add_argument("--init", action="store_true", help="跑首次初始化向导")
    ap.add_argument("--non-interactive", action="store_true")
    ap.add_argument("--yes", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="下载 + 校验 + 归档一站式")
    r.add_argument("--url", required=True)
    r.add_argument("--task", default="")
    r.add_argument("--name", default="", help="归档名；留空按模板生成")
    r.add_argument("--name-template", default=DEFAULT_NAME_TEMPLATE,
                   help="归档名模板，占位符 {platform} {title} {author} {date} {day}；"
                        "默认 {date} 即时间戳")
    r.add_argument("--dest", default="auto", choices=["auto", "feishu", "baidu"],
                   help="auto=按配置分流（默认）；feishu/baidu=全部媒体走单一目标")
    r.add_argument("--video-dest", default="", metavar="baidu|feishu",
                   help="auto 模式下覆盖配置的视频目的地")
    r.add_argument("--image-dest", default="", metavar="baidu|feishu",
                   help="auto 模式下覆盖配置的图片目的地")
    r.add_argument("--timeout", type=int, default=0, help="0 = 采用平台默认超时")
    r.add_argument("--no-upload", action="store_true")
    r.add_argument("--cleanup", action="store_true")

    d = sub.add_parser("download", help="仅下载不上传")
    d.add_argument("--url", required=True)
    d.add_argument("--task", default="")
    d.add_argument("--timeout", type=int, default=0)
    d.set_defaults(no_upload=True, dest="auto", name="", cleanup=False,
                   name_template=DEFAULT_NAME_TEMPLATE)

    q = sub.add_parser("poll", help="只读轮询任务状态（不重复启动）")
    q.add_argument("--task", required=True)

    u = sub.add_parser("upload-feishu", help="从已有 task_dir 上传到飞书（跳过下载）")
    u.add_argument("--task", required=True)
    u.add_argument("--name", default="")

    bd = sub.add_parser("upload-baidu", help="从已有 task_dir 上传到百度盘（跳过下载）")
    bd.add_argument("--task", required=True)
    bd.add_argument("--name", default="")
    bd.add_argument("--remote-dir", default="")

    sub.add_parser("doctor", help="契约自检：后端可用性与参数协商")

    args, _rest = ap.parse_known_args(argv)

    if args.init:
        sys.argv = ["init_wizard.py"] + [x for x in (argv or []) if x != "--init"]
        return init_main()

    handler = {"run": cmd_run, "download": cmd_run, "poll": cmd_poll,
               "upload-feishu": cmd_upload_feishu, "upload-baidu": cmd_upload_baidu,
               "doctor": cmd_doctor}.get(args.cmd)
    if handler is None:
        ap.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
