# -*- coding: utf-8 -*-
"""R6 批次 E：hw/ddc 纯解析与 app/base 纯函数补强（不碰硬件、不建 Tk）。"""

import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import app.base as base_mod
import hw.ddc as ddc_mod


class DdcParsingTest(unittest.TestCase):
    """能力串解析、标签与亮度换算：全部喂固定数据。"""

    CAPS = "model( MODEL-X ) 60(11 12 0F ZZ) D6(01 04 05 09)"

    def test_caps_parsers(self):
        self.assertEqual(ddc_mod._caps_model(self.CAPS), "MODEL-X")
        self.assertEqual(ddc_mod._caps_inputs(self.CAPS), [17, 18, 15])
        self.assertEqual(ddc_mod._caps_power(self.CAPS), [1, 4, 5])
        self.assertIsNone(ddc_mod._caps_model("无型号"))
        self.assertEqual(ddc_mod._caps_inputs(""), [])

    def test_display_key_with_and_without_uid(self):
        target = {"dev": "DISPLAY1"}
        with mock.patch.object(ddc_mod, "_display_uid", return_value="a1b2c3d4"):
            self.assertEqual(ddc_mod._display_key(target), "DISPLAY1#a1b2c3d4")
        with mock.patch.object(ddc_mod, "_display_uid", return_value=""):
            self.assertEqual(ddc_mod._display_key(target), "DISPLAY1")

    def test_monitor_label(self):
        self.assertEqual(ddc_mod.monitor_label(1), "1: 显示器 1")
        got = ddc_mod.monitor_label(0, ("DISPLAY1", 1920, 1080, True), "MODEL-X")
        self.assertEqual(got, "0: MODEL-X · 1920×1080 · 主显示器 · DISPLAY1")

    def test_luminance_max_and_raw_from_percent(self):
        target = {"dev": "DISPLAY1", "handle": 7}
        with mock.patch.dict(ddc_mod._MAX_LUM, {}, clear=True), \
             mock.patch.object(ddc_mod, "_display_uid", return_value="uid"), \
             mock.patch.object(ddc_mod, "_read_vcp", return_value=(80, 200)) as read:
            self.assertEqual(ddc_mod._luminance_max(target), 200)
            self.assertEqual(ddc_mod._luminance_max(target), 200)  # 命中缓存
            self.assertEqual(read.call_count, 1)
            self.assertEqual(ddc_mod._raw_from_percent(target, 50), 100)

    def test_human_gap(self):
        self.assertEqual(ddc_mod._human_gap(3), "3 秒前")
        self.assertEqual(ddc_mod._human_gap(125), "2 分钟前")
        self.assertEqual(ddc_mod._human_gap(7200), "2 小时前")

    def test_probe_states_uses_canned_vcp(self):
        target = {"index": 0, "handle": 7, "dev": "DISPLAY1"}
        with mock.patch.object(ddc_mod, "enumerate_targets", return_value=[target]), \
             mock.patch.object(ddc_mod, "release_targets") as release, \
             mock.patch.object(ddc_mod, "_read_vcp",
                               side_effect=lambda h, code: (1,) if code == ddc_mod.VCP_POWER_MODE
                               else (80, 100)):
            states = ddc_mod.probe_states()
        release.assert_called_once()
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["state"], "on")
        self.assertEqual(states[0]["brightness"], 80)


class BasePureFunctionTest(unittest.TestCase):
    """入口装配层的纯函数与极小 fake self。"""

    def test_migrate_old_logs_copies_once_without_overwrite(self):
        tmp = tempfile.mkdtemp(prefix="mb-migrate-")
        src_dir, dst_dir = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
        os.makedirs(src_dir)
        os.makedirs(dst_dir)
        with open(os.path.join(src_dir, "events.log"), "w", encoding="utf-8") as f:
            f.write("old")
        with mock.patch.object(base_mod, "CONFIG_DIR", src_dir), \
             mock.patch.object(base_mod, "LOG_DIR", dst_dir):
            base_mod._migrate_old_logs()
            with open(os.path.join(dst_dir, "events.log"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "old")
            with open(os.path.join(src_dir, "events.log"), "w", encoding="utf-8") as f:
                f.write("new")
            base_mod._migrate_old_logs()
            with open(os.path.join(dst_dir, "events.log"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "old")   # 不覆盖已有
            shutil.rmtree(tmp, ignore_errors=True)

    def test_raster_line_contains_endpoints(self):
        pts = base_mod._raster_line(0, 0, 3, 3, thick=2)
        self.assertIn((0, 0), pts)
        self.assertIn((3, 3), pts)
        self.assertTrue(all(0 <= x <= 4 and 0 <= y <= 4 for x, y in pts))

    def test_route_wheel_walks_master_chain(self):
        calls = []
        leaf = types.SimpleNamespace(
            _wheel_target=lambda e: False,
            master=types.SimpleNamespace(
                _wheel_target=lambda e: calls.append(e) or True,
                master=None))
        event = types.SimpleNamespace(widget=leaf)
        self.assertEqual(base_mod._route_wheel(event), "break")
        self.assertEqual(calls, [event])
        self.assertIsNone(base_mod._route_wheel(types.SimpleNamespace(widget=None)))

    def test_install_wheel_router_binds_once(self):
        root = types.SimpleNamespace(bind_all=mock.Mock())
        base_mod.install_wheel_router(root)
        root.bind_all.assert_called_once_with(
            "<MouseWheel>", base_mod._route_wheel, add="+")

    def test_selected_indices_and_warn_config_error(self):
        fake = types.SimpleNamespace(
            mon_panel=types.SimpleNamespace(selected=lambda: [0, 2]),
            _config_error="坏配置",
            status_var=types.SimpleNamespace(set=mock.Mock()),
            msg=types.SimpleNamespace(showwarning=mock.Mock()))
        self.assertEqual(base_mod.App.selected_indices(fake), [0, 2])
        base_mod.App._warn_config_error(fake)
        self.assertIn("配置文件读取失败", fake.status_var.set.call_args[0][0])
        fake.msg.showwarning.assert_called_once_with("配置文件有问题", "坏配置")

    def test_fit_window_debounce_and_skip_unchanged_height(self):
        after = mock.Mock()
        geometry = mock.Mock()
        fake = types.SimpleNamespace(
            _fit_pending=False, _fitted_h=100, _fit_window_now=mock.Mock(),
            root=types.SimpleNamespace(after=after, update_idletasks=mock.Mock(),
                                       geometry=geometry),
            scroll=types.SimpleNamespace(
                body=types.SimpleNamespace(winfo_reqheight=lambda: 100)))
        base_mod.App._fit_window(fake)
        after.assert_called_once_with(80, fake._fit_window_now)
        self.assertTrue(fake._fit_pending)
        base_mod.App._fit_window_now(fake)       # 高度没变 → 不 geometry
        geometry.assert_not_called()
        self.assertFalse(fake._fit_pending)
        fake.scroll.body.winfo_reqheight = lambda: 140
        base_mod.App._fit_window_now(fake)
        geometry.assert_called_once_with("")
