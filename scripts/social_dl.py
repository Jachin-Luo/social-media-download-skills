#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""social_dl.py — 社媒下载统一编排入口（新架构）。

设计原则（2026-09-03 实跑复盘）：
- 数组传参调子进程，不拼 shell（中文长文件名/空格/！安全）。
- 抖音 CLI 的 config.link 污染：每次生成隔离 task-config，强制 link: []。
- 飞书上传必须 cwd=中转目录 + 相对路径 ./name（绝对路径会被 lark-cli 拒）。
- 同一目标目录串行上传；远端 ls 对账通过才算成功；try/finally 清理。
- 不做 vision 识别（省 token），只做文件级校验。

用法示例：
    python3 social_dl.py --init --non-interactive --yes
    python3 social_dl.py run --url "https://v.douyin.com/xxx/" --verify-remote --cleanup
    python3 social_dl.py download --url "https://v.douyin.com/xxx/"
    python3 social_dl.py upload-feishu --task <task_dir> --name "2026-09-03_14-56-39"
    python3 social_dl.py upload-baidu --task <task_dir> --remote-dir "social-media-download/xxx"
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
# 保证 from init_wizard import 在任意 cwd 下可用
sys.path.insert(0, str(Path(__file__).resolve().parent))
DATA_DIR = Path(os.environ.get(
    "SOCIAL_DL_DATA_DIR",
    str(Path.home() / ".local/share/social-media-download-skills"),
))
CONFIG_PATH = DATA_DIR / "config.local.json"


def log(*a):
    print(*a, flush=True)


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {}


def detect_platform(url):
    u = url.lower()
    if "douyin.com" in u:
        return "douyin"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xiaohongshu"
    return ""


POLL_INTERVAL = 5


def exec_bg(cmd, cwd, logfile, timeout=600, interval=POLL_INTERVAL):
    """全部耗时外部命令统一入口：后台脱离启动 + pid 文件 + 轮询等待。

    返回 (rc, 日志全文尾部)。超时返回 (124, 尾部)，进程留后台继续跑，
    调用方凭 task 目录用 `poll --task` 续看，绝不重复启动。
    """
    import time
    Path(logfile).parent.mkdir(parents=True, exist_ok=True)
    lf = open(logfile, "a", encoding="utf-8")
    lf.write(f"$ {' '.join(cmd)}\n")
    lf.flush()
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True)
    Path(str(logfile) + ".pid").write_text(str(p.pid), encoding="utf-8")
    log(f"[bg] pid={p.pid} log={logfile}")
    waited = 0
    while waited < timeout:
        code = p.poll()
        if code is not None:
            lf.write(f"\n[exit {code}]\n")
            lf.close()
            return code, "\n".join(log_tail(logfile, 200))
        time.sleep(interval)
        waited += interval
    lf.close()
    return 124, "\n".join(log_tail(logfile, 50))


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def read_pid(logfile):
    try:
        return int(Path(str(logfile) + ".pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return -1


def log_tail(logfile, n=15):
    try:
        lines = Path(logfile).read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-n:]
    except OSError:
        return []


def log_exit(logfile):
    """从日志尾部找 [exit N]，返回码或 None。"""
    for line in reversed(log_tail(logfile, 5)):
        m = re.search(r"\[exit (-?\d+)\]", line)
        if m:
            return int(m.group(1))
    return None


def run_json(cmd, cwd):
    """跑短命令并解析 stdout 中的 JSON（lark-cli --format json）。"""
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=120)
    txt = (p.stdout or "").strip()
    try:
        start = txt.index("{")
        return p.returncode, json.loads(txt[start:])
    except (ValueError, json.JSONDecodeError):
        return p.returncode, {"ok": False, "raw": txt[-500:], "stderr": (p.stderr or "")[-300:]}


# ---------------- 抖音下载 ----------------

def make_isolated_config(vendor_dir, task_dir):
    """复制 vendor config.yml -> task-config.yml 并强制 link: []。"""
    src = Path(vendor_dir) / "config.yml"
    if not src.exists():
        src = Path(vendor_dir) / "config.example.yml"
    text = src.read_text(encoding="utf-8")
    text = re.sub(r"link:\s*\n(\s*-\s*.*\n)+", "link: []\n", text)
    text = re.sub(r"(?m)^link:[ \t]+\S.*$", "link: []", text)  # link 单行字符串形态也清空（已是 [] 时幂等）
    if "link:" not in text:
        text = "link: []\n" + text
    dst = Path(task_dir) / "task-config.yml"
    dst.write_text(text, encoding="utf-8")
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    return str(dst)


