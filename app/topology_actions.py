# -*- coding: utf-8 -*-
"""拓扑动作（P1-5，从 app/layout.py 逐字搬出）。

只放“断开后接回 / 交接主屏”三个 GUI 动作；布局快照与型号读取仍归 app/layout.py。
"""

from core.config import save_config
from core.logging import dlog
from hw.topology import topology_off
from schedule_report import _as_ok_errs
from service import topology_enable_anchor
from ui.sheets import HeirSheet
from app.layout import topology_heir_candidates


class _TopologyActions:
    """断开屏的接回、主屏交接与完成回调。"""

    def _ask_heir(self, index, off_anchors):
        """弹窗让用户挑一块屏接任主显示器。"""
        cands = topology_heir_candidates(off_anchors)
        # 诊断（v2.01.13）：候选数用户报过"该有 2 个却只显示 1 个"。
        # 这里把候选来源与每块屏的状态落日志，下次出现时一眼能看出
        # 是 targetAvailable=0 还是查询失败——只记录，不改任何行为。
        dlog("app.heir_prompt", off=list(off_anchors), count=len(cands),
             candidates=[{"anchor": c["anchor"], "vdd": c["vdd"],
                          "active": c["active"]} for c in cands])
        if not cands:
            self.msg.showwarning(
                "没有屏可以接任主显示器",
                "断开 %d 之后桌面上一块屏都不剩，Windows 不允许这样做（会黑屏）。\n\n"
                "先把另一块显示器的线接上，或让虚拟显示器（串流）先用起来，再试。"
                % index)
            return
        name = "显示器 %d" % index
        if index < len(self.mon_models) and self.mon_models.get(index):
            name = self.mon_models[index]

        def picked(anchor):
            self._busy_action = "off"
            self._row_action = True
            self.status_var.set("正在把主显示器交给另一块，然后断开 %s…" % name)
            self.run_bg(lambda: topology_off([index], self.cfg, heir=anchor),
                        self._finish_screen_off, busy=True)

        try:
            self._sheet = HeirSheet(self, cands, name, picked)
        except Exception as e:
            self.msg.showerror("打开选择框失败", "%s: %s" % (type(e).__name__, e))

    def topology_enable(self, anchor):
        """GUI「显示」按钮（断开的屏那一行）：后台接回指定锚点的屏（不碰 Tk）。"""
        dlog("app.action", what="topology_enable", anchor=anchor)
        if self._busy:
            self._queue_screen_action("on")
            return
        self._busy_action = "on"
        self.status_var.set("正在启用显示器…")

        def job():
            return topology_enable_anchor(self.cfg, anchor)

        self.run_bg(job, self._finish_topology_enable, busy=True)

    def _finish_topology_enable(self, result):
        ok, errs = _as_ok_errs(result)
        if ok:
            save_config(self.cfg)
            self.status_var.set("显示器已启用")
            self.status_card.render(self._status_payload("on", "亮度与定时均正常"))
            self.refresh_monitors()
        for i, e in errs:
            self.status_var.set("启用失败：" + (e if i < 0 else "显示器 %d: %s" % (i, e)))
        if errs:
            self.msg.showwarning("启用失败", "\n".join(
                (e if i < 0 else "显示器 %d: %s" % (i, e)) for i, e in errs))
        self.probe_screens()
