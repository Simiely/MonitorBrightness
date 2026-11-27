"""不碰硬件的回归测试：python -m unittest discover -s tests -v

覆盖 v1.9.x 修的几处核心逻辑：
  - 配置原子写 / 坏配置备份与提示 / 熄灭方式默认 ddc
  - 时间解析与旧配置兼容
  - 启动自检只校验计划任务，绝不补执行、绝不熄屏（v1.9.4）
  - 计划任务参数拼装与落盘 XML 校验
  - 「添加时间段」点击后立即创建弹窗、跑事件循环即可见，并能在弹窗里完成一次添加
  - v1.9.2 任务重建成功：经 after(0) 创建的「确认」窗在下一轮事件可见，点确认销毁；失败/启动自愈不弹
"""
import contextlib
import ctypes
import glob
import html
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import unittest.mock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.config as cfg_mod
import core.logging as log_mod
import domain.slots as slots_mod
import hw.ccd as hw_ccd
import hw.ccd_structs as hw_structs
import hw.ddc as hw_ddc
import hw.geometry as hw_geometry
import hw.power as hw_power
import hw.topology as hw_topology
import hw.winfollow as hw_winfollow
import monitor_brightness as mb
import schedule as schedule_mod
import schedule_report as sched_report_mod
import service as svc_mod

# R2：同一个函数可能同时绑在主文件与多个 hw/service 模块里。测试 patch 必须
# 覆盖**调用方所在的那个绑定**，否则 mock 根本不会被调用到。这里用同一个
# mock 实例同时 patch 所有持有该名字的模块，`as x` 拿到的仍是这个实例。
_PATCH_MODULES = (mb, cfg_mod, log_mod, slots_mod, hw_ddc, hw_structs, hw_ccd,
                  hw_topology, hw_geometry, hw_power, hw_winfollow, svc_mod,
                  schedule_mod, sched_report_mod)


_UNSET = object()


class _PatchAll:
    def __init__(self, name, new, kwargs):
        self.mock = new if new is not _UNSET else unittest.mock.MagicMock(**kwargs)
        self.stack = contextlib.ExitStack()
        for mod in _PATCH_MODULES:
            if hasattr(mod, name):
                self.stack.enter_context(
                    unittest.mock.patch.object(mod, name, new=self.mock))

    def __enter__(self):
        self.stack.__enter__()
        return self.mock

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def patch_any(name, new=_UNSET, **kwargs):
    return _PatchAll(name, new, kwargs)


def set_any(name, value):
    """直接赋值版打桩：把 value 写到所有持有 name 的模块，返回恢复记录。"""
    saved = []
    for mod in _PATCH_MODULES:
        if hasattr(mod, name):
            saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
    return saved


def restore_any(saved):
    for mod, name, old in saved:
        setattr(mod, name, old)

_MODULE_TMP = None


def setUpModule():
    """整份测试都不许写用户的真实配置 / 日志目录。

    有些用例类（TopologyPlanOnTest 等）直接继承 unittest.TestCase，不经过
    TempConfigCase；它们一旦触发 dlog / _write_event_log，就会写到
    %APPDATA%\\MonitorBrightness 去 —— 实测把单元测试的假数据写进了真实
    diag.log。这里给整份测试兜一个临时目录。
    """
    global _MODULE_TMP
    _MODULE_TMP = tempfile.mkdtemp(prefix="mb-test-module-")
    _MODULE_LEGACY = os.path.join(_MODULE_TMP, "legacy")
    # R1：配置/日志函数已沉到 core/，必须把路径同时指向临时目录，
    # 否则它们读的是自己模块里 import 时的真实 APPDATA 路径。
    mb.CONFIG_DIR = cfg_mod.CONFIG_DIR = _MODULE_TMP
    mb.CONFIG_FILE = cfg_mod.CONFIG_FILE = os.path.join(_MODULE_TMP, "config.json")
    mb.LOG_DIR = log_mod.LOG_DIR = _MODULE_TMP
    mb.EVENTS_FILE = log_mod.EVENTS_FILE = os.path.join(_MODULE_TMP, "events.log")
    # v2.01.20：save_config 会把配置镜像到 APPDATA，模块级也必须隔离，
    # 否则任何没继承 TempConfigCase 的用例都会写进用户真实配置。
    mb._LEGACY_CONFIG_DIR = cfg_mod._LEGACY_CONFIG_DIR = _MODULE_LEGACY


def tearDownModule():
    shutil.rmtree(_MODULE_TMP, ignore_errors=True)


def pump_ui(root, rounds=2):
    """跑固定轮数的 Tk 事件循环，把 UI 回归用例从墙钟时间上摘下来。

    实测（2026-11-27）：两个回归场景的弹窗都不需要等待真实时间——
      1. `SchedulePanel.add_row` 在点击调用内**同步**创建 Toplevel，1 次 update 即 mapped；
      2. `_announce_rebuilt` 用 `root.after(0, ...)` 投递通知，下一轮 update 即 mapped。
    所以旧写法 `time.time() + 0.3` 只是给慢机器留余量，却把「跑得快」写成了
    「必须 300ms 内」——在 2 核 CI runner 上（Tk 初始化实测 810ms）就是 flake 源。
    """
    for _ in range(rounds):
        root.update()


