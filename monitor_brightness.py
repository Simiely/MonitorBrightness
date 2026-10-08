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
      MonitorBrightness.exe --apply-schedule              按「当前时间」判定白天/晚上并下发
      MonitorBrightness.exe --list                        列出显示器
      MonitorBrightness.exe --screen off|on               熄灭 / 点亮选中的显示器
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
import os
import re
import json
import queue
import threading
import subprocess
import time
import ctypes.wintypes
from ctypes import wintypes
from datetime import datetime, timezone

import tkinter as tk
from tkinter import ttk, messagebox

# 托盘（可选依赖）：缺 pystray 时 GUI 照常工作，仅没有托盘/最小化到托盘能力
try:
    import pystray
    from pystray import Icon as _TrayIcon, Menu as _TrayMenu, MenuItem as _TrayItem
    TRAY_OK = True
except Exception:
    _TrayIcon = _TrayMenu = _TrayItem = None
    TRAY_OK = False

try:
    from monitorcontrol import get_monitors
except Exception:  # pragma: no cover
    get_monitors = None

APP_TITLE = "显示器亮度定时调节"
APP_VERSION = "1.6.1"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MonitorBrightness")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

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


# ============================================================
# 一、配置读写（设置项的唯一声明表在此，其余全部由它派生）
# ============================================================
# 为什么要有这张表：实测这些键在源码里触点 5~9 处（Fowler 的 Shotgun Surgery），
# 加一个设置要同时改 默认值表 / 校验 / 建控件 / 读表单 / 拼注册脚本 五六处。
# 现在只有这里声明一次，其余地方一律派生。
#   kind: indices 显示器序号列表 | hhmm 时间 | level 亮度 0-100 | flag 开关
DEFAULT_SLOTS = [{'time': '08:30', 'level': 80, 'screen': ''}, {'time': '21:00', 'level': 0, 'screen': ''}]

# 每个时间段可携带的屏幕动作：""（不变）/ off（到点熄灭）/ on（到点点亮）
SCREEN_ACTIONS = (("", "不变"), ("off", "熄灭"), ("on", "点亮"))
SCREEN_VALUE_BY_LABEL = {label: value for value, label in SCREEN_ACTIONS}
SETTINGS = (
    {"key": "monitors", "kind": "indices", "default": []},
    {"key": "slots", "kind": "slots", "default": DEFAULT_SLOTS},
    {"key": "enabled", "kind": "flag", "default": False},
    {"key": "catch_up_on_logon", "kind": "flag", "default": True},
    {"key": "screen_hotkeys", "kind": "flag", "default": False},          # 熄屏/点亮热键（需常驻）
    {"key": "screen_auto_relight_min", "kind": "level", "default": 0},    # 熄灭后 N 分钟自动点亮（0=关）
)
SETTING_BY_KEY = {s["key"]: s for s in SETTINGS}
CONFIG_VERSION = 4

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
    return normalize_indices(value)


def _coerce_slots(value):
    """把各种写法规整成 [{time, level}]：按时间排序、时间去重、亮度 0-100、至少一档。"""
    out, seen = [], set()
    if isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, dict):
                continue
            t = str(item.get("time", "")).strip()
            if not _valid_hhmm(t) or t in seen:
                continue
            seen.add(t)
            try:
                lv = max(0, min(100, int(float(item.get("level", 50)))))
            except Exception:
                lv = 50
            scr = str(item.get("screen", "")).strip().lower()
            if scr not in ("", "off", "on"):
                scr = ""
            out.append({"time": t, "level": lv, "screen": scr})
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


def load_config():
    """读取配置；兼容 v1.0.0 的单选字段 monitor（int）→ v2 的 monitors（list）。

    逐键走 coerce_setting，所以新增一个设置**不用**再来这里加一行校验。
    """
    cfg = default_config()
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
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
                for key in SETTING_BY_KEY:
                    if key in raw:
                        cfg[key] = coerce_setting(key, raw[key])
                cfg["models"] = _load_model_cache(raw)
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = dict(cfg)
        payload["version"] = CONFIG_VERSION
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True, ""
    except Exception as e:
        return False, str(e)


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
# 二、亮度控制（DDC/CI）
# ============================================================
NO_MONITOR_MSG = (
    "未检测到支持 DDC/CI 的显示器。\n请确认：\n"
    "1. 显示器 OSD 菜单已开启 DDC/CI\n"
    "2. 使用 HDMI / DP / DVI 直连（避免 KVM / USB 转接）"
)


def list_monitors():
    if get_monitors is None:
        return []
    try:
        return list(get_monitors())
    except Exception:
        return []


def enum_displays():
    """用 Win32 枚举显示设备，返回 [(设备名, 宽, 高, 是否主屏), ...]。

    顺序与 monitorcontrol 内部一致（它也是走 EnumDisplayMonitors + 逐个取物理显示器），
    所以可以直接按下标贴到显示器列表上，用来区分同型号的屏幕。
    """
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32)]

        out = []

        def _cb(hmon, hdc, lprect, lparam):
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(mi)
            if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                rc = mi.rcMonitor
                name = str(mi.szDevice).split("\\")[-1] or str(mi.szDevice)
                out.append((name, rc.right - rc.left, rc.bottom - rc.top,
                            bool(mi.dwFlags & 1)))
            return 1

        proc = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(_cb), None)
        return out
    except Exception:
        return []


def monitor_model(monitor):
    """从 MCCS 能力串里取显示器型号（如 'Legion R27qe Gen2'）。取不到就返回 None。"""
    try:
        with monitor:
            caps = monitor.get_vcp_capabilities()
        if isinstance(caps, dict):
            model = caps.get("model")
            if model:
                return str(model).strip()
    except Exception:
        pass
    return None


def monitor_label(index, monitor, info=None, model=None):
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


def set_brightness_many(indices, value):
    """批量设亮度。indices 为空 → 全部显示器。返回 (成功的序号列表, [(序号, 错误), ...])。"""
    monitors = list_monitors()
    if not monitors:
        raise RuntimeError(NO_MONITOR_MSG)
    targets = list(indices) if indices else list(range(len(monitors)))
    level = clamp_level(value)
    ok, errs = [], []
    for i in targets:
        if i < 0 or i >= len(monitors):
            errs.append((i, "序号超出范围"))
            continue
        try:
            with monitors[i] as m:
                m.set_luminance(level)
            ok.append(i)
        except Exception as e:
            errs.append((i, str(e)))
    return ok, errs


def get_brightness_many(indices):
    """批量读亮度。返回 ({序号: 亮度 or None}, [(序号, 错误), ...])。"""
    monitors = list_monitors()
    if not monitors:
        raise RuntimeError(NO_MONITOR_MSG)
    targets = list(indices) if indices else list(range(len(monitors)))
    values, errs = {}, []
    for i in targets:
        if i < 0 or i >= len(monitors):
            errs.append((i, "序号超出范围"))
            continue
        try:
            with monitors[i] as m:
                values[i] = m.get_luminance()
        except Exception as e:
            values[i] = None
            errs.append((i, str(e)))
    return values, errs


