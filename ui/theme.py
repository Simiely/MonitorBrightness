# -*- coding: utf-8 -*-
"""外观层：配色、字体、位图合成、ttk 样式、DPI。

v2.03.0 U1：从 monitor_brightness.py 逐字搬出（行为零变化）。
配色与位图放同一模块，是因为 install_dark_tickbox 要用 UI 配色，
拆两个文件会产生 theme ↔ graphics 的循环依赖。

⚠️ Pillow 是**可选依赖**：缺它时本模块必须还能 import 成功，
否则连 CLI（--set / --apply-schedule）都会在 import 阶段挂掉。
（历史坑：曾有第二句模块级 `from PIL import ...` 击穿了这层守卫。）
"""
import ctypes
import sys

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

try:
    from PIL import Image, ImageDraw, ImageTk
    PIL_OK = True
except Exception:
    Image = ImageDraw = ImageTk = None
    PIL_OK = False


# ---------- 深色主题配色 ----------
# 设计取向：中性灰打底（不带蓝味），强调色只出现在「主按钮 / 勾选态 / 列表选中」三处，
# 分组标题用常规字重的浅灰而非彩色粗体，避免满屏蓝造成的"AI 感"。
DARK = {
    "bg": "#1e1e1e",          # 窗口底色
    "surface": "#252525",     # 分组框底色
    "field": "#191919",       # 输入框、列表底色
    "button": "#2f2f2f",      # 常规按钮
    "button_hover": "#3a3a3a",
    "border": "#333333",
    "fg": "#e4e4e4",
    "fg_dim": "#9a9a9a",
    "group_fg": "#b8b8b8",    # 分组标题（浅灰，不用彩色粗体）
    "accent": "#2f5d8a",      # 克制的钢板蓝
    "accent_hover": "#3a6fa0",
    "accent_fg": "#ffffff",
    "sel_bg": "#33445c",      # 列表选中：低饱和蓝灰
    "sel_fg": "#ffffff",
    "slider": "#8f8f8f",      # 滑块：中性灰，不用高饱和色
}


FONT_UI = ("Microsoft YaHei UI", 9)


# 新版界面配色（Apple 深色模式近似值）：卡片比窗口底色亮一档，
# 文字分三级（主 / 次 / 弱），强调色只出现在主按钮与可点文本上。
UI = {
    "bg": "#0f0f11",
    "card": "#1c1c1e",
    "card_alt": "#242426",
    "fill": "#2a2a2d",
    "fill_hover": "#343438",
    "fill_off": "#3a3a3d",
    "seg_on": "#48484c",
    "sep": "#2f2f31",
    "text": "#f2f2f4",
    "text2": "#a1a1a6",
    "text3": "#6c6c70",
    "accent": "#0a84ff",
    "accent_hover": "#3d9bff",
    "accent_soft": "#1d3a5c",
    "green": "#32d74b",
    "orange": "#ff9f0a",
}


