# -*- coding: utf-8 -*-
"""自绘控件（v2.03.0 U2，从 monitor_brightness.py 逐字搬出）。

依赖方向：widgets → theme（配色/字体/位图）+ domain.timeutil（时间/星期）。
**禁止** widgets 反向 import 主文件或 App —— 那会形成循环。

搬移顺序按继承关系：Button 必须在 _SlotRow 之前，基类必须先定义。
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from domain.timeutil import (
    WEEKDAY_LABELS, clamp_level, days_label, normalize_days,
    normalize_time_input, shift_time,
)
from ui.theme import UI, _aa_circle, _aa_rounded, _ui_font


class ScrollArea(ttk.Frame):
    """可滚动内容容器（Canvas + 内部 Frame + 滚动条）。

    设置项一多窗口就会超过 1080p 的可用高度（实测 5 个档位时 1117px），
    底部控件就点不到了。高度按屏幕封顶，超出才出现滚动条。
    """

    def __init__(self, root, bg, on_fit=None):
        super().__init__(root)
        self._on_fit = on_fit
        # 标题栏 + 上下边距在部分 DPI 下不止 140px，留足余量避免窗口底边裁切
        self._max_h = max(400, self.winfo_screenheight() - 180)
        self.canvas = tk.Canvas(self, background=bg, highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, background=bg)
        self._inner = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        # 滚轮交给全局路由（见 _route_wheel）：不再用 <Enter>/<Leave> 绑 bind_all。
        # 老写法有两个坑：鼠标直接进到子控件时 Canvas 收不到 <Enter>（P-27）；
        # <Leave> 里的 unbind_all 还会把弹层的绑定一起清掉。
        self.canvas._wheel_target = self._on_wheel

    def _on_wheel(self, event):
        # 内容放得下就别吞滚轮，让上层容器有机会处理
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return False
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return True

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._inner, width=event.width)

    def _on_body(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        need = self.body.winfo_reqheight() > self._max_h
        if need and not self.vbar.winfo_ismapped():
            self.vbar.pack(side="right", fill="y")
        elif not need and self.vbar.winfo_ismapped():
            self.vbar.pack_forget()
        self.canvas.configure(
            width=self.body.winfo_reqwidth() + (16 if need else 0),
            height=min(self.body.winfo_reqheight(), self._max_h))

    def refresh(self):
        self.body.update_idletasks()
        self._on_body()
        if self._on_fit is not None:
            self._on_fit()


class Card(tk.Canvas):
    """圆角卡片容器：Pillow 抗锯齿圆角背景 + 内部 Frame（create_window 承载内容）。

    旧实现"四角盖小 Canvas 啃出圆角"没有抗锯齿，锯齿感和设计稿差距大。
    现在整张卡片是一块 4x 超采样渲染的圆角贴图（四角透明，直接露出父容器
    底色，不再需要 page_bg 参与抠角）。Canvas 不传播子控件尺寸，
    宽高在这里按内部 Frame 的请求值显式设置。
    """

    R = 12

    def __init__(self, parent, pad=14, bg=None, page_bg=None):
        bg = bg or UI["card"]
        page = page_bg or parent.cget("bg")  # 兼容旧签名；透明角自然露出
        super().__init__(parent, bg=page, highlightthickness=0, bd=0)
        self._bg = bg
        self._pad = pad
        self.inner = tk.Frame(self, bg=bg)
        self._img = None
        self._win = self.create_window(pad, pad, window=self.inner, anchor="nw")
        self._painted = None   # (w, h) 已渲染尺寸；尺寸没变就绝不重画
        self.inner.bind("<Configure>", lambda e: self._resize())
        self.bind("<Configure>", lambda e: self._paint())

    def _resize(self):
        # 值没变就不 configure：configure 会再触发 <Configure>，
        # 守卫不住就和 _paint 互相喂事件（布局抖动的来源）
        w = self.inner.winfo_reqwidth() + self._pad * 2
        h = self.inner.winfo_reqheight() + self._pad * 2
        if (w, h) != (self.winfo_width(), self.winfo_height()):
            self.configure(width=w, height=h)

    def _paint(self):
        w = max(self.winfo_width(), 2)
        h = max(self.winfo_height(), 2)
        if (w, h) == self._painted:
            return
        self._painted = (w, h)
        ph = _aa_rounded(w, h, self.R, self._bg)
        if self._img is None:
            self._img = self.create_image(0, 0, anchor="nw", image=ph)
        else:
            self.itemconfig(self._img, image=ph)
        self.tag_lower(self._img)
        # 内容区随卡片宽度伸展（对应旧 Frame 时代 pack fill="x" 的行为）
        self.itemconfig(self._win, width=max(1, int(w) - self._pad * 2))


class Button(tk.Canvas):
    """自绘按钮（圆角、三种语义、支持置灰）。tk.Button 做不出圆角和禁用配色。"""

    def __init__(self, parent, text, command=None, kind="secondary",
                 width=None, height=34, radius=9, pad_bg=None, font_size=10):
        self._kind = kind
        self._text = text
        self._command = command
        self._radius = radius
        self._h = height
        self._fs = font_size
        self._bg = pad_bg or parent.cget("bg")
        self._font = _ui_font(font_size, bold=(kind == "primary"))
        self._enabled = True
        self._hover = False
        fm = tkfont.Font(font=self._font)
        w = width or (fm.measure(text) + 30)
        super().__init__(parent, width=w, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _colors(self):
        k = self._kind
        if not self._enabled:
            return (UI["fill"], UI["text3"], "")
        if k == "primary":
            return (UI["accent_hover"] if self._hover else UI["accent"], "#ffffff", "")
        if k == "ghost":
            return (UI["fill"] if self._hover else UI["card"], UI["text2"], UI["sep"])
        if k == "link":
            return (self._bg, UI["accent"], "")
        return (UI["fill_hover"] if self._hover else UI["fill"], UI["text"], "")

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 2)
        h = max(self.winfo_height(), 2)
        fill, fg, outline = self._colors()
        if self._kind == "link":
            self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)
            return
        ph = _aa_rounded(w, h, self._radius, fill, outline or None,
                         1 if outline else 0)
        self.create_image(0, 0, anchor="nw", image=ph)
        self.create_text(w / 2, h / 2 + 0.5, text=self._text, fill=fg, font=self._font)

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._draw()

    def _on_press(self, _e):
        if not self._enabled:
            return
        self.configure(cursor="arrow")
        self._pressed = True
        self._draw()

    def _on_release(self, _e):
        self.configure(cursor="hand2")
        if not self._enabled or not getattr(self, "_pressed", False):
            return
        self._pressed = False
        if self._command:
            self._command()

    # ---- 外部接口 ----
    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_kind(self, kind):
        self._kind = kind
        self._font = _ui_font(self._fs, bold=(kind == "primary"))
        self._draw()

    def text(self):
        return self._text


class Switch(tk.Canvas):
    """Apple 风格开关（42×25 药丸 + 圆形滑块）。"""

    W, H = 42, 25

    def __init__(self, parent, value=False, command=None):
        self._bg = parent.cget("bg")
        super().__init__(parent, width=self.W, height=self.H, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._value = bool(value)
        self._command = command
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _draw(self):
        self.delete("all")
        on = self._value
        self.create_image(0, 0, anchor="nw",
                          image=_aa_rounded(self.W, self.H, self.H / 2.0,
                                            UI["green"] if on else UI["fill_off"]))
        x = self.W - 12.5 if on else 12.5
        self.create_image(x, self.H / 2.0,
                          image=_aa_circle(20, "#ffffff"), anchor="c")

    def _toggle(self, _e=None):
        self._value = not self._value
        self._draw()
        if self._command:
            self._command(self._value)

    def get(self):
        return self._value

    def set(self, value):
        self._value = bool(value)
        self._draw()


class Slider(tk.Canvas):
    """自绘滑块：细轨道 + 已填充段 + 圆形手柄，松手才回调（避免刷 DDC）。"""

    def __init__(self, parent, value=50, command=None, on_move=None,
                 height=26, pad_bg=None):
        self._bg = pad_bg or parent.cget("bg")
        super().__init__(parent, width=180, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._value = max(0, min(100, int(value)))
        self._command = command
        self._on_move = on_move
        self._dragging = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _track(self):
        w = max(self.winfo_width(), 2)
        pad = 11
        return pad, w - pad, 56.0

    def _draw(self):
        self.delete("all")
        h = max(self.winfo_height(), 2)
        x0, x1, _ = self._track()
        cy = h / 2.0
        self.create_image(x0, cy - 2, anchor="nw",
                          image=_aa_rounded(x1 - x0, 4, 2, UI["fill"]))
        kx = x0 + (x1 - x0) * self._value / 100.0
        # 填充宽度量化到 4px：拖动时避免每个像素都生成一张新贴图
        fw = max(0, int((kx - x0) / 4.0) * 4)
        if fw > 1:
            self.create_image(x0, cy - 2, anchor="nw",
                              image=_aa_rounded(fw, 4, 2, UI["accent"]))
        self.create_image(kx, cy, image=_aa_circle(16, "#ffffff"), anchor="c")

    def _value_at(self, x):
        x0, x1, _ = self._track()
        return int(round(max(0, min(100, (x - x0) * 100.0 / max(1, x1 - x0)))))

    def _on_press(self, e):
        self._dragging = True
        self._value = self._value_at(e.x)
        self._draw()
        if self._on_move:
            self._on_move(self._value)

    def _on_drag(self, e):
        self._value = self._value_at(e.x)
        self._draw()
        if self._on_move:
            self._on_move(self._value)

    def _on_release(self, _e):
        self._dragging = False
        if self._command:
            self._command(self._value)

    def get(self):
        return self._value

    def set(self, value):
        self._value = max(0, min(100, int(value)))
        self._draw()


class WeekdayPicker(tk.Frame):
    """周一到周日 7 个小按钮（多选）。

    v2.02.0：定时档位可以只在指定星期生效。选满 7 天等价于「每天」，
    内部统一存空列表（`normalize_days` 负责归一），所以旧配置/旧任务
    不需要迁移。
    """

    def __init__(self, parent, days=None, bg=None, on_change=None):
        bg = bg or parent.cget("bg")
        super().__init__(parent, bg=bg)
        self._days = set(normalize_days(days))
        self._on_change = on_change
        self._labels = []
        for i, text in enumerate(WEEKDAY_LABELS, start=1):
            lab = tk.Label(self, text=text, width=2, bd=0, cursor="hand2",
                           bg=UI["fill"], fg=UI["text2"],
                           font=_ui_font(9, bold=False))
            lab.pack(side="left", padx=(0, 4))
            lab.bind("<Button-1>", lambda _e, n=i: self._toggle(n))
            self._labels.append(lab)
        self._paint(notify=False)

    def _toggle(self, n):
        if n in self._days:
            self._days.discard(n)
        else:
            self._days.add(n)
        self._paint()

    def _paint(self, notify=True):
        for i, lab in enumerate(self._labels, start=1):
            on = i in self._days
            lab.configure(bg=UI["accent"] if on else UI["fill"],
                          fg="#ffffff" if on else UI["text2"])
        # 构造阶段不通知：此时调用方还没把 picker 挂到 self 上
        if notify and self._on_change is not None:
            self._on_change()

    def get(self):
        """返回归一后的 1-7 列表；空 = 每天。"""
        return normalize_days(sorted(self._days))

    def set(self, days):
        self._days = set(normalize_days(days))
        self._paint()


class Segmented(tk.Canvas):
    """分段控件（互斥选择）。用整块 Canvas 画，避免 ttk 按钮的方角。"""

    def __init__(self, parent, items, index=0, command=None, height=32):
        self._bg = parent.cget("bg")
        super().__init__(parent, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.items = list(items)
        self.index = index
        self._command = command
        self._h = height
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 2)
        h = self._h
        self.create_image(0, 0, anchor="nw",
                          image=_aa_rounded(w, h, 8, UI["fill"]))
        n = max(len(self.items), 1)
        seg = (w - 4) / float(n)
        for i, label in enumerate(self.items):
            x0 = 2 + i * seg
            if i == self.index:
                self.create_image(x0 + 1, 3, anchor="nw",
                                  image=_aa_rounded(seg - 2, h - 6, 7, UI["seg_on"]))
            fg = UI["text"] if i == self.index else UI["text2"]
            self.create_text(x0 + seg / 2, h / 2, text=label, fill=fg,
                             font=_ui_font(9, bold=(i == self.index)))

    def _click(self, e):
        w = max(self.winfo_width(), 2)
        seg = (w - 4) / float(max(len(self.items), 1))
        idx = int(max(0, min(len(self.items) - 1, (e.x - 2) // seg)))
        if idx != self.index:
            self.index = idx
            self._draw()
            if self._command:
                self._command(idx)

    def get(self):
        return self.index

    def set(self, index):
        self.index = index
        self._draw()


class Label(tk.Label):
    """统一字色/底色的文字控件（省掉到处传 bg 的噪音）。"""

    def __init__(self, parent, text="", size=9, color=None, bold=False,
                 bg=None, anchor="w", **kw):
        super().__init__(parent, text=text,
                         bg=bg or parent.cget("bg"),
                         fg=color or UI["text"],
                         font=_ui_font(size, bold), anchor=anchor, **kw)


class SectionTitle(tk.Label):
    def __init__(self, parent, text):
        super().__init__(parent, text=text, bg=UI["bg"], fg=UI["text3"],
                         font=_ui_font(8, bold=True), anchor="w")


class Tooltip:
    """鼠标悬停说明气泡（v2.01.15）。

    设置页原先每行都摆一行小字说明，太占地方。改成悬停才显示：
      - 延迟 400ms 才弹，鼠标划过时不闪；
      - 跟随鼠标、怕超出屏幕会自动翻边；
      - 一个控件一个实例，绑定 Enter/Leave/FocusOut/ButtonPress 全覆盖，
        避免"弹窗关了气泡还留在屏幕上"。
    """

    DELAY_MS = 400

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._after = None
        self._win = None
        self._bound = set()
        self._bind(widget)

    def _bind(self, widget):
        if id(widget) in self._bound:
            return
        self._bound.add(id(widget))
        # add="+" 追加，绝不能替换原有绑定（v2.01.19 走查发现）：
        # Switch / Button 自己都绑了 <Enter> 做 hover 视觉，不带 add 的
        # bind() 会把它们整条覆盖掉 → 悬停说明出来了，按钮却不再高亮。
        for seq, cb in (("<Enter>", self._on_enter),
                        ("<Leave>", self._on_leave),
                        ("<ButtonPress>", self._on_leave),
                        ("<FocusOut>", self._on_leave)):
            widget.bind(seq, cb, add="+")

    def bind_tree(self, widget=None):
        """递归给整棵子控件树绑定（v2.01.16）。

        Tk 的 <Enter> 不冒泡：鼠标停在 Label/Switch 子控件上时，绑在父
        Frame 上的回调根本不会触发 —— 这就是 v2.01.15"悬停不显示"的根因。
        row 构建完（builder 之后）调用本方法，把子控件全绑上。
        """
        widget = widget if widget is not None else self.widget
        self._bind(widget)
        for child in widget.winfo_children():
            self.bind_tree(child)

    def _on_enter(self, _e=None):
        if self._after is not None:
            return
        self._after = self.widget.after(self.DELAY_MS, self._show)

    def _on_leave(self, _e=None):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self._win is not None:
            self._win.destroy()
            self._win = None

    def _show(self):
        self._after = None
        if self._win is not None or not self.text:
            return
        try:
            w = tk.Toplevel(self.widget)
            w.withdraw()
            w.overrideredirect(True)
            # 设置弹层是 grab_set() 模态窗口：不加 topmost 的话气泡会被
            # 它压在下面，表现就是"有的行能看到、有的看不到"（Z 序随机）。
            # 交叉验证：StackOverflow #9135302 / #69869659 —— topmost 是
            # 模态/置顶父窗口下 tooltip 可见的标准解。
            w.attributes("-topmost", True)
            w.configure(bg=UI["accent"])
            # 1px 边框 + 内部标签，深色主题下气泡有清晰边界
            inner = tk.Frame(w, bg=UI["card_alt"])
            inner.pack(padx=1, pady=1)
            tk.Label(inner, text=self.text, bg=UI["card_alt"], fg=UI["text"],
                     font=_ui_font(8), justify="left", wraplength=320).pack(
                padx=8, pady=6)
            w.update_idletasks()
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            ww, wh = w.winfo_width(), w.winfo_height()
            sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
            if x + ww > sw - 8:
                x = max(8, sw - ww - 8)
            if y + wh > sh - 8:
                y = max(8, self.widget.winfo_rooty() - wh - 6)
            w.geometry("+%d+%d" % (x, y))
            w.deiconify()
            self._win = w
        except Exception:
            self._win = None


class TimeField(tk.Frame):
    """时间输入（P1：替代左右拖动/纯输入框）。

    形态：可直接键入 HH:MM + ▲▼ 上下步进 15 分钟 + 四个常用预设。
    键盘：输入框内 ↑/↓ 也能步进，回车/失焦自动补零（9:5 → 09:05）。
    """

    PRESETS = ("08:30", "12:00", "18:00", "21:00")
    STEP = 15

    def __init__(self, parent, value="12:00", bg=None):
        bg = bg or parent.cget("bg")
        super().__init__(parent, bg=bg)
        top = tk.Frame(self, bg=bg)
        top.pack(anchor="w", fill="x")
        self.var = tk.StringVar(value=normalize_time_input(value) or "12:00")
        self.entry = tk.Entry(
            top, textvariable=self.var, width=6, justify="center",
            bg=UI["fill"], fg=UI["text"], insertbackground=UI["text"],
            disabledbackground=UI["fill"], disabledforeground=UI["text3"],
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=UI["sep"], highlightcolor=UI["accent"],
            font=_ui_font(11))
        self.entry.pack(side="left", ipady=3)
        step = tk.Frame(top, bg=bg)
        step.pack(side="left", padx=(6, 0))
        self.up_btn = Button(step, "▲", lambda: self.step(self.STEP), kind="ghost",
                             width=24, height=15, radius=4, font_size=7, pad_bg=bg)
        self.up_btn.pack()
        self.down_btn = Button(step, "▼", lambda: self.step(-self.STEP), kind="ghost",
                               width=24, height=15, radius=4, font_size=7, pad_bg=bg)
        self.down_btn.pack(pady=(2, 0))
        self.entry.bind("<Return>", self._commit)
        self.entry.bind("<FocusOut>", self._commit)
        self.entry.bind("<Up>", lambda e: self.step(self.STEP))
        self.entry.bind("<Down>", lambda e: self.step(-self.STEP))

        presets = tk.Frame(self, bg=bg)
        presets.pack(anchor="w", pady=(6, 0))
        Label(presets, "常用", size=8, color=UI["text3"], bg=bg).pack(
            side="left", padx=(0, 6))
        self.preset_btns = []
        for p in self.PRESETS:
            b = Button(presets, p, lambda t=p: self.set_time(t), kind="ghost",
                       height=22, radius=7, font_size=8, pad_bg=bg)
            b.pack(side="left", padx=(0, 4))
            self.preset_btns.append(b)

    def _commit(self, _event=None):
        """键入内容规整成 HH:MM；非法则保留原文，等保存时再报错。"""
        norm = normalize_time_input(self.var.get())
        if norm:
            self.var.set(norm)
        return "break"

    def step(self, delta):
        self.var.set(shift_time(self.var.get(), delta))
        return "break"

    def set_time(self, text):
        norm = normalize_time_input(text)
        if norm:
            self.var.set(norm)

    def get_time(self):
        return normalize_time_input(self.var.get())


class _SlotRow(Button):
    """时间段行：在 Button 底色之上自绘 时间 / 亮度 / chip。

    为什么是 Button 的子类：hover 时 Button._draw 会 delete("all") 重画底色，
    旧版把行内容"额外"画上去，鼠标一扫文字就被抹掉（用户实测）。重写 _draw
    让底色和内容同一次绘制，hover 重画天然完整。
    """

    def __init__(self, parent, slot, command, pad_bg):
        self._slot = dict(slot)
        self._dim = False
        super().__init__(parent, "", command, kind="ghost",
                         height=34, pad_bg=pad_bg)

    def set_slot(self, slot):
        self._slot = dict(slot)
        self._draw()

    def set_dim(self, dim):
        """定时未启用时整行文字变暗。"""
        self._dim = bool(dim)
        self._draw()

    def _draw(self):
        Button._draw(self)          # 底色（含 hover 态）
        cv = self
        w = max(cv.winfo_width(), 2)
        h = 34
        slot = self._slot
        t_fg = UI["text3"] if self._dim else UI["text"]
        s_fg = UI["text3"] if self._dim else UI["text2"]
        cv.create_text(14, h / 2, text=slot.get("time", "--:--"), anchor="w",
                       fill=t_fg, font=_ui_font(11, bold=True))
        cv.create_text(78, h / 2, text="%d%%" % clamp_level(slot.get("level", 0)),
                       anchor="w", fill=s_fg, font=_ui_font(9))
        # v2.02.0：只在"不是每天"时才标出来——每天都标一遍纯属噪音
        days = normalize_days(slot.get("days"))
        if days:
            cv.create_text(132, h / 2, text=days_label(days), anchor="w",
                           fill=s_fg, font=_ui_font(8))
        act = slot.get("screen", "")
        if act:
            label = "同时熄灭" if act == "off" else "同时点亮"
            fill = UI["accent_soft"]
        else:
            label, fill = "不变", UI["fill"]
        tw = int(tkfont.Font(font=_ui_font(8)).measure(label)) + 16
        cv.create_image(w - 14 - tw, 9, anchor="nw",
                        image=_aa_rounded(tw, 16, 8, fill))
        cv.create_text(w - 14 - tw / 2, 17, text=label, anchor="c",
                       fill=UI["accent"] if act else UI["text3"],
                       font=_ui_font(8))

