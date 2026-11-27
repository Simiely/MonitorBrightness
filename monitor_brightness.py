#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示器亮度定时调节 - 深色主题 GUI 工具 (DDC/CI) [tkinter 轻量版]

功能:
  - 深色主题界面（含 Windows 标题栏沉浸式深色）
  - 显示器多选列表：可一次对多台显示器套用同一下发亮度，带「刷新」重新枚举
  - 设置白天/晚上两个时间点与目标亮度，写入 Windows 任务计划程序
  - 定时任务不依赖常驻进程：到点由系统调用本程序的命令行模式执行
  - 支持命令行模式:
      MonitorBrightness.exe --set <序号|all|0,1> <亮度>   立即调整
      MonitorBrightness.exe --apply-schedule              按「当前时间」判定档位并下发
      MonitorBrightness.exe --list                        列出显示器
      MonitorBrightness.exe --probe                       实测每台显示器的亮灭
      MonitorBrightness.exe --diag                        只读诊断报告（排查点不亮）
      MonitorBrightness.exe --diag-log                    打印最近的诊断日志（排障用）
      MonitorBrightness.exe --diag-export                 导出诊断包 zip（日志+配置）
      MonitorBrightness.exe --screen off|on|status        熄灭 / 点亮选中的显示器
      MonitorBrightness.exe --selftest                    自检（打印时间分档判定结果）

v1.1.0 相比 v1.0.0：
  1. 深色主题（ttk clam 自定义配色 + DWM 标题栏深色）
  2. 显示器由单选下拉改为多选列表 + 刷新
  3. 定时修复：改用 PowerShell 的 Register-ScheduledTask 并开启 StartWhenAvailable
     （schtasks.exe 命令行没有这个开关，这是 v1.0.0 定时会漏跑的根本原因）；
     任务动作统一改为 --apply-schedule，由程序按执行时刻自行判定该用哪一档，
     因此「白天/晚上两个任务都被补跑」时结果依然一致（幂等）；
     另加一条登录触发任务，保证开机时间已晚于切换点时也能立刻补上。
"""
import sys

# ── DPI 声明：必须在 import tkinter 之前 ────────────────────────────────
# v2.02.1：本程序原先没有 DPI 感知声明，Windows 会把整个窗口当"低 DPI 老程序"
# 做位图拉伸 —— 在 125%/150% 缩放下字体发虚、边框毛糙（多来源一致结论）。
# 这里用「系统级感知」(1)：单屏/同缩放多屏完全正确，代价是窗口拖到
# 缩放比例不同的屏时需要重启才会重新适配。
# 不用 (2) Per-Monitor：Tk 8.6 不响应 WM_DPICHANGED，跨屏会错乱甚至崩溃，
# 多个来源都明确建议 Tk 程序不要开 2。
try:
    import ctypes as _dpi_ctypes
    try:
        _dpi_ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                    # Win7 没有 shcore
        _dpi_ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import os
import re
import math
import json
import html
import queue
import shutil
import zipfile
import hashlib
import tempfile
import threading
import subprocess
import time
import traceback
import ctypes.wintypes
from ctypes import wintypes
from datetime import datetime, timezone

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# Pillow 只有 GUI 需要（抗锯齿圆角贴图）。**必须有守卫**：
# 缺 Pillow 时 CLI（--set / --apply-schedule / --diag / --selftest）照常工作，
# GUI 在 main() 里给一句明确报错后退出，不能因为 import 失败把整个程序拖死。
try:
    from PIL import Image, ImageDraw, ImageTk
    PIL_OK = True
except Exception:
    Image = ImageDraw = ImageTk = None
    PIL_OK = False

# 托盘（可选依赖）：缺 pystray 时 GUI 照常工作，仅没有托盘/最小化到托盘能力
try:
    import pystray
    from pystray import Icon as _TrayIcon, Menu as _TrayMenu, MenuItem as _TrayItem
    TRAY_OK = True
except Exception:
    _TrayIcon = _TrayMenu = _TrayItem = None
    TRAY_OK = False

# v1.7.0 起不再需要 monitorcontrol：DDC/CI 直接走 dxva2（见「二、显示器枚举与亮度控制」）。
# 留这个开关只是为了让"缺依赖"的旧提示不再误报——真正的枚举不看它。

APP_TITLE = "显示器亮度定时调节"
APP_VERSION = "2.02.1"     # DPI 感知声明（系统级）+ tk scaling 同步


def _app_dir():
    """程序所在目录：打包态 = exe 所在目录；源码运行 = 脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _dir_writable(path):
    """真的试着写一下再删。

    `os.access(W_OK)` 在 Windows 上对 Program Files 之类的目录会返回 True，
    实际写的时候才报错，所以必须真写。
    """
    probe = os.path.join(path, ".mb-write-probe")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        try:
            os.remove(probe)
        except Exception:
            pass
        return False


# v2.01.17：配置也便携化到**程序所在目录**（用户要求）。
# 便携化是软件常见做法（SO #4897525）：配置文件放程序根目录，整个目录拷走
# 设置就跟着走。只有程序目录不可写（Program Files / 只读介质）才回退 APPDATA。
_LEGACY_CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "MonitorBrightness")
APP_DIR = _app_dir()
CONFIG_DIR = APP_DIR if _dir_writable(APP_DIR) else _LEGACY_CONFIG_DIR
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def _pick_log_dir():
    """日志放**程序同目录**（便携、出问题整个目录发给开发者就行）。

    只有程序目录不可写才回退到 APPDATA —— 装在 Program Files、只读介质、
    网络盘上时会走到这条回退路径。配置现在也在这里（v2.01.17）。
    """
    app_dir = _app_dir()
    return app_dir if _dir_writable(app_dir) else CONFIG_DIR


# 事件日志：任务重建 / 配置恢复等旁路记账落在这里，界面只读展示。
# ⚠️ 本程序启动时不会主动熄屏：启动自检只校验计划任务定义，绝不补执行任何档位。
LOG_DIR = _pick_log_dir()
EVENTS_FILE = os.path.join(LOG_DIR, "events.log")
ERROR_FILE = os.path.join(LOG_DIR, "error.log")


def _migrate_old_logs():
    """老版本把日志写在 APPDATA，换到程序目录后把现有的几份搬一次过来。

    v2.01.5 之前日志在 `%APPDATA%\\MonitorBrightness`；现在优先放程序目录，
    这样出问题时整个目录打包发出去就行。搬一次、不覆盖已有的。
    **只在 main() 里调用**：单元测试不该有这个副作用。
    """
    if os.path.abspath(LOG_DIR) == os.path.abspath(CONFIG_DIR):
        return
    for name in ("events.log", "events.log.1", "error.log", "diag.log"):
        src = os.path.join(CONFIG_DIR, name)
        dst = os.path.join(LOG_DIR, name)
        try:
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
        except Exception:
            pass

# v1.9.4：删除启动补执行与 12 小时补检阈值。启动自检只比对任务定义
# （路径/参数/触发点/StartWhenAvailable），绝不按「今天错过了哪一档」执行业务动作，
# 因此本程序启动时不会主动熄屏，也不会主动改亮度。

TASK_SCHEDULE = "MonitorBrightness_Schedule"
TASK_CATCHUP = "MonitorBrightness_CatchUp"
# v1.2.x 及更早的分档任务名：注册前先清掉，避免残留
_LEGACY_TASKS = ("MonitorBrightness_Day", "MonitorBrightness_Night")
# 任务名 → 界面短名（顺序即显示顺序）
TASK_SHORT = {TASK_SCHEDULE: "定时", TASK_CATCHUP: "登录校准"}

# 触发时刻前后允许的判定容差（分钟）：任务到点执行时可能早/晚几秒，
# 靠这个窗口保证 09:00 的白天任务不会被判成晚上（反之亦然）。
# (v1.3.0 起为 N 档调度：生效规则=「time<=now 的最后一档」，无需容差常量)

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
# 一、配置读写（设置项的唯一声明表在此，其余全部由它派生）
# ============================================================
# 为什么要有这张表：实测这些键在源码里触点 5~9 处（Fowler 的 Shotgun Surgery），
# 加一个设置要同时改 默认值表 / 校验 / 建控件 / 读表单 / 拼注册脚本 五六处。
# 现在只有这里声明一次，其余地方一律派生。
#   kind: indices 显示器序号列表 | hhmm 时间 | level 亮度 0-100 | flag 开关
DEFAULT_SLOTS = [{'time': '08:30', 'level': 80, 'screen': '', 'days': []},
                 {'time': '21:00', 'level': 0, 'screen': '', 'days': []}]

# 星期：ISO 编号 1=周一 … 7=周日。
# `days` 空列表 = 每天（兼容没有这个字段的旧配置）。
WEEKDAY_NAMES_PS = ("Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday")
WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")
WEEKDAY_PRESETS = (("每天", []), ("工作日", [1, 2, 3, 4, 5]),
                   ("周末", [6, 7]))


def normalize_days(value):
    """把各种写法规整成 1-7 的有序去重列表；选满 7 天归一成 []（=每天）。"""
    out = set()
    if isinstance(value, (list, tuple, set)):
        for d in value:
            try:
                n = int(d)
            except Exception:
                continue
            if 1 <= n <= 7:
                out.add(n)
    return [] if len(out) >= 7 else sorted(out)


def days_label(days):
    """给界面看的一句话：每天 / 工作日 / 周末 / 周一,三,五。"""
    days = normalize_days(days)
    if not days:
        return "每天"
    if days == [1, 2, 3, 4, 5]:
        return "工作日"
    if days == [6, 7]:
        return "周末"
    return "周" + ",".join(WEEKDAY_LABELS[d - 1] for d in days)


def slot_matches_weekday(slot, weekday):
    """这一档在指定星期（1=周一）该不该执行；days 空 = 每天。"""
    days = normalize_days(slot.get("days"))
    return not days or weekday in days


def slots_for_weekday(slots, weekday):
    """只留下今天生效的档位；万一全都不生效，退回全部（避免无档可判）。"""
    keep = [s for s in slots if slot_matches_weekday(s, weekday)]
    return keep or list(slots)

# 每个时间段可携带的屏幕动作：""（不变）/ off（到点熄灭）/ on（到点点亮）
SCREEN_ACTIONS = (("", "不变"), ("off", "熄灭"), ("on", "点亮"))
SCREEN_VALUE_BY_LABEL = {label: value for value, label in SCREEN_ACTIONS}
SCREEN_LABEL_BY_VALUE = {value: label for value, label in SCREEN_ACTIONS}

# 熄灭方式：system   = 系统级整组熄灭（一定唤得回来）
#           ddc      = 逐台 DDC 0xD6=2 待机（本机实测可能唤不回，仅作高级选项）
#           topology = CCD 断开副屏（串流隐藏物理屏用；默认不是它）
OFF_METHODS = ("system", "ddc", "topology")
OFF_METHOD_LABEL = {"system": "系统级", "ddc": "单屏待机", "topology": "拓扑断开"}
SETTINGS = (
    {"key": "monitors", "kind": "indices", "default": []},
    # 设备名锚点（如 ['DISPLAY1','DISPLAY2']），与 monitors 同序对应。
    # 为什么不只用下标：EnumDisplayMonitors 的顺序会随显卡驱动/接线变化而重排，
    # 只存下标就会出现"熄的是这块屏、点的是那块屏"（另一台电脑点亮失效的形态之一）。
    {"key": "monitor_ids", "kind": "ids", "default": []},
    # 快捷预设：三个按钮的亮度值，用户可改（0/75/100 是出厂默认）
    {"key": "presets", "kind": "levels", "default": [0, 75, 100]},
    {"key": "slots", "kind": "slots", "default": DEFAULT_SLOTS},
    {"key": "enabled", "kind": "flag", "default": False},
    {"key": "catch_up_on_logon", "kind": "flag", "default": True},
    {"key": "screen_auto_relight_min", "kind": "level", "default": 0},    # 熄灭后 N 分钟自动点亮（0=关）
    {"key": "tray_enabled", "kind": "flag", "default": True},             # 托盘开关（关 = 点 X 直接退出）
    # ⚠️ v2.01.0 删除了 screen_state / screen_state_at / screen_off_tick：
    # 程序不再维护"屏幕现在亮还是灭"的状态账。拓扑/系统才是唯一事实源，
    # 每次需要结论时现场读（见 screen_verdict）。旧配置里残留的同名字段
    # 会被 load_config 丢弃，不影响任何行为。
    # 熄灭方式：system   = Windows 系统级整组熄灭（一定唤得回来）
    #           ddc      = 逐台 DDC 0xD6=2 待机（个别面板唤不回）
    #           topology = CCD 断开副屏（串流隐藏物理屏用）
    # v2.01.23：默认改为 topology（用户要求）。理由：这条路不碰面板电源，
    # 只改 Windows 拓扑，唤不回的风险最低；本机面板能力串不声明 0xD6=2，
    # ddc 那条路本来就得靠"明知有风险"的授权才能走。
    # ⚠️ load_config 里把 "system 且未授权 ddc" 恢复成 ddc 的那段是历史迁移
    # 逻辑（撤销 v1.9.0 的误迁移），与这里的默认值无关，不要跟着改。
    {"key": "screen_off_method", "kind": "choice",
     "values": OFF_METHODS, "default": "topology"},
    # 点亮时是否允许"切信号源"这最后一招（仅 ddc 方式下有效）
    {"key": "wake_switch_input", "kind": "flag", "default": True},
    # v1.9.7 快速点亮：0x60 切源翻转（读当前源 → 写中间源 → 立即写回），单台 ~180ms。
    # 仅有「ddc + 允许切源 + 记账认为熄过屏」时启用；关掉=完全回到 v1.9.6 完整阶梯。
    {"key": "wake_fast_flip", "kind": "flag", "default": True},
    # 是否允许写入面板未声明的待机值（0xD6=2）。默认关；
    # 用户在界面里选择"DDC 单屏待机"并确认风险后才会置 True。
    {"key": "ddc_allow_undeclared", "kind": "flag", "default": False},
    # 拓扑模式的显示器锚点（显卡 LUID + EDID mfr/prod + connectorInstance）。
    # E1 只读层先声明；E2 才做旧 monitors/monitor_ids 的迁移。
    {"key": "monitor_anchors", "kind": "anchors", "default": []},
    # 每块屏"断开那一刻"的桌面位置与尺寸，只为接回时摆回原位。
    # **这不是状态账**：它记的是摆放偏好，而且每次断开前都按当下的真实值
    # 重写一遍 —— 用户在 Windows 里挪过屏，这里就跟着变，不会与现实脱节。
    # 断开之后这个信息就再也读不到了（Windows 会把该屏的 mode 项摘掉），
    # 所以只能在断开前抓一次。
    {"key": "monitor_positions", "kind": "positions", "default": {}},
    # 「恢复双屏」按钮的倒计时秒数（v2.01.12）：点按钮后等这么多秒再把默认
    # 双屏布局写回，给用户一个反悔（取消）的窗口。0 = 立即恢复。
    {"key": "restore_delay", "kind": "int", "default": 10, "min": 0, "max": 120},
    # 诊断日志开关（临时项，v2.01.4 引入；问题收敛后连同 dlog 一起删掉）。
    {"key": "diag_log", "kind": "flag", "default": True},
    # 交接主屏会把所有屏平移，Windows 会顺手把应用窗口挤到一起且不搬回来。
    # 打开时由本程序在动作后把窗口"跟着它的屏"搬一次（v2.01.9）。
    {"key": "window_follow", "kind": "flag", "default": True},
)
SETTING_BY_KEY = {s["key"]: s for s in SETTINGS}
CONFIG_VERSION = 6

def coerce_setting(key, value):
    """按声明表把值规整到合法域；非法/缺失一律回落默认值。"""
    spec = SETTING_BY_KEY.get(key)
    if spec is None:
        return value
    kind = spec["kind"]
    if kind == "level":
        try:
            return max(0, min(100, int(float(value))))
        except Exception:
            return spec["default"]
    if kind == "hhmm":
        return value if _valid_hhmm(value) else spec["default"]
    if kind == "flag":
        return bool(value)
    if kind == "slots":
        return _coerce_slots(value)
    if kind == "levels":
        return _coerce_levels(value, spec["default"])
    if kind == "ids":
        return _coerce_ids(value)
    if kind == "choice":
        return value if value in spec.get("values", ()) else spec["default"]
    if kind == "int":
        try:
            n = int(value)
            lo = int(spec.get("min", 0))
            hi = int(spec.get("max", 120))
            return max(lo, min(hi, n))
        except Exception:
            return spec["default"]
    if kind == "anchors":
        if not isinstance(value, (list, tuple)):
            return []
        out = []
        for a in value:
            text = str(a or "").strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= 32:
                break
        return out
    if kind == "positions":
        return _coerce_positions(value)
    return normalize_indices(value)


def _coerce_positions(value):
    """{锚点: [x, y, w, h]}；坏项直接丢掉，不让一个坏值毁掉整份配置。"""
    if not isinstance(value, dict):
        return {}
    out = {}
    for k, v in value.items():
        if not isinstance(v, (list, tuple)) or len(v) != 4:
            continue
        try:
            nums = [int(round(float(n))) for n in v]
        except Exception:
            continue
        if nums[2] <= 0 or nums[3] <= 0:      # 宽高必须为正
            continue
        out[str(k)] = nums
        if len(out) >= 16:                    # 换过的屏会留下旧锚点，封个顶
            break
    return out


def _coerce_levels(value, fallback):
    """把预设亮度规整成与默认值**等长**的 0-100 整数列表。

    长度以默认值为准（当前 3 个）：多截断、少用默认值补齐，
    这样界面永远有固定个数的按钮，不会因配置残缺而少画一个。
    """
    want = len(fallback)
    out = []
    for item in _as_iterable(value):
        if len(out) >= want:
            break
        try:
            out.append(max(0, min(100, int(float(item)))))
        except Exception:
            continue
    while len(out) < want:
        out.append(fallback[len(out)])
    return out[:want]


def _coerce_ids(value):
    """设备名锚点：去空、去重、保持顺序（如 ['DISPLAY1','DISPLAY2']）。"""
    out = []
    for item in _as_iterable(value):
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
    return out


def _valid_hhmm(text):
    """'HH:MM' 合法性（时间分档、时间段编辑共用）。"""
    try:
        h, m = str(text).strip().split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return False


def normalize_time_input(text):
    """把用户键入的各种写法规整成 'HH:MM'；非法返回 None。

    接受 `9:5` / `09：05`（全角冒号）/ `0905` 这类写法；
    这是新时间输入框的"可键入"能力，单测直接覆盖。
    """
    s = str(text or "").strip().replace("：", ":").replace(" ", "")
    m = re.match(r"^(\d{1,2}):(\d{1,2})$", s)
    if not m:
        m = re.match(r"^(\d{1,2})(\d{2})$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return "%02d:%02d" % (h, mi)


def shift_time(text, delta_min):
    """'HH:MM' ± N 分钟（跨午夜回绕），返回 'HH:MM'。"""
    base = normalize_time_input(text) or "12:00"
    h, mi = int(base[:2]), int(base[3:])
    total = (h * 60 + mi + delta_min) % (24 * 60)
    return "%02d:%02d" % (total // 60, total % 60)


def _write_event_log(text):
    """追加一行事件日志（线程安全）。返回写入的那一行。

    ⚠️ 只用于旁路记账（任务重建/配置恢复/降级重试等），不得在熄灭、点亮
    调用链里写下正常成功的实现细节 —— 那些进 diag.log（dlog），
    因为它们对用户没有意义，只会让界面看起来"一直在报东西"。
    """
    line = "%s %s" % (datetime.now().strftime("%m-%d %H:%M"), text)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with _EVENT_LOCK:
            try:
                if os.path.getsize(EVENTS_FILE) > 256 * 1024:
                    os.replace(EVENTS_FILE, EVENTS_FILE + ".1")
            except OSError:
                pass
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _SESSION_EVENTS.append(line)
            del _SESSION_EVENTS[:-40]
    except Exception:
        pass
    return line


def read_session_events(limit=3):
    """**本次运行**产生的事件最后几条。

    不读 events.log 文件：那里面是历史，界面一开机就把上周的记录摆出来，
    用户会以为"一直报错"。历史留给 --diag / 诊断包，界面只反映这一次。
    """
    return list(_SESSION_EVENTS[-limit:])


_EVENT_LOCK = threading.Lock()
_SESSION_EVENTS = []            # 本次进程写的事件，界面只看这一份


def read_recent_events(limit=3):
    """读事件日志文件最后几行（给 --diag / 诊断包用，**不再直接喂界面**）。"""
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = [x.strip() for x in f.readlines() if x.strip()]
        return lines[-limit:]
    except Exception:
        return []


# ============================================================================
# 诊断日志（临时模块，v2.01.4 引入）
# ----------------------------------------------------------------------------
# 目的：趁问题还没收敛，把"当时到底读到了什么、算了什么、提交了什么、回来什么"
# 原样落盘，省掉来回猜。**问题收敛后整块删掉**，删除清单只有四条：
#   1) 删掉本节（DIAG_FILE ~ diag_dump，含 _DIAG_* 全局量）；
#   2) 删掉 SETTINGS 里的 "diag_log" 一项（grep diag_log）；
#   3) 删掉界面里两处（设置页的开关行、日志卡片的「打开目录」按钮）；
#   4) `grep -n "dlog(" monitor_brightness.py` 把埋点行整行删掉。
# 为了好删，这里只暴露两个函数、埋点一律写成单行 dlog(...)，不写多行调用。
#
# 格式是 JSON Lines：一行一个 JSON，字段固定 t/pid/th/lv/tag + 自定义键。
# 排查时 `findstr /c:"\"tag\": \"topo.on\"" diag.log` 就能把一次接回的全过程串起来。
# ============================================================================
_DIAG_LOCK = threading.Lock()
_DIAG_ON = True                 # 由配置项 diag_log 同步；关掉后 dlog 立即返回
_DIAG_LIMIT = 512 * 1024        # 超过就轮转到 diag.log.1，只留一份


def _diag_file():
    """每次现算：测试会把 LOG_DIR 指到临时目录，不能在导入时就定死。"""
    return os.path.join(LOG_DIR, "diag.log")


def diag_set_enabled(value):
    """配置变化时同步开关（load_config / apply_setting 各调一次就够了）。"""
    global _DIAG_ON
    _DIAG_ON = bool(value)


def diag_enabled():
    return _DIAG_ON


def dlog(tag, **fields):
    """写一条诊断日志。未启用时是纯函数调用，零 IO。

    tag 用点号分段，便于按前缀筛：
      app.*   界面动作（点了哪个按钮、哪个下标）
      topo.*  拓扑读取 / 计划 / 提交 / 回读
      ccd.*   和 SetDisplayConfig 打交道的细粒度动作
      sch.*   定时档位判定
    """
    if not _DIAG_ON:
        return
    rec = {"t": datetime.now().strftime("%m-%d %H:%M:%S.%f")[:-3],
           "pid": os.getpid(), "th": threading.current_thread().name,
           "tag": tag}
    for k, v in fields.items():
        if isinstance(v, (int, float, bool)) or v is None:
            rec[k] = v
            continue
        # 结构化值原样留（事后好筛），太大才退化成字符串
        if isinstance(v, (list, tuple, dict)):
            try:
                as_json = list(v) if isinstance(v, tuple) else v
                if len(json.dumps(as_json, ensure_ascii=False)) <= 600:
                    rec[k] = as_json
                    continue
            except Exception:
                pass
        text = str(v)
        rec[k] = text if len(text) <= 600 else text[:600] + "…"
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False)
        path = _diag_file()
        with _DIAG_LOCK:
            try:
                if os.path.getsize(path) > _DIAG_LIMIT:
                    os.replace(path, path + ".1")
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def diag_dump(limit=400):
    """给界面/CLI 用：返回最近 limit 行原文（不解析，原样给）。"""
    try:
        with open(_diag_file(), "r", encoding="utf-8", errors="replace") as f:
            return [x.rstrip("\n") for x in f.readlines()[-limit:]]
    except Exception:
        return []


def diag_export(dest_dir=None):
    """把日志 + 配置打成一个 zip，出问题直接把这个文件发出去。

    只装排障需要的东西：三份日志（含轮转的 .1）、config.json、一份环境摘要。
    返回 (zip 路径, 错误)。日志目录优先用程序同目录 —— 所以 zip 也放那里，
    和 exe 并排，用户一找就能看到。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = dest_dir or LOG_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "MonitorBrightness_诊断包_%s.zip" % stamp)
        cfg = load_config()
        summary = [
            "版本：v%s" % APP_VERSION,
            "打包态：%s" % bool(getattr(sys, "frozen", False)),
            "Python：%s" % sys.version.split()[0],
            "程序目录：%s" % APP_DIR,
            "日志目录：%s" % LOG_DIR,
            "配置目录：%s" % CONFIG_DIR,
            "熄灭方式：%s" % cfg.get("screen_off_method"),
            "锚点：%s" % (cfg.get("monitor_anchors") or []),
            "位置记录：%s" % (cfg.get("monitor_positions") or {}),
            "诊断日志：%s" % ("开启" if diag_enabled() else "关闭"),
            "导出时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("diag.log", "diag.log.1", "events.log",
                         "events.log.1", "error.log"):
                src = os.path.join(LOG_DIR, name)
                if os.path.exists(src):
                    z.write(src, name)
            if os.path.exists(CONFIG_FILE):
                z.write(CONFIG_FILE, "config.json")
            z.writestr("环境.txt", "\n".join(summary) + "\n")
        dlog("app.export", path=path)
        return path, ""
    except Exception as e:
        return "", "%s: %s" % (type(e).__name__, e)


# ============================================================================
# 诊断日志结束（以上整块可删）
# ============================================================================


def _coerce_slots(value):
    """把各种写法规整成 [{time, level}]：按时间排序、时间去重、亮度 0-100、至少一档。"""
    out, seen = [], set()
    if isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, dict):
                continue
            # 兼容旧写法：9:5 / 09：05 / 0905 统一成 09:05，顺带把"同一分钟"
            # 的不同写法去重（否则会注册两个同一时刻的触发器）
            t = normalize_time_input(item.get("time")) or ""
            if not t or t in seen:
                continue
            seen.add(t)
            try:
                lv = max(0, min(100, int(float(item.get("level", 50)))))
            except Exception:
                lv = 50
            scr = str(item.get("screen", "")).strip().lower()
            if scr not in ("", "off", "on"):
                scr = ""
            out.append({"time": t, "level": lv, "screen": scr,
                        "days": normalize_days(item.get("days"))})
    if not out:                      # 至少一档，否则调度无从判定
        out = [dict(d) for d in DEFAULT_SLOTS]
    return sorted(out, key=lambda x: parse_hhmm(x["time"], (0, 0)))


def default_config():
    """默认配置 —— 由 SETTINGS 表派生（不再手写第二份默认值）。"""
    return {spec["key"]: spec["default"] for spec in SETTINGS}


def _load_model_cache(raw):
    """从配置里取型号名缓存（非用户设置）：第二次启动起秒出型号，不再读 3s 的 DDC。"""
    models = raw.get("models")
    if isinstance(models, dict):
        return {str(k): str(v) for k, v in models.items() if v}
    return {}


# 配置读取失败时的说明（GUI 启动后显式提示；CLI 不受影响）
CONFIG_LOAD_ERROR = ""


def _backup_bad_config(reason):
    """读坏时把原文件留一份副本，避免下一次保存把它彻底覆盖掉。"""
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = "%s.bad-%s.json" % (CONFIG_FILE, stamp)
        shutil.copy2(CONFIG_FILE, bak)
        return "配置文件读取失败（%s）。已备份为 %s，本次使用默认值。" % (reason, bak)
    except Exception:
        return "配置文件读取失败（%s）。备份失败，本次使用默认值。" % reason


def load_config():
    """读取配置；兼容 v1.0.0 的单选字段 monitor（int）→ v2 的 monitors（list）。

    逐键走 coerce_setting，所以新增一个设置**不用**再来这里加一行校验。
    读坏时不再静默吞掉：备份坏文件 + 把原因记到 CONFIG_LOAD_ERROR，由界面提示。
    """
    global CONFIG_LOAD_ERROR
    CONFIG_LOAD_ERROR = ""
    cfg = default_config()
    _recovered_off_method = False
    if not os.path.exists(CONFIG_FILE):
        return cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("顶层不是 JSON 对象")
        # v2 -> v3 迁移：day/night 四键 -> slots 两档
        if "slots" not in raw and "day_time" in raw:
            raw = dict(raw, slots=[
                {"time": raw.get("day_time", "09:00"),
                 "level": raw.get("day_level", 80)},
                {"time": raw.get("night_time", "20:00"),
                 "level": raw.get("night_level", 30)}])
        # v1 -> v2 迁移：旧配置只有 "monitor": 0
        if "monitors" not in raw and "monitor" in raw:
            raw = dict(raw, monitors=[raw["monitor"]])
        # v1.9.3：撤销 v1.9.0 的「旧配置 ddc → system」自动迁移。
        # 迁移的判别特征是「system 且未允许未声明的 0xD6=2」；这里按同样特征
        # 反向恢复为 v1.8.5 的默认 ddc。改动必须落日志，不静默改用户配置。
        if (raw.get("screen_off_method") == "system"
                and not raw.get("ddc_allow_undeclared")):
            raw = dict(raw, screen_off_method="ddc")
            _recovered_off_method = True
        # 能力串缓存播种（静态固件属性，避免每次熄灭前重读 3s/台）。
        # v1.9.0 起 key 里带 EDID 短哈希（dev#uid），旧 dev-only 键不再播种：
        # 换面板后不会沿用旧缓存，代价只是首次重新实测一次。
        # 走查 E2-02：caps 是程序自己写的缓存，但手工编辑/旧版残留可能写成
        # list/str/int。原来只防 falsy，类型错时 .items() 抛 AttributeError，
        # 被外层 except 当成"整份配置读坏"→ 用户所有设置回落默认值。
        _caps_raw = raw.get("caps")
        if isinstance(_caps_raw, dict):
            for _dev, _caps in _caps_raw.items():
                if isinstance(_caps, str) and _caps and "#" in str(_dev):
                    _CAPS_CACHE.setdefault(_dev, _caps)
        for key in SETTING_BY_KEY:
            if key in raw:
                cfg[key] = coerce_setting(key, raw[key])
        cfg["models"] = _load_model_cache(raw)
        diag_set_enabled(cfg.get("diag_log", True))
        if _recovered_off_method:
            _ok, _err = save_config(cfg)
            if _ok:
                _write_event_log("熄灭方式已恢复：system → ddc"
                                 "（撤销 v1.9.0 的默认值迁移，恢复 v1.8.5 行为）")
            else:
                _write_event_log("熄灭方式恢复失败（磁盘仍为 system）：%s" % _err)
    except Exception as e:
        CONFIG_LOAD_ERROR = _backup_bad_config("%s: %s" % (type(e).__name__, e))
    return cfg


def _atomic_write_text(path, text):
    """原子写：同目录 mkstemp + os.replace，断电/并发读都不会读到半个文件。"""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_config(cfg):
    """保存配置：原子写。

    v2.01.0：不再做"屏幕状态按磁盘时间戳合并"——程序已经不记屏幕状态账
    （见 screen_verdict 的说明），没有需要跨进程协调的运行态字段，
    自然也没有"内存旧值覆盖磁盘新值"的问题。

    v2.01.20：**不迁移、不镜像**（用户决策）。配置就是程序目录那一份；
    老版本留在 APPDATA 的文件不自动搬，跨机器/跨版本各用各的。

    v2.03.0（重构 P1）：**写入失败必须留痕**。全文 18 处调用里，至少 7 处是
    `save_config(self.cfg)` 裸调用、直接丢弃返回值——磁盘只读、目录被占、
    杀软锁定都表现为"设置改了但下次启动又变回去"，且没有任何线索。
    这里统一写一条事件日志，不管调用方看不看返回值。
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = dict(cfg)
        if _CAPS_CACHE:
            payload["caps"] = dict(_CAPS_CACHE)
        payload["version"] = CONFIG_VERSION
        _atomic_write_text(CONFIG_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
        return True, ""
    except Exception as e:
        # 双层保护：日志本身再失败（比如整个目录只读）也不能把异常抛回调用方，
        # 否则"保存失败"会变成"程序崩溃"，那是更糟的结果。
        try:
            _write_event_log("配置保存失败：%s" % e)
        except Exception:
            pass
        return False, str(e)


def old_config_present():
    """程序目录没有配置、而旧 APPDATA 目录有时，返回旧文件路径（否则 ""）。

    v2.01.20：不自动迁移，但要让用户知道"旧配置还在那儿"，而不是以为丢了。
    只读判断，不碰文件。
    """
    if os.path.abspath(CONFIG_DIR) == os.path.abspath(_LEGACY_CONFIG_DIR):
        return ""
    if os.path.exists(CONFIG_FILE):
        return ""
    old = os.path.join(_LEGACY_CONFIG_DIR, "config.json")
    return old if os.path.exists(old) else ""


def _to_index(item):
    """单个元素转成非负整数序号；非法返回 None。"""
    try:
        i = int(item)
    except Exception:
        return None
    return i if i >= 0 else None


def _split_spec(text):
    """'0, 2' → ['0','2']。"""
    return [p for p in text.replace(" ", "").split(",") if p != ""]


def _as_iterable(value):
    """不是可迭代对象就返回空列表，让上层自然得到空结果。"""
    try:
        iter(value)
    except TypeError:
        return []
    return value


def normalize_indices(value):
    """把各种写法的显示器序号整理成有序去重的 int 列表。空/all → []（表示全部）。"""
    if value is None or value == "" or value == "all":
        return []
    if isinstance(value, (int, float)):
        value = [value]
    elif isinstance(value, str):
        value = _split_spec(value)
    out = []
    for item in _as_iterable(value):
        i = _to_index(item)
        if i is not None and i not in out:
            out.append(i)
    return sorted(out)


def clamp_level(value):
    try:
        return max(0, min(100, int(float(value))))
    except Exception:
        return 0


# ============================================================
# 二、显示器枚举与亮度控制（DDC/CI，dxva2 内置后端）
# ------------------------------------------------------------
# v1.7.0 起不再依赖 monitorcontrol，原因有三（都是实测，不是猜的）：
#   1. monitorcontrol 4.2.0 在 Python 3.14 上枚举直接抛
#      TypeError: expected LP_PHYSICAL_MONITOR instance —— 新版 ctypes 对
#      "数组冒充指针"更严格，源码态根本列不出显示器；
#   2. 它和 enum_displays() 各自枚举一遍，两套顺序一旦不同，就会出现
#      "熄的是这块、点的是那块"——正是"换电脑后有时点不亮"的形态之一；
#   3. 只需要它 capabilities 里的 model 字段，为这个背一个会崩的依赖不值。
# 现在全程序只有 enumerate_targets() 一个枚举源：界面标签、亮度读写、熄屏点亮、
# 状态探测全部按它的下标走，序号天然一致。
# ============================================================
NO_MONITOR_MSG = (
    "未检测到支持 DDC/CI 的显示器。\n请确认：\n"
    "1. 显示器 OSD 菜单已开启 DDC/CI\n"
    "2. 使用 HDMI / DP / DVI 直连（避免 KVM / USB 转接）"
)

# VCP 特性码
VCP_BRIGHTNESS = 0x10
VCP_INPUT_SOURCE = 0x60
VCP_POWER_MODE = 0xD6

# 0xD6 电源模式取值（MCCS 标准）
POWER_ON = 1
POWER_STANDBY = 2
POWER_SUSPEND = 3
POWER_SOFT_OFF = 4
POWER_OFF = 5
POWER_LABEL = {1: "开", 2: "待机", 3: "挂起", 4: "软关", 5: "硬关"}

# 0x60 输入源取值（只列常见项，其余照原样显示数字）
INPUT_LABEL = {1: "模拟1", 2: "模拟2", 3: "DVI-1", 4: "DVI-2", 5: "复合1",
               6: "复合2", 15: "DP-1", 16: "DP-2", 17: "HDMI-1", 18: "HDMI-2",
               19: "HDMI-3"}


class PHYSICAL_MONITOR(ctypes.Structure):
    """dxva2 的 PHYSICAL_MONITOR：一个物理显示器句柄 + 描述文字。"""
    _fields_ = [("hPhysicalMonitor", wintypes.HANDLE),
                ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]


_DXVA2_READY = False


def _dxva2():
    """返回已声明好函数签名的 dxva2（只声明一次）。

    为什么必须显式声明：
      - 64 位下 HANDLE 不声明会被按 C int 截断，句柄高位丢失后表现为
        "偶尔某块屏调不动"，且现象随启动顺序变化，极难复现；
      - 数组参数统一用 c_void_p + byref 传，绕开新版 ctypes 对
        "数组冒充 POINTER" 的严格检查（monitorcontrol 4.2.0 就栽在这）。
    """
    global _DXVA2_READY
    lib = ctypes.windll.dxva2
    if _DXVA2_READY:
        return lib
    try:
        lib.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HMONITOR, ctypes.POINTER(wintypes.DWORD)]
        lib.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        lib.GetPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HMONITOR, wintypes.DWORD, ctypes.c_void_p]
        lib.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        lib.GetVCPFeatureAndVCPFeatureReply.argtypes = [
            wintypes.HANDLE, wintypes.BYTE, ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
        lib.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL
        lib.SetVCPFeature.argtypes = [wintypes.HANDLE, wintypes.BYTE, wintypes.DWORD]
        lib.SetVCPFeature.restype = wintypes.BOOL
        lib.DestroyPhysicalMonitor.argtypes = [wintypes.HANDLE]
        lib.DestroyPhysicalMonitor.restype = wintypes.BOOL
        lib.GetCapabilitiesStringLength.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        lib.GetCapabilitiesStringLength.restype = wintypes.BOOL
        lib.CapabilitiesRequestAndCapabilitiesReply.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        lib.CapabilitiesRequestAndCapabilitiesReply.restype = wintypes.BOOL
    except Exception:
        pass
    _DXVA2_READY = True
    return lib


def _read_vcp(handle, code):
    """读一个 VCP 特性，返回 (当前值, 最大值)；失败返回 None。"""
    try:
        cur, mx = wintypes.DWORD(), wintypes.DWORD()
        if _dxva2().GetVCPFeatureAndVCPFeatureReply(
                handle, wintypes.BYTE(code), None,
                ctypes.byref(cur), ctypes.byref(mx)):
            return cur.value, mx.value
    except Exception:
        pass
    return None


def _write_vcp(handle, code, value):
    """写一个 VCP 特性，返回是否成功。"""
    try:
        return bool(_dxva2().SetVCPFeature(handle, wintypes.BYTE(code),
                                           wintypes.DWORD(int(value))))
    except Exception:
        return False


def enumerate_targets():
    """枚举显示器 —— 全程序唯一的枚举源。

    返回 list[dict]，顺序即 EnumDisplayMonitors 顺序（界面行号 = 下标 = index）：
      {index, dev, w, h, primary, handle, desc}

    ⚠️ 一台物理屏同时插了 DP 和 HDMI 两条线时，Windows 会为**每个输出**各建一个
    HMONITOR，这里就会出现两条指向同一块面板的记录。此时"点亮"必须跳过已经亮着的
    那条，否则会把面板的信号源切到 Windows 没在驱动的那个空口上 → 黑屏。
    """
    if os.name != "nt":
        return []
    hmons = []
    try:
        user32 = ctypes.windll.user32

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32)]

        def _cb(hmon, hdc, lprect, lparam):
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(mi)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                hmons.append((hmon, mi))
            return 1

        proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)
        user32.EnumDisplayMonitors(None, None, proc(_cb), None)
    except Exception:
        return []

    targets = []
    lib = _dxva2()
    for hmon, mi in hmons:
        rc = mi.rcMonitor
        base = {"dev": str(mi.szDevice).split("\\")[-1] or str(mi.szDevice),
                "w": rc.right - rc.left, "h": rc.bottom - rc.top,
                "primary": bool(mi.dwFlags & 1)}
        try:
            count = wintypes.DWORD()
            if not lib.GetNumberOfPhysicalMonitorsFromHMONITOR(
                    hmon, ctypes.byref(count)):
                continue
            arr = (PHYSICAL_MONITOR * max(1, count.value))()
            if not lib.GetPhysicalMonitorsFromHMONITOR(
                    hmon, count.value, ctypes.byref(arr)):
                continue
            for p in list(arr)[:count.value]:
                item = dict(base)
                item["handle"] = p.hPhysicalMonitor
                item["desc"] = str(p.szPhysicalMonitorDescription)
                item["index"] = len(targets)
                targets.append(item)
        except Exception:
            continue
    return targets