class TempConfigCase(unittest.TestCase):
    """把 CONFIG_DIR / CONFIG_FILE / EVENTS_FILE 指到临时目录。

    v2.01.20：`_LEGACY_CONFIG_DIR` 也必须一起指走 —— `save_config` 现在会
    镜像一份配置到 APPDATA 当迁移种子，不隔离的话单测会覆盖**用户真实配置**
    （走查时自查发现，属 P-24）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mb-test-")
        self._old = (mb.CONFIG_DIR, mb.CONFIG_FILE, mb.EVENTS_FILE,
                     mb.LOG_DIR, mb.CONFIG_LOAD_ERROR, mb._LEGACY_CONFIG_DIR,
                     cfg_mod.CONFIG_DIR, cfg_mod.CONFIG_FILE,
                     cfg_mod.CONFIG_LOAD_ERROR, cfg_mod._LEGACY_CONFIG_DIR,
                     log_mod.LOG_DIR, log_mod.EVENTS_FILE)
        # R1：主文件与 core 模块各有一份 import 时绑定的路径常量，必须同步改。
        mb.CONFIG_DIR = cfg_mod.CONFIG_DIR = self.tmp
        mb.CONFIG_FILE = cfg_mod.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        mb.LOG_DIR = log_mod.LOG_DIR = self.tmp
        mb.EVENTS_FILE = log_mod.EVENTS_FILE = os.path.join(self.tmp, "events.log")
        mb.CONFIG_LOAD_ERROR = cfg_mod.CONFIG_LOAD_ERROR = ""
        mb._LEGACY_CONFIG_DIR = cfg_mod._LEGACY_CONFIG_DIR = os.path.join(self.tmp, "legacy")

    def tearDown(self):
        (mb.CONFIG_DIR, mb.CONFIG_FILE, mb.EVENTS_FILE,
         mb.LOG_DIR, mb.CONFIG_LOAD_ERROR, mb._LEGACY_CONFIG_DIR,
         cfg_mod.CONFIG_DIR, cfg_mod.CONFIG_FILE,
         cfg_mod.CONFIG_LOAD_ERROR, cfg_mod._LEGACY_CONFIG_DIR,
         log_mod.LOG_DIR, log_mod.EVENTS_FILE) = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


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

    def test_default_off_method_is_topology(self):
        """v2.01.23：默认熄灭方式是拓扑断开（不碰面板电源，风险最低）。"""
        self.assertEqual(mb.default_config()["screen_off_method"], "topology")

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
        fake._poll_read_queue_inner = types.MethodType(mb.App._poll_read_queue_inner, fake)
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
        self.writes, self.switches, self.recycles = [], [], []
        self._saves = []
        self._saves.append(set_any("enumerate_targets", self._targets))
        self._saves.append(set_any("release_targets", lambda ts: None))
        self._saves.append(set_any("_write_vcp", lambda h, c, v: (self.writes.append((h, c, v)), True)[1]))
        self._saves.append(set_any("_read_vcp", lambda h, c: (80, 100)))
        self._saves.append(set_any("_nudge_user_input", lambda: None))
        self._saves.append(set_any("_output_recycle", lambda: (self.recycles.append("recycle"), True)[1]))
        self._saves.append(set_any("_switch_input_roundtrip", lambda t: (self.switches.append(t["handle"]), True)[1]))
        self._old_sleep = mb.time.sleep
        mb.time.sleep = lambda s: None

    def _restore(self):
        for saved in reversed(self._saves):
            restore_any(saved)
        mb.time.sleep = self._old_sleep

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
        names = ("_read_vcp", "_write_vcp", "_capabilities_cached",
                 "_display_key", "enumerate_targets", "release_targets",
                 "screen_on_cfg", "screen_wake_flip", "_fast_flip_one",
                 "_wake_one", "_broadcast_monitor_power")
        self._orig = [(name, [(mod, getattr(mod, name))
                              for mod in _PATCH_MODULES if hasattr(mod, name)])
                      for name in names]
        self._old_at = dict(mb._FAST_FLIP_AT)
        self._old_sleep = mb.time.sleep
        mb._FAST_FLIP_AT.clear()
        self.sleeps = []
        mb.time.sleep = lambda s: self.sleeps.append(s)

    def tearDown(self):
        for name, saves in reversed(self._orig):
            for mod, old in saves:
                setattr(mod, name, old)
        mb._FAST_FLIP_AT.clear()
        mb._FAST_FLIP_AT.update(self._old_at)
        mb.time.sleep = self._old_sleep
        super().tearDown()

    def _targets(self):
        return [{"index": 0, "handle": 0xA0, "dev": "DISPLAY1", "desc": "A"},
                {"index": 1, "handle": 0xB0, "dev": "DISPLAY2", "desc": "B"}]

    def test_flip_sequence_has_no_wait(self):
        writes = []
        set_any("_read_vcp", lambda h, c: (15, 100) if c == mb.VCP_INPUT_SOURCE else None)
        set_any("_write_vcp", lambda h, c, v: (writes.append((h, c, v)), True)[1])
        set_any("_capabilities_cached", lambda h, k: "60(11 12 0F)")
        set_any("_display_key", lambda t: t["dev"])

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
        set_any("enumerate_targets", self._targets)
        set_any("release_targets", lambda ts: None)
        set_any("_display_key", lambda t: t["dev"])
        set_any("_fast_flip_one", lambda t: (True, "翻转 0x60 15→17→15，耗时 0.0 ms"))
        set_any("_wake_one", lambda t, a, e, r: (ladder.append((t["dev"], e)),
                                                (True, 4, "x"))[1])

        mb.screen_wake_flip([0])
        # 第二次点击 = 第一次没点亮，8 秒内必须升级为完整阶梯。
        # （v2.01.0 起 expect_off 由当场探测给出，不再依赖"第一次成功后把记账改成 on"）
        mb.screen_wake_flip([0], expect_off=False)
        self.assertEqual(ladder, [("DISPLAY1", True)],
                         "第二次必须以 expect_off=True 走完整 _wake_one 阶梯")
        self.assertIn("已升级为完整点亮", "；".join(mb.take_wake_info()))

    def test_fast_flip_never_broadcasts(self):
        broadcast = []
        set_any("enumerate_targets", self._targets)
        set_any("release_targets", lambda ts: None)
        set_any("_display_key", lambda t: t["dev"])
        set_any("_broadcast_monitor_power", lambda flag: (broadcast.append(flag), True)[1])
        set_any("_fast_flip_one", lambda t: (True, "翻转 0x60 15→17→15，耗时 0.0 ms"))

        ok, errs = mb.screen_wake_flip([0, 1])
        self.assertEqual(sorted(ok), [0, 1])
        self.assertEqual(errs, [])
        self.assertEqual(broadcast, [], "快速路径绝不发 SC_MONITORPOWER 广播")

    def test_switch_off_falls_back_to_v196_ladder(self):
        cfg = mb.default_config()
        cfg["wake_fast_flip"] = False
        calls = []
        set_any("screen_on_cfg", lambda c, i, **k: (calls.append("ladder"), ([0], []))[1])
        set_any("screen_wake_flip", lambda *a, **k: self.fail("关掉开关不得走快速路径"))

        ok, errs = mb.screen_wake_entry(cfg, [0])
        self.assertEqual(calls, ["ladder"])
        self.assertEqual(ok, [0])
        self.assertEqual(errs, [])

    def test_fast_path_gate_needs_ddc_and_switch_allowed(self):
        cfg = mb.default_config()
        # 显式指定 ddc：默认值可能变（v2.01.23 改成 topology），
        # 这条用例测的是"ddc 模式下的快速点亮闸门"，不该依赖默认值。
        cfg["screen_off_method"] = "ddc"
        self.assertTrue(cfg["wake_fast_flip"])

        calls = []
        set_any("screen_wake_flip", lambda *a, **k: (calls.append("flip"), ([0], []))[1])
        set_any("screen_on_cfg", lambda c, i, **k: (calls.append("ladder"), ([0], []))[1])

        # v2.01.0：expect_off 现在靠当场探测；这里打桩成"读到待机"，避免碰真机 DDC。
        with patch_any(
                "probe_states",
                return_value=[{"index": 0, "state": "off"}]):
            mb.screen_wake_entry(cfg, [0])
            cfg["wake_switch_input"] = False            # 用户禁用切源：回旧路径
            mb.screen_wake_entry(cfg, [0])
            cfg["wake_switch_input"] = True
            cfg["screen_off_method"] = "system"         # 系统级点亮本来就是即时广播
            mb.screen_wake_entry(cfg, [0])
            cfg["screen_off_method"] = "ddc"
            mb.screen_wake_entry(cfg, [0])
        self.assertEqual(calls, ["flip", "ladder", "ladder", "flip"])

    def test_expect_off_comes_from_live_probe_not_config(self):
        """v2.01.0：点亮策略不再读记账，只看当场探测到的待机值。"""
        cfg = mb.default_config()
        cfg["screen_state"] = "off"        # 残留的旧记账，不得参与决策
        with patch_any(
                "probe_states",
                return_value=[{"index": 0, "state": "on"}]):
            self.assertFalse(mb.wake_options(cfg)["expect_off"])
        with patch_any(
                "probe_states",
                return_value=[{"index": 0, "state": "off"}]):
            self.assertTrue(mb.wake_options(cfg)["expect_off"])

    def test_lit_screen_without_recent_flip_keeps_old_behavior(self):
        calls = []
        set_any("enumerate_targets", self._targets)
        set_any("release_targets", lambda ts: None)
        set_any("_display_key", lambda t: t["dev"])
        set_any("_fast_flip_one", lambda t: self.fail("屏亮着且无近期记录时不得翻转"))
        set_any("_wake_one", lambda t, a, e, r: (calls.append((t["dev"], e, r)),
                                                (True, 4, "x"))[1])

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
    """点击「＋ 添加时间段」→ 立即创建弹窗、跑事件循环即可见 → 在弹窗里完成一次添加。

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
        fake.rebuild_schedule_tasks = lambda: None

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
        pump_ui(self.root)                    # 同步创建的弹窗：跑事件循环即可见

        dialog = next((w for w in self.root.winfo_children()
                       if isinstance(w, tk.Toplevel) and w.winfo_exists()), None)
        self.assertIsNotNone(dialog, "点击后没有创建 Toplevel 弹窗")
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
    """v1.9.2：重建成功 → after(0) 投递的「确认」窗在事件循环里可见 → 点确认销毁。

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
        # 走查修复：失败回滚统一走真方法，FakeApp 需提供 save_config 依赖
        fake._set_schedule_enabled = types.MethodType(mb.App._set_schedule_enabled, fake)
        fake._rollback_schedule = types.MethodType(mb.App._rollback_schedule, fake)
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
        pump_ui(self.root)                    # after(0) 投递的确认窗：下一轮事件可见

        tops = self._top_levels()
        self.assertTrue(tops, "重建成功后没有创建确认 Toplevel")
        dialog = tops[-1]
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


class TopologyVerdictTest(unittest.TestCase):
    """E1 只读层：拓扑模式用 ACTIVE 当结论；非拓扑模式不再假装知道亮灭。"""

    def _cfg(self, method="topology", anchors=None, **kw):
        cfg = mb.default_config()
        cfg["screen_off_method"] = method
        cfg["monitor_anchors"] = anchors or []
        cfg.update(kw)
        return cfg

    @staticmethod
    def _row(anchor, active, vdd=False):
        return {"anchor": anchor, "active": active, "connected": True,
                "inUse": active, "vdd": vdd, "friendly": "Fake", "gdi": "\\\\.\\DISPLAY9"}

    def test_topology_active_is_the_only_verdict(self):
        """拓扑 ACTIVE=1 是系统事实：不管配置里残留什么，结论都只能是它。"""
        row = self._row("LUID/0001/0002/1", True)
        with patch_any("ccd_probe_topology", return_value=[row]):
            v = mb.screen_verdict(self._cfg(anchors=[row["anchor"]]), probes=[])
        self.assertEqual(v["state"], "on")
        self.assertIn("拓扑", v["text"])
        # 结论里不许出现"记账/上次操作"这类旧依据
        self.assertNotIn("记账", v["detail"])

    def test_topology_inactive_wins_over_bookkeeping(self):
        row = self._row("LUID/0001/0002/1", False)
        with patch_any("ccd_probe_topology", return_value=[row]):
            v = mb.screen_verdict(self._cfg(anchors=[row["anchor"]]), probes=[])
        self.assertEqual(v["state"], "off")
        self.assertIn("已断开", v["text"])

    def test_vdd_never_counts_as_selected_display(self):
        row = self._row("VDD/ANCHOR", True, vdd=True)
        with patch_any("ccd_probe_topology", return_value=[row]):
            v = mb.screen_verdict(self._cfg(), probes=[])
        self.assertEqual(v["state"], "unknown")

    def test_ddc_path_never_claims_on(self):
        """v2.01.0：DDC/system 读不到可信的亮灭，0xD6=1 会撒谎，只能说不知道。"""
        row = self._row("LUID/0001/0002/1", False)
        probes = [{"index": 0, "state": "on", "why": "0xD6=1 开"}]
        with patch_any("ccd_probe_topology", return_value=[row]):
            v = mb.screen_verdict(self._cfg(method="ddc"), probes=probes)
        self.assertEqual(v["state"], "unknown")
        self.assertNotIn("拓扑", v["text"])
        self.assertIn("以眼睛为准", v["detail"])

    def test_ddc_standby_value_is_trusted_direction(self):
        """0xD6 只会假阴性：它报了待机就是真待机，可以据此说 off。"""
        probes = [{"index": 0, "state": "off", "why": "0xD6=2 待机"}]
        v = mb.screen_verdict(self._cfg(method="system"), probes=probes)
        self.assertEqual(v["state"], "off")
        self.assertIn("待机", v["text"])

    def test_screen_verdict_never_reads_state_bookkeeping(self):
        """配置里塞进旧的 screen_state 也不影响结论——程序不认这个字段。"""
        cfg = self._cfg(method="system", screen_state="off", screen_state_at="11-27 21:00")
        probes = [{"index": 0, "state": "on", "why": "0xD6=1 开"}]
        v = mb.screen_verdict(cfg, probes=probes)
        self.assertEqual(v["state"], "unknown")
        self.assertNotIn("21:00", v["text"] + v["detail"])

    def test_unknown_verdict_carries_short_evidence(self):
        """GUI 提示行要直接显示"为什么不知道"，所以证据得单独给一份短的。"""
        probes = [{"index": 0, "state": "on", "why": "0xD6=1 开"}]
        v = mb.screen_verdict(self._cfg(method="system"), probes=probes)
        self.assertEqual(v["state"], "unknown")
        self.assertIn("0=开", v["evidence"])
        self.assertIn("依据：", v["detail"])      # 长版仍带前缀

    def test_topology_verdict_carries_evidence(self):
        row = self._row("LUID/0001/0002/1", True)
        with patch_any("ccd_probe_topology", return_value=[row]):
            v = mb.screen_verdict(self._cfg(anchors=[row["anchor"]]), probes=[])
        self.assertEqual(v["state"], "on")
        self.assertTrue(v["evidence"])


class MonitorIdentityTest(unittest.TestCase):
    """E2：monitor_anchors 优先于旧 monitor_ids / 下标；任一层没命中就往下兜。"""

    TARGETS = [{"index": 0, "dev": "DISPLAY1"}, {"index": 1, "dev": "DISPLAY2"}]
    ROWS = [
        {"anchor": "A1", "gdi": "\\\\.\\DISPLAY2", "aliases": ["\\\\.\\DISPLAY3"],
         "vdd": False, "active": True},
        {"anchor": "VDD1", "gdi": "\\\\.\\DISPLAY9", "aliases": [],
         "vdd": True, "active": True},
    ]

    def _cfg(self, **kw):
        cfg = mb.default_config()
        cfg.update(kw)
        return cfg

    def _patch(self):
        return (patch_any("enumerate_targets",
                                           return_value=list(self.TARGETS)),
                patch_any("release_targets"),
                patch_any("ccd_probe_topology",
                                           return_value=list(self.ROWS)))

    def test_anchor_wins_over_old_ids_and_indices(self):
        a, b, c = self._patch()
        with a, b, c:
            cfg = self._cfg(monitor_anchors=["A1"], monitor_ids=["DISPLAY1"], monitors=[0])
            self.assertEqual(mb.resolve_monitors(cfg), [1])

    def test_unmatched_anchor_falls_back_to_ids(self):
        a, b, c = self._patch()
        with a, b, c:
            cfg = self._cfg(monitor_anchors=["NOPE"], monitor_ids=["DISPLAY1"], monitors=[1])
            self.assertEqual(mb.resolve_monitors(cfg), [0])

    def test_vdd_anchor_never_resolves(self):
        a, b, c = self._patch()
        with a, b, c:
            cfg = self._cfg(monitor_anchors=["VDD1"], monitors=[0])
            self.assertEqual(mb.resolve_monitors(cfg), [0])

    def test_legacy_config_without_anchors(self):
        a, b, c = self._patch()
        with a, b, c:
            cfg = self._cfg(monitor_ids=["DISPLAY2"], monitors=[0])
            self.assertEqual(mb.resolve_monitors(cfg), [1])

    def test_selection_anchors_and_empty_selection(self):
        a, b, c = self._patch()
        with a, b, c:
            self.assertEqual(mb.selection_anchors([1]), ["A1"])
            self.assertEqual(mb.selection_anchors([]), [])


class TopologyGuiTest(TempConfigCase):
    """拓扑模式 GUI：断开的屏要列出来且可「启用」；VDD 永不显示。"""

    def test_inactive_rows_include_vdd_with_flag(self):
        """对齐 Windows 设置→屏幕：断开的屏全列出来，虚拟屏也列（只带标记）。

        VDD 仍然永不作**断开目标**（topology_plan_off 的护栏），
        "能不能看见"和"能不能动它"是两件事。
        """
        rows = [
            {"anchor": "A1", "active": True, "vdd": False, "friendly": "P1", "gdi": "DISPLAY1"},
            {"anchor": "A2", "active": False, "vdd": False, "friendly": "P2", "gdi": "DISPLAY2"},
            {"anchor": "V1", "active": False, "vdd": True, "friendly": "VDD", "gdi": "DISPLAY9"},
            {"anchor": "", "active": False, "vdd": False, "friendly": "?", "gdi": ""},
        ]
        got = mb.topology_inactive_rows(rows)
        self.assertEqual([r["anchor"] for r in got], ["A2", "V1"])
        self.assertFalse(got[0]["vdd"])
        self.assertTrue(got[1]["vdd"])

    def test_inactive_rows_skip_unplugged(self):
        """真拔了线的（connected=False）不列——那不是"断开"，是没了。"""
        rows = [{"anchor": "A3", "active": False, "connected": False,
                 "vdd": False, "friendly": "P3", "gdi": ""}]
        self.assertEqual(mb.topology_inactive_rows(rows), [])

    def test_enable_passes_anchor_to_current_topology(self):
        """v2.01.0：启用不再查历史快照，直接把这块屏交给「现读现算」的接回。"""
        cfg = mb.default_config()
        cfg["screen_off_method"] = "topology"
        with patch_any("screen_on_cfg",
                                        return_value=([1], [])) as on:
            ok, errs = mb.topology_enable_anchor(cfg, "A2")
        self.assertEqual((ok, errs), ([1], []))
        on.assert_called_once_with(cfg, [], anchors=["A2"])

    def test_enable_rejects_empty_anchor(self):
        cfg = mb.default_config()
        cfg["screen_off_method"] = "topology"
        with patch_any("screen_on_cfg") as on:
            ok, errs = mb.topology_enable_anchor(cfg, "")
        self.assertEqual(ok, [])
        self.assertIn("锚点", errs[0][1])
        on.assert_not_called()

    def test_panel_renders_disconnected_rows_like_windows_settings(self):
        """断开的物理屏给「启用」；虚拟屏也列出来但不给按钮。"""
        try:
            import tkinter as tk
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有 tkinter：%s" % e)
        try:
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            fake = types.SimpleNamespace(
                body=tk.Frame(root), cfg=mb.default_config(),
                monitors=[{"index": 0, "dev": "DISPLAY1"}],
                mon_info=[("DISPLAY1", 2560, 1440, True)], mon_models={},
                topo_inactive=[
                    {"anchor": "A2", "friendly": "R27qe Gen2", "gdi": "DISPLAY2",
                     "vdd": False},
                    {"anchor": "V1", "friendly": "VDD by MTT", "gdi": "DISPLAY92",
                     "vdd": True},
                ],
                refresh_monitors=lambda silent=False: None,
                read_current=lambda: None,
                on_monitor_toggle=lambda: None,
                select_none=lambda: None, select_all=lambda: None,
                monitor_off=lambda i: None, monitor_on=lambda i: None,
                topology_enable=lambda anchor: None)
            panel = mb.MonitorPanel(fake)
            fake.mon_panel = panel
            panel.rebuild([])
            texts = []
            for row in panel.inner.winfo_children():
                for w in row.winfo_children():
                    for sub in w.winfo_children():
                        if hasattr(sub, "cget"):
                            try:
                                texts.append(str(sub.cget("text")))
                            except Exception:
                                pass
            self.assertTrue(any("已断开" in t for t in texts), texts)
            self.assertTrue(any("R27qe Gen2" in t for t in texts), texts)
            # 第三块屏（虚拟显示器）也要出现在列表里
            self.assertTrue(any("VDD by MTT" in t for t in texts), texts)
            self.assertTrue(any("虚拟显示器" in t for t in texts), texts)
            btns = [_row_button_texts(row) for row in panel.inner.winfo_children()]
            # 亮着的行只有一个「熄灭」；断开的物理屏只有「显示」；虚拟屏什么都不给
            self.assertEqual(btns[0], ["熄灭"], btns)
            self.assertEqual(btns[1], ["显示"], btns)
            self.assertEqual(btns[2], [], btns)
        finally:
            root.destroy()

    def test_panel_hides_row_buttons_for_group_wide_method(self):
        """系统级熄灭是整组广播：一个逐台按钮都不该有，免得点了没反应。"""
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            cfg = mb.default_config()
            cfg["screen_off_method"] = "system"
            fake = types.SimpleNamespace(
                body=tk.Frame(root), cfg=cfg,
                monitors=[{"index": 0, "dev": "DISPLAY1"}],
                mon_info=[("DISPLAY1", 2560, 1440, True)], mon_models={},
                topo_inactive=[], refresh_monitors=lambda silent=False: None,
                read_current=lambda: None, on_monitor_toggle=lambda: None,
                select_none=lambda: None, select_all=lambda: None,
                monitor_off=lambda i: None, monitor_on=lambda i: None,
                topology_enable=lambda anchor: None)
            panel = mb.MonitorPanel(fake)
            fake.mon_panel = panel
            panel.rebuild([])
            got = [_row_button_texts(row)
                   for row in panel.inner.winfo_children()]
            self.assertNotIn("熄灭", got)
            self.assertNotIn("显示", got)
        finally:
            root.destroy()

    def test_primary_row_has_one_enabled_off_button_in_topology_mode(self):
        """拓扑模式移不走主屏，但「熄灭」**可点**：点了会解释并给替代方案。

        用户反馈：灰按钮"感觉不符合直觉"。按钮应该表达"现在能做的那一件事"，
        不能做的时候不该摆一个灰的在那儿。
        """
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            cfg = mb.default_config()
            cfg["screen_off_method"] = "topology"
            fake = types.SimpleNamespace(
                body=tk.Frame(root), cfg=cfg,
                monitors=[{"index": 0, "dev": "DISPLAY1"},
                          {"index": 1, "dev": "DISPLAY2"}],
                mon_info=[("DISPLAY1", 2560, 1440, True),
                          ("DISPLAY2", 2560, 1440, False)], mon_models={},
                topo_inactive=[], refresh_monitors=lambda silent=False: None,
                read_current=lambda: None, on_monitor_toggle=lambda: None,
                select_none=lambda: None, select_all=lambda: None,
                monitor_off=lambda i: None, monitor_on=lambda i: None,
                topology_enable=lambda anchor: None)
            panel = mb.MonitorPanel(fake)
            fake.mon_panel = panel
            panel.rebuild([])
            rows = list(panel.inner.winfo_children())
            self.assertEqual(_row_button_texts(rows[0]), ["熄灭"])
            self.assertEqual(_row_button_texts(rows[1]), ["熄灭"])

            def off_enabled(row):
                queue = list(row.winfo_children())
                while queue:
                    w = queue.pop(0)
                    if getattr(w, "_text", None) == "熄灭":
                        return bool(getattr(w, "_enabled", True))
                    queue.extend(w.winfo_children())
                return None

            # 主屏也能点（点了会问是否改用待机），不再是灰的
            self.assertTrue(off_enabled(rows[0]))
            self.assertTrue(off_enabled(rows[1]))
        finally:
            root.destroy()

    def test_ddc_mode_switches_button_by_state(self):
        """DDC 模式下屏幕被枚举不代表面板亮着：按探测结论给「显示」。"""
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            cfg = mb.default_config()
            cfg["screen_off_method"] = "ddc"
            fake = types.SimpleNamespace(
                body=tk.Frame(root), cfg=cfg,
                monitors=[{"index": 0, "dev": "DISPLAY1"},
                          {"index": 1, "dev": "DISPLAY2"}],
                mon_info=[("DISPLAY1", 2560, 1440, True),
                          ("DISPLAY2", 2560, 1440, False)], mon_models={},
                topo_inactive=[], _screen_states={1: "off"},
                refresh_monitors=lambda silent=False: None,
                read_current=lambda: None, on_monitor_toggle=lambda: None,
                select_none=lambda: None, select_all=lambda: None,
                monitor_off=lambda i: None, monitor_on=lambda i: None,
                topology_enable=lambda anchor: None)
            panel = mb.MonitorPanel(fake)
            fake.mon_panel = panel
            panel.rebuild([])
            rows = list(panel.inner.winfo_children())
            self.assertEqual(_row_button_texts(rows[0]), ["熄灭"])   # 亮着
            self.assertEqual(_row_button_texts(rows[1]), ["显示"])   # DDC 报待机
        finally:
            root.destroy()


def _row_button_texts(row):
    """递归取一行里「熄灭 / 显示」按钮的文字，按出现顺序。

    按钮包在一个 box 里，不是行的直接子控件 —— 别按下标猜。
    """
    out, queue = [], list(row.winfo_children())
    while queue:
        w = queue.pop(0)
        if getattr(w, "_text", None) in ("熄灭", "显示"):
            out.append(w._text)
        queue.extend(w.winfo_children())
    return out


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


class WeekdayScheduleTest(unittest.TestCase):
    """v2.02.0：定时档位可以只在指定星期生效（days：1=周一 … 7=周日，空=每天）。"""

    def test_normalize_days(self):
        self.assertEqual(mb.normalize_days(None), [])
        self.assertEqual(mb.normalize_days([1, 3, 5]), [1, 3, 5])
        self.assertEqual(mb.normalize_days([7, 1, 1, 9]), [1, 7])   # 去重排序丢非法
        self.assertEqual(mb.normalize_days([1, 2, 3, 4, 5, 6, 7]), [])  # 全 7 天=每天
        self.assertEqual(mb.normalize_days("bad"), [])

    def test_days_label(self):
        self.assertEqual(mb.days_label([]), "每天")
        self.assertEqual(mb.days_label([1, 2, 3, 4, 5]), "工作日")
        self.assertEqual(mb.days_label([6, 7]), "周末")
        self.assertEqual(mb.days_label([1, 3, 5]), "周一,三,五")

    def test_slots_for_weekday_filters(self):
        slots = [{"time": "08:30", "level": 80, "screen": "", "days": [1, 2, 3, 4, 5]},
                 {"time": "20:00", "level": 30, "screen": "", "days": []}]
        sat = mb.slots_for_weekday(slots, 6)
        self.assertEqual([s["time"] for s in sat], ["20:00"])   # 周六只有每天档
        wed = mb.slots_for_weekday(slots, 3)
        self.assertEqual([s["time"] for s in wed], ["08:30", "20:00"])
        # 全都不生效时退回全部，避免无档可判
        only_work = [s for s in slots if s["days"]]
        self.assertEqual(len(mb.slots_for_weekday(only_work, 6)), 1)

    def test_trigger_line_daily_vs_weekly(self):
        daily = mb._ps_trigger_line({"time": "08:30", "days": []})
        self.assertIn("-Daily", daily)
        self.assertNotIn("DaysOfWeek", daily)
        weekly = mb._ps_trigger_line({"time": "08:30", "days": [1, 3, 5]})
        self.assertIn("-Weekly", weekly)
        self.assertIn("Monday,Wednesday,Friday", weekly)
        self.assertIn("-WeeksInterval 1", weekly)
        # 全 7 天按每天注册，与旧版注册结果一致
        all_days = mb._ps_trigger_line({"time": "08:30", "days": [1, 2, 3, 4, 5, 6, 7]})
        self.assertIn("-Daily", all_days)

    def test_xml_fingerprint_parses_daily_and_weekly(self):
        xml = """
        <Triggers>
          <CalendarTrigger>
            <StartBoundary>2026-11-27T08:30:00</StartBoundary>
            <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
          </CalendarTrigger>
          <CalendarTrigger>
            <StartBoundary>2026-11-27T20:00:00</StartBoundary>
            <ScheduleByWeek>
              <DaysOfWeek><Saturday /><Sunday /></DaysOfWeek>
              <WeeksInterval>1</WeeksInterval>
            </ScheduleByWeek>
          </CalendarTrigger>
        </Triggers>
        """
        fp = mb._xml_trigger_fingerprint(xml)
        self.assertEqual(fp, [(510, frozenset()), (1200, frozenset({6, 7}))])

    def test_config_fingerprint_matches_equivalent_xml(self):
        """配置"每天"与任务的 Daily 触发器必须判为一致，否则每次启动都重建。"""
        slots = [{"time": "08:30", "level": 80, "screen": "", "days": []}]
        xml = """<CalendarTrigger>
          <StartBoundary>2026-11-27T08:30:00</StartBoundary>
          <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
        </CalendarTrigger>"""
        self.assertEqual(mb._slot_trigger_fingerprint(slots),
                         mb._xml_trigger_fingerprint(xml))

    def test_next_run_respects_weekday(self):
        """周五 20:30 时，"仅周一"的档不能报成"明天"。"""
        now = datetime(2026, 11, 27, 20, 30)          # 周五
        self.assertEqual(
            mb._next_run_text([510], now=now, days_by_min={510: frozenset({1})}),
            "周一 08:30")
        self.assertEqual(
            mb._next_run_text([510], now=now,
                              days_by_min={510: frozenset({1, 2, 3, 4, 5})}),
            "周一 08:30")
        # 每天的行为与旧版一致
        self.assertEqual(
            mb._next_run_text([510], now=now, days_by_min={510: frozenset()}),
            "明天 08:30")
        self.assertEqual(mb._next_run_text([1260], now=now), "今天 21:00")
        # 周六的档 = 明天
        self.assertEqual(
            mb._next_run_text([540], now=now, days_by_min={540: frozenset({6})}),
            "明天 09:00")

    def test_days_by_min_from_fp(self):
        fp = [(510, frozenset({1, 2})), (510, frozenset({3})), (1260, frozenset())]
        got = mb._days_by_min_from_fp(fp)
        self.assertEqual(got[510], frozenset({1, 2, 3}))
        self.assertEqual(got[1260], frozenset())


class AppMethodTest(TempConfigCase):
    """v2.03.0 P0：重构要搬的 App 方法，先钉住输入输出。

    App 89 个方法里原先只有 24 个被测；这里补的是「抽服务时必然被搬动」的那些。
    用 SimpleNamespace 假 App + 绑定真方法，不需要 Tk 环境。
    """

    def _fake(self, **kw):
        base = dict(
            cfg=mb.default_config(),
            mon_panel=types.SimpleNamespace(selected=lambda: [0, 1]),
            status_var=types.SimpleNamespace(set=lambda t: None),
            run_bg=lambda job, done, busy=False: None,
            msg=types.SimpleNamespace(showerror=lambda *a: None,
                                      showinfo=lambda *a: None,
                                      askyesno=lambda *a: True),
            _row_action_blocker=lambda: "",
            _finish_screen_on=lambda r: None,
            _finish_screen_off=lambda r: None,
            _busy=False, _busy_action="", _row_action=False,
        )
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_selected_indices_delegates(self):
        fake = self._fake()
        self.assertEqual(mb.App.selected_indices(fake), [0, 1])
        fake.mon_panel = types.SimpleNamespace(selected=lambda: [])
        self.assertEqual(mb.App.selected_indices(fake), [])

    def test_auto_relight_minutes_clamps(self):
        for raw, want in ((0, 0), (30, 30), (120, 120), (200, 120), (-5, 0),
                          ("abc", 0), (None, 0)):
            fake = self._fake()
            fake.cfg["screen_auto_relight_min"] = raw
            self.assertEqual(mb.App._auto_relight_minutes(fake), want, repr(raw))

    def test_monitor_on_starts_background_job(self):
        calls = []
        fake = self._fake(run_bg=lambda job, done, busy=False:
                          calls.append((type(job).__name__, busy)))
        mb.App.monitor_on(fake, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1], "逐台点亮必须占 busy 锁")

    def test_monitor_off_blocked_by_mode_does_nothing(self):
        """系统级模式下没有「单块熄灭」这回事：只提示，不起任务。"""
        calls = []
        fake = self._fake(_row_action_blocker=lambda: "「系统级」熄灭是整组广播",
                          run_bg=lambda *a, **k: calls.append(1))
        mb.App.monitor_off(fake, 0)
        self.assertEqual(calls, [])
        fake._row_action_blocker = lambda: ""
        fake.cfg["screen_off_method"] = "topology"
        with patch_any("selection_anchors", return_value=[]):
            mb.App.monitor_off(fake, 0)
        self.assertEqual(calls, [], "拿不到锚点也不能动")

    def test_monitor_off_topology_secondary_uses_topology_off(self):
        """副屏单独熄灭走 topology_off；不应走整组 screen_off。"""
        started = []
        fake = self._fake(
            mon_info=[("DISPLAY1", 2560, 1440, True), ("DISPLAY2", 2560, 1440, False)],
            run_bg=lambda job, done, busy=False: started.append((job, done, busy)))
        fake.cfg["screen_off_method"] = "topology"
        with patch_any("selection_anchors", return_value=["A2"]):
            mb.App.monitor_off(fake, 1)
        self.assertEqual(len(started), 1)
        self.assertTrue(started[0][2])

    def test_on_slots_changed_saves_and_defers_rebuild(self):
        """时间段改了：立刻落盘；已启用时 500ms 防抖后重建任务（不逐次重建）。"""
        seen = []

        def after(ms, fn):
            seen.append(("after", ms))
            return "timer-1"

        fake = self._fake(root=types.SimpleNamespace(
            after=after, after_cancel=lambda t: seen.append(("cancel", t))))
        fake._schedule_reg_timer = None
        fake._register_after_edit = lambda: None
        fake.cfg["enabled"] = False
        with patch_any("save_config", return_value=(True, "")):
            mb.App.on_slots_changed(fake, [{"time": "07:00", "level": 40,
                                            "screen": "", "days": []}])
        self.assertEqual(fake.cfg["slots"][0]["time"], "07:00")
        self.assertEqual(seen, [], "未启用时不该重建任务")

        fake.cfg["enabled"] = True
        with patch_any("save_config", return_value=(True, "")):
            mb.App.on_slots_changed(fake, [{"time": "08:00", "level": 50,
                                            "screen": "", "days": []}])
        self.assertEqual(seen, [("after", 500)])
        self.assertEqual(fake._schedule_reg_timer, "timer-1")

    def test_on_slots_changed_reports_save_failure(self):
        """落盘失败必须报错并中止，不能"看起来保存成功、其实没写"。"""
        errors = []
        fake = self._fake(msg=types.SimpleNamespace(
            showerror=lambda t, m: errors.append((t, m)),
            showinfo=lambda *a: None, askyesno=lambda *a: True))
        fake._schedule_reg_timer = None
        fake.cfg["enabled"] = True
        with patch_any("save_config",
                                        return_value=(False, "磁盘只读")):
            mb.App.on_slots_changed(fake, [{"time": "07:00", "level": 40,
                                            "screen": "", "days": []}])
        self.assertEqual(len(errors), 1)
        self.assertIn("磁盘只读", errors[0][1])
        self.assertIsNone(fake._schedule_reg_timer, "失败不能排重建")


class UiDecisionTest(unittest.TestCase):
    """v2.03.0 P2：从 App 抽出的界面决策纯函数（不碰 Tk，可独立测）。"""

    def test_status_payload_only_trusts_topology(self):
        cfg = {"screen_off_method": "topology"}
        self.assertEqual(mb.status_payload(cfg, "off", "h"),
                         {"state": "off", "hint": "h"})
        cfg["screen_off_method"] = "ddc"
        self.assertEqual(mb.status_payload(cfg, "off", "h"),
                         {"state": "unknown", "hint": "h"})
        cfg["screen_off_method"] = "system"
        self.assertEqual(mb.status_payload(cfg, "on", "h")["state"], "unknown")

    def test_row_action_blocker(self):
        self.assertIn("整组广播",
                      mb.row_action_blocker({"screen_off_method": "system"}, False))
        self.assertEqual(mb.row_action_blocker({"screen_off_method": "ddc"}, True),
                         "正在处理上一个操作，请稍候")
        self.assertEqual(mb.row_action_blocker({"screen_off_method": "ddc"}, False), "")
        self.assertEqual(mb.row_action_blocker({"screen_off_method": "topology"}, False), "")

    def test_restore_selection(self):
        # 启动：全选
        self.assertEqual(mb.restore_selection([], 3), [0, 1, 2])
        # 刷新：保留用户勾选
        self.assertEqual(mb.restore_selection([1], 3), [1])
        # 越界过滤（屏变少了）
        self.assertEqual(mb.restore_selection([0, 5], 2), [0])
        # 全越界 → 退回全选
        self.assertEqual(mb.restore_selection([9], 2), [0, 1])
        self.assertEqual(mb.restore_selection(None, 2), [0, 1])

    def test_auto_relight_minutes_clamps(self):
        for raw, want in ((0, 0), (45, 45), (120, 120), (500, 120), (-3, 0),
                          ("x", 0), (None, 0)):
            self.assertEqual(mb.auto_relight_minutes(
                {"screen_auto_relight_min": raw}), want, repr(raw))

    def test_tray_tooltip_text(self):
        cfg = mb.default_config()
        cfg["enabled"] = False
        self.assertIn("定时未启用", mb.tray_tooltip_text(cfg, None, "MB"))
        cfg["enabled"] = True
        cfg["slots"] = mb._coerce_slots([{"time": "08:30", "level": 80,
                                          "screen": "off"}])
        txt = mb.tray_tooltip_text(cfg, {"state": "off"}, "MB")
        self.assertIn("08:30", txt)
        self.assertIn("已熄灭", txt)
        self.assertLessEqual(len(txt), 120)
        # 没探测过就老实说未检测
        self.assertIn("未检测", mb.tray_tooltip_text(cfg, None, "MB"))


    def test_is_vdd_index(self):
        mons = [{"dev": "\\\\.\\DISPLAY1"}, {"dev": "\\\\.\\DISPLAY92"}]
        self.assertTrue(mb.is_vdd_index(1, mons, {"display92"}))
        self.assertFalse(mb.is_vdd_index(0, mons, {"display92"}))
        self.assertFalse(mb.is_vdd_index(5, mons, {"display92"}))   # 越界
        self.assertFalse(mb.is_vdd_index(0, mons, ()))              # 无集合

    def test_ccd_friendly_name(self):
        mons = [{"dev": "\\\\.\\DISPLAY1"}]
        self.assertEqual(mb.ccd_friendly_name(0, mons, {"display1": "R27qe Gen2"}),
                         "R27qe Gen2")
        self.assertEqual(mb.ccd_friendly_name(0, mons, {}), "")
        self.assertEqual(mb.ccd_friendly_name(3, mons, {"display1": "x"}), "")

    def test_plan_model_updates(self):
        # 正常：只回需要改的，且按下标排序
        got = mb.plan_model_updates({1: "P2", 0: "P1", 2: "P3"},
                                    {0: "P1"}, 3)
        self.assertEqual(got, [(1, "P2"), (2, "P3")])
        # 越界 / 空名 / 已相同 / 非 dict 全部跳过
        self.assertEqual(mb.plan_model_updates({5: "X", 0: "", 1: "same"},
                                               {1: "same"}, 2), [])
        self.assertEqual(mb.plan_model_updates(OSError("boom"), {}, 2), [])
        self.assertEqual(mb.plan_model_updates({"a": "X"}, {}, 2), [])


class UiSmokeTest(unittest.TestCase):
    """v2.03.0 U0：UI 重构的护栏。

    搬家（拆 ui/ 包）最容易出的错是 import 断掉、控件构造参数错、
    父类没先定义 —— 这些都不会被业务测试抓到。这里把每个控件真建一遍。
    故意不建 App（会真去查显示器），也不建弹层（show() 会 grab + 等可见）。
    """

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as e:                        # pragma: no cover
            raise unittest.SkipTest("无可用显示：%s" % e)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def test_widgets_construct(self):
        """每个自绘控件都要能建出来且能正常销毁。"""
        import tkinter as tk
        parent = tk.Frame(self.root, bg="#1c1c1e")
        made = [
            mb.Label(parent, "文字"),
            mb.Label(parent, "小字", size=8, color="#fff"),
            mb.SectionTitle(parent, "分区标题"),
            mb.Card(parent),
            mb.Button(parent, "确定"),
            mb.Button(parent, "主操作", kind="primary"),
            mb.Switch(parent, True),
            mb.Slider(parent, 50),
            mb.Segmented(parent, ["不变", "熄灭", "点亮"]),
            mb.WeekdayPicker(parent, [1, 3, 5]),
            mb.TimeField(parent, "08:30"),
            mb.ScrollArea(parent, "#1c1c1e"),
        ]
        for w in made:
            w.pack() if hasattr(w, "pack") else None
        self.root.update_idletasks()
        # 逐个销毁：能建不能毁同样是 bug（比如 <Destroy> 回调里抛异常）
        for w in made:
            w.destroy()

    def test_tooltip_binds_and_unbinds(self):
        """Tooltip 依赖递归绑子树（P-20），搬家时最容易丢。"""
        import tkinter as tk
        parent = tk.Frame(self.root)
        btn = mb.Button(parent, "带提示")
        tip = mb.Tooltip(btn, "说明文字")
        self.assertIsNotNone(tip)
        btn.destroy()

    def test_button_state_helpers(self):
        """按钮的可用/文字切换是 UI 最常用的接口，别搬家搬坏。"""
        import tkinter as tk
        parent = tk.Frame(self.root)
        b = mb.Button(parent, "记住")
        b.set_text("已记住 ✓")
        self.assertEqual(b.text(), "已记住 ✓")
        b.set_enabled(False)
        b.set_enabled(True)
        b.destroy()

    def test_segmented_and_weekday_get(self):
        import tkinter as tk
        parent = tk.Frame(self.root)
        seg = mb.Segmented(parent, ["a", "b", "c"], index=1)
        self.assertEqual(seg.get(), 1)
        seg.index = 2            # Segmented 没有 set_index，用属性 + 重绘
        seg._draw()
        self.assertEqual(seg.get(), 2)
        picker = mb.WeekdayPicker(parent, [6, 7])
        self.assertEqual(picker.get(), [6, 7])
        picker.set([1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(picker.get(), [], "选满 7 天归一成每天")


    def test_app_init_window_runs(self):
        """回归锁：v2.03.0 U5 拆 __init__ 时，_init_window 里多留了一行
        `self.root = root`（该方法没有 root 参数）→ 打包版启动即崩
        "Unhandled exception in script"，而 216 个测试全绿。

        根因是**没有任何测试构造过 App**（构造会真去枚举显示器）。
        这里用假 self 跑窗口初始化这一段，成本低且能抓住这类 NameError。
        """
        fake = types.SimpleNamespace(root=self.root, tray=None)
        mb.App._init_window(fake)
        self.assertIs(fake.root, self.root)
        self.assertEqual(fake.root.title(), "%s v%s" % (mb.APP_TITLE, mb.APP_VERSION))


class WheelRouterTest(unittest.TestCase):
    """v2.03.1：滚轮改成进程级路由。

    原实现两处 `unbind_all("<MouseWheel>")`（Sheet 关闭、ScrollArea 的 Leave），
    会清掉**所有**滚轮绑定 —— 开一次设置页再关掉，主界面滚轮就永久失效。
    """

    def test_no_unbind_all_anywhere(self):
        """硬回归锁：源码里不许再出现 unbind_all 调用（注释除外）。"""
        import ast as _ast
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "monitor_brightness.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        offenders = []
        for n in _ast.walk(_ast.parse(src)):
            if isinstance(n, _ast.Call) and getattr(n.func, "attr", "") == "unbind_all":
                offenders.append(n.lineno)
        self.assertEqual(offenders, [],
                         "unbind_all 会清空全局滚轮绑定（见 P-27 副作用）")

    def test_route_wheel_finds_nearest_target(self):
        calls = []

        def target(event):
            calls.append(event.widget)
            return True

        parent = types.SimpleNamespace(_wheel_target=target)
        child = types.SimpleNamespace(master=parent)
        event = types.SimpleNamespace(widget=child)
        self.assertEqual(mb._route_wheel(event), "break")
        self.assertEqual(calls, [child])

    def test_route_wheel_skips_declining_target(self):
        """容器返回 False（内容放得下）→ 继续往上找，最终没人接就返回 None。"""
        outer_hits = []
        inner = types.SimpleNamespace(_wheel_target=lambda e: False)
        outer = types.SimpleNamespace(_wheel_target=lambda e: outer_hits.append(1) or True)
        inner.master = outer
        event = types.SimpleNamespace(widget=inner)
        self.assertEqual(mb._route_wheel(event), "break")
        self.assertEqual(outer_hits, [1])
        # 全都不接 → None（不吞事件）
        solo = types.SimpleNamespace(_wheel_target=lambda e: False, master=None)
        self.assertIsNone(mb._route_wheel(types.SimpleNamespace(widget=solo)))


class RegisterScriptTest(unittest.TestCase):
    """v2.03.0 P0：build_register_script 原为零覆盖。

    这是"定时到点没执行"类故障的唯一源头，重构前必须钉住生成结果。
    """

    def _cfg(self, **kw):
        cfg = mb.default_config()
        cfg["enabled"] = True
        cfg["slots"] = mb._coerce_slots([{"time": "08:30", "level": 80},
                                         {"time": "21:00", "level": 0}])
        cfg.update(kw)
        return cfg

    def test_one_trigger_per_slot(self):
        script = mb.build_register_script(self._cfg())
        for t in ("'08:30'", "'21:00'"):
            self.assertIn(t, script)
        # 两档 = 两条 -Daily 触发器（登录校准的 -AtLogOn 是另一类，不计入）
        self.assertEqual(script.count("New-ScheduledTaskTrigger -Daily"), 2)

    def test_start_when_available_is_set(self):
        """错过补跑（关机/睡眠）是 v1.0.0 定时漏跑的根本原因，不能丢。"""
        script = mb.build_register_script(self._cfg())
        self.assertIn("-StartWhenAvailable", script)

    def test_weekly_trigger_for_weekday_slots(self):
        cfg = self._cfg()
        cfg["slots"] = mb._coerce_slots([{"time": "08:30", "level": 80,
                                          "days": [1, 3, 5]}])
        script = mb.build_register_script(cfg)
        self.assertIn("-Weekly", script)
        self.assertIn("Monday,Wednesday,Friday", script)

    def test_catch_up_task_registered_and_removed(self):
        on = mb.build_register_script(self._cfg(catch_up_on_logon=True))
        self.assertIn("-AtLogOn", on)
        self.assertIn("CATCHUP=OK", on)
        # 关掉时不是"不管它"，而是**主动清掉**残留的登录校准任务
        off = mb.build_register_script(self._cfg(catch_up_on_logon=False))
        self.assertNotIn("-AtLogOn", off)
        self.assertIn("Unregister-ScheduledTask -TaskName '%s'" % mb.TASK_CATCHUP, off)

    def test_single_quote_in_path_is_escaped(self):
        """PowerShell 单引号字符串里 ' 要写成 ''，否则路径带引号时脚本语法错误。"""
        self.assertEqual(mb._ps_quote("C:\\a'b\\x.exe"), "'C:\\a''b\\x.exe'")
        self.assertIn("--apply-schedule", mb.build_register_script(self._cfg()))

    def test_uses_set_scheduled_task_when_exists(self):
        """已存在的任务优先原地更新（保留上次运行记录），失败才重建。"""
        script = mb.build_register_script(self._cfg())
        self.assertIn("Set-ScheduledTask", script)
        self.assertIn("REBUILT=FALLBACK", script)


