# -*- coding: utf-8 -*-
"""P1-8 拆分出的测试用例；由 tests/test_core.py 聚合运行。"""

from tests.support import (  # noqa: F401
    _FakeU32, _PATCH_MODULES, _TopoFixture, _UNSET, _core_state,
    _row_button_texts,
    TempConfigCase, cfg_mod, glob, json, mb, os, unittest,
)


class ConfigTest(TempConfigCase):
    def test_atomic_roundtrip_and_no_temp_left(self):
        cfg = mb.default_config()
        cfg["slots"] = [{"time": "08:30", "level": 80, "screen": "on", "days": []},
                        {"time": "21:00", "level": 0, "screen": "off", "days": []}]
        ok, msg = mb.save_config(cfg)
        self.assertTrue(ok, msg)
        self.assertFalse(glob.glob(os.path.join(self.tmp, ".tmp-*")),
                         "mkstemp 临时文件必须被 os.replace 吃掉")
        with open(mb.CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual([s["time"] for s in raw["slots"]], ["08:30", "21:00"])

        got = mb.load_config()
        self.assertEqual(got["slots"], cfg["slots"])
        self.assertEqual(cfg_mod.CONFIG_LOAD_ERROR, "")

    def test_bad_config_is_backed_up_and_reported(self):
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        cfg = mb.load_config()
        self.assertTrue(cfg_mod.CONFIG_LOAD_ERROR, "读坏必须显式记录，不能静默")
        self.assertIn("备份", cfg_mod.CONFIG_LOAD_ERROR)
        self.assertEqual(cfg["slots"], mb._coerce_slots(None))
        self.assertTrue(glob.glob(mb.CONFIG_FILE + ".bad-*.json"),
                        "坏配置必须留副本，避免下一次保存覆盖掉用户原文件")

    def test_legacy_day_night_migration(self):
        raw = {"version": 2, "day_time": "09:00", "day_level": 80,
               "night_time": "20:00", "night_level": 30}
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        cfg = mb.load_config()
        self.assertEqual([s["time"] for s in cfg["slots"]], ["09:00", "20:00"])
        self.assertEqual([s["level"] for s in cfg["slots"]], [80, 30])

    def test_topology_only_config_schema(self):
        """v2.04：熄灭方式固定拓扑；旧方式键被丢弃，不再迁移也不再写回。"""
        cfg = mb.default_config()
        self.assertNotIn("screen_off_method", cfg)
        self.assertNotIn("ddc_allow_undeclared", cfg)
        self.assertNotIn("wake_fast_flip", cfg)
        self.assertNotIn("wake_switch_input", cfg)

        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 6, "screen_off_method": "system",
                       "ddc_allow_undeclared": True, "wake_fast_flip": False}, f)
        loaded = mb.load_config()
        for key in ("screen_off_method", "ddc_allow_undeclared",
                    "wake_fast_flip", "wake_switch_input"):
            self.assertNotIn(key, loaded)
        with open(mb.CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.assertIn("screen_off_method", raw)   # 不回写，原文件保持不动


class TimeTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(mb.normalize_time_input("9:5"), "09:05")
        self.assertEqual(mb.normalize_time_input("09：05"), "09:05")
        self.assertEqual(mb.normalize_time_input("0905"), "09:05")
        self.assertEqual(mb.normalize_time_input("23:59"), "23:59")
        self.assertIsNone(mb.normalize_time_input("24:00"))
        self.assertIsNone(mb.normalize_time_input("9:60"))
        self.assertIsNone(mb.normalize_time_input("abc"))

    def test_shift_wraps_midnight(self):
        self.assertEqual(mb.shift_time("23:50", 15), "00:05")
        self.assertEqual(mb.shift_time("00:05", -15), "23:50")

    def test_slots_coerce_forward_compatible(self):
        got = mb._coerce_slots([
            {"time": "9:5", "level": "80", "screen": "OFF"},
            {"time": "09:05", "level": 30},          # 同一分钟的不同写法 -> 去重
            {"time": "bad", "level": 10},
            {"time": "21:00", "level": 200, "screen": "auto"},
        ])
        self.assertEqual([s["time"] for s in got], ["09:05", "21:00"])
        self.assertEqual(got[0], {"time": "09:05", "level": 80, "screen": "off",
                                  "days": []})
        self.assertEqual(got[1]["level"], 100)
        self.assertEqual(got[1]["screen"], "")


class StartupCheckTest(TempConfigCase):
    """v1.9.4：启动自检只校验任务配置，绝不补执行、绝不熄屏。"""

    def make_cfg(self, slots=None):
        cfg = mb.default_config()
        cfg["enabled"] = True
        cfg["slots"] = mb._coerce_slots(slots or [
            {"time": "08:30", "level": 80},
            {"time": "21:00", "level": 0},
        ])
        return cfg

    def test_no_in_app_catch_up_api(self):
        for name in ("catch_up_decision", "_catch_up_worker", "apply_slot",
                     "mark_fired", "CATCH_UP_LIMIT_MIN"):
            self.assertFalse(hasattr(mb, name), "%s 必须被删除" % name)

    def test_consistent_task_returns_without_rebuild(self):
        calls = []
        old_match, old_reg = mb.task_match_reason, mb.register_tasks
        mb.task_match_reason = lambda cfg: ""
        mb.register_tasks = lambda cfg: calls.append(1) or (True, "")
        try:
            ok, msg = mb.ensure_task_registration(self.make_cfg())
        finally:
            mb.task_match_reason, mb.register_tasks = old_match, old_reg
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(calls, [], "配置一致时不得重建任务")
        # 启动自检链路里不能出现任何执行档位/熄屏的函数
        self.assertNotIn("screen_off", mb.App._start_auto_checks.__code__.co_names)

    def test_finish_auto_checks_reports_ready_only(self):
        class Fake:
            status_var = None
            cfg = None

        fake = Fake()
        import tkinter as tk
        try:
            probe = tk.Tk()
            probe.withdraw()
        except Exception as e:               # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            fake.status_var = tk.StringVar()
            fake.cfg = self.make_cfg()
            fake._refresh_events = lambda: None
            fake.refresh_task_status = lambda: None
            mb.App._finish_auto_checks(fake, {"task": (True, "")})
            text = fake.status_var.get()
        finally:
            probe.destroy()
        self.assertIn("定时任务已就绪", text)
        self.assertIn("下次运行", text)


class DiagLogTest(TempConfigCase):
    """诊断日志（临时模块 v2.01.4）：模块删掉时这组测试一起删。"""

    def tearDown(self):
        mb.diag_set_enabled(False)      # 默认关闭，别让其它用例往日志里写
        super().tearDown()

    def test_writes_one_json_line_with_tag_and_fields(self):
        mb.diag_set_enabled(True)
        mb.dlog("topo.on", anchors=["A2"], pc=233)
        lines = mb.diag_dump()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["tag"], "topo.on")
        self.assertEqual(rec["anchors"], ["A2"])
        self.assertEqual(rec["pc"], 233)
        for key in ("t", "pid", "th"):
            self.assertIn(key, rec)

    def test_disabled_writes_nothing_at_all(self):
        mb.diag_set_enabled(False)
        mb.dlog("topo.on", a=1)
        self.assertEqual(mb.diag_dump(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "diag.log")))

    def test_long_value_is_truncated(self):
        mb.diag_set_enabled(True)
        mb.dlog("x", big="a" * 2000)
        rec = json.loads(mb.diag_dump()[0])
        self.assertLessEqual(len(rec["big"]), 601)

    def test_path_follows_config_dir(self):
        """CONFIG_DIR 会被测试/便携模式改，路径必须现算，不能导入时定死。"""
        self.assertEqual(mb._diag_file(), os.path.join(self.tmp, "diag.log"))

    def test_load_config_syncs_the_switch(self):
        cfg = mb.default_config()
        cfg["diag_log"] = False
        mb.save_config(cfg)
        mb.load_config()
        self.assertFalse(mb.diag_enabled())

    def test_rotation_keeps_one_old_file(self):
        mb.diag_set_enabled(True)
        path = os.path.join(self.tmp, "diag.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * (mb._DIAG_LIMIT + 10))
        mb.dlog("after.rotate")
        self.assertTrue(os.path.exists(path + ".1"))
        self.assertEqual(json.loads(mb.diag_dump()[0])["tag"], "after.rotate")


class DiagExportTest(TempConfigCase):
    """日志跟程序同目录 + 一键导出诊断包（v2.01.5）。"""

    def tearDown(self):
        mb.diag_set_enabled(False)
        super().tearDown()

    def test_log_dir_is_app_dir_when_writable(self):
        self.assertEqual(mb._pick_log_dir(), mb.APP_DIR)

    def test_log_dir_falls_back_to_config_dir_when_readonly(self):
        """v2.03.0 U5：_pick_log_dir 搬到 core/paths.py 后，它在本模块内查找
        _dir_writable，所以要 patch 那里 —— patch 主文件的名字不再生效。"""
        from core import paths as _paths
        with unittest.mock.patch.object(_paths, "_dir_writable", return_value=False):
            self.assertEqual(mb._pick_log_dir(), _paths.CONFIG_DIR)

    def test_export_zip_contains_logs_and_env(self):
        import zipfile
        mb.diag_set_enabled(True)
        mb.dlog("demo", a=1)
        mb._write_event_log("事件一行")
        path, err = mb.diag_export()
        self.assertEqual(err, "")
        self.assertTrue(path.endswith(".zip"))
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        self.assertIn("diag.log", names)
        self.assertIn("events.log", names)
        self.assertIn("环境.txt", names)

    def test_export_reports_error_instead_of_raising(self):
        old = mb.zipfile.ZipFile
        mb.zipfile.ZipFile = lambda *a, **k: (_ for _ in ()).throw(OSError("磁盘满"))
        try:
            path, err = mb.diag_export()
        finally:
            mb.zipfile.ZipFile = old
        self.assertEqual(path, "")
        self.assertIn("磁盘满", err)


class SessionEventsTest(TempConfigCase):
    """界面只显示**本次运行**的事件（v2.01.6）。

    用户反馈：界面底部一直挂着 16:29 / 16:39 那几条「source mode 已不在
    modes 数组里」，以为程序一直在报错。那其实是一次正常接回的实现细节，
    被写进了 events.log，而界面又把整个文件的历史读出来显示。
    """

    def setUp(self):
        super().setUp()
        mb._SESSION_EVENTS.clear()

    def tearDown(self):
        mb._SESSION_EVENTS.clear()
        super().tearDown()

    def test_history_file_is_not_shown_in_ui(self):
        with open(mb.EVENTS_FILE, "w", encoding="utf-8") as f:
            f.write("01-01 08:00 很久以前的事件\n")
        self.assertEqual(mb.read_session_events(), [])       # 界面什么都拿不到
        mb._write_event_log("本次事件")
        got = mb.read_session_events()
        self.assertEqual(len(got), 1)
        self.assertIn("本次事件", got[0])
        # 文件里仍是两行（历史 + 本次），但界面只拿到本次这一条
        self.assertEqual(len(mb.read_recent_events(10)), 2)

    def test_session_events_are_capped(self):
        for i in range(60):
            mb._write_event_log("事件 %d" % i)
        got = mb.read_session_events(3)
        self.assertEqual(len(got), 3)
        self.assertIn("事件 59", got[-1])

    def test_state_compat_reads_follow_accessors(self):
        """R6：旧模块名只读转发，且可变容器保持同一对象身份。"""
        _core_state.set_load_error("boom")
        self.assertEqual(mb.CONFIG_LOAD_ERROR, "boom")
        self.assertEqual(cfg_mod.CONFIG_LOAD_ERROR, "boom")
        self.assertIs(mb._CAPS_CACHE, _core_state.caps_cache())
        self.assertIs(cfg_mod._CAPS_CACHE, _core_state.caps_cache())
        self.assertIs(mb._SESSION_EVENTS, _core_state.session_events())