def release_targets(targets):
    """释放 enumerate_targets() 拿到的物理句柄。

    旧版从不释放：反复操作会一点点吃掉 DDC 句柄，长跑后表现为"越用越不灵"。
    """
    lib = _dxva2()
    for t in targets or []:
        try:
            lib.DestroyPhysicalMonitor(t["handle"])
        except Exception:
            pass


def _select(targets, indices):
    """按序号挑目标，返回 (选中列表, [(越界序号, 错误), ...])。空 indices = 全部。"""
    if not indices:
        return list(targets), []
    known = {t["index"] for t in targets}
    picked = [t for t in targets if t["index"] in indices]
    return picked, [(i, "序号超出范围") for i in indices if i not in known]


def _capabilities(handle):
    """读 MCCS 能力串（实测约 3 秒/台，必须放后台线程）。失败返回 ""。"""
    try:
        lib = _dxva2()
        n = wintypes.DWORD()
        if not lib.GetCapabilitiesStringLength(handle, ctypes.byref(n)) or not n.value:
            return ""
        buf = ctypes.create_string_buffer(n.value + 1)
        if not lib.CapabilitiesRequestAndCapabilitiesReply(handle, buf, n.value):
            return ""
        return buf.value.decode("ascii", "replace")
    except Exception:
        return ""


_EDID_CACHE = {}


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128)]


def _display_uid(dev):
    """取该输出口对应显示器的稳定标识（8 位短哈希）。

    为什么不能只按 dev 名（DISPLAY1/DISPLAY2）缓存：拔线换面板后输出口名不变，
    旧的能力串/亮度上限会继续套在新面板上（例如上限 255 的屏被当成 100）。

    最稳的是 EDID；但本机（Lenovo Legion）实测 MONITOR 枚举键下没有
    `Device Parameters\\EDID`，故改用 EnumDisplayDevicesW 的 DeviceID
    （形如 `MONITOR\\LEN68C7\\{...}\\0005`）：它按"显示器设备实例"区分，
    两块同型号屏也是不同实例；换面板时 Windows 会生成新实例，标识随之变化。
    取不到时返回 ""，退回 dev-only 键 —— 只是退回旧行为，不会更糟。
    """
    if not dev:
        return ""
    if dev in _EDID_CACHE:
        return _EDID_CACHE[dev]
    uid = ""
    try:
        user32 = ctypes.windll.user32
        dd = _DISPLAY_DEVICE()
        dd.cb = ctypes.sizeof(dd)
        # 枚举显示器的 lpDevice 必须是适配器全名 "\\.\DISPLAY1"；
        # 本项目内部存的是短名 "DISPLAY1"，直接传会一直返回失败。
        adapter = str(dev) if str(dev).startswith("\\\\.\\") else "\\\\.\\" + str(dev)
        if user32.EnumDisplayDevicesW(adapter, 0, ctypes.byref(dd), 0):
            did = str(dd.DeviceID).strip()
            if did:
                uid = hashlib.sha1(did.encode("utf-8")).hexdigest()[:8]
    except Exception:
        uid = ""
    _EDID_CACHE[dev] = uid
    return uid


def _display_key(t):
    """缓存键：`DISPLAY1#a1b2c3d4`。取不到显示器标识时退回 `DISPLAY1`。"""
    dev = str(t.get("dev") or "")
    uid = _display_uid(dev)
    return ("%s#%s" % (dev, uid)) if uid else dev


# 能力串缓存：display key -> 能力串。能力串是面板的静态固件属性（实测 3.1s/台），
# 每次熄灭前都重读一遍曾让 DDC 熄灭耗时 6.3s/2 台 —— 缓存后只剩真正的写入
# (~62ms/台)。随配置持久化（load_config 播种 / save_config 写回），
# 换显示器后 EDID 变化 → key 不命中 → 首次实测刷新。
_CAPS_CACHE = {}


def _capabilities_cached(handle, key):
    """优先读缓存的能力串；key 为空或缓存未命中时实读（读到了就缓存）。"""
    if key:
        got = _CAPS_CACHE.get(key)
        if got:
            return got
    caps = _capabilities(handle)
    if caps and key:
        _CAPS_CACHE[key] = caps
    return caps


def _caps_model(caps):
    """从能力串里取型号（纯解析，不发 DDC 请求）。"""
    m = re.search(r"model\(([^)]*)\)", caps or "", re.I)
    return (m.group(1).strip() if m else "") or None


def _caps_inputs(caps):
    """从能力串里取 0x60 声明支持的输入源列表（纯解析）。

    ⚠️ 能力串里的数值是**不带 0x 的十六进制**。本机实测 `60(11 12 0F)`
    = HDMI-1(17) / HDMI-2(18) / DP-1(15)；按十进制解析会得到 11/12/0 这种
    根本不存在的口，拿它去切信号源就是把画面切进黑洞。
    """
    m = re.search(r"\b60\(([^)]*)\)", caps or "")
    if not m:
        return []
    out = []
    for tok in m.group(1).split():
        try:
            code = int(tok, 16)
        except Exception:
            continue
        if 1 <= code <= 19:          # MCCS 0x60 的有效取值区间
            out.append(code)
    return out


def _caps_power(caps):
    """能力串里 0xD6 声明的电源模式取值（十六进制，如本机的 [1, 4, 5]）。

    ⚠️ 只能当"提示"用（ddcutil 文档原话：capabilities string 经常是错的），
    但**未声明的值绝不盲写**：MS Learn 的 SetVCPFeature 文档明确写着
    "Many monitors don't fully implement that standard; so your use of these
    commands might result in undefined monitor behavior"。
    本机面板声明 D6(01 04 05) 而**不含 02（待机）**——v1.7.0 那次"屏幕黑了
    唤不回来"的事故，就是盲写 02 落进未定义状态造成的。
    """
    m = re.search(r"\bD6\(([^)]*)\)", caps or "", re.I)
    if not m:
        return []
    out = []
    for tok in m.group(1).split():
        try:
            code = int(tok, 16)
        except Exception:
            continue
        if 1 <= code <= 5:              # MCCS 0xD6 只有 01~05
            out.append(code)
    return out


def monitor_model(handle, key=None):
    """从能力串里取型号（如 'Legion R27qe Gen2'）。取不到返回 None。

    传 display key（见 _display_key）走缓存；不传则实读（保持旧语义）。
    """
    return _caps_model(_capabilities_cached(handle, key))


def monitor_label(index, info=None, model=None):
    """显示器列表里那一行的文字。model 需要 DDC 往返，由后台线程补齐。"""
    parts = []
    if model:
        parts.append(model)
    if info:
        name, w, h, primary = info
        parts.append("%d×%d" % (w, h))
        parts.append("主显示器" if primary else "副显示器")
        parts.append(name)
    if not parts:
        parts.append("显示器 %d" % index)
    return "%d: %s" % (index, " · ".join(parts))


# display key（DEV#EDID）-> 0x10 的上限（多数是 100，少数 255）。按面板缓存，
# 避免每次写都多跑一趟 DDC；换面板后 key 变化，不会沿用旧上限。
_MAX_LUM = {}


def _luminance_max(t):
    key = _display_key(t)
    if key not in _MAX_LUM:
        got = _read_vcp(t["handle"], VCP_BRIGHTNESS)
        _MAX_LUM[key] = got[1] if got and got[1] else 100
    return _MAX_LUM[key]


def _raw_from_percent(t, level):
    """把 0-100 的亮度换算成该屏 0x10 的原始值（上限不是 100 的屏必须换算）。"""
    mx = _luminance_max(t)
    return level if mx == 100 else int(round(level * mx / 100.0))


def set_brightness_many(indices, value):
    """批量设亮度。indices 为空 → 全部显示器。返回 (成功序号, [(序号, 错误), ...])。"""
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    level = clamp_level(value)
    ok, errs = [], []
    try:
        picked, bad = _select(targets, indices)
        errs.extend(bad)
        for t in picked:
            if _write_vcp(t["handle"], VCP_BRIGHTNESS, _raw_from_percent(t, level)):
                ok.append(t["index"])
            else:
                errs.append((t["index"], "写入失败（显示器未响应 DDC/CI）"))
    finally:
        release_targets(targets)
    return ok, errs


def get_brightness_many(indices):
    """批量读亮度。返回 ({序号: 亮度 or None}, [(序号, 错误), ...])。"""
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    values, errs = {}, []
    try:
        picked, bad = _select(targets, indices)
        errs.extend(bad)
        for t in picked:
            got = _read_vcp(t["handle"], VCP_BRIGHTNESS)
            if got is None:
                values[t["index"]] = None
                errs.append((t["index"], "读取失败"))
                continue
            cur, mx = got
            values[t["index"]] = int(round(cur * 100.0 / mx)) if mx else cur
    finally:
        release_targets(targets)
    return values, errs


# ---- 亮灭状态探测 ---------------------------------------------------------
def probe_states(indices=None):
    """探测每台显示器"现在亮着还是灭着"，并把判断依据一起带回去。

    判据（全部是 DDC/CI 只读命令，不写任何寄存器）：
      1) 0xD6 电源模式：1=开；2/3/4/5=待机/挂起/软关/硬关；读不到=无回读；
      2) 0x10 亮度是否可读：能读说明面板还有供电、AUX/I2C 通道还在；
    ⚠️ 本机 v1.5.0 事故证明：0xD6 回读在某些机型上与面板事实不符（寄存器值≠面板
       实际状态），所以这里只把它当"证据"之一，界面会把依据一并写明，不假装能
       100% 确定。完全读不到的屏标 unknown，而不是猜成"灭"。

    返回 [{index, dev, power, brightness, state(on/off/unknown), why}]。
    """
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    out = []
    try:
        picked, _bad = _select(targets, indices)
        for t in picked:
            pr = _read_vcp(t["handle"], VCP_POWER_MODE)
            power = pr[0] if pr else None
            br = _read_vcp(t["handle"], VCP_BRIGHTNESS)
            bright = (int(round(br[0] * 100.0 / br[1])) if br[1] else br[0]) if br else None
            if power == POWER_ON:
                state, why = "on", "0xD6=1 开"
            elif power in POWER_LABEL:
                state, why = "off", "0xD6=%d %s" % (power, POWER_LABEL[power])
            elif bright is not None:
                state, why = "on", "亮度可读"
            else:
                state, why = "unknown", "DDC 无回读"
            out.append({"index": t["index"], "dev": t["dev"], "power": power,
                        "brightness": bright, "state": state, "why": why})
    finally:
        release_targets(targets)
    return out


