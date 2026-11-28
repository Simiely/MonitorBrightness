# -*- coding: utf-8 -*-
"""R6 批次 D：app 层假 self 回归（不构造 Tk、不碰硬件）。"""

import types
import unittest
from unittest import mock

import app.brightness as br_mod
import app.common as common_mod
import app.topology_actions as ta_mod


def _var():
    seen = []
    return types.SimpleNamespace(set=seen.append, seen=seen), seen


def _msg():
    return types.SimpleNamespace(showwarning=mock.Mock(), showerror=mock.Mock())


class TopologyActionsTest(unittest.TestCase):
    """三个 GUI 动作用最小 fake self 驱动，断言回调与后台任务。"""

    def _fake(self):
        var, seen = _var()
        return types.SimpleNamespace(
            msg=_msg(), mon_models={}, status_var=var, status_seen=seen,
            run_bg=mock.Mock(), cfg={"monitors": [1]},
            _busy=False, _queue_screen_action=mock.Mock(),
            _finish_screen_off=mock.Mock(), _finish_topology_enable=mock.Mock(),
            refresh_monitors=mock.Mock(),
            probe_screens=mock.Mock(), status_card=types.SimpleNamespace(render=mock.Mock()),
            _status_payload=mock.Mock(return_value={"state": "on"}),
        )

    def test_ask_heir_no_candidates_warns(self):
        fake = self._fake()
        with mock.patch.object(ta_mod, "topology_heir_candidates", return_value=[]), \
             mock.patch.object(ta_mod, "dlog"):
            ta_mod._TopologyActions._ask_heir(fake, 0, ["a1"])
        fake.msg.showwarning.assert_called_once()
        self.assertIn("一块屏都不剩", fake.msg.showwarning.call_args[0][1])

    def test_ask_heir_picked_queues_background_job(self):
        fake = self._fake()
        fake.mon_models = {0: "MODEL-Y"}
        cands = [{"anchor": "a2", "vdd": False, "active": True}]
        with mock.patch.object(ta_mod, "topology_heir_candidates", return_value=cands), \
             mock.patch.object(ta_mod, "dlog"), \
             mock.patch.object(ta_mod, "HeirSheet") as sheet, \
             mock.patch.object(ta_mod, "topology_off", return_value=(True, [])) as off:
            ta_mod._TopologyActions._ask_heir(fake, 0, ["a1"])
            self.assertEqual(sheet.call_args[0][2], "MODEL-Y")
            sheet.call_args[0][3]("a2")            # 模拟用户选中继承人
            fake.run_bg.call_args[0][0]()          # 后台 job 不碰 Tk
        self.assertEqual(fake._busy_action, "off")
        self.assertTrue(fake._row_action)
        self.assertIn("MODEL-Y", fake.status_seen[-1])
        self.assertTrue(fake.run_bg.call_args[1]["busy"])
        off.assert_called_once_with([0], fake.cfg, heir="a2")

    def test_ask_heir_sheet_error_is_reported(self):
        fake = self._fake()
        with mock.patch.object(ta_mod, "topology_heir_candidates",
                               return_value=[{"anchor": "a2", "vdd": False,
                                              "active": True}]), \
             mock.patch.object(ta_mod, "dlog"), \
             mock.patch.object(ta_mod, "HeirSheet", side_effect=RuntimeError("no tk")):
            ta_mod._TopologyActions._ask_heir(fake, 0, [])
        fake.msg.showerror.assert_called_once()

    def test_topology_enable_busy_queues(self):
        fake = self._fake()
        fake._busy = True
        ta_mod._TopologyActions.topology_enable(fake, "a2")
        fake._queue_screen_action.assert_called_once_with("on")
        fake.run_bg.assert_not_called()

    def test_topology_enable_starts_job(self):
        fake = self._fake()
        with mock.patch.object(ta_mod, "dlog"), \
             mock.patch.object(ta_mod, "topology_enable_anchor",
                               return_value=(True, [])) as enable:
            ta_mod._TopologyActions.topology_enable(fake, "a2")
            self.assertEqual(fake.run_bg.call_args[0][0](), (True, []))
        enable.assert_called_once_with(fake.cfg, "a2")
        self.assertEqual(fake._busy_action, "on")

    def test_finish_topology_enable_success(self):
        fake = self._fake()
        with mock.patch.object(ta_mod, "_as_ok_errs", return_value=([0], [])), \
             mock.patch.object(ta_mod, "save_config") as save:
            ta_mod._TopologyActions._finish_topology_enable(fake, (True, []))
        save.assert_called_once_with(fake.cfg)
        fake.status_card.render.assert_called_once()
        fake.refresh_monitors.assert_called_once()
        fake.probe_screens.assert_called_once()

    def test_finish_topology_enable_failure(self):
        fake = self._fake()
        with mock.patch.object(ta_mod, "_as_ok_errs", return_value=([], [(0, "boom")])), \
             mock.patch.object(ta_mod, "save_config") as save:
            ta_mod._TopologyActions._finish_topology_enable(fake, (False, []))
        save.assert_not_called()
        fake.msg.showwarning.assert_called_once()
        self.assertIn("显示器 0: boom", fake.status_seen[-1])


