# -*- coding: utf-8 -*-
"""逐台开关屏 / 忙时排队控制器（R4-3，从 _ScreenActions 逐字拆出）。

`_finish_apply` 实质是任务重建完成回调，暂留本模块，R4-4 迁
_ScheduleActions 时一并归到 app/schedule.py。其它 self.xxx 仍由 App
多继承解析，跨 Mixin 调用不需要 import。
"""
import threading

from core.config import save_config
from core.logging import _write_event_log, dlog
from hw.power import take_wake_info
from schedule import _as_ok_errs
from service import screen_off_cfg, screen_on_row, screen_wake_entry, selection_anchors
from app.common import auto_relight_minutes, row_action_blocker


class _ScreenPowerActions:
    """熄灭 / 点亮、逐台动作、忙时请求排队与自动复亮。"""

    def _row_action_blocker(self):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return row_action_blocker(self.cfg, self._busy)

    def monitor_off(self, index):
        """行内「熄灭」：只熄灭这一块，不动别的。"""
        dlog("app.action", what="monitor_off", index=index)
        why = self._row_action_blocker()
        if why:
            self.status_var.set(why)
            return
        if self.cfg.get("screen_off_method") == "topology":
            anchors = selection_anchors([index])
            if not anchors:
                self.status_var.set("拿不到这块屏的锚点，无法单独断开")
                return
            is_primary = (index < len(self.mon_info) and self.mon_info[index][3])
            if is_primary:
                # 主屏不能直接从拓扑里摘掉（Windows 实测 rc=87），但可以先把
                # 主屏角色交给别人再摘 —— 全程纯拓扑操作，不碰面板电源。
                self._ask_heir(index, anchors)
                return
        self._busy_action = "off"
        self._row_action = True
        self.status_var.set("正在熄灭显示器 %d…" % index)
        self.run_bg(lambda: screen_off_cfg(self.cfg, [index]),
                    self._finish_screen_off, busy=True)

    def monitor_on(self, index):
        """行内「显示」：只点亮这一块。"""
        dlog("app.action", what="monitor_on", index=index)
        why = self._row_action_blocker()
        if why:
            self.status_var.set(why)
            return
        self._busy_action = "on"
        self._row_action = True
        self.status_var.set("正在点亮显示器 %d…" % index)
        self.run_bg(lambda: screen_on_row(self.cfg, index),
                    self._finish_screen_on, busy=True)

    def _queue_screen_action(self, action):
        """忙时点击：给可见反馈，并记下最后一次请求，结束后补执行一次。

        v1.9.5：之前 `_busy` 窗口内直接静默 return，用户看到的是
        「点了没反应」。这里只做排队记账，**不启动任何屏幕动作**，因此不改变
        熄灭/点亮调用链，也不改变 `_busy` 窗口长度。
        """
        self._pending_screen = action
        label = "点亮" if action == "on" else "熄灭"
        if self._busy_action == "on":
            head = "正在唤醒中…"
        elif self._busy_action == "off":
            head = "正在熄灭中…"
        else:
            head = "正在处理其他操作…"
        self.status_var.set("%s%s请求已排队，完成后自动执行" % (head, label))
        _write_event_log("忙时%s请求已排队（当前动作：%s）"
                         % (label, {"on": "点亮", "off": "熄灭"}.get(
                             self._busy_action, "其它")))

    def _dispatch_pending_screen(self):
        """当前忙任务结束后补执行排队的那一次屏幕请求。返回是否补执行。"""
        if self._busy or not self._pending_screen:
            return False
        action, self._pending_screen = self._pending_screen, None
        _write_event_log("补执行忙时排队的%s请求" % ("点亮" if action == "on" else "熄灭"))
        if action == "on":
            self.screen_on()
        else:
            self.screen_off()
        return True

    def screen_off(self):
        targets = self.selected_indices()
        dlog("app.action", what="screen_off", targets=targets)
        if not targets:
            self.status_var.set("请先勾选要操作的显示器")
            return
        if self._busy:
            self._queue_screen_action("off")
            return
        self._busy_action = "off"
        self.status_var.set("正在熄灭显示器…")
        self.run_bg(lambda: screen_off_cfg(self.cfg, targets),
                    self._finish_screen_off, busy=True)

    def _finish_screen_off(self, result):
        row_action, self._row_action = getattr(self, "_row_action", False), False
        ok, errs = _as_ok_errs(result)
        if ok:
            # 行内逐台操作不碰"勾选"：用户点的是这一行，不是改选择
            if not row_action:
                self._remember_selection()
            save_config(self.cfg)          # 拓扑断开时顺手记下了原位置
            self.status_var.set("已熄灭显示器")
            self.status_card.render(self._status_payload(
                "off", "键鼠输入不会点亮它；点「点亮屏幕」恢复"))
        if errs:
            self.msg.showwarning("熄灭失败", "\n".join(
                ("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs))
        mins = self._auto_relight_minutes()
        if ok and mins > 0:
            self._relight_pending = True
            # 走查 E1-05：必须持有 timer 句柄，手动点亮/再次熄灭时取消旧的，
            # 否则残留 timer 会在用户已手动点亮后触发一次"无缘无故的点亮"。
            self._cancel_relight_timer()
            self._relight_timer = threading.Timer(
                mins * 60, lambda: self._read_queue.put(("screen_action", "on", None)))
            self._relight_timer.start()
        if self.cfg.get("screen_off_method") == "topology":
            # 断开后副屏从 enumerate_targets 消失，重刷列表把它灰显列出来
            self.refresh_monitors()
        self.probe_screens()

    def screen_on(self):
        targets = self.selected_indices()
        dlog("app.action", what="screen_on", targets=targets)
        if not targets:
            self.status_var.set("请先勾选要操作的显示器")
            return
        if self._busy:
            self._queue_screen_action("on")
            return
        self._busy_action = "on"
        self.status_var.set("正在点亮显示器…")
        self.run_bg(lambda: screen_wake_entry(self.cfg, targets),
                    self._finish_screen_on, busy=True)

    def _cancel_relight_timer(self):
        """走查 E1-05：取消尚未触发的自动复亮 timer，并同步轮询标志。"""
        timer = getattr(self, "_relight_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        self._relight_timer = None
        self._relight_pending = False

    def _finish_screen_on(self, result):
        row_action, self._row_action = getattr(self, "_row_action", False), False
        ok, errs = _as_ok_errs(result)
        info = take_wake_info()
        self._cancel_relight_timer()
        if ok:
            if not row_action:
                self._remember_selection()
            save_config(self.cfg)
            self.status_var.set("已点亮显示器" + (" · " + "；".join(info) if info else ""))
            self.status_card.render(self._status_payload("on", "亮度与定时均正常"))
        if errs:
            detail = "\n".join(("显示器 %d：%s" % (i, e)) if i >= 0 else e
                               for i, e in errs)
            self.status_var.set("点亮失败：" + detail.replace("\n", " "))
            tip = ("\n\n若这台屏是 DP+HDMI 同时接线，把「点亮时允许切信号源」关掉"
                   "或改用「系统级」再试一次。"
                   if self.cfg.get("screen_off_method") == "ddc" else "")
            self.msg.showwarning("点亮失败", detail + tip)
        if self.cfg.get("screen_off_method") == "topology":
            # 接回后副屏重新出现，刷新列表把灰显行去掉
            self.refresh_monitors()
        self.probe_screens()

    def _auto_relight_minutes(self):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return auto_relight_minutes(self.cfg)