class SelectionAnchorsTest(unittest.TestCase):
    """勾选 → 锚点。aliases 是陷阱：每块屏的 aliases 都含 DISPLAY1~4。"""

    def test_aliases_do_not_leak_other_screens(self):
        targets = [{"index": 0, "dev": "DISPLAY1", "primary": True},
                   {"index": 1, "dev": "DISPLAY2", "primary": False}]
        rows = [
            {"anchor": "A1", "gdi": "\\\\.\\DISPLAY1", "vdd": False, "active": True,
             "aliases": ["\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2"]},
            {"anchor": "A2", "gdi": "\\\\.\\DISPLAY2", "vdd": False, "active": True,
             "aliases": ["\\\\.\\DISPLAY2", "\\\\.\\DISPLAY1"]},
            {"anchor": "V1", "gdi": "\\\\.\\DISPLAY92", "vdd": True, "active": False,
             "aliases": []},
        ]
        with patch_any("ccd_probe_topology", return_value=rows):
            self.assertEqual(mb.selection_anchors([0], targets), ["A1"])
            self.assertEqual(mb.selection_anchors([1], targets), ["A2"])
            self.assertEqual(mb.selection_anchors([0, 1], targets), ["A1", "A2"])
            self.assertEqual(mb.selection_anchors([], targets), [])   # 空=全部，不锚死

    def test_inactive_screen_with_same_gdi_does_not_steal_anchor(self):
        """v2.01.18：断开屏 gdi 撞名主屏时，不能把它一起命中。

        用户 19:46 截图实证：主屏 active DISPLAY1，断开屏 inactive 也残留
        DISPLAY1。selection_anchors 若按 gdi 匹配不排除 inactive，会把两块
        都命中 → 交接弹窗 skip 掉两块 → 候选只剩 VDD（该有 2 个却只有 1 个）。
        """
        targets = [{"index": 0, "dev": "DISPLAY1", "primary": True}]
        rows = [
            {"anchor": "A1", "gdi": "\\\\.\\DISPLAY1", "vdd": False,
             "active": True, "connected": True},
            {"anchor": "A2", "gdi": "\\\\.\\DISPLAY1", "vdd": False,
             "active": False, "connected": True},
            {"anchor": "VDD", "gdi": "\\\\.\\DISPLAY92", "vdd": True,
             "active": False, "connected": True},
        ]
        with patch_any("ccd_probe_topology", return_value=rows):
            anchors = mb.selection_anchors([0], targets)
        self.assertEqual(anchors, ["A1"])          # 只命中主屏，不带 A2
        cands = mb.topology_heir_candidates(anchors, rows=rows)
        self.assertEqual([c["anchor"] for c in cands], ["A2", "VDD"])  # 候选 2 个


