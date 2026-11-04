"""不碰硬件的回归测试：python -m unittest discover -s tests -v

覆盖 v1.9.x 修的几处核心逻辑：
  - 配置原子写 / 坏配置备份与提示 / 熄灭方式默认 ddc
  - 时间解析与旧配置兼容
  - 启动自检只校验计划任务，绝不补执行、绝不熄屏（v1.9.4）
  - 计划任务参数拼装与落盘 XML 校验
  - 「添加时间段」点击后 300ms 内出现可见弹窗，并能在弹窗里完成一次添加（需要可用 Tk）
  - v1.9.2 任务重建成功：300ms 内可见「确认」窗，点确认销毁；失败/启动自愈不弹
"""
import glob
import html
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor_brightness as mb


class TempConfigCase(unittest.TestCase):
    """把 CONFIG_DIR / CONFIG_FILE / EVENTS_FILE 指到临时目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mb-test-")
        self._old = (mb.CONFIG_DIR, mb.CONFIG_FILE, mb.EVENTS_FILE, mb.CONFIG_LOAD_ERROR)
        mb.CONFIG_DIR = self.tmp
        mb.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        mb.EVENTS_FILE = os.path.join(self.tmp, "events.log")
        mb.CONFIG_LOAD_ERROR = ""

    def tearDown(self):
        mb.CONFIG_DIR, mb.CONFIG_FILE, mb.EVENTS_FILE, mb.CONFIG_LOAD_ERROR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class ConfigTest(TempConfigCase):
    def test_atomic_roundtrip_and_no_temp_left(self):
        cfg = mb.default_config()
        cfg["slots"] = [{"time": "08:30", "level": 80, "screen": "on"},
                        {"time": "21:00", "level": 0, "screen": "off"}]
        ok, msg = mb.save_config(cfg)
        self.assertTrue(ok, msg)
        self.assertFalse(glob.glob(os.path.join(self.tmp, ".tmp-*")),
                         "mkstemp 临时文件必须被 os.replace 吃掉")
        with open(mb.CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual([s["time"] for s in raw["slots"]], ["08:30", "21:00"])

        got = mb.load_config()
        self.assertEqual(got["slots"], cfg["slots"])
        self.assertEqual(mb.CONFIG_LOAD_ERROR, "")

    def test_bad_config_is_backed_up_and_reported(self):
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        cfg = mb.load_config()
        self.assertTrue(mb.CONFIG_LOAD_ERROR, "读坏必须显式记录，不能静默")
        self.assertIn("备份", mb.CONFIG_LOAD_ERROR)
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

    def test_default_off_method_is_ddc(self):
        """v1.9.3：默认熄灭方式恢复 v1.8.5 的 ddc。"""
        self.assertEqual(mb.default_config()["screen_off_method"], "ddc")

    def test_migrated_system_is_restored_to_ddc_with_log(self):
        """v1.9.3：撤销 v1.9.0 的 ddc→system 迁移；反向恢复必须落日志。"""
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 5, "screen_off_method": "ddc",
                       "ddc_allow_undeclared": False}, f)
        self.assertEqual(mb.load_config()["screen_off_method"], "ddc")

        # 被 v1.9.0 自动迁成 system 的旧配置：恢复为 ddc，并写事件日志
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 6, "screen_off_method": "system",
                       "ddc_allow_undeclared": False}, f)
        cfg = mb.load_config()
        self.assertEqual(cfg["screen_off_method"], "ddc")
        with open(mb.CONFIG_FILE, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["screen_off_method"], "ddc")
        self.assertIn("system → ddc", "\n".join(mb.read_recent_events(5)))

        # 主动选过 DDC（确认框会置 allow_undeclared=True）后改选 system 的，不动
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 6, "screen_off_method": "system",
                       "ddc_allow_undeclared": True}, f)
        self.assertEqual(mb.load_config()["screen_off_method"], "system")


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
        self.assertEqual(got[0], {"time": "09:05", "level": 80, "screen": "off"})
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


class BusyQueueTest(TempConfigCase):
    """v1.9.5：忙时点击/热键不再静默丢弃，改为提示 + 结束后补执行一次。"""

    class _Var:
        def __init__(self):
            self._v = ""

        def set(self, v):
            self._v = v

        def get(self):
            return self._v

    def test_busy_click_is_queued_with_visible_hint(self):
        fake = type("F", (), {})()
        fake._queue_screen_action = types.MethodType(mb.App._queue_screen_action, fake)
        fake.status_var = self._Var()
        fake._busy = True
        fake._busy_action = "on"          # 当前正在唤醒（约 4s 窗口）
        fake._pending_screen = None
        fake.selected_indices = lambda: [0]
        fake.run_bg = lambda *a, **k: self.fail("忙时不得启动新的屏幕任务")

        mb.App.screen_on(fake)
        text = fake.status_var.get()
        self.assertIn("正在唤醒中", text)
        self.assertIn("点亮请求已排队", text)
        self.assertEqual(fake._pending_screen, "on")

        with open(mb.EVENTS_FILE, "r", encoding="utf-8") as f:
            log = f.read()
        self.assertIn("忙时点亮请求已排队", log)

        # 连点两次只保留最后一次意图
        mb.App.screen_off(fake)
        self.assertEqual(fake._pending_screen, "off")
        self.assertIn("熄灭请求已排队", fake.status_var.get())

    def test_pending_is_replayed_once_after_busy_done(self):
        fake = type("F", (), {})()
        fake._busy = False
        fake._pending_screen = "on"
        fake.screen_on = lambda: calls.append("on")

        calls = []
        self.assertTrue(mb.App._dispatch_pending_screen(fake))
        self.assertEqual(calls, ["on"])
        self.assertIsNone(fake._pending_screen)
        self.assertFalse(mb.App._dispatch_pending_screen(fake), "只能补执行一次")
        self.assertEqual(calls, ["on"])

        with open(mb.EVENTS_FILE, "r", encoding="utf-8") as f:
            self.assertIn("补执行忙时排队的点亮请求", f.read())

    def test_poll_dispatches_pending_after_on_done(self):
        order = []
        fake = type("F", (), {})()
        fake._dispatch_pending_screen = types.MethodType(mb.App._dispatch_pending_screen, fake)
        fake._read_queue = __import__("queue").Queue()
        fake._bg_tasks = 1
        fake._busy = True
        fake._busy_action = "on"
        fake._pending_screen = "off"
        fake._hotkeys_on = False
        fake._relight_pending = False
        fake.root = type("R", (), {"after": lambda *a, **k: None})()
        fake._set_actions_enabled = lambda enabled: None
        fake.screen_off = lambda: order.append("replay-off")
        fake._read_queue.put(("done", (lambda r: order.append("on_done"), None, True), None))

        mb.App._poll_read_queue(fake)
        self.assertEqual(order, ["on_done", "replay-off"], "补执行必须在当前动作回调之后")
        self.assertFalse(fake._busy)
        self.assertIsNone(fake._busy_action)
        self.assertIsNone(fake._pending_screen)

    def test_idle_click_sets_busy_action_and_runs(self):
        seen = []
        fake = type("F", (), {})()
        fake.status_var = self._Var()
        fake._busy = False
        fake._busy_action = None
        fake.selected_indices = lambda: [0]
        fake._finish_screen_on = lambda result: None
        fake.run_bg = lambda job, on_done, busy: seen.append(busy)

        mb.App.screen_on(fake)
        self.assertEqual(fake._busy_action, "on")
        self.assertEqual(seen, [True])
        self.assertIn("正在点亮显示器", fake.status_var.get())


class WakeSelectionTest(TempConfigCase):
    """v1.9.6：点亮只作用于已勾选显示器；空勾选不发命令。"""

    def _targets(self):
        return [{"index": 0, "handle": 0xA0, "dev": "DISPLAY1", "desc": "A"},
                {"index": 1, "handle": 0xB0, "dev": "DISPLAY2", "desc": "B"}]

    def _patch(self):
        self._orig = (mb.enumerate_targets, mb.release_targets, mb._write_vcp,
                      mb._read_vcp, mb._nudge_user_input, mb._output_recycle,
                      mb._switch_input_roundtrip, mb.time.sleep)
        self.writes, self.switches, self.recycles = [], [], []
        mb.enumerate_targets = self._targets
        mb.release_targets = lambda ts: None
        mb._write_vcp = lambda h, c, v: (self.writes.append((h, c, v)), True)[1]
        mb._read_vcp = lambda h, c: (80, 100)
        mb._nudge_user_input = lambda: None
        mb._output_recycle = lambda: (self.recycles.append("recycle"), True)[1]
        mb._switch_input_roundtrip = lambda t: (self.switches.append(t["handle"]), True)[1]
        mb.time.sleep = lambda s: None

    def _restore(self):
        (mb.enumerate_targets, mb.release_targets, mb._write_vcp, mb._read_vcp,
         mb._nudge_user_input, mb._output_recycle, mb._switch_input_roundtrip,
         mb.time.sleep) = self._orig

    def test_subset_wake_writes_only_checked_monitor(self):
        self._patch()
        try:
            ok, errs = mb.screen_wake_many([0], allow_switch=True, expect_off=True)
        finally:
            self._restore()
        self.assertEqual([w[0] for w in self.writes], [0xA0, 0xA0],
                         "只勾 1 台时，另一台必须 0 次写调用")
        self.assertEqual(self.recycles, [], "子集时不得广播 SC_MONITORPOWER")
        self.assertEqual(self.switches, [0xA0])
        self.assertEqual(ok, [0])
        self.assertEqual(errs, [])

    def test_all_selected_keeps_recycle_behavior(self):
        self._patch()
        try:
            ok, errs = mb.screen_wake_many([0, 1], allow_switch=True, expect_off=True)
        finally:
            self._restore()
        self.assertEqual(sorted({w[0] for w in self.writes}), [0xA0, 0xB0])
        self.assertEqual(len(self.recycles), 2, "全选时每台仍走系统级重驱动（原行为）")
        self.assertEqual(sorted(ok), [0, 1])

    def test_empty_selection_shows_prompt_without_commands(self):
        fake = type("F", (), {})()
        fake.status_var = BusyQueueTest._Var()
        fake._busy = False
        fake.selected_indices = lambda: []
        fake.run_bg = lambda *a, **k: self.fail("空勾选不得发命令")

        mb.App.screen_on(fake)
        self.assertIn("请先勾选", fake.status_var.get())
        mb.App.screen_off(fake)
        self.assertIn("请先勾选", fake.status_var.get())


class FastFlipTest(TempConfigCase):
    """v1.9.7：0x60 切源翻转快速点亮（打桩，不发任何真实 DDC 命令）。"""

    def setUp(self):
        super().setUp()
        self._orig = (mb._read_vcp, mb._write_vcp, mb._capabilities_cached,
                      mb._display_key, mb.enumerate_targets, mb.release_targets,
                      mb.screen_on_cfg, mb.screen_wake_flip, mb._fast_flip_one,
                      mb._wake_one, mb._broadcast_monitor_power, mb.time.sleep,
                      dict(mb._FAST_FLIP_AT))
        mb._FAST_FLIP_AT.clear()
        self.sleeps = []
        mb.time.sleep = lambda s: self.sleeps.append(s)

    def tearDown(self):
        (mb._read_vcp, mb._write_vcp, mb._capabilities_cached, mb._display_key,
         mb.enumerate_targets, mb.release_targets, mb.screen_on_cfg,
         mb.screen_wake_flip, mb._fast_flip_one, mb._wake_one,
         mb._broadcast_monitor_power, mb.time.sleep, old_at) = self._orig
        mb._FAST_FLIP_AT.clear()
        mb._FAST_FLIP_AT.update(old_at)
        super().tearDown()

    def _targets(self):
        return [{"index": 0, "handle": 0xA0, "dev": "DISPLAY1", "desc": "A"},
                {"index": 1, "handle": 0xB0, "dev": "DISPLAY2", "desc": "B"}]

    def test_flip_sequence_has_no_wait(self):
        writes = []
        mb._read_vcp = lambda h, c: (15, 100) if c == mb.VCP_INPUT_SOURCE else None
        mb._write_vcp = lambda h, c, v: (writes.append((h, c, v)), True)[1]
        mb._capabilities_cached = lambda h, k: "60(11 12 0F)"
        mb._display_key = lambda t: t["dev"]

        ok, note = mb._fast_flip_one({"index": 0, "handle": 0xA0, "dev": "DISPLAY1"})
        self.assertTrue(ok, note)
        self.assertEqual(writes, [(0xA0, mb.VCP_INPUT_SOURCE, 17),
                                 (0xA0, mb.VCP_INPUT_SOURCE, 15)],
                         "必须 读 0x60 → 写中间源 → 立即写回原源")
        self.assertEqual(self.sleeps, [], "快速路径不得有任何 sleep")
        self.assertIn("翻转 0x60 15→17→15", note)
        self.assertIn("耗时", note)

    def test_mid_source_when_current_is_17(self):
        self.assertEqual(mb.pick_flip_source(15, [17, 18, 15]), 17)
        self.assertEqual(mb.pick_flip_source(17, [17, 18, 15]), 15,
                         "当前就是 17 时必须换一个声明过的口")
        self.assertEqual(mb.pick_flip_source(17, [17, 18]), 18)
        self.assertEqual(mb.pick_flip_source(15, []), 17, "能力串读不到时用通用候选")
        self.assertIsNone(mb.pick_flip_source(17, [17]))

    def test_second_trigger_within_8s_escalates(self):
        mb._mark_fast_flip("DISPLAY1#x", 100.0)
        self.assertTrue(mb._fast_flip_recent("DISPLAY1#x", 103.0), "8 秒内算第二次")
        self.assertFalse(mb._fast_flip_recent("DISPLAY1#x", 120.0), "超过窗口不算")
        self.assertFalse(mb._fast_flip_recent("DISPLAY2#x", 103.0), "另一台不受影响")

    def test_second_trigger_uses_full_ladder_and_says_so(self):
        ladder = []
        mb.enumerate_targets = self._targets
        mb.release_targets = lambda ts: None
        mb._display_key = lambda t: t["dev"]
        mb._fast_flip_one = lambda t: (True, "翻转 0x60 15→17→15，耗时 0.0 ms")
        mb._wake_one = lambda t, a, e, r: (ladder.append((t["dev"], e)),
                                          (True, 4, "x"))[1]

        mb.screen_wake_flip([0])
        # 第一次成功后 GUI 会把 screen_state 记成 on，第二次点击 expect_off=False；
        # 但 8 秒内二击仍必须升级为完整阶梯（用户点第二次=第一次没亮）。
        mb.screen_wake_flip([0], expect_off=False)
        self.assertEqual(ladder, [("DISPLAY1", True)],
                         "第二次必须以 expect_off=True 走完整 _wake_one 阶梯")
        self.assertIn("已升级为完整点亮", "；".join(mb.take_wake_info()))

    def test_fast_flip_never_broadcasts(self):
        broadcast = []
        mb.enumerate_targets = self._targets
        mb.release_targets = lambda ts: None
        mb._display_key = lambda t: t["dev"]
        mb._broadcast_monitor_power = lambda flag: (broadcast.append(flag), True)[1]
        mb._fast_flip_one = lambda t: (True, "翻转 0x60 15→17→15，耗时 0.0 ms")

        ok, errs = mb.screen_wake_flip([0, 1])
        self.assertEqual(sorted(ok), [0, 1])
        self.assertEqual(errs, [])
        self.assertEqual(broadcast, [], "快速路径绝不发 SC_MONITORPOWER 广播")

    def test_switch_off_falls_back_to_v196_ladder(self):
        cfg = mb.default_config()
        cfg["wake_fast_flip"] = False
        cfg["screen_state"] = "off"
        calls = []
        mb.screen_on_cfg = lambda c, i: (calls.append("ladder"), ([0], []))[1]
        mb.screen_wake_flip = lambda *a, **k: self.fail("关掉开关不得走快速路径")

        ok, errs = mb.screen_wake_entry(cfg, [0])
        self.assertEqual(calls, ["ladder"])
        self.assertEqual(ok, [0])
        self.assertEqual(errs, [])

    def test_fast_path_gate_needs_ddc_and_switch_allowed(self):
        cfg = mb.default_config()
        self.assertEqual(cfg["screen_off_method"], "ddc")
        self.assertTrue(cfg["wake_fast_flip"])

        calls = []
        mb.screen_wake_flip = lambda *a, **k: (calls.append("flip"), ([0], []))[1]
        mb.screen_on_cfg = lambda c, i: (calls.append("ladder"), ([0], []))[1]

        cfg["screen_state"] = "on"                      # 屏亮着也进路由器（内部不翻转）
        mb.screen_wake_entry(cfg, [0])
        cfg["screen_state"] = "off"
        cfg["wake_switch_input"] = False                # 用户禁用切源：回旧路径
        mb.screen_wake_entry(cfg, [0])
        cfg["wake_switch_input"] = True
        cfg["screen_off_method"] = "system"             # 系统级点亮本来就是即时广播
        mb.screen_wake_entry(cfg, [0])
        cfg["screen_off_method"] = "ddc"
        mb.screen_wake_entry(cfg, [0])
        self.assertEqual(calls, ["flip", "ladder", "ladder", "flip"])

    def test_lit_screen_without_recent_flip_keeps_old_behavior(self):
        calls = []
        mb.enumerate_targets = self._targets
        mb.release_targets = lambda ts: None
        mb._display_key = lambda t: t["dev"]
        mb._fast_flip_one = lambda t: self.fail("屏亮着且无近期记录时不得翻转")
        mb._wake_one = lambda t, a, e, r: (calls.append((t["dev"], e, r)),
                                          (True, 4, "x"))[1]

        mb.screen_wake_flip([0, 1], expect_off=False)
        self.assertEqual(sorted(calls),
                         [("DISPLAY1", False, True), ("DISPLAY2", False, True)],
                         "必须原样按 expect_off=False 走 _wake_one，不翻转")

    def test_old_config_without_key_defaults_to_enabled(self):
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 6, "screen_off_method": "ddc"}, f)
        cfg = mb.load_config()
        self.assertIs(cfg["wake_fast_flip"], True,
                      "旧配置缺 wake_fast_flip 必须按默认 1 处理")
        self.assertEqual(mb.default_config()["wake_fast_flip"], True)


class TaskTest(TempConfigCase):
    def _xml(self, cfg, command=None, arguments=None, times=None):
        exe, arg = mb._task_action(cfg)
        command = command if command is not None else exe
        arguments = arguments if arguments is not None else arg
        times = times or [s["time"] for s in cfg["slots"]]
        trig = "".join(
            "<CalendarTrigger><StartBoundary>2026-11-04T%s:00</StartBoundary>"
            "<Enabled>true</Enabled></CalendarTrigger>" % t for t in times)
        return (
            '<?xml version="1.0" encoding="UTF-16"?>'
            "<Task><Actions Context=\"Author\"><Exec>"
            "<Command>%s</Command><Arguments>%s</Arguments>"
            "</Exec></Actions>"
            "<Triggers>%s</Triggers>"
            "<Settings><Enabled>true</Enabled><StartWhenAvailable>true</StartWhenAvailable>"
            "</Settings></Task>" % (html.escape(command), html.escape(arguments), trig))

    def test_action_and_signature(self):
        cfg = mb.default_config()
        exe, arg = mb._task_action(cfg)
        self.assertTrue(exe)
        self.assertIn("--apply-schedule", arg)
        sig = mb.task_signature(cfg)
        self.assertIn(os.path.normcase(os.path.abspath(exe)), sig)

    def test_definition_parses_quoted_arguments(self):
        xml = ('<Actions><Exec><Command>C:\\Program Files\\x.exe</Command>'
               '<Arguments>&quot;D:\\a b\\s.py&quot; --apply-schedule</Arguments>'
               "</Exec></Actions>")
        cmd, args = mb._task_definition(xml)
        self.assertEqual(cmd, "C:\\Program Files\\x.exe")
        self.assertEqual(args, '"D:\\a b\\s.py" --apply-schedule')

    def test_match_ok_and_mismatches(self):
        cfg = mb.default_config()
        cfg["enabled"] = True
        xml = self._xml(cfg)
        self.assertEqual(mb.task_match_reason(cfg, xml), "")

        bad_path = self._xml(cfg, command=r"D:\old\MonitorBrightness.exe")
        self.assertIn("程序路径不一致", mb.task_match_reason(cfg, bad_path))

        bad_arg = self._xml(cfg, arguments="--minimized")
        self.assertIn("启动参数不一致", mb.task_match_reason(cfg, bad_arg))

        bad_time = self._xml(cfg, times=["09:15"])
        self.assertIn("触发时间不一致", mb.task_match_reason(cfg, bad_time))

        no_task = ""
        self.assertIn("未注册", mb.task_match_reason(cfg, no_task))

    def test_summary_shows_last_result(self):
        text = ("MonitorBrightness_Schedule=Ready|2026-11-04 13:30|SWA=True"
                "|TRIG=%d,%d" % (510, 1260))
        runtime = "RUNTIME=NEVER|-2147483645|2026-11-04 13:30|Ready"
        info = mb.task_summary(text, [{"time": "08:30"}, {"time": "21:00"}], runtime)
        self.assertIn("从未运行", info["detail"])
        self.assertEqual(info["next"], "下次 11-04 13:30")

        runtime2 = "RUNTIME=2026-11-04 08:30|0|2026-11-04 21:00|Ready"
        info2 = mb.task_summary(text, [{"time": "08:30"}, {"time": "21:00"}], runtime2)
        self.assertIn("上次 11-04 08:30（成功）", info2["detail"])
        self.assertEqual(info2["next"], "下次 11-04 21:00")


class AddButtonRegression(TempConfigCase):
    """点击「＋ 添加时间段」→ 300ms 内出现可见弹窗 → 在弹窗里完成一次添加。

    用隐藏的 Tk 构建真实的 SchedulePanel 和真实 SlotEditor，全程不显示窗口、
    不碰显示器。v1.9.0 的教训：只断言列表 +1 不够，必须断言弹窗真的可见。
    """

    def setUp(self):
        super().setUp()
        try:
            import tkinter as tk
        except Exception as e:               # pragma: no cover
            self.skipTest("没有 tkinter：%s" % e)
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as e:               # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        super().tearDown()

    def test_add_click_opens_visible_dialog_and_saves(self):
        import tkinter as tk

        cfg = mb.default_config()
        cfg["enabled"] = False
        cfg["slots"] = [{"time": "08:30", "level": 80, "screen": ""}]
        written = {}

        class Msg:
            def showerror(self, *a): raise AssertionError("不该报错：%s" % (a,))
            def showinfo(self, *a): pass

        class FakeApp:
            msg = Msg()

        fake = FakeApp()
        fake.root = self.root
        fake.body = tk.Frame(self.root, bg=mb.UI["bg"])
        fake.body.pack(fill="both", expand=True)
        fake.status_var = tk.StringVar()
        fake.slots_saved = []
        fake.refresh_task_status = lambda: None
        fake.schedule_toggled = lambda value: None
        fake.apply_schedule = lambda: None

        def on_slots_changed(slots):
            fake.slots_saved = list(slots)
            c = mb.default_config()
            c["slots"] = list(slots)
            ok, msg = mb.save_config(c)
            written["ok"], written["msg"] = ok, msg

        fake.on_slots_changed = on_slots_changed
        panel = mb.SchedulePanel(fake, cfg)
        self.root.update()
        self.assertTrue(panel.add_btn._enabled,
                        "定时未启用时「添加」也必须可点（v1.8.5 的回归）")
        # 按钮真正绑到 add_row，不是绑到别的空 handler
        self.assertIs(panel.add_btn._command.__func__, mb.SchedulePanel.add_row)

        self.assertEqual(len(panel.slots), 1)
        panel.add_btn._command()              # 等价于一次完整点击的最终出口

        dialog = None
        deadline = time.time() + 0.3
        while time.time() < deadline:
            self.root.update()
            for w in self.root.winfo_children():
                if isinstance(w, tk.Toplevel) and w.winfo_exists():
                    dialog = w
                    if w.winfo_ismapped() and w.winfo_width() > 0:
                        break
            if dialog is not None and dialog.winfo_ismapped():
                break
            time.sleep(0.02)
        self.assertIsNotNone(dialog, "点击后 300ms 内没有创建 Toplevel 弹窗")
        self.assertTrue(dialog.winfo_ismapped(), "弹窗存在但不可见")
        self.assertGreater(dialog.winfo_width(), 0, "弹窗宽度为 0")
        self.assertGreater(dialog.winfo_height(), 0, "弹窗高度为 0")

        editor = panel._editor
        self.assertIsNotNone(editor, "弹窗没有挂到 panel._editor")
        editor.time_field.set_time("07:15")
        editor.level_spin.set(40)
        editor.ok_btn._command()              # 弹窗里的「保存」
        self.root.update()

        self.assertEqual(len(panel.slots), 2, "保存后列表必须多一条")
        self.assertIn("07:15", [s["time"] for s in panel.slots])
        self.assertFalse(dialog.winfo_exists(), "保存后弹窗应关闭")
        self.assertTrue(written.get("ok"), written.get("msg"))
        disk = mb.load_config()
        self.assertEqual(len(disk["slots"]), 2, "必须落盘")
        self.assertIn("07:15", [s["time"] for s in disk["slots"]])


class RebuildNoticeRegression(TempConfigCase):
    """v1.9.2：重建成功 → 300ms 内出现可见「确认」窗 → 点确认销毁。

    失败路径不得弹成功窗（只走 showerror）；启动自动重建也不弹（状态/事件反馈）。
    """

    def setUp(self):
        super().setUp()
        try:
            import tkinter as tk
        except Exception as e:               # pragma: no cover
            self.skipTest("没有 tkinter：%s" % e)
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as e:               # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        super().tearDown()

    def _fake(self):
        import tkinter as tk

        class Msg:
            def __init__(self, root):
                self.box = mb.DarkBox(root)
                self.errors = []
                self.notices = []

            def showerror(self, title, message):
                self.errors.append((title, message))

            def showinfo(self, *a):
                pass

            def shownotice(self, title, message):
                self.notices.append((title, message))
                return self.box.shownotice(title, message)

        class Panel:
            def set_enabled_flag(self, value):
                self.flag = bool(value)

        class FakeApp:
            pass

        cfg = mb.default_config()
        cfg["enabled"] = True
        cfg["slots"] = mb._coerce_slots([
            {"time": "08:30", "level": 80},
            {"time": "21:00", "level": 0},
        ])
        fake = FakeApp()
        fake.root = self.root
        fake.msg = Msg(self.root)
        fake.status_var = tk.StringVar()
        fake.cfg = cfg
        fake.sch_panel = Panel()
        fake.refresh_task_status = lambda: None
        fake._refresh_events = lambda: None
        fake.run_bg = lambda *a, **k: None
        fake._finish_auto_checks = lambda result: None
        fake._announce_rebuilt = lambda *a: mb.App._announce_rebuilt(fake, *a)
        return fake, cfg

    @staticmethod
    def _walk(widget):
        for child in widget.winfo_children():
            yield child
            for sub in RebuildNoticeRegression._walk(child):
                yield sub

    def _top_levels(self):
        import tkinter as tk
        return [w for w in self.root.winfo_children()
                if isinstance(w, tk.Toplevel) and w.winfo_exists()]

    def _stale_xml(self):
        return ('<Task><Actions><Exec><Command>D:\\old\\MonitorBrightness.exe</Command>'
                '<Arguments>--apply-schedule</Arguments></Exec></Actions>'
                '<Triggers><CalendarTrigger>'
                '<StartBoundary>2026-11-04T09:15:00</StartBoundary>'
                '</CalendarTrigger></Triggers>'
                '<Settings><Enabled>true</Enabled>'
                '<StartWhenAvailable>true</StartWhenAvailable></Settings></Task>')

    def test_success_notice_visible_then_confirm_destroys(self):
        fake, cfg = self._fake()
        part = {"slots": cfg["slots"], "enabled": True, "catch_up_on_logon": True}
        out = "TaskInfo=NEVER|0|2026-11-04 21:00|Ready\nCatchUpTask=Ready\nREGISTER_OK"

        mb.App._finish_register(fake, (True, out), part, self._stale_xml())

        dialog = None
        deadline = time.time() + 0.3
        while time.time() < deadline:
            self.root.update()
            tops = self._top_levels()
            if tops:
                dialog = tops[-1]
                if dialog.winfo_ismapped() and dialog.winfo_width() > 0:
                    break
            time.sleep(0.01)
        self.assertIsNotNone(dialog, "重建成功后 300ms 内没有创建确认 Toplevel")
        self.assertTrue(dialog.winfo_ismapped(), "确认窗存在但不可见")
        self.assertGreater(dialog.winfo_width(), 0, "确认窗宽度为 0")

        labels = [w.cget("text") for w in self._walk(dialog)
                  if isinstance(w, mb.tk.Label)]
        text = "\n".join(str(x) for x in labels)
        self.assertIn("2 档", text)
        self.assertIn("08:30", text)
        self.assertIn("下次运行：11-04 21:00", text)
        self.assertIn("已更新任务路径/触发点", text, "路径+触发点都变了必须写明")

        confirm = [w for w in self._walk(dialog)
                   if isinstance(w, mb.Button) and w.text() == "确认"]
        self.assertEqual(len(confirm), 1, "确认窗里必须只有一个「确认」按钮")
        confirm[0]._command()                 # 等价于一次点击
        self.root.update()
        self.assertFalse(dialog.winfo_exists(), "点「确认」后弹窗必须销毁")
        self.assertEqual(fake.msg.errors, [])

    def test_failure_does_not_show_success_notice(self):
        fake, cfg = self._fake()
        part = {"slots": cfg["slots"], "enabled": True, "catch_up_on_logon": True}

        mb.App._finish_register(fake, (False, "注册子进程退出码 1"), part, "")

        self.root.update()
        self.assertEqual(self._top_levels(), [], "失败时不能弹成功确认窗")
        self.assertEqual(len(fake.msg.errors), 1)
        self.assertFalse(cfg["enabled"], "注册失败要把开关拨回去")
        self.assertFalse(fake.sch_panel.flag)

    def test_startup_auto_rebuild_does_not_pop_notice(self):
        fake, _cfg = self._fake()
        mb.App._finish_auto_checks(fake, {
            "task": (True, "计划任务已按当前程序重建（程序路径不一致），2 档，下次 11-04 21:00"),
        })
        self.root.update()
        self.assertEqual(self._top_levels(), [], "启动自动重建成功不应弹窗")
        self.assertIn("重建", fake.status_var.get())


if __name__ == "__main__":
    unittest.main()