# ---- 熄屏 / 点亮（S6）----------------------------------------------------
# ⚠️ 硬件红线（本机 Legion R27qe x2 实测事故）：0xD6=4/5（软关/硬关）后面板进入
# 固件级熄灭，DDC 点亮/驱动重启/拓扑重握手/信号源切换全部唤不醒，只有物理电源键能救；
# 且 0xD6 回读值与面板实际状态不符。故熄灭只用 2（待机），点亮只用信号源切换（0x60）。

def _physical_handles():
    """枚举物理显示器句柄（DXVA2）。顺序与 list_monitors() 一致（同为 EnumDisplayMonitors）。"""
    dxva2 = ctypes.windll.dxva2
    user32 = ctypes.windll.user32
    cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                 ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    class _PHYS(ctypes.Structure):
        _fields_ = [("handle", wintypes.HANDLE), ("desc", wintypes.WCHAR * 128)]

    out = []

    def _cb(hmon, hdc, lprect, lparam):
        count = wintypes.DWORD()
        if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
            arr = (_PHYS * count.value)()
            if dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
                out.extend(p.handle for p in arr)
        return True

    user32.EnumDisplayMonitors(None, None, cb_type(_cb), 0)
    return out


def screen_off_many(indices):
    """批量熄灭（0xD6=2 待机）。indices 空 -> 全部。返回 (成功序号, [(序号, 错误), ...])。"""
    handles = _physical_handles()
    if not handles:
        raise RuntimeError(NO_MONITOR_MSG)
    targets = list(indices) if indices else list(range(len(handles)))
    ok, errs = [], []
    for i in targets:
        if i < 0 or i >= len(handles):
            errs.append((i, "序号超出范围"))
            continue
        if ctypes.windll.dxva2.SetVCPFeature(handles[i], wintypes.BYTE(0xD6), wintypes.DWORD(2)):
            ok.append(i)
        else:
            errs.append((i, "SetVCPFeature 失败"))
    return ok, errs


def screen_wake_many(indices):
    """批量点亮（0x60 信号源：切到另一输入口 3 秒再切回）。

    本机实测：0xD6 写回点亮不生效（回读还是假的 on），信号源切换是唯一
    被单变量验证可靠的软件唤醒路径（端到端 2/2 成功）。
    """
    handles = _physical_handles()
    if not handles:
        raise RuntimeError(NO_MONITOR_MSG)
    targets = list(indices) if indices else list(range(len(handles)))
    dxva2 = ctypes.windll.dxva2
    ok, errs = [], []
    for i in targets:
        if i < 0 or i >= len(handles):
            errs.append((i, "序号超出范围"))
            continue
        h = handles[i]
        cur, mx = wintypes.DWORD(), wintypes.DWORD()
        if not dxva2.GetVCPFeatureAndVCPFeatureReply(h, wintypes.BYTE(0x60), None,
                                                     ctypes.byref(cur), ctypes.byref(mx)):
            errs.append((i, "读信号源失败"))
            continue
        cur = cur.value
        other = 17 if cur != 17 else 4
        if not dxva2.SetVCPFeature(h, wintypes.BYTE(0x60), wintypes.DWORD(other)):
            errs.append((i, "切换信号源失败"))
            continue
        time.sleep(3)
        if not dxva2.SetVCPFeature(h, wintypes.BYTE(0x60), wintypes.DWORD(cur)):
            errs.append((i, "切回信号源失败"))
            continue
        ok.append(i)
    return ok, errs


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


def apply_schedule(cfg=None):
    """按当前时间套用对应档位（亮度 + 该档的屏幕动作）。

    返回 (level, screen, ok, errs)；screen 为该档屏幕动作（""/off/on）。
    屏幕动作失败的错误以序号 -1 记入 errs（界面按原文显示）。
    """
    cfg = cfg or load_config()
    slot = active_slot_dict(cfg.get("slots", []), _minutes())
    level = slot.get("level", 50)
    screen = slot.get("screen", "")
    ok, errs = set_brightness_many(cfg.get("monitors"), level)
    if screen == "off":
        sok, serrs = screen_off_many(cfg.get("monitors"))
    elif screen == "on":
        sok, serrs = screen_wake_many(cfg.get("monitors"))
    else:
        sok, serrs = [], []
    ok = ok + sok
    errs = errs + [(-1, "屏幕 %d: %s" % (i, e)) for i, e in serrs]
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


def build_register_script(cfg):
    """生成注册脚本：一条定时任务挂 N 个 Daily 触发器（每档一个），CatchUp 登录任务保留。

    一条任务多触发器是任务计划程序的标准能力（-Trigger 接受触发器数组）。
    """
    exe, prefix = task_target()
    arg = (prefix + " --apply-schedule").strip()
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
        # 旧任务名（v1.2.x 及更早的 Day/Night 分任务）一并清掉，避免残留
        "foreach ($n in @(%s)) { Unregister-ScheduledTask -TaskName $n "
        "-Confirm:$false -ErrorAction SilentlyContinue }"
        % ", ".join(_ps_quote(x) for x in (TASK_SCHEDULE, TASK_CATCHUP) + _LEGACY_TASKS),
    ]
    for i, x in enumerate(slots):
        lines.append("$t%d = New-ScheduledTaskTrigger -Daily -At %s"
                     % (i, _ps_quote(x["time"])))
    lines.append(
        "Register-ScheduledTask -TaskName '%s' -Action $action -Trigger @(%s) "
        "-Settings $settings -Description %s | Out-Null"
        % (TASK_SCHEDULE, ", ".join("$t%d" % i for i in range(len(slots))),
           _ps_quote("MonitorBrightness · %d 个时间段（到点切换亮度）" % len(slots))))
    if cfg.get("catch_up_on_logon"):
        # 单独 try/catch：这条失败也不能拖垮定时任务，失败原因要能带回界面
        lines += [
            "$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $me",
            "$tLogon.Delay = 'PT30S'",
            "try { Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $tLogon "
            "-Settings $settings -Description 'MonitorBrightness: 登录后按当前时间校准亮度"
            "（补上错过的切换点）' -ErrorAction Stop | Out-Null; "
            "Write-Output 'CATCHUP=OK' } "
            "catch { Write-Output ('CATCHUP=FAIL ' + $_.Exception.Message) }" % TASK_CATCHUP,
        ]
    lines += [
        "$t = Get-ScheduledTask -TaskName '%s'" % TASK_SCHEDULE,
        "Write-Output ('StartWhenAvailable=' + $t.Settings.StartWhenAvailable)",
        "Write-Output ('Triggers=' + $t.Triggers.Count)",
        "Write-Output ('NextRun=' + (Get-ScheduledTaskInfo -TaskName '%s').NextRunTime)" % TASK_SCHEDULE,
        "$g = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % TASK_CATCHUP,
        "if ($g) { Write-Output ('CatchUpTask=' + $g.State) } else { Write-Output 'CatchUpTask=MISSING' }",
        "Write-Output 'REGISTER_OK'",
    ]
    return _ps_script(lines)