def _idle_seconds():
    """距最后一次键鼠输入的秒数；取不到返回 None。

    系统级熄灭后，屏幕是被"输入"唤醒的，所以这个数字能解释
    "记账说熄灭、屏幕却亮着"的情况（人一动鼠标就亮了，记账还没更新）。
    """
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = ctypes.windll.kernel32.GetTickCount()
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def _human_gap(seconds):
    """秒数 -> '3 秒前 / 12 分钟前 / 2 小时前'。"""
    if seconds < 60:
        return "%d 秒前" % int(seconds)
    if seconds < 3600:
        return "%d 分钟前" % int(seconds // 60)
    return "%d 小时前" % int(seconds // 3600)


# ---- CCD 只读拓扑（拓扑模式的状态层；只查不写）-------------------------
# 依据：Windows CCD API（QueryDisplayConfig / DisplayConfigGetDeviceInfo）。
# 只用于"现在这块屏在不在桌面拓扑里"的判断；真正的断开/接回在阶段 E3 才接入。
class _CcdLuid(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _CcdRational(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.DWORD), ("Denominator", wintypes.DWORD)]


class _CcdSourceInfo(ctypes.Structure):
    _fields_ = [("adapterId", _CcdLuid), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]


class _CcdTargetInfo(ctypes.Structure):
    _fields_ = [("adapterId", _CcdLuid), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("outputTechnology", wintypes.UINT),
                ("rotation", wintypes.UINT), ("scaling", wintypes.UINT),
                ("refreshRate", _CcdRational), ("scanLineOrdering", wintypes.UINT),
                ("targetAvailable", ctypes.c_int), ("statusFlags", wintypes.UINT)]


class _CcdPathInfo(ctypes.Structure):
    _fields_ = [("sourceInfo", _CcdSourceInfo), ("targetInfo", _CcdTargetInfo),
                ("flags", wintypes.UINT)]


class _CcdPoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _CcdSourceMode(ctypes.Structure):
    _fields_ = [("width", wintypes.UINT), ("height", wintypes.UINT),
                ("pixelFormat", wintypes.UINT), ("position", _CcdPoint),
                ("reserved", wintypes.UINT)]


class _CcdModeInfo(ctypes.Structure):
    _fields_ = [("infoType", wintypes.UINT), ("id", wintypes.UINT),
                ("adapterId", _CcdLuid), ("sourceMode", _CcdSourceMode),
                ("tail", ctypes.c_uint64 * 3)]


class _CcdInfoHeader(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", _CcdLuid), ("id", wintypes.UINT)]


class _CcdSourceName(ctypes.Structure):
    _fields_ = [("header", _CcdInfoHeader),
                ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class _CcdTargetName(ctypes.Structure):
    _fields_ = [("header", _CcdInfoHeader), ("flags", wintypes.UINT),
                ("outputTechnology", wintypes.UINT),
                ("edidManufactureId", wintypes.WORD),
                ("edidProductCodeId", wintypes.WORD),
                ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]


class _Ccd2D(ctypes.Structure):
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]


class _CcdVideoSignalInfo(ctypes.Structure):
    _fields_ = [("pixelRate", ctypes.c_uint64),
                ("hSyncFreq", _CcdRational), ("vSyncFreq", _CcdRational),
                ("activeSize", _Ccd2D), ("totalSize", _Ccd2D),
                ("videoStandard", wintypes.UINT),
                ("scanLineOrdering", wintypes.UINT)]


class _CcdTargetPreferredMode(ctypes.Structure):
    """DISPLAYCONFIG_TARGET_PREFERRED_MODE：这块屏的首选分辨率。"""

    _fields_ = [("header", _CcdInfoHeader), ("width", wintypes.UINT),
                ("height", wintypes.UINT), ("targetMode", _CcdVideoSignalInfo)]


_QDC_ALL_PATHS = 0x1
_QDC_VIRTUAL_MODE_AWARE = 0x10
_CCD_PATH_ACTIVE = 0x1
_CCD_GET_SOURCE_NAME = 1
_CCD_GET_TARGET_NAME = 2
_DC_GET_TARGET_PREFERRED_MODE = 3          # DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_PREFERRED_MODE
_CCD_PIXELFORMAT_NONGDI = 4                # Windows 给 source mode 用的就是它，不是 32BPP
_CCD_EDID_IDS_VALID = 0x4          # DISPLAYCONFIG_TARGET_DEVICE_NAME_FLAGS.edidIdsValid
_CCD_TARGET_IN_USE = 0x1          # DISPLAYCONFIG_TARGET_IN_USE


def ccd_probe_topology():
    """只读返回当前桌面拓扑（含 inactive 但 targetAvailable=1 的屏）。

    每项：anchor / active / connected / inUse / friendly / gdi / vdd / pos / size。
    失败一律返回 []（只读探测不能拖垮 CLI 或 GUI）。
    实测开销：QDC_ALL_PATHS 约 0.1ms（233 条 path）。
    """
    if os.name != "nt":
        return []
    try:
        u32 = ctypes.windll.user32
        u32.GetDisplayConfigBufferSizes.argtypes = [wintypes.UINT,
                                                    ctypes.POINTER(wintypes.UINT),
                                                    ctypes.POINTER(wintypes.UINT)]
        u32.GetDisplayConfigBufferSizes.restype = wintypes.LONG
        u32.QueryDisplayConfig.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT),
                                           ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                                           ctypes.c_void_p, ctypes.c_void_p]
        u32.QueryDisplayConfig.restype = wintypes.LONG
        u32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
        u32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG

        flags = _QDC_ALL_PATHS | _QDC_VIRTUAL_MODE_AWARE
        paths = modes = None
        for _ in range(4):
            pc, mc = wintypes.UINT(), wintypes.UINT()
            if u32.GetDisplayConfigBufferSizes(flags, ctypes.byref(pc),
                                               ctypes.byref(mc)) != 0:
                return []
            paths = (_CcdPathInfo * pc.value)()
            modes = (_CcdModeInfo * mc.value)()
            rc = u32.QueryDisplayConfig(flags, ctypes.byref(pc), ctypes.byref(paths),
                                        ctypes.byref(mc), ctypes.byref(modes), None)
            if rc == 0:
                break
            if rc != 122:                 # ERROR_INSUFFICIENT_BUFFER 才重试
                return []
        else:
            return []

        rows = []
        for p in list(paths):
            tgt = p.targetInfo
            if not tgt.targetAvailable:   # 线不在 / 设备不存在：不参与状态判定
                continue
            tname = _CcdTargetName()
            tname.header.type = _CCD_GET_TARGET_NAME
            tname.header.size = ctypes.sizeof(tname)
            tname.header.adapterId = tgt.adapterId
            tname.header.id = tgt.id
            if u32.DisplayConfigGetDeviceInfo(ctypes.byref(tname.header)) != 0:
                continue
            sname = _CcdSourceName()
            sname.header.type = _CCD_GET_SOURCE_NAME
            sname.header.size = ctypes.sizeof(sname)
            sname.header.adapterId = p.sourceInfo.adapterId
            sname.header.id = p.sourceInfo.id
            u32.DisplayConfigGetDeviceInfo(ctypes.byref(sname.header))

            luid = "%08X:%08X" % (p.sourceInfo.adapterId.LowPart & 0xFFFFFFFF,
                                  p.sourceInfo.adapterId.HighPart & 0xFFFFFFFF)
            if tname.flags & _CCD_EDID_IDS_VALID:
                anchor = "%s/%04X/%04X/%d" % (luid, tname.edidManufactureId,
                                              tname.edidProductCodeId,
                                              tname.connectorInstance)
            else:
                anchor = "%s/name=%s" % (luid, tname.monitorFriendlyDeviceName)
            pos = size = ""
            smi = p.sourceInfo.modeInfoIdx >> 16
            if smi != 0xFFFF and smi < len(modes):
                sm = modes[smi].sourceMode
                pos = "(%d,%d)" % (sm.position.x, sm.position.y)
                size = "%dx%d" % (sm.width, sm.height)
            rows.append({
                "anchor": anchor,
                "active": bool(p.flags & _CCD_PATH_ACTIVE),
                "connected": True,
                "inUse": bool(tgt.statusFlags & _CCD_TARGET_IN_USE),
                "friendly": tname.monitorFriendlyDeviceName,
                "gdi": sname.viewGdiDeviceName,
                "vdd": any(k in (tname.monitorFriendlyDeviceName or "").lower()
                           for k in ("vdd", "virtual", "iddcx")),
                "pos": pos, "size": size,
            })

        # QDC_ALL_PATHS 会为同一块屏返回多个 source（实测 233 条 path 只有 3 块屏）。
        # 按锚点折叠，active 的那条胜出，其余 gdi 存进 aliases。
        merged = []
        for r in rows:
            hit = next((x for x in merged if x["anchor"] == r["anchor"]), None)
            if hit is None:
                r["aliases"] = [r["gdi"]] if r["gdi"] else []
                merged.append(r)
                continue
            if r["gdi"] and r["gdi"] not in hit["aliases"]:
                hit["aliases"].append(r["gdi"])
            if r["active"] and not hit["active"]:
                old_aliases = hit["aliases"]
                hit.update(r)
                hit["aliases"] = old_aliases
        return merged
    except Exception:
        return []


def topology_state_for(cfg, rows=None):
    """把拓扑行折叠成一句状态；只认 monitor_anchors，没配置就不猜。

    判据（官方定义）：ACTIVE=1 表示 Windows 正在这条 path 上输出；
    targetAvailable=1 表示设备仍连接（被禁用也仍是 1）。
    官方文档另外说明拔线瞬间可能出现 ACTIVE=1 但 targetAvailable=0（瞬时），
    这种路径已被 ccd_probe_topology() 过滤，不会误判成"显示中"。
    """
    if rows is None:
        rows = ccd_probe_topology()
    anchors = [str(a) for a in (cfg.get("monitor_anchors") or [])]
    if anchors:
        rows = [r for r in rows if r["anchor"] in anchors and not r["vdd"]]
    else:
        rows = [r for r in rows if not r["vdd"]]
    if not rows:
        return {"state": "unknown", "detail": "拓扑里没有匹配的物理屏（未配置锚点？）",
                "rows": []}
    active = [r for r in rows if r["active"]]
    if active:
        state = "on"
        detail = "拓扑 ACTIVE=%d/%d" % (len(active), len(rows))
    else:
        state = "off"
        detail = "拓扑 inactive=%d/%d，设备仍连接" % (len(rows), len(rows))
    return {"state": state, "detail": detail, "rows": rows}


def screen_verdict(cfg, probes=None):
    """给出"屏幕现在是亮还是灭"的结论 + 一行依据——**只依据当场读到的信号**。

    ⚠️ v2.01.0 设计纠偏：程序**不维护任何屏幕状态账**。
    拓扑和系统才是唯一事实源，每次要结论就现场读，读不到就说"不知道"：

      topology  → CCD 的 ACTIVE flag 是系统事实，直接当结论（on/off/unknown）。
      system /  → Windows 没有公开可靠的"显示器电源"读取接口，
      ddc          实测 `0xD6` 在面板已黑时仍报 1（会撒谎）。
                   所以：
                     · 读到 0xD6=2/3/4/5 → 可信地说明它待机了 → off
                       （0xD6 只会假阴性，不会假阳性）
                     · 其余情况 → unknown，把读到的寄存器、上次输入距今时间
                       摊开给用户自己判断
                   给个假结论比说"不知道"更糟：v1.5.0 就是
                   "程序说亮着、屏幕其实黑着"。

    返回 {"state": "on"/"off"/"unknown", "text": "...", "detail": "..."}。
    """
    if cfg.get("screen_off_method") == "topology":
        try:
            topo = topology_state_for(cfg)
        except Exception as e:
            topo = {"state": "unknown", "detail": "拓扑读取失败：%s" % e, "rows": []}
        state = topo["state"]
        if state == "on":
            text = "屏幕：显示中（拓扑）"
            tail = "物理电源键、Windows 自动熄屏不在拓扑追踪范围内"
        elif state == "off":
            text = "屏幕：已断开（拓扑）"
            tail = "接回时按当前拓扑现算，再写亮度"
        else:
            text = "屏幕：状态未知（拓扑）"
            tail = topo["detail"]
        return {"state": state, "text": text,
                "evidence": topo["detail"],
                "detail": "依据：" + topo["detail"] + ("｜" + tail if tail else "")}

    if probes is None:
        try:
            probes = probe_states()
        except Exception:
            probes = []
    idle = _idle_seconds()
    offs = [p for p in (probes or []) if p.get("state") == "off"]

    if offs:
        state = "off"
        text = "屏幕：已熄灭（DDC 报待机）"
        tail = "点「点亮屏幕」恢复；键鼠输入未必唤得回来"
    else:
        state = "unknown"
        text = "屏幕：状态未知"
        tail = "Windows 没有可靠的显示器电源读取接口，亮灭请以眼睛为准"

    bits = []
    if probes:
        # 原始寄存器值留给 --probe / --diag，结论行只给人话
        bits.append("DDC 回读 %s"
                    % "、".join("%d=%s" % (p["index"],
                                          {"on": "开", "off": "灭"}.get(p["state"], "未知"))
                                for p in probes))
    else:
        bits.append("DDC 无回读")
    if idle is not None:
        bits.append("上次输入 %s" % ("刚发生" if idle < 2 else _human_gap(idle)))
    evidence = " · ".join(bits)
    return {"state": state, "text": text, "evidence": evidence,
            "detail": "依据：" + evidence + ("｜" + tail if tail else "")}


# ---- 拓扑动作层（E3）-----------------------------------------------------
# 只服务于 screen_off_method == "topology"；默认 ddc / system 的路径完全不经过这里。
# 事实依据见 docs/拓扑探针.md C2–C5：
#   - 副屏断开 rc=0 / 805ms，接回 986ms（含进程启动）；
#   - 主屏断开 rc=87，Windows 不允许直接移除主屏；
#   - inactive 时 DDC 读不到，所以「点亮」必须先恢复拓扑、再写亮度；
#   - 回读失败时原始字节快照可原样恢复（C5 强制失败实验验证过）。
# 快照落在 %APPDATA%\MonitorBrightness\topology-snapshot.json（仓库外，不入库）。
_SDC_USE_SUPPLIED = 0x00000020
_SDC_APPLY = 0x00000080
_SDC_SAVE_TO_DATABASE = 0x00000200
_SDC_ALLOW_CHANGES = 0x00000400
_SDC_VIRTUAL_MODE_AWARE = 0x00008000
_CCD_APPLY_FLAGS = (_SDC_USE_SUPPLIED | _SDC_APPLY | _SDC_SAVE_TO_DATABASE |
                    _SDC_ALLOW_CHANGES | _SDC_VIRTUAL_MODE_AWARE)
_CCD_MODE_IDX_INVALID = 0xFFFFFFFF
TOPO_VERIFY_TIMEOUT = 2.0          # D3：接回回读有界轮询 ≤2s


def _ccd_api():
    """拿一份声明好函数签名的 user32；非 Windows 返回 None。"""
    if os.name != "nt":
        return None
    u32 = ctypes.windll.user32
    u32.GetDisplayConfigBufferSizes.argtypes = [
        wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
    u32.GetDisplayConfigBufferSizes.restype = wintypes.LONG
    u32.QueryDisplayConfig.argtypes = [
        wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT), ctypes.c_void_p, ctypes.c_void_p]
    u32.QueryDisplayConfig.restype = wintypes.LONG
    u32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
    u32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG
    return u32


def _ccd_query_raw(u32=None):
    """返回 (paths, modes, pc, mc, rows)；失败返回 None。

    rows 每条 path 一项（不折叠）：anchor / active / connected / gdi /
    friendly / vdd / pathIndex。paths + modes 是原始字节快照的来源。
    """
    u32 = u32 if u32 is not None else _ccd_api()
    if u32 is None:
        return None
    flags = _QDC_ALL_PATHS | _QDC_VIRTUAL_MODE_AWARE
    for _ in range(4):
        pc, mc = wintypes.UINT(), wintypes.UINT()
        if u32.GetDisplayConfigBufferSizes(flags, ctypes.byref(pc),
                                           ctypes.byref(mc)) != 0:
            return None
        paths = (_CcdPathInfo * pc.value)()
        modes = (_CcdModeInfo * mc.value)()
        rc = u32.QueryDisplayConfig(flags, ctypes.byref(pc), ctypes.byref(paths),
                                    ctypes.byref(mc), ctypes.byref(modes), None)
        if rc == 0:
            break
        if rc != 122:                  # ERROR_INSUFFICIENT_BUFFER 才重试
            return None
    else:
        return None

    rows = []
    for i, p in enumerate(list(paths)):
        tgt = p.targetInfo
        item = {"pathIndex": i, "anchor": "",
                "active": bool(p.flags & _CCD_PATH_ACTIVE),
                "connected": bool(tgt.targetAvailable),
                # 接回时要现算 modeInfoIdx，必须知道这条 path 用哪个 source/target。
                # srcId 是 adapter 内的独占资源：已被 ACTIVE 占用的 id 不能再置 ACTIVE。
                # 判断"占没占用"必须带 adapter —— source id 只在 adapter 内唯一，
                # 混着比会把另一块显卡上的空闲 source 误判成被占（VDD 就是例子）。
                "srcId": int(p.sourceInfo.id), "tgtId": int(tgt.id),
                "luidLow": int(p.sourceInfo.adapterId.LowPart) & 0xFFFFFFFF,
                "luidHigh": int(p.sourceInfo.adapterId.HighPart) & 0xFFFFFFFF,
                "tgtAdapterLow": int(tgt.adapterId.LowPart) & 0xFFFFFFFF,
                "tgtAdapterHigh": int(tgt.adapterId.HighPart) & 0xFFFFFFFF,
                "srcModeInfoIdx": int(p.sourceInfo.modeInfoIdx),
                "tgtModeInfoIdx": int(tgt.modeInfoIdx),
                "gdi": "", "friendly": "", "vdd": False}
        if not tgt.targetAvailable:
            rows.append(item)
            continue
        tname = _CcdTargetName()
        tname.header.type = _CCD_GET_TARGET_NAME
        tname.header.size = ctypes.sizeof(tname)
        tname.header.adapterId = tgt.adapterId
        tname.header.id = tgt.id
        if u32.DisplayConfigGetDeviceInfo(ctypes.byref(tname.header)) != 0:
            rows.append(item)
            continue
        luid = "%08X:%08X" % (p.sourceInfo.adapterId.LowPart & 0xFFFFFFFF,
                              p.sourceInfo.adapterId.HighPart & 0xFFFFFFFF)
        if tname.flags & _CCD_EDID_IDS_VALID:
            item["anchor"] = "%s/%04X/%04X/%d" % (
                luid, tname.edidManufactureId, tname.edidProductCodeId,
                tname.connectorInstance)
        else:
            item["anchor"] = "%s/name=%s" % (luid, tname.monitorFriendlyDeviceName)
        item["friendly"] = tname.monitorFriendlyDeviceName
        item["vdd"] = any(k in (tname.monitorFriendlyDeviceName or "").lower()
                          for k in ("vdd", "virtual", "iddcx"))
        sname = _CcdSourceName()
        sname.header.type = _CCD_GET_SOURCE_NAME
        sname.header.size = ctypes.sizeof(sname)
        sname.header.adapterId = p.sourceInfo.adapterId
        sname.header.id = p.sourceInfo.id
        if u32.DisplayConfigGetDeviceInfo(ctypes.byref(sname.header)) == 0:
            item["gdi"] = sname.viewGdiDeviceName
        rows.append(item)
    return paths, modes, pc.value, mc.value, rows


def _ccd_slice_bytes(arr, count):
    """只取数组**前 count 个元素**的原始字节。

    ⚠️ 不能用 `bytes(arr)`：`GetDisplayConfigBufferSizes` 给的是"够用的上限"
    （本机 modes 要 699 条），而 `QueryDisplayConfig` 只填真正用到的那些
    （本机 6 条）并把计数改回 6。整个数组一起存，长度就和 count 对不上，
    回滚时 memmove 会越界写坏堆 —— 实测表现为最后在某个 print 里段错误。
    """
    if count <= 0:
        return b""
    elem = ctypes.sizeof(arr) // max(1, len(arr))
    return ctypes.string_at(ctypes.addressof(arr), elem * int(count))


def _ccd_make_snapshot(paths, modes, pc, mc, target_anchors):
    """把 CCD 原始字节打包成**内存内**回滚快照（用完即弃，绝不落盘）。"""
    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "note": "CCD 快照；pathsHex/modesHex 为原始 ctypes 字节，仅用于本次操作失败回滚",
        "pathCount": int(pc), "modeCount": int(mc),
        "pathsHex": _ccd_slice_bytes(paths, pc).hex(),
        "modesHex": _ccd_slice_bytes(modes, mc).hex(),
        "targetAnchors": list(target_anchors),
    }


def _ccd_apply(u32, paths, modes, pc, mc):
    """提交拓扑变更（带 SAVE_TO_DATABASE），原样返回 Windows 错误码。"""
    u32.SetDisplayConfig.argtypes = [
        wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    u32.SetDisplayConfig.restype = wintypes.LONG
    rc = u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes),
                              _CCD_APPLY_FLAGS)
    dlog("ccd.apply", pc=pc, mc=mc, flags="0x%08X" % _CCD_APPLY_FLAGS, rc=rc)
    return rc


def _ccd_restore_from_bytes(u32, snap):
    """回读失败后的唯一一次回滚：原样写回动作前的完整字节快照。

    从 hex 重新构造数组，绝不复用调用方已被改过的 paths/modes。
    **长度对不上就一个字都不写**：宁可少一次回滚，也不要越界写坏堆。
    """
    paths = (_CcdPathInfo * int(snap["pathCount"]))()
    modes = (_CcdModeInfo * int(snap["modeCount"]))()
    pb = bytes.fromhex(snap["pathsHex"])
    mb_ = bytes.fromhex(snap["modesHex"])
    if len(pb) != ctypes.sizeof(paths) or len(mb_) != ctypes.sizeof(modes):
        return -1                    # 快照与数组形状不符：拒绝提交
    if len(pb):
        ctypes.memmove(ctypes.byref(paths), pb, len(pb))
    if len(mb_):
        ctypes.memmove(ctypes.byref(modes), mb_, len(mb_))
    return _ccd_apply(u32, paths, modes, snap["pathCount"], snap["modeCount"])


def _ccd_wait_state(anchors, expect_active, timeout=TOPO_VERIFY_TIMEOUT, probe=None,
                    allow_vdd=False):
    """有界轮询回读；**目标每一个**都达到 expect_active 才算成功。

    v2.01.0 修：原来只要"存在的那些都符合"就返回 True，多屏接回时
    少接回来一块也会报成功。现在要求 wanted 全部出现在回读结果里且状态一致。

    `allow_vdd=True` 给"交接主屏给虚拟显示器"用：平时 VDD 不算目标，
    但那一刻它就是接班屏，必须看到它 ACTIVE。
    """
    probe = probe or ccd_probe_topology
    deadline = time.monotonic() + max(0.0, float(timeout))
    wanted = set(anchors or [])
    while True:
        try:
            rows = [r for r in probe()
                    if r.get("anchor") in wanted
                    and (allow_vdd or not r.get("vdd"))]
        except Exception:
            rows = []
        got = {r.get("anchor") for r in rows}
        if wanted and wanted <= got and all(
                bool(r.get("active")) == bool(expect_active) for r in rows):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def topology_plan_off(indices, rows, primary_gdis=None, dev_to_index=None, heir=None):
    """纯函数：算出把这些屏从拓扑里摘掉要动哪些 path，**必要时交接主屏**。

    Windows 没有"设置主显示器"的 API —— 主屏就是桌面原点 (0,0) 所在的那块屏，
    而且不允许把它从拓扑里摘掉（实测 rc=87）。所以想断开主屏，只能先把 (0,0)
    让给别人：接班屏平移到 (0,0)、其余屏跟着平移（相对布局不变），再摘原主屏。
    这条路径在真机上验过（scripts/topology_handover_probe.py，SDC_VALIDATE 只读）：

      | 做法 | rc |
      |---|---|
      | 只改 source mode 的 position | 0 |
      | 接班屏移到 (0,0) + 摘掉原主屏 | 0 |
      | VDD 当主屏 + 物理屏全摘（串流形态） | 0 |

    护栏：VDD 永不作**断开目标**；断开后桌面必须还剩至少一块屏（否则黑屏）。

    返回 (plan, errs, noop)，plan =
      {"off": [锚点...],                 # 要摘掉的（可能包含当前主屏）
       "heir": 锚点 or None,             # 接班当主屏的那块；None = 主屏不动
       "heir_active": bool}              # False 表示它当前断着，需要先接回
    平移量不在这里算 —— 要读 source mode，由 topology_off 按 hire 的位置算。
    """
    primary = {_short_dev(g) for g in (primary_gdis or [])}
    idx = set(normalize_indices(indices))
    wanted_names = None
    if dev_to_index is not None:
        wanted_names = {_short_dev(name) for name, i in dev_to_index.items()
                        if i in idx} if idx else None
    selected = []
    for r in rows:
        if r.get("vdd") or not r.get("anchor"):
            continue
        if wanted_names is not None:
            # 只认 ACTIVE 的 source：aliases 里两块屏会互相混进 DISPLAYn，
            # 拿它做选择匹配会把「只勾主屏」误判成「勾了副屏」。
            if _short_dev(r.get("gdi")) not in wanted_names:
                continue
        elif idx and r.get("index") not in idx:
            continue
        selected.append(r)

    off = []
    for r in selected:
        if r.get("active") and r["anchor"] not in off:
            off.append(r["anchor"])
    if not off:
        if selected:
            return {"off": [], "heir": None, "heir_active": False}, [], True
        return ({"off": [], "heir": None, "heir_active": False},
                ["没有可断开的屏（VDD 不作目标，未连接的屏也断不了）"], False)

    # 当前主屏（摆在 0,0 的那块）在不在这次要摘的名单里？
    cur_primary = [r["anchor"] for r in rows
                   if r.get("active") and r.get("connected")
                   and _short_dev(r.get("gdi")) in primary]
    must_handover = any(a in off for a in cur_primary)
    if not must_handover:
        return {"off": off, "heir": None, "heir_active": False}, [], False

    # 候选接班屏：连着、且不在这次要摘的名单里
    cands = [r for r in rows
             if r.get("connected") and r.get("anchor")
             and r["anchor"] not in off]
    if heir:
        if not any(r["anchor"] == heir for r in cands):
            return ({"off": [], "heir": None, "heir_active": False},
                    ["指定接班的屏不在了（可能刚被拔掉），请重新选择"], False)
        pick = next(r for r in cands if r["anchor"] == heir)
    else:
        if not cands:
            return ({"off": [], "heir": None, "heir_active": False},
                    ["断开后桌面上就一块屏都不剩（会黑屏），Windows 不允许。\n"
                     "先留一块屏当主显示器，或把虚拟显示器接上再试。"], False)
        # 自动选：优先物理屏（用户看得见），再考虑虚拟屏
        phys = [r for r in cands if not r.get("vdd")]
        pick = (phys or cands)[0]

    heir_active = bool(pick.get("active"))
    # 平移量要读 source mode，纯函数里拿不到 —— 由 topology_off 按 hire 的位置算
    return ({"off": off, "heir": pick["anchor"], "heir_active": heir_active},
            [], False)


def _fold_raw_rows(raw_rows):
    """把逐 path 的原始行按锚点折叠（同一块屏的多个 source 算一块）。"""
    rows = []
    for r in raw_rows:
        hit = next((x for x in rows if x["anchor"] == r["anchor"]), None)
        if hit is None:
            rows.append(dict(r, aliases=[r["gdi"]] if r["gdi"] else []))
        else:
            if r["gdi"] and r["gdi"] not in hit["aliases"]:
                hit["aliases"].append(r["gdi"])
            if r["active"] and not hit["active"]:
                hit["active"] = True
    return rows


# ---- 声明式接回（v2.01.0）------------------------------------------------
# ⚠️ 设计纠偏：接回**不再恢复"断开前的历史快照"**。
# 快照是"过去某一刻的整份拓扑字节"，一旦期间用户在 Windows 设置里改过排布、
# 分辨率、主屏，恢复旧快照会把这些改动全部覆盖掉——这正是"程序自己维护状态"
# 的典型错误。程序的职责只是「读当前拓扑 + 把要用的屏加回当前拓扑」。
#
# 现算依据（2026-11-27 真机实测，scripts/topo-declarative-probe.py --verify）：
#   DISPLAYCONFIG_PATH_SOURCE_INFO.modeInfoIdx = (sourceModeIdx << 16) | 0xFFFF
#   DISPLAYCONFIG_PATH_TARGET_INFO.modeInfoIdx = (targetModeIdx << 16) | desktopModeIdx
#   按 (infoType, id) 在**当前** modes 数组里查，得到的三元组与 Windows 自己填的
#   值逐位一致（path#0 0x0001FFFF/0x00000002、path#1 0x0004FFFF/0x00030005）。
_CCD_TYPE_SOURCE = 1        # DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE
_CCD_TYPE_TARGET = 2        # DISPLAYCONFIG_MODE_INFO_TYPE_TARGET
_CCD_TYPE_DESKTOP = 3       # DISPLAYCONFIG_MODE_INFO_TYPE_DESKTOP_IMAGE


def _ccd_mode_entries(modes, mc):
    """把 modes 数组摘成 [(下标, infoType, id, adapterLow)]，供纯函数现算 modeInfoIdx。"""
    out = []
    for i in range(max(0, min(int(mc or 0), len(modes)))):
        m = modes[i]
        out.append((i, int(m.infoType), int(m.id),
                    int(m.adapterId.LowPart) & 0xFFFFFFFF))
    return out


def _ccd_pick_mode(entries, info_type, ident, luid_low=None):
    """按 (infoType, adapter, id) 找 mode 下标；找不到返回 None。

    **必须带 adapter**：source/target id 只在 adapter 内唯一，不同显卡上撞 id
    很常见（本机 VDD 的 src=0 与物理屏的 src=0 就撞了）。不带 adapter 会挑中
    另一块显卡的 mode 项，提交必然 rc=87 —— 这正是 v2.01.2 前"借用别的屏的
    source mode → 87"的真身。

    entries 里没有 adapter 字段时（老的三元组）不做 adapter 过滤，向后兼容。
    """
    for e in entries:
        if len(e) < 3:
            continue
        if int(e[1]) != info_type or int(e[2]) != int(ident):
            continue
        if luid_low is not None and len(e) > 3 and int(e[3]) != int(luid_low):
            continue
        return int(e[0])
    return None


def topology_plan_on(anchors, rows, mode_entries, allow_vdd=False):
    """纯函数：算出「把 anchors 里的屏加回**当前**拓扑」要动哪几条 path。

    全部输入都是本次现读的：rows=_ccd_query_raw 的逐 path 行，
    mode_entries=当前 modes 数组摘要。不读快照、不记历史。

    护栏：
      - 只认 connected 的 path（拔了线的接不回来）；
      - VDD 永不作目标；
      - 已 ACTIVE 的锚点直接跳过（幂等：接两次和接一次结果一样）；
      - source id 是 **adapter 内**独占资源，只挑没被同 adapter 的 ACTIVE path 占用的；
      - source mode 缺失**不算错误**：真实断开后 Windows 一定会把它从 modes 数组
        里摘掉，这时标记 `makeSourceMode`，由调用方现造一条补进去
        （`_ccd_append_source_mode`；置 INVALID 必 rc=87）。

    返回 (plan, errs, noop)。plan 每项含 pathIndex/anchor/srcId/tgtId/三个 modeIdx
    以及 `makeSourceMode`（True 表示 sourceModeIdx 为 None，需要现造）。
    target/desktop 可能为 None，表示交给 Windows 定。

    `allow_vdd=True` 只给验证脚本用：VDD 是"连着但整块不在拓扑里"的现成样本，
    拿它跑一次真接回就能验完整条链路，而不会碰到任何物理屏。产品路径永不传。
    """
    wanted = [str(a) for a in (anchors or []) if a]
    rows = list(rows or [])
    used = {(int(r.get("luidLow", 0)), int(r["srcId"])) for r in rows
            if r.get("active") and r.get("connected") and r.get("srcId") is not None}
    plan, errs = [], []
    for anchor in wanted:
        mine = [r for r in rows if r.get("anchor") == anchor and r.get("connected")]
        if not mine:
            errs.append("锚点 %s 在拓扑里没有已连接的 path（可能已拔线）" % anchor)
            continue
        if any(r.get("vdd") for r in mine) and not allow_vdd:
            errs.append("虚拟屏 %s 不作接回目标" % anchor)
            continue
        if any(r.get("active") for r in mine):
            continue                       # 已经亮着：no-op
        key = lambda r: (int(r.get("luidLow", 0)), int(r["srcId"]))
        free = [r for r in mine if key(r) not in used]
        if not free:
            errs.append("锚点 %s 没有空闲 source（全被其它屏占用）" % anchor)
            continue
        # 确定性：source id 最小 → pathIndex 最小。不依赖"上次用的是哪条"。
        pick = min(free, key=lambda r: (int(r["srcId"]), int(r["pathIndex"])))
        smode = _ccd_pick_mode(mode_entries, _CCD_TYPE_SOURCE, pick["srcId"],
                               pick.get("luidLow"))
        plan.append({
            "pathIndex": int(pick["pathIndex"]), "anchor": anchor,
            "srcId": int(pick["srcId"]), "tgtId": int(pick["tgtId"]),
            "luidLow": int(pick.get("luidLow", 0)),
            "luidHigh": int(pick.get("luidHigh", 0)),
            "tgtAdapterLow": int(pick.get("tgtAdapterLow", 0)),
            "tgtAdapterHigh": int(pick.get("tgtAdapterHigh", 0)),
            "sourceModeIdx": smode,
            # 真实断开后 source mode 必然不在 modes 数组里 → 由调用方现造一条
            "makeSourceMode": smode is None,
            "targetModeIdx": _ccd_pick_mode(mode_entries, _CCD_TYPE_TARGET,
                                            pick["tgtId"], pick.get("tgtAdapterLow")),
            "desktopModeIdx": _ccd_pick_mode(mode_entries, _CCD_TYPE_DESKTOP,
                                             pick["tgtId"], pick.get("tgtAdapterLow")),
        })
        used.add((int(pick.get("luidLow", 0)), int(pick["srcId"])))
    return plan, errs, (not plan and not errs)


def _ccd_preferred_size(u32, adapter_low, adapter_high, target_id):
    """问 Windows：这块屏的首选分辨率。查不到返回 None。"""
    info = _CcdTargetPreferredMode()
    info.header.type = _DC_GET_TARGET_PREFERRED_MODE
    info.header.size = ctypes.sizeof(info)
    info.header.adapterId.LowPart = int(adapter_low) & 0xFFFFFFFF
    info.header.adapterId.HighPart = int(adapter_high)
    info.header.id = int(target_id)
    if u32.DisplayConfigGetDeviceInfo(ctypes.byref(info.header)) != 0:
        return None
    w, h = int(info.width), int(info.height)
    return (w, h) if w and h else None


def _ccd_live_size(rows, paths, modes):
    """兜底尺寸：抄一块**正在用**的屏的 source mode。

    同机同型号很常见；比硬编码 1920x1080 更可能被 Windows 接受。
    一块都没有才退回 1920x1080。
    """
    for r in rows or []:
        if not (r.get("active") and r.get("connected")):
            continue
        idx = int(paths[int(r["pathIndex"])].sourceInfo.modeInfoIdx)
        if idx == _CCD_MODE_IDX_INVALID:
            continue
        src = (idx >> 16) & 0xFFFF
        if src >= len(modes):
            continue
        sm = modes[src].sourceMode
        if sm.width and sm.height:
            return int(sm.width), int(sm.height)
    return 1920, 1080


def _ccd_layout_rects(rows, paths, modes):
    """当前桌面上每块活动屏的 (x, y, w, h)。"""
    return list(_ccd_layout_by_anchor(rows, paths, modes).values())


def _ccd_layout_by_anchor(rows, paths, modes):
    """{锚点: (x, y, w, h)} —— 当前桌面上每块活动屏的位置。"""
    out = {}
    for r in rows or []:
        if not (r.get("active") and r.get("connected")):
            continue
        idx = int(paths[int(r["pathIndex"])].sourceInfo.modeInfoIdx)
        if idx == _CCD_MODE_IDX_INVALID:
            continue
        src = (idx >> 16) & 0xFFFF
        if src >= len(modes):
            continue
        sm = modes[src].sourceMode
        if sm.width and sm.height:
            out[r["anchor"]] = (int(sm.position.x), int(sm.position.y),
                                int(sm.width), int(sm.height))
    return out


def _ccd_free_origin(rows, paths, modes):
    """当前桌面最右边的 x —— 接回时"没记住原位置"的兜底落点。"""
    rects = _ccd_layout_rects(rows, paths, modes)
    return max([r[0] + r[2] for r in rects] or [0])


def _ccd_place(want_xy, w, h, rects):
    """给一块要接回的屏决定桌面位置：优先摆回**原位置**，重叠才退回最右边。

    **重叠必须避免**：实测把新屏放在 (0,0) 与主屏重叠，`SetDisplayConfig`
    直接 rc=87，接不回来。所以"摆回原位"和"不重叠"冲突时，宁可换位置。

    原位置来自 `cfg["monitor_positions"]`（断开前抓的一次快照），没有就用
    桌面最右边 —— 与 Windows 自己点「扩展这些显示器」的行为一致。
    """
    if want_xy is not None:
        x, y = int(want_xy[0]), int(want_xy[1])
        clash = any(x < rx + rw and rx < x + w and y < ry + rh and ry < y + h
                    for rx, ry, rw, rh in rects)
        if not clash:
            return x, y
    return max([rx + rw for rx, _ry, rw, _rh in rects] or [0]), 0


# ============================================================================
# 窗口跟随（v2.01.9）
# ----------------------------------------------------------------------------
# 拓扑一变，Windows 会把各应用的窗口挤到"还能显示"的地方，而且**不会**在
# 拓扑恢复后搬回来。用户看到的就是"一改显示器设置，窗口全乱了"。
#
# 这里只在**一次操作内**做：动作前记下每个窗口属于哪块屏，动作后按这块屏的
# 平移量把窗口跟着搬。不落盘、不跨操作 —— 跨操作记录会跟用户后来的手动摆放
# 打架（他挪过的窗口会被我们又搬回去）。
# ============================================================================
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


class _WinRect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _user32():
    return ctypes.windll.user32 if os.name == "nt" else None


def _window_rect(u32, hwnd):
    r = _WinRect()
    if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top),
            int(r.right - r.left), int(r.bottom - r.top))


def iter_app_windows(u32=None):
    """枚举"用户看得见的应用窗口"。

    排除：不可见、最小化、无标题、工具窗/不激活窗（输入法、悬浮条）、
    本进程自己的窗口。收起的窗口不碰 —— 动它等于把它还原，那是另一回事。
    """
    u32 = u32 or _user32()
    if u32 is None:
        return []
    out = []
    me = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):
        try:
            if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
                return True
            if u32.GetWindowTextLengthW(hwnd) == 0:
                return True
            ex = u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if ex & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE):
                return True
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == me:
                return True
            out.append(int(hwnd))
        except Exception:
            pass
        return True

    u32.EnumWindows(_cb, 0)
    return out