class _FakeU32:
    """够用的 user32 替身：枚举窗口 + 读/写矩形。"""

    def __init__(self, rects):
        self._r = dict(rects)          # hwnd -> (x, y, w, h)
        self.moved = []                # [(hwnd, x, y)]
        self.pid = 999999              # 别等于本进程，否则会被过滤掉

    def EnumWindows(self, cb, _lp):
        for h in list(self._r):
            cb(h, 0)
        return 1

    def IsWindowVisible(self, h):
        return True

    def IsIconic(self, h):
        return False

    def GetWindowTextLengthW(self, h):
        return 5

    def GetWindowLongW(self, h, _i):
        return 0

    def GetWindowThreadProcessId(self, h, ref):
        ref._obj.value = self.pid
        return 1

    def GetWindowRect(self, h, ref):
        x, y, w, hh = self._r[h]
        r = ref._obj
        r.left, r.top, r.right, r.bottom = x, y, x + w, y + hh
        return 1

    def IsWindow(self, h):
        return h in self._r

    def SetWindowPos(self, h, _after, x, y, _cx, _cy, _flags):
        ox, oy, w, hh = self._r[h]
        self._r[h] = (x, y, w, hh)
        self.moved.append((h, x, y))
        return 1


class WindowFollowTest(unittest.TestCase):
    """v2.01.9：交接主屏会让所有屏平移，窗口必须跟着自己那块屏走。

    不做的话用户看到的是"一改显示器设置，所有窗口挤成一堆" —— Windows 在
    拓扑变化时会把窗口挪走，而且不会在拓扑恢复后搬回来。
    """

    LAYOUT = {"A1": (0, 0, 2560, 1440), "A2": (-2560, 0, 2560, 1440)}

    def test_snapshot_maps_window_to_its_screen(self):
        u32 = _FakeU32({101: (100, 100, 800, 600),      # 中心 (500,400) → A1
                        202: (-2000, 50, 800, 600)})    # 中心 (-1600,350) → A2
        snap = mb.win_snapshot(self.LAYOUT, u32)
        self.assertEqual({s["hwnd"]: s["anchor"] for s in snap},
                         {101: "A1", 202: "A2"})

    def test_follow_moves_only_windows_on_surviving_screens(self):
        u32 = _FakeU32({101: (100, 100, 800, 600),
                        202: (-2000, 50, 800, 600)})
        snap = mb.win_snapshot(self.LAYOUT, u32)
        # 主屏 A1 被摘掉、其余屏平移 +2560（A2 挪到桌面原点）
        moved = mb.win_follow(snap, (2560, 0), {"A1"}, u32)
        self.assertEqual(moved, 1)
        self.assertEqual(u32.moved, [(202, 560, 50)])   # -2000+2560
        self.assertEqual(u32._r[101][0], 100)           # 被摘屏上的窗口不动

    def test_follow_skips_windows_already_in_place(self):
        """Windows 已经把它放到目标位置了就别再动 —— 否则是我们在制造抖动。"""
        u32 = _FakeU32({202: (560 + 2560, 50, 800, 600)})
        snap = [{"hwnd": 202, "rect": (560, 50, 800, 600), "anchor": "A2"}]
        self.assertEqual(mb.win_follow(snap, (2560, 0), set(), u32), 0)
        self.assertEqual(u32.moved, [])

    def test_no_shift_means_no_work(self):
        u32 = _FakeU32({101: (100, 100, 800, 600)})
        snap = [{"hwnd": 101, "rect": (100, 100, 800, 600), "anchor": "A1"}]
        self.assertEqual(mb.win_follow(snap, (0, 0), set(), u32), 0)
        self.assertEqual(u32.moved, [])

    def test_dead_window_is_skipped(self):
        u32 = _FakeU32({})
        snap = [{"hwnd": 777, "rect": (0, 0, 10, 10), "anchor": "A2"}]
        self.assertEqual(mb.win_follow(snap, (10, 0), set(), u32), 0)

    def test_switch_defaults_on_and_is_declared(self):
        self.assertTrue(mb.default_config()["window_follow"])
        self.assertEqual(mb.coerce_setting("window_follow", 0), False)


class RestoreSelectionTest(unittest.TestCase):
    """启动时默认勾选**全部**显示器（v2.01.10）。

    用户："打开的时候，应该默认勾选两个显示器，这样我可以方便的，一起调节亮度。"
    之前启动会沿用 `cfg["monitors"]`（只剩一块），每次都得先手动补勾。
    """

    def _app(self, n):
        return types.SimpleNamespace(monitors=[{"index": i} for i in range(n)],
                                     cfg={})

    def test_startup_selects_everything(self):
        self.assertEqual(mb.App._restore_selection(self._app(2), []), [0, 1])
        self.assertEqual(mb.App._restore_selection(self._app(3), []), [0, 1, 2])

    def test_refresh_keeps_current_selection(self):
        """刷新（改分辨率/插拔）不该把用户正在用的勾选洗掉。"""
        self.assertEqual(mb.App._restore_selection(self._app(2), [1]), [1])
        self.assertEqual(mb.App._restore_selection(self._app(3), [0, 2]), [0, 2])

    def test_stale_index_is_dropped(self):
        self.assertEqual(mb.App._restore_selection(self._app(2), [5]), [0, 1])


class RememberLayoutTest(TempConfigCase):
    """「记住当前显示器布局」—— 位置记录的唯一主动更新入口。

    背景（17:36 的日志）：交接主屏会把接班屏挪到桌面原点，之后每次断开都
    把"那一刻的位置"记进去，两块屏的记录全被写成 (0,0)，原布局丢失。
    """

    def setUp(self):
        super().setUp()
        self.cfg = mb.default_config()
        self.cfg["monitor_positions"] = {}
        self.saved = []
        self._old_save = mb.save_config

        def _fake_save(c):
            self.saved.append(dict(c))
            return True, ""

        mb.save_config = _fake_save

    def tearDown(self):
        mb.save_config = self._old_save
        super().tearDown()

    def _topo(self):
        """两块屏的最小拓扑：A1 在 (0,0)，A2 在 (-2560,0)。"""
        rows = [
            {"pathIndex": 0, "index": 0, "anchor": "A1", "active": True,
             "connected": True, "gdi": "\\\\.\\DISPLAY1", "friendly": "P1",
             "vdd": False, "srcId": 0, "tgtId": 100},
            {"pathIndex": 1, "index": 1, "anchor": "A2", "active": True,
             "connected": True, "gdi": "\\\\.\\DISPLAY2", "friendly": "P2",
             "vdd": False, "srcId": 1, "tgtId": 200},
        ]
        paths = (mb._CcdPathInfo * 2)()
        modes = (mb._CcdModeInfo * 4)()
        paths[0].sourceInfo.modeInfoIdx = (0 << 16) | 0xFFFF
        modes[0].sourceMode.width = 2560
        modes[0].sourceMode.height = 1440
        paths[1].sourceInfo.modeInfoIdx = (3 << 16) | 0xFFFF
        modes[3].sourceMode.width = 2560
        modes[3].sourceMode.height = 1440
        modes[3].sourceMode.position.x = -2560
        return paths, modes, 2, 4, rows

    def test_records_current_layout(self):
        paths, modes, pc, mc, rows = self._topo()
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           return_value=(paths, modes, pc, mc, rows)):
            layout, err = mb.remember_layout_snapshot(self.cfg)
        self.assertEqual(err, None)
        self.assertEqual(layout["A1"], [0, 0, 2560, 1440])
        self.assertEqual(layout["A2"], [-2560, 0, 2560, 1440])
        self.assertEqual(self.saved, [self.cfg])

    def test_reports_when_nothing_active(self):
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a: (None, None, 0, 0, [])):
            layout, err = mb.remember_layout_snapshot(self.cfg)
        self.assertIsNone(layout)
        self.assertIn("没有活动", err)


