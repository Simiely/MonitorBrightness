# -*- coding: utf-8 -*-
"""P1-8 拆分出的测试用例；由 tests/test_core.py 聚合运行。"""

from tests.support import (  # noqa: F401
    _FakeU32, _PATCH_MODULES, _TopoFixture, _UNSET, _row_button_texts,
    TempConfigCase, app_base_mod, app_cli_entry_mod, datetime, html, mb,
    os, pump_ui, schedule_mod, sys, types, unittest,
)


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

    def test_read_task_xml_reexport(self):
        """R3-2 把 _read_task_xml 搬进 schedule.py 后，主文件 GUI 仍要能取到它。

        这个回归锁专门防「逐字搬移但漏了再导出」：单测靠 patch_any 能过，
        但真实点击「启用每日定时」会在 mb._read_task_xml 上 NameError。
        """
        self.assertIs(mb._read_task_xml, schedule_mod._read_task_xml)


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


class ScheduleSubprocessTest(unittest.TestCase):
    """P0-2：PowerShell 被卡住/缺失时要返回可读错误，不能 traceback。"""

    def test_timeout_returns_124(self):
        import subprocess
        with unittest.mock.patch.object(
                schedule_mod.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("powershell", 90)):
            code, out = schedule_mod._ps("Write-Output ok", timeout=90)
        self.assertEqual(code, 124)
        self.assertIn("超时", out)

    def test_missing_powershell_returns_127(self):
        with unittest.mock.patch.object(
                schedule_mod.subprocess, "run",
                side_effect=OSError("access denied")):
            code, out = schedule_mod._ps("Write-Output ok")
        self.assertEqual(code, 127)
        self.assertIn("无法启动 PowerShell", out)

    def test_register_tasks_turns_timeout_into_false(self):
        cfg = mb.default_config()
        with unittest.mock.patch.object(
                schedule_mod, "_ps", return_value=(124, "PowerShell 超时（90s）")):
            ok, out = schedule_mod.register_tasks(cfg)
        self.assertFalse(ok)
        self.assertIn("超时", out)


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


class MainDispatchTest(unittest.TestCase):
    """R4-5：main() 拆成 _run_cli/_start_gui 后，CLI 分流不能变形。"""

    def test_base_reexports(self):
        self.assertIs(mb.App, app_base_mod.App)
        self.assertIs(mb.main, app_base_mod.main)
        self.assertIs(mb._route_wheel, app_base_mod._route_wheel)

    def test_unknown_or_empty_falls_through(self):
        self.assertFalse(app_base_mod._run_cli([]))
        self.assertFalse(app_base_mod._run_cli(["--not-a-command"]))

    def test_list_dispatches_and_returns_true(self):
        with unittest.mock.patch.object(app_cli_entry_mod, "cli_list") as fn:
            self.assertTrue(app_base_mod._run_cli(["--list"]))
            fn.assert_called_once_with()

    def test_screen_status_uses_live_verdict(self):
        with unittest.mock.patch.object(
                app_cli_entry_mod, "load_config", return_value={"x": 1}), \
                unittest.mock.patch.object(
                    app_cli_entry_mod, "screen_verdict",
                    return_value={"text": "亮着", "detail": ""}) as verdict, \
                unittest.mock.patch.object(app_cli_entry_mod, "_out"):
            self.assertTrue(app_base_mod._run_cli(["--screen", "status"]))
            verdict.assert_called_once_with({"x": 1})


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
