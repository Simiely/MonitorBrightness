# -*- coding: utf-8 -*-
"""P1-8 拆分出的测试用例；由 tests/test_core.py 聚合运行。"""

from tests.support import (  # noqa: F401
    _FakeU32, _PATCH_MODULES, _TopoFixture, _UNSET, _row_button_texts,
    ctypes, mb, patch_any, types, unittest,
)


class TopologyActionTest(_TopoFixture):
    """E3/E4：拓扑动作层全部 monkeypatch，绝不碰真机屏幕。"""

    def test_topology_is_the_only_off_path(self):
        """v2.04：熄灭方式不再是可选项，配置里也不再有这个键。"""
        self.assertNotIn("screen_off_method", mb.default_config())
        self.assertNotIn("screen_off_method", mb.SETTING_BY_KEY)

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
        cfg = self._cfg()
        with patch_any("active_slot_dict",
                                        return_value={"level": 75, "screen": "on"}), \
                patch_any("resolve_monitors", return_value=[1]), \
                patch_any("set_brightness_many") as bright, \
                patch_any("screen_on_cfg",
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