class WalkthroughRound2Test(TempConfigCase):
    """场景走查第 2 轮：探索会话 E1-01~E2-03 的回归测试。"""

    def test_schedule_never_writes_screen_state(self):
        """v2.01.0：定时任务只做动作，不往配置里记屏幕状态。

        （原 E1-01 的问题是"亮度成功也算熄屏成功 → 记成 off"。状态账整个
        取消了，这条回归改成：无论成败，apply_schedule 都不许写配置。）
        """
        cfg = mb.default_config()
        # 显式选 ddc：这条用例要的是"走 screen_off_cfg 这条路时也不写状态"，
        # 与默认值无关（v2.01.23 默认改 topology 后不能再依赖默认）。
        cfg["screen_off_method"] = "ddc"
        cfg["slots"] = mb._coerce_slots([{"time": "08:30", "level": 80, "screen": "off"}])
        with patch_any("active_slot_dict",
                                        return_value={"level": 80, "screen": "off"}), \
                patch_any("set_brightness_many", return_value=([0], [])), \
                patch_any("screen_off_cfg",
                                           return_value=([], [(-1, "DDC 写失败")])), \
                patch_any("save_config") as save:
            level, screen, ok, errs = mb.apply_schedule(cfg)
        save.assert_not_called()
        self.assertNotIn("screen_state", cfg)
        self.assertEqual(ok, [0])                # 亮度成功仍保留在返回值里
        self.assertTrue(errs)                    # 屏幕动作错误必须上报

    def test_save_failure_is_logged(self):
        """v2.03.0 P1：18 处调用里 7 处丢弃返回值，失败必须自己留痕。"""
        logged = []
        with unittest.mock.patch.object(cfg_mod, "_atomic_write_text",
                                        side_effect=OSError("磁盘只读")), \
                unittest.mock.patch.object(cfg_mod, "_write_event_log",
                                           side_effect=lambda m: logged.append(m)):
            ok, err = mb.save_config(mb.default_config())
        self.assertFalse(ok)
        self.assertIn("磁盘只读", err)
        self.assertEqual(len(logged), 1)
        self.assertIn("配置保存失败", logged[0])

    def test_save_failure_never_raises_when_log_also_fails(self):
        """"保存失败"不能升级成"程序崩溃"——日志再失败也要吞掉。"""
        with unittest.mock.patch.object(cfg_mod, "_atomic_write_text",
                                        side_effect=OSError("磁盘只读")), \
                unittest.mock.patch.object(cfg_mod, "_write_event_log",
                                           side_effect=OSError("日志也写不了")):
            ok, err = mb.save_config(mb.default_config())
        self.assertFalse(ok)
        self.assertIn("磁盘只读", err)

    def test_save_config_does_not_touch_legacy_dir(self):
        """v2.01.20（用户决策）：不迁移、不镜像，旧 APPDATA 目录一个字节不动。"""
        cfg = mb.default_config()
        cfg["tray_enabled"] = False
        self.assertTrue(mb.save_config(cfg)[0])
        self.assertFalse(os.path.exists(mb._LEGACY_CONFIG_DIR),
                         "不迁移策略下不能创建/写入旧目录")

    def test_old_config_present_detects_but_does_not_copy(self):
        """旧配置只在存在时被报告，绝不自动拷到程序目录。"""
        os.makedirs(mb._LEGACY_CONFIG_DIR, exist_ok=True)
        old = os.path.join(mb._LEGACY_CONFIG_DIR, "config.json")
        with open(old, "w", encoding="utf-8") as f:
            f.write('{"legacy": true}')
        self.assertEqual(mb.old_config_present(), old)
        self.assertFalse(os.path.exists(mb.CONFIG_FILE), "不能自动复制")
        # 程序目录已有配置时，不再提示
        self.assertTrue(mb.save_config(mb.default_config())[0])
        self.assertEqual(mb.old_config_present(), "")

    def test_save_config_is_pure_write(self):
        """v2.01.0：保存就是整份写盘，不再与磁盘合并运行态（已经没有运行态字段）。"""
        cfg = mb.default_config()
        self.assertTrue(mb.save_config(cfg)[0])
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(cfg, legacy_note="disk"), f)
        stale = dict(cfg)
        stale["tray_enabled"] = False            # 用户刚改的设置
        self.assertTrue(mb.save_config(stale)[0])
        with open(mb.CONFIG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertFalse(saved["tray_enabled"])              # 内存值生效
        self.assertNotIn("legacy_note", saved)               # 磁盘杂项不带进来
        self.assertNotIn("screen_state", saved)              # 不再写状态账

    def test_caps_type_error_does_not_wipe_config(self):
        """E2-02：caps 类型写错不应把整份配置判为读坏。"""
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"caps": ["bad"], "monitors": [1], "enabled": True}, f)
        cfg = mb.load_config()
        self.assertEqual(cfg_mod.CONFIG_LOAD_ERROR, "")
        self.assertEqual(cfg["monitors"], [1])   # 设置没被重置
        self.assertTrue(cfg["enabled"])

    def test_models_bad_key_is_skipped(self):
        """E2-03：models 非数字键不得让 GUI 构造崩溃。"""
        with open(mb.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"models": {"abc": "Dell", "1": "OK"}}, f)
        cfg = mb.load_config()
        parsed = {}
        for k, v in cfg.get("models", {}).items():
            try:
                parsed[int(k)] = v
            except (TypeError, ValueError):
                continue
        self.assertEqual(parsed, {1: "OK"})

    def test_cancel_relight_timer(self):
        """E1-05：手动点亮要取消尚未触发的自动复亮 timer。"""
        fake = type("F", (), {})()
        timer = unittest.mock.Mock()
        fake._relight_timer = timer
        fake._relight_pending = True
        mb.App._cancel_relight_timer(fake)
        timer.cancel.assert_called_once()
        self.assertFalse(fake._relight_pending)
        self.assertIsNone(fake._relight_timer)

    def test_poll_reentry_guard(self):
        """E1-03：重复轮询不得重入内层，避免累积 after 链。"""
        calls = []
        fake = type("F", (), {})()
        fake._polling = True
        fake._poll_read_queue_inner = lambda: calls.append("inner")
        mb.App._poll_read_queue(fake)
        self.assertEqual(calls, [])              # 重入被挡住
        fake._polling = False
        mb.App._poll_read_queue(fake)
        self.assertEqual(calls, ["inner"])       # 正常调用放行

    def test_force_realigns_tasks_when_disabled(self):
        """E2-04：enabled=False 时 --force 不许被吞掉，要做反向对齐（清残留任务）。"""
        cfg = mb.default_config()
        cfg["enabled"] = False
        with patch_any("unregister_tasks",
                                       return_value=(True, "OK")) as un, \
                patch_any("register_tasks") as reg:
            ok, msg = mb.ensure_task_registration(cfg, force=True)
        self.assertTrue(ok)
        self.assertIn("已清理", msg)
        un.assert_called_once()
        reg.assert_not_called()

    def test_disabled_without_force_touches_nothing(self):
        cfg = mb.default_config()
        cfg["enabled"] = False
        with patch_any("unregister_tasks") as un:
            ok, msg = mb.ensure_task_registration(cfg, force=False)
        self.assertTrue(ok)
        self.assertIn("跳过", msg)
        un.assert_not_called()

    def test_force_failure_is_reported_not_swallowed(self):
        cfg = mb.default_config()
        cfg["enabled"] = False
        with patch_any("unregister_tasks",
                                       return_value=(False, "Access denied")):
            ok, msg = mb.ensure_task_registration(cfg, force=True)
        self.assertFalse(ok)
        self.assertIn("清理失败", msg)

    def test_leaving_ddc_revokes_undeclared_permission(self):
        """E2-05：ddc_allow_undeclared 不是单向阀门，离开 ddc 就收回授权。"""
        cfg = dict(mb.default_config(), screen_off_method="ddc",
                   ddc_allow_undeclared=True)
        fake = types.SimpleNamespace(
            cfg=cfg,
            msg=types.SimpleNamespace(askyesno=lambda *a, **k: True),
            status_var=types.SimpleNamespace(set=lambda t: None),
            refresh_monitors=lambda silent=False: None,
            probe_screens=lambda: None)
        with patch_any("save_config", return_value=(True, "")):
            mb.App.apply_off_method(fake, "system")
        self.assertFalse(fake.cfg["ddc_allow_undeclared"])
        self.assertEqual(fake.cfg["screen_off_method"], "system")


class WalkthroughFixTest(TempConfigCase):
    """场景走查第 1 轮 P-01/P-02/P-03/P-04/P-05 的回归测试。"""

    class _Var:
        def __init__(self):
            self._v = ""

        def set(self, v):
            self._v = v

        def get(self):
            return self._v

    def _app_stub(self, enabled=True, save_ok=True):
        fake = type("F", (), {})()
        fake.cfg = mb.default_config()
        fake.cfg["enabled"] = enabled
        fake.status_var = self._Var()
        fake.flags = []
        fake.sch_panel = types.SimpleNamespace(
            set_enabled_flag=lambda v: fake.flags.append(v))
        saved = []

        def fake_save(cfg):
            saved.append(dict(cfg))
            return (True, "") if save_ok else (False, "disk fail")

        fake.msg = types.SimpleNamespace(
            showerror=lambda *a: None, showinfo=lambda *a: None,
            showwarning=lambda *a: None)
        fake._set_schedule_enabled = types.MethodType(mb.App._set_schedule_enabled, fake)
        fake._rollback_schedule = types.MethodType(mb.App._rollback_schedule, fake)
        return fake, fake_save, saved

    def test_schedule_enabled_single_writer_persists(self):
        fake, fake_save, saved = self._app_stub()
        with patch_any("save_config", side_effect=fake_save):
            ok = mb.App._set_schedule_enabled(fake, False, persist=True)
        self.assertTrue(ok)
        self.assertFalse(fake.cfg["enabled"])
        self.assertEqual(fake.flags, [False])
        self.assertEqual(saved[-1]["enabled"], False)   # 落盘了（P-01）

    def test_schedule_enabled_persist_failure_rolls_back_memory(self):
        fake, fake_save, _ = self._app_stub(enabled=True, save_ok=False)
        with patch_any("save_config", side_effect=fake_save):
            ok = mb.App._set_schedule_enabled(fake, False, persist=True)
        self.assertFalse(ok)
        self.assertTrue(fake.cfg["enabled"])            # 内存回滚
        self.assertEqual(fake.flags, [])                # 界面没说谎

    def test_tray_register_failure_uses_unified_rollback(self):
        fake, fake_save, saved = self._app_stub(enabled=True)
        with patch_any("save_config", side_effect=fake_save):
            mb.App._finish_tray_register(fake, (False, "boom"))
        self.assertFalse(fake.cfg["enabled"])
        self.assertEqual(fake.flags, [False])
        self.assertTrue(any(not c["enabled"] for c in saved))  # P-01 修复

    def test_cancel_failure_restores_enabled(self):
        fake, fake_save, saved = self._app_stub(enabled=True)
        fake.refresh_task_status = lambda: None
        with patch_any("save_config", side_effect=fake_save):
            mb.App._finish_unregister(fake, (False, "still running"))
        self.assertTrue(fake.cfg["enabled"])     # P-02：失败后配置与任务一致
        self.assertEqual(fake.flags, [True])

    def test_cli_apply_schedule_skips_when_disabled(self):
        cfg = mb.default_config()
        cfg["enabled"] = False
        with patch_any("load_config", return_value=cfg), \
                patch_any("apply_schedule") as apply:
            with self.assertRaises(SystemExit) as cm:
                mb.cli_apply_schedule()
        self.assertEqual(cm.exception.code, 0)
        apply.assert_not_called()               # P-02 护栏：残留任务不执行

    def test_probe_result_never_written_back_to_config(self):
        """v2.01.0：检测结果只用来渲染，不回写配置（程序不维护屏幕状态）。"""
        fake, fake_save, saved = self._app_stub()
        fake._probing = True
        fake._brighting = True
        fake.monitors = [{"index": 0}]
        fake.sch_panel = types.SimpleNamespace(
            set_enabled_flag=lambda v: None,
            set_hint_default=lambda t: None)
        fake.status_card = types.SimpleNamespace(
            render=lambda p: None, set_actions_enabled=lambda e: None)
        fake._last_verdict = {}
        fake._fit_window = lambda: None
        fake._status_payload = lambda s, h: {"state": s, "hint": h}
        with patch_any("screen_verdict",
                                        return_value={"state": "on",
                                                      "text": "t", "detail": "d"}), \
                patch_any("save_config", side_effect=fake_save):
            mb.App._render_states(fake, ([{"index": 0}], {}))
        self.assertNotIn("screen_state", fake.cfg)
        self.assertEqual(saved, [])              # 一次都不许落盘

    def test_rebuild_name_is_distinct_from_module_apply(self):
        self.assertTrue(callable(mb.App.rebuild_schedule_tasks))
        self.assertFalse(hasattr(mb.App, "apply_schedule"))  # P-05：不再同名


class ApplyCallbackTest(unittest.TestCase):
    """回归：App 曾有两个 _finish_apply，亮度回调被任务回调覆盖 → TypeError。"""

    def test_brightness_and_task_callbacks_have_distinct_names(self):
        self.assertTrue(callable(mb.App._finish_apply_level))
        self.assertTrue(callable(mb.App._finish_apply))
        sig_level = __import__("inspect").signature(mb.App._finish_apply_level)
        sig_task = __import__("inspect").signature(mb.App._finish_apply)
        self.assertIn("level", sig_level.parameters)
        self.assertIn("before_xml", sig_task.parameters)
        self.assertNotEqual(sig_level, sig_task)

    def test_apply_now_uses_brightness_callback(self):
        fake = type("F", (), {})()
        fake._busy = False
        fake.selected_indices = lambda: [0]
        fake.bri_panel = types.SimpleNamespace(level=lambda: 80)
        fake.status_var = BusyQueueTest._Var()
        seen = {}
        fake.run_bg = lambda job, cb, busy=False: seen.update(cb=cb, busy=busy)
        mb.App.apply_now(fake)
        self.assertTrue(seen.get("busy"))
        self.assertIn("正在下发亮度", fake.status_var.get())
        # 回调带 level 能直接调用（旧代码这里会 TypeError 缺 before_xml）
        fake._finish_apply_level = lambda result, level: seen.update(result=result, level=level)
        seen["cb"](([0], []))
        self.assertEqual(seen["level"], 80)
        self.assertEqual(seen["result"], ([0], []))


class _TopoFixture(TempConfigCase):
    """拓扑动作层测试的共用夹具。

    单独抽出来当基类（不含 test_*），是为了让下面两个测试类共享同一份
    假拓扑而**不重复执行**彼此的用例。
    """

    ROWS_RAW = [
        {"pathIndex": 0, "index": 0, "anchor": "A1", "active": True, "connected": True,
         "gdi": "\\\\.\\DISPLAY1", "friendly": "P1", "vdd": False,
         "srcId": 0, "tgtId": 100},
        {"pathIndex": 1, "index": 1, "anchor": "A2", "active": True, "connected": True,
         "gdi": "\\\\.\\DISPLAY2", "friendly": "P2", "vdd": False,
         "srcId": 1, "tgtId": 200},
        {"pathIndex": 2, "index": 2, "anchor": "V1", "active": False, "connected": True,
         "gdi": "\\\\.\\DISPLAY92", "friendly": "VDD by MTT", "vdd": True,
         "srcId": 2, "tgtId": 300},
    ]
    TARGETS = [{"index": 0, "dev": "DISPLAY1", "primary": True},
               {"index": 1, "dev": "DISPLAY2", "primary": False}]

    def _cfg(self, **kw):
        cfg = mb.default_config()
        cfg.update(kw)
        return cfg

    @staticmethod
    def _modes():
        """造 modes 数组：source/target/desktop 各两条，id 与 ROWS_RAW 对齐。

        索引 → (infoType, id)：0→(1,0) 1→(2,100) 2→(3,100)
                              3→(1,1) 4→(2,200) 5→(3,200)
        """
        modes = (mb._CcdModeInfo * 6)()
        for i, (info_type, ident) in enumerate(
                [(1, 0), (2, 100), (3, 100), (1, 1), (2, 200), (3, 200)]):
            modes[i].infoType = info_type
            modes[i].id = ident
        return modes

    def _raw(self):
        paths = (mb._CcdPathInfo * 3)()
        modes = self._modes()
        paths[0].flags = mb._CCD_PATH_ACTIVE
        paths[1].flags = mb._CCD_PATH_ACTIVE
        for p in paths:
            p.targetInfo.targetAvailable = 1
        return (paths, modes, 3, 6, [dict(r) for r in self.ROWS_RAW]), paths

    def _off_raw(self):
        """断开后的拓扑：只剩主屏 ACTIVE，A2 有一条 source=1 空闲的 path。"""
        paths = (mb._CcdPathInfo * 4)()
        modes = self._modes()
        rows = [dict(r) for r in self.ROWS_RAW]
        rows[1].update(active=False, gdi="")
        rows.append({"pathIndex": 3, "index": -1, "anchor": "A2", "active": False,
                     "connected": True, "gdi": "", "friendly": "P2", "vdd": False,
                     "srcId": 1, "tgtId": 200})
        paths[0].flags = mb._CCD_PATH_ACTIVE
        for p in paths:
            p.targetInfo.targetAvailable = 1
        return paths, modes, 4, 6, rows

    def _base_patches(self, fake_query, apply_rc=0, verify=True):
        return (
            patch_any("_ccd_api", return_value=object()),
            patch_any("_ccd_query_raw", side_effect=fake_query),
            patch_any("_ccd_apply", return_value=apply_rc),
            patch_any("_ccd_wait_state", return_value=verify),
            patch_any("enumerate_targets",
                                       return_value=list(self.TARGETS)),
            patch_any("release_targets"),
        )