# ============================================================
# 五、深色主题
# ============================================================
def apply_dark_theme(root):
    D = DARK
    style = ttk.Style(root)
    try:
        style.theme_use("clam")   # clam 是内置主题里最容易改色的
    except Exception:
        pass
    root.configure(bg=D["bg"])

    style.configure(".", background=D["bg"], foreground=D["fg"],
                    fieldbackground=D["field"], bordercolor=D["border"],
                    lightcolor=D["surface"], darkcolor=D["surface"],
                    focuscolor=D["accent"], font=FONT_UI)
    style.configure("TFrame", background=D["bg"])
    style.configure("Surface.TFrame", background=D["surface"])
    style.configure("TLabel", background=D["bg"], foreground=D["fg"])
    style.configure("Dim.TLabel", background=D["bg"], foreground=D["fg_dim"])
    style.configure("Hint.TLabel", background=D["surface"], foreground=D["fg_dim"])
    # 结论行：surface 底 + 正常字色（比 Hint 更"显眼"，一眼能读到结果）
    style.configure("Value.TLabel", background=D["surface"], foreground=D["fg"])

    style.configure("TLabelframe", background=D["surface"], bordercolor=D["border"],
                    relief="solid", borderwidth=1)
    # 分组标题：常规字重的浅灰，不做彩色粗体
    style.configure("TLabelframe.Label", background=D["surface"], foreground=D["group_fg"],
                    font=FONT_UI)

    style.configure("TButton", background=D["button"], foreground=D["fg"],
                    bordercolor=D["border"], focuscolor=D["button"],
                    relief="flat", padding=(10, 5), font=FONT_UI)
    style.map("TButton",
              background=[("pressed", D["button_hover"]), ("active", D["button_hover"]),
                          ("disabled", D["surface"])],
              foreground=[("disabled", D["fg_dim"])],
              bordercolor=[("active", D["border"])])
    style.configure("Accent.TButton", background=D["accent"], foreground=D["accent_fg"],
                    bordercolor=D["accent"])
    style.map("Accent.TButton",
              background=[("pressed", D["accent"]), ("active", D["accent_hover"]),
                          ("disabled", D["surface"])],
              foreground=[("disabled", D["fg_dim"])],
              bordercolor=[("active", D["accent_hover"])])

    # Combobox（定时行的「屏幕」下拉框）：ttk 输入框部分走 style；
    # 弹出的下拉列表是原生 Listbox，必须走 option_add 才能变深色
    style.configure("TCombobox", fieldbackground=D["surface"], background=D["surface"],
                    foreground=D["fg"], arrowcolor=D["fg"], bordercolor=D["border"],
                    lightcolor=D["surface"], darkcolor=D["surface"])
    style.map("TCombobox",
              fieldbackground=[("readonly", D["surface"])],
              foreground=[("readonly", D["fg"])],
              bordercolor=[("active", D["accent"])])
    # ttk 弹层路径可能不含 TCombobox 前缀，两种模式都设（Listbox 全项目仅下拉框使用）
    for _pat in ("*TCombobox*Listbox.", "*Listbox."):
        root.option_add(_pat + "background", D["surface"])
        root.option_add(_pat + "foreground", D["fg"])
        root.option_add(_pat + "selectBackground", D["accent"])
        root.option_add(_pat + "selectForeground", "#ffffff")
        root.option_add(_pat + "font", FONT_UI)

    # 勾选框不用 ttk.Checkbutton：clam 主题的"选中"态画的是**叉**（X）而不是勾，
    # 语义别扭；经典 tk.Checkbutton 的 selectcolor 又会让"未勾选"也带颜色。
    # 改用下面的自绘 CheckBox 类（空框 / 强调色底 + 白勾，状态一眼可辨）。

    for name in ("TEntry", "TSpinbox"):
        style.configure(name, fieldbackground=D["field"], foreground=D["fg"],
                        insertcolor=D["fg"], bordercolor=D["border"],
                        lightcolor=D["border"], darkcolor=D["border"],
                        arrowcolor=D["fg_dim"], padding=3)
        style.map(name,
                  fieldbackground=[("disabled", D["surface"]), ("readonly", D["field"])],
                  foreground=[("disabled", D["fg_dim"])],
                  arrowcolor=[("disabled", D["border"])])

    # 滑块用中性灰；槽比背景略深一点点即可，不用高饱和色
    style.configure("TScale", background=D["slider"], troughcolor=D["field"],
                    bordercolor=D["border"], lightcolor=D["slider"], darkcolor=D["slider"])

    # 滚动条（内容区超过屏幕高度时才出现）
    style.configure("Vertical.TScrollbar", background=D["button"],
                    troughcolor=D["bg"], bordercolor=D["bg"],
                    arrowcolor=D["fg_dim"], lightcolor=D["button"],
                    darkcolor=D["button"])
    style.map("Vertical.TScrollbar",
              background=[("active", D["button_hover"])])

    # 勾选框：换掉指示器元素（见 install_dark_tickbox 里的依据说明）
    install_dark_tickbox(style, root)


def apply_tk_scaling(root):
    """DPI 感知之后，把 Tk 的"逻辑像素/点"比例同步成系统缩放。

    v2.02.1：不设的话，Tk 内部仍按 96 DPI 排版，字体和控件在 125% 屏上
    会小一号（虽然不再模糊）。Tk 的 scaling 语义是"每点多少像素"：
      100% → 96/72 = 1.3333；125% → 120/72 = 1.6667；150% → 144/72 = 2.0
    即 `百分比 / 75`。取不到就保持 Tk 默认，不影响启动。
    """
    try:
        import ctypes
        pct = ctypes.windll.shcore.GetScaleFactorForDevice(0)
        if pct and 50 <= pct <= 500:
            root.tk.call("tk", "scaling", pct / 75.0)
    except Exception:
        pass


# ============================================================
# 七、图形界面（tkinter · Apple 风格深色）
# ------------------------------------------------------------
# 设计取向（2026-11 改版，参照 Apple HIG 的 Clarity / Deference / Depth）：
#   - 主界面只放日常操作：状态卡 + 显示器 + 亮度 + 定时；
#   - 托盘 / 自启 / 熄灭方式 / 预设数值 全部收进「设置」；
#   - 解释性文案收进「说明」，界面上不再铺满灰色小字；
#   - 分组用卡片 + 行分隔，动作按钮按"当前可执行"决定主次并置灰不可用项。
# tkinter 没有圆角/模糊：圆角用 4 个角上的 Canvas 画四分之一圆补出来，
# 弹层没有毛玻璃，用纯色遮罩代替 —— 其余结构 1:1 还原设计稿。
# ============================================================
# 自绘指示器图片的引用要留着，PhotoImage 被回收后控件就空白了
_TICKBOX_IMAGES = []