def register_tasks(cfg):
    """注册（覆盖）定时任务。返回 (是否成功, 说明文本)。

    ⚠️ S5 施工时本函数曾被"按锚点整段替换"误删（它恰好在两个 build_* 之间），
    App 在保存时调用它 -> NameError。现恢复，并由自测的"未定义名检查"守卫。
    """
    code, out = _ps(build_register_script(cfg))
    if code == 0 and "REGISTER_OK" in out:
        return True, out
    return False, out or ("注册子进程退出码 %s" % code)


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
TASKS_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Tasks")


def _xml_flag(block, tag):
    """从 XML 片段里取布尔标记；取不到返回 None。"""
    m = re.search(r"<%s>(true|false)</%s>" % (tag, tag), block or "", re.I)
    return (m.group(1).lower() == "true") if m else None


def _settings_block(xml_text):
    """只取 <Settings>…</Settings>：<Enabled> 在触发器里也有，必须限定作用域。"""
    m = re.search(r"<Settings>(.*?)</Settings>", xml_text or "", re.S)
    return m.group(1) if m else ""


def _boundary_minutes_all(xml_text):
    """任务 XML 里全部触发器的 StartBoundary（本地当日分钟列表）。"""
    out = []
    for m in re.finditer(r"<StartBoundary>([^<]+)</StartBoundary>", xml_text or ""):
        b = m.group(1).strip()
        parts = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", b)
        if not parts:
            continue
        y, mo, d, h, mi, sec = (int(v) for v in parts.groups())
        try:
            dt = (datetime(y, mo, d, h, mi, sec, tzinfo=timezone.utc).astimezone()
                  if b.endswith("Z") else datetime(y, mo, d, h, mi, sec))
        except Exception:
            continue
        out.append(dt.hour * 60 + dt.minute)
    return out


def _next_run_text(minutes_list, now=None):
    """多触发器取「离现在最近的下一次」；没有未来的（都被错过）取最早的一档（明天）。"""
    if not minutes_list:
        return ""
    now_dt = now if now is not None else datetime.now()
    now_min = now_dt.hour * 60 + now_dt.minute
    future = [m for m in minutes_list if m > now_min]
    target = min(future) if future else min(minutes_list)
    day = "今天" if target > now_min else "明天"
    return "%s %02d:%02d" % (day, target // 60, target % 60)


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
        nxt = "" if name == TASK_CATCHUP else _next_run_text(_boundary_minutes_all(txt))
        out.append("%s=%s|%s|SWA=%s" % (name, state, nxt, swa))
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


def _format_task_line(name, short, rest):
    """把一条任务状态渲染成人读的一行；rest 为 None 表示这次查询没拿到它。"""
    if rest is None:
        return "%s：读取失败" % short
    if rest == "MISSING":
        return "%s：未注册" % short
    bits = rest.split("|")
    state = "已启用" if bits[0] == "Ready" else bits[0]
    nxt = (bits[1] if len(bits) > 1 else "").strip()
    swa = "补跑已开" if any("True" in b for b in bits[2:]) else "补跑未开"
    if name == TASK_CATCHUP:
        # 登录触发没有"下次运行时间"，显示成校准时机更有意义
        return "%s：%s · 登录时校准 · %s" % (short, state, swa)
    return "%s：%s · 下次 %s · %s" % (short, state, nxt or "—", swa)


def _as_ok_errs(result):
    """把 run_bg 的结果统一成 (成功列表, 失败列表)。

    job 抛异常时 run_bg 把异常对象原样传回，这里转成一条 (-1, 原因) 失败项，
    免得每个 on_done 都要写一遍 isinstance 判断。
    """
    if isinstance(result, Exception):
        return [], [(-1, str(result))]
    return result


def _saved_summary(part, out):
    """注册成功后的提示正文（抽成函数，让回调保持短小）。"""
    lines = ["定时任务已写入 Windows 任务计划程序："]
    lines += ["• %s → %d%%%s" % (x["time"], x["level"],
              "（同时熄屏）" if x.get("screen") == "off" else
              "（同时点亮）" if x.get("screen") == "on" else "")
              for x in part.get("slots", [])]
    if not part["catch_up_on_logon"]:
        lines.append("• 登录后自动校准：未开启")
    elif "CATCHUP=FAIL" in (out or ""):
        reason = out.split("CATCHUP=FAIL", 1)[1].strip().splitlines()[0].strip()
        lines.append("• 登录后自动校准：注册失败")
        lines.append("  原因：%s" % reason)
    else:
        lines.append("• 登录后自动校准：已注册")
    lines += ["",
              "已开启「错过后尽快补跑」：电脑关机/睡眠错过时间点，开机后也会自动补上。",
              "本程序无需常驻后台。"]
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
    try:
        level, screen, ok, errs = apply_schedule()
        for i, e in errs:
            _err("显示器 %s: %s" % (i, e))
        if screen:
            _out("屏幕动作: %s" % screen)
        sys.exit(0 if ok else 1)
    except Exception as e:
        _err(e)
        sys.exit(1)


def cli_list():
    monitors = list_monitors()
    if not monitors:
        _err(NO_MONITOR_MSG)
        sys.exit(1)
    info = enum_displays()
    for i, m in enumerate(monitors):
        _out(monitor_label(i, m, info[i] if i < len(info) else None))
    sys.exit(0)


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
# 七、图形界面（tkinter，深色）
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
    """生成一张勾选框指示器图片（纯代码绘制，不依赖外部图片文件）。"""
    img = tk.PhotoImage(width=size, height=size)
    img.put(fill, to=(0, 0, size, size))
    img.put(border, to=(0, 0, size, 1))
    img.put(border, to=(0, size - 1, size, size))
    img.put(border, to=(0, 0, 1, size))
    img.put(border, to=(size - 1, 0, size, size))
    for (x, y) in (mark or ()):
        if 0 <= x < size and 0 <= y < size:
            img.put(mark_color, to=(x, y, x + 1, y + 1))
    _TICKBOX_IMAGES.append(img)
    return img


def install_dark_tickbox(style, root=None):
    """把 TCheckbutton 的指示器换成自绘图片，得到一个能正常显示的深色勾选框。

    为什么必须这么做（都有依据，不是猜的）：
      - Tk 官方手册里 TCheckbutton 可配置的选项只有 background / foreground /
        indicatorbackground / indicatorcolor / indicatormargin / indicatorrelief / padding；
      - 本机 Tk 自带的 ttk/clamTheme.tcl 对勾选框**只开放 -indicatorbackground**
        （默认值还是 #ffffff 白底），选中态那个图形是 C 层绘制的，
        改 indicatorcolor 无效（像素实测：选中态没有任何白色像素），
        深色底下几乎看不清勾没勾；
      - 换 alt 主题能画出正常对勾，但它的指示器底色不吃深色配置。
    → ttk 官方的正当做法（ttk::style 的 element_create / layout）是用自己的图片
      替换 Checkbutton.indicator 元素。这样仍然是**真正的 ttk.Checkbutton**，
      变量绑定、键盘空格、禁用态等行为都保留。
    """
    D = DARK
    size = 14
    img_off = _tickbox_image(size, D["field"], D["border"])
    img_on = _tickbox_image(size, D["accent"], D["accent"],
                            mark=_raster_line(3.5, 7.5, 6.0, 10.0) |
                                 _raster_line(6.0, 10.0, 10.5, 4.5),
                            mark_color=D["accent_fg"])
    img_off_dim = _tickbox_image(size, D["surface"], D["border"])
    img_on_dim = _tickbox_image(size, D["border"], D["border"],
                                mark=_raster_line(3.5, 7.5, 6.0, 10.0) |
                                     _raster_line(6.0, 10.0, 10.5, 4.5),
                                mark_color=D["fg_dim"])

    try:
        style.element_create("Dark.tickbox", "image", img_off,
                             ("selected", img_on),
                             ("disabled", img_off_dim),
                             ("selected", "disabled", img_on_dim))
    except Exception:
        pass   # 同一解释器里重复注册会报"已存在"，忽略即可
    # 间距走文档里的 indicatormargin（左 上 右 下），不能给 Checkbutton.label 塞 padding
    style.configure("TCheckbutton", background=D["surface"], foreground=D["fg"],
                    indicatormargin=(1, 1, 8, 1), padding=2)
    style.map("TCheckbutton",
              background=[("active", D["surface"])],
              foreground=[("disabled", D["fg_dim"])])
    style.layout("TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Dark.tickbox", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])