class TopologyActionTest(_TopoFixture):
    """E3/E4：拓扑动作层全部 monkeypatch，绝不碰真机屏幕。"""

    def test_topology_is_default_off_method(self):
        """v2.01.23：拓扑断开成为默认（原用例名是"新增选项但不改默认"）。"""
        self.assertIn("topology", mb.OFF_METHODS)
        self.assertEqual(mb.coerce_setting("screen_off_method", "topology"), "topology")
        self.assertEqual(mb.default_config()["screen_off_method"], "topology")

    def test_plan_hands_over_primary_to_other_physical_screen(self):
        """只断主屏时，接班屏优先**物理屏**（用户看得见），而不是虚拟屏。"""
        rows = [dict(r) for r in self.ROWS_RAW]
        plan, errs, noop = mb.topology_plan_off([0], rows, ["DISPLAY1"])
        self.assertEqual(errs, [])
        self.assertFalse(noop)
        self.assertEqual(plan["off"], ["A1"])
        self.assertEqual(plan["heir"], "A2")
        self.assertTrue(plan["heir_active"])

    def test_plan_hands_over_primary_to_vdd_when_all_physical_off(self):
        """全部物理屏都断开 → 交给虚拟显示器（串流的形态）。"""
        rows = [dict(r) for r in self.ROWS_RAW]
        plan, errs, _ = mb.topology_plan_off([0, 1], rows, ["DISPLAY1"])
        self.assertEqual(errs, [])
        self.assertEqual(plan["off"], ["A1", "A2"])
        self.assertEqual(plan["heir"], "V1")
        self.assertFalse(plan["heir_active"])       # 它当前断着，要先接回

    def test_plan_refuses_when_nothing_left_to_carry_primary(self):
        """断开后一块屏都不剩 = 黑屏，必须拒绝（没有虚拟屏可接班时）。"""
        rows = [dict(r) for r in self.ROWS_RAW if not r["vdd"]]
        plan, errs, _ = mb.topology_plan_off([0, 1], rows, ["DISPLAY1"])
        self.assertEqual(plan["off"], [])
        self.assertTrue(any("一块屏都不剩" in e for e in errs))

    def test_plan_never_targets_vdd(self):
        rows = [dict(r) for r in self.ROWS_RAW]
        plan, errs, _ = mb.topology_plan_off([2], rows, ["DISPLAY1"])
        self.assertEqual(plan["off"], [])
        self.assertTrue(any("没有可断开的屏" in e for e in errs))

    def test_plan_honours_explicit_heir_and_rejects_missing_one(self):
        rows = [dict(r) for r in self.ROWS_RAW]
        plan, errs, _ = mb.topology_plan_off([0], rows, ["DISPLAY1"], heir="V1")
        self.assertEqual(errs, [])
        self.assertEqual(plan["heir"], "V1")
        # 指了块不在候选里的（刚被拔掉）
        bad, errs2, _ = mb.topology_plan_off([0], rows, ["DISPLAY1"], heir="A9")
        self.assertTrue(errs2)
        self.assertEqual(bad["off"], [])

    def test_plan_noop_when_target_already_inactive(self):
        rows = [dict(r) for r in self.ROWS_RAW]
        rows[1]["active"] = False
        plan, errs, noop = mb.topology_plan_off([1], rows, ["DISPLAY1"])
        self.assertEqual(plan["off"], [])
        self.assertFalse(errs)
        self.assertTrue(noop)

    # ---- v2.03.0 P0：topology_on 原为零覆盖，重构前必须先钉住 ----
    def _on_patches(self, fake_query, apply_rc=0, verify=True, restored=None):
        """topology_on 需要的全套 mock（含现造 source mode 与索引回映）。"""
        return self._base_patches(fake_query, apply_rc=apply_rc, verify=verify) + (
            patch_any(
                "_ccd_fill_missing_source_modes",
                side_effect=lambda u32, p, m, mc, rows, plan, want=None: (m, mc)),
            patch_any(
                "_ccd_apply_plan_on",
                return_value=list(restored or ["A2"])),
            patch_any(
                "topology_indices_for_anchors", return_value=[1]),
        )

    def test_on_restores_inactive_screen(self):
        """接回断开的屏：提交一次；返回 (下标, 错误, 接回锚点)。"""
        ps = self._on_patches(lambda u32=None: self._off_raw())
        with ps[0], ps[1], ps[2] as apply, ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
            ok, errs, restored = mb.topology_on(["A2"], {})
        self.assertEqual(errs, [])
        self.assertEqual(restored, ["A2"])
        self.assertEqual(ok, [1])
        self.assertEqual(apply.call_count, 1)

    def test_on_skips_already_active(self):
        """已经亮着的锚点 = noop：不提交拓扑，也不报错（幂等）。"""
        ps = self._on_patches(lambda u32=None: self._raw()[0])
        with ps[0], ps[1], ps[2] as apply, ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
            ok, errs, restored = mb.topology_on(["A1"], {})
        self.assertEqual((ok, errs, restored), ([], [], []))
        self.assertEqual(apply.call_count, 0, "noop 不该提交")

    def test_on_rejects_unknown_anchor(self):
        """锚点对不上任何已连接 path（刚被拔线）→ 明确报错，不瞎提交。"""
        ps = self._on_patches(lambda u32=None: self._off_raw())
        with ps[0], ps[1], ps[2] as apply, ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
            ok, errs, restored = mb.topology_on(["NOPE"], {})
        self.assertEqual(ok, [])
        self.assertTrue(errs)
        self.assertEqual(apply.call_count, 0)

    def test_on_without_ccd_reports_windows_only(self):
        with patch_any("_ccd_api", return_value=None):
            ok, errs, restored = mb.topology_on(["A1"])
        self.assertEqual((ok, restored), ([], []))
        self.assertIn("仅支持 Windows", errs[0][1])

    def test_on_verify_timeout_is_not_success(self):
        """提交 rc=0 但 2 秒内回读没确认 ACTIVE → 必须报错。

        否则会接着写亮度，而屏其实没接回来（用户看到"点亮了但黑屏"）。
        """
        ps = self._on_patches(lambda u32=None: self._off_raw(), verify=False)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
            ok, errs, restored = mb.topology_on(["A2"], {})
        self.assertEqual((ok, restored), ([], []))
        self.assertTrue(errs)
        self.assertIn("回读未确认", errs[0][1])

    def test_off_snapshots_in_memory_then_applies(self):
        """v2.01.0：断开只把内存快照拍在**动作前**（用于本次失败回滚），不落盘。"""
        seen = {}

        def fake_query(u32=None):
            raw, paths = self._raw()
            seen["original"] = bytes(paths).hex()
            return raw

        def fake_snap(paths, modes, pc, mc, anchors):
            seen["snap_paths"] = bytes(paths).hex()
            seen["snap_anchors"] = list(anchors)
            return {"pathCount": pc, "modeCount": mc, "pathsHex": "",
                    "modesHex": "", "targetAnchors": list(anchors)}

        patches = self._base_patches(fake_query)
        with patches[0], patches[1], patches[2] as apply, patches[3], patches[4], patches[5], \
                patch_any("_ccd_make_snapshot",
                                           side_effect=fake_snap):
            ok, errs = mb.topology_off([1])
        self.assertEqual(ok, [1])
        self.assertEqual(errs, [])
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(seen["snap_anchors"], ["A2"])
        # 快照必须来自动作之前：ACTIVE 位还在，且路径未被置 INVALID
        self.assertEqual(seen["snap_paths"], seen["original"])

    def test_off_hands_over_primary_then_removes_it(self):
        """断开主屏 = 一次提交里「接班屏挪到 (0,0) + 原主屏摘掉」。

        Windows 没有"设置主显示器"的 API，主屏就是 (0,0) 那块。真机验证过
        （scripts/topology_handover_probe.py，SDC_VALIDATE）：这样提交 rc=0。
        """
        cfg = self._cfg(screen_off_method="topology")

        def fake_query(u32=None):
            (paths, modes, pc, mc, rows), _ = self._raw()
            # A2 的 source mode 指向 modes[3]，原来摆在主屏左边
            paths[1].sourceInfo.modeInfoIdx = (3 << 16) | 0xFFFF
            modes[3].sourceMode.position.x = -2560
            modes[0].sourceMode.width = 2560        # A1 自己的 source mode（用于记位置）
            modes[0].sourceMode.height = 1440
            return paths, modes, pc, mc, rows

        patches = self._base_patches(fake_query)
        with patches[0], patches[1], patches[2] as apply, patches[3], patches[4], patches[5]:
            ok, errs = mb.topology_off([0], cfg, heir="A2")
        self.assertEqual(errs, [])
        self.assertEqual(ok, [0])
        args = apply.call_args[0]
        paths, modes = args[1], args[2]
        # 接班屏被平移到桌面原点 → 它就是新的主屏
        self.assertEqual(int(modes[3].sourceMode.position.x), 0)
        self.assertEqual(int(modes[3].sourceMode.position.y), 0)
        # 原主屏被摘掉
        self.assertFalse(paths[0].flags & mb._CCD_PATH_ACTIVE)
        self.assertEqual(int(paths[0].sourceInfo.modeInfoIdx), 0xFFFFFFFF)
        # 位置记录按**平移后**的坐标存：接回时正好落在新主屏右边
        self.assertEqual(cfg["monitor_positions"]["A1"][:2], [2560, 0])

    def test_off_rolls_back_once_when_verify_fails(self):
        def fake_query(u32=None):
            return self._raw()[0]

        rollback = []
        patches = self._base_patches(fake_query, verify=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                patch_any("_ccd_make_snapshot",
                                           return_value={"pathCount": 3, "modeCount": 3,
                                                         "pathsHex": "", "modesHex": "",
                                                         "targetAnchors": ["A2"]}), \
                patch_any(
                    "_ccd_restore_from_bytes",
                    side_effect=lambda u32, snap: rollback.append(snap) or 0):
            ok, errs = mb.topology_off([1])
        self.assertEqual(ok, [])
        self.assertEqual(len(rollback), 1)
        self.assertIn("已回滚", errs[0][1])

    def test_off_never_applies_when_plan_rejected(self):
        """护栏拦下时，一次 SetDisplayConfig 都不许发。"""
        def fake_query(u32=None):
            return self._raw()[0]

        patches = self._base_patches(fake_query)
        with patches[0], patches[1], patches[2] as apply, patches[3], patches[4], patches[5]:
            ok, errs = mb.topology_off([2])          # VDD：永不作目标
        self.assertEqual(ok, [])
        self.assertEqual(apply.call_count, 0)
        self.assertIn("没有可断开的屏", errs[0][1])

    def test_on_plans_from_current_topology_then_writes_brightness(self):
        """接回 = 现读当前拓扑 → 现算 mode → 提交 → 回读 → 写亮度。"""
        calls = []
        cfg = self._cfg(screen_off_method="topology")
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_apply",
                                           side_effect=lambda *a: calls.append("apply") or 0), \
                patch_any("_ccd_wait_state",
                                           side_effect=lambda *a, **k: calls.append("verify") or True), \
                patch_any("topology_indices_for_anchors",
                                           return_value=[1]), \
                patch_any("current_level", return_value=80), \
                patch_any("set_brightness_many",
                                           side_effect=lambda idx, lv: (
                                               calls.append(("brightness", idx, lv)), ([1], []))[1]):
            ok, errs = mb.screen_on_cfg(cfg, [1])
        self.assertEqual(errs, [])
        self.assertEqual(ok, [1, 1])
        self.assertEqual(calls[:2], ["apply", "verify"])
        self.assertEqual(calls[2], ("brightness", [1], 80))

    def test_reconnect_writes_brightness_to_newly_restored_screen(self):
        """回归：断开源接回后才回到 EnumDisplayMonitors，不能拿断开前的旧序号写亮度。"""
        cfg = self._cfg(screen_off_method="topology")
        # 断开期间算出的 indices 只有主屏 [0]；接回后 A2 重新成为 [1]。
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_apply", return_value=0), \
                patch_any("_ccd_wait_state", return_value=True), \
                patch_any("topology_indices_for_anchors",
                                           return_value=[1]), \
                patch_any("current_level", return_value=80), \
                patch_any("set_brightness_many") as bright:
            bright.return_value = ([0, 1], [])
            ok, errs = mb.screen_on_cfg(cfg, [0])
        self.assertEqual(errs, [])
        self.assertEqual(ok, [1, 0, 1])
        bright.assert_called_once_with([0, 1], 80)

    def test_anchor_to_index_uses_active_gdi_only(self):
        """aliases 会混入另一块屏的 DISPLAYn；映射下标只认 ACTIVE source 的 gdi。"""
        rows = [
            {"anchor": "A1", "gdi": "\\\\.\\DISPLAY1", "vdd": False,
             "aliases": ["\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2"]},
            {"anchor": "A2", "gdi": "\\\\.\\DISPLAY2", "vdd": False,
             "aliases": ["\\\\.\\DISPLAY2", "\\\\.\\DISPLAY1"]},
        ]
        with patch_any("enumerate_targets",
                                        return_value=list(self.TARGETS)), \
                patch_any("release_targets"), \
                patch_any("ccd_probe_topology", return_value=rows):
            self.assertEqual(mb.topology_indices_for_anchors(["A2"]), [1])

    def test_on_does_not_write_brightness_when_apply_fails(self):
        cfg = self._cfg(screen_off_method="topology")
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_apply", return_value=87), \
                patch_any("set_brightness_many") as bright:
            ok, errs = mb.screen_on_cfg(cfg, [1])
        self.assertEqual(ok, [])
        self.assertIn("rc=87", errs[0][1])
        bright.assert_not_called()

    def test_on_does_not_write_brightness_when_verify_fails(self):
        cfg = self._cfg(screen_off_method="topology")
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_apply", return_value=0), \
                patch_any("_ccd_wait_state", return_value=False), \
                patch_any("set_brightness_many") as bright:
            ok, errs = mb.screen_on_cfg(cfg, [1])
        self.assertEqual(ok, [])
        self.assertIn("回读未确认", errs[0][1])
        bright.assert_not_called()


class TopologySubmitShapeTest(_TopoFixture):
    """v2.01.1：钉住"提交什么形状"，这是 rc=87 的唯一开关。

    三条真机定论（SDC_VALIDATE 验的，scripts/topology_repro87.py 可复跑）：
      - sourceInfo.modeInfoIdx=INVALID → 必 87，补多少旗标都救不回来；
      - targetInfo.modeInfoIdx=INVALID → rc=0，可以交 Windows 定；
      - 整份 QDC_ALL_PATHS（233 条）全量提交 → rc=0，不是 rc=87 的原因。
        曾经据此改过 SDC_TOPOLOGY_SUPPLIED 部分提交，实测那才是 87，已撤销。
    """

    class _Fn:
        """够 `_ccd_apply` 用的假 SetDisplayConfig（能挂 argtypes、能记 flags）。"""

        def __init__(self, rc=0):
            self.argtypes = None
            self.restype = None
            self.seen = None
            self.rc = rc

        def __call__(self, *a):
            self.seen = a
            return self.rc

    def _fake_u32(self, rc=0):
        fn = self._Fn(rc)
        return types.SimpleNamespace(SetDisplayConfig=fn), fn

    def test_ccd_apply_always_uses_one_submit_mode(self):
        """只有一种提交方式：SDC_USE_SUPPLIED 全量，且绝不带 SDC_TOPOLOGY_SUPPLIED。"""
        u32, fn = self._fake_u32()
        paths = (mb._CcdPathInfo * 2)()
        mb._ccd_apply(u32, paths, (mb._CcdModeInfo * 1)(), 2, 1)
        flags = fn.seen[-1]
        self.assertTrue(flags & mb._SDC_USE_SUPPLIED)
        self.assertFalse(flags & 0x00000010)      # SDC_TOPOLOGY_SUPPLIED
        self.assertFalse(hasattr(mb, "_CCD_PARTIAL_FLAGS"))
        self.assertFalse(hasattr(mb, "_ccd_plan_paths"))

    def test_plan_marks_source_mode_for_synthesis_when_missing(self):
        """真实断开后 source mode 必然不在 modes 数组里 —— 不是错误，是要现造。"""
        rows = self._off_raw()[4]
        plan, errs, noop = mb.topology_plan_on(["A2"], rows, [])
        self.assertEqual(errs, [])
        self.assertFalse(noop)
        self.assertEqual(len(plan), 1)
        self.assertIsNone(plan[0]["sourceModeIdx"])
        self.assertTrue(plan[0]["makeSourceMode"])

    def test_plan_allows_missing_target_mode(self):
        """target/desktop mode 查不到不算错，交 Windows 定。"""
        rows = self._off_raw()[4]
        entries = [(3, 1, 1)]                     # 只给 source(1,id=1)
        plan, errs, _ = mb.topology_plan_on(["A2"], rows, entries)
        self.assertEqual(errs, [])
        self.assertEqual(plan[0]["sourceModeIdx"], 3)
        self.assertIsNone(plan[0]["targetModeIdx"])
        self.assertIsNone(plan[0]["desktopModeIdx"])

    def test_apply_plan_never_writes_invalid_source(self):
        """source mode 缺失时宁可不写这条 path，也不写 INVALID 去换 rc=87。"""
        paths = (mb._CcdPathInfo * 1)()
        plan = [{"pathIndex": 0, "anchor": "A2", "sourceModeIdx": None,
                 "targetModeIdx": 4, "desktopModeIdx": 5}]
        done = mb._ccd_apply_plan_on(paths, plan)
        self.assertEqual(done, [])
        self.assertEqual(int(paths[0].sourceInfo.modeInfoIdx), 0)   # 没被动过
        self.assertFalse(paths[0].flags & mb._CCD_PATH_ACTIVE)

    def test_apply_plan_writes_invalid_target_only(self):
        paths = (mb._CcdPathInfo * 1)()
        plan = [{"pathIndex": 0, "anchor": "A2", "sourceModeIdx": 3,
                 "targetModeIdx": None, "desktopModeIdx": None}]
        mb._ccd_apply_plan_on(paths, plan)
        self.assertEqual(int(paths[0].sourceInfo.modeInfoIdx), 0x0003FFFF)
        self.assertEqual(int(paths[0].targetInfo.modeInfoIdx), 0xFFFFFFFF)


