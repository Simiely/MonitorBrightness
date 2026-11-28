# -*- coding: utf-8 -*-
"""P1-8 拆分出的测试用例；由 tests/test_core.py 聚合运行。"""

from tests.support import (  # noqa: F401
    _FakeU32, _PATCH_MODULES, _TopoFixture, _UNSET, _row_button_texts,
    TempConfigCase, app_layout_mod, json, mb, os, patch_any, restore_any,
    set_any, types, unittest,
)


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
        # R4-4：布局函数已搬 app/layout.py，路径常量必须两处同时改，
        # 否则函数仍读 app.layout 里 import 时的真实 CONFIG_DIR。
        mb.DEFAULT_LAYOUT_FILE = app_layout_mod.DEFAULT_LAYOUT_FILE = self.layout_file

    def tearDown(self):
        mb.DEFAULT_LAYOUT_FILE = app_layout_mod.DEFAULT_LAYOUT_FILE = self._old_file
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
        with patch_any("_ccd_api", side_effect=AssertionError("不该取 API")), \
                patch_any("dlog"), \
                patch_any(
                    "_ccd_apply", side_effect=AssertionError("不该 apply")):
            err = mb.restore_default_layout(snap=bad)
        self.assertIn("结构不符", err)

    def test_huge_counts_do_not_allocate(self):
        """P0-3：畸形 count 必须在分配 ctypes 数组之前被拒。"""
        bad = {"pathCount": 10 ** 9, "modeCount": 10 ** 9,
               "pathsHex": "", "modesHex": ""}
        with patch_any("_ccd_api", side_effect=AssertionError("不该取 API")):
            err = mb.restore_default_layout(snap=bad)
        self.assertIn("结构不符", err)
        self.assertIn("结构不符", app_layout_mod._layout_snapshot_error(bad))

    def test_load_rejects_bad_hex_length(self):
        with open(self.layout_file, "w", encoding="utf-8") as f:
            json.dump({"pathCount": 1, "modeCount": 1,
                       "pathsHex": "00", "modesHex": "00"}, f)
        snap, err = mb.load_default_layout()
        self.assertIsNone(snap)
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