class BrightnessActionsTest(unittest.TestCase):
    """亮度控制器的纯计算与假 self 分支。"""

    def _fake(self):
        var, seen = _var()
        return types.SimpleNamespace(
            cfg={"presets": [10, 75, 100], "screen_off_method": "ddc"},
            status_var=var, status_seen=seen, msg=_msg(),
            mon_panel=types.SimpleNamespace(
                set_all=mock.Mock(), _suspend=False, rebuild=mock.Mock()),
            status_card=types.SimpleNamespace(
                set_hint_default=mock.Mock(), render=mock.Mock()),
            monitors=[{"index": 0}, {"index": 1}],
            _last_verdict={"state": "on"}, _brighting=False, _probing=False,
            apply_setting=mock.Mock(), read_current=mock.Mock(),
            _restore_selection=lambda prev: prev, selected_indices=lambda: [0],
        )

    def test_update_hint_brightness_uniform(self):
        fake = self._fake()
        br_mod._BrightnessActions._update_hint_brightness(fake, {0: 80, 1: 80})
        fake.status_card.set_hint_default.assert_called_once_with("当前 80% · 2 台显示器")

    def test_update_hint_brightness_mixed_and_off(self):
        fake = self._fake()
        br_mod._BrightnessActions._update_hint_brightness(fake, {0: 80, 1: 90})
        self.assertIn("当前 80/90%", fake.status_card.set_hint_default.call_args[0][0])

        fake.status_card.set_hint_default.reset_mock()
        fake._last_verdict = {"state": "off"}
        br_mod._BrightnessActions._update_hint_brightness(fake, {0: 80, 1: 80})
        fake.status_card.set_hint_default.assert_not_called()

    def test_status_payload_forwards_pure_function(self):
        fake = self._fake()
        got = br_mod._BrightnessActions._status_payload(fake, "on", "提示")
        self.assertEqual(got, common_mod.status_payload(fake.cfg, "on", "提示"))

    def test_presets_from_settings_when_sheet_present(self):
        fake = self._fake()

        class FakeSheet:
            preset_spins = [types.SimpleNamespace(get=lambda: "10"),
                            types.SimpleNamespace(get=lambda: "75"),
                            types.SimpleNamespace(get=lambda: "100")]

        sheet = FakeSheet()
        fake.root = types.SimpleNamespace(winfo_children=lambda: [object(), sheet])
        with mock.patch.object(br_mod, "SettingsSheet", FakeSheet):
            br_mod._BrightnessActions.apply_presets_from_settings(fake)
        fake.apply_setting.assert_called_once_with("presets", [10, 75, 100])

    def test_selection_shortcuts(self):
        fake = self._fake()
        br_mod._BrightnessActions.select_all(fake)
        fake.mon_panel.set_all.assert_called_with(True)
        br_mod._BrightnessActions.select_none(fake)
        fake.mon_panel.set_all.assert_called_with(False)
        br_mod._BrightnessActions.on_monitor_toggle(fake)
        self.assertEqual(fake.read_current.call_count, 3)
        fake.mon_panel._suspend = True
        br_mod._BrightnessActions.on_monitor_toggle(fake)
        self.assertEqual(fake.read_current.call_count, 3)

    def test_refresh_monitors_keeps_only_vdd_in_ddc_mode(self):
        fake = self._fake()
        targets = [{"index": 0, "handle": 11, "dev": "DISPLAY1",
                    "w": 1920, "h": 1080, "primary": True}]
        rows = [{"gdi": "DISPLAY1", "friendly": "主屏", "vdd": False},
                {"gdi": "DISPLAY2", "friendly": "串流", "vdd": True}]
        inactive = [rows[0], rows[1]]
        with mock.patch.object(br_mod, "enumerate_targets", return_value=targets), \
             mock.patch.object(br_mod, "release_targets") as release, \
             mock.patch.object(br_mod, "ccd_probe_topology", return_value=rows), \
             mock.patch.object(br_mod, "topology_inactive_rows", return_value=inactive):
            br_mod._BrightnessActions.refresh_monitors(fake)
        release.assert_called_once_with(targets)
        self.assertEqual(len(fake.topo_inactive), 1)
        self.assertTrue(fake.topo_inactive[0]["vdd"])
        fake.mon_panel.rebuild.assert_called_once()

    def test_finish_preset_reports_partial_failure(self):
        fake = self._fake()
        with mock.patch.object(br_mod, "_as_ok_errs", return_value=([], [(0, "boom")])):
            br_mod._BrightnessActions._finish_preset(fake, (False, []), 75)
        fake.msg.showwarning.assert_called_once()
        self.assertIn("显示器 0：boom", fake.status_seen[-1])
        fake.read_current.assert_called_once()
