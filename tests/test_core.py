#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心逻辑单元测试：纯函数、零外部依赖，CI 可直接跑。

    cd <repo> && python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from backends import detect_platform, normalize_platform, platform_label  # noqa: E402
from backends.douyin import DouyinBackend  # noqa: E402
from social_dl import (  # noqa: E402
    build_archive_name, build_manifest, clean_name, collect_media,
    mask_secrets, plan_uploads, resolve_dest,
)
from social_dl import _image_complete  # noqa: E402


class TestDetectPlatform(unittest.TestCase):
    def test_domains_and_shortlinks(self):
        cases = {
            "https://www.bilibili.com/video/BV1xx": "bilibili",
            "https://b23.tv/abc": "bilibili",
            "https://v.douyin.com/abc/": "douyin",
            "https://www.douyin.com/video/123": "douyin",
            "https://www.xiaohongshu.com/explore/abc": "xiaohongshu",
            "https://xhslink.com/abc": "xiaohongshu",
        }
        for url, expect in cases.items():
            self.assertEqual(detect_platform(url), expect, url)

    def test_invalid(self):
        self.assertEqual(detect_platform("https://youtube.com/watch?v=1"), "")
        self.assertEqual(detect_platform(""), "")
        self.assertEqual(detect_platform("not a url"), "")


class TestPlatformAlias(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_platform("b站"), "bilibili")
        self.assertEqual(normalize_platform("抖音"), "douyin")
        self.assertEqual(normalize_platform("小红书"), "xiaohongshu")
        self.assertEqual(normalize_platform("unknown"), "")

    def test_label(self):
        self.assertEqual(platform_label("bilibili"), "B站")
        self.assertEqual(platform_label("douyin"), "抖音")


class TestArchiveName(unittest.TestCase):
    """归档命名：默认时间戳，支持 {platform} {title} 模板。"""

    def test_default_timestamp(self):
        name = build_archive_name("{date}", "douyin",
                                  {"title": "测试"}, "2026-09-03_21-45-41")
        self.assertEqual(name, "2026-09-03_21-45-41")

    def test_platform_title_template(self):
        name = build_archive_name("{platform}_{title}_{day}", "xiaohongshu",
                                  {"title": "春日穿搭/分享"}, "2026-09-03_21-45-41")
        self.assertEqual(name, "小红书_春日穿搭_分享_2026-09-03")

    def test_empty_title_fallback(self):
        name = build_archive_name("{platform}_{title}_{day}", "douyin",
                                  {}, "2026-09-03_21-45-41")
        self.assertEqual(name, "抖音_无标题_2026-09-03")

    def test_long_title_truncated(self):
        name = build_archive_name("{title}", "douyin",
                                  {"title": "长" * 100}, "2026-09-03_21-45-41")
        self.assertLessEqual(len(name), 45)