def _tickbox_image(size, fill, border, mark=None, mark_color=None):
    """抗锯齿圆角勾选框（Pillow 4x 渲染 + LANCZOS 缩小，四角透明）。

    mark 传对勾折线的顶点序列 [(x, y), ...]（1x 坐标），用带 joint 的
    宽线绘制，比逐像素 put 平滑得多。
    """
    img = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size * 4 - 1, size * 4 - 1],
                        radius=4 * 4, fill=_hex_rgba(fill))
    if mark:
        pts = [(x * 4, y * 4) for x, y in mark]
        d.line(pts, fill=_hex_rgba(mark_color or "#ffffff"),
               width=8, joint="curve")
    out = img.resize((size, size), Image.LANCZOS)
    ph = ImageTk.PhotoImage(out)
    _TICKBOX_IMAGES.append(ph)
    return ph


def install_dark_tickbox(style, root=None):
    """把 TCheckbutton 的指示器换成自绘图片，得到深色下可辨认的勾选框。

    依据：本机 Tk 自带的 clamTheme 对勾选框只开放 -indicatorbackground，
    选中态图形是 C 层画的，改 indicatorcolor 无效（实测无白色像素）。
    正当做法是 ttk::style 的 element_create + layout 换掉 Checkbutton.indicator，
    这样仍然是原生 ttk.Checkbutton，变量绑定/键盘空格/禁用态都保留。
    """
    size = 15
    img_off = _tickbox_image(size, UI["fill"], UI["fill"])
    img_on = _tickbox_image(size, UI["accent"], UI["accent"],
                            mark=((3.5, 7.5), (6.2, 10.8), (11.2, 4.5)),
                            mark_color="#ffffff")
    img_off_dim = _tickbox_image(size, UI["card"], UI["sep"])

    try:
        style.element_create("Dark.tickbox", "image", img_off,
                             ("selected", img_on),
                             ("disabled", img_off_dim))
    except Exception:
        pass
    style.configure("TCheckbutton", background=UI["card"], foreground=UI["text"],
                    indicatormargin=(0, 0, 8, 0), padding=0)
    style.map("TCheckbutton",
              background=[("active", UI["card"])],
              foreground=[("disabled", UI["text3"])])
    style.layout("TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Dark.tickbox", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])


def _ui_font(size, bold=False):
    return ("Microsoft YaHei UI", size, "bold") if bold else ("Microsoft YaHei UI", size)


_AA_CACHE = {}


_AA_REFS = []


_AA_EPOCH = [None]


def _aa_sync_epoch():
    """缓存的 PhotoImage 属于**某个 Tk 解释器**；解释器换了旧图必然失效。

    v2.03.0（写 UI 冒烟测试时暴露）：单测会反复建/销毁 Tk，模块级缓存却留着
    上一次的 PhotoImage，再用就抛 `TclError: image "pyimageN" doesn't exist`。
    正常 GUI 全程同一 root 不会遇到，但"同进程重建 GUI"和"测试"都会。
    这里按解释器标识清一次缓存，比在每个调用点 try/except 更干净。
    """
    ep = id(getattr(tk, "_default_root", None))
    if _AA_EPOCH[0] != ep:
        _AA_CACHE.clear()
        _AA_REFS.clear()
        _AA_EPOCH[0] = ep


def _hex_rgba(color):
    c = str(color).lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255)


def _aa_rounded(w, h, radius, fill, outline=None, ow=0):
    """抗锯齿圆角矩形 PhotoImage（4x 超采样 + LANCZOS 缩小，带缓存）。"""
    _aa_sync_epoch()
    key = ("r", int(w), int(h), int(radius), fill, outline, int(ow))
    got = _AA_CACHE.get(key)
    if got is not None:
        return got
    img = Image.new("RGBA", (max(1, int(w)) * 4, max(1, int(h)) * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, max(1, int(w)) * 4 - 1, max(1, int(h)) * 4 - 1],
                        radius=max(1, int(radius) * 4), fill=_hex_rgba(fill),
                        outline=_hex_rgba(outline) if outline else None,
                        width=int(ow) * 4 if outline else 0)
    img = img.resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)
    ph = ImageTk.PhotoImage(img)
    _AA_CACHE[key] = ph
    _AA_REFS.append(ph)
    return ph


def _aa_circle(dia, fill):
    """抗锯齿圆形 PhotoImage（圆点 / 滑块手柄 / 开关圆钮共用）。"""
    _aa_sync_epoch()
    key = ("c", int(dia), fill)
    got = _AA_CACHE.get(key)
    if got is not None:
        return got
    img = Image.new("RGBA", (max(1, int(dia)) * 4, max(1, int(dia)) * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([1, 1, max(1, int(dia)) * 4 - 1, max(1, int(dia)) * 4 - 1],
              fill=_hex_rgba(fill))
    img = img.resize((max(1, int(dia)), max(1, int(dia))), Image.LANCZOS)
    ph = ImageTk.PhotoImage(img)
    _AA_CACHE[key] = ph
    _AA_REFS.append(ph)
    return ph



def apply_dark_titlebar(root):
    """把 Windows 标题栏切成深色（DWM 沉浸式深色模式）。非致命，失败就跳过。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):   # 20：Win10 20H1+ / Win11；19：更早的 Win10
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass

