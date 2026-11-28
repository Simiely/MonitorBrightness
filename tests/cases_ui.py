# -*- coding: utf-8 -*-
"""P1-8 拆分出的测试用例；由 tests/test_core.py 聚合运行。"""

from tests.support import (  # noqa: F401
    _FakeU32, _PATCH_MODULES, _TopoFixture, _UNSET, _row_button_texts,
    TempConfigCase, app_bright_mod, app_common_mod, app_diag_mod,
    app_layout_mod, app_power_mod, app_schedule_mod, app_settings_mod,
    mb, os, patch_any, restore_any, set_any, types, ui_sheets_mod,
    unittest,
)
from tests.cases_screen import BusyQueueTest  # noqa: F401


class UiDecisionTest(unittest.TestCase):
    """v2.03.0 P2：从 App 抽出的界面决策纯函数（不碰 Tk，可独立测）。"""

    def test_status_payload_uses_topology_result(self):
        self.assertEqual(mb.status_payload({}, "off", "h"),
                         {"state": "off", "hint": "h"})
        self.assertEqual(mb.status_payload({}, "on", "h")["state"], "on")

    def test_row_action_blocker(self):
        self.assertEqual(mb.row_action_blocker({}, True),
                         "正在处理上一个操作，请稍候")
        self.assertEqual(mb.row_action_blocker({}, False), "")

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

    def test_settings_sheet_reexport(self):
        """R4-1：SettingsSheet 搬进 ui/sheets.py 后，主文件仍要拿到同一个类。"""
        self.assertIs(mb.SettingsSheet, ui_sheets_mod.SettingsSheet)

    def test_settings_sheet_builds_without_showing(self):
        """P1-8：设置弹层能完整装配；show() 打桩，避免 grab/wait 卡住测试。"""
        fake = types.SimpleNamespace(
            root=self.root, cfg=mb.default_config(),
            _remember_btn=None,
            remember_layout=lambda: None,
            apply_setting=lambda *a, **k: None,
            apply_autostart=lambda *a, **k: None,
            apply_off_method=lambda *a, **k: None,
            apply_presets_from_settings=lambda *a, **k: None,
            export_diag=lambda: None,
            open_log_dir=lambda: None,
        )
        with unittest.mock.patch.object(ui_sheets_mod, "get_autostart",
                                        return_value=(False, "")), \
                unittest.mock.patch.object(mb.SettingsSheet, "show",
                                           lambda self: None):
            sheet = mb.SettingsSheet(fake)
        try:
            self.assertEqual(len(sheet.preset_spins), 3)
            self.assertIsNotNone(fake._remember_btn)
        finally:
            sheet.destroy()

    def test_app_controller_reexports(self):
        """R4-2：设置/托盘/诊断控制器迁到 app/ 后，主文件再导出不能断。"""
        self.assertIs(mb._SettingsActions, app_settings_mod._SettingsActions)
        self.assertIs(mb._DiagActions, app_diag_mod._DiagActions)
        self.assertIs(mb.tray_tooltip_text, app_settings_mod.tray_tooltip_text)

    def test_screen_action_split(self):
        """R4-3：共用纯函数再导出仍在，App 仍同时混入两个屏幕控制器。"""
        for name in ("status_payload", "row_action_blocker",
                     "auto_relight_minutes", "selection_payload"):
            self.assertIs(getattr(mb, name), getattr(app_common_mod, name))
        self.assertTrue(issubclass(mb.App, app_bright_mod._BrightnessActions))
        self.assertTrue(issubclass(mb.App, app_power_mod._ScreenPowerActions))

    def test_schedule_layout_split(self):
        """R4-4：调度/布局类与布局辅助函数再导出仍在，_finish_apply 已归位。"""
        self.assertIs(mb._ScheduleActions, app_schedule_mod._ScheduleActions)
        self.assertIs(mb._LayoutActions, app_layout_mod._LayoutActions)
        for name in ("topology_heir_candidates", "remember_layout_snapshot",
                     "save_default_layout", "load_default_layout",
                     "restore_default_layout", "DEFAULT_LAYOUT_FILE"):
            self.assertIs(getattr(mb, name), getattr(app_layout_mod, name))
        self.assertTrue(hasattr(app_schedule_mod._ScheduleActions, "_finish_apply"))
        self.assertFalse(hasattr(app_power_mod._ScreenPowerActions, "_finish_apply"))

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

        def _fake_save(c):
            self.saved.append(dict(c))
            return True, ""

        self._saves = set_any("save_config", _fake_save)

    def tearDown(self):
        restore_any(self._saves)
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

class SingleInstanceStateTest(unittest.TestCase):
    """R6 批次 B：互斥体句柄由 app.runtime_state 保活，且注入名不碰真实实例。"""

    @unittest.skipUnless(os.name == "nt", "Windows 互斥体专属")
    def test_mutex_handle_kept_in_runtime_state(self):
        import ctypes as _ctypes
        from app.base import acquire_single_instance
        from app.runtime_state import RUNTIME_STATE

        before = RUNTIME_STATE.mutex_handle
        handle = None
        try:
            self.assertTrue(acquire_single_instance("MonitorBrightness_R6_UnitTest"))
            handle = RUNTIME_STATE.mutex_handle
            self.assertIsNotNone(handle)
        finally:
            RUNTIME_STATE.mutex_handle = before
            if handle:
                _ctypes.windll.kernel32.CloseHandle(handle)
