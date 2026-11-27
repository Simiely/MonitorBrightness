# -*- coding: utf-8 -*-
"""弹层（v2.03.0 U3，从 monitor_brightness.py 逐字搬出）。

依赖：widgets（按钮/分段/时间输入）+ theme（配色/标题栏）+ domain（屏幕动作）。
SettingsSheet 已在 R4-1 从主文件搬入（配置层已就位），__init__ 按分区拆成 _build_*。

⚠️ 搬函数/类时别忘了它用到的模块级 import（U3 踩过：apply_dark_titlebar
用了 sys.platform，搬进 theme.py 时漏了 import sys，5 个测试直接红）。
"""
import tkinter as tk
from tkinter import ttk

from core.config import (
    APP_VERSION, OFF_METHODS, OFF_METHOD_LABEL, SETTING_BY_KEY,
    get_autostart,
)
from core.util import _safe_int

from domain.screen_actions import (
    SCREEN_ACTIONS, SCREEN_LABEL_BY_VALUE, SCREEN_VALUE_BY_LABEL,
)
from domain.timeutil import days_label
from ui.theme import UI, _aa_rounded, _ui_font, apply_dark_titlebar
from ui.widgets import (
    Button, Card, Label, SectionTitle, Segmented, Switch, TimeField, Tooltip,
    WeekdayPicker,
)


class DarkBox:
    """深色模态消息框：messagebox 的替代品（原生对话框不吃深色主题）。"""

    _ICONS = {"info": ("\u2139", "#4a9eff"),
              "warn": ("\u26a0", "#e5c07b"),
              "error": ("\u2715", "#e06c75")}

    def __init__(self, root):
        self.root = root

    def _show(self, kind, title, message):
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()          # 防双屏跳位：定位完成前不显示
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=UI["card"])
        apply_dark_titlebar(dlg)
        icon, color = self._ICONS[kind]
        body = tk.Frame(dlg, background=UI["card"])
        body.pack(fill="both", expand=True, padx=20, pady=(18, 4))
        tk.Label(body, text=icon, fg=color, bg=UI["card"],
                 font=("Segoe UI Symbol", 18)).pack(side="left", padx=(0, 14))
        tk.Label(body, text=message, fg=UI["text"], bg=UI["card"],
                 font=("Microsoft YaHei UI", 10), justify="left",
                 wraplength=400).pack(side="left")
        btn = Button(dlg, "确定", dlg.destroy, kind="primary", width=96,
                     height=32, pad_bg=UI["card"])
        btn.pack(pady=(8, 16))
        dlg.bind("<Return>", lambda e: dlg.destroy())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        self._center(dlg)
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()

    def _center(self, dlg):
        dlg.transient(self.root)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width()
                                              - dlg.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height()
                                              - dlg.winfo_height()) // 2)
        dlg.geometry("+%d+%d" % (x, y))
        dlg.deiconify()

    def showinfo(self, title, message):
        self._show("info", title, message)

    def showwarning(self, title, message):
        self._show("warn", title, message)

    def showerror(self, title, message):
        self._show("error", title, message)

    def shownotice(self, title, message):
        """成功提示（非阻塞）：自绘深色窗 + 单个「确认」按钮。

        和 _show 的区别：不 wait_window，回调链不会被弹窗卡住；窗口仍是普通
        Toplevel + 子控件（没有 UpdateLayeredWindow），子控件由 DWM 正常合成。
        """
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()          # 防双屏跳位：定位完成前不显示
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=UI["card"])
        apply_dark_titlebar(dlg)
        body = tk.Frame(dlg, background=UI["card"])
        body.pack(fill="both", expand=True, padx=20, pady=(18, 4))
        tk.Label(body, text="\u2713", fg=UI["green"], bg=UI["card"],
                 font=("Segoe UI Symbol", 18)).pack(side="left", padx=(0, 14))
        tk.Label(body, text=message, fg=UI["text"], bg=UI["card"],
                 font=("Microsoft YaHei UI", 10), justify="left",
                 wraplength=360).pack(side="left")
        Button(dlg, "确认", dlg.destroy, kind="primary", width=96,
               height=32, pad_bg=UI["card"]).pack(pady=(8, 16))
        dlg.bind("<Return>", lambda e: dlg.destroy())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        self._center(dlg)
        dlg.lift()
        try:
            dlg.grab_set()      # 只拦输入，不阻塞事件循环
        except Exception:
            pass
        return dlg

    def askyesno(self, title, message, yes="继续", no="取消"):
        """是/否确认框（深色）。返回 True/False。"""
        answer = {"ok": False}
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=UI["card"])
        apply_dark_titlebar(dlg)
        body = tk.Frame(dlg, background=UI["card"])
        body.pack(fill="both", expand=True, padx=20, pady=(18, 4))
        tk.Label(body, text="\u26a0", fg="#e5c07b", bg=UI["card"],
                 font=("Segoe UI Symbol", 18)).pack(side="left", padx=(0, 14))
        tk.Label(body, text=message, fg=UI["text"], bg=UI["card"],
                 font=("Microsoft YaHei UI", 10), justify="left",
                 wraplength=420).pack(side="left")
        btns = tk.Frame(dlg, background=UI["card"])
        btns.pack(pady=(8, 16))

        def _close(ok):
            answer["ok"] = ok
            dlg.destroy()

        Button(btns, no, lambda: _close(False), kind="secondary", width=88,
               height=32, pad_bg=UI["card"]).pack(side="left", padx=4)
        Button(btns, yes, lambda: _close(True), kind="primary", width=96,
               height=32, pad_bg=UI["card"]).pack(side="left", padx=4)
        dlg.bind("<Escape>", lambda e: _close(False))
        dlg.bind("<Return>", lambda e: _close(True))
        self._center(dlg)
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
        return answer["ok"]


