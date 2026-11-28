# -*- coding: utf-8 -*-
"""R6 批次 C：CLI 纯逻辑补强（不碰硬件、不碰 Tk）。"""

import unittest
from unittest import mock

from tests.support import TempConfigCase, cli_mod


def _exit(code=0):
    """替代 sys.exit 并保留退出码，便于断言任务计划的成败判据。"""
    raise SystemExit(code)


class CliParserTest(unittest.TestCase):
    """三个解析器都是纯函数，参数写错时绝不能退化成「全部显示器」。"""

    def test_parse_level_arg(self):
        self.assertEqual(cli_mod.parse_level_arg(" 50 "), 50)
        self.assertEqual(cli_mod.parse_level_arg(0), 0)
        self.assertEqual(cli_mod.parse_level_arg(100), 100)
        for bad in ("abc", "", "-1", "101", "50.5", None):
            self.assertIsNone(cli_mod.parse_level_arg(bad), bad)

    def test_parse_index_arg(self):
        self.assertEqual(cli_mod.parse_index_arg("all"), [])
        self.assertEqual(cli_mod.parse_index_arg("*"), [])
        self.assertEqual(cli_mod.parse_index_arg("0,1"), [0, 1])
        self.assertEqual(cli_mod.parse_index_arg(" 2, 2,0 "), [0, 2])
        for bad in ("", "-1", "a", "0,x", ","):
            self.assertIsNone(cli_mod.parse_index_arg(bad), bad)

    def test_parse_screen_action(self):
        self.assertEqual(cli_mod.parse_screen_action(" OFF "), "off")
        self.assertEqual(cli_mod.parse_screen_action("on"), "on")
        self.assertIsNone(cli_mod.parse_screen_action("toggle"))


class CliDiagLinesTest(TempConfigCase):
    """诊断报告四个拼装函数：喂固定数据，断言逐字片段。"""

    def test_header_lines(self):
        cfg = {"presets": [0, 75, 100], "screen_off_method": "topology",
               "monitors": [1], "monitor_ids": ["DISPLAY1"],
               "monitor_anchors": [], "monitor_positions": {}}
        with mock.patch.object(cli_mod, "load_config", return_value=cfg), \
             mock.patch.object(cli_mod, "resolve_monitors", return_value=[1]), \
             mock.patch.object(cli_mod, "diag_enabled", return_value=False):
            got_cfg, lines = cli_mod._diag_header_lines()
        self.assertIs(got_cfg, cfg)
        joined = "\n".join(lines)
        self.assertIn("诊断报告", joined)
        self.assertIn("presets=[0, 75, 100]", joined)
        self.assertIn("锚点优先解析结果：[1]", joined)
        self.assertIn("（已关闭，用 --diag-log 看内容）", joined)

    def test_target_lines(self):
        targets = [{"handle": 7, "index": 0, "dev": r"\\.\DISPLAY1",
                    "w": 1920, "h": 1080, "primary": True, "desc": "物理屏"}]
        with mock.patch.object(cli_mod, "_read_vcp", return_value=(2,)), \
             mock.patch.object(cli_mod, "_capabilities", return_value=b"caps"), \
             mock.patch.object(cli_mod, "_caps_model", return_value="MODEL-X"), \
             mock.patch.object(cli_mod, "_caps_inputs", return_value=[1]), \
             mock.patch.object(cli_mod, "_caps_power", return_value=[2]):
            lines = cli_mod._diag_target_lines(targets)
        joined = "\n".join(lines)
        self.assertIn("共 1 个输出", joined)
        self.assertIn("主屏", joined)
        self.assertIn("电源 0xD6=2", joined)
        self.assertIn("MODEL-X", joined)

    def test_selection_lines(self):
        cfg = {"screen_off_method": "topology"}
        states = [{"index": 0, "dev": "DISPLAY1", "state": "off", "why": "0xD6=2"}]
        rows = [{"gdi": "DISPLAY1", "friendly": "主屏", "active": True,
                 "vdd": False, "anchor": "a1", "aliases": ["DISPLAY2"]}]
        with mock.patch.object(cli_mod, "resolve_monitors", return_value=[0]), \
             mock.patch.object(cli_mod, "probe_states", return_value=states), \
             mock.patch.object(cli_mod, "ccd_probe_topology", return_value=rows), \
             mock.patch.object(cli_mod, "topology_state_for",
                               return_value={"detail": "折叠正常"}):
            lines = cli_mod._diag_selection_lines(cfg)
        joined = "\n".join(lines)
        self.assertIn("resolve_monitors() -> [0]", joined)
        self.assertIn("亮灭判定", joined)
        self.assertIn("同源: DISPLAY2", joined)
        self.assertIn("折叠正常", joined)

    def test_selection_lines_without_ccd_rows(self):
        with mock.patch.object(cli_mod, "resolve_monitors", return_value=[]), \
             mock.patch.object(cli_mod, "probe_states", return_value=[]), \
             mock.patch.object(cli_mod, "ccd_probe_topology", return_value=[]), \
             mock.patch.object(cli_mod, "topology_state_for",
                               return_value={"detail": "无拓扑"}):
            joined = "\n".join(cli_mod._diag_selection_lines({}))
        self.assertIn("未取到 CCD 拓扑", joined)
        self.assertIn("[]（= 全部）", joined)

    def test_schedule_lines(self):
        cfg = {"enabled": True, "slots": []}
        with mock.patch.object(cli_mod, "query_task_status",
                               return_value="任务已注册\n下一跑 08:30"), \
             mock.patch.object(cli_mod, "query_task_runtime", return_value=""), \
             mock.patch.object(cli_mod, "task_match_reason", return_value="task.exe"), \
             mock.patch.object(cli_mod, "_task_action", return_value=("exe", "arg")):
            lines = cli_mod._diag_schedule_lines(cfg)
        joined = "\n".join(lines)
        self.assertIn("下一跑 08:30", joined)
        self.assertIn("task.exe", joined)
        self.assertIn("exe | arg", joined)