class DarkBox:
    """深色模态消息框：messagebox 的替代品。

    原因：messagebox 是 Windows 原生对话框，不吃 ttk 深色主题；
    实测 SetPreferredAppMode(ForceDark) 只能暗标题栏、内容区仍为浅色（A/B 截图）。
    模态模式（Toplevel + grab_set + wait_window）与 CustomTkinter 的对话框一致。
    """

    _ICONS = {"info": ("\u2139", "#4a9eff"),
              "warn": ("\u26a0", "#e5c07b"),
              "error": ("\u2715", "#e06c75")}

    def __init__(self, root):
        self.root = root

    def _show(self, kind, title, message):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=DARK["surface"])
        apply_dark_titlebar(dlg)
        icon, color = self._ICONS[kind]
        body = tk.Frame(dlg, background=DARK["surface"])
        body.pack(fill="both", expand=True, padx=20, pady=(18, 4))
        tk.Label(body, text=icon, fg=color, bg=DARK["surface"],
                 font=("Segoe UI Symbol", 18)).pack(side="left", padx=(0, 14))
        tk.Label(body, text=message, fg=DARK["fg"], bg=DARK["surface"],
                 font=("Microsoft YaHei UI", 10), justify="left",
                 wraplength=400).pack(side="left")
        btn = ttk.Button(dlg, text="确定", style="Accent.TButton", command=dlg.destroy)
        btn.pack(pady=(6, 16))
        btn.focus_set()
        dlg.bind("<Return>", lambda e: dlg.destroy())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.transient(self.root)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width()
                                              - dlg.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height()
                                              - dlg.winfo_height()) // 2)
        dlg.geometry("+%d+%d" % (x, y))
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()

    def showinfo(self, title, message):
        self._show("info", title, message)

    def showwarning(self, title, message):
        self._show("warn", title, message)

    def showerror(self, title, message):
        self._show("error", title, message)


class MonitorPanel(ttk.LabelFrame):
    """显示器勾选区（View）：只管勾选行的创建与显示，业务在 App。"""

    def __init__(self, app):
        super().__init__(app.root, text=" 显示器（可多选） ")
        self.app = app
        self.pack(fill="x", padx=8, pady=4)
        self.mon_vars = {}      # 序号 -> BooleanVar
        self.mon_rows = {}      # 序号 -> Checkbutton（型号名到达时就地改文字）
        self._suspend = False   # 重建期间挂起勾选回调
        row = ttk.Frame(self, style="Surface.TFrame")
        row.pack(fill="x", padx=8, pady=(8, 2))
        self.mon_box = ttk.Frame(row, style="Surface.TFrame")
        self.mon_box.pack(side="left", fill="both", expand=True)
        btns = ttk.Frame(row, style="Surface.TFrame")
        btns.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(btns, text="刷新", command=app.refresh_monitors, width=8).pack(fill="x")
        ttk.Button(btns, text="全选", command=app.select_all, width=8).pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="全不选", command=app.select_none, width=8).pack(fill="x", pady=(4, 0))
        ttk.Label(self, text="勾选要调节的显示器；一个都不勾 = 全部显示器",
                  style="Hint.TLabel").pack(anchor="w", padx=10, pady=(2, 8))

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
        """按 app.monitors / mon_info / mon_models 重建勾选行（挂起回调）。"""
        self._suspend = True
        try:
            for w in self.mon_box.winfo_children():
                w.destroy()
            self.mon_vars, self.mon_rows = {}, {}
            app = self.app
            if not app.monitors:
                ttk.Label(self.mon_box, text="（未检测到支持 DDC/CI 的显示器）",
                          style="Hint.TLabel").pack(anchor="w")
                return
            for i in range(len(app.monitors)):
                info = app.mon_info[i] if i < len(app.mon_info) else None
                var = tk.BooleanVar(value=(i in keep))
                var.trace_add("write", lambda *_: app.on_monitor_toggle())
                self.mon_vars[i] = var
                row = ttk.Checkbutton(self.mon_box, variable=var, text=monitor_label(
                    i, app.monitors[i], info, app.mon_models.get(i)))
                row.pack(anchor="w", pady=1)
                self.mon_rows[i] = row
        finally:
            self._suspend = False

    def set_row_text(self, i, text):
        row = self.mon_rows.get(i)
        if row is not None:
            try:
                row.config(text=text)
            except Exception:
                pass


class BrightnessPanel(ttk.LabelFrame):
    """亮度调节区（View）：滑块与"当前亮度"一行。"""

    def __init__(self, app, initial_level):
        super().__init__(app.root, text=" 亮度调节（即时生效） ")
        self.pack(fill="x", padx=8, pady=4)
        self.cur_lbl = ttk.Label(self, text="当前亮度: --", style="Hint.TLabel")
        self.cur_lbl.pack(anchor="w", padx=10, pady=(8, 0))
        srow = ttk.Frame(self, style="Surface.TFrame")
        srow.pack(fill="x", padx=10, pady=6)
        self.slider = ttk.Scale(srow, from_=0, to=100, orient="horizontal",
                                command=self._on_slide)
        self.slider.set(initial_level)
        self.slider.pack(side="left", fill="x", expand=True)
        self.level_lbl = ttk.Label(srow, text=str(initial_level), width=4,
                                   anchor="e", style="Hint.TLabel")
        self.level_lbl.pack(side="left", padx=(8, 0))
        brow = ttk.Frame(self, style="Surface.TFrame")
        brow.pack(fill="x", padx=10, pady=(0, 8))
        self.apply_btn = ttk.Button(brow, text="应用到选中显示器", style="Accent.TButton",
                                    command=app.apply_now)
        self.apply_btn.pack(side="left")
        self.read_btn = ttk.Button(brow, text="读取当前亮度", command=app.read_current)
        self.read_btn.pack(side="left", padx=6)

    def _on_slide(self, _=None):
        try:
            self.level_lbl.config(text=str(int(float(self.slider.get()))))
        except Exception:
            pass

    def level(self):
        return clamp_level(self.slider.get())

    def set_current(self, text):
        self.cur_lbl.config(text=text)

    def set_actions_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in (self.apply_btn, self.read_btn):
            try:
                btn.config(state=state)
            except Exception:
                pass


class SchedulePanel(ttk.LabelFrame):
    """定时调节区（View）：动态 N 行时间段（每行 时间+亮度+删除），底部「添加时间段」。"""

    def __init__(self, app, cfg):
        super().__init__(app.root, text=" 定时调节 ")
        self.app = app
        self.pack(fill="x", padx=8, pady=4)
        self.enabled_var = tk.BooleanVar(value=bool(cfg.get("enabled", False)))
        self.catchup_var = tk.BooleanVar(value=bool(cfg.get("catch_up_on_logon", True)))
        self._rows = []          # [{"frame","entry","spin","del_btn"}]
        ttk.Checkbutton(self, text="启用每日定时", variable=self.enabled_var,
                        command=self.set_enabled_state).pack(anchor="w", padx=10, pady=(8, 2))
        self.rows_box = ttk.Frame(self, style="Surface.TFrame")
        self.rows_box.pack(fill="x", padx=10, pady=2)
        hint = ttk.Frame(self, style="Surface.TFrame")
        hint.pack(fill="x", padx=10)
        self.add_btn = ttk.Button(hint, text="+ 添加时间段", width=12, command=self.add_row)
        self.add_btn.pack(side="left")
        ttk.Label(hint, text="同一时刻只能有一档；到点切换为该档亮度",
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))
        for x in cfg.get("slots", []):
            self.add_row(x.get("time", "12:00"), x.get("level", 50),
                         x.get("screen", ""))
        self.catchup_chk = ttk.Checkbutton(
            self, text="登录后自动校准（开机时已过切换点也能补上）", variable=self.catchup_var)
        self.catchup_chk.pack(anchor="w", padx=10, pady=(4, 0))
        r_btn = ttk.Frame(self, style="Surface.TFrame")
        r_btn.pack(fill="x", padx=10, pady=(6, 4))
        self.save_btn = ttk.Button(r_btn, text="保存并启用定时", style="Accent.TButton",
                                   command=app.save_schedule)
        self.save_btn.pack(side="left")
        self.test_btn = ttk.Button(r_btn, text="测试当前档", command=app.test_current)
        self.test_btn.pack(side="left", padx=4)
        self.cancel_btn = ttk.Button(r_btn, text="取消定时", command=app.cancel_schedule)
        self.cancel_btn.pack(side="left", padx=4)
        # 状态区固定 4 行占位：异步结果回来时窗口高度才不会跳
        self.task_lbl = ttk.Label(self, justify="left", style="Hint.TLabel", text=(
            "任务计划状态：\n定时：读取中…\n登录校准：读取中…"))
        self.task_lbl.pack(anchor="w", padx=10, pady=(0, 8))
        self.set_enabled_state()

    def _build_row(self, time_text, level_value, screen_value=""):
        row = ttk.Frame(self.rows_box, style="Surface.TFrame")
        row.pack(fill="x", pady=1)
        entry = ttk.Entry(row, width=7, justify="center")
        entry.insert(0, str(time_text))
        entry.pack(side="left")
        ttk.Label(row, text="亮度", style="Hint.TLabel").pack(side="left", padx=(8, 0))
        spin = ttk.Spinbox(row, from_=0, to=100, width=5, justify="center")
        spin.set(level_value)
        spin.pack(side="left", padx=4)
        ttk.Label(row, text="屏幕", style="Hint.TLabel").pack(side="left", padx=(8, 0))
        combo = ttk.Combobox(row, values=[l for _v, l in SCREEN_ACTIONS],
                             state="readonly", width=5)
        combo.set({label: value for value, label in SCREEN_ACTIONS}.get(
            screen_value, "不变"))
        combo.pack(side="left", padx=4)
        del_btn = ttk.Button(row, text="删除", width=6,
                             command=lambda: self._del_row(row))
        del_btn.pack(side="left", padx=6)
        rec = {"frame": row, "entry": entry, "spin": spin, "combo": combo,
               "del_btn": del_btn}
        self._rows.append(rec)
        return rec

    def add_row(self, time_text="12:00", level_value=50, screen_value=""):
        rec = self._build_row(time_text, level_value, screen_value)
        self.set_enabled_state()
        return rec

    def _del_row(self, row_widget):
        if len(self._rows) <= 1:      # 至少保留一档
            return
        for r in self._rows:
            if r["frame"] is row_widget:
                row_widget.destroy()
                self._rows.remove(r)
                break

    def set_enabled_state(self):
        state = "normal" if self.enabled_var.get() else "disabled"
        widgets = [w for r in self._rows for w in (r["entry"], r["spin"], r["del_btn"])]
        # 按钮在 __init__ 里晚于数据行创建，用 getattr 兜住"尚不存在"的阶段
        widgets += [w for w in (getattr(self, n, None) for n in
                                ("add_btn", "save_btn", "test_btn", "cancel_btn"))
                    if w is not None]
        for w in widgets:
            try:
                w.config(state=state)
            except Exception:
                pass

    def set_enabled_flag(self, value):
        self.enabled_var.set(bool(value))
        self.set_enabled_state()

    def schedule_enabled(self):
        return bool(self.enabled_var.get())

    def read_slots(self):
        """读全部行 -> [{"time","level"}]（按时间排序）；非法/重复时间弹窗并返回 None。"""
        out, seen = [], set()
        for r in self._rows:
            t = r["entry"].get().strip()
            if not _valid_hhmm(t):
                self.app.msg.showerror("格式不对",
                                     "时间需要写成 HH:MM，例如 09:00。当前是：%s" % t)
                return None
            if t in seen:
                self.app.msg.showerror("时间重复", "同一时刻 %s 只能有一档。" % t)
                return None
            seen.add(t)
            try:
                lv = max(0, min(100, int(float(r["spin"].get()))))
            except Exception:
                lv = 50
            out.append({"time": t, "level": lv,
                        "screen": SCREEN_VALUE_BY_LABEL.get(r["combo"].get(), "")})
        if not out:
            self.app.msg.showinfo("提示", "至少保留一个时间段。")
            return None
        return sorted(out, key=lambda x: parse_hhmm(x["time"], (0, 0)))

    def set_task_status(self, text):
        self.task_lbl.config(text=text)

    def set_actions_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        widgets = [self.save_btn, self.test_btn, self.cancel_btn]
        for r in self._rows:
            widgets += [r["entry"], r["spin"], r["del_btn"]]
        for w in widgets:
            try:
                w.config(state=state)
            except Exception:
                pass