class Sheet(tk.Toplevel):
    """深色模态弹层（设置 / 说明 / 编辑时间段共用）。

    和 DarkBox 同一套做法：Toplevel + grab_set + wait_window；
    区别是本类带标题区和滚动正文，适合放成组的内容。
    """

    def __init__(self, app, title, subtitle="", width=430):
        super().__init__(app.root)
        # 双屏实测：Toplevel 默认先在系统级联位置显示一帧，show() 里才挪到
        # 主窗中心 —— 用户看到"弹窗先出现在别处再跳过来"。先藏起来，
        # 定好几何尺寸再 deiconify，就只有一次出现。
        self.withdraw()
        self.app = app
        self.title(title)
        self.resizable(False, False)
        self.configure(background=UI["card"])
        apply_dark_titlebar(self)
        self._width = width

        head = tk.Frame(self, bg=UI["card"])
        head.pack(fill="x", padx=20, pady=(16, 0))
        Label(head, title, size=13, bold=True, bg=UI["card"]).pack(anchor="w")
        if subtitle:
            Label(head, subtitle, size=8, color=UI["text3"], bg=UI["card"],
                  justify="left", wraplength=self._width - 40).pack(anchor="w", pady=(3, 0))

        # v2.01.13：弹层正文加滚动条。原来 body 是裸 Frame，设置项一多就
        # 超出屏幕、底部控件点不到（Sheet 文档一直写着"带滚动正文"却没实现）。
        # 复用主窗同款 Canvas + Frame 结构；正文高度按屏幕封顶，超出才出滚动条。
        # v2.01.15：说明文字挪进 tooltip 后正文只剩 636px，封顶再降到 520px，
        # 滚动条仍稳定出现（用户明确要"有滑动条"），窗口也更矮。
        self._sheet_max_h = max(360, min(520, self.winfo_screenheight() - 220))
        wrap = tk.Frame(self, bg=UI["card"])
        wrap.pack(fill="both", expand=True, padx=20, pady=(12, 0))
        self._body_canvas = tk.Canvas(wrap, bg=UI["card"], highlightthickness=0, bd=0)
        self._body_bar = ttk.Scrollbar(wrap, orient="vertical",
                                       command=self._body_canvas.yview)
        self.body = tk.Frame(self._body_canvas, bg=UI["card"])
        self._body_win = self._body_canvas.create_window(
            (0, 0), window=self.body, anchor="nw")
        self._body_canvas.configure(yscrollcommand=self._body_bar.set)
        self._body_canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", self._on_sheet_body)
        self._body_canvas.bind("<Configure>", lambda e: self._body_canvas.itemconfigure(
            self._body_win, width=e.width))
        # v2.01.22：滚轮不再靠 Canvas 的 Enter/Leave（见 show() 的说明）。

        foot = tk.Frame(self, bg=UI["card"])
        foot.pack(fill="x", padx=20, pady=(10, 16))
        self.ok_btn = Button(foot, "完成", self.close, kind="primary",
                             height=34, pad_bg=UI["card"])
        self.ok_btn.pack(fill="x")

        self.bind("<Escape>", lambda e: self.close())
        self.transient(app.root)

    def _on_sheet_wheel(self, event):
        # 没滚动条就别吞滚轮（内容本来就放得下）
        if not self._body_bar.winfo_ismapped():
            return False
        self._body_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return True

    def _on_sheet_body(self, _event=None):
        self._body_canvas.configure(scrollregion=self._body_canvas.bbox("all"))
        need = self.body.winfo_reqheight() > self._sheet_max_h
        if need and not self._body_bar.winfo_ismapped():
            self._body_bar.pack(side="right", fill="y")
        elif not need and self._body_bar.winfo_ismapped():
            self._body_bar.pack_forget()
        # Canvas 显式给尺寸：否则 create_window 的内容把它撑到全高，
        # show() 一量 reqheight 就是整份正文，窗口又超出屏幕（滚动条白加）。
        self._body_canvas.configure(
            width=self.body.winfo_reqwidth(),
            height=min(self.body.winfo_reqheight(), self._sheet_max_h))

    def show(self):
        """内容搭完后再定尺寸并弹出。

        必须由子类在 **最后** 调用：基类构造时 body 还是空的，
        那时量出来只有 144px 高（实测），窗口会被 resizable(False) 锁死。
        """
        self.update_idletasks()
        self._on_sheet_body()             # 先按封顶高度定好 Canvas
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), self._width)
        h = self.winfo_reqheight()
        x = self.app.root.winfo_rootx() + max(
            0, (self.app.root.winfo_width() - w) // 2)
        y = self.app.root.winfo_rooty() + max(
            0, (self.app.root.winfo_height() - h) // 2)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        # 滚轮：v2.03.1 起交给进程级路由（见 _route_wheel）。
        # P-27 原来在这里 bind_all、Destroy 时 unbind_all —— 后者会清掉**所有**
        # 滚轮绑定，导致"开一次设置页再关掉，主界面滚轮永久失效"（用户实测）。
        # 现在只挂标记，路由沿 master 链找到谁该滚。
        self._body_canvas._wheel_target = self._on_sheet_wheel
        self._wheel_target = self._on_sheet_wheel      # 鼠标落在页脚时也能滚正文
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event=None):
        """弹层销毁时不需要解绑任何全局滚轮（v2.03.1：全局只绑一次）。"""
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._body_canvas._wheel_target = None

    def close(self):
        self._on_destroy()
        self.grab_release()
        self.destroy()

    def section(self, text):
        # v2.01.14 紧凑化：原来 (14,6) 让 5 个分区白吃掉 ~190px
        SectionTitle(self.body, text).pack(anchor="w", pady=(9, 3))
        card = Card(self.body, pad=0, bg=UI["card_alt"], page_bg=UI["card"])
        card.pack(fill="x")
        return card.inner

    def row(self, parent, title, sub="", right_text=None, builder=None, tip=None):
        """一行：标题（+副标题）在左，右侧放文字或控件，底部一条分隔线。

        `tip`：鼠标悬停才弹出的说明（v2.01.15）。给了 tip 就不显示 sub 文字，
        把说明藏进气泡——设置页靠这个把 869px 又压下去一大截。
        HeirSheet 等需要常显副标题的地方不传 tip，行为不变。

        ⚠️ 控件必须通过 builder 在**本行内**创建：先建好再 pack 到别的父容器会
        报 "can't pack inside"，或悄悄跑到卡片其它位置（实测踩过）。
        """
        # 用父容器的底色，卡片（card_alt）里才不会出现一块深色补丁
        pbg = parent.cget("bg")
        row = tk.Frame(parent, bg=pbg)
        row.pack(fill="x")
        tip_obj = Tooltip(row, tip) if tip else None   # 绑定延后到子树建好
        if tip:
            sub = ""                       # 说明不再常显
        right = tk.Frame(row, bg=pbg)
        right.pack(side="right", padx=(8, 14))
        left = tk.Frame(row, bg=pbg)
        # v2.01.14 紧凑化：pady 9 → 4；副标题加宽减少换行
        left.pack(side="left", fill="x", expand=True, padx=(14, 0), pady=4)
        Label(left, title, size=9, bg=pbg).pack(anchor="w")
        if sub:
            Label(left, sub, size=8, color=UI["text3"], bg=pbg,
                  justify="left", wraplength=self._width - 130).pack(anchor="w", pady=(1, 0))
        if right_text is not None:
            Label(right, right_text, size=8, color=UI["text3"],
                  bg=pbg).pack(anchor="e")
        if builder is not None:
            builder(right)
        if tip_obj is not None:
            tip_obj.bind_tree()            # 子控件全绑上，悬停才真的会出
            row.tooltip = tip_obj          # 挂引用，便于调试/测试
        sep = tk.Frame(parent, bg=UI["sep"], height=1)
        sep.pack(fill="x", padx=14)
        return row


class SlotEditor(Sheet):
    """单个时间段的编辑弹层（时间 / 亮度 / 到点是否熄屏点亮）。"""

    def __init__(self, app, slot, on_save, on_delete=None):
        super().__init__(app, "时间段", "到点自动切到该亮度", width=380)
        self._slot = dict(slot)
        self._on_save = on_save
        self._on_delete = on_delete

        box = tk.Frame(self.body, bg=UI["card"])
        box.pack(fill="x", pady=(4, 0))

        Label(box, "时间", size=9, color=UI["text2"], bg=UI["card"]).pack(anchor="w")
        self.time_field = TimeField(box, slot.get("time", "12:00"), bg=UI["card"])
        self.time_field.pack(anchor="w", pady=(4, 10))

        Label(box, "亮度", size=9, color=UI["text2"], bg=UI["card"]).pack(anchor="w")
        self.level_spin = ttk.Spinbox(box, from_=0, to=100, width=6, justify="center")
        self.level_spin.set(slot.get("level", 50))
        self.level_spin.pack(anchor="w", pady=(4, 10))

        Label(box, "到点时屏幕", size=9, color=UI["text2"], bg=UI["card"]).pack(anchor="w")
        labels = [lab for _v, lab in SCREEN_ACTIONS]
        cur = SCREEN_LABEL_BY_VALUE.get(slot.get("screen", ""), "不变")
        self.screen_seg = Segmented(box, labels, index=labels.index(cur))
        self.screen_seg.pack(fill="x", pady=(4, 10))

        # v2.02.0：这一档只在勾选的星期生效；一个都不勾 = 每天
        Label(box, "重复", size=9, color=UI["text2"], bg=UI["card"]).pack(anchor="w")
        # 先建 hint 再建 picker：picker 构造时就会触发一次 on_change 回调
        self.days_hint = Label(box, "", size=8, color=UI["text3"], bg=UI["card"])
        self.days_picker = WeekdayPicker(box, slot.get("days"),
                                         on_change=self._refresh_days_hint)
        self.days_picker.pack(anchor="w", pady=(4, 0))
        self.days_hint.pack(anchor="w", pady=(4, 0))
        self._refresh_days_hint()

        if on_delete is not None:
            del_btn = Button(box, "删除这个时间段", self._do_delete, kind="secondary",
                             height=32, pad_bg=UI["card"])
            del_btn.pack(fill="x", pady=(16, 0))

        self.ok_btn.set_text("保存")
        self.ok_btn._command = self._do_save
        self.show()

    def _refresh_days_hint(self):
        """把当前星期选择翻成一句话（每天 / 工作日 / 周一,三,五）。"""
        hint = getattr(self, "days_hint", None)
        if hint is None:
            return
        hint.configure(text="生效于：" + days_label(self.days_picker.get()))

    def _do_save(self):
        t = self.time_field.get_time()
        if not t:
            self.app.msg.showerror("格式不对", "时间需要写成 HH:MM，例如 09:00。")
            return
        try:
            lv = max(0, min(100, int(float(self.level_spin.get()))))
        except Exception:
            lv = 50
        idx = self.screen_seg.get()
        self._on_save({"time": t, "level": lv,
                       "days": self.days_picker.get(),
                       "screen": SCREEN_VALUE_BY_LABEL.get(
                           [lab for _v, lab in SCREEN_ACTIONS][idx], "")})
        self.close()

    def _do_delete(self):
        self._on_delete()
        self.close()


class HeirSheet(Sheet):
    """断开主屏前，选一块屏接班当主显示器。

    Windows 没有"设置主显示器"的 API —— 主屏就是桌面原点 (0,0) 所在的那块；
    把某块屏挪到 (0,0) 就等于让它当主屏，其余屏跟着平移，相对布局不变。
    所以这个弹窗问的不是"要不要"，而是"交给谁"。
    """

    def __init__(self, app, candidates, going_off, on_pick):
        super().__init__(app, "把主屏交给谁？",
                         "先让下面某一块接任主屏，再把 %s 断开。\n"
                         "接任者会挪到桌面原点（Windows 以原点认定主屏），其它屏跟着平移。"
                         % going_off, width=430)
        self._on_pick = on_pick
        inner = self.section("可以接任的屏")
        # 两块同型号屏 friendly 都是 "R27qe Gen2"，光看名字分不清。
        # 给物理屏按候选顺序编号（不按 index，重编号后 index 会漂）。
        phys_no = 0
        for c in candidates:
            if c["vdd"]:
                sub = "虚拟显示器 · 当前断着，会先接回来再断开物理屏"
                title = c["friendly"] or "虚拟显示器"
            else:
                phys_no += 1
                sub = "物理显示器 %d · %s · 会挪到桌面原点" % (
                    phys_no, c["gdi"] or "—")
                title = "%s（物理显示器 %d）" % (c["friendly"] or "显示器", phys_no)
            self.row(inner, title, sub,
                     builder=lambda p, a=c["anchor"]: Button(
                         p, "选它", lambda a=a: self._choose(a), kind="link",
                         height=22, font_size=8).pack())
        self.ok_btn.set_text("取消")
        self.show()

    def _choose(self, anchor):
        self.close()
        self._on_pick(anchor)


class HelpSheet(Sheet):
    """说明：只放解释，不放开关（开关都在设置里）。"""

    def __init__(self, app):
        super().__init__(app, "说明", "常用设置都在主界面；这里放平时不需要看的信息", width=440)

        def block(title, lines):
            SectionTitle(self.body, title).pack(anchor="w", pady=(12, 4))
            for line in lines:
                Label(self.body, "· " + line, size=8, color=UI["text2"],
                      bg=UI["card"], justify="left",
                      wraplength=400).pack(anchor="w", pady=1)

        block("屏幕亮着 / 已熄灭", [
            "状态以本程序的记录为准：程序熄灭后，动键鼠会让系统自动唤醒。",
            "DDC 回读只作参考 —— 面板待机时仍会回报“已开机”，不可信。",
            "物理电源键、Windows 自动熄屏不在追踪范围内。",
        ])
        block("三种熄灭方式", [
            "系统级：整组一起灭，一定唤得回；任何键鼠输入都会点亮。",
            "DDC 单屏待机（默认）：键鼠不会点亮，适合远程防窥；唤醒靠四级兜底，"
            "个别面板仍可能唤不回。",
            "拓扑断开：把选中的屏从 Windows 桌面移除，串流时只留虚拟显示器。",
            "断开主屏时，会先问「把主屏交给谁」——让另一块屏挪到桌面原点接任主屏，"
            "再把原主屏摘掉。全程纯拓扑操作，不碰面板电源。",
            "全部物理屏都断开时，主屏交给虚拟显示器（串流形态）。",
        ])
        block("唤醒阶梯", [
            "0xD6=1 电源模式 → 写回亮度 → 输出重驱动（SC_MONITORPOWER 2→-1）"
            " → 切信号源，任何一级成功即停。",
        ])
        block("定时为什么这次准了", [
            "每次启动都会校验计划任务是否仍指向当前程序；换了目录/文件名会自动重建。",
            "启动自检只检查任务配置，不会主动熄屏，也不会补执行今天错过的档位。",
            "任务状态行会显示下次运行、上次运行与上次结果；下面还会列出重建记录。",
        ])
        block("排查命令", [
            "--probe 打印每台屏的亮灭结论；--diag 打印枚举、能力串、寄存器回读与任务状态。",
            "--ensure-schedule 按当前配置/当前程序重建计划任务；--selftest 打印档位判定表。",
        ])
        self.show()


class SettingsSheet(Sheet):
    """设置：托盘 / 自启 / 熄灭方式 / 预设 / 关于（都是"不常改"的东西）。"""

    def _build_remember_btn(self, parent, app):
        """「记住」按钮 + 结果回执（v2.01.25）。

        走查反馈：点完没有任何可见反馈。根因不是没写状态栏——`remember_layout`
        写了——而是**设置弹层是模态的、盖住主窗口的状态卡**，用户看不见。
        所以在按钮自己身上给回执：成功变「已记住 ✓」，2 秒后复原。
        """
        btn = Button(parent, "记住", app.remember_layout, kind="link",
                     height=22, font_size=8)
        btn.pack()
        app._remember_btn = btn
        return btn

    def __init__(self, app):
        super().__init__(app, "设置", "不常用的开关都在这里，主界面只留日常操作", width=480)
        self.app = app
        cfg = app.cfg
        self._build_window(app, cfg)
        self._build_startup(app)
        self._build_off(app, cfg)
        self._build_presets(app, cfg)
        self._build_about(app, cfg)
        self.show()

    def _build_window(self, app, cfg):
        win = self.section("窗口")
        self.row(win, "系统托盘图标",
                 tip="在右下角常驻，右键可熄/亮屏、切换定时",
                 builder=lambda p: Switch(p, bool(cfg.get("tray_enabled", True)),
                                          lambda v: app.apply_setting("tray_enabled", v)).pack())
        self.row(win, "关闭窗口时最小化到托盘",
                 tip="关掉则点 × 直接退出程序")


    def _build_startup(self, app):
        start = self.section("启动")
        self.row(start, "开机自动启动", tip="启动后直接进托盘",
                 builder=lambda p: Switch(p, get_autostart()[0],
                                          app.apply_autostart).pack())


    def _build_off(self, app, cfg):
        off = self.section("熄屏")
        pbg = off.cget("bg")
        wrap = tk.Frame(off, bg=pbg)
        wrap.pack(fill="x", padx=14, pady=(10, 6))
        mode_lbl = Label(wrap, "熄灭方式", size=9, bg=pbg)
        mode_lbl.pack(anchor="w")
        # v2.01.15：说明文字全挪到悬停气泡，标题下不再常驻小字
        Tooltip(mode_lbl, "系统级：整组熄灭、键鼠会点亮\n"
                          "单屏待机：键鼠不点亮，个别面板唤不回\n"
                          "拓扑断开：从桌面移除副屏，串流用；接回摆回原位")
        mode = Segmented(wrap, [OFF_METHOD_LABEL[m] for m in OFF_METHODS],
                         # fallback 跟着 SETTINGS 的默认值走，别再硬编码
                         index=OFF_METHODS.index(
                             cfg.get("screen_off_method")
                             or SETTING_BY_KEY["screen_off_method"]["default"]),
                         command=lambda i: app.apply_off_method(OFF_METHODS[i]))
        mode.pack(fill="x", pady=(5, 0))
        tk.Frame(off, bg=UI["sep"], height=1).pack(fill="x", padx=14)
        self._build_off_behavior(off, app, cfg)

    def _build_off_behavior(self, off, app, cfg):
        def _relight(p):
            spin = ttk.Spinbox(p, from_=0, to=120, width=5, justify="center")
            spin.set(cfg.get("screen_auto_relight_min", 0))
            spin.pack()
            cb = lambda e: app.apply_setting(
                "screen_auto_relight_min", _safe_int(spin.get(), 0))
            spin.bind("<<Increment>>", cb)
            spin.bind("<<Decrement>>", cb)
            spin.bind("<FocusOut>", cb)

        self.row(off, "熄灭后自动点亮", tip="0 = 不自动；远程时建议设个值", builder=_relight)

        self.row(off, "点亮时允许切信号源",
                 tip="最后一招；DP+HDMI 双线时可能切到空口",
                 builder=lambda p: Switch(p, bool(cfg.get("wake_switch_input", True)),
                                          lambda v: app.apply_setting("wake_switch_input", v)).pack())
        # 交接主屏会把所有屏平移，Windows 会顺手把应用窗口挤到一起且不搬回来
        self.row(off, "改显示器时窗口跟着屏走",
                 tip="拓扑变化后把应用窗口搬回原来那块屏；关掉则完全交给 Windows",
                 builder=lambda p: Switch(p, bool(cfg.get("window_follow", True)),
                                          lambda v: app.apply_setting("window_follow", v)).pack())
        # 位置记录不会自动覆盖（交接会把屏挪来挪去，那一刻的位置不是用户摆的），
        # 所以给一个主动更新的入口 —— 这是"记住布局"的唯一途径
        self.row(off, "记住当前显示器布局",
                 tip="断开/接回都按这份位置摆放；自己挪过屏之后点一次",
                 builder=lambda p: self._build_remember_btn(p, app))

        def _restore_delay(p):
            spin = ttk.Spinbox(p, from_=0, to=120, width=5, justify="center")
            spin.set(cfg.get("restore_delay", 10))
            spin.pack()

            def cb(_e=None):
                app.apply_setting("restore_delay", _safe_int(spin.get(), 10))
            spin.bind("<<Increment>>", cb)
            spin.bind("<<Decrement>>", cb)
            spin.bind("<FocusOut>", cb)

        self.row(off, "恢复双屏倒计时",
                 tip="点「恢复双屏」后等几秒再执行；0 = 立即恢复",
                 builder=_restore_delay)


    def _build_presets(self, app, cfg):
        pre = self.section("亮度预设")
        pbg = pre.cget("bg")
        prow = tk.Frame(pre, bg=pbg)
        prow.pack(fill="x", padx=14, pady=9)
        lbl = Label(prow, "三个快捷按钮的值", size=9, bg=pbg)
        lbl.pack(anchor="w")
        Tooltip(lbl, "主界面亮度卡片下面三个快捷按钮对应的百分比")
        spins = tk.Frame(prow, bg=pbg)
        spins.pack(anchor="w", pady=(6, 0))
        self.preset_spins = []
        for v in cfg.get("presets", [0, 75, 100]):
            sp = ttk.Spinbox(spins, from_=0, to=100, width=5, justify="center")
            sp.set(v)
            sp.pack(side="left", padx=(0, 8))
            sp.bind("<FocusOut>", lambda e: app.apply_presets_from_settings())
            self.preset_spins.append(sp)


    def _build_about(self, app, cfg):
        about = self.section("关于")
        # v2.01.14：5 行压成 3 行 —— 版本/路径并一行，两个诊断动作并排
        self.row(about, "版本 / 配置位置", "",
                 right_text="v%s · %%APPDATA%%\\MonitorBrightness" % APP_VERSION)

        def _diag_controls(p):
            box = tk.Frame(p, bg=p.cget("bg"))
            Button(box, "导出", app.export_diag, kind="link",
                   height=22, font_size=8, width=34).pack(side="right")
            Button(box, "日志", app.open_log_dir, kind="link",
                   height=22, font_size=8, width=30).pack(side="right", padx=(0, 6))
            Switch(box, bool(cfg.get("diag_log", True)),
                   lambda v: app.apply_setting("diag_log", v)).pack(side="right", padx=(0, 8))
            box.pack()

        self.row(about, "诊断", tip="开关 + 日志/导出；问题收敛后可关掉",
                 builder=_diag_controls)