class CliExitCodeTest(TempConfigCase):
    """退出码是任务计划判断成败的唯一依据，必须逐条锁住。"""

    def test_cli_set_exit_codes(self):
        with mock.patch.object(cli_mod, "set_brightness_many",
                               return_value=(True, [])), \
             mock.patch.object(cli_mod, "_err"), \
             mock.patch.object(cli_mod.sys, "exit", side_effect=_exit):
            with self.assertRaises(SystemExit) as good:
                cli_mod.cli_set("0", "50")
            self.assertEqual(good.exception.code, 0)

        with mock.patch.object(cli_mod, "set_brightness_many") as setter, \
             mock.patch.object(cli_mod, "_err"), \
             mock.patch.object(cli_mod.sys, "exit", side_effect=_exit):
            with self.assertRaises(SystemExit) as bad:
                cli_mod.cli_set("0", "abc")
            self.assertEqual(bad.exception.code, 2)
            setter.assert_not_called()

    def test_apply_schedule_disabled_is_noop(self):
        with mock.patch.object(cli_mod, "load_config", return_value={"enabled": False}), \
             mock.patch.object(cli_mod, "_out") as out, \
             mock.patch.object(cli_mod.sys, "exit", side_effect=_exit):
            with self.assertRaises(SystemExit) as done:
                cli_mod.cli_apply_schedule()
        self.assertEqual(done.exception.code, 0)
        self.assertIn("定时已关闭", out.call_args[0][0])

    def test_selftest_is_hardware_free(self):
        cfg = {"slots": [{"time": "08:30", "level": 80, "screen": "", "days": []},
                         {"time": "21:00", "level": 0, "screen": "", "days": []}]}
        with mock.patch.object(cli_mod, "load_config", return_value=cfg), \
             mock.patch.object(cli_mod, "_out") as out, \
             mock.patch.object(cli_mod.sys, "exit", side_effect=_exit):
            with self.assertRaises(SystemExit) as done:
                cli_mod.cli_selftest()
        self.assertEqual(done.exception.code, 0)
        self.assertEqual(out.call_count, 11)   # 配置 + 当前档 + 9 个固定时刻
