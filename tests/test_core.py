"""不碰硬件的回归测试：python -m unittest discover -s tests -v

覆盖 v1.9.0 修的几处核心逻辑：
  - 配置原子写 / 坏配置备份与提示 / last_fired 与磁盘取较新
  - 时间解析与旧配置兼容
  - 启动补检判定（错过阈值、同日同档防重）
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

    def test_last_fired_keeps_newer_disk_value(self):
        disk = mb.default_config()
        mb.mark_fired(disk, {"time": "08:30"}, datetime(2026, 11, 4, 8, 30, 10))
        mb.save_config(disk)

        stale = mb.default_config()
        mb.mark_fired(stale, {"time": "08:30"}, datetime(2026, 11, 3, 8, 30, 10))
        mb.save_config(stale)

        got = mb.load_config()
        self.assertEqual(got["last_fired"]["date"], "2026-11-04",
                         "GUI 手里的旧 cfg 不能覆盖计划任务刚写的 last_fired")

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


class CatchUpTest(TempConfigCase):
    def make_cfg(self, slots=None):
        cfg = mb.default_config()
        cfg["enabled"] = True
        cfg["slots"] = mb._coerce_slots(slots or [
            {"time": "08:30", "level": 80},
            {"time": "21:00", "level": 0},
        ])
        return cfg

    def test_due_after_missed(self):
        d = mb.catch_up_decision(self.make_cfg(), datetime(2026, 11, 4, 9, 5))
        self.assertTrue(d["due"])
        self.assertEqual(d["slot"]["time"], "08:30")
        self.assertEqual(d["missed_min"], 35)

    def test_before_first_slot_today_no_catch_up(self):
        d = mb.catch_up_decision(self.make_cfg(), datetime(2026, 11, 4, 6, 30))
        self.assertFalse(d["due"], "早上开机不该把昨晚 21:00 的夜间档再套一遍")

    def test_same_day_same_slot_not_repeated(self):
        cfg = self.make_cfg()
        mb.mark_fired(cfg, {"time": "08:30"}, datetime(2026, 11, 4, 8, 30, 5))
        d = mb.catch_up_decision(cfg, datetime(2026, 11, 4, 10, 0))
        self.assertFalse(d["due"])
        self.assertIn("已执行", d["reason"])

    def test_yesterday_record_does_not_block_today(self):
        cfg = self.make_cfg()
        mb.mark_fired(cfg, {"time": "08:30"}, datetime(2026, 11, 3, 8, 30, 5))
        d = mb.catch_up_decision(cfg, datetime(2026, 11, 4, 9, 0))
        self.assertTrue(d["due"])

    def test_over_threshold_skipped(self):
        d = mb.catch_up_decision(self.make_cfg(), datetime(2026, 11, 4, 21, 40))
        # 21:40 的当前档是 21:00（错过 40 分钟），仍然该补
        self.assertTrue(d["due"])
        d2 = mb.catch_up_decision(self.make_cfg(), datetime(2026, 11, 4, 21, 1))
        self.assertTrue(d2["due"])
        cfg = self.make_cfg([{"time": "08:30", "level": 80}])
        d3 = mb.catch_up_decision(cfg, datetime(2026, 11, 4, 21, 0))
        self.assertFalse(d3["due"])
        self.assertGreater(d3["missed_min"], mb.CATCH_UP_LIMIT_MIN)


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
        fake._catch_up_worker = lambda: None
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
            "catchup": {"due": False},
        })
        self.root.update()
        self.assertEqual(self._top_levels(), [], "启动自动重建成功不应弹窗")
        self.assertIn("重建", fake.status_var.get())


if __name__ == "__main__":
    unittest.main()