class TestCleanName(unittest.TestCase):
    def test_invalid_chars(self):
        self.assertEqual(clean_name('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_collapses_underscores(self):
        self.assertEqual(clean_name("a///b"), "a_b")

    def test_empty(self):
        self.assertEqual(clean_name(""), "")


class TestIsolatedConfig(unittest.TestCase):
    """抖音隔离 config：link 清空的五种形态 + 幂等。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vendor = self.tmp / "douyin-downloader"
        self.vendor.mkdir()
        self.backend = DouyinBackend(self.tmp)

    def _gen(self, text):
        (self.vendor / "config.yml").write_text(text, encoding="utf-8")
        task = self.tmp / "task"
        task.mkdir(exist_ok=True)
        cfg = self.backend._make_isolated_config(task)
        content = Path(cfg).read_text(encoding="utf-8")
        return cfg, content

    def _assert_clean(self, text):
        self.assertIn("link: []", text)
        self.assertNotIn("douyin.com/1", text)
        links = re.findall(r"(?m)^\s*link:", text)
        self.assertEqual(len(links), 1, "link 键应恰好出现一次")

    def test_multiline_list(self):
        _, text = self._gen(
            "link:\n  - https://v.douyin.com/1/\n  - https://v.douyin.com/2/\n"
            "cookies:\n  a: b\n")
        self._assert_clean(text)

    def test_single_line(self):
        _, text = self._gen("link: https://v.douyin.com/1/\nx: 1\n")
        self._assert_clean(text)

    def test_already_empty(self):
        _, text = self._gen("link: []\ncookies: {a: b}\n")
        self._assert_clean(text)

    def test_indented(self):
        _, text = self._gen("root:\n  link:\n    - https://v.douyin.com/1/\nx: 1\n")
        self._assert_clean(text)

    def test_missing_link(self):
        _, text = self._gen("cookies:\n  a: b\n")
        self._assert_clean(text)

    def test_config_permission(self):
        cfg, _ = self._gen("link: []\n")
        mode = oct(Path(cfg).stat().st_mode & 0o777)
        self.assertEqual(mode, "0o600", "含 Cookie 的 task-config 权限应为 600")


class TestCollectMedia(unittest.TestCase):
    """媒体收集：扩展名覆盖（含 .m4s）、中间产物排除。"""

    def test_ext_and_partial(self):
        tmp = Path(tempfile.mkdtemp())
        for name in ("a.mp4", "b.jpg", "c.mp4.tmp", "d.m4s", "e.gif",
                     "f.png.part", "g.webp", "h.flv"):
            (tmp / name).write_bytes(b"x" * 10)
        imgs, vids = collect_media(tmp)
        self.assertEqual(sorted(Path(x).name for x in imgs),
                         ["b.jpg", "e.gif", "g.webp"])
        self.assertEqual(sorted(Path(x).name for x in vids), ["a.mp4", "d.m4s", "h.flv"])


class TestImageComplete(unittest.TestCase):
    """图片完整性：截断的 JPEG（有头无尾）必须被拒绝。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_truncated_jpeg_rejected(self):
        p = self.tmp / "bad.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 100)     # 有头无尾
        self.assertFalse(_image_complete(p))

    def test_valid_jpeg_accepted(self):
        p = self.tmp / "ok.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 100 + b"\xff\xd9")
        self.assertTrue(_image_complete(p))

    def test_valid_png_accepted(self):
        p = self.tmp / "ok.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100 + b"IEND\xaeB`\x82")
        self.assertTrue(_image_complete(p))

    def test_too_small_rejected(self):
        p = self.tmp / "tiny.jpg"
        p.write_bytes(b"\xff\xd8")
        self.assertFalse(_image_complete(p))


class TestPlanUploads(unittest.TestCase):
    """分流：auto 模式按配置决定目的地；显式 dest 覆盖一切；非法值报错。"""

    def test_auto_split_default(self):
        """无配置时回退默认：视频→百度、图片→飞书。"""
        plan = plan_uploads("auto", ["a.jpg"], ["v.mp4"], {})
        self.assertEqual(plan, [("baidu", ["v.mp4"]), ("feishu", ["a.jpg"])])

    def test_auto_video_only(self):
        self.assertEqual(plan_uploads("auto", [], ["v.mp4"], {}),
                         [("baidu", ["v.mp4"])])

    def test_auto_image_only(self):
        self.assertEqual(plan_uploads("auto", ["a.jpg"], [], {}),
                         [("feishu", ["a.jpg"])])

    def test_auto_custom_dest(self):
        """用户配置反向分流：视频→飞书、图片→百度。"""
        plan = plan_uploads("auto", ["a.jpg"], ["v.mp4"],
                            {"video_dest": "feishu", "image_dest": "baidu"})
        self.assertEqual(plan, [("feishu", ["v.mp4"]), ("baidu", ["a.jpg"])])

    def test_auto_same_dest_merges_types_not_files(self):
        """视频图片同目标时仍按类型分两批，不混装。"""
        plan = plan_uploads("auto", ["a.jpg"], ["v.mp4"],
                            {"video_dest": "feishu", "image_dest": "feishu"})
        self.assertEqual(plan, [("feishu", ["v.mp4"]), ("feishu", ["a.jpg"])])

    def test_auto_invalid_cfg_dest_raises(self):
        """配置写了非法目的地必须炸出来，不能静默回退。"""
        with self.assertRaises(ValueError):
            plan_uploads("auto", ["a.jpg"], [], {"image_dest": "onedrive"})

    def test_force_baidu(self):
        plan = plan_uploads("baidu", ["a.jpg"], ["v.mp4"], {})
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(plan[0][1]), 2)

    def test_resolve_dest_invalid(self):
        with self.assertRaises(ValueError):
            resolve_dest("onedrive", "baidu")

    def test_resolve_dest_normalizes(self):
        self.assertEqual(resolve_dest(" Feishu ", None), "feishu")
        self.assertEqual(resolve_dest("", "baidu"), "baidu")


class TestManifest(unittest.TestCase):
    def test_fields(self):
        m = build_manifest("https://x", "douyin", "douyin-downloader",
                           "2026-09-03", ["a.jpg"], ["b.mp4"],
                           {"title": "T", "author": "A"})
        self.assertEqual(m["source_url"], "https://x")
        self.assertEqual(m["platform_label"], "抖音")
        self.assertEqual(m["counts"], {"images": 1, "videos": 1})
        self.assertEqual(len(m["files"]), 2)
        self.assertEqual(m["title"], "T")

    def test_empty_meta(self):
        m = build_manifest("https://x", "douyin", "b", "t", [], [], {})
        self.assertEqual(m["title"], "")


class TestMaskSecrets(unittest.TestCase):
    def test_masked(self):
        s = mask_secrets("tool --token abc123 --url https://x")
        self.assertNotIn("abc123", s)
        self.assertIn("***", s)

    def test_plain_kept(self):
        self.assertEqual(mask_secrets("yutto url -d dir"),
                         "yutto url -d dir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