def douyin_download(url, task_dir, timeout=600):
    vendor = DATA_DIR / "douyin-downloader"
    if not vendor.exists():
        return 2, "vendor 缺失，请先跑: python3 social_dl.py --init --non-interactive --yes"
    cfg = make_isolated_config(vendor, task_dir)
    logpath = str(Path(task_dir) / "download.log")
    rc, _ = exec_bg(["uv", "run", "douyin-dl", "--url", url,
                     "--path", str(task_dir), "--config", cfg],
                    cwd=str(vendor), logfile=logpath, timeout=timeout)
    return rc, logpath


def bilibili_download(url, task_dir, timeout=900):
    logpath = str(Path(task_dir) / "download.log")
    rc, _ = exec_bg(["yutto", url, "-d", str(task_dir)],
                    cwd=str(task_dir), logfile=logpath, timeout=timeout)
    return rc, logpath


def xiaohongshu_download(url, task_dir, timeout=600):
    vendor = DATA_DIR / "XHS-Downloader"
    if not vendor.exists():
        return 2, "vendor 缺失，请先跑: python3 social_dl.py --init --non-interactive --yes"
    logpath = str(Path(task_dir) / "download.log")
    rc, _ = exec_bg(["uv", "run", "python", "-c",
                     "from source.CLI.main import cli; cli()",
                     "--url", url, "--work_path", str(task_dir),
                     "--folder_name", "media"],
                    cwd=str(vendor), logfile=logpath, timeout=timeout)
    return rc, logpath


def read_manifest(task_dir):
    mp = Path(task_dir) / "download_manifest.jsonl"
    if not mp.exists():
        return {}
    try:
        line = mp.read_text(encoding="utf-8").strip().splitlines()[0]
        return json.loads(line)
    except (OSError, IndexError, json.JSONDecodeError):
        return {}


def collect_media(task_dir):
    """返回 ([images], [videos]) 绝对路径。"""
    imgs, vids = [], []
    for pat in ("**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp"):
        imgs += [str(p) for p in Path(task_dir).glob(pat) if p.is_file()]
    for pat in ("**/*.mp4", "**/*.mkv", "**/*.webm"):
        vids += [str(p) for p in Path(task_dir).glob(pat)
                 if p.is_file() and not p.name.endswith(".tmp")]
    return sorted(imgs), sorted(vids)


def verify_local(files):
    """文件存在且 >0。视频抽查 ffprobe，图片抽查文件头。"""
    bad = []
    for f in files:
        p = Path(f)
        if not p.exists() or p.stat().st_size == 0:
            bad.append(f)
            continue
        if p.suffix.lower() in (".mp4", ".mkv", ".webm"):
            r = subprocess.run(["ffprobe", "-v", "quiet", "-show_format", str(p)],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                bad.append(f)
        elif p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            with open(p, "rb") as fh:
                head = fh.read(4)
            if len(head) < 4:
                bad.append(f)
    return bad


# ---------------- 飞书上传（相对路径串行） ----------------

def feishu_mkdir(name, parent=""):
    cmd = ["lark-cli", "drive", "+create-folder", "--name", name,
           "--as", "user", "--format", "json"]
    if parent:
        cmd += ["--folder-token", parent]
    rc, data = run_json(cmd, cwd=str(DATA_DIR))
    if rc == 0 and data.get("ok") and data.get("data", {}).get("folder_token"):
        return data["data"]["folder_token"], data["data"]
    return "", data


def feishu_upload_serial(files, folder_token):
    """中转到 staging（原文件名保留），cwd=staging + ./相对路径串行上传。"""
    staging = Path(tempfile.mkdtemp(prefix="feishu-stage-", dir="/tmp"))
    results = []
    try:
        for src in files:
            shutil.copy(src, staging / Path(src).name)
        for src in files:
            name = Path(src).name
            cmd = ["lark-cli", "drive", "+upload", "--file", "./" + name,
                   "--folder-token", folder_token, "--as", "user", "--format", "json"]
            rc, data = run_json(cmd, cwd=str(staging))
            ok = rc == 0 and data.get("ok")
            results.append({"file": name,
                            "ok": ok,
                            "file_token": (data.get("data") or {}).get("file_token", "") if ok else "",
                            "raw": "" if ok else json.dumps(data, ensure_ascii=False)[:300]})
            if not ok:
                break  # 同目录串行，失败即停
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return results


def feishu_verify(folder_token):
    cmd = ["lark-cli", "drive", "files", "list",
           "--params", json.dumps({"folder_token": folder_token, "page_size": 100}),
           "--format", "json"]
    rc, data = run_json(cmd, cwd=str(DATA_DIR))
    if rc != 0 or not data.get("ok"):
        return -1
    files = (data.get("data") or {}).get("files", [])
    return len(files)


# ---------------- 百度盘上传 ----------------

def baidu_mkdir(remote_dir):
    p = subprocess.run(["bdpan", "mkdir", remote_dir],
                       capture_output=True, text=True, timeout=60)
    return p.returncode == 0 or "已存在" in (p.stdout + p.stderr)


def baidu_upload(local_file, remote_file, task_dir=""):
    logdir = Path(task_dir) / "logs" if task_dir else Path(tempfile.mkdtemp(prefix="bd-", dir="/tmp"))
    logdir.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"\W+", "_", Path(local_file).name)[:40]
    rc, tail = exec_bg(["bdpan", "upload", local_file, remote_file],
                       cwd=str(task_dir or "/tmp"),
                       logfile=str(logdir / f"upload-{tag}.log"), timeout=900)
    ok = rc == 0 and ("上传成功" in tail or "成功" in tail)
    return ok, tail[-500:]