def win_snapshot(layout, u32=None):
    """记下每个应用窗口现在落在哪块屏上。layout = {锚点: (x, y, w, h)}。"""
    u32 = u32 or _user32()
    if u32 is None or not layout:
        return []
    snap = []
    for hwnd in iter_app_windows(u32):
        rect = _window_rect(u32, hwnd)
        if not rect:
            continue
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        for anchor, (x, y, w, h) in layout.items():
            if x <= cx < x + w and y <= cy < y + h:
                snap.append({"hwnd": hwnd, "rect": rect, "anchor": anchor})
                break
    return snap


def win_follow(snap, shift, dropped, u32=None):
    """按屏的平移量把窗口搬到新位置；所在屏没了的不动（交给 Windows）。

    只改位置不改大小，并且**只在窗口确实被挪走时才动**：如果它已经在目标
    位置，说明 Windows 没碰它，我们也不碰。
    """
    u32 = u32 or _user32()
    if u32 is None or not snap or tuple(shift) == (0, 0):
        return 0
    dropped = set(dropped or ())
    moved = 0
    for it in snap:
        if it["anchor"] in dropped:
            continue
        hwnd = it["hwnd"]
        try:
            if not u32.IsWindow(hwnd):
                continue
            want = (it["rect"][0] + shift[0], it["rect"][1] + shift[1])
            cur = _window_rect(u32, hwnd)
            if cur is None or (cur[0], cur[1]) == want:
                continue
            u32.SetWindowPos(hwnd, 0, want[0], want[1], 0, 0,
                             _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
            moved += 1
        except Exception:
            continue
    return moved


def _ccd_append_source_mode(modes, mc, adapter_low, adapter_high, source_id,
                            width, height, x, y):
    """在 modes 数组末尾**追加**一条 SOURCE mode 项，返回 (新数组, 新下标)。

    为什么必须自己造：一块屏被断开之后，Windows 就把它从 modes 数组里摘掉了，
    接回时没有任何下标可引用（引用 INVALID 必 rc=87）。实测只有这条路能过：

      | 做法                                   | rc |
      |----------------------------------------|----|
      | source = INVALID                        | 87 |
      | 借用别的屏的 source mode 项             | 87 |
      | 自己补一条（pf=NONGDI，位置不重叠）     | 0  |
      | 自己补一条但 pf=32BPP                   | 87 |
      | 自己补一条但位置 (0,0) 与主屏重叠       | 87 |

    pixelFormat 必须是 `DISPLAYCONFIG_PIXELFORMAT_NONGDI(4)` —— Windows 自己给
    source mode 填的就是 4，填 32BPP(3) 会被拒。证据：scripts/topology_mode_synthesis.py
    """
    out = (_CcdModeInfo * (mc + 1))()
    ctypes.memmove(ctypes.byref(out), ctypes.byref(modes),
                   ctypes.sizeof(_CcdModeInfo) * mc)
    i = mc
    out[i].infoType = _CCD_TYPE_SOURCE
    out[i].id = int(source_id)
    out[i].adapterId.LowPart = int(adapter_low) & 0xFFFFFFFF
    out[i].adapterId.HighPart = int(adapter_high)
    out[i].sourceMode.width = int(width)
    out[i].sourceMode.height = int(height)
    out[i].sourceMode.pixelFormat = _CCD_PIXELFORMAT_NONGDI
    out[i].sourceMode.position.x = int(x)
    out[i].sourceMode.position.y = int(y)
    return out, i


def _ccd_capture_positions(anchors, paths, modes, mc, rows):
    """记下这些屏**此刻**的桌面位置与尺寸，供下次接回摆回原位。

    只在"断开之前"调用 —— 断开之后 Windows 会把该屏的 mode 项摘掉，
    位置就再也读不到了。

    为什么这不是"状态账"：它存的是摆放偏好，且每次断开前都按当下的真实值
    重写。用户在 Windows 里把屏挪到左边，这里下一次就变成左边，不会像
    "上次操作结果"那样越用越离谱。
    """
    want = set(anchors or [])
    out = {}
    for r in rows or []:
        if r.get("anchor") not in want or not r.get("active") or r.get("vdd"):
            continue
        idx = int(paths[int(r["pathIndex"])].sourceInfo.modeInfoIdx)
        if idx == _CCD_MODE_IDX_INVALID:
            continue
        src = (idx >> 16) & 0xFFFF
        if src >= int(mc or 0) or src >= len(modes):
            continue
        sm = modes[src].sourceMode
        if not (sm.width and sm.height):
            continue
        out[r["anchor"]] = [int(sm.position.x), int(sm.position.y),
                            int(sm.width), int(sm.height)]
    return out


def _ccd_fill_missing_source_modes(u32, paths, modes, mc, rows, plan, want=None):
    """给 plan 里 source mode 已缺失的项**现造**一条补进 modes 数组。

    真实断开后这是常态而非异常（Windows 会把断开屏的 SOURCE mode 从 modes
    数组里摘掉），所以不能报错，只能自己补。返回 (新 modes 数组, 新计数)。

    尺寸：`want` 里记着就照记着的（保真），否则问首选分辨率，再不行抄一块
    正在用的屏；
    位置：`want` 里记着就摆回原位（不与现有屏重叠的前提下），否则桌面最右边。
    """
    need = [it for it in plan if it.get("makeSourceMode")]
    if not need:
        return modes, mc
    want = want or {}
    rects = _ccd_layout_rects(rows, paths, modes)
    dlog("topo.on.synth.enter", need=[it["anchor"] for it in need],
         want=want, layout=rects)
    for it in need:
        saved = want.get(it["anchor"])
        if saved and len(saved) == 4:
            w, h = int(saved[2]), int(saved[3])
        else:
            w, h = (_ccd_preferred_size(u32, it["tgtAdapterLow"],
                                        it["tgtAdapterHigh"], it["tgtId"])
                    or _ccd_live_size(rows, paths, modes))
        x, y = _ccd_place(saved[:2] if saved else None, w, h, rects)
        rects.append((x, y, w, h))
        modes, idx = _ccd_append_source_mode(
            modes, mc, it["luidLow"], it["luidHigh"], it["srcId"], w, h, x, y)
        mc += 1
        it["sourceModeIdx"] = idx
        it["makeSourceMode"] = False
        # 这条只进 diag.log：它是"为了接回而临时造了一条 source mode"，
        # 属于实现细节，写进 events.log 只会让界面挂着看不懂的术语。
        dlog("topo.on.synth.item", anchor=it["anchor"], size=[w, h], pos=[x, y],
             modeIdx=idx, saved=bool(saved))
    return modes, mc


def _ccd_apply_plan_on(paths, plan):
    """把接回计划就地写进 paths 数组。返回真正改动的锚点列表。

    ⚠️ **sourceInfo.modeInfoIdx 绝不能置 INVALID**（v2.01.1 真机定论）：
    同一份数组只把这一个字段换成 0xFFFFFFFF，`SetDisplayConfig` 立刻
    rc=87 ERROR_INVALID_PARAMETER；换成 targetInfo.modeInfoIdx=INVALID
    反而 rc=0。所以"两个都交 Windows 挑"是错的，source 必须有有效值。
    证据：scripts/topology_repro87.py 步骤 6（只用 SDC_VALIDATE，不改拓扑）。
    """
    done = []
    for item in plan:
        s = item.get("sourceModeIdx")
        if s is None:
            # plan 阶段已经拦过；真走到这里只能跳过，硬写 INVALID 必然 rc=87。
            # 注意必须在置 ACTIVE 之前判：半改的数组一旦提交就是 rc=87。
            continue
        t = item.get("targetModeIdx")
        d = item.get("desktopModeIdx")
        p = paths[item["pathIndex"]]
        p.flags |= _CCD_PATH_ACTIVE
        p.sourceInfo.modeInfoIdx = ((int(s) << 16) | 0xFFFF) & 0xFFFFFFFF
        if None in (t, d):
            # 这块屏的 target/desktop mode 已不在 modes 数组里 → 交 Windows 定
            p.targetInfo.modeInfoIdx = _CCD_MODE_IDX_INVALID
        else:
            p.targetInfo.modeInfoIdx = ((int(t) << 16) | int(d)) & 0xFFFFFFFF
        p.sourceInfo.statusFlags = 0
        p.targetInfo.statusFlags = 0
        done.append(item["anchor"])
        dlog("ccd.plan.write", anchor=item.get("anchor"), path=item.get("pathIndex"),
             srcId=item.get("srcId"), tgtId=item.get("tgtId"),
             srcModeIdx="0x%08X" % int(p.sourceInfo.modeInfoIdx),
             tgtModeIdx="0x%08X" % int(p.targetInfo.modeInfoIdx))
    return done


def topology_off(indices, cfg=None, heir=None):
    """拓扑断开：先快照，再提交，再回读；失败自动回滚一次（D2/D4/D6）。

    如果这次要摘的屏里包含**当前主屏**，会先把主屏角色交接给 `heir`
    （不指定就自动选：优先物理屏，其次虚拟屏）：接班屏平移到 (0,0)、
    其余屏跟着平移（相对布局不变），再摘原主屏。交接的技术依据见
    `topology_plan_off` 的注释与 scripts/topology_handover_probe.py。

    这样"禁用所有物理屏、只留虚拟显示器"（串流场景）也能走纯拓扑操作，
    全程不碰面板电源，没有 DDC 待机唤不回的风险。

    `cfg` 给了就把这些屏**此刻**的桌面位置写进 `monitor_positions`，供下次
    接回摆回原位（记录的是平移后的坐标，接回时正好落在新主屏旁边）。

    返回 (成功下标列表, [(下标, 错误), ...])，与 screen_off_many 同形状。
    """
    u32 = _ccd_api()
    if u32 is None:
        return [], [(-1, "拓扑模式仅支持 Windows")]
    raw = _ccd_query_raw(u32)
    if raw is None:
        return [], [(-1, "CCD 查询失败，未做任何改动")]
    paths, modes, pc, mc, raw_rows = raw
    dlog("topo.off", indices=normalize_indices(indices), pc=pc, mc=mc, heir=heir,
         active=[r["anchor"] for r in raw_rows if r.get("active")],
         connected=[r["anchor"] for r in raw_rows if r.get("connected")])
    try:
        targets = enumerate_targets()
    except Exception:
        targets = []
    primary = [t.get("dev") for t in targets if t.get("primary")]
    dev_to_index = {t.get("dev"): t.get("index") for t in targets}
    release_targets(targets)
    plan, errs, noop = topology_plan_off(
        indices, _fold_raw_rows(raw_rows), primary, dev_to_index, heir)
    dlog("topo.off.plan", plan=plan, errs=errs, noop=noop, primary=primary)
    if errs:
        return [], [(-1, e) for e in errs]
    if noop:
        return list(normalize_indices(indices)), []
    anchors = plan["off"]
    heir_anchor = plan.get("heir")
    heir_active = bool(plan.get("heir_active"))

    # D6：动作前拍一份**本进程内**的原始字节快照，只用于"这次操作失败就原样撤回"。
    # v2.01.0 起不再落盘：快照一旦落盘就会被当成"状态"，接回时恢复旧快照
    # 会把用户其后在 Windows 设置里做的改动一起覆盖掉（设计纠偏，见 topology_plan_on）。
    snap = _ccd_make_snapshot(paths, modes, pc, mc, anchors)

    # 接班屏已在拓扑里 → 算出"把它挪到 (0,0)"需要的平移量。
    # 主屏就是 (0,0) 那块，所以平移接班屏到 (0,0) 就等于把主屏角色交给它；
    # 其余屏跟着平移，相对布局完全不变（真机验证见 topology_plan_off 注释）。
    shift = (0, 0)
    if heir_anchor and heir_active:
        row = next((r for r in raw_rows
                    if r["anchor"] == heir_anchor and r.get("active")), None)
        if row is not None:
            idx = int(paths[row["pathIndex"]].sourceInfo.modeInfoIdx)
            if idx != _CCD_MODE_IDX_INVALID:
                s = (idx >> 16) & 0xFFFF
                if s < mc:
                    shift = (-int(modes[s].sourceMode.position.x),
                             -int(modes[s].sourceMode.position.y))
        dlog("topo.off.handover", heir=heir_anchor, heir_active=True, shift=list(shift))

    # 断开之后 Windows 会把这块屏的 mode 项摘掉，位置就再也读不到了，
    # 所以趁现在把"它此刻摆在哪"记下来，接回时才能摆回原位。
    # 记的是**平移后**的坐标：接回时正好落在新主屏旁边，而不是跑到旧原点去。
    if cfg is not None:
        pos = _ccd_capture_positions(anchors, paths, modes, mc, raw_rows)
        if pos:
            pos = {a: [v[0] + shift[0], v[1] + shift[1], v[2], v[3]]
                   for a, v in pos.items()}
            merged = dict(cfg.get("monitor_positions") or {})
            # **已有记录不覆盖**（v2.01.11）。交接主屏会把接班屏挪到桌面原点，
            # 于是"断开那一刻的位置"已经不是用户摆的位置了；照它记下来，
            # 下次接回就会把副屏丢到右边去（实测：两块屏的记录都被写成 (0,0)）。
            # 用户真想更新布局，用设置里的「记住当前显示器布局」。
            for a, v in pos.items():
                merged.setdefault(a, v)
            cfg["monitor_positions"] = _coerce_positions(merged)
            save_config(cfg)
            dlog("topo.off.positions", captured=pos,
                 kept=list(cfg["monitor_positions"]))

    cur_modes, cur_mc = modes, mc

    # 动作前记下"每个应用窗口在哪块屏上"：交接会让所有屏平移，Windows 这时
    # 会把窗口挤到一起且不会搬回来。只在 shift≠0（真会平移）时才记。
    win_snap = []
    if shift != (0, 0) and bool((cfg or {}).get("window_follow", True)):
        win_snap = win_snapshot(_ccd_layout_by_anchor(raw_rows, paths, modes))
        dlog("topo.off.windows.snap", count=len(win_snap))

    # 接班屏当前是断着的（虚拟显示器）→ 先把它接回，并直接放到 (0,0)
    if heir_anchor and not heir_active:
        p_on, e_on, n_on = topology_plan_on(
            [heir_anchor], raw_rows, _ccd_mode_entries(modes, mc), allow_vdd=True)
        if e_on:
            dlog("topo.off.handover", heir=heir_anchor, errs=e_on)
            return [], [(-1, e) for e in e_on]
        if n_on:                       # 它其实已经亮着，按"在拓扑里"处理
            heir_active = True
        else:
            cur_modes, cur_mc = _ccd_fill_missing_source_modes(
                u32, paths, cur_modes, cur_mc, raw_rows, p_on, want={})
            midx = p_on[0]["sourceModeIdx"]
            cur_modes[midx].sourceMode.position.x = 0
            cur_modes[midx].sourceMode.position.y = 0
            _ccd_apply_plan_on(paths, p_on)
            dlog("topo.off.handover", heir=heir_anchor, heir_active=False,
                 synthMode=midx, size=[int(cur_modes[midx].sourceMode.width),
                                       int(cur_modes[midx].sourceMode.height)])

    # 其余屏跟着平移（按 mode 下标去重：同一块屏的多条 path 共用一条 mode 项）
    if shift != (0, 0):
        moved = set()
        for r in raw_rows:
            if not (r.get("active") and r.get("connected")):
                continue
            if r["anchor"] in anchors:
                continue
            idx = int(paths[r["pathIndex"]].sourceInfo.modeInfoIdx)
            if idx == _CCD_MODE_IDX_INVALID:
                continue
            s = (idx >> 16) & 0xFFFF
            if s in moved or s >= cur_mc:
                continue
            moved.add(s)
            cur_modes[s].sourceMode.position.x = \
                int(cur_modes[s].sourceMode.position.x) + shift[0]
            cur_modes[s].sourceMode.position.y = \
                int(cur_modes[s].sourceMode.position.y) + shift[1]

    for i in range(len(paths)):
        info = next((x for x in raw_rows if x["pathIndex"] == i), None)
        # 与 topology_switch.py 的 C2/C4 实测口径一致：只清 ACTIVE 的那条 path，
        # 同一块屏的其它 source 原样保留，快照负责兜底恢复。
        if (not info or info["anchor"] not in anchors
                or not info["connected"] or not info["active"]):
            continue
        p = paths[i]
        p.flags &= ~_CCD_PATH_ACTIVE
        p.sourceInfo.modeInfoIdx = _CCD_MODE_IDX_INVALID
        p.targetInfo.modeInfoIdx = _CCD_MODE_IDX_INVALID
        p.sourceInfo.statusFlags = 0
        p.targetInfo.statusFlags = 0

    rc = _ccd_apply(u32, paths, cur_modes, pc, cur_mc)
    if rc != 0:
        dlog("topo.off.fail", stage="apply", rc=rc)
        return [], [(-1, "SetDisplayConfig 失败 rc=%d（拓扑未改动）" % rc)]
    ok_off = _ccd_wait_state(anchors, False)
    ok_heir = (not heir_anchor) or _ccd_wait_state([heir_anchor], True, allow_vdd=True)
    if not (ok_off and ok_heir):
        back = _ccd_restore_from_bytes(u32, snap)
        why = "回读未确认断开，已回滚"
        if not ok_heir:
            why = "主屏接班没生效，已回滚"
        if back != 0:
            why += "；回滚失败 rc=%d（请用 --screen on 重试）" % back
        dlog("topo.off.fail", stage="verify", rollback=back,
             ok_off=ok_off, ok_heir=ok_heir)
        return [], [(-1, why)]
    # 只报"真正被断开"的下标
    done_devs = {_short_dev(r.get("gdi")) for r in raw_rows
                 if r["anchor"] in anchors and r["connected"] and r["active"]}
    done = sorted({i for name, i in dev_to_index.items()
                   if _short_dev(name) in done_devs})
    dlog("topo.off.done", anchors=anchors, indices=done, heir=heir_anchor)
    # 拓扑已经稳定，再把窗口搬回"跟着它的屏"的位置
    if win_snap:
        moved = win_follow(win_snap, shift, set(anchors))
        dlog("topo.off.windows", moved=moved, total=len(win_snap),
             shift=list(shift), dropped=len(anchors))
    return done or list(normalize_indices(indices)), []


def topology_inactive_rows(rows=None):
    """给 GUI 用：拓扑里「设备还连着但 ACTIVE=0」的屏——**全部**列出来。

    对齐 Windows 设置 → 屏幕：那里会把"断开但还接在机器上"的屏画出来，
    虚拟显示器（VDD）也画。所以这里**不再排除 VDD**，只把 `vdd` 标记带出去，
    由界面决定怎么呈现（VDD 不给「启用」按钮：它归串流软件管，
    本程序断开/接回它是没有意义的，硬断还会让串流黑屏）。

    纯函数，便于单测。VDD 仍然**永不作断开目标**——那是 topology_plan_off 的护栏，
    和"能不能看见"是两件事。
    """
    rows = rows if rows is not None else ccd_probe_topology()
    out = []
    for r in rows:
        if r.get("active") or not r.get("anchor"):
            continue
        if not r.get("connected", True):
            continue
        out.append({"anchor": r["anchor"],
                    "friendly": r.get("friendly") or "",
                    "gdi": r.get("gdi") or "",
                    "vdd": bool(r.get("vdd"))})
    return out


def topology_enable_anchor(cfg, anchor):
    """GUI「启用」按钮：把这一块屏加回**当前**拓扑（不再查历史快照）。

    v2.01.0 起不再要求"断开时留下的快照里得有它"——那等于要求程序维护状态账。
    现在只问当前拓扑：这块屏 connected 且 inactive 吗？是就直接接回。
    校验由 topology_plan_on 现读现判，这里不做重复预检。
    """
    anchor = str(anchor or "")
    if not anchor:
        return [], [(-1, "缺少显示器锚点")]
    return screen_on_cfg(cfg, [], anchors=[anchor])


def topology_indices_for_anchors(anchors):
    """把恢复出来的锚点映射回**当前**显示器下标（只认 ACTIVE source 的 GDI）。

    不能走 resolve_monitors()：它的 aliases 会把同一块屏的 4 个 DISPLAYn 混在一起，
    只勾副屏时会误中主屏。接回后 enumeratetargets 才重新看到副屏，所以必须现算。
    """
    wanted = set(anchors or [])
    if not wanted:
        return []
    targets = enumerate_targets()
    try:
        by_dev = {_short_dev(t.get("dev")): t.get("index") for t in targets}
    finally:
        release_targets(targets)
    out = []
    for r in ccd_probe_topology():
        if r["anchor"] in wanted and not r["vdd"]:
            i = by_dev.get(_short_dev(r.get("gdi")))
            if i is not None and i not in out:
                out.append(i)
    return sorted(out)


def topology_wanted_anchors(cfg, rows=None):
    """拓扑模式「点亮」要接回哪些屏——**每次现算**，不读任何记账。

    优先用用户勾选时存下的锚点（monitor_anchors）；没存过就表示"全部接回"，
    取当前拓扑里所有 connected + inactive 的物理屏（VDD 除外）。
    """
    saved = [str(a) for a in (cfg.get("monitor_anchors") or []) if a]
    if saved:
        return saved
    rows = rows if rows is not None else _ccd_query_raw_rows()
    return sorted({r["anchor"] for r in rows
                   if r.get("connected") and not r.get("active")
                   and not r.get("vdd") and r.get("anchor")})


def _ccd_query_raw_rows():
    """只读：当前拓扑的逐 path 行（失败返回空列表）。"""
    raw = _ccd_query_raw()
    return list(raw[4]) if raw else []


def topology_on(anchors, cfg=None):
    """拓扑接回：把 anchors 里的屏加回**当前**拓扑（声明式，无历史快照）。

    步骤：现读拓扑 → 现算要动哪几条 path / 哪个 mode → 提交 → 有界回读。
    同一块屏接两次与接一次结果相同（已 ACTIVE 的锚点直接跳过）。

    两档，每档都重新现读、绝不复用被改过的数组；**只有上一档被拒才降级**，
    所以 events.log 里每一句"改用…重试"都是真的发生过（v2.01.1 修：原来那句
    提示写在第一档里无条件输出，一次成功也被写成了"降级后成功"）：
      1) 现算 source/target/desktop 三个 modeInfoIdx —— 忠于这块屏原来的
         分辨率和桌面位置（`--verify` 证明与 Windows 自己填的逐位一致）。
      2) source 仍用现算值，target/desktop 交 INVALID 让 Windows 定
         （真机验证过：只把 target 置 INVALID 能过校验，rc=0）。

    ⚠️ 两条真机定论（都用 SDC_VALIDATE 验的，没有一次改动拓扑；
      scripts/topology_repro87.py 可复跑）：
      - **sourceInfo.modeInfoIdx 一旦置 INVALID 就必然 rc=87**，加多少
        ALLOW_CHANGES / PATH_PERSIST / FORCE_MODE_ENUMERATION 都救不回来。
        所以"两个都交 Windows 挑"这条老路是死的，source mode 必须有有效值。
      - **全量提交（整份 QDC_ALL_PATHS 233 条）本身是合法的，rc=0**。
        曾经怀疑"224 条未连接组合导致 87"并改成 SDC_TOPOLOGY_SUPPLIED 部分
        提交——实测部分提交才是 87，那个方向是错的，已撤销。

    而 source mode "算不出来"是**常态**，不是异常：一块屏被断开之后，Windows
    就把它从 modes 数组里摘掉了，接回时没有任何下标可引用。这时现造一条补进
    数组末尾（`_ccd_append_source_mode`），尺寸取该屏首选分辨率、位置取桌面
    最右边。真机矩阵见 scripts/topology_mode_synthesis.py。

    返回 (成功下标列表, [(下标, 错误), ...], restored_anchors)。
    亮度不在这里写：断开源在接回后才重新出现在 EnumDisplayMonitors 里，
    必须由 screen_on_cfg 用接回后的下标写，否则刚接回的屏拿不到亮度。
    """
    u32 = _ccd_api()
    if u32 is None:
        return [], [(-1, "拓扑模式仅支持 Windows")], []
    tries = ("full", "windows-picks-target")
    last_err = "CCD 查询失败，未做任何改动"
    dlog("topo.on", anchors=list(anchors or []),
         saved_positions=(cfg or {}).get("monitor_positions"))
    for n, mode in enumerate(tries):
        raw = _ccd_query_raw(u32)
        if raw is None:
            return [], [(-1, last_err)], []
        paths, modes, pc, mc, raw_rows = raw
        plan, errs, noop = topology_plan_on(anchors, raw_rows,
                                            _ccd_mode_entries(modes, mc))
        dlog("topo.on.plan", tier=mode, pc=pc, mc=mc, plan=plan,
             errs=errs, noop=noop)
        if errs:
            return [], [(-1, e) for e in errs], []
        if noop:                       # 已经全亮：不提交、不写日志
            return [], [], []
        if mode != "full":
            for item in plan:
                item["targetModeIdx"] = item["desktopModeIdx"] = None
        # 真实断开后 Windows 会把这块屏的 SOURCE mode 从 modes 数组里摘掉，
        # 空手引用必然 rc=87；这里现造一条补进数组末尾。
        # 位置优先摆回"断开前那一刻"的原位（cfg.monitor_positions），
        # 没记过、或会和现有屏重叠，才退回桌面最右边。
        cur_modes, cur_mc = _ccd_fill_missing_source_modes(
            u32, paths, modes, mc, raw_rows, plan,
            want=(cfg or {}).get("monitor_positions"))
        restored = _ccd_apply_plan_on(paths, plan)
        rc = _ccd_apply(u32, paths, cur_modes, pc, cur_mc)
        if rc == 0:
            if not _ccd_wait_state(restored, True):
                dlog("topo.on.fail", tier=mode, stage="verify", restored=restored)
                return [], [(-1, "接回后 2 秒内回读未确认 ACTIVE；亮度未写")], []
            dlog("topo.on.done", tier=mode, restored=restored, mc=cur_mc)
            return topology_indices_for_anchors(restored), [], restored
        last_err = "SetDisplayConfig 失败 rc=%d（拓扑未改动）" % rc
        dlog("topo.on.fail", tier=mode, stage="apply", rc=rc, mc=cur_mc, pc=pc)
        if n + 1 < len(tries):
            # 降级是用户能感知的（结果一样但过程换了路），说人话，别甩参数名
            _write_event_log("接回显示器：第一套参数被 Windows 拒绝（rc=%d），"
                             "已自动换一种方式重试" % rc)
    return [], [(-1, last_err)], []


# ---- 熄屏 / 点亮 ----------------------------------------------------------
# ⚠️ 硬件红线，两条，都有本机实测事故背书：
# 1) **绝不用 `0xD6=4/5`（软关/硬关）** —— 面板进固件级熄灭，DDC 点亮 / 驱动重启 /
#    拓扑重握手 / 信号源切换全部唤不醒，只有物理电源键能救。
# 2) **v1.7.0 现场事故：`0xD6=2`（待机）也不该用来熄灭** ——
#    本机写 `0xD6=2` 后：回读仍是 `1`、亮度照样读得到，但面板背光真的灭了；
#    随后 `0xD6=1`、写亮度、`0x60` 切源（4 秒往返 + 补发）全部救不回来，
#    最后靠物理电源键恢复。结论：**"熄灭"容易，"点亮"却不由软件决定**。
#
# 所以 v1.7.1 起，熄灭改走 Windows 系统级：
#     SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
#   - 恢复由系统负责（`SC_MONITORPOWER -1` 一定亮回来），不依赖面板固件；
#   - 代价是只能整组熄灭，不能只灭其中几台 —— 对防窥场景没有区别。
#   DDC 待机方式保留为高级选项（`screen_off_method=ddc`），默认关闭。
SC_MONITORPOWER = 0xF170
SC_OFF = 2          # 关闭显示器（比 1 低功耗更彻底，系统仍能恢复）
SC_ON = -1          # 点亮显示器
_WM_SYSCOMMAND = 0x0112
_HWND_BROADCAST = 0xFFFF

# 唤醒分级：DDC 方式下的顺序，从"最标准、最不可能把屏搞黑"的一招开始。
# 为什么不再一上来就切信号源（v1.6.x 的做法）：
#   旧代码把"切到另一个输入"写死成 4（DVI-2）。现代显示器基本没有 DVI-2，
#   切过去等于切到无信号口 → 面板进省电，再切回来能不能醒取决于链路重训练，
#   于是表现为"有时行、有时不行"。若这台屏还同时插了 DP+HDMI 两根线，
#   切到 HDMI 正好切到 Windows 没在驱动的那个空口上，画面直接黑在空口。
WAKE_STAGE_LABEL = {1: "电源模式", 2: "写亮度", 3: "系统输入", 4: "切信号源"}


def _broadcast_monitor_power(flag):
    """向系统广播"显示器电源"命令，返回是否下发成功。"""
    try:
        return bool(ctypes.windll.user32.SendMessageTimeoutW(
            _HWND_BROADCAST, _WM_SYSCOMMAND, SC_MONITORPOWER, flag, 0, 2000, None))
    except Exception:
        return False


def screen_off_system():
    """系统级熄灭全部显示器（可恢复）。"""
    return _broadcast_monitor_power(SC_OFF)


def screen_on_system():
    """系统级点亮，并补一次输入事件（Windows 有时不主动重发画面）。"""
    ok = _broadcast_monitor_power(SC_ON)
    _nudge_user_input()
    return ok


def _nudge_user_input():
    """喂一次真实的用户输入事件。

    Windows 有时在收到点亮命令后仍不重发画面（它仍认为显示器处于关闭状态），
    一次微小的鼠标移动就能把它踢回"有显示"。移 1 像素再移回来，肉眼无感。
    """
    try:
        u = ctypes.windll.user32
        u.mouse_event(0x0001, 1, 0, 0, 0)      # MOUSEEVENTF_MOVE
        u.mouse_event(0x0001, -1, 0, 0, 0)
        return True
    except Exception:
        return False


def _switch_input_roundtrip(t):
    """切到"另一个真实支持的输入源"，等 2 秒再切回来。返回两端是否都下发成功。

    选源规则（顺序即优先级）：
      1) 能力串里 0x60 声明支持的口（真实存在的口）；
      2) 其中只留 DP/HDMI（15~19）—— 模拟/DVI/复合口切过去基本等于无信号；
      3) 优先挑"与当前口异类"的：当前在 DP 就先试 HDMI，反之亦然，
         因为双线接法下另一根线通常是另一种口，切过去有极大概率真的有信号。
    **不再用旧版的 4（DVI-2）**：那是多数显示器根本没接的口，切过去即黑屏。
    """
    h = t["handle"]
    got = _read_vcp(h, VCP_INPUT_SOURCE)
    if not got:
        return False
    cur = got[0]
    declared = [c for c in _caps_inputs(_capabilities_cached(h, _display_key(t)))
                if 15 <= c <= 19]
    cand = [c for c in (declared or [15, 16, 17, 18, 19]) if c != cur]
    if not cand:
        return False
    cur_is_dp = 15 <= cur <= 16
    cand.sort(key=lambda c: (cur_is_dp == (15 <= c <= 16), c))
    if not _write_vcp(h, VCP_INPUT_SOURCE, cand[0]):
        return False
    time.sleep(2)
    return _write_vcp(h, VCP_INPUT_SOURCE, cur)


def _wake_one(t, allow_switch=True, expect_off=True, allow_recycle=True):
    """分级唤醒单台显示器。返回 (命令是否都下发成功, 走到第几级, 说明)。

    ⚠️ **不用 `0xD6` 回读判断"醒了没有"**（v1.7.0 现场取证）：
    本机写入 `0xD6=2` 待机之后，回读仍是 `1`、`0x10` 亮度也照样读得到。
    拿它当判据就会出现"程序说点亮成功、屏幕其实黑着"——和 v1.5.0 熄屏事故同源。
    所以这里只保证"该发的命令都发到位"，真实亮灭交给「检测亮灭」+ 眼睛。

    ⚠️ `allow_recycle=False`（v1.9.6）：只勾选了部分显示器时禁用第 3 级的
    `SC_MONITORPOWER` 广播。那是**系统级**调用，会作用于所有显示器，把没勾选的
    那台也黑一下再点亮。此时只用每台的 `0xD6=1` + 亮度写 + 输入注入。

    顺序按"危害从小到大"，前三步都不碰信号源：
      1 `0xD6=1` 电源模式=开（标准唤醒命令，无害）
      2 写回当前亮度（部分固件靠收到 VCP 写入才醒）
      3 **让系统把显示输出关一次再打开**（SC_MONITORPOWER 2→-1）+ 输入注入。
        依据：显示器"亮着却黑屏"的公开排查一致指向 GPU 与面板握手失败，
        标准恢复手段就是切一次显示模式 / 让输出重新上电。此时屏幕本来就是黑的，
        重驱动不会造成额外闪烁。
      4 切信号源（唯一可能把屏切到空口的一招：受 allow_switch 控制，
        并且只在"软件确实熄过这块屏"时才做，避免给亮着的屏白闪 2 秒）
    """
    h = t["handle"]
    wrote = False
    stage = 0

    if _write_vcp(h, VCP_POWER_MODE, POWER_ON):
        wrote, stage = True, 1
        time.sleep(0.35)

    cur = _read_vcp(h, VCP_BRIGHTNESS)
    if cur and _write_vcp(h, VCP_BRIGHTNESS, cur[0]):
        wrote, stage = True, 2
        time.sleep(0.35)

    if not wrote:
        # 两个最基本的写都不通，后面切信号源也不会通，早点收工别让用户干等
        return False, 0, "显示器未响应 DDC/CI（电源 / 亮度写入均失败）"

    output_recycled = False
    if not expect_off:
        _nudge_user_input()          # 屏幕本来就亮着：只喂输入，别去动输出
    else:
        if allow_recycle:
            output_recycled = _output_recycle()
        _nudge_user_input()
    stage = max(stage, 3)
    time.sleep(0.35)

    if not allow_switch:
        note = ("已下发电源/亮度/输出重驱动" if output_recycled
                else "已下发电源/亮度/输入注入")
        return wrote, stage, note + "（切信号源已禁用）"
    if not expect_off:
        return wrote, stage, "已下发电源/亮度/系统输入（未熄过屏，跳过切信号源）"
    if not _switch_input_roundtrip(t):
        note = "切信号源失败（前几级已下发）"
        if output_recycled:
            note += "，输出已重新驱动过"
        return wrote, stage, note
    return wrote, 4, "已下发电源/亮度/输出重驱动/信号源切换"


def _output_recycle():
    """把显示输出关一次再打开，强制 GPU 与面板重新握手。

    返回是否两次下发都成功。依据见 _wake_one 第 3 步的说明。
    """
    off_ok = _broadcast_monitor_power(SC_OFF)
    time.sleep(0.8)
    on_ok = _broadcast_monitor_power(SC_ON)
    return off_ok and on_ok


def screen_off_many(indices, allow_undeclared=False):
    """批量熄灭（DDC 待机 0xD6=2）。indices 空 -> 全部。

    ⚠️ 先查面板能力串：**没声明支持 02（待机）的一律跳过**，不做"写写看"。
    依据：MS Learn 的 SetVCPFeature 文档明确警告盲写未实现的 MCCS 值会导致
    "undefined monitor behavior"；本机面板正是不支持 02 却被盲写，
    结果进入了 DDC 能读、背光却唤不回来的状态。
    """
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    ok, errs = [], []
    try:
        picked, bad = _select(targets, indices)
        errs.extend(bad)
        for t in picked:
            declared = _caps_power(_capabilities_cached(t["handle"], _display_key(t)))
            if POWER_STANDBY not in declared and not allow_undeclared:
                errs.append((t["index"], "面板未声明支持待机(0xD6=2)%s，已跳过"
                             "（要强制启用请重选「单屏待机」并确认风险）"
                             % ("，只支持 %s" % "/".join(str(v) for v in declared)
                                if declared else "（能力串也读不到）")))
                continue
            if _write_vcp(t["handle"], VCP_POWER_MODE, POWER_STANDBY):
                ok.append(t["index"])
            else:
                errs.append((t["index"], "SetVCPFeature 失败"))
    finally:
        release_targets(targets)
    return ok, errs


def screen_wake_many(indices, allow_switch=True, expect_off=True):
    """批量点亮（分级唤醒，并行执行）。indices 空 -> 全部。

    并行是因为每台要 0.6~6 秒，两台串行就翻倍；不同显示器的 DDC 句柄互相独立，
    并行写是安全的（型号名读取早就这么干了）。
    allow_switch=False 关掉"切信号源"这最后一招（双线接法可避免切到空口）。
    expect_off=False 表示"屏幕可能本来就亮着"，此时先探测、亮着就跳过。
    返回 (成功序号, [(序号, 错误), ...])，错误文案带走到第几级。
    """
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    picked, bad = _select(targets, indices)
    # v1.9.6：只勾选部分显示器时，禁用系统级 SC_MONITORPOWER 重驱动
    # （广播会把没勾选的那台也黑一下）。全选时保持原行为，逐字节不变。
    allow_recycle = len(picked) == len(targets)
    results = {}

    def work(t):
        try:
            results[t["index"]] = _wake_one(t, allow_switch, expect_off, allow_recycle)
        except Exception as e:
            results[t["index"]] = (False, 0, str(e))

    threads = [threading.Thread(target=work, args=(t,)) for t in picked]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)

    ok, errs = [], list(bad)
    try:
        for t in picked:
            done, stage, why = results.get(t["index"], (False, 0, "超时未返回"))
            if done:
                ok.append(t["index"])
            else:
                errs.append((t["index"], "第 %d 级（%s）：%s"
                             % (stage, WAKE_STAGE_LABEL.get(stage, "-"), why)))
    finally:
        release_targets(targets)
    return ok, errs


# ---- 对外统一入口：按配置的熄灭方式分派 -----------------------------------
def _monitor_count():
    """当前显示器台数（只为报数，句柄立刻还掉）。"""
    targets = enumerate_targets()
    try:
        return len(targets)
    finally:
        release_targets(targets)


def _system_ok_targets(indices):
    """系统级动作没有"每台"的概念，用动作前选中的序号充数（空 = 全部）。"""
    return normalize_indices(indices) or list(range(_monitor_count()))


def screen_off_cfg(cfg, indices):
    """熄灭显示器。ddc = 逐台 0xD6=2；topology = CCD 断开副屏；其余系统级。

    返回 (成功序号列表, [(序号, 错误), ...])，与逐台接口保持同一形状，
    这样界面与 CLI 不用关心底层是哪种方式。
    """
    dlog("screen.off.enter", method=cfg.get("screen_off_method"),
         indices=normalize_indices(indices))
    if cfg.get("screen_off_method") == "ddc":
        out = screen_off_many(indices, bool(cfg.get("ddc_allow_undeclared")))
    elif cfg.get("screen_off_method") == "topology":
        out = topology_off(indices, cfg)
    elif not screen_off_system():
        out = ([], [(-1, "系统级熄灭下发失败")])
    else:
        out = (_system_ok_targets(indices), [])
    dlog("screen.off.exit", ok=out[0], errs=out[1])
    return out


def screen_on_row(cfg, index):
    """行内「显示」：只点亮这一块。

    拓扑模式下分两种情况，靠"有没有真的接回一块屏"来分：
      - 它确实断开了 → 按拓扑接回，再写亮度；
      - 它本来就在拓扑里（`restored` 为空）→ 那多半只是面板被待机了，
        拓扑帮不上忙，改走 DDC 唤醒。**不许切信号源**：拓扑模式下切源
        会把副屏切到空口，等于自找黑屏。
    """
    if cfg.get("screen_off_method") != "topology":
        return screen_wake_entry(cfg, [index])
    anchors = selection_anchors([index])
    if not anchors:
        return [], [(-1, "拿不到这块屏的锚点，无法单独点亮")]
    ok, errs, restored = topology_on(anchors, cfg)
    if errs:
        return ok, errs
    ids = topology_indices_for_anchors(restored) or [index]
    bok, berrs = set_brightness_many(ids, current_level(cfg))
    if restored:
        dlog("screen.on.row", index=index, via="topology", ids=ids)
        return ok + bok, errs + berrs
    wok, werrs = screen_wake_many([index], allow_switch=False)
    dlog("screen.on.row", index=index, via="ddc-wake", ok=wok, errs=werrs)
    return wok + bok, werrs + berrs


def screen_on_cfg(cfg, indices, anchors=None, wake_opts=None):
    """点亮显示器。ddc = 分级 DDC；topology = 现读现算接回后写亮度；其余系统级。"""
    dlog("screen.on.enter", method=cfg.get("screen_off_method"),
         indices=normalize_indices(indices), anchors=anchors)
    if cfg.get("screen_off_method") == "ddc":
        out = screen_wake_many(indices, **(wake_opts or wake_options(cfg)))
        dlog("screen.on.exit", ok=out[0], errs=out[1])
        return out
    if cfg.get("screen_off_method") == "topology":
        want = [str(a) for a in anchors] if anchors else topology_wanted_anchors(cfg)
        ok, errs, restored = topology_on(want, cfg)
        if errs:
            return ok, errs
        # 断开的屏在接回后才回到 EnumDisplayMonitors；用「接回后」的下标写亮度，
        # 否则副屏接回来了却拿不到亮度（D3）。
        write_to = sorted(set(normalize_indices(indices))
                          | set(topology_indices_for_anchors(restored)))
        if not write_to:
            return ok, errs
        bok, berrs = set_brightness_many(write_to, current_level(cfg))
        return ok + bok, errs + berrs
    if not screen_on_system():
        return [], [(-1, "系统级点亮下发失败")]
    return _system_ok_targets(indices), []


# ============================================================
# 快速点亮（v1.9.7）：0x60 信号源翻转，0 等待
# ------------------------------------------------------------
# 现场依据：两次独立实测（Legion R27qe Gen2 / DISPLAY2，16:57 与 17:00）
#   读 0x60 → 写 17 → 立即写回 15：动作 182~188ms，两次都把屏点亮，
#   读回 0x60 均回到原值 15。
# 为什么不用等：SetVCPFeature 返回只代表这次 DDC 写事务被面板 ACK，真正的点亮
# 来自「离开当前口→再回来」这一个边沿——DP 接收端必须重跑链路训练并重锁时序。
# 停在中途多久与重锁无关（v1.9.6 的 2 秒只是让第二次写不落在面板切换窗口里）。
#
# 边界：本路径只写 0x60；不写 0xD6 / 0x10，不发系统广播。
# 失败（读不到当前源 / 无可用中间源 / 写失败）一律回退 v1.9.6 的完整阶梯；
# 同一台 8 秒内第二次触发点亮时也升级为完整阶梯。
# ============================================================
FAST_FLIP_WINDOW_S = 8.0
_FAST_FLIP_AT = {}                 # 显示器标识 -> 上次触发的时间（monotonic 秒）
_FAST_FLIP_LOCK = threading.Lock()
_WAKE_INFO = []                    # 本次点亮的说明行（后台线程写，界面/CLI 读）
_WAKE_INFO_LOCK = threading.Lock()


def _wake_info(text):
    """追加一行本次点亮的说明（线程安全）。"""
    with _WAKE_INFO_LOCK:
        _WAKE_INFO.append(str(text))


def reset_wake_info():
    with _WAKE_INFO_LOCK:
        del _WAKE_INFO[:]


def take_wake_info():
    """取走并清空本次点亮的说明行；界面状态行 / CLI 输出用。"""
    with _WAKE_INFO_LOCK:
        out = list(_WAKE_INFO)
        del _WAKE_INFO[:]
    return out


def pick_flip_source(cur, declared):
    """纯函数：为 0x60 翻转挑一个**与当前值不同**的中间源。

    规则（按优先级）：
      1) 优先 17（HDMI-1，现场实测在这台 Legion 上有效）；
      2) 当前就是 17 时，从能力串声明的口里另选一个——优先 15（DP-1），
         其次按 15/16/17/18/19 的顺序；
      3) 能力串读不到时退回 MCCS 15~19 的通用候选（与 _switch_input_roundtrip 同口径）。
    没有可用候选（例如能力串只声明了当前这一个口）返回 None。
    ⚠️ 能力串里的数值是十六进制（见 _caps_inputs），这里接收的已是整数。
    """
    try:
        cur = int(cur)
    except Exception:
        return None
    cands = []
    for c in (declared or []):
        try:
            c = int(c)
        except Exception:
            continue
        if 15 <= c <= 19 and c not in cands:
            cands.append(c)
    if not cands:
        cands = [15, 16, 17, 18, 19]
    if 17 in cands and cur != 17:
        return 17
    for c in (15, 16, 17, 18, 19):
        if c in cands and c != cur:
            return c
    return None


def _fast_flip_recent(key, now=None):
    """这台屏在 8 秒内是否已经快速点亮过一次（只读，不改时间戳）。"""
    now = time.monotonic() if now is None else now
    with _FAST_FLIP_LOCK:
        last = _FAST_FLIP_AT.get(key)
        return last is not None and (now - last) <= FAST_FLIP_WINDOW_S


def _mark_fast_flip(key, now=None):
    """记下这台屏的快速点亮时刻（后续 8 秒内再点亮就升级完整阶梯）。"""
    now = time.monotonic() if now is None else now
    with _FAST_FLIP_LOCK:
        _FAST_FLIP_AT[key] = now


def _fast_flip_one(t):
    """对一台屏做 0x60 翻转，返回 (成功, 说明)。真实调用序列（无 sleep）：

      GetVCPFeatureAndVCPFeatureReply(h, 0x60)   读当前源
      SetVCPFeature(h, 0x60, 中间源)             切走
      SetVCPFeature(h, 0x60, 当前源)             立即切回

    ⚠️ 写本回失败时先立即补写一次（不等待），仍失败才判失败；
    并把「可能停在中间源」写进说明，避免界面报"已点亮"而屏其实不在原口。
    """
    t0 = time.perf_counter()
    h = t["handle"]
    got = _read_vcp(h, VCP_INPUT_SOURCE)
    if not got:
        return False, "读 0x60 失败"
    cur = int(got[0])
    # 只有当前源正好是 17 时才需要读能力串另选一个口；其余情况直接用 17，
    # 避免为了"快速"在缓存未命中时搭进去 3 秒的能力串实读。
    declared = []
    reason = ""
    if cur == 17:
        declared = _caps_inputs(_capabilities_cached(h, _display_key(t)))
    mid = pick_flip_source(cur, declared)
    if mid is None:
        return False, "0x60 无可用中间源（当前 %d）" % cur
    if cur == 17:
        reason = "（当前已是 17，按能力串声明改选 %d）" % mid
    if not _write_vcp(h, VCP_INPUT_SOURCE, mid):
        return False, "写 0x60=%d 失败（当前 %d）" % (mid, cur)
    if not _write_vcp(h, VCP_INPUT_SOURCE, cur):
        _write_vcp(h, VCP_INPUT_SOURCE, cur)          # 立即补写一次，不等待
        back = _read_vcp(h, VCP_INPUT_SOURCE)
        if not back or int(back[0]) != cur:
            return False, ("写回 0x60=%d 失败（现读 %s），屏可能停在中间源 %d"
                           % (cur, ("%d" % back[0]) if back else "—", mid))
    ms = (time.perf_counter() - t0) * 1000.0
    return True, "翻转 0x60 %d→%d→%d，耗时 %.1f ms%s" % (cur, mid, cur, ms, reason)


def screen_wake_flip(indices, allow_switch=True, expect_off=True):
    """快速点亮：逐台 0x60 翻转。与 screen_wake_many 同形状返回。

    全选/部分选择都只操作选中的屏，**绝不发系统广播**。
    同一台 8 秒内第二次触发、或翻转做不成时，该台回退到 _wake_one 完整阶梯。
    """
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    picked, bad = _select(targets, indices)
    # 与 screen_wake_many 同一口径：只有全选时才允许升级路径里的系统级重驱动
    allow_recycle = len(picked) == len(targets)
    now = time.monotonic()
    results = {}

    def work(t):
        key = _display_key(t)
        try:
            if _fast_flip_recent(key, now):
                _mark_fast_flip(key, now)
                _wake_info("已升级为完整点亮（显示器 %d 在 8 秒内第二次点亮）" % t["index"])
                # 用户 8 秒内再点一次 = 第一次没亮：强制按"屏仍黑"走完整阶梯
                results[t["index"]] = _wake_one(t, allow_switch, True, allow_recycle)
                return
            if not expect_off:
                # 屏亮着、且没有近期的快速点亮记录：不翻转（避免可见黑屏），按旧行为
                results[t["index"]] = _wake_one(t, allow_switch, False, allow_recycle)
                return
            _mark_fast_flip(key, now)
            ok, note = _fast_flip_one(t)
            if ok:
                _wake_info("快速点亮：" + note)
                results[t["index"]] = (True, 1, note)
                return
            _wake_info("快速点亮不可用（%s），已回退完整阶梯" % note)
            results[t["index"]] = _wake_one(t, allow_switch, expect_off, allow_recycle)
        except Exception as e:
            results[t["index"]] = (False, 0, str(e))

    threads = [threading.Thread(target=work, args=(t,)) for t in picked]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)

    ok, errs = [], list(bad)
    try:
        for t in picked:
            done, stage, why = results.get(t["index"], (False, 0, "超时未返回"))
            if done:
                ok.append(t["index"])
            else:
                errs.append((t["index"], "第 %d 级（%s）：%s"
                             % (stage, WAKE_STAGE_LABEL.get(stage, "-"), why)))
    finally:
        release_targets(targets)
    return ok, errs


def screen_wake_entry(cfg, indices):
    """点亮统一入口（v1.9.7）：快速翻转优先，其余情况原样走 v1.9.6。

    只有同时满足以下条件才走快速路径，任何一条不满足都完全回到旧行为：
      - 开关 wake_fast_flip 打开（旧配置缺键 -> 默认 1）；
      - 熄灭方式为 ddc（system 模式的 SC_MONITORPOWER -1 是即时广播，不需要翻转）；
      - 允许切信号源（用户关掉「切信号源」时不得偷偷切换）。
    `expect_off` 由 wake_options 当场探测得出（v2.01.0 起不读记账）：
    探不到"确实待机"就按保守路径走，不翻转、不切源。

    ⚠️ 这里只算一次 wake_options：它内部要读 DDC，算两次会让点亮慢一倍。
    """
    reset_wake_info()
    if (not bool(cfg.get("wake_fast_flip", True))
            or cfg.get("screen_off_method") != "ddc"):
        return screen_on_cfg(cfg, indices)
    opts = wake_options(cfg)
    if not opts["allow_switch"]:
        return screen_on_cfg(cfg, indices, wake_opts=opts)
    return screen_wake_flip(indices, opts["allow_switch"], opts["expect_off"])


# ============================================================
# 三、按时间判定白天 / 晚上
#    所有计划任务都执行 --apply-schedule，由程序按「执行时刻」自行判定该用哪一档。
#    这样即便白天、晚上两个任务被同时补跑，结果也一致（幂等）。
# ============================================================
def parse_hhmm(text, default=(9, 0)):
    """'HH:MM' → 当日分钟数；解析失败返回 default。"""
    try:
        parts = str(text).strip().split(":")
        if len(parts) != 2:
            raise ValueError
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return default[0] * 60 + default[1]


def _minutes(now=None):
    now = now or datetime.now()
    return now.hour * 60 + now.minute


def _slot_minutes(cfg):
    """cfg["slots"] -> [(当日分钟, 亮度)]，按时间排序。"""
    out = []
    for x in cfg.get("slots", []):
        out.append((parse_hhmm(x.get("time"), (0, 0)), clamp_level(x.get("level", 50))))
    return sorted(out)


def active_slot(slots, now_min):
    """N 档调度核心：取 time <= now 的最后一档；一个都没有 -> 最后一档（跨午夜回绕）。

    slots 为 [(当日分钟, 亮度)]，返回 (当日分钟, 亮度)。
    """
    best = None
    for m, lv in sorted(slots):
        if m <= now_min:
            best = (m, lv)
    if best is None:
        best = sorted(slots)[-1]
    return best


def active_slot_dict(slots, now_min):
    """同 active_slot 的选择规则，但作用于原始档位 dict（含 screen 动作）。

    取 time <= now_min 的最后一档；一个都没有 -> 时间最大的最后一档（跨午夜回绕）。
    """
    best = None
    best_m = None
    for s in slots:
        m = parse_hhmm(s.get("time"), (0, 0))
        if m <= now_min and (best is None or m >= best_m):
            best, best_m = s, m
    if best is None:
        best = max(slots, key=lambda x: parse_hhmm(x.get("time"), (0, 0)))
    return best


def current_level(cfg=None, now=None):
    """当前时刻应处的亮度（纯逻辑；定时任务与「测试当前档」共用）。"""
    cfg = cfg or load_config()
    return active_slot(_slot_minutes(cfg), _minutes(now))[1]


def _short_dev(name):
    """'\\\\.\\DISPLAY1' / 'DISPLAY1' -> 'display1'（大小写、前缀都归一）。"""
    return str(name or "").split("\\")[-1].strip().lower()


def selection_anchors(indices, targets=None):
    """给勾选的下标生成 CCD 稳定锚点（E2）。

    锚点来自 `ccd_probe_topology()`：显卡 LUID + EDID mfr/prod + connectorInstance。
    **只按 `gdi`（当前 ACTIVE 的 source 名）匹配，绝不用 `aliases`**：
    QDC_ALL_PATHS 下每块屏的 aliases 都会列出 DISPLAY1~4 全部四个 source 名，
    用它匹配等于"任何选中都命中所有屏"——v2.01.8 实测：只点一块屏的「显示」，
    实际拿到了两块屏的锚点，于是"点亮这一块"变成了"接回全部"。
    取不到就返回 []，绝不把不确定的锚点写进配置。
    """
    idx = normalize_indices(indices)
    if not idx:
        return []                          # 空选择 = 全部，不锚死
    owned = targets is None
    if owned:
        targets = enumerate_targets()
    try:
        wanted = {_short_dev(t["dev"]) for t in targets if t["index"] in idx}
        rows = ccd_probe_topology()
        out = []
        for r in rows:
            if r["vdd"]:
                continue
            # v2.01.18：只认 ACTIVE path 的 gdi（微软文档 DISPLAYCONFIG_PATH_ACTIVE
            # = "路径处于活动状态且是桌面的一部分"）。断开屏没有 active path，
            # 折叠后残留的 gdi 会和当前主屏撞名（都是 DISPLAY1）；不排除它，
            # selection_anchors 会把主屏和断开屏一起命中 → 交接弹窗 skip 掉
            # 两块 → 候选只剩 VDD（用户 19:46 截图实证）。
            if not r.get("active"):
                continue
            if _short_dev(r.get("gdi")) in wanted and r["anchor"] not in out:
                out.append(r["anchor"])
        return out
    finally:
        if owned:
            release_targets(targets)


def topology_heir_candidates(off_anchors=(), rows=None):
    """能接班当主屏的屏：连着、且不在这次要断开的名单里。

    物理屏排前面（用户看得见），虚拟屏排后面 —— 只有"全部物理屏都要断"
    的时候才会轮到它，那正是串流场景。
    """
    rows = rows if rows is not None else ccd_probe_topology()
    skip = set(off_anchors or ())
    out, seen = [], set()
    for r in rows:
        anchor = r.get("anchor")
        if not r.get("connected") or not anchor or anchor in skip or anchor in seen:
            continue
        seen.add(anchor)
        out.append({"anchor": anchor,
                    "friendly": r.get("friendly") or anchor,
                    "vdd": bool(r.get("vdd")),
                    "active": bool(r.get("active")),
                    "gdi": r.get("gdi") or ""})
    out.sort(key=lambda c: (c["vdd"], not c["active"]))
    return out


def remember_layout_snapshot(cfg):
    """把"当前拓扑"记成用户的显示器布局（v2.01.11）。

    位置记录**不会自动覆盖**：交接主屏会把接班屏挪到桌面原点，那一刻的
    "真实位置"不是用户摆的。这条是唯一的主动更新入口，放主线程同步做
    （一次 CCD 查询 ~50ms，不值得异步）。
    返回 (布局, None) 或 (None, 错误文案)。
    """
    u32 = _ccd_api()
    raw = _ccd_query_raw(u32) if u32 is not None else None
    if raw is None:
        return None, "读不到当前显示器拓扑"
    paths, modes, pc, mc, rows = raw
    layout = _ccd_layout_by_anchor(rows, paths, modes)
    if not layout:
        return None, "当前没有活动的显示器"
    cfg["monitor_positions"] = {a: list(r) for a, r in layout.items()}
    ok, err = save_config(cfg)
    if not ok:
        return None, err or "配置保存失败"
    dlog("app.remember_layout", layout=cfg["monitor_positions"])
    return cfg["monitor_positions"], None


# ---- 默认双屏布局快照（v2.01.12） ----------------------------------------
# 「恢复双屏」按钮要回到的是"用户记住的那一版默认布局"。整份 CCD 字节写回
# 是唯一同时做到「接回物理屏 + 让 VDD 退场 + 摆回原位」的办法（普通 topology_on
# 在 VDD 占着 (0,0) 时会把物理屏挤到桌面最右，参见 17:40 的 diag.log）。
# 快照跟 monitor_positions 一起记：**只在用户主动点「记住布局」时写**，
# 不自动覆盖（交接主屏那一刻的布局不是用户摆的，见 v2.01.11）。
DEFAULT_LAYOUT_FILE = os.path.join(CONFIG_DIR, "default_layout.json")
_SDC_VALIDATE = 0x00000040
# 必须与 _CCD_APPLY_FLAGS 完全同一套旗标，只把 APPLY 换成 VALIDATE，
# 否则会 rc=87 假失败（真机 18:5x 实测：漏掉 SAVE_TO_DATABASE 就拒）。
# 口径与 scripts/topology_handover_probe.py / topology_repro87.py 一致。
_VALIDATE_FLAGS = ((_CCD_APPLY_FLAGS | _SDC_VALIDATE)
                   & ~(_SDC_APPLY | _SDC_SAVE_TO_DATABASE))


def save_default_layout():
    """把当前完整 CCD 布局存成"默认双屏"快照。

    返回 (简述, 错误文案)。简述里带屏数与友好名，给状态卡显示。
    快照里的 bytes 已按 count 截断（_ccd_slice_bytes），不会越界。
    """
    u32 = _ccd_api()
    raw = _ccd_query_raw(u32) if u32 is not None else None
    if raw is None:
        return None, "读不到当前显示器拓扑"
    paths, modes, pc, mc, rows = raw
    visible = [r for r in rows
               if r.get("active") and r.get("connected") and r.get("anchor")]
    # 同一块屏在 QDC_ALL_PATHS 下有多条 path，按锚点去重
    anchors = list(dict.fromkeys(r["anchor"] for r in visible))
    if not anchors:
        return None, "当前没有活动的显示器"
    snap = _ccd_make_snapshot(paths, modes, pc, mc, anchors)
    snap["note"] = "用户记住的默认双屏布局；「恢复双屏」按这份整份写回"
    try:
        _atomic_write_text(DEFAULT_LAYOUT_FILE,
                           json.dumps(snap, ensure_ascii=False, indent=2))
    except Exception as e:
        return None, str(e)
    dlog("app.save_default_layout", anchors=anchors, pc=pc, mc=mc)
    return "%d 块屏（%s）" % (len(anchors), ", ".join(
        r["friendly"] or "显示器" for r in
        {a: next(x for x in visible if x["anchor"] == a) for a in anchors}.values())), None


def load_default_layout():
    """读回默认布局快照；文件不存在/坏掉返回 (None, 原因)。"""
    try:
        with open(DEFAULT_LAYOUT_FILE, "r", encoding="utf-8") as f:
            snap = json.load(f)
        if not all(k in snap for k in ("pathCount", "modeCount",
                                       "pathsHex", "modesHex")):
            return None, "默认布局快照格式不对"
        return snap, None
    except FileNotFoundError:
        return None, "还没记住默认布局"
    except Exception as e:
        return None, "读默认布局失败：%s" % e


def restore_default_layout(validate_only=False, snap=None):
    """把默认双屏布局整份写回。

    先 SDC_VALIDATE 校验提交形态（只读、不碰桌面），rc=0 才真正 APPLY。
    长度对不上/校验失败一律不写。返回 (错误文案 或 None)。

    `validate_only=True` 只做校验，给按钮预检用；`snap` 可注入，便于单测。
    """
    if snap is None:
        snap, err = load_default_layout()
        if err:
            return err
    u32 = _ccd_api()
    if u32 is None:
        return "恢复双屏仅支持 Windows"
    try:
        u32.SetDisplayConfig.argtypes = [
            wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        u32.SetDisplayConfig.restype = wintypes.LONG
    except (AttributeError, TypeError):
        pass                      # 测试假对象没有 ctypes 的 argtypes

    def build():
        paths = (_CcdPathInfo * int(snap["pathCount"]))()
        modes = (_CcdModeInfo * int(snap["modeCount"]))()
        pb = bytes.fromhex(snap["pathsHex"])
        mb_ = bytes.fromhex(snap["modesHex"])
        if len(pb) != ctypes.sizeof(paths) or len(mb_) != ctypes.sizeof(modes):
            return None, None, None, "快照与当前显示器结构不符，拒绝写回"
        if len(pb):
            ctypes.memmove(ctypes.byref(paths), pb, len(pb))
        if len(mb_):
            ctypes.memmove(ctypes.byref(modes), mb_, len(mb_))
        return paths, modes, (snap["pathCount"], snap["modeCount"]), None

    paths, modes, counts, err = build()
    if err:
        dlog("app.restore_default.fail", stage="shape", err=err)
        return err
    pc, mc = counts
    rc = u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes),
                              _VALIDATE_FLAGS)
    dlog("app.restore_default.validate", pc=pc, mc=mc,
         flags="0x%08X" % _VALIDATE_FLAGS, rc=rc)
    if rc != 0:
        return "默认布局已和当前硬件不匹配（校验 rc=%d），请重新「记住布局」" % rc
    if validate_only:
        return None
    # 校验通过 → 用带 SAVE_TO_DATABASE 的正式 flags 提交
    rc = _ccd_apply(u32, paths, modes, pc, mc)
    if rc != 0:
        dlog("app.restore_default.fail", stage="apply", rc=rc)
        return "恢复失败：SetDisplayConfig rc=%d（拓扑未改动）" % rc
    anchors = list(snap.get("targetAnchors") or [])
    # v2.01.19：快照可能是在串流形态（VDD 当主屏）下记的，此时 targetAnchors
    # 含 VDD。_ccd_wait_state 的 allow_vdd=False 会把 VDD 过滤掉，导致
    # wanted 里的 VDD 永远等不到 → 误报"2 秒内没读到确认"（实际已写回）。
    # 所以只拿物理屏锚点做回读验证。
    try:
        vdd_anchors = {r["anchor"] for r in ccd_probe_topology()
                       if r.get("vdd") and r.get("anchor")}
    except Exception:
        vdd_anchors = set()
    phys = [a for a in anchors if a not in vdd_anchors]
    ok = _ccd_wait_state(phys, True) if phys else True
    dlog("app.restore_default.done", anchors=anchors, verified=ok,
         vdd_skipped=sorted(vdd_anchors & set(anchors)))
    return None if ok else "布局已写回，但 2 秒内没读到确认，请「检测」看结果"


def is_vdd_row(row):
    """这一行是不是虚拟显示器 —— 名称显示与逐台按钮都按它判定。

    单独抽成纯函数是为了单测：VDD 当主屏时它是 index 0，绝不能套用
    mon_models[0] 那块物理屏的型号名（v2.01.12 修）。
    """
    return bool(row and row.get("vdd"))


_GENERIC_DESC = "generic pnp monitor"


def display_name_for_row(index, row, mon_models):
    """界面行的显示名：VDD 用 friendly，其余才用按 index 缓存的型号名。

    v2.01.21：DDC 读型号走 EDID，真机实测两块屏要 6.2 秒。缓存还没到位时
    先用 CCD 的 friendly（"R27qe Gen2"，0.1ms 就能拿到）顶上，
    别让用户盯着"显示器 0"等 6 秒。

    v2.01.22（走查 P-28）：DDC 的 `desc` 恒为 "Generic PnP Monitor"，
    它是驱动占位名、不是名字，别拿它当兜底显示。
    """
    if is_vdd_row(row):
        name = (row.get("friendly") or "").strip()
        if name.lower() == _GENERIC_DESC:
            name = ""
        return (name or "虚拟显示器"), True
    model = mon_models.get(index)
    if model:
        return model, False
    name = (row.get("friendly") or "").strip()
    if name.lower() == _GENERIC_DESC:
        name = ""
    return (name or "显示器 %d" % index), False


def resolve_monitors(cfg):
    """把"用户选过的显示器"解析成**当前**的下标列表。

    优先级（新 → 旧）：
      1. `monitor_anchors`：CCD 稳定锚点，跨 `DISPLAYn` 重排仍认得人；
      2. `monitor_ids`：旧的设备名锚点（DISPLAY1 这类），兼容旧配置；
      3. `monitors`：纯下标，最后兜底。

    任一层"没命中"都继续往下走，绝不静默把用户选择清空。
    返回 [] 表示"全部显示器"（与界面上"一个都不勾 = 全部"一致）。
    """
    anchors = cfg.get("monitor_anchors") or []
    ids = cfg.get("monitor_ids") or []
    if anchors or ids:
        targets = enumerate_targets()
        try:
            by_dev = {_short_dev(t["dev"]): t["index"] for t in targets}
        finally:
            release_targets(targets)      # 只借一瞬，别把句柄漏在这
        hit = set()
        if anchors:
            for r in ccd_probe_topology():
                if r["vdd"] or r["anchor"] not in anchors:
                    continue
                names = [_short_dev(r.get("gdi"))] + [
                    _short_dev(a) for a in r.get("aliases", [])]
                hit.update(by_dev[n] for n in names if n in by_dev)
        if not hit and ids:
            hit.update(by_dev[d] for d in (_short_dev(i) for i in ids) if d in by_dev)
        if hit:
            return sorted(hit)
    return normalize_indices(cfg.get("monitors"))


def selection_payload(indices, targets=None):
    """把界面勾选结果变成 (monitors, monitor_ids) 两份配置值。

    下标给人看、也兼容旧配置；设备名给下次启动兜底。两者一起存才是稳的。
    空选择 = 全部显示器，此时锚点也留空（否则"全部"会被锚死成当时那几台）。
    """
    idx = normalize_indices(indices)
    if not idx:
        return [], []
    owned = targets is None
    if owned:
        targets = enumerate_targets()
    try:
        by_index = {t["index"]: t["dev"] for t in targets}
        return idx, [by_index[i] for i in idx if i in by_index]
    finally:
        if owned:
            release_targets(targets)


def wake_options(cfg, probes=None):
    """唤醒策略的两个开关，CLI 与界面共用。

    `expect_off` 的意思是"这次点亮之前，屏幕可能真的是灭的"——它决定要不要
    动用「切信号源」「快速翻转」这两招（会给本来就亮着的屏白闪两秒）。

    v2.01.0：改成**当场探测**，不再读软件记账。判据只认可信方向：
      读到任一屏 0xD6=2/3/4/5  → 确实待机过 → True
      其余情况（含读不到）    → False，走保守路径
    依据：`0xD6` 只会假阴性（待机时仍报 1），不会假阳性，所以"报了待机"
    就是真的待机；"报了开"什么都不能证明。
    """
    if probes is None:
        try:
            probes = probe_states()
        except Exception:
            probes = []
    return {"allow_switch": bool(cfg.get("wake_switch_input", True)),
            "expect_off": any(p.get("state") == "off" for p in (probes or []))}


def apply_schedule(cfg=None):
    """按当前时间套用对应档位（亮度 + 该档的屏幕动作）。

    返回 (level, screen, ok, errs)；screen 为该档屏幕动作（""/off/on）。
    屏幕动作失败的错误以序号 -1 记入 errs（界面按原文显示）。

    v1.9.4：恢复 v1.8.5 的语义（不再写 last_fired、不再从 GUI 启动自检调用）。
    v2.01.0：不再把"屏幕现在是什么状态"记进配置——定时任务只负责"到点把
    这几个动作做掉"，是纯粹的一次性执行，不维护状态。
    本函数只由计划任务（--apply-schedule）与用户显式操作触发。
    """
    cfg = cfg or load_config()
    # v2.02.0：按星期过滤档位。`days` 空 = 每天（旧配置行为不变）。
    # 触发器层已经按星期注册，这里再判一次是防"登录校准"或残留任务
    # 在非指定星期被唤起——那两条路径不经过 Weekly 触发器。
    _wd = datetime.now().isoweekday()
    _slots = slots_for_weekday(cfg.get("slots", []), _wd)
    slot = active_slot_dict(_slots, _minutes())
    level = slot.get("level", 50)
    screen = slot.get("screen", "")
    targets = resolve_monitors(cfg)
    dlog("sch.decide", slot=slot, level=level, screen=screen, targets=targets,
         method=cfg.get("screen_off_method"), weekday=_wd,
         slot_days=slot.get("days") or [])
    # D3：拓扑模式的 off 跳过亮度（inactive 时 DDC 读不到/写不进）；
    # on 的亮度由 screen_on_cfg 在「按当前拓扑接回 → 回读」之后写。
    topo = cfg.get("screen_off_method") == "topology"
    if topo and screen in ("off", "on"):
        ok, errs = [], []
    else:
        ok, errs = set_brightness_many(targets, level)
    if screen == "off":
        sok, serrs = screen_off_cfg(cfg, targets)
    elif screen == "on":
        sok, serrs = screen_wake_entry(cfg, targets)
    else:
        sok, serrs = [], []
    ok = ok + sok
    errs = errs + [(-1, "屏幕 %d: %s" % (i, e)) for i, e in serrs]
    dlog("sch.result", level=level, screen=screen, ok=ok, errs=errs)
    # v2.01.0：不再记账屏幕状态。程序只负责"到点把这几个动作做掉"，
    # 做完就结束；屏幕现在是什么状态，下次需要时现场读（screen_verdict）。
    return level, screen, ok, errs


# ============================================================
# 四、任务计划（PowerShell Register-ScheduledTask）
#    ⚠️ 关键：schtasks.exe 命令行**没有**「错过就尽快补跑」的开关，
#       必须走 PowerShell 的 -StartWhenAvailable，
#       否则电脑关机 / 睡眠错过时间点后任务不会补跑（v1.0.0 的定时 bug 根因）。
# ============================================================
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _ps(script, timeout=90):
    """执行一段 PowerShell，返回 (returncode, 输出文本)。"""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(exe):
        exe = "powershell"
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )
    out = proc.stdout.decode("mbcs", "replace") if proc.stdout else ""
    return proc.returncode, out.strip()


def _ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def _ps_script(lines):
    """把 PowerShell 语句拼成一段脚本。

    ⚠️ 必须用**换行**拼接，绝不能用 '; '：
    `foreach (...) { ... ; else { ... } }` 这种把分号插在 `}` 和 `else` 之间的写法
    在 PowerShell 里是**语法错误**（实测报「无法将"else"项识别为 cmdlet」），
    整段解析失败 → 退出码非 0 → 界面显示「任务计划状态：读取失败」。
    """
    return "\n".join(lines)


def task_target():
    """返回 (要执行的程序, 需要前置的参数)。

    打包成 exe 时 sys.executable 就是本程序；
    直接从源码跑时 sys.executable 是 python 解释器，必须把脚本路径带上
    —— v1.0.0 漏了这一步，源码模式下注册出来的任务根本跑不起来。
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    script = os.path.abspath(__file__)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    runner = pyw if os.path.exists(pyw) else sys.executable
    return runner, '"%s"' % script


def _task_action(cfg):
    """当前进程应写进任务的 (程序路径, 参数)。"""
    exe, prefix = task_target()
    return exe, (prefix + " --apply-schedule").strip()


def task_signature(cfg):
    """任务"指纹"：程序路径 + 参数 + 全部触发时间。用于启动自检与日志。"""
    exe, arg = _task_action(cfg)
    times = ",".join(str(s.get("time", "")) for s in cfg.get("slots", []))
    return "%s|%s|%s" % (os.path.normcase(os.path.abspath(exe)), arg, times)


def _ps_trigger_line(slot):
    """一档 → 一条 PowerShell 触发器语句。

    v2.02.0：`days` 非空且不是全 7 天时用 `-Weekly`（任务计划程序里能直接看到
    "只在周一、三、五"）；空 = 每天，仍用 `-Daily`，与旧版注册结果完全一致。
    """
    days = normalize_days(slot.get("days"))
    at = _ps_quote(slot["time"])
    if not days:
        return "  (New-ScheduledTaskTrigger -Daily -At %s)" % at
    names = ",".join(WEEKDAY_NAMES_PS[d - 1] for d in days)
    return ("  (New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 "
            "-DaysOfWeek %s -At %s)" % (names, at))


def build_register_script(cfg):
    """生成注册脚本：一条定时任务挂 N 个触发器（每档一个）、可选登录补跑任务。

    v1.9.0 起：任务已存在时优先 Set-ScheduledTask **原地更新**（保留上次运行记录），
    失败才回退到 Unregister + Register。旧实现每次都先删后建，会把 LastRunTime
    重置成"从未运行"，也让用户误以为定时没生效。
    """
    exe, arg = _task_action(cfg)
    slots = cfg.get("slots", [])
    lines = [
        "$ErrorActionPreference = 'Stop'",
        # -AtLogOn 必须带 -User，否则会被当成"任意用户登录"、非管理员注册直接拒绝访问
        "$me = $env:USERNAME; if (-not $me) "
        "{ $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name }",
        "$action = New-ScheduledTaskAction -Execute %s -Argument %s" % (_ps_quote(exe), _ps_quote(arg)),
        # StartWhenAvailable = 错过计划时间（关机/睡眠）后尽快补跑；schtasks.exe 无此开关
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)",
        # 只清历史遗留任务名；当前任务不再先删后建
        "foreach ($n in @(%s)) { Unregister-ScheduledTask -TaskName $n "
        "-Confirm:$false -ErrorAction SilentlyContinue }"
        % ", ".join(_ps_quote(x) for x in _LEGACY_TASKS),
        "$triggers = @(",
    ]
    for i, x in enumerate(slots):
        lines.append(_ps_trigger_line(x))
    lines += [
        ")",
        "$old = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % TASK_SCHEDULE,
        "if ($old) {",
        "  try {",
        "    Set-ScheduledTask -TaskName '%s' -Action $action -Trigger $triggers "
        "-Settings $settings -Description %s -ErrorAction Stop | Out-Null"
        % (TASK_SCHEDULE, _ps_quote("MonitorBrightness · %d 个时间段（到点切换亮度）" % len(slots))),
        "    Write-Output 'REBUILT=UPDATED'",
        "  } catch {",
        "    Unregister-ScheduledTask -TaskName '%s' -Confirm:$false" % TASK_SCHEDULE,
        "    Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $triggers "
        "-Settings $settings -Description %s | Out-Null"
        % (TASK_SCHEDULE, _ps_quote("MonitorBrightness · %d 个时间段（到点切换亮度）" % len(slots))),
        "    Write-Output ('REBUILT=FALLBACK ' + $_.Exception.Message)",
        "  }",
        "} else {",
        "  Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $triggers "
        "-Settings $settings -Description %s | Out-Null"
        % (TASK_SCHEDULE, _ps_quote("MonitorBrightness · %d 个时间段（到点切换亮度）" % len(slots))),
        "  Write-Output 'REBUILT=NEW'",
        "}",
    ]
    if cfg.get("catch_up_on_logon"):
        # 单独 try/catch：这条失败也不能拖垮定时任务，失败原因要能带回界面
        lines += [
            "$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $me",
            "$tLogon.Delay = 'PT30S'",
            "$g = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % TASK_CATCHUP,
            "try {",
            "  if ($g) {",
            "    Set-ScheduledTask -TaskName '%s' -Action $action -Trigger $tLogon -Settings $settings "
            "-Description 'MonitorBrightness: 登录后按当前时间校准亮度（补上错过的切换点）' "
            "-ErrorAction Stop | Out-Null" % TASK_CATCHUP,
            "  } else {",
            "    Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $tLogon -Settings $settings "
            "-Description 'MonitorBrightness: 登录后按当前时间校准亮度（补上错过的切换点）' "
            "-ErrorAction Stop | Out-Null" % TASK_CATCHUP,
            "  }",
            "  Write-Output 'CATCHUP=OK'",
            "} catch { Write-Output ('CATCHUP=FAIL ' + $_.Exception.Message) }",
        ]
    else:
        lines.append("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false "
                     "-ErrorAction SilentlyContinue" % TASK_CATCHUP)
    lines += [
        "$t = Get-ScheduledTask -TaskName '%s'" % TASK_SCHEDULE,
        "Write-Output ('StartWhenAvailable=' + $t.Settings.StartWhenAvailable)",
        "Write-Output ('Triggers=' + $t.Triggers.Count)",
        "$info = Get-ScheduledTaskInfo -TaskName '%s'" % TASK_SCHEDULE,
        "if ($info.LastRunTime.Year -lt 2000) { $lr = 'NEVER' } "
        "else { $lr = $info.LastRunTime.ToString('yyyy-MM-dd HH:mm') }",
        "if ($info.NextRunTime) { $nx = $info.NextRunTime.ToString('yyyy-MM-dd HH:mm') } "
        "else { $nx = 'NONE' }",
        "Write-Output ('TaskInfo=' + $lr + '|' + [int64]$info.LastTaskResult + '|' + $nx)",
        "$g2 = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % TASK_CATCHUP,
        "if ($g2) { Write-Output ('CatchUpTask=' + $g2.State) } else { Write-Output 'CatchUpTask=MISSING' }",
        "Write-Output 'REGISTER_OK'",
    ]
    return _ps_script(lines)


def register_tasks(cfg):
    """注册/更新定时任务。返回 (是否成功, 说明文本)。"""
    code, out = _ps(build_register_script(cfg))
    if code == 0 and "REGISTER_OK" in out:
        return True, out
    return False, out or ("注册子进程退出码 %s" % code)


# ------------------------------------------------------------
# 任务定义校验：启动时静默自愈
#   v1.8.5 之前只有用户手动点「应用」才会重建任务。exe 换目录/改名（每版构建目录
#   dist_vXXX 都在变）后，任务 action 仍指旧路径：任务"跑了"，但找不到程序，
#   LastTaskResult 0x80070002，用户侧就是"今天没有准时执行"。
#   现在每次启动读一次落盘的任务 XML，路径/参数/触发点/设置任一项和当前进程不一致
#   就静默重建，并写一条事件日志（界面可见）。
# ------------------------------------------------------------
TASKS_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Tasks")


def _read_task_xml(name=TASK_SCHEDULE):
    """读任务计划落盘的 XML；读不到返回 ""。"""
    path = os.path.join(TASKS_DIR, name)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return ""
    return (raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
            else raw.decode("utf-8", "replace"))


def _task_definition(xml_text):
    """从任务 XML 取 (Command, Arguments)。"""
    m = re.search(r"<Actions[^>]*>(.*?)</Actions>", xml_text or "", re.S)
    block = m.group(1) if m else ""
    cm = re.search(r"<Command>(.*?)</Command>", block, re.S)
    am = re.search(r"<Arguments>(.*?)</Arguments>", block, re.S)
    return (html.unescape(cm.group(1).strip()) if cm else "",
            html.unescape(am.group(1).strip()) if am else "")


def _same_path(a, b):
    try:
        return os.path.normcase(os.path.abspath(str(a).strip().strip('"'))) == \
            os.path.normcase(os.path.abspath(str(b).strip().strip('"')))
    except Exception:
        return str(a).strip().strip('"').lower() == str(b).strip().strip('"').lower()


def _slot_trigger_fingerprint(slots):
    """配置侧触发器指纹：[(当日分钟, 星期frozenset)]，全 7 天归成空集。"""
    out = []
    for s in slots or []:
        days = frozenset(normalize_days(s.get("days")))
        out.append((parse_hhmm(s.get("time"), (0, 0)), days))
    return sorted(out)


def _xml_trigger_fingerprint(xml_text):
    """任务 XML 侧触发器指纹。

    一个 `<CalendarTrigger>` 里若有 `<DaysOfWeek>` 就是 Weekly，否则是 Daily
    （空集）。选满 7 天与"每天"等价，统一归成空集，避免每次启动都误判要重建。
    """
    out = []
    for block in re.finditer(r"<CalendarTrigger>(.*?)</CalendarTrigger>",
                             xml_text or "", re.S):
        body = block.group(1)
        m = re.search(r"<StartBoundary>([^<]+)</StartBoundary>", body)
        if not m:
            continue
        mins = _boundary_to_minutes(m.group(1).strip())
        if mins is None:
            continue
        days = set()
        dw = re.search(r"<DaysOfWeek>(.*?)</DaysOfWeek>", body, re.S)
        if dw:
            for i, name in enumerate(WEEKDAY_NAMES_PS, start=1):
                if re.search(r"<%s\s*/>" % name, dw.group(1)):
                    days.add(i)
        out.append((mins, frozenset() if len(days) >= 7 else frozenset(days)))
    return sorted(out)


def _fp_text(fp):
    """指纹 → 人话，只用于不一致时的事件/状态文案。"""
    if not fp:
        return "无触发器"
    parts = []
    for mins, days in fp:
        d = ("每天" if not days
             else "周" + ",".join(WEEKDAY_LABELS[n - 1] for n in sorted(days)))
        parts.append("%02d:%02d(%s)" % (mins // 60, mins % 60, d))
    return " ".join(parts)


def task_match_reason(cfg, xml_text=None):
    """任务是否与当前进程/配置一致；一致返回空串，否则返回人话原因。"""
    if xml_text is None:
        xml_text = _read_task_xml()
    if not xml_text:
        return "任务未注册"
    exe, arg = _task_action(cfg)
    command, arguments = _task_definition(xml_text)
    if not command:
        return "任务定义里没有 action"
    if not _same_path(command, exe):
        return "程序路径不一致（任务：%s，当前：%s）" % (command, exe)
    if arguments.strip() != arg.strip():
        return "启动参数不一致（任务：%s，当前：%s）" % (arguments, arg)
    block = _settings_block(xml_text)
    if _xml_flag(block, "Enabled") is False:
        return "任务已被禁用"
    if _xml_flag(block, "StartWhenAvailable") is False:
        return "未开启「错过补跑」(StartWhenAvailable)"
    bounds = sorted(set(_boundary_minutes_all(xml_text)))
    wanted = sorted({parse_hhmm(s.get("time"), (0, 0)) for s in cfg.get("slots", [])})
    if bounds != wanted:
        have = ",".join("%02d:%02d" % (m // 60, m % 60) for m in bounds) or "无"
        want = ",".join("%02d:%02d" % (m // 60, m % 60) for m in wanted) or "无"
        return "触发时间不一致（任务：%s，配置：%s）" % (have, want)
    # v2.02.0：星期也要比对，否则用户改了「只在工作日」不会重建任务
    have_fp = _xml_trigger_fingerprint(xml_text)
    want_fp = _slot_trigger_fingerprint(cfg.get("slots", []))
    if have_fp != want_fp:
        return "触发星期不一致（任务：%s，配置：%s）" % (
            _fp_text(have_fp), _fp_text(want_fp))
    return ""


def ensure_task_registration(cfg, force=False):
    """让"计划任务"与"用户当前配置"保持一致。返回 (是否可用, 说明)。

    ⚠️ v2.01.0 修走查 E2-04：原来 `enabled=False` 时直接 return，
    连 force=True（`--force`）也被吞掉——用户显式要求"不管怎样对齐一次"，
    程序却因为记住的开关而什么都不做。现在 force 的语义是"对齐"，两个方向都做：
      enabled=True  → 任务不一致（或 force）就重建
      enabled=False → force 时**反向对齐**：把残留任务清掉
    在后台线程调用，不阻塞窗口显示；结果写 events.log。
    """
    if not cfg.get("enabled"):
        if not force:
            return True, "定时未启用，跳过任务校验"
        ok, out = unregister_tasks()
        event = ("定时未启用：残余计划任务已清理" if ok
                 else "定时未启用，但残余任务清理失败：%s"
                      % ((out or "").strip().splitlines() or ["未知原因"])[-1])
        _write_event_log(event)
        return ok, event
    reason = task_match_reason(cfg)
    if not reason and not force:
        return True, ""
    ok, out = register_tasks(cfg)
    if ok:
        event = "计划任务已按当前程序重建（%s）" % (reason or "手动强制")
        nxt = _task_info_next(out)
        if nxt:
            # 启动自检不弹窗（每次开机都弹会糊脸），但事件日志要给足细节
            event += "，%d 档，下次 %s" % (len(cfg.get("slots", [])), _short_dt(nxt))
        _write_event_log(event)
        return True, event
    detail = (out or "").strip().splitlines()
    event = "计划任务重建失败：%s" % (detail[-1] if detail else "未知原因")
    _write_event_log(event)
    return False, event


def build_unregister_script():
    return _ps_script([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "foreach ($n in @(%s)) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }"
        % ", ".join(_ps_quote(x) for x in (TASK_SCHEDULE, TASK_CATCHUP) + _LEGACY_TASKS),
        "Write-Output 'UNREGISTER_OK'",
    ])


def unregister_tasks():
    code, out = _ps(build_unregister_script())
    return code == 0 and "UNREGISTER_OK" in out, out


def build_query_script():
    """生成「查询三条任务状态」的脚本（抽成函数是为了能被自测覆盖）。

    注意：这是**兜底路径**。正常走 query_task_status_local() 直接读盘上的任务 XML，
    只有读不出来（权限等）才会启动 PowerShell —— 实测冷启动要 ~2.7 秒。
    """
    return _ps_script([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "foreach ($n in @('%s','%s')) {" % (TASK_SCHEDULE, TASK_CATCHUP),
        "  $t = Get-ScheduledTask -TaskName $n",
        "  if ($t) {",
        "    $i = Get-ScheduledTaskInfo -TaskName $n",
        "    Write-Output ($n + '=' + $t.State + '|' + $i.NextRunTime + '|SWA=' + $t.Settings.StartWhenAvailable)",
        "  } else {",
        "    Write-Output ($n + '=MISSING')",
        "  }",
        "}",
    ])


# 任务计划把每个任务的定义落盘成 XML：<SystemRoot>\System32\Tasks\<任务名>


def _xml_flag(block, tag):
    """从 XML 片段里取布尔标记；取不到返回 None。"""
    m = re.search(r"<%s>(true|false)</%s>" % (tag, tag), block or "", re.I)
    return (m.group(1).lower() == "true") if m else None


def _settings_block(xml_text):
    """只取 <Settings>…</Settings>：<Enabled> 在触发器里也有，必须限定作用域。"""
    m = re.search(r"<Settings>(.*?)</Settings>", xml_text or "", re.S)
    return m.group(1) if m else ""


def _boundary_to_minutes(b):
    """'2026-11-27T08:30:00' → 510（当日分钟）；解析失败返回 None。"""
    b = (b or "").strip()
    parts = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", b)
    if not parts:
        return None
    y, mo, d, h, mi, sec = (int(v) for v in parts.groups())
    try:
        dt = (datetime(y, mo, d, h, mi, sec, tzinfo=timezone.utc).astimezone()
              if b.endswith("Z") else datetime(y, mo, d, h, mi, sec))
    except Exception:
        return None
    return dt.hour * 60 + dt.minute


def _boundary_minutes_all(xml_text):
    """任务 XML 里全部触发器的 StartBoundary（本地当日分钟列表）。"""
    out = []
    for m in re.finditer(r"<StartBoundary>([^<]+)</StartBoundary>", xml_text or ""):
        mins = _boundary_to_minutes(m.group(1))
        if mins is not None:
            out.append(mins)
    return out


def _next_run_text(minutes_list, now=None, days_by_min=None):
    """多触发器取「离现在最近的下一次」。

    v2.02.0：`days_by_min` 可选，{当日分钟: 星期集合}。给了就按真实星期推算
    （周一档在周五要显示"周一 08:30"，不能再说"明天"）；空集合 = 每天，
    行为与旧版完全一致。
    """
    if not minutes_list:
        return ""
    now_dt = now if now is not None else datetime.now()
    today = now_dt.isoweekday()
    now_min = now_dt.hour * 60 + now_dt.minute
    best = None                      # (距今天数, 当日分钟)
    for m in minutes_list:
        days = frozenset((days_by_min or {}).get(m) or ())
        for delta in range(0, 8):    # 今天起 7 天内必有至少一次
            wd = (today - 1 + delta) % 7 + 1
            if days and wd not in days:
                continue
            if delta == 0 and m <= now_min:
                continue
            cand = (delta, m)
            if best is None or cand < best:
                best = cand
            break
    if best is None:
        return ""
    delta, m = best
    if delta == 0:
        day = "今天"
    elif delta == 1:
        day = "明天"
    else:
        day = "周" + WEEKDAY_LABELS[(today - 1 + delta) % 7]
    return "%s %02d:%02d" % (day, m // 60, m % 60)


def _days_by_min_from_fp(fingerprint):
    """[(分钟, 星期frozenset)] → {分钟: 星期集合}；"每天"给空集合。"""
    out = {}
    for mins, days in fingerprint or []:
        prev = out.get(mins)
        if prev is None:
            out[mins] = set(days)
        else:
            prev |= set(days)
            if not prev:
                out[mins] = set()
    return {k: frozenset(v) for k, v in out.items()}


def query_task_status_local():
    """直接读任务计划落盘的 XML 得到任务状态（实测 ~1ms，不启动子进程）。

    返回格式与 query_task_status() 一致；若任务文件**存在但读不出来**（权限等），
    返回 None 表示"请走兜底"，不要误报成 MISSING。
    """
    out = []
    for name in (TASK_SCHEDULE, TASK_CATCHUP):
        path = os.path.join(TASKS_DIR, name)
        if not os.path.exists(path):
            out.append(name + "=MISSING")
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception:
            return None
        txt = (raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
               else raw.decode("utf-8", "replace"))
        block = _settings_block(txt)
        state = "Disabled" if _xml_flag(block, "Enabled") is False else "Ready"
        swa = _xml_flag(block, "StartWhenAvailable")
        if name == TASK_CATCHUP:
            out.append("%s=%s||SWA=%s" % (name, state, swa))
        else:
            bounds = _boundary_minutes_all(txt)
            nxt = _next_run_text(
                bounds, days_by_min=_days_by_min_from_fp(
                    _xml_trigger_fingerprint(txt)))
            out.append("%s=%s|%s|SWA=%s|TRIG=%s"
                       % (name, state, nxt, swa,
                          ",".join(str(m) for m in sorted(bounds))))
    return "\n".join(out)


def query_task_status():
    """查询三条例行任务的状态/下次运行时间，用于界面展示。

    输出每行形如 `任务名=State|NextRunTime|SWA=True`；任务不存在则为 `任务名=MISSING`。

    快路径：读本地任务 XML（实测 ~1ms）。
    兜底：启动 PowerShell 查询（实测 ~2.7s 冷启动），只在 XML 读不出来时走。
    """
    local = query_task_status_local()
    if local is not None:
        return local
    code, out = _ps(build_query_script())
    return out if code == 0 else ""


def _as_ok_errs(result):
    """把 run_bg 的结果统一成 (成功列表, 失败列表)。

    job 抛异常时 run_bg 把异常对象原样传回，这里转成一条 (-1, 原因) 失败项，
    免得每个 on_done 都要写一遍 isinstance 判断。
    """
    if isinstance(result, Exception):
        return [], [(-1, str(result))]
    return result


def build_runtime_query_script():
    """查询"上次运行时间 / 上次结果 / 下次运行时间"——任务计划 API 才拿得到。

    XML 里只有定义，没有 LastRunTime/LastTaskResult，所以要单独一次 PowerShell。
    界面在后台线程调用，2~3 秒，不影响窗口显示。
    """
    return _ps_script([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "$i = Get-ScheduledTaskInfo -TaskName '%s'" % TASK_SCHEDULE,
        "$t = Get-ScheduledTask -TaskName '%s'" % TASK_SCHEDULE,
        "if (-not $i) { Write-Output 'RUNTIME=MISSING'; exit 0 }",
        "if ($i.LastRunTime.Year -lt 2000) { $lr = 'NEVER' } "
        "else { $lr = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') }",
        "if ($i.NextRunTime) { $nx = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') } "
        "else { $nx = 'NONE' }",
        "Write-Output ('RUNTIME=' + $lr + '|' + [int64]$i.LastTaskResult + '|' + $nx "
        "+ '|' + $t.State)",
    ])


def query_task_runtime():
    """返回一行 `RUNTIME=上次运行|上次结果|下次运行|状态`；失败返回 ""。"""
    code, out = _ps(build_runtime_query_script())
    return out if code == 0 else ""


def _task_result_text(code):
    """任务结果码 → 人话。0x41303 = 任务从未运行过（Windows 的哨兵值）。"""
    try:
        n = int(str(code).strip())
    except Exception:
        return str(code)
    if n == 0:
        return "成功"
    if n == 0x41303:
        return "从未运行"
    if n == 0x41301:
        return "正在运行"
    if n == 0x41302:
        return "被用户终止"
    return "0x%X" % (n & 0xFFFFFFFF)


def _runtime_fields(text):
    """从 RUNTIME= 行解析 (上次运行, 上次结果, 下次运行, 状态)。"""
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("RUNTIME="):
            bits = line[8:].split("|")
            return (bits + ["", "", "", ""])[:4]
    return ("", "", "", "")


def _task_info_next(out):
    """从 register_tasks 的 TaskInfo= 行取下次运行时间；没有返回 ""。"""
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("TaskInfo="):
            bits = line[9:].split("|")
            nxt = bits[2].strip() if len(bits) > 2 else ""
            return "" if nxt in ("NONE", "MISSING", "NEVER") else nxt
    return ""


def _rebuild_changes(before_xml, cfg):
    """重建前后任务定义差异：[] / ["任务路径"] / ["触发点"] / 两者。"""
    if not before_xml:
        return ["任务路径", "触发点"]         # 原本没有任务，路径+触发点都是新写的
    command, arguments = _task_definition(before_xml)
    exe, arg = _task_action(cfg)
    changed = []
    if not _same_path(command, exe) or arguments.strip() != arg.strip():
        changed.append("任务路径")
    old_bounds = sorted(set(_boundary_minutes_all(before_xml)))
    new_bounds = sorted({parse_hhmm(s.get("time"), (0, 0))
                         for s in cfg.get("slots", [])})
    if old_bounds != new_bounds:
        changed.append("触发点")
    return changed


def _rebuild_report(cfg, out, before_xml):
    """重建成功后的确认正文：档位数量、下次运行、改了什么。"""
    slots = sorted(cfg.get("slots", []),
                   key=lambda s: parse_hhmm(s.get("time"), (0, 0)))
    times = [s.get("time") for s in slots]
    lines = ["任务计划已重建成功。", "",
             "• 触发档位：%d 档" % len(slots)]
    if times:
        # 用空格分隔，Tk Label 只在空格处换行；顿号连写会整行被裁掉
        lines.append("   " + "  ".join(times))
    nxt = _task_info_next(out)
    if not nxt:
        minutes = sorted({parse_hhmm(t, (0, 0)) for t in times})
        if minutes:
            nxt = _next_run_text(
                minutes, days_by_min=_days_by_min_from_fp(
                    _slot_trigger_fingerprint(slots)))
    lines.append("• 下次运行：%s" % (_short_dt(nxt) if nxt
                                    else "未取到（点「检测」刷新）"))
    changed = _rebuild_changes(before_xml, cfg)
    if changed:
        lines.append("• 已更新%s" % "/".join(changed))
    if cfg.get("catch_up_on_logon", True):
        lines.append("• 登录后自动校准：%s"
                     % ("注册失败" if "CATCHUP=FAIL" in (out or "") else "已注册"))
    else:
        lines.append("• 登录后自动校准：未开启")
    return "\n".join(lines)


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
# 六、命令行模式（供任务计划静默调用）
# ============================================================
def _out(msg=""):
    """打印到 stdout。--windowed 打包后若没有控制台，sys.stdout 可能是 None。"""
    try:
        if sys.stdout is not None:
            sys.stdout.write(str(msg) + "\n")
            sys.stdout.flush()
    except Exception:
        pass


def _err(msg):
    try:
        if sys.stderr is not None:
            sys.stderr.write(str(msg) + "\n")
            sys.stderr.flush()
    except Exception:
        pass


def parse_level_arg(text):
    """严格解析命令行里的亮度：必须是 0-100 的整数，非法返回 None。

    ⚠️ 不能复用 clamp_level —— 它会把 'abc' 静默变成 0，
    那样任务计划里参数写错就会把屏幕直接调黑。
    """
    try:
        v = int(str(text).strip())
    except Exception:
        return None
    return v if 0 <= v <= 100 else None


def parse_index_arg(spec):
    """解析命令行里的显示器序号：'all' → []（全部）；'0,1' → [0, 1]；非法 → None。

    同样不能退化成"全部"：写错序号就去动所有显示器是危险的。
    """
    s = str(spec).strip().lower()
    if s in ("all", "*"):
        return []
    parts = [p for p in s.replace(" ", "").split(",") if p != ""]
    if not parts:
        return None
    out = []
    for p in parts:
        try:
            i = int(p)
        except Exception:
            return None
        if i < 0:
            return None
        if i not in out:
            out.append(i)
    return sorted(out)


def cli_set(spec, value):
    level = parse_level_arg(value)
    if level is None:
        _err("亮度必须是 0-100 的整数，收到：%r" % (value,))
        sys.exit(2)
    indices = parse_index_arg(spec)
    if indices is None:
        _err("显示器序号不合法，应为 all 或 0,1 这样的写法，收到：%r" % (spec,))
        sys.exit(2)
    try:
        ok, errs = set_brightness_many(indices, level)
        for i, e in errs:
            _err("显示器 %s: %s" % (i, e))
        sys.exit(0 if ok else 1)
    except Exception as e:
        _err(e)
        sys.exit(1)


def cli_apply_schedule():
    # 走查 P-02 补充：注销任务失败时任务可能残留，但配置已 enabled=False。
    # 到这里必须再确认一次用户意图，否则"已关闭定时"仍会熄屏。
    try:
        cfg = load_config()
        if not cfg.get("enabled"):
            _out("定时已关闭（enabled=false），本次不执行档位")
            sys.exit(0)
    except Exception:
        pass                      # 读不到配置时照旧执行，避免升级为阻断
    try:
        level, screen, ok, errs = apply_schedule()
        for i, e in errs:
            _err("显示器 %s: %s" % (i, e))
        if screen:
            _out("屏幕动作: %s" % screen)
        for line in take_wake_info():
            _out(line)
        sys.exit(0 if ok else 1)
    except Exception as e:
        _err(e)
        sys.exit(1)


def cli_list():
    """列出显示器（含设备名 / 分辨率 / 主副屏 / 当前输入源）。"""
    targets = enumerate_targets()
    if not targets:
        _err(NO_MONITOR_MSG)
        sys.exit(1)
    try:
        for t in targets:
            line = monitor_label(t["index"], (t["dev"], t["w"], t["h"], t["primary"]))
            src = _read_vcp(t["handle"], VCP_INPUT_SOURCE)
            if src:
                line += " · %s" % INPUT_LABEL.get(src[0], "输入源 %d" % src[0])
            _out(line)
    finally:
        release_targets(targets)
    sys.exit(0)


def cli_probe():
    """打印"亮还是灭"的结论 + 每台的回读证据（只读 DDC，不写任何寄存器）。"""
    cfg = load_config()
    try:
        states = probe_states()
    except Exception as e:
        _err(e)
        sys.exit(1)
    v = screen_verdict(cfg, states)
    _out(v["text"])
    for s in states:
        _out("  显示器 %d（%s）：回读 %s — %s"
             % (s["index"], s["dev"],
                {"on": "亮", "off": "灭"}.get(s["state"], "未知"), s["why"]))
    if states:
        _out("  说明：0xD6 只会假阴性（待机时仍报 1），所以「报灭」可信、"
             "「报亮」不能证明面板亮着；程序不再记账，读不到就说不知道")
    if v.get("detail"):
        _out("  " + v["detail"])
    sys.exit(0)


def cli_diag():
    """打印一份只读诊断报告（给"另一台电脑上点不亮"取证用）。

    刻意把"同一个物理屏被枚举成几个输出""现在每个输出的输入源是什么"
    都打出来：DP+HDMI 双线接法下，这两项直接决定"点亮"会不会切到空口。
    """
    _out("MonitorBrightness v%s 诊断报告" % APP_VERSION)
    _out("Python %s · 打包态=%s · 配置=%s"
         % (sys.version.split()[0], bool(getattr(sys, "frozen", False)), CONFIG_FILE))
    cfg = load_config()
    _out("配置：presets=%s · 熄灭方式=%s（屏幕亮灭每次现读，不记账）"
         % (cfg.get("presets"), cfg.get("screen_off_method")))
    _out("配置里的显示器选择：monitors=%s · monitor_ids=%s · monitor_anchors=%s"
         % (cfg.get("monitors"), cfg.get("monitor_ids"), cfg.get("monitor_anchors")))
    _out("接回位置记录：%s" % (cfg.get("monitor_positions") or "{}"))
    _out("锚点优先解析结果：%s" % (resolve_monitors(cfg) or "[]（= 全部）"))
    _out("日志目录：%s（%s）" % (LOG_DIR, "程序同目录" if LOG_DIR == APP_DIR else "回退到 APPDATA"))
    _out("诊断日志：%s（%s，用 --diag-log 看内容）"
         % (_diag_file(), "开启" if diag_enabled() else "已关闭"))

    targets = enumerate_targets()
    if not targets:
        _out("")
        _out(NO_MONITOR_MSG)
        sys.exit(1)
    try:
        _out("")
        _out("一、枚举结果（共 %d 个输出，即界面上的行数）" % len(targets))
        for t in targets:
            pow_ = _read_vcp(t["handle"], VCP_POWER_MODE)
            src = _read_vcp(t["handle"], VCP_INPUT_SOURCE)
            lum = _read_vcp(t["handle"], VCP_BRIGHTNESS)
            caps = _capabilities(t["handle"])
            _out("  [%d] %s  %d×%d  %s  desc=%r"
                 % (t["index"], t["dev"], t["w"], t["h"],
                    "主屏" if t["primary"] else "副屏", t["desc"]))
            _out("      电源 0xD6=%s · 输入源 0x60=%s · 亮度 0x10=%s"
                 % (pow_[0] if pow_ else "不可读", src[0] if src else "不可读",
                    lum[0] if lum else "不可读"))
            _out("      型号=%r · 支持输入源=%s · 支持电源值 0xD6=%s"
                 % (_caps_model(caps), _caps_inputs(caps) or "读不到",
                    _caps_power(caps) or "读不到"))

        _out("")
        _out("二、程序实际会操作的序号（按设备名锚点解析）")
        _out("  resolve_monitors() -> %s" % (resolve_monitors(cfg) or "[]（= 全部）"))

        _out("")
        _out("三、亮灭判定")
        for s in probe_states():
            _out("  [%d] %s -> %s（%s）"
                 % (s["index"], s["dev"],
                    {"on": "亮", "off": "灭"}.get(s["state"], "未知"), s["why"]))

        _out("")
        _out("四、显示拓扑（只读，含被断开的屏）")
        ccd_rows = ccd_probe_topology()
        if not ccd_rows:
            _out("  未取到 CCD 拓扑（非控制台会话 / 驱动不支持？）")
        for r in ccd_rows:
            extra = [a for a in r.get("aliases", []) if a != r["gdi"]]
            _out("  %s %s  %s  锚点=%s  gdi=%s%s" %
                 ("ACTIVE " if r["active"] else "inactive",
                  "虚拟屏" if r["vdd"] else "物理屏",
                  r["friendly"] or "(无友好名)", r["anchor"], r["gdi"] or "-",
                  "  (同源: %s)" % ",".join(extra) if extra else ""))
        _out("  折叠状态：%s" % topology_state_for(cfg, ccd_rows)["detail"])

        _out("")
        _out("五、定时任务")
        for line in (query_task_status() or "").splitlines():
            _out("  " + line)
        for line in (query_task_runtime() or "").splitlines():
            _out("  " + line)
        reason = task_match_reason(cfg)
        _out("  任务定义与当前进程：%s" % (reason or "一致"))
        _out("  当前进程应写：%s | %s" % _task_action(cfg))
    finally:
        release_targets(targets)
    sys.exit(0)


def cli_ensure_schedule():
    """按当前配置/当前 exe 重建计划任务（供发版部署后无界面自愈；也可排障用）。"""
    cfg = load_config()
    _out("配置：enabled=%s · slots=%s"
         % (cfg.get("enabled"), [s.get("time") for s in cfg.get("slots", [])]))
    ok, msg = ensure_task_registration(cfg, force=("--force" in sys.argv))
    _out(msg or "任务定义与当前程序一致，无需重建")
    if cfg.get("enabled"):
        for line in (query_task_runtime() or "").splitlines():
            _out("  " + line)
    sys.exit(0 if ok else 1)


def cli_selftest():
    """打印 N 档调度自检表（不触碰显示器）。"""
    cfg = load_config()
    slots = _slot_minutes(cfg)
    _out("配置：%s" % " / ".join("%s->%s%%" % (x["time"], x["level"])
                                 for x in cfg.get("slots", [])))
    _out("当前档：%s%%" % active_slot(slots, _minutes())[1])
    for hh, mm in [(0, 0), (8, 28), (8, 30), (8, 32), (12, 0),
                   (20, 58), (21, 0), (21, 2), (23, 59)]:
        t = hh * 60 + mm
        _out("  %02d:%02d -> %s%%" % (hh, mm, active_slot(slots, t)[1]))
    sys.exit(0)


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


def _raster_line(x0, y0, x1, y1, thick=2):
    """把一条线段栅格化成像素集合（用于画对勾，避免手工列像素）。"""
    pts = set()
    steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * 3))
    for i in range(steps + 1):
        t = i / float(steps)
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        for dx in range(thick):
            for dy in range(thick):
                pts.add((int(round(x)) + dx, int(round(y)) + dy))
    return pts


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
        self.canvas.bind("<Enter>",
                         lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>",
                         lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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


def _ui_font(size, bold=False):
    return ("Microsoft YaHei UI", size, "bold") if bold else ("Microsoft YaHei UI", size)


# ---- 抗锯齿圆角渲染管线 ----------------------------------------------------
# Tk Canvas 在 Windows 上没有抗锯齿（CustomTkinter 官方文档明说只有 macOS 有，
# smooth=True 多边形只能改善曲率、消除不了锯齿）。所以所有圆角一律用 Pillow
# 以 4 倍超采样渲染、LANCZOS 缩小，作为带透明通道的 PhotoImage 贴到 Canvas 上
# —— Tk 8.6+ 的 Canvas 原生支持图片 alpha。已知坑只有两个，都已处理：
# PhotoImage 必须持有引用（_AA_REFS），渲染结果按参数缓存避免重复开销。

_AA_CACHE = {}
_AA_REFS = []


def _hex_rgba(color):
    c = str(color).lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255)


def _aa_rounded(w, h, radius, fill, outline=None, ow=0):
    """抗锯齿圆角矩形 PhotoImage（4x 超采样 + LANCZOS 缩小，带缓存）。"""
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


# 走查 E2-01：此处曾有第二句模块级 `from PIL import ...`，击穿了文件顶部的
# PIL_OK 守卫——缺 Pillow 时连 CLI（--set / --apply-schedule）都在 import 阶段
# 崩掉。图片符号统一由顶部 try/except 提供，缺依赖时 GUI 在 main() 里报错退出。


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
        w = max(self.winfo_width(), 2)
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
            return
        self._body_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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
        # v2.01.22（走查 P-27）：滚轮改用全局绑定。原来靠 Canvas 的 <Enter>
        # 才 bind_all —— 鼠标若从窗口外直接进到 Label/Frame 上，Canvas 收不到
        # Enter（Tk 事件不冒泡），滚轮根本没绑上，设置页只能拖滚动条。
        # 模态弹层期间只有它在收事件，全局绑定不会误伤别的窗口。
        self.bind_all("<MouseWheel>", self._on_sheet_wheel, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event=None):
        """弹层销毁时解掉全局滚轮绑定，避免关窗后还抢滚轮。"""
        if event is not None and getattr(event, "widget", None) is not self:
            return
        try:
            self.unbind_all("<MouseWheel>")
        except Exception:
            pass

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

        win = self.section("窗口")
        self.row(win, "系统托盘图标",
                 tip="在右下角常驻，右键可熄/亮屏、切换定时",
                 builder=lambda p: Switch(p, bool(cfg.get("tray_enabled", True)),
                                          lambda v: app.apply_setting("tray_enabled", v)).pack())
        self.row(win, "关闭窗口时最小化到托盘",
                 tip="关掉则点 × 直接退出程序")

        start = self.section("启动")
        self.row(start, "开机自动启动", tip="启动后直接进托盘",
                 builder=lambda p: Switch(p, get_autostart()[0],
                                          app.apply_autostart).pack())

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
        self.show()


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


# ============================================================
# 十、界面决策纯函数（v2.03.0 P2：从 App 里抽出的业务判断）
#
# 这些原先都是 App 的方法，但只依赖 cfg / 少量入参，不碰任何控件。
# 抽出来的目的：能单独测（不需要 Tk），且 App 只留一行转发。
# **不改变任何行为**——逻辑逐字搬移。
# ============================================================
def status_payload(cfg, state, hint):
    """动作刚结束时的即时显示。

    v2.01.0：只有拓扑模式"动作完成 = 拓扑回读确认"才算知道结果；
    ddc / system 读不到可信的亮灭，一律先显示未知，随后的「检测」
    会用真实读数覆盖。宁可短暂不显示，也不给假结论。
    """
    if cfg.get("screen_off_method") != "topology":
        return {"state": "unknown", "hint": hint}
    return {"state": state, "hint": hint}


def row_action_blocker(cfg, busy):
    """逐台按钮能不能用？不能用就返回一句原因。"""
    method = cfg.get("screen_off_method")
    if method not in ("ddc", "topology"):
        return "「系统级」熄灭是整组广播，不能只动一块；改成「DDC」或「拓扑」才有逐台按钮"
    if busy:
        return "正在处理上一个操作，请稍候"
    return ""


def auto_relight_minutes(cfg):
    """熄灭后自动点亮的分钟数；非法值一律当 0（不自动）。"""
    try:
        return max(0, min(120, int(cfg.get("screen_auto_relight_min", 0))))
    except Exception:
        return 0


def restore_selection(prev, count):
    """刷新后界面上要勾哪些屏。

    **启动时全选**（v2.01.10 起）：用户打开程序最常见的目的就是"把所有屏
    一起调一下"。刷新时（`prev` 非空）保留用户当前的勾选，不打断他。
    定时任务的目标是另一套解析（`resolve_monitors` 读 monitor_anchors），
    不受这里影响。
    """
    keep = [i for i in (prev or []) if 0 <= i < count]
    if keep:
        return keep
    return list(range(count))


def tray_tooltip_text(cfg, last_verdict, title):
    """托盘悬停文案：定时摘要 + 下次运行 + 屏幕状态（最多 120 字）。"""
    if not cfg.get("enabled"):
        return "%s\n定时未启用" % title
    slots = cfg.get("slots", [])
    brief = "  ".join("%s→%d%%%s" % (x.get("time"), x.get("level", 0),
                                      "熄" if x.get("screen") == "off" else
                                      "亮" if x.get("screen") == "on" else "")
                       for x in slots)
    nxt = _next_run_text([parse_hhmm(x.get("time")) for x in slots],
                         days_by_min=_days_by_min_from_fp(
                             _slot_trigger_fingerprint(slots)))
    # v2.01.0：用**上次实际探测**的结果，不再读配置里的记账；
    # 没探测过就老实说"未检测"，不猜。
    st = {"on": "亮着", "off": "已熄灭"}.get(
        (last_verdict or {}).get("state"), "未检测")
    return ("%s\n定时: %s\n下次 %s\n屏幕: %s" % (title, brief, nxt, st))[:120]


class App:
    """协调者：服务调用、异步编排、弹窗；界面细节都在上面的控件里。"""

    def __init__(self, root, start_minimized=False):
        self.root = root
        self.tray = None
        root.title("%s v%s" % (APP_TITLE, APP_VERSION))
        root.resizable(False, False)
        apply_dark_theme(root)

        self.cfg = load_config()
        self._config_error = CONFIG_LOAD_ERROR
        self.monitors = []
        self.mon_info = []
        self.topo_inactive = []           # 拓扑模式下断开的物理屏（GUI 灰显行）
        self._row_action = False          # 本次动作来自行内逐台按钮（不改勾选）
        self._screen_states = {}          # 逐台亮灭（只有 DDC 模式需要，决定行内按钮）
        # 走查 E2-03：models 键被 _load_model_cache 统一 str() 存盘，但一个
        # 手工编辑/旧版残留的非数字键会让 int(k) 抛 ValueError，windowed 下
        # stderr=None → 用户只看到"程序打不开"。跳过坏键，不拖垮 GUI。
        self.mon_models = {}
        for k, v in self.cfg.get("models", {}).items():
            try:
                self.mon_models[int(k)] = v
            except (TypeError, ValueError):
                continue
        self._read_queue = queue.Queue()
        self._bg_tasks = 0
        self._busy = False
        self._busy_action = None      # 当前忙任务对应的屏幕动作（"on"/"off"/None）
        self._pending_screen = None   # 忙时最后一次点击，结束后补执行一次
        self._task_querying = False
        self._relight_pending = False
        self._relight_timer = None       # 走查 E1-05：自动复亮 timer 句柄（可取消）
        # 启动时**不猜**屏幕状态：unknown 直到第一次「检测」拿到真实读数。
        # v2.01.0：以前这里从配置里读 screen_state，等于把上次的记账当成当前事实。
        self._last_verdict = {"state": "unknown", "detail": ""}
        self.status_var = tk.StringVar(value="就绪")
        self.msg = DarkBox(self.root)
        # windowed 打包后 stderr 为 None，Tk 回调异常会彻底静默；
        # 落到 error.log + 状态栏，避免再出现「点了没反应」式黑洞。
        self.root.report_callback_exception = self._report_tk_error
        self._sheet = None
        self._editor = None
        self._probing = False
        self._brighting = False
        self._fit_pending = False
        self._fitted_h = -1
        self._schedule_reg_timer = None
        self._restore_after = None       # 恢复双屏倒计时 timer 句柄（可取消）
        self._restore_deadline = 0.0
        self._auto_checked = False

        self.scroll = ScrollArea(root, UI["bg"], on_fit=self._fit_window)
        self.scroll.pack(side="top", fill="both", expand=True)
        self.body = self.scroll.body

        # 顶部：右侧两个入口（标题由系统标题栏承担，避免重复）
        top = tk.Frame(self.body, bg=UI["bg"])
        top.pack(fill="x", padx=14, pady=(8, 0))
        Button(top, "设置", self.open_settings, kind="ghost", width=52,
               height=24, pad_bg=UI["bg"], font_size=8).pack(side="right")
        Button(top, "说明", self.open_help, kind="ghost", width=52,
               height=24, pad_bg=UI["bg"], font_size=8).pack(side="right", padx=(0, 6))

        # 显示器选择在最上面，屏幕控制（状态卡）移到其下（用户要求）
        self.mon_panel = MonitorPanel(self)
        self.status_card = StatusCard(self)
        self.status_card.pack(fill="x", padx=14, pady=(8, 0))
        self.bri_panel = BrightnessPanel(self, current_level(self.cfg),
                                         self.cfg.get("presets"))
        self.sch_panel = SchedulePanel(self, self.cfg)
        # 底部留白：最后一张卡（定时）的下缘不贴窗口下边（用户要求 10px）
        tk.Frame(self.body, bg=UI["bg"], height=10).pack(fill="x")

        # 没有底部状态栏：status_var.set() 的消息统一走状态卡的临时反馈行
        # （闪 3 秒回到常驻提示）。trace 必须在状态卡建好之后再挂。
        self.status_var.trace_add(
            "write", lambda *_: self.status_card.flash(self.status_var.get()))

        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.refresh_monitors(silent=True)
        self.refresh_task_status()
        self.probe_screens()
        if self.cfg.get("tray_enabled", True):
            self._create_tray()
        if start_minimized and self.tray is not None:
            self.root.withdraw()

        # 配置读坏的显式提示（不能静默吞掉：用户的设置还在坏文件里）
        self.root.after(150, self._warn_config_error)
        # 首次启动：自动把检测到的显示器锚点写进配置（v2.01.17）。
        # 原因：锚点只在用户手动勾选时才存，首次打开配置里 anchors 是空的，
        # 定时/熄灭逻辑只能靠纯 index 兜底，换屏/重排后就会认错屏。
        # 用 after_idle（Tk 官方推荐的"窗口显示后再初始化"做法，Bryan Oakley）：
        # 等窗口可见、显示器列表建好再做，不阻塞首屏绘制。
        # 只在"从未保存过锚点"时写一次，不覆盖用户后续的手动选择。
        if not (self.cfg.get("monitor_anchors") or []):
            self.root.after_idle(self._first_run_detect)

        # 启动后台自检：只校验任务定义与当前进程是否一致（不一致则原地重建）。
        # 不补执行、不改亮度、不熄屏；900ms 是给首屏探测让路，全程后台线程。
        self.root.after(900, self._start_auto_checks)

        # v2.01.20：发现旧 APPDATA 配置时提示一句（不自动迁移）。
        # 延迟到首屏探测之后，避免被启动期的其它状态文案盖掉。
        old_cfg = old_config_present()
        if old_cfg:
            _write_event_log("检测到旧版配置（未自动迁移）：%s" % old_cfg)
            self.root.after(1400, lambda p=old_cfg: self.status_var.set(
                "旧版配置在 %s；要沿用就手动复制 config.json 到程序目录" % p))

    def _fit_window(self):
        """内容晚到（任务状态行/显示器行）后按请求尺寸重排窗口。

        窗口 resizable(False) 时 WM 不会自动跟随内容增长，晚到的行会被
        底边裁掉（用户实测）。两个护栏：
        1) 防抖：合并 80ms 内的多次触发（探测/任务状态/渲染常常连环触发）；
        2) 高度没变就跳过 geometry("")——整窗重排不便宜，且会闪。
        超高部分仍由 ScrollArea 的封顶 + 滚动条兜底。
        """
        if self._fit_pending:
            return
        self._fit_pending = True
        self.root.after(80, self._fit_window_now)

    def _fit_window_now(self):
        self._fit_pending = False
        try:
            self.root.update_idletasks()
            need = self.scroll.body.winfo_reqheight()
            if need == self._fitted_h:
                return
            self._fitted_h = need
            self.root.geometry("")
        except Exception:
            pass

    # ---------- 顶部入口 ----------
    def open_settings(self):
        try:
            # 必须留一个引用：Toplevel 被 GC 时窗口会跟着消失（实测踩过）
            self._sheet = SettingsSheet(self)
        except Exception as e:
            self.msg.showerror("打开设置失败", "%s: %s" % (type(e).__name__, e))

    def open_help(self):
        try:
            self._sheet = HelpSheet(self)
        except Exception as e:
            self.msg.showerror("打开说明失败", "%s: %s" % (type(e).__name__, e))

    def open_log_dir(self):
        """打开日志目录（和程序同目录；不可写时才在 APPDATA）。"""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)             # noqa: S606 — 只打开自家目录
        except Exception as e:
            self.status_var.set("打开日志目录失败：%s" % e)

    def remember_layout(self):
        """把现在的显示器摆法记成"用户的布局"，断开/接回都按这份来。"""
        layout, err = remember_layout_snapshot(self.cfg)
        if err:
            self.status_var.set("记不住布局：%s" % err)
            self.msg.showerror("记住布局失败", err)
            return
        dlog("app.action", what="remember_layout", count=len(layout))
        # v2.01.12：位置与完整 CCD 快照一起记，供「恢复双屏」整份写回
        desc, snap_err = save_default_layout()
        if snap_err:
            self.status_var.set("位置已记住，但默认布局快照没存上：%s" % snap_err)
            self._flash_remember_btn("快照失败", err=True)
            return
        self.status_var.set("已记住 %d 块屏的位置和布局（%s）" % (len(layout), desc))
        self._flash_remember_btn("已记住 ✓")

    def _flash_remember_btn(self, text, err=False):
        """在「记住」按钮上短暂显示结果（v2.01.25）。

        设置弹层是模态的，会挡住主窗口状态卡；不给就地回执，用户就以为没反应。
        2 秒后复原；弹层先被关掉时 widget 已销毁，静默跳过。
        """
        btn = getattr(self, "_remember_btn", None)
        if btn is None:
            return
        try:
            btn.set_text(text)
            if err:
                btn.after(2600, lambda: self._restore_remember_btn(btn))
            else:
                btn.after(1800, lambda: self._restore_remember_btn(btn))
        except Exception:
            pass

    def _restore_remember_btn(self, btn):
        try:
            if btn.winfo_exists():
                btn.set_text("记住")
        except Exception:
            pass

    # ---- 恢复双屏（倒计时） ------------------------------------------------
    def ask_restore_default(self):
        """点「恢复双屏」：先校验默认布局还在且与硬件匹配，再启动倒计时。"""
        if getattr(self, "_restore_after", None) is not None:
            return
        err = restore_default_layout(validate_only=True)
        if err:
            dlog("app.action", what="restore_default", stage="precheck", err=err)
            self.status_var.set(err)
            self.msg.showwarning("恢复双屏不可用", err)
            return
        secs = max(0, min(120, int(self.cfg.get("restore_delay", 10))))
        dlog("app.action", what="restore_default", stage="countdown", seconds=secs)
        self._restore_deadline = time.monotonic() + secs
        self._tick_restore()

    def _tick_restore(self):
        """每秒刷一次剩余秒数；到点执行恢复。"""
        left = int(self._restore_deadline - time.monotonic() + 0.999)
        if left > 0:
            self.status_card.set_restore_countdown(left)
            self._restore_after = self.root.after(200, self._tick_restore)
        else:
            self._restore_after = None
            self.status_card.set_restore_countdown(None)
            self._do_restore_default()

    def cancel_restore_default(self):
        if getattr(self, "_restore_after", None) is not None:
            self.root.after_cancel(self._restore_after)
            self._restore_after = None
        self.status_card.set_restore_countdown(None)
        dlog("app.action", what="restore_default", stage="cancel")
        self.status_var.set("已取消恢复双屏")

    def _do_restore_default(self):
        """倒计时到点：整份写回默认双屏布局（后台线程，不卡 UI）。"""
        # v2.01.20（走查 P-22）：到点时如果正有屏幕动作在跑（用户刚点了
        # 熄灭/点亮），两边都改拓扑会互相覆盖，_busy 的归属也会错乱
        # （先完成的那个把按钮启用了，另一个还在跑）。直接取消并说明。
        if self._busy:
            self.status_var.set("有屏幕操作正在进行，恢复双屏已取消，请稍后重试")
            dlog("app.action", what="restore_default", stage="cancel_busy",
                 busy_action=self._busy_action)
            return

        def job():
            return restore_default_layout()

        def done(err):
            if err:
                self.status_var.set(err)
                self.msg.showerror("恢复双屏失败", err)
            else:
                dlog("app.action", what="restore_default", stage="done")
                self.status_var.set("已恢复默认双屏布局")
                self.refresh_monitors(silent=True)
        self.run_bg(job, done, busy=True)

    def export_diag(self):
        """导出诊断包，并直接在资源管理器里选中它。"""
        path, err = diag_export()
        if err:
            self.status_var.set("导出失败：%s" % err)
            self.msg.showerror("导出诊断包失败", err)
            return
        self.status_var.set("诊断包已导出：%s" % path)
        try:
            subprocess.Popen(["explorer", "/select,%s" % path])
        except Exception:
            pass

    # ---------- 设置项写回 ----------
    def apply_setting(self, key, value):
        """设置弹层里每个开关/输入框的统一出口：落配置并立即生效。"""
        try:
            if key == "tray_enabled":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
                if value:
                    self._create_tray()
                    self.status_var.set("托盘图标已启用（点 × 进托盘）")
                else:
                    self._stop_tray()
                    self.status_var.set("托盘图标已关闭（点 × 直接退出）")
            elif key == "screen_auto_relight_min":
                self.cfg[key] = max(0, min(120, int(value)))
                save_config(self.cfg)
            elif key == "wake_switch_input":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
            elif key == "diag_log":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
                # 先写一条再改开关：否则"已关闭"这一条自己就记不下来了
                dlog("app.setting", key=key, value=bool(value))
                diag_set_enabled(self.cfg[key])
                self.status_var.set("诊断日志已" + ("开启" if value else "关闭"))
            elif key == "window_follow":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
                self.status_var.set("改显示器时" + ("会" if value else "不会")
                                    + "把窗口搬回原屏")
            elif key == "presets":
                self.cfg[key] = coerce_setting("presets", value)
                save_config(self.cfg)
                self.bri_panel.set_presets(self.cfg[key])
        except Exception as e:
            self.msg.showerror("保存设置失败", "%s: %s" % (type(e).__name__, e))

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

    def apply_autostart(self, enable):
        exe, prefix = task_target()
        cmd = ('"%s" %s--minimized' % (exe, prefix)).strip()
        try:
            set_autostart(bool(enable), cmd)
            self.status_var.set("开机自启动已" + ("开启" if enable else "关闭"))
        except Exception as e:
            self.msg.showerror("设置失败", "%s: %s" % (type(e).__name__, e))

    def apply_off_method(self, value):
        if value == "ddc" and not self.cfg.get("ddc_allow_undeclared"):
            if not self.msg.askyesno(
                    "单屏待机：有风险",
                    "这条路的「熄灭」用面板待机命令（0xD6=2），但：\n"
                    "• 你的面板能力串只声明 01/04/05，没声明 02；\n"
                    "• 微软文档警告盲写未实现的 MCCS 值会导致未定义行为；\n"
                    "• 万一唤不回，只能按显示器电源键。\n\n"
                    "点亮时会依次尝试：电源模式 → 写亮度 → 输出重驱动 → 切信号源。"):
                return
            self.cfg["ddc_allow_undeclared"] = True
        if value == "topology" and not self.msg.askyesno(
                "拓扑断开：先确认",
                "这条路会把「选中的副屏」从 Windows 桌面拓扑里移除，供串流隐藏物理屏用：\n"
                "• 主屏不会被断（Windows 实测 rc=87），虚拟屏 VDD 永不作目标；\n"
                "• 每次只断一块；恢复时先回读 ACTIVE 再写亮度（≤2 秒）；\n"
                "• 窗口位置不保证完全还原，首次使用请做一次串流回归。\n\n"
                "默认仍是「单屏待机」，选它才会切到本方式。"):
            return
        self.cfg["screen_off_method"] = value
        # v2.01.0：`ddc_allow_undeclared` 是"这一次明知有风险"的授权，不是永久状态。
        # 离开 ddc 就收回——否则用户在旧屏上点过一次「同意」，换屏后盲写
        # 未声明的待机值这件事会被悄悄继承下来（走查 E2-05 的单向阀门）。
        if value != "ddc":
            self.cfg["ddc_allow_undeclared"] = False
        save_config(self.cfg)
        if value == "ddc":
            self.status_var.set("熄灭方式：单屏待机（键鼠不会点亮）")
        elif value == "topology":
            self.status_var.set("熄灭方式：拓扑断开（副屏移除，主屏不动）")
        else:
            self.status_var.set("熄灭方式：系统级（整组熄灭，一定能唤回）")
        # v2.01.22（走查 P-29）：切模式必须重建列表 ——
        # topology 才列"断开的物理屏"，ddc/system 只留 VDD。
        # 原来只 probe_screens，用户切到 topology 后看不到能接回的屏。
        self.refresh_monitors(silent=True)
        # 状态卡的结论形态随模式变化（DDC 下不显示亮灭），立即重探一次
        self.probe_screens()

    # ---------- 显示器勾选 ----------
    def select_all(self):
        self.mon_panel.set_all(True)
        self.read_current()

    def select_none(self):
        self.mon_panel.set_all(False)
        self.read_current()

    def selected_indices(self):
        return self.mon_panel.selected()

    def on_monitor_toggle(self):
        if not self.mon_panel._suspend:
            self.read_current()

    def _restore_selection(self, prev):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return restore_selection(prev, len(self.monitors))

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

    def _first_run_detect(self):
        """首次启动补一次全量检测，并补写显示器锚点（v2.01.17，v2.01.19 修）。

        v2.01.19 修：原实现无条件 `list(range(len(self.monitors)))`，把老配置
        里"用户只勾了一块屏"的选择**静默改成全选**。现在按配置解析：
        `resolve_monitors()` 返回 [] 才表示"配置里确实没有选择信息"= 全选；
        有选择就按原选择补锚点，不动物理勾选语义。
        """
        if not self.monitors:
            return
        # 全量检测一次（启动链路里已有一次 probe_screens，这里再补一次是
        # 为了 after_idle 时窗口已可见、显示器句柄都稳定了，读数更可靠）
        self.probe_screens()
        idx = resolve_monitors(self.cfg) or list(range(len(self.monitors)))
        anchors = selection_anchors(idx)
        if not anchors:
            dlog("app.first_run_detect", err="拿不到锚点", idx=idx)
            return
        # v2.01.24（走查 R-02 转正）：只写 anchors，**不动 monitors/monitor_ids**。
        # 用户配的 `monitors: []` 是"全部"语义；写成 [0,1] 会把"全部"固化成
        # "这两块"，以后插新屏不会自动纳入。anchors 只用来让定时任务认得住屏，
        # 不承担"范围"语义。
        self.cfg["monitor_anchors"] = anchors
        ok, err = save_config(self.cfg)
        dlog("app.first_run_detect", idx=idx, anchors=anchors, saved=ok, err=err)
        if ok:
            self.status_var.set("已自动识别 %d 块显示器" % len(idx))

    def is_vdd(self, index):
        """这一行是不是虚拟显示器 —— 归串流软件管，不给逐台按钮。"""
        if index >= len(self.monitors):
            return False
        dev = _short_dev(self.monitors[index].get("dev"))
        return bool(getattr(self, "_vdd_devs", ()) and dev in self._vdd_devs)

    def ccd_friendly(self, index):
        """这块屏在 CCD 里的友好名（v2.01.19）。

        DDC 枚举出来的 `desc` 恒为 "Generic PnP Monitor"（真机实测），
        拿它当名字会让 VDD 显示成物理屏的通用名。CCD 的
        `monitorFriendlyDeviceName` 才是用户看到的 "VDD by MTT" / "R27qe Gen2"。
        """
        if index >= len(self.monitors):
            return ""
        dev = _short_dev(self.monitors[index].get("dev"))
        return (getattr(self, "_ccd_names", {}) or {}).get(dev) or ""
        if not self.monitors:
            self.status_var.set(
                "未检测到显示器：请确认显示器 OSD 已开 DDC/CI，且用 HDMI/DP/DVI 直连")
            return
        if not silent:
            self.status_var.set("已刷新：检测到 %d 台显示器" % len(self.monitors))
        self.read_current()

    def _apply_models(self, models):
        if isinstance(models, Exception):
            return
        changed = False
        for i, name in models.items():
            if not name or self.mon_models.get(i) == name or i >= len(self.monitors):
                continue
            self.mon_models[i] = name
            self.mon_panel.set_row_text(i, name)
            changed = True
        if changed:
            self.cfg["models"] = {str(k): v for k, v in self.mon_models.items() if v}
            save_config(self.cfg)

    # ---------- 统一异步入口 ----------
    def run_bg(self, job, on_done=None, busy=False):
        if busy:
            self._busy = True
            self._set_actions_enabled(False)
        self._bg_tasks += 1

        def runner():
            try:
                result = job()
            except Exception as e:
                result = e
            self._read_queue.put(("done", (on_done, result, busy), None))

        threading.Thread(target=runner, daemon=True).start()
        self._poll_read_queue()

    def _poll_read_queue(self):
        # 走查 E1-03：screen_action 分支直接调 screen_on/off → run_bg 内又同步调
        # _poll_read_queue，每次重入都会多挂一条 after(150)，链数随调用次数
        # 线性增长且永不回落。用 _polling 护栏保证同时只有一条链。
        if getattr(self, "_polling", False):
            return
        self._polling = True
        try:
            self._poll_read_queue_inner()
        finally:
            self._polling = False

    def _poll_read_queue_inner(self):
        while True:
            try:
                kind, payload, _extra = self._read_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "screen_action":
                (self.screen_off if payload == "off" else self.screen_on)()
                continue
            if kind != "done":
                continue
            on_done, result, busy = payload
            self._bg_tasks = max(0, self._bg_tasks - 1)
            if busy:
                self._busy = False
                self._busy_action = None
                self._set_actions_enabled(True)
            try:
                if on_done is not None:
                    on_done(result)
            finally:
                # 忙时排队的屏幕请求：当前动作一结束就补执行（只一次）
                if busy:
                    self._dispatch_pending_screen()
        if self._bg_tasks > 0 or self._relight_pending:
            self.root.after(150, self._poll_read_queue)

    def _set_actions_enabled(self, enabled):
        self.status_card.set_actions_enabled(enabled)
        self.bri_panel.set_actions_enabled(enabled)
        self.sch_panel.set_actions_enabled(enabled)

    # ---------- 亮度 / 探测 ----------
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

    def _fetch_models(self):
        """显示器型号缓存没有的补一次（写入行标题与配置）。"""
        targets = self.selected_indices() or list(range(len(self.monitors)))
        # VDD 不读型号：它没有真实 EDID，按 index 缓存下来的名字会串到
        # 物理屏行（或反过来，VDD 当主屏时套用物理屏名）—— v2.01.12 修
        missing = [i for i in targets
                   if i not in self.mon_models and 0 <= i < len(self.monitors)
                   and not self.is_vdd(i)]
        if not missing:
            return

        def job_models():
            out = {}
            targets_now = enumerate_targets()
            lib = _dxva2()
            used = [t for t in targets_now if t["index"] in missing]

            def one(t):
                try:
                    out[t["index"]] = monitor_model(t["handle"], _display_key(t))
                finally:
                    # 同 screen_wake_many：句柄由线程自己销毁，避免 join 超时后 UAF
                    try:
                        lib.DestroyPhysicalMonitor(t["handle"])
                    except Exception:
                        pass

            threads = [threading.Thread(target=one, args=(t,), daemon=True) for t in used]
            for th in threads:
                th.start()
            for th in threads:
                th.join(20)
            release_targets([t for t in targets_now if all(t is not u for u in used)])
            return {i: v for i, v in out.items() if v}

        self.run_bg(job_models, self._apply_models)

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

    # ---------- 快捷预设 ----------
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

    # ---------- 亮灭检测 ----------
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

    def _render_states(self, result):
        self._probing = False
        self._brighting = False
        if isinstance(result, Exception):
            self.status_var.set("检测失败：%s" % result)
            return
        states, bright = result
        self._screen_states = {s["index"]: s["state"] for s in states
                               if isinstance(s, dict) and "index" in s and "state" in s}
        # 启动早期可能还没建面板；建了才让它按最新亮灭重画行内按钮
        panel = getattr(self, "mon_panel", None)
        if panel is not None:
            panel.refresh_buttons()
        if not states:
            # 轻量刷新（只读亮度）：不动亮灭结论，只更新提示行
            if bright and self._last_verdict:
                self._update_hint_brightness(bright)
            return
        v = screen_verdict(self.cfg, states)
        # v2.01.0：这里以前会在"被键鼠唤醒"时把状态回写落盘。程序不再记账，
        # 结论就是当次探测的结果，不需要也不应该往配置里写运行态。
        if v["state"] != "off":
            vals = [bright[i] for i in sorted(bright) if bright.get(i) is not None]
            if vals and len(set(vals)) == 1:
                hint = "当前 %d%% · %d 台显示器" % (vals[0], len(states))
            elif vals:
                hint = "当前 %s%% · %d 台显示器" % (
                    "/".join(str(x) for x in vals), len(states))
            else:
                hint = "%d 台显示器 · 亮度可调" % len(states)
        else:
            hint = "键鼠输入不会点亮它；点「点亮屏幕」恢复"
        # 状态未知时，把"为什么不知道"直接摆在提示行——否则用户点一次「检测」
        # 只看到一片空白，不知道程序到底读了什么。
        if v["state"] == "unknown" and v.get("evidence"):
            hint = hint + " · " + v["evidence"]
        self.status_card.render(self._status_payload(v["state"], hint))
        self._last_verdict = v
        self._fit_window()

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

    def _remember_selection(self):
        idx, ids = selection_payload(self.selected_indices())
        self.cfg["monitors"] = idx
        self.cfg["monitor_ids"] = ids
        self.cfg["monitor_anchors"] = selection_anchors(idx)

    # ---------- 熄屏 / 点亮 ----------
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
            self.refresh_monitors(silent=True)
        self.probe_screens()

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
            self.refresh_monitors(silent=True)
        for i, e in errs:
            self.status_var.set("启用失败：" + (e if i < 0 else "显示器 %d: %s" % (i, e)))
        if errs:
            self.msg.showwarning("启用失败", "\n".join(
                (e if i < 0 else "显示器 %d: %s" % (i, e)) for i, e in errs))
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
            self.refresh_monitors(silent=True)
        self.probe_screens()

    def _auto_relight_minutes(self):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return auto_relight_minutes(self.cfg)

    def _stop_tray(self):
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None

    # ---------- 托盘 ----------
    def _tray_image(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(30, 30, 30, 255),
                  outline=(74, 158, 255, 255), width=3)
        d.rounded_rectangle((14, 28, 50, 36), radius=4, fill=(74, 158, 255, 255))
        d.ellipse((36, 23, 52, 41), fill=(255, 255, 255, 255))
        return img

    def _tray_tooltip_text(self):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return tray_tooltip_text(self.cfg, self._last_verdict, APP_TITLE)

    def _tray_update_tooltip(self):
        if self.tray is not None:
            try:
                self.tray.tooltip = self._tray_tooltip_text()
                self.tray.update_menu()
            except Exception:
                pass

    def _create_tray(self):
        if not TRAY_OK or self.tray is not None or not self.root.winfo_exists():
            return
        menu = _TrayMenu(
            _TrayItem("打开主窗口", lambda *_: self.root.after(0, self._tray_show_main),
                      default=True),
            _TrayItem("熄灭显示器", lambda *_: self.root.after(0, self.screen_off)),
            _TrayItem("点亮显示器", lambda *_: self.root.after(0, self.screen_on)),
            _TrayItem("启用定时", lambda *_: self.root.after(0, self._tray_toggle_schedule),
                      checked=lambda _item: bool(self.cfg.get("enabled"))),
            _TrayItem("退出", lambda *_: self.root.after(0, self._tray_quit)),
        )
        self.tray = _TrayIcon("MonitorBrightness", self._tray_image(),
                              self._tray_tooltip_text(), menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_show_main(self):
        self.root.deiconify()
        self.root.lift()
        # v2.01.22（走查 P-30）：窗口在托盘里期间可能拔插过屏、开关过串流，
        # 打开时必须重读，否则列表还是最小化前那一份（用户报"打开没有
        # 刷新出虚拟显示器"的另一条路径）。
        self.refresh_monitors(silent=True)
        self.probe_screens()
        self.status_var.set("就绪")

    def _tray_toggle_schedule(self):
        if self.sch_panel.schedule_enabled():
            self.cancel_schedule()
        else:
            if not self._set_schedule_enabled(True, persist=True):
                return
            self.status_var.set("正在写入任务计划…")
            self.run_bg(lambda: register_tasks(self.cfg),
                        self._finish_tray_register, busy=True)

    def _finish_tray_register(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            self._rollback_schedule(False, "注册任务失败", out)
            return
        self.refresh_task_status()
        self.status_var.set("定时已启用")

    def _tray_quit(self):
        self._stop_tray()
        self.root.destroy()

    def _on_close(self):
        if self.tray is not None:
            # 倒计时中进行时最小化：不让它在用户看不到界面时偷偷恢复
            if getattr(self, "_restore_after", None) is not None:
                self.cancel_restore_default()
            self.root.withdraw()
            self.status_var.set("已最小化到托盘（右下角图标，右键可退出）")
            return
        self.root.destroy()

    # ---------- 启动自检（只校验任务，不执行业务动作） ----------
    def _report_tk_error(self, exc, val, tb):
        """Tk 回调异常兜底：windowed 下 stderr 不可见，必须落盘并提示。"""
        detail = "".join(traceback.format_exception(exc, val, tb))
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(ERROR_FILE, "a", encoding="utf-8") as f:
                f.write("%s\n%s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), detail))
        except Exception:
            pass
        short = "%s: %s" % (getattr(exc, "__name__", "错误"), val)
        try:
            _write_event_log("界面异常 %s" % short)
        except Exception:
            pass
        try:
            self.status_var.set("界面异常：%s（详见 error.log）" % short)
        except Exception:
            pass

    def _warn_config_error(self):
        if self._config_error:
            self.status_var.set("配置文件读取失败，已使用默认值")
            self.msg.showwarning("配置文件有问题", self._config_error)

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

    # 兼容旧名（托盘/tray 里调用）
    _refresh_task_status = refresh_task_status

    def _apply_task_status(self, text, runtime=""):
        info = task_summary(text, self.cfg.get("slots", []), runtime)
        self.sch_panel.set_task_status(info)
        self.scroll.refresh()
        self._tray_update_tooltip()


def _safe_int(text, fallback=0):
    try:
        return int(float(str(text).strip()))
    except Exception:
        return fallback


def _short_dt(text):
    """'2026-11-04 08:30' → '11-04 08:30'；格式不认识就原样返回。"""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})$", str(text or "").strip())
    return "%s-%s %s" % (m.group(2), m.group(3), m.group(4)) if m else str(text or "")


def task_summary(text, slots, runtime=""):
    """把 query_task_status() / query_task_runtime() 的原始文本变成界面结论。

    返回 {"state": ok|warn|error|loading, "title": ..., "next": ..., "detail": ...}
    """
    by_name = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if "=" in line:
            name, rest = line.split("=", 1)
            if name in TASK_SHORT:
                by_name[name] = rest
    sched = by_name.get(TASK_SCHEDULE)
    catch = by_name.get(TASK_CATCHUP)
    if sched is None:
        return {"state": "error", "title": "任务计划：读不到状态",
                "next": "", "detail": "点「检测」重试；仍失败请用 --diag 排查"}
    if sched == "MISSING":
        return {"state": "warn", "title": "任务计划：未注册",
                "next": "", "detail": "打开「启用每日定时」开关即可注册"}

    last_run, last_code, rt_next, rt_state = _runtime_fields(runtime)
    bits = sched.split("|")
    state = "已启用" if bits[0] == "Ready" else bits[0]
    if rt_state:
        state = {"Ready": "已启用", "Running": "运行中",
                 "Disabled": "已禁用"}.get(rt_state, rt_state)
    nxt = (bits[1] if len(bits) > 1 else "").strip()
    if rt_next and rt_next not in ("NONE", "MISSING"):
        nxt = rt_next                       # 任务计划 API 的 NextRunTime 才是权威值
    swa = "补跑已开" if any("True" in b for b in bits[2:]) else "补跑未开"
    trig = ""
    for b in bits:
        if b.startswith("TRIG="):
            trig = b[5:]
    times = sorted(int(v) for v in trig.split(",") if v.strip().isdigit())
    by_time = {x.get("time"): x for x in slots}
    extra = [t for t in times
             if "%02d:%02d" % (t // 60, t % 60) not in by_time]
    detail = "%d 个触发点 · %s · 登录校准%s" % (
        len(times) or len(slots), swa,
        "已注册" if (catch and catch != "MISSING") else "未注册")
    if extra:
        detail += " · %d 个触发点不在当前配置" % len(extra)
    # 上次运行 / 上次结果：让"任务到底跑没跑"在界面里直接可见
    if last_run:
        last = "从未运行" if last_run == "NEVER" else \
            "上次 %s（%s）" % (_short_dt(last_run), _task_result_text(last_code))
        detail += " · " + last
    return {"state": "ok" if state == "已启用" and swa == "补跑已开" else "warn",
            "title": "任务计划：%s" % state,
            "next": ("下次 %s" % _short_dt(nxt)) if nxt else "",
            "detail": detail}


# ============================================================
# 八、入口
# ============================================================
# ---- 开机自启动（HKCU Run 键，免管理员）------------------------------------
AUTOSTART_RUN_NAME = "MonitorBrightness"


def _autostart_path():
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_autostart():
    """读开机自启动项。返回 (是否存在, 命令文本 or "")。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _autostart_path()) as k:
            val, _t = winreg.QueryValueEx(k, AUTOSTART_RUN_NAME)
            return True, str(val)
    except FileNotFoundError:
        return False, ""
    except Exception:
        return False, ""