class ScreenPanel(ttk.LabelFrame):
    """熄屏/点亮区（View）：按钮、热键开关与自动点亮保险丝，业务在 App。"""

    def __init__(self, app, cfg):
        super().__init__(app.root, text=" 熄屏 / 点亮 / 托盘 ")
        self.app = app
        self.pack(fill="x", padx=8, pady=4)
        row = ttk.Frame(self, style="Surface.TFrame")
        row.pack(fill="x", padx=10, pady=(8, 2))
        self.off_btn = ttk.Button(row, text="熄灭显示器", command=app.screen_off)
        self.off_btn.pack(side="left")
        self.on_btn = ttk.Button(row, text="点亮显示器", command=app.screen_on)
        self.on_btn.pack(side="left", padx=6)
        ttk.Label(row, text="点亮时信号源会自动切换一次（亮着的屏闪 3 秒，正常现象）",
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))
        hk = ttk.Frame(self, style="Surface.TFrame")
        hk.pack(fill="x", padx=10)
        self.hotkey_var = tk.BooleanVar(value=bool(cfg.get("screen_hotkeys", False)))
        ttk.Checkbutton(hk, text="启用热键：Ctrl+Alt+Shift+O 熄灭 / P 点亮（需保持本程序运行）",
                        variable=self.hotkey_var,
                        command=app.toggle_screen_hotkeys).pack(anchor="w")
        ar = ttk.Frame(self, style="Surface.TFrame")
        ar.pack(fill="x", padx=10, pady=(2, 8))
        ttk.Label(ar, text="熄灭后", style="Hint.TLabel").pack(side="left")
        self.relight_spin = ttk.Spinbox(ar, from_=0, to=120, width=5, justify="center")
        self.relight_spin.set(cfg.get("screen_auto_relight_min", 0))
        self.relight_spin.pack(side="left", padx=4)
        ttk.Label(ar, text="分钟自动点亮（0 = 不自动；远程时建议设个值防失联）",
                  style="Hint.TLabel").pack(side="left")
        au = ttk.Frame(self, style="Surface.TFrame")
        au.pack(fill="x", padx=10, pady=(2, 0))
        self.autostart_var = tk.BooleanVar(value=get_autostart()[0])
        ttk.Checkbutton(au, text="开机自动启动（启动后直接进托盘；点 X 也是进托盘，退出在托盘菜单）",
                        variable=self.autostart_var,
                        command=app.toggle_autostart).pack(anchor="w")

    def set_actions_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in (self.off_btn, self.on_btn):
            try:
                btn.config(state=state)
            except Exception:
                pass