def baidu_verify(remote_dir):
    """--json 结构化对账：数非目录项，不怕换行/特殊字符文件名。"""
    p = subprocess.run(["bdpan", "ls", "/apps/bdpan/" + remote_dir.lstrip("/"), "--json"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return -1
    try:
        items = json.loads(p.stdout.strip())
        return sum(1 for it in items if not it.get("isdir", False))
    except (ValueError, TypeError, AttributeError):
        return -1


# ---------------- 主流程 ----------------

def cmd_run(args):
    started = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    task_dir = args.task or tempfile.mkdtemp(prefix="social-media-", dir="/tmp")
    os.makedirs(task_dir, exist_ok=True)
    log(f"[run] task={task_dir} start={started}")
    platform = detect_platform(args.url)
    if not platform:
        print(json.dumps({"ok": False, "stage": "parse", "error": "invalid_input"},
                         ensure_ascii=False))
        return 1
    logpath = str(Path(task_dir) / "download.log")
    if args.task and "[exit 0]" in "\n".join(log_tail(logpath, 5)):
        # 断点续跑：后台已完成的下载不重复启动，直接沿用文件
        pre_imgs, pre_vids = collect_media(task_dir)
        if pre_imgs or pre_vids:
            log("[run] 断点续跑：沿用已完成的下载")
            rc, info = 0, logpath
        else:
            rc, info = -1, logpath
    elif platform == "douyin":
        rc, info = douyin_download(args.url, task_dir, args.timeout)
    elif platform == "bilibili":
        rc, info = bilibili_download(args.url, task_dir, args.timeout)
    else:
        rc, info = xiaohongshu_download(args.url, task_dir, args.timeout)
    if rc == 124:
        print(json.dumps({"ok": False, "stage": "download", "status": "running",
                          "poll": f"social_dl.py poll --task {task_dir}",
                          "task_dir": task_dir}, ensure_ascii=False))
        return 124
    if rc != 0:
        print(json.dumps({"ok": False, "stage": "download", "exit": rc,
                          "log": info, "task_dir": task_dir}, ensure_ascii=False))
        return 1
    imgs, vids = collect_media(task_dir)
    if not imgs and not vids:
        # 后端常 exit 0 但 Success 0（如无 Cookie 被反爬），无文件必须判下载失败
        print(json.dumps({"ok": False, "stage": "download", "error": "no_media_files",
                          "log": info, "task_dir": task_dir}, ensure_ascii=False))
        return 1
    bad = verify_local(imgs + vids)
    if bad:
        print(json.dumps({"ok": False, "stage": "verify", "bad": bad,
                          "task_dir": task_dir}, ensure_ascii=False))
        return 1
    manifest = read_manifest(task_dir)
    result = {"ok": True, "stage": "downloaded", "platform": platform,
              "manifest": manifest, "images": len(imgs), "videos": len(vids),
              "task_dir": task_dir, "started": started}
    # 按配置自动归档
    cfg = load_config()
    if args.dest == "auto":
        dest = cfg.get("video_dest" if vids else "image_dest", "feishu" if imgs else "baidu")
    else:
        dest = args.dest
    if args.no_upload:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if dest == "feishu" and (imgs or vids):
        parent = cfg.get("feishu_parent_folder_token", "")
        token, _ = feishu_mkdir(args.name or started, parent)
        if not token:
            print(json.dumps({"ok": False, "stage": "mkdir-feishu"}, ensure_ascii=False))
            return 1
        res = feishu_upload_serial(imgs + vids, token)
        n = feishu_verify(token)
        result.update({"dest": "feishu", "folder_token": token,
                       "uploaded": sum(1 for r in res if r["ok"]),
                       "remote_count": n})
    elif dest == "baidu" and (vids or imgs):
        base = cfg.get("baidu_base", "social-media-download")
        rdir = f"{base}/{args.name or started}"
        baidu_mkdir(rdir)
        ups = []
        for f in vids + imgs:
            ok, tail = baidu_upload(f, f"{rdir}/{Path(f).name}", task_dir)
            ups.append(ok)
            if not ok:
                log(f"[baidu] 上传失败 {f}: {tail[-200:]}")
                break
        n = baidu_verify(rdir)
        result.update({"dest": "baidu", "remote_dir": rdir,
                       "uploaded": sum(1 for x in ups if x), "remote_count": n})
    print(json.dumps(result, ensure_ascii=False))
    if args.cleanup:
        shutil.rmtree(task_dir, ignore_errors=True)
        result["cleaned"] = True
    if not args.no_upload:
        expected = len(imgs) + len(vids)
        if result.get("uploaded", 0) < expected or result.get("remote_count", -1) < expected:
            return 1
    return 0


def cmd_poll(args):
    """只读轮询：pid 存活 + 日志尾 + 文件数。不做任何写入/启动。"""
    task_dir = args.task
    logpath = str(Path(task_dir) / "download.log")
    pid = read_pid(logpath)
    code = log_exit(logpath)
    imgs, vids = collect_media(task_dir)
    print(json.dumps({
        "task_dir": task_dir,
        "download": {"pid": pid, "alive": pid_alive(pid) if pid > 0 else False,
                     "exit": code, "tail": log_tail(logpath, 8)},
        "media": {"images": len(imgs), "videos": len(vids)},
        "resume": f"social_dl.py run --url <链接> --task {task_dir}" if code == 0 else "",
    }, ensure_ascii=False))
    return 0


def main(argv=None):
    from init_wizard import main as init_main
    ap = argparse.ArgumentParser(description="social-media-download-skills 新编排入口")
    ap.add_argument("--init", action="store_true", help="跑首次初始化向导")
    sub = ap.add_subparsers(dest="cmd")
    # --init 透传参数
    ap.add_argument("--non-interactive", action="store_true")
    ap.add_argument("--yes", action="store_true")
    r = sub.add_parser("run", help="下载+校验+归档一站式")
    r.add_argument("--url", required=True)
    r.add_argument("--task", default="")
    r.add_argument("--name", default="", help="归档文件夹名，默认任务开始时间")
    r.add_argument("--dest", default="auto", choices=["auto", "feishu", "baidu"])
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--no-upload", action="store_true")
    r.add_argument("--cleanup", action="store_true")
    d = sub.add_parser("download", help="仅下载")
    d.add_argument("--url", required=True)
    d.add_argument("--task", default="")
    d.add_argument("--timeout", type=int, default=600)
    q = sub.add_parser("poll", help="只读轮询任务状态（不重复启动）")
    q.add_argument("--task", required=True)
    args, rest = ap.parse_known_args(argv)
    if args.init:
        sys.argv = ["init_wizard.py"] + [x for x in (argv or []) if x != "--init"]
        return init_main()
    if args.cmd == "download":
        args.no_upload, args.dest, args.name, args.cleanup = True, "auto", "", False
        return cmd_run(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "poll":
        return cmd_poll(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
