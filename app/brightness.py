# -*- coding: utf-8 -*-
"""亮度 / 显示器选择控制器（R4-3，从 _ScreenActions 逐字拆出）。

self.xxx 仍指向最终的 App 实例；跨 Mixin 回调由 App 多继承解析。
"""
from core.util import _safe_int
from domain.decisions import _short_dev
from domain.timeutil import clamp_level
from hw.ccd import ccd_probe_topology
from hw.ddc import (
    enumerate_targets, get_brightness_many, probe_states, release_targets,
    set_brightness_many,
)
from hw.topology import topology_inactive_rows
from schedule import _as_ok_errs
from ui.sheets import SettingsSheet
from app.common import status_payload


class _BrightnessActions:
    """显示器选择、亮度下发、快捷预设与状态探测。"""

    def apply_presets_from_settings(self):
        """预设数值：从设置弹层里的三个输入框读回来（失焦时触发）。"""
        sheet = None
        for w in self.root.winfo_children():
            if isinstance(w, SettingsSheet):
                sheet = w
        if sheet is None:
            return
        vals = [_safe_int(sp.get(), 50) for sp in sheet.preset_spins]
        self.apply_setting("presets", vals)

    def select_all(self):
        self.mon_panel.set_all(True)
        self.read_current()

    def select_none(self):
        self.mon_panel.set_all(False)
        self.read_current()

    def on_monitor_toggle(self):
        if not self.mon_panel._suspend:
            self.read_current()

    def refresh_monitors(self, silent=False):
        prev = self.selected_indices()
        targets = enumerate_targets()
        try:
            self.monitors = [{k: v for k, v in t.items() if k != "handle"}
                             for t in targets]
        finally:
            release_targets(targets)
        self.mon_info = [(t["dev"], t["w"], t["h"], t["primary"]) for t in self.monitors]
        # v2.01.21：CCD 探测（QDC_ALL_PATHS，实测 0.1ms）在**任何模式**都做：
        #   1) 填 `_ccd_names` —— DDC 读型号要 6.2 秒，期间先用真实友好名显示，
        #      别让用户看"显示器 0"（截图实证）；
        #   2) 填 `topo_inactive` —— 虚拟显示器始终列出来。以前只有 topology
        #      模式才列，默认 ddc 模式下串流在跑也看不见 VDD（用户报"没刷新出
        #      虚拟显示器"）。物理断开屏仍只在 topology 模式列（只有它能接回）。
        try:
            rows = ccd_probe_topology()
            self._vdd_devs = {_short_dev(r.get("gdi"))
                              for r in rows if r.get("vdd")}
            self._ccd_names = {_short_dev(r.get("gdi")): r.get("friendly")
                               for r in rows
                               if r.get("gdi") and r.get("friendly")}
            inactive = topology_inactive_rows(rows)
            if self.cfg.get("screen_off_method") == "topology":
                self.topo_inactive = inactive
            else:
                # 非拓扑模式：只留 VDD（灰显、无操作按钮），它代表串流在跑
                self.topo_inactive = [r for r in inactive if r.get("vdd")]
        except Exception:
            self._vdd_devs = set()
            self._ccd_names = {}
            self.topo_inactive = []
        self.mon_panel.rebuild(self._restore_selection(prev))

    def _set_actions_enabled(self, enabled):
        self.status_card.set_actions_enabled(enabled)
        self.bri_panel.set_actions_enabled(enabled)
        self.sch_panel.set_actions_enabled(enabled)

    def read_current(self):
        """轻量刷新：只读亮度（1 次 DDC 读/屏）。

        ⚠️ 实测（2026-11-03）：合并"探测+读亮度"后这里变成 3 读/屏（371ms），
        而本方法在每次应用亮度/切换勾选后都会自动执行，是"响应变慢"的主因。
        自动刷新回归轻量路径；全量探测（probe_screens，371ms）只留给
        「检测」按钮、启动和熄灭/点亮状态切换点。
        """
        self.refresh_brightness()
        self._fetch_models()

    def refresh_brightness(self):
        """只读各屏亮度并更新提示行（不重判亮灭）。

        用独立的 _brighting 标志：启动时 refresh_monitors 的轻量刷新如果
        占着全量探测的 _probing，紧接着的 probe_screens() 会被守卫静默跳过，
        状态卡就停在控件默认值上（实测踩过）。
        """
        if not self.monitors or self._brighting:
            return
        self._brighting = True
        targets = self.selected_indices() or list(range(len(self.monitors)))

        def job():
            try:
                return {}, get_brightness_many(targets)[0]
            except Exception:
                return {}, {}

        self.run_bg(job, self._render_states)

    def apply_now(self):
        if self._busy:
            return
        targets = self.selected_indices()
        level = clamp_level(self.bri_panel.level())
        self.status_var.set("正在下发亮度 %d%% …" % level)
        self.run_bg(lambda: set_brightness_many(targets, level),
                    lambda r: self._finish_apply_level(r, level), busy=True)

    def _finish_apply_level(self, result, level):
        ok, errs = _as_ok_errs(result)
        if ok:
            self.status_var.set("已把 %s 的亮度设为 %d%%"
                                % ("、".join("显示器 %d" % i for i in ok), level))
        if errs:
            detail = "\n".join(("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs)
            self.status_var.set("部分失败：" + detail.replace("\n", " "))
            self.msg.showwarning("部分显示器失败", detail)
        self.read_current()

    def apply_preset(self, i):
        if self._busy:
            return
        presets = self.cfg.get("presets") or []
        if not (0 <= i < len(presets)):
            return
        level = clamp_level(presets[i])
        self.bri_panel.set_level(level)
        self.status_var.set("正在把全部显示器设为 %d%% …" % level)
        self.run_bg(lambda: set_brightness_many([], level),
                    lambda r: self._finish_preset(r, level), busy=True)

    def _finish_preset(self, result, level):
        ok, errs = _as_ok_errs(result)
        if ok:
            self.status_var.set("全部显示器已设为 %d%%（%d 台）" % (level, len(ok)))
        if errs:
            detail = "\n".join(("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs)
            self.status_var.set("部分失败：" + detail.replace("\n", " "))
            self.msg.showwarning("部分显示器失败", detail)
        self.read_current()

    def probe_screens(self):
        """「检测」：一次后台任务里同时读亮灭结论与当前亮度。"""
        if not self.monitors or self._probing:
            return
        self._probing = True
        targets = self.selected_indices() or list(range(len(self.monitors)))

        def job():
            states = probe_states()
            try:
                bright = get_brightness_many(targets)[0]
            except Exception:
                bright = {}
            return states, bright

        self.run_bg(job, self._render_states)

    def _update_hint_brightness(self, bright):
        vals = [bright[i] for i in sorted(bright) if bright.get(i) is not None]
        if not vals:
            return
        if len(set(vals)) == 1:
            b = "当前 %d%%" % vals[0]
        else:
            b = "当前 %s%%" % "/".join(str(x) for x in vals)
        if self._last_verdict.get("state") == "off":
            return  # 熄灭状态提示行不显示亮度
        self.status_card.set_hint_default("%s · %d 台显示器" % (b, len(self.monitors)))

    def _status_payload(self, state, hint):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return status_payload(self.cfg, state, hint)
