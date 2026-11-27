# -*- coding: utf-8 -*-
"""面板（v2.03.0 U4，从 monitor_brightness.py 逐字搬出）。

依赖：widgets + sheets（SlotEditor）+ theme + domain。
面板持有 `self.app`（鸭子类型），**不 import App** —— 否则与主文件循环。
"""
import time
import tkinter as tk
from tkinter import ttk

from domain.decisions import display_name_for_row
from domain.timeutil import clamp_level, parse_hhmm
from ui.theme import UI, _aa_circle, _aa_rounded, _ui_font
from ui.widgets import (
    Button, Card, Label, ScrollArea, SectionTitle, Segmented, Slider, Switch,
    Tooltip, _SlotRow,
)
from ui.sheets import SlotEditor


class MonitorPanel:
    """显示器勾选列表（数据在 App，这里只负责渲染）。"""

    def __init__(self, app):
        self.app = app
        head = tk.Frame(app.body, bg=UI["bg"])
        head.pack(fill="x", padx=28, pady=(14, 6))
        SectionTitle(head, "显示器").pack(side="left")
        for text, cmd in (("刷新", app.refresh_monitors), ("全不选", app.select_none),
                          ("全选", app.select_all)):
            Button(head, text, cmd, kind="link", height=18, pad_bg=UI["bg"],
                   font_size=8).pack(side="right", padx=(6, 0))
        card = Card(app.body)
        card.pack(fill="x", padx=14)
        self.inner = card.inner
        self.mon_vars = {}
        self.mon_rows = {}
        self.mon_text = {}
        self._suspend = False
        self._empty = Label(self.inner, "（未检测到支持 DDC/CI 的显示器）",
                            size=8, color=UI["text3"])
        self._empty.pack(anchor="w")

    def selected(self):
        return sorted(i for i, v in self.mon_vars.items() if v.get())

    def set_all(self, value):
        self._suspend = True
        try:
            for var in self.mon_vars.values():
                var.set(bool(value))
        finally:
            self._suspend = False

    def rebuild(self, keep):
        self._suspend = True
        try:
            for w in self.inner.winfo_children():
                w.destroy()
            self.mon_vars, self.mon_rows = {}, {}
            self.mon_text = {}
            if not self.app.monitors:
                Label(self.inner, "（未检测到支持 DDC/CI 的显示器）",
                      size=8, color=UI["text3"]).pack(anchor="w", pady=4)
                return
            for i in range(len(self.app.monitors)):
                row = tk.Frame(self.inner, bg=UI["card"])
                row.pack(fill="x")
                var = tk.BooleanVar(value=(i in keep))
                var.trace_add("write", lambda *_: self.app.on_monitor_toggle())
                self.mon_vars[i] = var
                chk = ttk.Checkbutton(row, variable=var, text="")
                chk.pack(side="left", padx=(0, 8), pady=7)
                # 逐台按钮先于文本 pack：文本是 expand 的，后 pack 的侧边控件
                # 在窄窗口里会被挤掉。顺序：勾选 / 按钮(右) / 文本(左,撑满)。
                btns = self._row_action_button(row, i)
                txt = tk.Frame(row, bg=UI["card"])
                txt.pack(side="left", fill="x", expand=True)
                self.mon_text[i] = txt
                info = self.app.mon_info[i] if i < len(self.app.mon_info) else None
                # VDD 当主屏时它是 index 0，绝不能套用 mon_models[0] 那块物理屏
                # 的型号名（v2.01.12 修，日志 18:38:43 heir=VDD 实证）
                mon = self.app.monitors[i] if i < len(self.app.monitors) else {}
                # v2.01.19：friendly 优先取 CCD 的（"VDD by MTT"），
                # DDC 的 desc 恒为 "Generic PnP Monitor"，只能当兜底。
                ccd_name = (getattr(self.app, "ccd_friendly", lambda _i: "")(i)
                            or mon.get("desc") or (info[0] if info else ""))
                name_row = {"vdd": bool(getattr(self.app, "is_vdd", lambda _i: False)(i)),
                            "friendly": ccd_name,
                            "index": i}
                name, _is_vdd = display_name_for_row(i, name_row, self.app.mon_models)
                Label(txt, name, size=9, bg=UI["card"]).pack(anchor="w")
                sub = ""
                if info:
                    sub = "%d × %d · %s · %s" % (
                        info[1], info[2], "主显示器" if info[3] else "副显示器", info[0])
                Label(txt, sub, size=8, color=UI["text3"], bg=UI["card"],
                      justify="left", wraplength=320).pack(anchor="w")
                self.mon_rows[i] = row
            # 拓扑模式：把「断开但设备还在」的屏也列出来（对齐 Windows 设置 →
            # 屏幕，那里连虚拟显示器都会画出来）。物理屏给「显示」按钮；
            # VDD 只显示不给按钮——它归串流软件管，本程序断/接它没有意义。
            for extra in (getattr(self.app, "topo_inactive", None) or []):
                row = tk.Frame(self.inner, bg=UI["card"])
                row.pack(fill="x")
                tk.Frame(row, bg=UI["fill_off"], width=3).pack(
                    side="left", fill="y", padx=(6, 0), pady=7)
                is_vdd = bool(extra.get("vdd"))
                if not is_vdd:
                    Button(row, "显示",
                           lambda a=extra["anchor"]: self.app.topology_enable(a),
                           kind="link", height=18, pad_bg=UI["card"],
                           font_size=8).pack(side="right", padx=(0, 8))
                txt = tk.Frame(row, bg=UI["card"])
                txt.pack(side="left", fill="x", expand=True, padx=(10, 0))
                Label(txt, extra.get("friendly") or "断开的显示器",
                      size=9, color=UI["text3"], bg=UI["card"]).pack(anchor="w")
                Label(txt, ("虚拟显示器 · 由串流软件管理，本程序不启用也不断开"
                            if is_vdd else
                            "已断开 · 设备仍连接（点「显示」接回，位置会回到原处）"),
                      size=8, color=UI["text3"], bg=UI["card"]).pack(anchor="w")
        finally:
            self._btn_state = tuple(
                self._row_is_off(i) for i in range(len(self.app.monitors or [])))
            self._suspend = False

    def _row_is_off(self, index):
        """这一行现在是不是"灭着"——决定放「熄灭」还是「显示」。

        只看**上一次探测**的结论，不按模式猜：
          - 拓扑模式下，能进 `self.monitors` 说明它还在拓扑里，但面板仍可能被
            DDC 待机（主屏走"点了给选择"那条路时就是这样）—— 那时必须给出
            「显示」，否则用户没法把它唤回来。
          - DDC 模式下屏幕被枚举到同样不代表面板亮着。
        探测不到（unknown）就按"亮着"处理，给「熄灭」。
        """
        return getattr(self.app, "_screen_states", {}).get(index) == "off"

    def refresh_buttons(self):
        """探测结果变了就重画（只在"亮/灭"结论真的变了时重画，避免闪烁）。"""
        want = tuple(self._row_is_off(i) for i in range(len(self.app.monitors or [])))
        if want == getattr(self, "_btn_state", None):
            return
        self.rebuild(self.app.selected_indices())

    def _row_action_button(self, row, index):
        """一行**只有一个动作按钮**：亮着给「熄灭」，灭着给「显示」。

        以前是两个都摆上（一个常灰），实测用户看到的是"这按钮怎么是灰的、
        是不是坏了"。按钮本来就该表达"现在能做的那一件事"。
        系统级熄灭是整组广播，没有"只动一块"，那种模式一个都不放。
        虚拟显示器也不放：它归串流软件管，本程序断/接它没有意义
        （实测：VDD 当主屏时它也会出现在列表里，给个「熄灭」点了只会
        提示"拿不到锚点"，纯属骗人）。
        """
        method = self.app.cfg.get("screen_off_method")
        if method not in ("ddc", "topology"):
            return None
        if getattr(self.app, "is_vdd", lambda i: False)(index):
            return None
        box = tk.Frame(row, bg=UI["card"])
        box.pack(side="right", padx=(6, 10))
        if self._row_is_off(index):
            Button(box, "显示", lambda i=index: self.app.monitor_on(i),
                   kind="link", height=18, font_size=8).pack(side="right")
        else:
            Button(box, "熄灭", lambda i=index: self.app.monitor_off(i),
                   kind="link", height=18, font_size=8).pack(side="right")
        return box

    def set_row_text(self, i, text):
        # 行里现在有「勾选 / 按钮 / 文本」三段，不能再按下标猜 —— 直接记住文本容器
        txt = self.mon_text.get(i)
        if txt is None:
            return
        labels = txt.winfo_children()
        if labels:
            labels[0].config(text=text)


