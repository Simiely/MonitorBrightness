# -*- coding: utf-8 -*-
"""P1-8 拆分出的测试用例；由 tests/test_core.py 聚合运行。"""

from tests.support import (  # noqa: F401
    _FakeU32, _PATCH_MODULES, _TopoFixture, _UNSET, _row_button_texts,
    TempConfigCase, cfg_mod, json, mb, os, patch_any, types, unittest,
)


class WalkthroughRound2Test(TempConfigCase):
    """场景走查第 2 轮：探索会话 E1-01~E2-03 的回归测试。"""

    def test_schedule_never_writes_screen_state(self):
        """v2.01.0：定时任务只做动作，不往配置里记屏幕状态。

        （原 E1-01 的问题是"亮度成功也算熄屏成功 → 记成 off"。状态账整个
        取消了，这条回归改成：无论成败，apply_schedule 都不许写配置。）
        """
        cfg = mb.default_config()
        # 走 screen_off_cfg 这条路时也不写状态（v2.04 起它固定走拓扑）。
        cfg["slots"] = mb._coerce_slots([{"time": "08:30", "level": 80, "screen": "off"}])
        with patch_any("active_slot_dict",
                                        return_value={"level": 80, "screen": "off"}), \
                patch_any("set_brightness_many", return_value=([0], [])), \
                patch_any("screen_off_cfg",
                                           return_value=([], [(-1, "拓扑断开失败")])), \
                patch_any("save_config") as save:
            level, screen, ok, errs = mb.apply_schedule(cfg)
        save.assert_not_called()
        self.assertNotIn("screen_state", cfg)
        # v2.04：off 档跳过这里的亮度写入，返回值只反映拓扑动作
        self.assertEqual(ok, [])
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