class TopologySourceModeSynthesisTest(_TopoFixture):
    """v2.01.2：真实断开后 Windows 会把该屏的 SOURCE mode 从 modes 数组里摘掉，
    接回时没有任何下标可引用。只能**自己补一条**。

    真机矩阵（scripts/topology_mode_synthesis.py，SDC_VALIDATE，只读）：
      source=INVALID / 借用别的屏的 source mode / pf=32BPP / 位置与主屏重叠 → 87
      自己补一条 + pf=NONGDI + 位置不与现有屏重叠                       → 0
    """

    def _active_layout(self):
        """一块 2560x1440 活在 (0,0)，mo=des 数组里它能被引用。"""
        paths = (mb._CcdPathInfo * 2)()
        modes = (mb._CcdModeInfo * 4)()
        paths[0].flags = mb._CCD_PATH_ACTIVE
        paths[0].sourceInfo.modeInfoIdx = ((1 << 16) | 0xFFFF)
        paths[0].targetInfo.targetAvailable = 1
        modes[1].infoType = mb._CCD_TYPE_SOURCE
        modes[1].id = 0
        modes[1].sourceMode.width = 2560
        modes[1].sourceMode.height = 1440
        modes[1].sourceMode.position.x = 0
        modes[1].sourceMode.position.y = 0
        rows = [{"pathIndex": 0, "anchor": "A1", "active": True, "connected": True,
                 "vdd": False, "srcId": 0, "tgtId": 100, "luidLow": 7},
                {"pathIndex": 1, "anchor": "A2", "active": False, "connected": True,
                 "vdd": False, "srcId": 1, "tgtId": 200, "luidLow": 7}]
        return paths, modes, 2, 4, rows

    def test_append_source_mode_uses_nongdi_and_does_not_touch_original(self):
        modes = self._modes()
        before = bytes(modes).hex()
        grown, idx = mb._ccd_append_source_mode(modes, 6, 0x00010CBD, 0, 2,
                                                2560, 1440, 2560, 0)
        self.assertEqual(idx, 6)
        self.assertEqual(len(grown), 7)
        self.assertEqual(int(grown[idx].infoType), mb._CCD_TYPE_SOURCE)
        self.assertEqual(int(grown[idx].id), 2)
        self.assertEqual(int(grown[idx].adapterId.LowPart), 0x00010CBD)
        self.assertEqual(int(grown[idx].sourceMode.width), 2560)
        self.assertEqual(int(grown[idx].sourceMode.height), 1440)
        # Windows 自己给 source mode 填的就是 NONGDI；填 32BPP 会被拒
        self.assertEqual(int(grown[idx].sourceMode.pixelFormat),
                         mb._CCD_PIXELFORMAT_NONGDI)
        self.assertEqual(int(grown[idx].sourceMode.position.x), 2560)
        self.assertEqual(bytes(modes).hex(), before)     # 原数组没被带脏

    def test_free_origin_is_right_edge_of_current_desktop(self):
        paths, modes, _pc, _mc, rows = self._active_layout()
        self.assertEqual(mb._ccd_free_origin(rows, paths, modes), 2560)

    def test_live_size_copies_an_active_screen(self):
        paths, modes, _pc, _mc, rows = self._active_layout()
        self.assertEqual(mb._ccd_live_size(rows, paths, modes), (2560, 1440))

    def test_live_size_falls_back_when_nothing_active(self):
        paths, modes, _pc, _mc, rows = self._active_layout()
        rows[0]["active"] = False
        self.assertEqual(mb._ccd_live_size(rows, paths, modes), (1920, 1080))

    def test_on_synthesizes_source_mode_and_grows_mode_array(self):
        """端到端：plan 缺 source mode → 造一条 → 提交时 mc 多 1、下标指向新项。"""
        seen = {}
        cfg = self._cfg(screen_off_method="topology")

        def fake_apply(u32, paths, modes, pc, mc):
            seen["mc"] = mc
            seen["modes"] = modes
            seen["srcIdx"] = int(paths[1].sourceInfo.modeInfoIdx)
            return 0

        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_mode_entries", return_value=[]), \
                patch_any("_ccd_preferred_size",
                                           return_value=(2560, 1440)), \
                patch_any("_ccd_apply", side_effect=fake_apply), \
                patch_any("_ccd_wait_state", return_value=True), \
                patch_any("topology_indices_for_anchors",
                                           return_value=[1]), \
                patch_any("current_level", return_value=80), \
                patch_any("set_brightness_many",
                                           return_value=([1], [])), \
                patch_any("_write_event_log") as evt:
            ok, errs = mb.screen_on_cfg(cfg, [1])
        self.assertEqual(errs, [])
        self.assertEqual(seen["mc"], 7)                       # 原 6 条 + 补 1 条
        self.assertEqual(seen["srcIdx"], (6 << 16) | 0xFFFF)  # 指向新补的 modes[6]
        self.assertEqual(int(seen["modes"][6].sourceMode.width), 2560)
        self.assertEqual(int(seen["modes"][6].sourceMode.pixelFormat),
                         mb._CCD_PIXELFORMAT_NONGDI)
        # 成功的接回不往 events.log 写实现细节（那是 diag.log 的事）
        evt.assert_not_called()

    def test_on_falls_back_to_windows_target_choice(self):
        """降级链只有两档：现算三元组 → source 现算 + target/desktop 交 Windows。"""
        seq = []
        logs = []

        def fake_apply(u32, paths, modes, pc, mc):
            seq.append((pc, int(paths[1].sourceInfo.modeInfoIdx),
                        int(paths[1].targetInfo.modeInfoIdx)))
            return 87 if len(seq) < 2 else 0

        cfg = self._cfg(screen_off_method="topology")
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_apply", side_effect=fake_apply), \
                patch_any("_ccd_wait_state", return_value=True), \
                patch_any("topology_indices_for_anchors",
                                           return_value=[1]), \
                patch_any("current_level", return_value=80), \
                patch_any("set_brightness_many",
                                           return_value=([1], [])), \
                patch_any("_write_event_log",
                                           side_effect=logs.append):
            ok, errs = mb.screen_on_cfg(cfg, [1])
        self.assertEqual(errs, [])
        self.assertEqual(ok, [1, 1])
        self.assertEqual(len(seq), 2)
        # 两次都交整份数组（pc 不变），source mode 两次都是有效值
        self.assertEqual(seq[0][0], seq[1][0])
        self.assertNotEqual(seq[0][1], 0xFFFFFFFF)
        self.assertNotEqual(seq[1][1], 0xFFFFFFFF)
        self.assertEqual(seq[0][1], seq[1][1])
        # 第一档给了 target mode，第二档交 Windows
        self.assertNotEqual(seq[0][2], 0xFFFFFFFF)
        self.assertEqual(seq[1][2], 0xFFFFFFFF)
        # 降级必须真的被拒过一次才写日志，且文案是人话（不甩参数名）
        self.assertEqual(len(logs), 1)
        self.assertIn("rc=87", logs[0])
        self.assertIn("重试", logs[0])
        self.assertNotIn("modeInfoIdx", logs[0])

    def test_on_first_tier_success_writes_no_warning(self):
        """回归：旧代码在第一档里无条件写"现算 mode 被拒"，一次成功也说降级过。"""
        logs = []
        cfg = self._cfg(screen_off_method="topology")
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_apply", return_value=0), \
                patch_any("_ccd_wait_state", return_value=True), \
                patch_any("topology_indices_for_anchors",
                                           return_value=[1]), \
                patch_any("current_level", return_value=80), \
                patch_any("set_brightness_many",
                                           return_value=([1], [])), \
                patch_any("_write_event_log",
                                           side_effect=logs.append):
            mb.screen_on_cfg(cfg, [1])
        self.assertEqual(logs, [])


class TopologySnapshotShapeTest(_TopoFixture):
    """快照只许存"用到的那些元素"。存多了回滚时会越界写坏堆（实测段错误）。

    `GetDisplayConfigBufferSizes` 给的是**上限**（本机 modes 699 条），
    `QueryDisplayConfig` 只填真正用到的（本机 6 条）并把计数改回 6。
    以前用 `bytes(modes)` 存了整个 699 条，回滚按 6 条建数组再 memmove
    44736 字节 → 堆越界 → 最后在某个 print 里崩成 access violation。
    """

    def test_snapshot_only_captures_used_elements(self):
        paths = (mb._CcdPathInfo * 10)()          # 分配 10 条
        modes = (mb._CcdModeInfo * 40)()          # 分配 40 条
        snap = mb._ccd_make_snapshot(paths, modes, 3, 2, [])
        self.assertEqual(len(bytes.fromhex(snap["pathsHex"])),
                         3 * ctypes.sizeof(mb._CcdPathInfo))
        self.assertEqual(len(bytes.fromhex(snap["modesHex"])),
                         2 * ctypes.sizeof(mb._CcdModeInfo))
        self.assertEqual((snap["pathCount"], snap["modeCount"]), (3, 2))

    def test_restore_refuses_mismatched_snapshot(self):
        """长度对不上就一个字都不写：宁可少一次回滚，也不要写坏堆。"""

        class _U:
            def SetDisplayConfig(self, *a):
                raise AssertionError("形状不符时不该提交")

        snap = {"pathCount": 2, "modeCount": 2,
                "pathsHex": "00" * 10, "modesHex": "00" * 10}
        self.assertEqual(mb._ccd_restore_from_bytes(_U(), snap), -1)

    def test_snapshot_roundtrip_keeps_bytes(self):
        paths = (mb._CcdPathInfo * 4)()
        modes = (mb._CcdModeInfo * 3)()
        paths[0].sourceInfo.id = 7
        modes[0].id = 11
        snap = mb._ccd_make_snapshot(paths, modes, 4, 3, [])
        self.assertEqual(bytes.fromhex(snap["pathsHex"])[:16],
                         bytes(mb._ccd_slice_bytes(paths, 1))[:16])
        self.assertEqual(len(bytes.fromhex(snap["modesHex"])),
                         3 * ctypes.sizeof(mb._CcdModeInfo))


class TopologyPositionTest(_TopoFixture):
    """v2.01.3：接回要摆回**原位置**，不能每次都跳到最右边。

    断开之后 Windows 会把该屏的 mode 项摘掉，位置就再也读不到，所以只能在
    断开前抓一次（`monitor_positions`）。这不是状态账：每次断开前都按当下
    真实值重写，用户挪过屏这里就跟着变。
    """

    def _raw_with_pos(self):
        (paths, modes, pc, mc, rows), _ = self._raw()
        paths[1].sourceInfo.modeInfoIdx = (3 << 16) | 0xFFFF
        modes[3].sourceMode.width = 1920
        modes[3].sourceMode.height = 1080
        modes[3].sourceMode.position.x = -1920
        modes[3].sourceMode.position.y = 0
        return paths, modes, pc, mc, rows

    def test_coerce_positions_drops_bad_entries(self):
        got = mb._coerce_positions({
            "A": [1, 2, 3, 4], "B": [0, 0, 0, 100], "C": "x",
            "D": [1, 2, 3], "E": [1.4, 2.6, 100, 200]})
        self.assertEqual(sorted(got), ["A", "E"])
        self.assertEqual(got["E"], [1, 3, 100, 200])
        self.assertEqual(mb._coerce_positions(None), {})
        self.assertEqual(mb.default_config()["monitor_positions"], {})

    def test_place_prefers_saved_position_when_free(self):
        rects = [(0, 0, 2560, 1440)]
        self.assertEqual(mb._ccd_place((-1920, 0), 1920, 1080, rects), (-1920, 0))

    def test_place_falls_back_to_right_edge_on_overlap(self):
        """重叠必须避开：实测把新屏放在主屏位置，SetDisplayConfig 直接 rc=87。"""
        rects = [(0, 0, 2560, 1440)]
        self.assertEqual(mb._ccd_place((0, 0), 1920, 1080, rects), (2560, 0))

    def test_place_without_saved_goes_right(self):
        self.assertEqual(mb._ccd_place(None, 1920, 1080, [(0, 0, 2560, 1440)]),
                         (2560, 0))

    def test_off_captures_position_before_disconnecting(self):
        cfg = self._cfg(screen_off_method="topology")
        patches = self._base_patches(lambda *a, **k: self._raw_with_pos())
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            ok, errs = mb.topology_off([1], cfg)
        self.assertEqual(errs, [])
        self.assertEqual(cfg["monitor_positions"],
                         {"A2": [-1920, 0, 1920, 1080]})

    def test_off_without_cfg_does_not_write_config(self):
        """没给 cfg 时不能顺手往配置里塞东西（CLI 不该有副作用）。"""
        patches = self._base_patches(lambda *a, **k: self._raw_with_pos())
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            ok, errs = mb.topology_off([1])
        self.assertEqual(errs, [])
        self.assertEqual(mb.load_config().get("monitor_positions"), {})

    def test_on_reuses_saved_position_when_synthesizing(self):
        cfg = self._cfg(screen_off_method="topology")
        cfg["monitor_positions"] = {"A2": [-1920, 0, 1920, 1080]}
        seen = {}

        def fake_apply(u32, paths, modes, pc, mc):
            sm = modes[6].sourceMode
            seen["rect"] = (int(sm.position.x), int(sm.position.y),
                            int(sm.width), int(sm.height))
            return 0

        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_mode_entries", return_value=[]), \
                patch_any("_ccd_preferred_size",
                                           return_value=(2560, 1440)), \
                patch_any("_ccd_apply", side_effect=fake_apply), \
                patch_any("_ccd_wait_state", return_value=True), \
                patch_any("topology_indices_for_anchors",
                                           return_value=[1]), \
                patch_any("current_level", return_value=80), \
                patch_any("set_brightness_many",
                                           return_value=([1], [])), \
                patch_any("_write_event_log"):
            ok, errs = mb.screen_on_cfg(cfg, [1])
        self.assertEqual(errs, [])
        # 记过位置就用记的（连尺寸一起），不去问首选分辨率
        self.assertEqual(seen["rect"], (-1920, 0, 1920, 1080))

    def test_on_without_saved_position_uses_preferred_and_right_edge(self):
        cfg = self._cfg(screen_off_method="topology")
        seen = {}

        def fake_apply(u32, paths, modes, pc, mc):
            sm = modes[6].sourceMode
            seen["rect"] = (int(sm.position.x), int(sm.position.y),
                            int(sm.width), int(sm.height))
            return 0

        with patch_any("_ccd_api", return_value=object()), \
                patch_any("_ccd_query_raw",
                                           side_effect=lambda *a, **k: self._off_raw()), \
                patch_any("_ccd_mode_entries", return_value=[]), \
                patch_any("_ccd_preferred_size",
                                           return_value=(2560, 1440)), \
                patch_any("_ccd_apply", side_effect=fake_apply), \
                patch_any("_ccd_wait_state", return_value=True), \
                patch_any("topology_indices_for_anchors",
                                           return_value=[1]), \
                patch_any("current_level", return_value=80), \
                patch_any("set_brightness_many",
                                           return_value=([1], [])), \
                patch_any("_write_event_log"):
            mb.screen_on_cfg(cfg, [1])
        self.assertEqual(seen["rect"], (0, 0, 2560, 1440))


class TopologyScheduleTest(_TopoFixture):
    """D3：档位层与拓扑接回的边界——亮度只能由 screen_on_cfg 在接回**之后**写。"""

    def test_schedule_off_skips_brightness_in_topology_mode(self):
        cfg = self._cfg(screen_off_method="topology")
        with patch_any("active_slot_dict",
                                        return_value={"level": 0, "screen": "off"}), \
                patch_any("resolve_monitors", return_value=[1]), \
                patch_any("set_brightness_many") as bright, \
                patch_any("screen_off_cfg", return_value=([1], [])):
            level, screen, ok, errs = mb.apply_schedule(cfg)
        self.assertEqual((level, screen, ok, errs), (0, "off", [1], []))
        bright.assert_not_called()

    def test_schedule_on_defers_brightness_to_screen_on_cfg(self):
        """D3：档位层不能在接回拓扑之前写亮度；写亮度归 screen_on_cfg。"""
        cfg = self._cfg(screen_off_method="topology")
        with patch_any("active_slot_dict",
                                        return_value={"level": 75, "screen": "on"}), \
                patch_any("resolve_monitors", return_value=[1]), \
                patch_any("set_brightness_many") as bright, \
                patch_any("screen_wake_entry",
                                           return_value=([1], [])):
            level, screen, ok, errs = mb.apply_schedule(cfg)
        self.assertEqual((level, screen, ok, errs), (75, "on", [1], []))
        bright.assert_not_called()