class BrightnessPanel:
    """亮度：滑块 + 预设分段。预设数值在「设置」里改。"""

    def __init__(self, app, initial_level, presets=None):
        self.app = app
        SectionTitle(app.body, "亮度").pack(anchor="w", padx=28, pady=(14, 6))
        card = Card(app.body)
        card.pack(fill="x", padx=14)
        inner = card.inner
        row = tk.Frame(inner, bg=UI["card"])
        row.pack(fill="x")
        # 松手即写回（Slider 的 command 在 release 时才触发）；拖动过程只刷新
        # 数字，避免 DDC 被拖动期间连续刷写。
        self.slider = Slider(row, value=initial_level, on_move=self._on_slide,
                             command=self._on_release, pad_bg=UI["card"])
        self.slider.pack(side="left", fill="x", expand=True, pady=(2, 0))
        self.level_lbl = Label(row, "%d%%" % initial_level, size=9,
                               color=UI["text2"], bg=UI["card"], anchor="e", width=5)
        self.level_lbl.pack(side="left", padx=(10, 0))
        self._preset_values = [clamp_level(v) for v in (presets or [0, 75, 100])]
        idx = self._preset_values.index(initial_level) \
            if initial_level in self._preset_values else -1
        self.presets = Segmented(inner, ["%d%%" % v for v in self._preset_values],
                                 index=idx, command=self._on_preset)
        self.presets.pack(fill="x", pady=(10, 0))

    def _on_slide(self, value=None):
        v = self.slider.get() if value is None else value
        self.level_lbl.config(text="%d%%" % clamp_level(v))

    def _on_release(self, value=None):
        self.app.apply_now()

    def _on_preset(self, idx):
        self.app.apply_preset(idx)

    def level(self):
        return clamp_level(self.slider.get())

    def set_level(self, level):
        level = clamp_level(level)
        try:
            self.slider.set(level)
            self.level_lbl.config(text="%d%%" % level)
        except Exception:
            pass

    def set_presets(self, presets):
        self.presets.items = ["%d%%" % clamp_level(v) for v in (presets or [])]
        self.presets._draw()

    def set_actions_enabled(self, enabled):
        # 滑块误触由 App._busy 兜底；忙期间把预设点击禁掉即可
        self.presets._command = self._on_preset if enabled else None


