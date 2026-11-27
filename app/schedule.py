# -*- coding: utf-8 -*-
"""定时控制器（R4-4，从 monitor_brightness.py 逐字搬出）。

`_finish_apply` 原本误放在 _ScreenActions；R4-3 暂留 screen_power，
这里按职责归位到任务重建完成回调。
"""
from core.config import save_config
from core.logging import _write_event_log, read_session_events
from domain.timeutil import parse_hhmm
from schedule import (
    _days_by_min_from_fp, _next_run_text, _read_task_xml, _rebuild_report,
    _slot_trigger_fingerprint, ensure_task_registration, query_task_runtime,
    query_task_status, register_tasks, task_summary, unregister_tasks,
)
from service import selection_anchors
from app.common import selection_payload


class _ScheduleActions:
    """定时：档位保存、任务重建、启动自检。

    v2.03.0 U5-2：从 App 里按职责切出。方法体一字未改（`self.xxx`
    仍指向最终的 App 实例），因此零行为变化。将来的物理分离只需
    把这些类搬到独立文件再 import —— 那时才算真正的模块化。
    """

    def _tray_toggle_schedule(self):
        if self.sch_panel.schedule_enabled():
            self.cancel_schedule()
        else:
            if not self._set_schedule_enabled(True, persist=True):
                return
            self.status_var.set("正在写入任务计划…")
            self.run_bg(lambda: register_tasks(self.cfg),
                        self._finish_tray_register, busy=True)

    def _refresh_events(self):
        try:
            self.sch_panel.set_events(read_session_events(3))
        except Exception:
            pass

    def _start_auto_checks(self):
        """窗口出来之后才跑：只校验计划任务是否已正确配置（全后台）。

        ⚠️ 本程序启动时不会主动熄屏：这里只读任务 XML / 必要时原地重建任务，
        绝不按「今天错过了哪一档」补执行，也不会改亮度或屏幕状态。
        """
        if self._auto_checked:
            return
        self._auto_checked = True
        if not self.cfg.get("enabled"):
            self._refresh_events()
            return

        def job():
            try:
                return {"task": ensure_task_registration(self.cfg)}
            except Exception as e:
                return {"task": (False, "任务自检失败：%s: %s" % (type(e).__name__, e))}

        self.run_bg(job, self._finish_auto_checks)

    def _finish_auto_checks(self, result):
        if isinstance(result, Exception):
            self.status_var.set("启动自检失败：%s" % result)
            self._refresh_events()
            return
        _task_ok, task_msg = result.get("task", (True, ""))
        # 启动自愈成功时不弹「确认」窗：每次启动都可能发生（换 exe 路径后尤其），
        # 开机即弹会糊脸；反馈走状态卡 + 事件日志（task_msg 已含档位数/下次运行）。
        if task_msg:
            self.status_var.set(task_msg)
        else:
            # 配置正确：只报「已就绪 + 下次运行」，不做任何业务动作
            _slots_now = self.cfg.get("slots") or []
            nxt = _next_run_text([parse_hhmm(s.get("time"), (0, 0))
                                  for s in _slots_now],
                                 days_by_min=_days_by_min_from_fp(
                                     _slot_trigger_fingerprint(_slots_now)))
            self.status_var.set(("定时任务已就绪，下次运行 %s" % nxt) if nxt
                                else "定时任务已就绪")
        self._refresh_events()
        if task_msg:
            self.refresh_task_status()

    # ---------- 定时 ----------
    def on_slots_changed(self, slots):
        """面板里增删改时间段后的统一出口：立即落盘；已启用则重写任务。"""
        self.cfg["slots"] = [dict(s) for s in slots]
        ok, msg = save_config(self.cfg)
        if not ok:
            self.msg.showerror("保存时间段失败", msg)
            return
        if not self.cfg.get("enabled"):
            self.status_var.set("时间段已保存（开启每日定时后生效）")
            return
        self.status_var.set("时间段已保存，正在重写任务计划…")
        if self._schedule_reg_timer:
            try:
                self.root.after_cancel(self._schedule_reg_timer)
            except Exception:
                pass
        # 500ms 防抖：连续改几档只重建一次任务（每次 PowerShell 约 2~3 秒）
        self._schedule_reg_timer = self.root.after(500, self._register_after_edit)

    def _register_after_edit(self):
        self._schedule_reg_timer = None
        self.run_bg(lambda: register_tasks(self.cfg),
                    self._finish_edit_register, busy=True)

    def _finish_edit_register(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            # 走查 P-04：与面板/托盘失败统一回滚策略（原来只提示不回滚）
            self._rollback_schedule(False, "重写任务计划失败", out)
            return
        _write_event_log("时间段已更新，任务计划已重写")
        self.refresh_task_status()
        self.status_var.set("定时已更新")
        self._refresh_events()

    def save_schedule(self):
        try:
            self._save_schedule_inner()
        except Exception as e:
            self.msg.showerror("保存定时出错", "%s: %s" % (type(e).__name__, e))
            self.status_var.set("保存定时出错")

    def _save_schedule_inner(self):
        if not self.sch_panel.schedule_enabled():
            self.msg.showinfo("提示", "请先勾选「启用每日定时」")
            return
        slots = self.sch_panel.read_slots()
        if slots is None:
            return
        idx, ids = selection_payload(self.selected_indices())
        part = {"monitors": idx, "monitor_ids": ids,
                "monitor_anchors": selection_anchors(idx),
                "catch_up_on_logon": bool(self.cfg.get("catch_up_on_logon", True)),
                "slots": slots}
        self.cfg.update(part)
        if not self._set_schedule_enabled(True, persist=True):
            return
        self.status_var.set("正在写入任务计划…")
        before_xml = _read_task_xml()          # 留一份重建前定义，用于比对变化
        self.run_bg(lambda: register_tasks(self.cfg),
                    lambda r: self._finish_register(r, part, before_xml), busy=True)

    def rebuild_schedule_tasks(self):
        """「应用」按钮：按当前面板内容强制重建任务计划（成功弹确认窗）。

        走查 P-05：原名 apply_schedule 与模块级同名函数（按当前时间套档位）
        混淆——按钮点的是"重建任务"，CLI --apply-schedule 跑的是"套档位"。
        """
        if not self.sch_panel.schedule_enabled():
            self.msg.showinfo("提示", "请先勾选「启用每日定时」")
            return
        slots = self.sch_panel.read_slots()
        if slots is None:
            return
        part = {"slots": slots, "enabled": True,
                "catch_up_on_logon": bool(self.cfg.get("catch_up_on_logon", True))}
        self.cfg["slots"] = [dict(s) for s in slots]
        ok, msg = save_config(self.cfg)
        if not ok:
            self.msg.showerror("保存失败", msg)
            return
        self.status_var.set("正在重建任务计划…")
        before_xml = _read_task_xml()
        self.run_bg(lambda: register_tasks(self.cfg),
                    lambda r: self._finish_apply(r, part, before_xml), busy=True)

    def _announce_rebuilt(self, part, out, before_xml, title):
        """重建成功后的「确认」弹窗（失败路径绝不调用这里）。

        run_bg 的完成回调本来就由 _poll_read_queue 在 Tk 线程执行；再 after(0)
        一次是为了让弹窗与回调栈彻底分开，也满足"后台线程结果必须回 Tk 线程"。
        """
        message = _rebuild_report(part, out, before_xml)
        self.root.after(0, lambda: self.msg.shownotice(title, message))

    def _finish_register(self, result, part, before_xml=""):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            # 开关是"切换即注册"：注册失败统一回滚（改状态 + 落盘 + 界面）
            self._rollback_schedule(False, "注册任务失败", out)
            return
        self.refresh_task_status()
        self.status_var.set("定时已启用")
        self._announce_rebuilt(part, out, before_xml, "定时已保存")

    def _set_schedule_enabled(self, enabled, persist=True):
        """定时开关状态的**唯一**写入者：改内存 + 落盘 + 同步界面开关。

        走查 P-01/P-02/P-04：以前 6 个入口各自写 cfg["enabled"]，
        有的落盘有的不落盘，失败回滚策略四种不同。所有入口统一走这里。
        返回 (是否成功)。落盘失败时回滚内存，不让界面说谎。
        """
        want = bool(enabled)
        old = bool(self.cfg.get("enabled"))
        self.cfg["enabled"] = want
        if persist:
            ok, msg = save_config(self.cfg)
            if not ok:
                self.cfg["enabled"] = old
                self.msg.showerror("保存失败", msg)
                return False
        self.sch_panel.set_enabled_flag(want)
        return True

    def _rollback_schedule(self, to_enabled, title, out):
        """任务写入失败时的**唯一**回滚者：改状态 + 落盘 + 提示。"""
        self._set_schedule_enabled(to_enabled, persist=True)
        self.msg.showerror(title, out or "未知错误")
        self.status_var.set("%s（已恢复开关）" % title)

    def cancel_schedule(self):
        self._set_schedule_enabled(False, persist=True)
        self.status_var.set("正在取消定时任务…")
        self.run_bg(unregister_tasks, self._finish_unregister, busy=True)

    def schedule_toggled(self, on):
        """面板上的「启用每日定时」开关：开 = 注册，关 = 注销。"""
        if on:
            self.save_schedule()
        else:
            self.cancel_schedule()

    def _finish_unregister(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            # 走查 P-02：注销失败必须把开关恢复为 True 并落盘，
            # 否则磁盘说"已关闭"但任务残留、继续熄屏（cli_apply_schedule
            # 现在也会因 enabled=false 拒绝执行，双保险）。
            self._rollback_schedule(True, "取消定时任务失败", out)
            self.refresh_task_status()
            return
        self.refresh_task_status()
        self.status_var.set("已取消定时任务")

    def refresh_task_status(self):
        if self._task_querying:
            return
        self._task_querying = True
        self.sch_panel.set_task_status({"state": "loading", "title": "任务计划：读取中…"})

        def job():
            try:
                return (query_task_status(), query_task_runtime())
            except Exception:
                return ("", "")

        def done(result):
            self._task_querying = False
            if isinstance(result, Exception):
                result = ("", "")
            text, runtime = result
            self._apply_task_status(text, runtime)

        self.run_bg(job, done)

    def _apply_task_status(self, text, runtime=""):
        info = task_summary(text, self.cfg.get("slots", []), runtime)
        self.sch_panel.set_task_status(info)
        self.scroll.refresh()
        self._tray_update_tooltip()


    # 兼容旧名（托盘/tray 里调用）
    _refresh_task_status = refresh_task_status

    def _finish_apply(self, result, part, before_xml):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            # 走查 P-04：与其余入口统一回滚策略
            self._rollback_schedule(False, "重建任务计划失败", out)
            return
        self.refresh_task_status()
        self.status_var.set("任务计划已重建")
        self._announce_rebuilt(part, out, before_xml, "任务计划已重建")