class App:
    """协调者（Presenter）：服务调用、异步编排与弹窗；界面细节都在三个 Panel 里。"""

    def __init__(self, root, start_minimized=False):
        self.root = root
        self.tray = None            # pystray.Icon（延迟创建，见 _create_tray）
        root.title("%s v%s" % (APP_TITLE, APP_VERSION))
        root.resizable(False, False)
        apply_dark_theme(root)

        self.cfg = load_config()
        self.monitors = []
        self.mon_info = []      # Win32 枚举信息，下标与 self.monitors 对齐
        # 序号 -> 型号名；先吃上次缓存（毫秒级），DDC 后台刷新只补缺的
        self.mon_models = {int(k): v for k, v in self.cfg.get("models", {}).items()}
        self._read_queue = queue.Queue()   # 后台线程 → 主线程 的唯一通道
        self._bg_tasks = 0                 # 在跑的后台任务数（归零就停轮询）
        self._reading = False
        self._read_pending = False
        self._busy = False
        self._task_querying = False
        self._hotkeys_on = False      # 热键监听线程运行中（保持轮询活着的条件之一）
        self._relight_pending = False # 有定时自动点亮在等（同上）
        self._hotkey_tid = None       # 热键线程 id（用于 PostThreadMessage 退出）
        self.status_var = tk.StringVar(value="就绪")
        self.msg = DarkBox(self.root)     # 深色弹窗（替代 messagebox）

        self.mon_panel = MonitorPanel(self)
        self.bri_panel = BrightnessPanel(self, current_level(self.cfg))
        self.sch_panel = SchedulePanel(self, self.cfg)
        self.screen_panel = ScreenPanel(self, self.cfg)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.cfg.get("screen_hotkeys"):
            self._start_hotkey_listener()
        ttk.Label(self.root, textvariable=self.status_var, style="Dim.TLabel").pack(
            anchor="w", padx=12, pady=(0, 8))

        self.refresh_monitors(silent=True)
        self._refresh_task_status()
        self._create_tray()
        if start_minimized and self.tray is not None:
            self.root.withdraw()

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
        """某一台被勾选/取消勾选 → 重读一次当前亮度（重建列表期间不触发）。"""
        if not self.mon_panel._suspend:
            self.read_current()

    def _restore_selection(self, prev):
        """刷新后该勾选哪几台：优先沿用刷新前的勾选，其次配置里的，最后全勾。"""
        keep = [i for i in prev if i < len(self.monitors)]
        if not keep:
            keep = [i for i in self.cfg.get("monitors", []) if i < len(self.monitors)]
        return keep or list(range(len(self.monitors)))

    def refresh_monitors(self, silent=False):
        prev = self.selected_indices()
        self.monitors = list_monitors()
        self.mon_info = enum_displays()
        self.mon_panel.rebuild(self._restore_selection(prev))
        if not self.monitors:
            self.bri_panel.set_current("当前亮度: --")
            self.status_var.set("未检测到显示器：请确认显示器 OSD 已开 DDC/CI，且用 HDMI/DP/DVI 直连")
            return
        if not silent:
            self.status_var.set("已刷新：检测到 %d 台显示器" % len(self.monitors))
        self.read_current()

    def _apply_models(self, models):
        """型号名到达后就地改那一行文字，并写回缓存（下次启动秒出）。"""
        if isinstance(models, Exception):
            return
        changed = False
        for i, name in models.items():
            if not name or self.mon_models.get(i) == name or i >= len(self.monitors):
                continue
            self.mon_models[i] = name
            info = self.mon_info[i] if i < len(self.mon_info) else None
            self.mon_panel.set_row_text(
                i, monitor_label(i, self.monitors[i], info, name))
            changed = True
        if changed:
            self.cfg["models"] = {str(k): v for k, v in self.mon_models.items() if v}
            save_config(self.cfg)

    # ---------- 统一异步入口 ----------
    def run_bg(self, job, on_done=None, busy=False):
        """全程序唯一的异步写法：job 在后台跑，返回值/异常交给 on_done 在主线程处理。

        busy=True 期间禁用动作按钮，结束后自动恢复（防连点起一串线程）。
        """
        if busy:
            self._busy = True
            self._set_actions_enabled(False)
        self._bg_tasks += 1

        def runner():
            try:
                result = job()
            except Exception as e:
                result = e          # 异常也回主线程处理，不在后台静默吞掉
            self._read_queue.put(("done", (on_done, result, busy), None))

        threading.Thread(target=runner, daemon=True).start()
        self._poll_read_queue()

    def _poll_read_queue(self):
        """主线程轮询：把后台结果搬回界面（界面只在这里被改）。"""
        while True:
            try:
                kind, payload, _extra = self._read_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "hotkey":
                (self.screen_off if payload == "off" else self.screen_on)()
                continue
            if kind == "hotkey_error":
                self.status_var.set(payload)
                continue
            if kind != "done":
                continue
            on_done, result, busy = payload
            self._bg_tasks = max(0, self._bg_tasks - 1)
            if busy:
                self._busy = False
                self._set_actions_enabled(True)
            if on_done is not None:
                on_done(result)
        if self._bg_tasks > 0 or self._hotkeys_on or self._relight_pending:
            self.root.after(150, self._poll_read_queue)
        elif self._read_pending:
            self._read_pending = False
            self.root.after(50, self.read_current)

    def _set_actions_enabled(self, enabled):
        self.bri_panel.set_actions_enabled(enabled)
        self.sch_panel.set_actions_enabled(enabled)
        self.screen_panel.set_actions_enabled(enabled)

    # ---------- 亮度 ----------
    def read_current(self):
        """后台读亮度 + 补型号名（两者拆开：读亮度 ~0.13s，读型号 ~3s/台）。"""
        if not self.monitors:
            return
        if self._reading:
            self._read_pending = True
            return
        targets = self.selected_indices() or list(range(len(self.monitors)))
        self._reading = True
        self._read_pending = False
        self.bri_panel.set_current("当前亮度: 读取中…")

        def job():
            try:
                return get_brightness_many(targets)[0]
            except Exception:
                return {}

        def done(values):
            self._reading = False
            self._render_values(values, targets)

        self.run_bg(job, done)

        missing = [i for i in targets
                   if i not in self.mon_models and 0 <= i < len(self.monitors)]
        if missing:
            def job_models():
                """每台一个线程并行读型号（不同显示器 = 不同设备句柄，安全）。"""
                out = {}

                def one(i):
                    out[i] = monitor_model(self.monitors[i])

                ts = [threading.Thread(target=one, args=(i,)) for i in missing]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join(15)
                return {i: v for i, v in out.items() if v}

            self.run_bg(job_models, self._apply_models)

    def _render_values(self, values, targets):
        if values:
            parts = ["显示器 %d = %s" % (i, "--" if values.get(i) is None else values.get(i))
                     for i in targets]
            self.bri_panel.set_current("当前亮度: " + " | ".join(parts))
        else:
            self.bri_panel.set_current("当前亮度: 读取失败")

    def apply_now(self):
        """后台下发亮度（DDC 逐台写实测 ~370ms，同步会冻界面）。"""
        if self._busy:
            return
        targets = self.selected_indices()
        level = clamp_level(self.bri_panel.slider.get())
        self.status_var.set("正在下发亮度 %d%% …" % level)
        self.run_bg(lambda: set_brightness_many(targets, level),
                    lambda r: self._finish_apply(r, level), busy=True)

    def _finish_apply(self, result, level):
        ok, errs = _as_ok_errs(result)
        if ok:
            self.status_var.set("已把 %s 的亮度设为 %d%%"
                                % ("、".join("显示器 %d" % i for i in ok), level))
        if errs:
            detail = "\n".join(("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs)
            self.status_var.set("部分失败：" + detail.replace("\n", " "))
            self.msg.showwarning("部分显示器失败", detail)
        self.read_current()

    def test_current(self):
        """把「按当前时刻判定出的档位亮度」试下发一次。"""
        if self._busy:
            return
        level = current_level(self.cfg)
        self.run_bg(lambda: set_brightness_many(self.selected_indices(), level),
                    lambda r: self._finish_test(r, level), busy=True)

    def _finish_test(self, result, level):
        ok, errs = _as_ok_errs(result)
        self.status_var.set("测试：当前档位亮度 %d%%，已下发 %d 台" % (level, len(ok)))
        if errs:
            self.msg.showwarning("部分失败", "\n".join(
                ("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs))
        self.read_current()

    # ---------- 熄屏 / 点亮 ----------
    def screen_off(self):
        """后台熄灭选中显示器（0xD6=2 待机；绝不用 4/5，见服务层硬件红线）。"""
        if self._busy:
            return
        targets = self.selected_indices()
        self.status_var.set("正在熄灭显示器…")
        self.run_bg(lambda: screen_off_many(targets),
                    self._finish_screen_off, busy=True)

    def _finish_screen_off(self, result):
        ok, errs = _as_ok_errs(result)
        if ok:
            self.status_var.set("已熄灭 %d 台显示器" % len(ok))
        if errs:
            self.msg.showwarning("熄灭部分失败", "\n".join(
                ("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs))
        mins = self._auto_relight_minutes()
        if ok and mins > 0:
            # 保险丝：N 分钟后自动点亮（经队列回主线程执行）
            self._relight_pending = True
            threading.Timer(mins * 60,
                            lambda: self._read_queue.put(("hotkey", "on", None))).start()

    def screen_on(self):
        """后台点亮（0x60 信号源切换，每台约 4 秒）。"""
        if self._busy:
            return
        targets = self.selected_indices()
        self.status_var.set("正在点亮显示器（信号源切换，约 4 秒）…")
        self.run_bg(lambda: screen_wake_many(targets),
                    self._finish_screen_on, busy=True)

    def _finish_screen_on(self, result):
        ok, errs = _as_ok_errs(result)
        self._relight_pending = False
        if ok:
            self.status_var.set("已点亮 %d 台显示器" % len(ok))
        if errs:
            self.msg.showwarning("点亮部分失败", "\n".join(
                ("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs))

    def _auto_relight_minutes(self):
        try:
            return max(0, min(120, int(float(self.screen_panel.relight_spin.get()))))
        except Exception:
            return 0

    def toggle_screen_hotkeys(self):
        enabled = bool(self.screen_panel.hotkey_var.get())
        self.cfg["screen_hotkeys"] = enabled
        save_config(self.cfg)
        if enabled:
            self._start_hotkey_listener()
        else:
            self._stop_hotkey_listener()
            self.status_var.set("已停用熄屏热键")

    def toggle_autostart(self):
        enable = bool(self.screen_panel.autostart_var.get())
        exe, prefix = task_target()
        cmd = ('"%s" %s--minimized' % (exe, prefix)).strip()
        try:
            set_autostart(enable, cmd)
            self.status_var.set("开机自启动已" + ("开启" if enable else "关闭"))
        except Exception as e:
            self.screen_panel.autostart_var.set(not enable)
            self.msg.showerror("设置失败", "%s: %s" % (type(e).__name__, e))

    def _start_hotkey_listener(self):
        """常驻热键监听（可选功能）：RegisterHotKey + 消息循环，事件经队列回主线程。"""
        if self._hotkeys_on:
            return
        self._hotkeys_on = True

        def listener():
            user32 = ctypes.windll.user32
            mods = 0x0001 | 0x0002 | 0x0004 | 0x4000     # ALT|CONTROL|SHIFT|NOREPEAT
            ids = {1: ("off", 0x4F), 2: ("on", 0x50)}    # O / P
            for hid, (_act, vk) in ids.items():
                if not user32.RegisterHotKey(None, hid, mods, vk):
                    self._hotkeys_on = False
                    self._read_queue.put(("hotkey_error",
                                          "热键注册失败（可能被其他程序占用）", None))
                    return
            self._hotkey_tid = threading.get_ident()
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312 and msg.wParam in ids:
                    self._read_queue.put(("hotkey", ids[msg.wParam][0], None))
            for hid in ids:
                user32.UnregisterHotKey(None, hid)

        threading.Thread(target=listener, daemon=True).start()
        self.status_var.set("熄屏热键已启用（Ctrl+Alt+Shift+O / P）")

    def _stop_hotkey_listener(self):
        self._hotkeys_on = False
        if self._hotkey_tid is not None:
            ctypes.windll.user32.PostThreadMessageW(self._hotkey_tid, 0x0012, 0, 0)
            self._hotkey_tid = None

    # ---------- 托盘 ----------
    def _tray_image(self):
        """托盘图标（纯代码绘制：深色圆 + 亮度滑杆意象，不依赖外部资源）。"""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(30, 30, 30, 255),
                  outline=(74, 158, 255, 255), width=3)
        d.rounded_rectangle((14, 28, 50, 36), radius=4, fill=(74, 158, 255, 255))
        d.ellipse((36, 23, 52, 41), fill=(255, 255, 255, 255))
        return img

    def _tray_tooltip_text(self):
        """托盘悬停文本：定时档位摘要 + 下次运行时间（超长截断到 120 字符）。"""
        if not self.cfg.get("enabled"):
            return "%s\n定时未启用" % APP_TITLE
        slots = self.cfg.get("slots", [])
        brief = "  ".join("%s→%d%%%s" % (x.get("time"), x.get("level", 0),
                                          "熄" if x.get("screen") == "off" else
                                          "亮" if x.get("screen") == "on" else "")
                           for x in slots)
        nxt = _next_run_text([parse_hhmm(x.get("time")) for x in slots])
        text = "%s\n定时: %s\n下次 %s" % (APP_TITLE, brief, nxt)
        return text[:120]

    def _tray_update_tooltip(self):
        if self.tray is not None:
            try:
                self.tray.tooltip = self._tray_tooltip_text()
                self.tray.update_menu()
            except Exception:
                pass

    def _create_tray(self):
        """创建托盘图标。pystray 必须与 tkinter 分线程运行（多来源一致的权威模式）：
        图标跑在独立 daemon 线程；回调里操作 Tk 控件前先 root.after(0, ...) 投递，
        否则 RuntimeError: main thread is not in main loop。"""
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
        self.status_var.set("就绪")

    def _tray_toggle_schedule(self):
        if self.sch_panel.schedule_enabled():
            self.cancel_schedule()
        else:
            self.sch_panel.set_enabled_flag(True)
            self.cfg["enabled"] = True
            save_config(self.cfg)
            self.status_var.set("正在写入任务计划…")
            self.run_bg(lambda: register_tasks(self.cfg),
                        self._finish_tray_register, busy=True)

    def _finish_tray_register(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            self.msg.showerror("注册任务失败", out or "未知错误")
            self.status_var.set("注册任务失败")
            self.sch_panel.set_enabled_flag(False)
            self.cfg["enabled"] = False
            return
        self._refresh_task_status()
        self.status_var.set("定时已启用")

    def _tray_quit(self):
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None
        self._stop_hotkey_listener()
        self.root.destroy()

    def _on_close(self):
        """点 X = 最小化到托盘（退出走托盘菜单）；无托盘环境照旧退出。"""
        if self.tray is not None:
            self.root.withdraw()
            self.status_var.set("已最小化到托盘（右下角图标，右键可退出）")
            return
        self._stop_hotkey_listener()
        self.root.destroy()

    # ---------- 定时 ----------
    def save_schedule(self):
        """整体包异常兜底：--windowed 没控制台，回调异常会无声消失。"""
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
        part = {"monitors": self.selected_indices(), "enabled": True,
                "catch_up_on_logon": bool(self.sch_panel.catchup_var.get()),
                "slots": slots}
        self.cfg.update(part)
        ok, msg = save_config(self.cfg)
        if not ok:
            self.msg.showerror("保存失败", msg)
            return
        # 注册要冷启动一个子进程（实测 ~3s），必须放后台
        self.status_var.set("正在写入任务计划…")
        self.run_bg(lambda: register_tasks(self.cfg),
                    lambda r: self._finish_register(r, part), busy=True)

    def _finish_register(self, result, part):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            self.msg.showerror("注册任务失败", out or "未知错误")
            self.status_var.set("注册任务失败")
            return
        self._refresh_task_status()
        self.status_var.set("定时已启用")
        self.msg.showinfo("定时已保存", _saved_summary(part, out))

    def cancel_schedule(self):
        self.cfg["enabled"] = False
        save_config(self.cfg)
        self.sch_panel.set_enabled_flag(False)
        self.status_var.set("正在取消定时任务…")
        self.run_bg(unregister_tasks, self._finish_unregister, busy=True)

    def _finish_unregister(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        self._refresh_task_status()
        self.status_var.set("已取消定时任务" if ok else "取消定时任务时出现问题：%s" % out)

    def _refresh_task_status(self):
        """后台查任务状态：快路径读本地 XML（~1ms），兜底冷启动子进程（~2.7s）。"""
        if self._task_querying:
            return
        self._task_querying = True

        def job():
            try:
                return query_task_status()
            except Exception:
                return ""

        def done(text):
            self._task_querying = False
            self._apply_task_status(text)

        self.run_bg(job, done)

    def _apply_task_status(self, text):
        by_name = {}
        for line in (text or "").splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            name, rest = line.split("=", 1)
            if name in TASK_SHORT:      # 过滤兜底路径错误文本里带 "=" 的行
                by_name[name] = rest
        lines = ["任务计划状态："]
        for name, short in TASK_SHORT.items():
            lines.append(_format_task_line(name, short, by_name.get(name)))
        self.sch_panel.set_task_status("\n".join(lines))
        self._tray_update_tooltip()


def _valid_hhmm(text):
    try:
        h, m = str(text).strip().split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return False



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


def parse_screen_action(text):
    """'off'/'on' -> 规范动作名；其他 -> None（供 CLI 与测试复用）。"""
    t = str(text).strip().lower()
    return t if t in ("off", "on") else None


def cli_screen(action):
    """熄灭/点亮选中的显示器（配置 monitors；空 = 全部）。"""
    targets = load_config().get("monitors") or []
    if action == "off":
        ok, errs = screen_off_many(targets)
        _out("已熄灭 %d 台显示器" % len(ok) if ok else "熄灭失败")
    else:
        ok, errs = screen_wake_many(targets)
        _out("已点亮 %d 台显示器" % len(ok) if ok else "点亮失败")
    for i, e in errs:
        _out("显示器 %d：%s" % (i, e))
    if errs and not ok:
        sys.exit(1)


def main():
    args = sys.argv[1:]
    if args:
        cmd = args[0]
        if cmd == "--set" and len(args) >= 3:
            cli_set(args[1], args[2])
            return
        if cmd == "--apply-schedule":
            cli_apply_schedule()
            return
        if cmd == "--screen" and len(args) >= 2:
            action = parse_screen_action(args[1])
            if action is None:
                _out("--screen 参数需为 off 或 on")
                sys.exit(2)
            cli_screen(action)
            return
        if cmd == "--list":
            cli_list()
            return
        if cmd == "--selftest":
            cli_selftest()
            return
    root = tk.Tk()
    App(root, start_minimized=("--minimized" in args))
    apply_dark_titlebar(root)
    if get_monitors is None:
        messagebox.showerror("缺少依赖", "monitorcontrol 未能加载，请重新安装本程序。")
    root.mainloop()


if __name__ == "__main__":
    main()