def set_autostart(enable, command):
    """写/删开机自启动项。command 例：'"C:////...exe" --minimized'。"""
    import winreg
    if enable:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _autostart_path(), 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, AUTOSTART_RUN_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _autostart_path(), 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, AUTOSTART_RUN_NAME)
        except FileNotFoundError:
            pass


# ---- 单实例（命名互斥体；重复启动唤起已有窗口后退出）------------------------
# ⚠️ 只约束 GUI：CLI 模式（--apply-schedule 等计划任务动作）不获取互斥体，
#    否则托盘常驻时定时任务会被自己拦死。
_MUTEX_NAME = "MonitorBrightness_SingleInstance"
_mutex_handle = None


def acquire_single_instance(name=_MUTEX_NAME):
    """尝试获取单实例互斥体。返回 True=本进程是首个实例。

    已有实例时：尝试把它的主窗口恢复并置前，然后返回 False（调用方应退出）。
    name 参数供测试注入专用互斥体名（默认用全局单实例名）。
    互斥体随进程退出自动释放（崩溃残留的 abandoned 状态不影响下次创建）。
    """
    global _mutex_handle
    if os.name != "nt":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        _activate_existing_window()
        if handle:
            k32.CloseHandle(handle)
        return False
    _mutex_handle = handle                      # 保引用，防止句柄被 GC 关闭
    return True


def _activate_existing_window():
    """把已运行实例的主窗口（按标题前缀枚举，含托盘隐藏态）恢复并置前。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith(APP_TITLE):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, 9)              # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


def parse_screen_action(text):
    """'off'/'on' -> 规范动作名；其他 -> None（供 CLI 与测试复用）。"""
    t = str(text).strip().lower()
    return t if t in ("off", "on") else None


def cli_screen(action):
    """熄灭/点亮选中的显示器（配置里的选择；空 = 全部）。

    v2.01.0：不再把动作结果记账落盘——动作做完就是做完了，
    屏幕现在什么状态要靠 screen_verdict 现场读。
    """
    cfg = load_config()
    targets = resolve_monitors(cfg)
    if action == "off":
        ok, errs = screen_off_cfg(cfg, targets)
        _out("已熄灭显示器" if ok else "熄灭失败")
    else:
        ok, errs = screen_wake_entry(cfg, targets)
        _out("已点亮显示器" if ok else "点亮失败")
        for line in take_wake_info():
            _out(line)
    for i, e in errs:
        _out("显示器 %d：%s" % (i, e))
    if errs and not ok:
        sys.exit(1)


def main():
    args = sys.argv[1:]
    # v2.01.20：不自动迁移旧配置（用户决策：每台电脑各用各的）。
    # 旧 APPDATA 文件只在界面上提示一句，由用户决定是否手动拷贝。
    _migrate_old_logs()
    dlog("app.start", ver=APP_VERSION, argv=args,
         packaged=bool(getattr(sys, "frozen", False)),
         py="%d.%d.%d" % sys.version_info[:3])
    if args:
        cmd = args[0]
        if cmd == "--diag-log":
            # 诊断日志（临时命令，v2.01.4）：直接打印最近若干行
            lines = diag_dump(200)
            _out("diag.log：%s（%d 行）" % (_diag_file(), len(lines)))
            for line in lines:
                _out(line)
            return
        if cmd == "--diag-export":
            # 导出诊断包（临时命令）：日志 + 配置打成一个 zip
            path, err = diag_export()
            _out(err or ("已导出：%s" % path))
            return
        if cmd == "--set" and len(args) >= 3:
            cli_set(args[1], args[2])
            return
        if cmd == "--apply-schedule":
            cli_apply_schedule()
            return
        if cmd == "--screen" and len(args) >= 2:
            arg = args[1].strip().lower()
            if arg == "status":
                # 与界面同一套结论：当场读、不读任何记账（v2.01.0）
                v = screen_verdict(load_config())
                _out(v["text"])
                if v.get("detail"):
                    _out("  " + v["detail"])
                return
            action = parse_screen_action(args[1])
            if action is None:
                _out("--screen 参数需为 off 或 on")
                sys.exit(2)
            cli_screen(action)
            return
        if cmd == "--list":
            cli_list()
            return
        if cmd == "--probe":
            cli_probe()
            return
        if cmd == "--diag":
            cli_diag()
            return
        if cmd == "--selftest":
            cli_selftest()
            return
        if cmd == "--ensure-schedule":
            cli_ensure_schedule()
            return
    if not acquire_single_instance():
        return                                  # 已有实例：已唤起其窗口，直接退出
    if not PIL_OK:
        # GUI 明确报错退出；CLI 路径（--set/--apply-schedule/--diag/--selftest）不受影响
        from tkinter import messagebox
        messagebox.showerror(
            "缺少 Pillow",
            "图形界面需要 Pillow（用于圆角抗锯齿贴图），当前环境没有安装。\n\n"
            "命令行功能仍可正常使用：--set / --list / --probe / --diag / --selftest / "
            "--apply-schedule。\n安装后重试：pip install pillow")
        return
    root = tk.Tk()
    apply_tk_scaling(root)          # 必须在建控件之前：字体按新比例算
    App(root, start_minimized=("--minimized" in args))
    apply_dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