class TopologyPlanOnTest(unittest.TestCase):
    """v2.01.0 声明式接回：不依赖历史快照，全部从**当前**拓扑现读现算。

    设计纠偏背景：快照是"过去某一刻的整份拓扑字节"，恢复它会把用户其后在
    Windows 设置里改过的排布 / 分辨率 / 主屏一起覆盖掉——那是程序在替用户
    维护状态。现算依据（真机实测，scripts/topo-declarative-probe.py --verify）：
      sourceInfo.modeInfoIdx = (sourceModeIdx << 16) | 0xFFFF
      targetInfo.modeInfoIdx = (targetModeIdx << 16) | desktopModeIdx
    """

    ROWS = [
        {"pathIndex": 0, "anchor": "A1", "active": True, "connected": True,
         "vdd": False, "gdi": "\\\\.\\DISPLAY1", "srcId": 0, "tgtId": 100},
        {"pathIndex": 1, "anchor": "A2", "active": False, "connected": True,
         "vdd": False, "gdi": "", "srcId": 1, "tgtId": 200},
        {"pathIndex": 2, "anchor": "A2", "active": False, "connected": True,
         "vdd": False, "gdi": "", "srcId": 3, "tgtId": 200},
        {"pathIndex": 3, "anchor": "V1", "active": False, "connected": True,
         "vdd": True, "gdi": "", "srcId": 2, "tgtId": 300},
    ]
    ENTRIES = [(0, 1, 0), (1, 2, 100), (2, 3, 100),
               (3, 1, 1), (4, 2, 200), (5, 3, 200)]

    def test_plan_picks_free_source_and_computes_mode_indices(self):
        plan, errs, noop = mb.topology_plan_on(["A2"], self.ROWS, self.ENTRIES)
        self.assertEqual(errs, [])
        self.assertFalse(noop)
        self.assertEqual(len(plan), 1)
        item = plan[0]
        self.assertEqual(item["pathIndex"], 1)      # source id 小的胜出
        self.assertEqual(item["sourceModeIdx"], 3)
        self.assertEqual(item["targetModeIdx"], 4)
        self.assertEqual(item["desktopModeIdx"], 5)

    def test_plan_is_idempotent_for_already_active_anchor(self):
        plan, errs, noop = mb.topology_plan_on(["A1"], self.ROWS, self.ENTRIES)
        self.assertEqual((plan, errs), ([], []))
        self.assertTrue(noop)

    def test_plan_refuses_vdd_and_missing_anchor(self):
        plan, errs, _ = mb.topology_plan_on(["V1"], self.ROWS, self.ENTRIES)
        self.assertEqual(plan, [])
        self.assertIn("虚拟屏", errs[0])
        plan, errs, _ = mb.topology_plan_on(["NOPE"], self.ROWS, self.ENTRIES)
        self.assertEqual(plan, [])
        self.assertIn("没有已连接的 path", errs[0])

    def test_plan_skips_source_used_by_another_active_screen(self):
        rows = [dict(r) for r in self.ROWS]
        rows[1]["srcId"] = 0                 # 和主屏抢同一个 source
        rows[2]["srcId"] = 0
        plan, errs, _ = mb.topology_plan_on(["A2"], rows, self.ENTRIES)
        self.assertEqual(plan, [])
        self.assertIn("没有空闲 source", errs[0])

    def test_plan_returns_none_mode_when_mode_gone(self):
        """source mode 缺失 → 标记 makeSourceMode，由接回时现造一条补上。"""
        plan, errs, _ = mb.topology_plan_on(["A2"], self.ROWS, [])
        self.assertEqual(errs, [])
        self.assertEqual(len(plan), 1)
        self.assertIsNone(plan[0]["sourceModeIdx"])
        self.assertTrue(plan[0]["makeSourceMode"])
        self.assertIsNone(plan[0]["targetModeIdx"])
        self.assertIsNone(plan[0]["desktopModeIdx"])

    def test_apply_plan_writes_bitexact_layout(self):
        paths = (mb._CcdPathInfo * 1)()
        plan = [{"pathIndex": 0, "anchor": "A2", "sourceModeIdx": 3,
                 "targetModeIdx": 4, "desktopModeIdx": 5}]
        mb._ccd_apply_plan_on(paths, plan)
        self.assertTrue(paths[0].flags & mb._CCD_PATH_ACTIVE)
        self.assertEqual(int(paths[0].sourceInfo.modeInfoIdx), 0x0003FFFF)
        self.assertEqual(int(paths[0].targetInfo.modeInfoIdx), 0x00040005)

    def test_apply_plan_keeps_source_and_lets_target_be_invalid(self):
        """真机矩阵：source 必须有效，target/desktop 可以 INVALID。"""
        paths = (mb._CcdPathInfo * 1)()
        plan = [{"pathIndex": 0, "anchor": "A2", "sourceModeIdx": 3,
                 "targetModeIdx": None, "desktopModeIdx": None}]
        mb._ccd_apply_plan_on(paths, plan)
        self.assertEqual(int(paths[0].sourceInfo.modeInfoIdx), 0x0003FFFF)
        self.assertEqual(int(paths[0].targetInfo.modeInfoIdx), 0xFFFFFFFF)

    def test_snapshot_helpers_are_memory_only(self):
        """快照只活在内存里：落盘/读盘函数必须不存在，否则又变成"状态账"。"""
        for name in ("_ccd_save_snapshot", "_ccd_load_snapshot",
                     "topology_snapshot_path", "TOPO_SNAPSHOT_FILE"):
            self.assertFalse(hasattr(mb, name), name)
        self.assertTrue(callable(mb._ccd_make_snapshot))
        self.assertTrue(callable(mb._ccd_restore_from_bytes))

    def test_wanted_anchors_prefers_selection_else_all_disconnected(self):
        cfg = mb.default_config()
        cfg["monitor_anchors"] = ["A1", "A2"]
        self.assertEqual(mb.topology_wanted_anchors(cfg, rows=[]), ["A1", "A2"])
        cfg["monitor_anchors"] = []
        got = mb.topology_wanted_anchors(cfg, rows=self.ROWS)
        self.assertEqual(got, ["A2"])        # 只取 disconnected 的非 VDD 物理屏


class VddNameTest(unittest.TestCase):
    """v2.01.12：VDD 当主屏时是 index 0，不能套用物理屏的型号名。

    日志实证：18:38:43 `topo.off.handover heir=1589A778.../VDD`，之后 VDD
    成为主屏（index 0），而 mon_models["0"]="Legion R27qe Gen2"（物理屏型号）
    —— 界面就把虚拟显示器显示成物理显示器名，这就是用户报的"名称会错误"。
    """

    def test_vdd_uses_friendly_not_model(self):
        name, is_vdd = mb.display_name_for_row(
            0, {"vdd": True, "friendly": "VDD by MTT"},
            {0: "Legion R27qe Gen2"})
        self.assertEqual(name, "VDD by MTT")
        self.assertTrue(is_vdd)

    def test_vdd_without_friendly_has_own_label(self):
        name, is_vdd = mb.display_name_for_row(
            0, {"vdd": True, "friendly": ""}, {0: "Legion R27qe Gen2"})
        self.assertEqual(name, "虚拟显示器")
        self.assertTrue(is_vdd)

    def test_physical_still_uses_model(self):
        name, is_vdd = mb.display_name_for_row(
            0, {"vdd": False, "friendly": "P1"}, {0: "Legion R27qe Gen2"})
        self.assertEqual(name, "Legion R27qe Gen2")
        self.assertFalse(is_vdd)

    def test_physical_without_model_falls_back(self):
        name, is_vdd = mb.display_name_for_row(2, {"vdd": False}, {})
        self.assertEqual(name, "显示器 2")
        self.assertFalse(is_vdd)

    def test_physical_uses_ccd_friendly_before_model_arrives(self):
        """v2.01.21：DDC 读型号要 6.2 秒，期间用 CCD friendly 顶上。"""
        name, is_vdd = mb.display_name_for_row(
            2, {"vdd": False, "friendly": "R27qe Gen2"}, {})
        self.assertEqual(name, "R27qe Gen2")     # 不是"显示器 2"
        self.assertFalse(is_vdd)
        # 型号缓存到位后仍以完整型号为准
        name2, _ = mb.display_name_for_row(
            2, {"vdd": False, "friendly": "R27qe Gen2"}, {2: "Legion R27qe Gen2"})
        self.assertEqual(name2, "Legion R27qe Gen2")

    def test_generic_pnp_desc_is_not_a_name(self):
        """v2.01.22（P-28）：desc 恒为 "Generic PnP Monitor"，不能当名字显示。"""
        name, _ = mb.display_name_for_row(
            2, {"vdd": False, "friendly": "Generic PnP Monitor"}, {})
        self.assertEqual(name, "显示器 2")
        name2, _ = mb.display_name_for_row(
            2, {"vdd": False, "friendly": "generic pnp monitor  "}, {})
        self.assertEqual(name2, "显示器 2")


class DefaultLayoutTest(TempConfigCase):
    """「恢复双屏」的默认布局快照：落盘、读回、校验后才写回。"""

    def setUp(self):
        super().setUp()
        self.layout_file = os.path.join(self.tmp, "default_layout.json")
        self._old_file = mb.DEFAULT_LAYOUT_FILE
        mb.DEFAULT_LAYOUT_FILE = self.layout_file

    def tearDown(self):
        mb.DEFAULT_LAYOUT_FILE = self._old_file
        super().tearDown()

    def _snap(self):
        paths = (mb._CcdPathInfo * 1)()
        modes = (mb._CcdModeInfo * 1)()
        return mb._ccd_make_snapshot(paths, modes, 1, 1, ["A1", "A2"])

    def test_missing_layout_reports_clear_error(self):
        snap, err = mb.load_default_layout()
        self.assertIsNone(snap)
        self.assertIn("还没记住", err)

    def test_save_then_load_roundtrip(self):
        snap = self._snap()
        with patch_any("_ccd_api", return_value=object()), \
                patch_any(
                    "_ccd_query_raw",
                    return_value=tuple(self._topo_raw())):
            desc, err = mb.save_default_layout()
        self.assertIsNone(err)
        self.assertIn("1 块屏", desc)
        got, err = mb.load_default_layout()
        self.assertIsNone(err)
        self.assertEqual(got["pathsHex"], snap["pathsHex"])
        self.assertEqual(got["targetAnchors"], ["A1"])

    def _topo_raw(self):
        paths = (mb._CcdPathInfo * 1)()
        modes = (mb._CcdModeInfo * 1)()
        rows = [{"anchor": "A1", "active": True, "connected": True,
                 "friendly": "P1", "vdd": False, "pathIndex": 0}]
        return paths, modes, 1, 1, rows

    def test_bad_shape_is_rejected_before_apply(self):
        """快照长度对不上时，一个 SetDisplayConfig 都不该发。"""
        bad = {"pathCount": 99, "modeCount": 1,
               "pathsHex": "00", "modesHex": "00", "targetAnchors": []}
        with patch_any("_ccd_api", return_value=object()), \
                patch_any("dlog"), \
                patch_any(
                    "_ccd_apply", side_effect=AssertionError("不该 apply")):
            err = mb.restore_default_layout(snap=bad)
        self.assertIn("结构不符", err)

    def test_validate_failure_does_not_apply(self):
        """SDC_VALIDATE 被拒时只报告，不走正式 APPLY。"""
        class FakeU32:
            def __init__(self):
                self.calls = []

            def SetDisplayConfig(self, pc, paths, mc, modes, flags):
                self.calls.append(flags)
                return 87 if flags & mb._SDC_VALIDATE else 0

        fake = FakeU32()
        with patch_any("_ccd_api", return_value=fake), \
                patch_any("dlog"):
            err = mb.restore_default_layout(snap=self._snap())
        self.assertIn("校验", err)
        self.assertEqual(len(fake.calls), 1)      # 只有 VALIDATE，没有 APPLY

    def test_restore_delay_config_clamped(self):
        self.assertEqual(mb.coerce_setting("restore_delay", 10), 10)
        self.assertEqual(mb.coerce_setting("restore_delay", 999), 120)
        self.assertEqual(mb.coerce_setting("restore_delay", -5), 0)
        self.assertEqual(mb.coerce_setting("restore_delay", "abc"), 10)

    def test_restore_skips_vdd_anchor_in_verify(self):
        """v2.01.19：快照含 VDD 时，回读验证只等物理屏，不能误报超时。"""
        class FakeU32:
            def SetDisplayConfig(self, pc, paths, mc, modes, flags):
                return 0

        snap = self._snap()
        snap["targetAnchors"] = ["A1", "VDD"]
        waited = {}

        def fake_wait(anchors, expect, **kw):
            waited["anchors"] = list(anchors)
            return True

        with patch_any("_ccd_api", return_value=FakeU32()), \
                patch_any("_ccd_apply", return_value=0), \
                patch_any(
                    "ccd_probe_topology",
                    return_value=[{"anchor": "VDD", "vdd": True}]), \
                patch_any("_ccd_wait_state", fake_wait), \
                patch_any("dlog"):
            err = mb.restore_default_layout(snap=snap)
        self.assertIsNone(err)
        self.assertEqual(waited["anchors"], ["A1"])     # VDD 被排除


class TooltipTest(unittest.TestCase):
    """v2.01.15：设置说明改成悬停气泡；气泡要能显示、隐藏、不泄漏。"""

    def test_tooltip_show_hide(self):
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            old = mb.Tooltip.DELAY_MS
            mb.Tooltip.DELAY_MS = 1
            widget = tk.Label(root, text="设置项")
            widget.pack()
            tip = mb.Tooltip(widget, "悬停说明")
            tip._show()
            root.update_idletasks()
            self.assertIsNotNone(tip._win)
            # 气泡里是一个 Frame + Label，文字必须正确
            labels = [w for w in tip._win.winfo_children()[0].winfo_children()
                      if isinstance(w, tk.Label)]
            self.assertEqual(len(labels), 1)
            self.assertEqual(labels[0].cget("text"), "悬停说明")
            tip._on_leave()
            self.assertIsNone(tip._win)
            mb.Tooltip.DELAY_MS = old
        finally:
            root.destroy()

    def test_tooltip_on_child_widget(self):
        """v2.01.16 回归：Tk 的 <Enter> 不冒泡，子控件必须也被绑定。

        不断言 event_generate 后气泡立刻可见 —— 那是事件队列时序，CI 上会
        flake（见模块顶部 pump_ui 的同样教训）。改断言结构性事实：bind_tree
        覆盖了每个后代且都绑上了 <Enter>；气泡本身由 show_hide 用例验证。
        """
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            fake = types.SimpleNamespace(root=root, cfg=mb.default_config())
            sheet = mb.Sheet(fake, "测试", width=400)
            card = sheet.section("分组")
            row = sheet.row(card, "设置项", tip="悬停说明",
                            builder=lambda p: tk.Label(p, text="开关").pack())
            root.update_idletasks()
            tip_obj = row.tooltip
            all_widgets = []
            def walk(w):
                all_widgets.append(w)
                for c in w.winfo_children():
                    walk(c)
            walk(row)
            self.assertGreater(len(all_widgets), 3)
            for w in all_widgets:
                self.assertIn(id(w), tip_obj._bound,
                              "子控件 %s 没被 tooltip 绑定" % w.winfo_class())
            sheet.destroy()
        finally:
            root.destroy()

    def test_tooltip_keeps_widget_own_hover_binding(self):
        """v2.01.19 回归：Tooltip 必须用 add="+" 追加，不能覆盖控件自身的
        <Enter>（自绘 Button 用它做 hover 高亮），否则悬停说明有了、按钮不再亮。
        """
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            base = mb.Button(root, "基线", lambda: None, kind="link",
                             height=22, font_size=8)
            root.update_idletasks()
            base_blocks = base.bind("<Enter>").count("if {")
            self.assertEqual(base_blocks, 1)          # 自带一个 hover 绑定
            fake = types.SimpleNamespace(root=root, cfg=mb.default_config())
            sheet = mb.Sheet(fake, "测试", width=400)
            card = sheet.section("分组")
            holder = {}
            def build(p):
                b = mb.Button(p, "记住", lambda: None, kind="link",
                              height=22, font_size=8)
                b.pack()
                holder["b"] = b
            sheet.row(card, "设置项", tip="说明", builder=build)
            root.update_idletasks()
            after = holder["b"].bind("<Enter>").count("if {")
            self.assertEqual(after, base_blocks + 1)  # 原有 + Tooltip，未被覆盖
            sheet.destroy()
        finally:
            root.destroy()

    def test_remember_btn_flashes_result(self):
        """v2.01.25：设置弹层挡住主窗口状态卡，「记住」必须在按钮上给回执。"""
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            btn = mb.Button(root, "记住", None, kind="link",
                            height=22, font_size=8)
            fake = types.SimpleNamespace(_remember_btn=btn)
            self.assertEqual(btn.text(), "记住")
            mb.App._flash_remember_btn(fake, "已记住 ✓")
            self.assertEqual(btn.text(), "已记住 ✓")
            mb.App._restore_remember_btn(fake, btn)
            self.assertEqual(btn.text(), "记住")
            # 弹层先关（按钮已销毁）时静默跳过，不能抛
            btn.destroy()
            mb.App._flash_remember_btn(fake, "已记住 ✓")
            mb.App._restore_remember_btn(fake, btn)
        finally:
            root.destroy()

    def test_row_tip_hides_sub(self):
        """row 传了 tip 就不显示 sub 文字；不传 tip 的 HeirSheet 行为不变。"""
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:                       # pragma: no cover
            self.skipTest("没有可用显示：%s" % e)
        try:
            fake = types.SimpleNamespace(root=root, cfg=mb.default_config())
            sheet = mb.Sheet(fake, "测试", width=400)
            card = sheet.section("分组")
            def texts_in(row):
                out = []
                for w in row.winfo_children():
                    if isinstance(w, tk.Label):
                        out.append(w.cget("text"))
                    else:
                        out.extend(c.cget("text") for c in w.winfo_children()
                                   if isinstance(c, tk.Label))
                return out

            row = sheet.row(card, "设置项", sub="常显说明", tip="悬停说明")
            self.assertEqual(texts_in(row), ["设置项"])       # sub 被藏进气泡
            row2 = sheet.row(card, "另一项", sub="常显说明")
            self.assertEqual(texts_in(row2), ["另一项", "常显说明"])  # 不传 tip 不变
            sheet.destroy()
        finally:
            root.destroy()


class CliSmokeTest(unittest.TestCase):
    """v2.03.0 P0：把三条只读 CLI 的"能跑通 + 有该有的字段"钉住。

    刻意**不比对具体数值**：`--selftest` 含当前时间点，`--list`/`--diag`
    含本机显示器与路径。逐字基线跨机器必挂，维护成本高于收益；
    这里只保证"重构后命令还能跑、输出结构没塌"。
    """

    @classmethod
    def setUpClass(cls):
        cls.py = sys.executable
        cls.script = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "monitor_brightness.py")

    def _run(self, *args):
        import subprocess
        return subprocess.run([self.py, self.script, *args],
                              capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace")

    def test_selftest_runs(self):
        r = self._run("--selftest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("配置：", r.stdout)
        self.assertIn("当前档：", r.stdout)
        # 判定表至少有时刻行
        self.assertRegex(r.stdout, r"\d{2}:\d{2} -> \d+%")

    def test_list_runs(self):
        r = self._run("--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 无显示器的机器允许输出为空，但不能崩
        if r.stdout.strip():
            self.assertRegex(r.stdout, r"^\d+: \d+×\d+")

    def test_diag_runs(self):
        r = self._run("--diag")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("诊断报告", r.stdout)
        self.assertIn("Python " + ".".join(str(v) for v in sys.version_info[:3])[:4],
                      r.stdout)


if __name__ == "__main__":
    unittest.main()