class SchedulePanel:
    """定时：开关 + 时间段列表 + 任务计划实测状态。"""

    def __init__(self, app, cfg):
        self.app = app
        SectionTitle(app.body, "定时").pack(anchor="w", padx=26, pady=(14, 6))
        card = Card(app.body)
        card.pack(fill="x", padx=14)
        self.inner = card.inner
        self.slots = [dict(x) for x in cfg.get("slots", [])]
        self._rows = []

        head = tk.Frame(self.inner, bg=UI["card"])
        head.pack(fill="x")
        self.enabled_var = tk.BooleanVar(value=bool(cfg.get("enabled", False)))
        # 开关切换即保存/注册，不再需要「保存并启用定时」大按钮
        self.enabled_switch = Switch(head, bool(cfg.get("enabled", False)),
                                     self._on_toggle)
        self.enabled_switch.pack(side="left")
        Label(head, "启用每日定时", size=9, bg=UI["card"]).pack(
            side="left", padx=(8, 0))
        self.count_lbl = Label(head, "", size=8, color=UI["text3"], bg=UI["card"])
        self.count_lbl.pack(side="right")

        self.list_box = tk.Frame(self.inner, bg=UI["card"])
        self.list_box.pack(fill="x", pady=(4, 0))

        add = tk.Frame(self.inner, bg=UI["card"])
        add.pack(fill="x", pady=(6, 0))
        self.add_btn = Button(add, "＋ 添加时间段", self.add_row, kind="link",
                              height=26, pad_bg=UI["card"])
        self.add_btn.pack(side="left")

        # 任务计划实测状态：圆点 + 结论 + 细节 + 下次 + 检测
        tk.Frame(self.inner, bg=UI["sep"], height=1).pack(fill="x", pady=(10, 0))
        st = tk.Frame(self.inner, bg=UI["card"])
        st.pack(fill="x", pady=(8, 0))
        self.dot = tk.Canvas(st, width=8, height=8, bg=UI["card"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left", pady=2)
        self._dot_colors = {}
        self._dot = self.dot.create_image(0, 0, anchor="nw",
                                          image=_aa_circle(8, UI["text3"]))
        self.task_title = Label(st, "任务计划：读取中…", size=9, bg=UI["card"])
        self.task_title.pack(side="left", padx=(8, 0))
        # 先放「检测」（贴最右），再放「下次」，顺序才对
        self.probe_task_btn = Button(st, "检测", app.refresh_task_status,
                                     kind="link", width=40, height=22,
                                     pad_bg=UI["card"])
        self.probe_task_btn.pack(side="right")
        # 显式重建入口：v1.9.2 之前只有改开关/改时间段才会写任务，
        # 用户想"按现在配置再来一遍"时没有按钮可点。
        self.apply_btn = Button(st, "应用", app.rebuild_schedule_tasks, kind="secondary",
                                width=52, height=22, pad_bg=UI["card"])
        self.apply_btn.pack(side="right", padx=(0, 6))
        self.task_next = Label(st, "", size=8, color=UI["text3"], bg=UI["card"])
        self.task_next.pack(side="right", padx=(0, 8))
        self.task_sub = Label(self.inner, "", size=8, color=UI["text3"],
                              bg=UI["card"], justify="left", wraplength=360)
        self.task_sub.pack(fill="x", pady=(2, 0))
        # 事件（任务重建 / 配置恢复 / 降级重试）：**只放本次运行产生的**。
        # 创建时不 pack —— 没有事件就不占位，否则底部会留一条空白线。
        self.event_lbl = Label(self.inner, "", size=8, color=UI["text3"],
                               bg=UI["card"], justify="left", wraplength=360)

        self.render()

    # ---- 渲染 ----
    def render(self):
        for w in self.list_box.winfo_children():
            w.destroy()
        self._rows = []
        on = bool(self.enabled_var.get())
        for slot in sorted(self.slots, key=lambda x: parse_hhmm(x.get("time"), (0, 0))):
            self._rows.append(self._build_slot_row(slot))
        self.count_lbl.config(text="%d 档" % len(self.slots))
        for r in self._rows:
            r.set_dim(not on)
        # ⚠️ 「添加」永远可点：编辑时间段是纯本地操作，不需要先启用定时。
        # v1.8.5 把它和开关绑在一起，默认关闭时按钮点了没反应（用户反馈）。
        self.add_btn.set_enabled(True)

    def _on_toggle(self, value):
        """开关切换 = 立即注册 / 注销任务计划（不再有单独的保存按钮）。"""
        self.enabled_var.set(bool(value))
        self.render()
        self.app.schedule_toggled(bool(value))

    def _build_slot_row(self, slot):
        row = _SlotRow(self.list_box, slot, lambda s=slot: self._edit(s),
                       pad_bg=UI["card"])
        row.pack(fill="x")
        return row

    def _edit(self, slot):
        def save(new):
            self.slots = [s for s in self.slots if s is not slot]
            if any(s.get("time") == new["time"] for s in self.slots):
                self.app.msg.showerror("时间重复", "同一时刻 %s 只能有一档。" % new["time"])
                self.slots.append(slot)
                return
            self.slots.append(new)
            self.slots.sort(key=lambda x: parse_hhmm(x.get("time"), (0, 0)))
            self.render()
            self._notify_changed()

        def delete():
            if len(self.slots) <= 1:
                self.app.msg.showinfo("提示", "至少保留一个时间段。")
                return
            self.slots = [s for s in self.slots if s is not slot]
            self.render()
            self._notify_changed()

        self._editor = SlotEditor(self.app, slot, save, delete)

    def add_row(self):
        used = {s.get("time") for s in self.slots}
        t = "12:00"
        while t in used:
            m = parse_hhmm(t, (12, 0))
            m = (m + 60) % (24 * 60)
            t = "%02d:%02d" % (m // 60, m % 60)

        def save(new):
            if any(s.get("time") == new["time"] for s in self.slots):
                self.app.msg.showerror("时间重复", "同一时刻 %s 只能有一档。" % new["time"])
                return
            self.slots.append(new)
            self.slots.sort(key=lambda x: parse_hhmm(x.get("time"), (0, 0)))
            self.render()
            self._notify_changed()

        self._editor = SlotEditor(self.app, {"time": t, "level": 50, "screen": "",
                                             "days": []}, save)

    def _notify_changed(self):
        """时间段增删改后立刻落盘；定时已启用时由 App 去重写任务计划。

        v1.8.5 只有开关切换才保存，用户改完时间不关开关 => 任务触发器还是旧的，
        表现同样是"到点没执行"。
        """
        try:
            self.app.on_slots_changed([dict(s) for s in self.slots])
        except Exception as e:
            self.app.status_var.set("保存定时设置失败：%s" % e)

    # ---- App 用到的接口 ----
    def schedule_enabled(self):
        return bool(self.enabled_var.get())

    def set_enabled_flag(self, value):
        self.enabled_var.set(bool(value))
        self.render()

    def read_slots(self):
        if not self.slots:
            self.app.msg.showinfo("提示", "至少保留一个时间段。")
            return None
        return sorted([dict(s) for s in self.slots],
                      key=lambda x: parse_hhmm(x.get("time"), (0, 0)))

    def set_task_status(self, info):
        """info: {"state": registered|missing|error|loading, "next": "...",
                  "detail": "...", "triggers": n, "mismatch": n}"""
        state = info.get("state")
        color = {"ok": UI["green"], "warn": UI["orange"],
                 "error": UI["orange"]}.get(state, UI["text3"])
        if color not in self._dot_colors:
            self._dot_colors[color] = _aa_circle(8, color)
        self.dot.itemconfigure(self._dot, image=self._dot_colors[color])
        self.task_title.config(text=info.get("title", "任务计划"))
        self.task_next.config(text=info.get("next", ""))
        self.task_sub.config(text=info.get("detail", ""))

    def set_events(self, lines):
        """显示**本次运行**产生的事件（任务重建 / 配置恢复 / 降级重试）。

        没有事件就把这一行收起来 —— 以前这里读的是 events.log 的历史，
        开机就把上周的排障记录摆出来，用户以为"程序一直在报错"。

        正常事件用辅助灰（与 task_sub 同色）；含失败/异常字样时保留橙色，
        避免把告警一起灰掉。
        """
        lines = list(lines or [])
        if not lines:
            self.event_lbl.pack_forget()
            self.event_lbl.config(text="")
            return
        text = "\n".join(lines)
        bad = any(k in text for k in ("失败", "错误", "异常", "未就绪"))
        self.event_lbl.config(text=text, fg=UI["orange"] if bad else UI["text3"])
        if not self.event_lbl.winfo_ismapped():
            self.event_lbl.pack(fill="x", pady=(2, 0))

    def set_actions_enabled(self, enabled):
        # 添加/编辑是本地操作，只受"正在执行"影响，不受定时开关影响
        self.add_btn.set_enabled(enabled)
        self.probe_task_btn.set_enabled(enabled)
        self.apply_btn.set_enabled(enabled)


class StatusCard(Card):
    """顶部状态卡：亮灭结论 + 一句上下文 + 三个动作。"""

    def __init__(self, app):
        super().__init__(app.body)
        self.app = app
        self._status_row = tk.Frame(self.inner, bg=UI["card"])
        self._status_row.pack(fill="x")
        row = self._status_row
        self.dot = tk.Canvas(row, width=10, height=10, bg=UI["card"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left")
        self._dot_on = _aa_circle(10, UI["green"])
        self._dot_off = _aa_circle(10, UI["fill_off"])
        self._dot_item = self.dot.create_image(0, 0, anchor="nw",
                                               image=self._dot_on)
        self.title_lbl = Label(row, "屏幕亮着", size=13, bold=True, bg=UI["card"])
        self.title_lbl.pack(side="left", padx=(9, 0))
        self.meta_lbl = Label(row, "", size=8, color=UI["text3"], bg=UI["card"])
        self.meta_lbl.pack(side="right")

        self.hint_lbl = Label(self.inner, "", size=9, color=UI["text2"], bg=UI["card"])
        self.hint_lbl.pack(fill="x", pady=(3, 12))

        btns = tk.Frame(self.inner, bg=UI["card"])
        btns.pack(fill="x")
        self.off_btn = Button(btns, "熄灭屏幕", app.screen_off, kind="secondary",
                              height=34, pad_bg=UI["card"])
        self.off_btn.pack(side="left", fill="x", expand=True)
        self.on_btn = Button(btns, "点亮屏幕", app.screen_on, kind="primary",
                             height=34, pad_bg=UI["card"])
        self.on_btn.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.probe_btn = Button(btns, "检测", app.probe_screens, kind="ghost",
                                width=56, height=34, pad_bg=UI["card"])
        self.probe_btn.pack(side="left", padx=(8, 0))

        # 恢复双屏（v2.01.12）：单独一行，不改上面三个按钮的宽度比例。
        # 倒计时期间按钮变「恢复中 Ns」，右侧出现「取消」；两者都只在这行里。
        self.restore_row = tk.Frame(self.inner, bg=UI["card"])
        self.restore_row.pack(fill="x", pady=(8, 0))
        self.restore_btn = Button(self.restore_row, "恢复双屏",
                                  app.ask_restore_default, kind="ghost",
                                  width=76, height=26, pad_bg=UI["card"], font_size=8)
        self.restore_btn.pack(side="left")
        self.restore_cancel = Button(self.restore_row, "取消",
                                     app.cancel_restore_default, kind="link",
                                     width=36, height=26, pad_bg=UI["card"], font_size=8)
        self.cancel_lbl_slot = tk.Frame(self.restore_row, bg=UI["card"])
        self.cancel_lbl_slot.pack(side="left", padx=(8, 0))
        Label(self.restore_row, "点后倒计时，到时恢复记住的双屏布局",
              size=8, color=UI["text3"], bg=UI["card"]).pack(side="left", padx=(8, 0))
        # 「取消」默认藏起来，倒计时开始才 pack 进槽位
        self.restore_cancel.pack(in_=self.cancel_lbl_slot, side="left")
        self.restore_cancel.pack_forget()

        # 临时反馈（flash）与常驻提示（hint_default）共用提示行：
        # 操作结果闪 3 秒后回到常驻内容，底部状态栏因此不再需要。
        self._hint_default = ""
        self._flash_until = 0.0
        self._flash_after = None
        self._unknown = False

    def set_restore_countdown(self, left):
        """恢复双屏倒计时 UI：left=None 复位；否则显示剩余秒并亮出「取消」。"""
        if left is None:
            self.restore_btn.set_text("恢复双屏")
            self.restore_btn.set_enabled(True)
            self.restore_cancel.pack_forget()
            self._cancel_lbl_pack = None
            return
        self.restore_btn.set_text("恢复中 %ds" % left)
        self.restore_btn.set_enabled(False)
        if not self.restore_cancel.winfo_ismapped():
            self.restore_cancel.pack(in_=self.cancel_lbl_slot, side="left")

    def set_hint_default(self, text):
        """常驻提示：检测后写一次；有临时消息在展示时不打断。"""
        self._hint_default = text
        if time.time() >= self._flash_until:
            self.hint_lbl.config(text=text)

    def flash(self, text, ms=3200):
        """临时反馈：显示 ms 毫秒后自动回到常驻提示。"""
        self._flash_until = time.time() + ms / 1000.0
        if self._flash_after is not None:
            try:
                self.after_cancel(self._flash_after)
            except Exception:
                pass
        self.hint_lbl.config(text=text)
        self._flash_after = self.after(ms, self._restore_hint)

    def _restore_hint(self):
        self._flash_after = None
        self.hint_lbl.config(text=self._hint_default)

    def render(self, verdict):
        state = verdict.get("state", "on")
        # unknown = DDC 模式：回读不可信，不显示亮灭结论（用户明确要求）
        self._unknown = state == "unknown"
        on = state == "on"
        if self._unknown:
            # 单屏待机模式：不显示"亮着/熄灭"结论行（回读不可信，术语也留给说明）
            self._status_row.pack_forget()
        else:
            if self._status_row.winfo_manager() != "pack":
                self._status_row.pack(fill="x", before=self.hint_lbl)
            self.dot.itemconfigure(self._dot_item,
                                   image=self._dot_on if on else self._dot_off)
            self.title_lbl.config(text="屏幕亮着" if on else "屏幕已熄灭")
            # v2.01.0：不再显示"几点熄灭/点亮"——那需要维护状态账，
            # 而程序只读当前状态、不记历史。时间信息在提示行里按需给。
            self.meta_lbl.config(text="")
        self.set_hint_default(verdict.get("hint", ""))
        # 主按钮 = 当前可执行的那个动作，另一个置灰；状态未知时两个都可用
        if self._unknown:
            self.off_btn.set_kind("secondary")
            self.on_btn.set_kind("secondary")
            self.off_btn.set_enabled(True)
            self.on_btn.set_enabled(True)
        else:
            self.off_btn.set_kind("primary" if on else "secondary")
            self.on_btn.set_kind("secondary" if on else "primary")
            self.off_btn.set_enabled(on)
            self.on_btn.set_enabled(not on)

    def set_actions_enabled(self, enabled):
        if self._unknown:
            self.off_btn.set_enabled(enabled)
            self.on_btn.set_enabled(enabled)
        else:
            self.off_btn.set_enabled(enabled and self.off_btn._kind == "primary")
            self.on_btn.set_enabled(enabled and self.on_btn._kind == "primary")
        self.probe_btn.set_enabled(enabled)

