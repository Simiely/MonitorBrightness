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
import os
import re
import math
import json
import html
import queue
import shutil
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
APP_VERSION = "1.9.1"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MonitorBrightness")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
# 事件日志：任务重建 / 启动补执行 / 补执行失败都落在这里，界面直接读它展示
EVENTS_FILE = os.path.join(CONFIG_DIR, "events.log")
ERROR_FILE = os.path.join(CONFIG_DIR, "error.log")

# 启动补检的最大错过时长（分钟）。取 12 小时的理由：
# 覆盖"前一晚关机、早上开机后才想起"的整个工作日；超过 12 小时的档位意图已经过期，
# 往往下一档就在眼前，再补会把白天亮度硬套到夜里。
CATCH_UP_LIMIT_MIN = 12 * 60

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
DEFAULT_SLOTS = [{'time': '08:30', 'level': 80, 'screen': ''}, {'time': '21:00', 'level': 0, 'screen': ''}]

# 每个时间段可携带的屏幕动作：""（不变）/ off（到点熄灭）/ on（到点点亮）
SCREEN_ACTIONS = (("", "不变"), ("off", "熄灭"), ("on", "点亮"))
SCREEN_VALUE_BY_LABEL = {label: value for value, label in SCREEN_ACTIONS}
SCREEN_LABEL_BY_VALUE = {value: label for value, label in SCREEN_ACTIONS}

# 熄灭方式：system = 系统级整组熄灭（默认，一定唤得回来）
#           ddc    = 逐台 DDC 0xD6=2 待机（本机实测可能唤不回，仅作高级选项）
OFF_METHODS = ("system", "ddc")
OFF_METHOD_LABEL = {"system": "系统级", "ddc": "单屏待机"}
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
    {"key": "screen_hotkeys", "kind": "flag", "default": False},          # 熄屏/点亮热键（需常驻）
    {"key": "screen_auto_relight_min", "kind": "level", "default": 0},    # 熄灭后 N 分钟自动点亮（0=关）
    {"key": "tray_enabled", "kind": "flag", "default": True},             # 托盘开关（关 = 点 X 直接退出）
    {"key": "screen_state", "kind": "screenstate", "default": "on"},      # 屏幕状态记账（软件最后一次操作）
    # 记账时间点（"MM-DD HH:MM"）：结论行要能说清"什么时候熄的/亮的"
    {"key": "screen_state_at", "kind": "stamp", "default": ""},
    # 熄灭那一刻的系统 tick（毫秒）。用来判断"熄灭之后有没有过键鼠输入"——有输入
    # 就说明系统已经把屏幕唤醒了（见 _screen_woke_by_input）。重启后 tick 失效，忽略。
    {"key": "screen_off_tick", "kind": "int", "default": 0},
    # 熄灭方式：system = Windows 系统级整组熄灭（默认，一定唤得回来）
    #           ddc    = 逐台 DDC 0xD6=2 待机（本机实测可能唤不回，慎用）
    # ⚠️ v1.9.0 起默认改为 system。旧默认 ddc + ddc_allow_undeclared=False 会让
    #    新装用户点「熄灭」时被"面板未声明待机"闸门全部跳过，表现为按钮没反应。
    {"key": "screen_off_method", "kind": "choice",
     "values": ("system", "ddc"), "default": "system"},
    # 点亮时是否允许"切信号源"这最后一招（仅 ddc 方式下有效）
    {"key": "wake_switch_input", "kind": "flag", "default": True},
    # 是否允许写入面板未声明的待机值（0xD6=2）。默认关；
    # 用户在界面里选择"DDC 单屏待机"并确认风险后才会置 True。
    {"key": "ddc_allow_undeclared", "kind": "flag", "default": False},
    # 定时防重复记账：{"date": "YYYY-MM-DD", "slot": "HH:MM", "at": "YYYY-MM-DD HH:MM:SS"}
    # 计划任务与启动补检都写这里；同一天同一档只执行一次（save 时还会与磁盘取较新者）。
    {"key": "last_fired", "kind": "fired", "default": {}},
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
    if kind == "stamp":
        # 只接受我们自己写的 "MM-DD HH:MM" 形状；其他一律当没有
        text = str(value or "").strip()
        return text if _valid_stamp(text) else ""
    if kind == "int":
        try:
            return max(0, int(value))
        except Exception:
            return spec["default"]
    if kind == "fired":
        return _coerce_fired(value)
    if kind == "screenstate":
        # ⚠️ 0xD6 硬件回读在本机不可信（寄存器值≠面板事实），状态由软件操作记账
        return value if value in ("on", "off") else spec["default"]
    return normalize_indices(value)


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


def _valid_stamp(text):
    """记账时间点形状：'MM-DD HH:MM'（自己写的，只认这一种）。"""
    return bool(re.match(r"^\d{2}-\d{2} \d{2}:\d{2}$", text or ""))


def stamp_now():
    return datetime.now().strftime("%m-%d %H:%M")


def _coerce_fired(value):
    """last_fired 规整：只认 {"date": YYYY-MM-DD, "slot": HH:MM}。

    at 是完整时间戳（秒级），只用于和磁盘上的记录比"谁更新"，缺省按当天 0 点算。
    """
    if not isinstance(value, dict):
        return {}
    date = str(value.get("date", "")).strip()
    slot = str(value.get("slot", "")).strip()
    at = str(value.get("at", "")).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or not _valid_hhmm(slot):
        return {}
    if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", at):
        at = date + " 00:00:00"
    return {"date": date, "slot": slot, "at": at}


def _fired_newer(a, b):
    """a 是否比 b 更新（都是 _coerce_fired 的结果）。"""
    if not a:
        return False
    if not b:
        return True
    return a.get("at", "") > b.get("at", "")


def mark_fired(cfg, slot, when=None):
    """记下"今天这一档已经执行过"，供启动补检防重复。"""
    when = when or datetime.now()
    slot = slot or {}
    cfg["last_fired"] = {"date": when.strftime("%Y-%m-%d"),
                         "slot": str(slot.get("time", "")),
                         "at": when.strftime("%Y-%m-%d %H:%M:%S")}


def _write_event_log(text):
    """追加一行事件日志（线程安全）。返回写入的那一行。"""
    line = "%s %s" % (datetime.now().strftime("%m-%d %H:%M"), text)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with _EVENT_LOCK:
            try:
                if os.path.getsize(EVENTS_FILE) > 256 * 1024:
                    os.replace(EVENTS_FILE, EVENTS_FILE + ".1")
            except OSError:
                pass
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    return line


def read_recent_events(limit=3):
    """读事件日志最后几行，界面用来显示「今天 08:15 补执行」这类记录。"""
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = [x.strip() for x in f.readlines() if x.strip()]
        return lines[-limit:]
    except Exception:
        return []


_EVENT_LOCK = threading.Lock()


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
        # v5 -> v6 迁移：旧默认「ddc + 不允许未声明值」= 逐台全被闸门跳过，
        # 表现就是点熄灭没反应。主动选过 DDC 的用户会在确认框里被置
        # ddc_allow_undeclared=True，所以这条组合只可能是没动过的旧默认。
        try:
            _ver = int(raw.get("version") or 0)
        except Exception:
            _ver = 0
        if (_ver < 6 and raw.get("screen_off_method") == "ddc"
                and not raw.get("ddc_allow_undeclared")):
            raw = dict(raw, screen_off_method="system")
        # 能力串缓存播种（静态固件属性，避免每次熄灭前重读 3s/台）。
        # v1.9.0 起 key 里带 EDID 短哈希（dev#uid），旧 dev-only 键不再播种：
        # 换面板后不会沿用旧缓存，代价只是首次重新实测一次。
        for _dev, _caps in (raw.get("caps") or {}).items():
            if isinstance(_caps, str) and _caps and "#" in str(_dev):
                _CAPS_CACHE.setdefault(_dev, _caps)
        for key in SETTING_BY_KEY:
            if key in raw:
                cfg[key] = coerce_setting(key, raw[key])
        cfg["models"] = _load_model_cache(raw)
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


def _read_disk_last_fired():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return coerce_setting("last_fired", raw.get("last_fired")) \
            if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    """保存配置。原子写 + last_fired 与磁盘取较新者。

    为什么 last_fired 要合并：计划任务在后台跑 --apply-schedule 时也会写它，
    而 GUI 常驻进程手里的 cfg 是启动时读的；如果 GUI 之后随手保存一次，
    就会把任务刚写的"今天已执行"覆盖回旧值 → 下次启动又补执行一遍。
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = dict(cfg)
        if _CAPS_CACHE:
            payload["caps"] = dict(_CAPS_CACHE)
        payload["version"] = CONFIG_VERSION
        mine = coerce_setting("last_fired", cfg.get("last_fired"))
        payload["last_fired"] = mine
        disk = _read_disk_last_fired()
        if _fired_newer(disk, mine):
            payload["last_fired"] = disk
        _atomic_write_text(CONFIG_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
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


def _tick_ms():
    """系统启动以来的毫秒数（GetTickCount）。取不到返回 0。"""
    try:
        return int(ctypes.windll.kernel32.GetTickCount())
    except Exception:
        return 0


def mark_screen_state(cfg, state):
    """屏幕状态记账：状态 + 时间点 + （熄灭时）tick。

    tick 是"熄灭之后有没有输入过"的判据来源，见 _screen_woke_by_input。
    """
    cfg["screen_state"] = state
    cfg["screen_state_at"] = stamp_now()
    cfg["screen_off_tick"] = _tick_ms() if state == "off" else 0


def _screen_woke_by_input(cfg):
    """系统级熄灭之后，只要出现过键鼠输入，屏幕就已经被 Windows 唤醒了。

    判据：`熄灭至今的时长` 明显大于 `当前 idle`，说明期间 idle 被重置过 = 有过输入。
    （熄灭那一刻的 idle 约等于 0——点按钮本身就是输入，所以留 0.8 秒余量。）
    只对 system 方式成立：DDC 待机不会因为输入而醒来。
    """
    if cfg.get("screen_off_method") == "ddc" or cfg.get("screen_state") != "off":
        return False
    off_tick = cfg.get("screen_off_tick") or 0
    if not off_tick:
        return False
    try:
        now = ctypes.windll.kernel32.GetTickCount()
    except Exception:
        return False
    if off_tick > now:                 # 中间重启过，tick 失效
        return False
    idle = _idle_seconds()
    if idle is None:
        return False
    return idle * 1000 + 800 < (now - off_tick)


def screen_verdict(cfg, probes=None):
    """给出"屏幕现在是亮还是灭"的**一句话结论** + 一行依据。

    判据优先级（越靠前越硬）：
      1) `_screen_woke_by_input()`：熄灭后出现过输入 → 屏幕已被系统唤醒（结论：亮）；
      2) 软件记账：我们熄灭后再没有输入 → 仍然是灭的；
      3) DDC 回读：只作为一行证据展示 —— `0xD6` 会撒谎（实测待机时仍报 1），
         之前把它直接当结论，才会出现"明明黑着却显示亮"。
    返回 {"state": "on"/"off"/"unknown", "text": "...", "detail": "..."}。
    """
    at = (cfg.get("screen_state_at") or "").strip()
    woke = _screen_woke_by_input(cfg)
    state = "on" if woke else cfg.get("screen_state", "on")
    idle = _idle_seconds()

    if state == "off":
        text = "屏幕：熄灭中" + ("（%s 熄灭）" % at if at else "")
        tail = "点「点亮显示器」或动一下鼠标/键盘即可恢复"
    elif woke:
        text = "屏幕：亮着" + ("（%s 熄灭后被键鼠输入唤醒）" % at if at else
                              "（熄灭后收到键鼠输入，已自动唤醒）")
        tail = "系统级熄灭由 Windows 恢复，输入即亮"
    elif state == "on":
        text = "屏幕：亮着" + ("（%s 点亮）" % at if at else "")
        tail = "物理电源键、Windows 自动熄屏不在追踪范围内"
    else:
        text, tail = "屏幕：状态未知", ""

    if probes is None:
        try:
            probes = probe_states()
        except Exception:
            probes = []
    bits = ["记账为主"]
    if probes:
        # 结论行要人话；原始寄存器值留给 --probe / --diag
        bits.append("DDC 回读 %s（会撒谎，仅参考）"
                    % "、".join("%d=%s" % (p["index"],
                                          {"on": "开", "off": "灭"}.get(p["state"], "未知"))
                                for p in probes))
    else:
        bits.append("DDC 无回读")
    if idle is not None:
        bits.append("上次输入 %s" % ("刚发生" if idle < 2 else _human_gap(idle)))
    detail = "依据：" + " · ".join(bits) + ("｜" + tail if tail else "")
    return {"state": state, "text": text, "detail": detail, "woke_by_input": woke}


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
    """系统级点亮，并补一次"显示需求"声明（Windows 有时不主动重发画面）。"""
    ok = _broadcast_monitor_power(SC_ON)
    _request_display_on()
    return ok


ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000002


def _request_display_on():
    """向系统声明"现在需要显示"，不移动真实光标。

    v1.8.5 及更早用 mouse_event 把光标移 1 像素再移回来。虽然肉眼几乎无感，
    但它确实是**真实输入**：会打断"用户是不是在操作"的判断（全屏游戏、演示、
    远程协助里都能被感知），鼠标落在屏幕边缘时还可能出现 1px 抖动。
    改成 SetThreadExecutionState(ES_DISPLAY_REQUIRED)：语义就是"强制显示器退出
    低功耗状态"，不产生任何输入事件。它只对"系统把屏关掉"有效；DDC 面板
    自身待机仍靠上面的 0xD6=1 / 重新驱动输出。
    """
    try:
        k = ctypes.windll.kernel32
        k.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
        time.sleep(0.2)
        k.SetThreadExecutionState(ES_CONTINUOUS)
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


def _wake_one(t, allow_switch=True, expect_off=True):
    """分级唤醒单台显示器。返回 (命令是否都下发成功, 走到第几级, 说明)。

    ⚠️ **不用 `0xD6` 回读判断"醒了没有"**（v1.7.0 现场取证）：
    本机写入 `0xD6=2` 待机之后，回读仍是 `1`、`0x10` 亮度也照样读得到。
    拿它当判据就会出现"程序说点亮成功、屏幕其实黑着"——和 v1.5.0 熄屏事故同源。
    所以这里只保证"该发的命令都发到位"，真实亮灭交给「检测亮灭」+ 眼睛。

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
        _request_display_on()        # 屏幕本来就亮着：只声明显示需求，别去动输出
    else:
        output_recycled = _output_recycle()
        _request_display_on()
    stage = max(stage, 3)
    time.sleep(0.35)

    if not allow_switch:
        return wrote, stage, "已下发电源/亮度/输出重驱动（切信号源已禁用）"
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
    results = {}
    lib = _dxva2()

    def work(t):
        try:
            results[t["index"]] = _wake_one(t, allow_switch, expect_off)
        except Exception as e:
            results[t["index"]] = (False, 0, str(e))
        finally:
            # 句柄所有权归线程：即使 join 超时、本函数先返回，也不会出现
            # "主线程 DestroyPhysicalMonitor、线程还在用这个句柄"的 use-after-free。
            # DDC API 本身没有超时参数，卡住的线程只能让它自己收尾（daemon，不阻塞退出）。
            try:
                lib.DestroyPhysicalMonitor(t["handle"])
            except Exception:
                pass

    threads = [threading.Thread(target=work, args=(t,), daemon=True) for t in picked]
    for th in threads:
        th.start()
    deadline = time.monotonic() + 30
    timed_out = False
    for th in threads:
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            break
        th.join(left)

    ok, errs = [], list(bad)
    for t in picked:
        done, stage, why = results.get(t["index"], (False, 0, "超时未返回（后台线程仍在收尾）"))
        if done:
            ok.append(t["index"])
        else:
            errs.append((t["index"], "第 %d 级（%s）：%s"
                         % (stage, WAKE_STAGE_LABEL.get(stage, "-"), why)))
    # 只释放**没交给线程**的句柄；被选中的句柄由各自线程负责销毁
    release_targets([t for t in targets if all(t is not p for p in picked)])
    if timed_out:
        errs.append((-1, "部分显示器超过 30 秒仍未返回，已放弃等待（不会影响已完成的写入）"))
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
    """熄灭显示器。system（默认）= 整组系统级；ddc = 逐台 0xD6=2（可能唤不回）。

    返回 (成功序号列表, [(序号, 错误), ...])，与逐台接口保持同一形状，
    这样界面与 CLI 不用关心底层是哪种方式。
    """
    if cfg.get("screen_off_method") == "ddc":
        return screen_off_many(indices, bool(cfg.get("ddc_allow_undeclared")))
    if not screen_off_system():
        return [], [(-1, "系统级熄灭下发失败")]
    return _system_ok_targets(indices), []


def screen_on_cfg(cfg, indices):
    """点亮显示器。system（默认）= 系统级点亮；ddc = 分级 DDC 唤醒。"""
    if cfg.get("screen_off_method") == "ddc":
        return screen_wake_many(indices, **wake_options(cfg))
    if not screen_on_system():
        return [], [(-1, "系统级点亮下发失败")]
    return _system_ok_targets(indices), []


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


def catch_up_decision(cfg, now=None):
    """启动补检判定（纯逻辑，不碰硬件，单测直接跑）。

    只看**今天**已经到点的档位里最后一档：
      - 今天还没到任何档位 → 不补（早上 7 点开机不该把昨晚 21:00 的夜间档再套一遍）
      - 错过超过 CATCH_UP_LIMIT_MIN 分钟 → 不补（意图过期，下一档往往就在眼前）
      - config 的 last_fired 已经是"今天 + 同一档" → 不补（计划任务已经跑过）
    返回 dict：due / slot / missed_min / reason。
    """
    now = now or datetime.now()
    now_min = _minutes(now)
    slots = [s for s in (cfg.get("slots") or []) if _valid_hhmm(s.get("time"))]
    due = [s for s in slots if parse_hhmm(s.get("time")) <= now_min]
    if not due:
        return {"due": False, "slot": None, "missed_min": 0,
                "reason": "今天还没有到点的档位"}
    slot = max(due, key=lambda s: parse_hhmm(s.get("time")))
    missed = now_min - parse_hhmm(slot.get("time"))
    if missed > CATCH_UP_LIMIT_MIN:
        return {"due": False, "slot": slot, "missed_min": missed,
                "reason": "已错过 %d 分钟，超过补检上限 %d 分钟" % (missed, CATCH_UP_LIMIT_MIN)}
    fired = coerce_setting("last_fired", cfg.get("last_fired"))
    if fired.get("date") == now.strftime("%Y-%m-%d") and fired.get("slot") == slot.get("time"):
        return {"due": False, "slot": slot, "missed_min": missed,
                "reason": "今天这一档已执行过"}
    return {"due": True, "slot": slot, "missed_min": missed, "reason": ""}



def current_level(cfg=None, now=None):
    """当前时刻应处的亮度（纯逻辑；定时任务与「测试当前档」共用）。"""
    cfg = cfg or load_config()
    return active_slot(_slot_minutes(cfg), _minutes(now))[1]


def resolve_monitors(cfg):
    """把"用户选过的显示器"解析成**当前**的下标列表。

    配置里存两份：monitors（下标）+ monitor_ids（设备名，如 DISPLAY1）。
    设备名由输出口决定、同一次会话内稳定；下标却会随显卡驱动与接线重排。
    只存下标就会出现"熄的是这块、开机后点的是那块"——这是换电脑/换线后
    点亮失效最典型的一种形态，所以解析时优先认设备名。

    返回 [] 表示"全部显示器"（与界面上"一个都不勾 = 全部"一致）。
    """
    ids = cfg.get("monitor_ids") or []
    if ids:
        targets = enumerate_targets()
        try:
            by_dev = {t["dev"]: t["index"] for t in targets}
        finally:
            release_targets(targets)      # 只借一瞬，别把句柄漏在这
        hit = sorted({by_dev[d] for d in ids if d in by_dev})
        if hit:
            return hit
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


def wake_options(cfg):
    """唤醒策略的两个开关，CLI 与界面共用。"""
    return {"allow_switch": bool(cfg.get("wake_switch_input", True)),
            "expect_off": cfg.get("screen_state") != "on"}


def apply_slot(cfg, slot, persist=True):
    """套用指定档位（亮度 + 该档屏幕动作）；成功写亮度后记 last_fired。

    返回 (level, screen, ok, errs)；screen 为该档屏幕动作（""/off/on）。
    屏幕动作失败的错误以序号 -1 记入 errs（界面按原文显示）。
    记账只在**写亮度成功**时发生：全失败（比如显卡驱动没起来）留待下次启动重试。
    """
    level = slot.get("level", 50)
    screen = slot.get("screen", "")
    targets = resolve_monitors(cfg)
    ok, errs = set_brightness_many(targets, level)
    if screen == "off":
        sok, serrs = screen_off_cfg(cfg, targets)
    elif screen == "on":
        sok, serrs = screen_on_cfg(cfg, targets)
    else:
        sok, serrs = [], []
    ok = ok + sok
    errs = errs + [(-1, "屏幕 %d: %s" % (i, e)) for i, e in serrs]
    if screen in ("off", "on") and ok:
        mark_screen_state(cfg, screen)
    if ok and persist:
        mark_fired(cfg, slot)
        save_config(cfg)
    return level, screen, ok, errs


def apply_schedule(cfg=None, now=None):
    """按当前时间套用对应档位（亮度 + 该档的屏幕动作）。

    CLI --apply-schedule 与 GUI 启动补检都走这里；执行成功后写 last_fired，
    供补检防重复（计划任务跑过之后，界面不会再补一遍）。
    """
    cfg = cfg or load_config()
    slot = active_slot_dict(cfg.get("slots", []), _minutes(now))
    return apply_slot(cfg, slot)


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


def build_register_script(cfg):
    """生成注册脚本：一条定时任务挂 N 个 Daily 触发器（每档一个），可选登录补跑任务。

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
        lines.append("  (New-ScheduledTaskTrigger -Daily -At %s)" % _ps_quote(x["time"]))
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
    return ""


def ensure_task_registration(cfg, force=False):
    """定时启用时校验任务；不一致则静默重建。返回 (是否可用, 说明)。

    在后台线程调用，不阻塞窗口显示。重建结果写 events.log，界面读它展示。
    """
    if not cfg.get("enabled"):
        return True, "定时未启用，跳过任务校验"
    reason = task_match_reason(cfg)
    if not reason and not force:
        return True, ""
    ok, out = register_tasks(cfg)
    if ok:
        event = "计划任务已按当前程序重建（%s）" % (reason or "手动强制")
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
        if name == TASK_CATCHUP:
            out.append("%s=%s||SWA=%s" % (name, state, swa))
        else:
            bounds = _boundary_minutes_all(txt)
            nxt = _next_run_text(bounds)
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
    _out(screen_verdict(cfg, states)["text"])
    for s in states:
        _out("  显示器 %d（%s）：回读 %s — %s"
             % (s["index"], s["dev"],
                {"on": "亮", "off": "灭"}.get(s["state"], "未知"), s["why"]))
    if states:
        _out("  说明：0xD6 回读不可信（待机时仍报 1），结论以上面那行为准（软件记账）")
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
    _out("配置：presets=%s · 熄灭方式=%s · 记录屏幕状态=%s"
         % (cfg.get("presets"), cfg.get("screen_off_method"), cfg.get("screen_state")))
    _out("配置里的显示器选择：monitors=%s · monitor_ids=%s"
         % (cfg.get("monitors"), cfg.get("monitor_ids")))

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
        _out("四、定时任务")
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
#   - 托盘 / 自启 / 热键 / 熄灭方式 / 预设数值 全部收进「设置」；
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


def enable_dpi_awareness():
    """在创建 Tk 之前声明 DPI 感知（否则 125%/150% 下整窗被系统位图拉伸变糊）。

    优先 per-monitor v2（多屏不同缩放时也正确），失败回退 per-monitor，
    再回退 system aware。返回实际生效的模式，方便 --diag/日志。
    """
    if os.name != "nt":
        return ""
    try:
        u = ctypes.windll.user32
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # PER_MONITOR_AWARE_V2
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)            # PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()                 # SYSTEM_DPI_AWARE
        return "system"
    except Exception:
        return ""


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def _work_area_height(widget=None):
    """当前窗口所在显示器的工作区高度（已扣掉任务栏）。

    v1.8.5 用 winfo_screenheight()（主屏**全高**，含任务栏）再减 180，
    多屏/缩放不同时会把窗口顶出工作区。这里改成 MonitorFromWindow + rcWork。
    """
    try:
        u = ctypes.windll.user32
        u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.MonitorFromWindow.restype = wintypes.HMONITOR
        hwnd = widget.winfo_id() if widget is not None else 0
        hmon = u.MonitorFromWindow(hwnd, 2)                       # MONITOR_DEFAULTTONEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            h = int(mi.rcWork.bottom) - int(mi.rcWork.top)
            if h > 0:
                return h
    except Exception:
        pass
    try:
        return int(widget.winfo_screenheight()) if widget is not None else 800
    except Exception:
        return 800


class ScrollArea(ttk.Frame):
    """可滚动内容容器（Canvas + 内部 Frame + 滚动条）。

    设置项一多窗口就会超过可用高度（实测 5 个档位时 1117px），
    底部控件就点不到了。高度按**当前屏工作区**封顶，超出才出现滚动条。
    """

    def __init__(self, root, bg, on_fit=None):
        super().__init__(root)
        self._on_fit = on_fit
        # rcWork 已扣任务栏；再留 120 给标题栏(约 31) + 窗口边框 + 呼吸边距
        self._max_h = max(320, _work_area_height(self) - 120)
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

    def refresh_max_height(self):
        """窗口被拖到另一块屏后重算封顶高度。"""
        h = max(320, _work_area_height(self) - 120)
        if h != self._max_h:
            self._max_h = h
            self._on_body()

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


from PIL import Image, ImageDraw, ImageTk  # noqa: E402  （延迟到 Tk 场景可用处）


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

        self.body = tk.Frame(self, bg=UI["card"])
        self.body.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        foot = tk.Frame(self, bg=UI["card"])
        foot.pack(fill="x", padx=20, pady=(10, 16))
        self.ok_btn = Button(foot, "完成", self.close, kind="primary",
                             height=34, pad_bg=UI["card"])
        self.ok_btn.pack(fill="x")

        self.bind("<Escape>", lambda e: self.close())
        self.transient(app.root)

    def show(self):
        """内容搭完后再定尺寸并弹出。

        必须由子类在 **最后** 调用：基类构造时 body 还是空的，
        那时量出来只有 144px 高（实测），窗口会被 resizable(False) 锁死。
        """
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

    def close(self):
        self.grab_release()
        self.destroy()

    def section(self, text):
        SectionTitle(self.body, text).pack(anchor="w", pady=(14, 6))
        card = Card(self.body, pad=0, bg=UI["card_alt"], page_bg=UI["card"])
        card.pack(fill="x")
        return card.inner

    def row(self, parent, title, sub="", right_text=None, builder=None):
        """一行：标题（+副标题）在左，右侧放文字或控件，底部一条分隔线。

        ⚠️ 控件必须通过 builder 在**本行内**创建：先建好再 pack 到别的父容器会
        报 "can't pack inside"，或悄悄跑到卡片其它位置（实测踩过）。
        """
        # 用父容器的底色，卡片（card_alt）里才不会出现一块深色补丁
        pbg = parent.cget("bg")
        row = tk.Frame(parent, bg=pbg)
        row.pack(fill="x")
        right = tk.Frame(row, bg=pbg)
        right.pack(side="right", padx=(8, 14))
        left = tk.Frame(row, bg=pbg)
        left.pack(side="left", fill="x", expand=True, padx=(14, 0), pady=9)
        Label(left, title, size=9, bg=pbg).pack(anchor="w")
        if sub:
            Label(left, sub, size=8, color=UI["text3"], bg=pbg,
                  justify="left", wraplength=self._width - 170).pack(anchor="w", pady=(1, 0))
        if right_text is not None:
            Label(right, right_text, size=8, color=UI["text3"],
                  bg=pbg).pack(anchor="e")
        if builder is not None:
            builder(right)
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

        # 临时反馈（flash）与常驻提示（hint_default）共用提示行：
        # 操作结果闪 3 秒后回到常驻内容，底部状态栏因此不再需要。
        self._hint_default = ""
        self._flash_until = 0.0
        self._flash_after = None
        self._unknown = False

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
            self.meta_lbl.config(text=self.app.screen_time_text())
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
                txt = tk.Frame(row, bg=UI["card"])
                txt.pack(side="left", fill="x", expand=True)
                info = self.app.mon_info[i] if i < len(self.app.mon_info) else None
                model = self.app.mon_models.get(i)
                Label(txt, model or ("显示器 %d" % i), size=9, bg=UI["card"]).pack(anchor="w")
                sub = ""
                if info:
                    sub = "%d × %d · %s · %s" % (
                        info[1], info[2], "主显示器" if info[3] else "副显示器", info[0])
                Label(txt, sub, size=8, color=UI["text3"], bg=UI["card"]).pack(anchor="w")
                self.mon_rows[i] = row
        finally:
            self._suspend = False

    def set_row_text(self, i, text):
        row = self.mon_rows.get(i)
        if row is None:
            return
        labels = [w for w in row.winfo_children()[1].winfo_children()]
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
        self.screen_seg.pack(fill="x", pady=(4, 0))

        if on_delete is not None:
            del_btn = Button(box, "删除这个时间段", self._do_delete, kind="secondary",
                             height=32, pad_bg=UI["card"])
            del_btn.pack(fill="x", pady=(16, 0))

        self.ok_btn.set_text("保存")
        self.ok_btn._command = self._do_save
        self.show()

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
        self.task_next = Label(st, "", size=8, color=UI["text3"], bg=UI["card"])
        self.task_next.pack(side="right", padx=(0, 8))
        self.task_sub = Label(self.inner, "", size=8, color=UI["text3"],
                              bg=UI["card"], justify="left", wraplength=360)
        self.task_sub.pack(fill="x", pady=(2, 0))
        # 事件日志（补执行 / 任务重建）：计划任务跑过什么、启动补过什么，一眼可见
        self.event_lbl = Label(self.inner, "", size=8, color=UI["orange"],
                               bg=UI["card"], justify="left", wraplength=360)
        self.event_lbl.pack(fill="x", pady=(2, 0))

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

        self._editor = SlotEditor(self.app, {"time": t, "level": 50, "screen": ""}, save)

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
        """显示最近的事件（补执行 / 任务重建 / 重建失败）。"""
        self.event_lbl.config(text="\n".join(lines or []))

    def set_actions_enabled(self, enabled):
        # 添加/编辑是本地操作，只受"正在执行"影响，不受定时开关影响
        self.add_btn.set_enabled(enabled)
        self.probe_task_btn.set_enabled(enabled)


class SettingsSheet(Sheet):
    """设置：托盘 / 自启 / 热键 / 熄灭方式 / 预设 / 关于（都是"不常改"的东西）。"""

    def __init__(self, app):
        super().__init__(app, "设置", "不常用的开关都在这里，主界面只留日常操作", width=440)
        self.app = app
        cfg = app.cfg

        win = self.section("窗口")
        self.row(win, "系统托盘图标", "在右下角常驻，右键可熄/亮屏、切换定时",
                 builder=lambda p: Switch(p, bool(cfg.get("tray_enabled", True)),
                                          lambda v: app.apply_setting("tray_enabled", v)).pack())
        self.row(win, "关闭窗口时最小化到托盘", "关掉则点 × 直接退出程序")

        start = self.section("启动与快捷键")
        self.row(start, "开机自动启动", "启动后直接进托盘",
                 builder=lambda p: Switch(p, get_autostart()[0],
                                          app.apply_autostart).pack())
        self.row(start, "全局热键", "熄灭 Ctrl Alt Shift O　点亮 Ctrl Alt Shift P",
                 builder=lambda p: Switch(p, bool(cfg.get("screen_hotkeys", False)),
                                          lambda v: app.apply_setting("screen_hotkeys", v)).pack())

        off = self.section("熄屏")
        pbg = off.cget("bg")
        wrap = tk.Frame(off, bg=pbg)
        wrap.pack(fill="x", padx=14, pady=(10, 6))
        Label(wrap, "熄灭方式", size=9, bg=pbg).pack(anchor="w")
        mode = Segmented(wrap, [OFF_METHOD_LABEL[m] for m in OFF_METHODS],
                         index=OFF_METHODS.index(cfg.get("screen_off_method", "system")),
                         command=lambda i: app.apply_off_method(OFF_METHODS[i]))
        mode.pack(fill="x", pady=(6, 0))
        Label(wrap, "系统级：整组熄灭、一定唤得回，但键鼠会点亮。\n"
                    "单屏待机：键鼠不点亮（远程防窥用），个别面板唤不回。",
              size=8, color=UI["text3"], bg=pbg, justify="left").pack(anchor="w", pady=(6, 0))
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

        self.row(off, "熄灭后自动点亮", "0 = 不自动；远程时建议设个值", builder=_relight)

        self.row(off, "点亮时允许切信号源", "最后一招；DP+HDMI 双线时可能切到空口",
                 builder=lambda p: Switch(p, bool(cfg.get("wake_switch_input", True)),
                                          lambda v: app.apply_setting("wake_switch_input", v)).pack())

        pre = self.section("亮度预设")
        pbg = pre.cget("bg")
        prow = tk.Frame(pre, bg=pbg)
        prow.pack(fill="x", padx=14, pady=9)
        Label(prow, "三个快捷按钮的值", size=9, bg=pbg).pack(anchor="w")
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
        self.row(about, "版本", "", right_text="v%s" % APP_VERSION)
        self.row(about, "配置文件", "", right_text="%APPDATA%\\MonitorBrightness")
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
        block("两种熄灭方式", [
            "系统级（默认）：整组一起灭，一定唤得回；任何键鼠输入都会点亮。",
            "DDC 单屏待机：键鼠不会点亮，适合远程操作时防窥；唤醒靠四级兜底，"
            "个别面板仍可能唤不回。",
        ])
        block("唤醒阶梯", [
            "0xD6=1 电源模式 → 写回亮度 → 输出重驱动（SC_MONITORPOWER 2→-1）"
            " → 切信号源，任何一级成功即停。",
        ])
        block("定时为什么这次准了", [
            "每次启动都会校验计划任务是否仍指向当前程序；换了目录/文件名会自动重建。",
            "今天已经错过的那一档，启动时会在后台补一次（同一天同一档只补一次）。",
            "任务状态行会显示下次运行、上次运行与上次结果；下面还会列出重建/补执行记录。",
        ])
        block("排查命令", [
            "--probe 打印每台屏的亮灭结论；--diag 打印枚举、能力串、寄存器回读与任务状态。",
            "--ensure-schedule 按当前配置/当前程序重建计划任务；--selftest 打印档位判定表。",
        ])
        self.show()


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
        self.mon_models = {int(k): v for k, v in self.cfg.get("models", {}).items()}
        self._read_queue = queue.Queue()
        self._bg_tasks = 0
        self._busy = False
        self._task_querying = False
        self._hotkeys_on = False
        self._relight_pending = False
        self._hotkey_tid = None
        self._last_verdict = {"state": self.cfg.get("screen_state", "on"), "detail": ""}
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
        if self.cfg.get("screen_hotkeys"):
            self._start_hotkey_listener()

        self.refresh_monitors(silent=True)
        self.refresh_task_status()
        self.probe_screens()
        if self.cfg.get("tray_enabled", True):
            self._create_tray()
        if start_minimized and self.tray is not None:
            self.root.withdraw()

        # 配置读坏的显式提示（不能静默吞掉：用户的设置还在坏文件里）
        self.root.after(150, self._warn_config_error)
        # 启动后台自检：任务定义与当前进程是否一致 + 今天错过的那一档要不要补。
        # 900ms 是给首屏探测让路，避免和开窗时的 DDC 枚举抢总线；全程后台线程。
        self.root.after(900, self._start_auto_checks)

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
            self.scroll.refresh_max_height()
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
            elif key == "screen_hotkeys":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
                if value:
                    self._start_hotkey_listener()
                else:
                    self._stop_hotkey_listener()
                    self.status_var.set("已停用熄屏热键")
            elif key == "screen_auto_relight_min":
                self.cfg[key] = max(0, min(120, int(value)))
                save_config(self.cfg)
            elif key == "wake_switch_input":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
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
        self.cfg["screen_off_method"] = value
        save_config(self.cfg)
        if value == "ddc":
            self.status_var.set("熄灭方式：单屏待机（键鼠不会点亮）")
        else:
            self.status_var.set("熄灭方式：系统级（整组熄灭，一定能唤回）")
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
        keep = [i for i in prev if i < len(self.monitors)]
        if not keep:
            keep = [i for i in resolve_monitors(self.cfg) if i < len(self.monitors)]
        return keep or list(range(len(self.monitors)))

    def refresh_monitors(self, silent=False):
        prev = self.selected_indices()
        targets = enumerate_targets()
        try:
            self.monitors = [{k: v for k, v in t.items() if k != "handle"}
                             for t in targets]
        finally:
            release_targets(targets)
        self.mon_info = [(t["dev"], t["w"], t["h"], t["primary"]) for t in self.monitors]
        self.mon_panel.rebuild(self._restore_selection(prev))
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
        missing = [i for i in targets
                   if i not in self.mon_models and 0 <= i < len(self.monitors)]
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
    def screen_time_text(self):
        if self.cfg.get("screen_off_method") == "ddc":
            return ""
        at = (self.cfg.get("screen_state_at") or "").strip()
        m = re.match(r"^\d{2}-\d{2} (\d{2}:\d{2})$", at)
        short = m.group(1) if m else at
        if self.cfg.get("screen_state") == "off":
            return ("%s 熄灭" % short) if short else ""
        return ("%s 点亮" % short) if short else ""

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
        if not states:
            # 轻量刷新（只读亮度）：不动亮灭结论，只更新提示行
            if bright and self._last_verdict:
                self._update_hint_brightness(bright)
            return
        v = screen_verdict(self.cfg, states)
        if v.get("woke_by_input"):
            mark_screen_state(self.cfg, "on")
            save_config(self.cfg)
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
        """DDC 模式下回读不可信：不显示亮灭结论（用户明确要求）。"""
        if self.cfg.get("screen_off_method") == "ddc":
            return {"state": "unknown", "hint": hint}
        return {"state": state, "hint": hint}

    def _remember_selection(self):
        idx, ids = selection_payload(self.selected_indices())
        self.cfg["monitors"] = idx
        self.cfg["monitor_ids"] = ids

    # ---------- 熄屏 / 点亮 ----------
    def screen_off(self):
        if self._busy:
            return
        targets = self.selected_indices()
        self.status_var.set("正在熄灭显示器…")
        self.run_bg(lambda: screen_off_cfg(self.cfg, targets),
                    self._finish_screen_off, busy=True)

    def _finish_screen_off(self, result):
        ok, errs = _as_ok_errs(result)
        if ok:
            mark_screen_state(self.cfg, "off")
            self._remember_selection()
            save_config(self.cfg)
            self.status_var.set("已熄灭显示器")
            self.status_card.render(self._status_payload(
                "off", "键鼠输入不会点亮它；点「点亮屏幕」恢复"))
        if errs:
            self.msg.showwarning("熄灭失败", "\n".join(
                ("显示器 %d：%s" % (i, e)) if i >= 0 else e for i, e in errs))
        mins = self._auto_relight_minutes()
        if ok and mins > 0:
            self._relight_pending = True
            threading.Timer(mins * 60,
                            lambda: self._read_queue.put(("hotkey", "on", None))).start()
        self.probe_screens()

    def screen_on(self):
        if self._busy:
            return
        targets = self.selected_indices()
        self.status_var.set("正在点亮显示器…")
        self.run_bg(lambda: screen_on_cfg(self.cfg, targets),
                    self._finish_screen_on, busy=True)

    def _finish_screen_on(self, result):
        ok, errs = _as_ok_errs(result)
        self._relight_pending = False
        if ok:
            mark_screen_state(self.cfg, "on")
            self._remember_selection()
            save_config(self.cfg)
            self.status_var.set("已点亮显示器")
            self.status_card.render(self._status_payload("on", "亮度与定时均正常"))
        if errs:
            detail = "\n".join(("显示器 %d：%s" % (i, e)) if i >= 0 else e
                               for i, e in errs)
            self.status_var.set("点亮失败：" + detail.replace("\n", " "))
            tip = ("\n\n若这台屏是 DP+HDMI 同时接线，把「点亮时允许切信号源」关掉"
                   "或改用「系统级」再试一次。"
                   if self.cfg.get("screen_off_method") == "ddc" else "")
            self.msg.showwarning("点亮失败", detail + tip)
        self.probe_screens()

    def _auto_relight_minutes(self):
        try:
            return max(0, min(120, int(self.cfg.get("screen_auto_relight_min", 0))))
        except Exception:
            return 0

    def _stop_tray(self):
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None

    def _start_hotkey_listener(self):
        if self._hotkeys_on:
            return
        self._hotkeys_on = True

        def listener():
            user32 = ctypes.windll.user32
            mods = 0x0001 | 0x0002 | 0x0004 | 0x4000
            ids = {1: ("off", 0x4F), 2: ("on", 0x50)}
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
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(30, 30, 30, 255),
                  outline=(74, 158, 255, 255), width=3)
        d.rounded_rectangle((14, 28, 50, 36), radius=4, fill=(74, 158, 255, 255))
        d.ellipse((36, 23, 52, 41), fill=(255, 255, 255, 255))
        return img

    def _tray_tooltip_text(self):
        if not self.cfg.get("enabled"):
            return "%s\n定时未启用" % APP_TITLE
        slots = self.cfg.get("slots", [])
        brief = "  ".join("%s→%d%%%s" % (x.get("time"), x.get("level", 0),
                                          "熄" if x.get("screen") == "off" else
                                          "亮" if x.get("screen") == "on" else "")
                           for x in slots)
        nxt = _next_run_text([parse_hhmm(x.get("time")) for x in slots])
        st = "熄灭中" if self.cfg.get("screen_state") == "off" else "点亮中"
        return ("%s\n定时: %s\n下次 %s\n屏幕: %s" % (APP_TITLE, brief, nxt, st))[:120]

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
        self.refresh_task_status()
        self.status_var.set("定时已启用")

    def _tray_quit(self):
        self._stop_tray()
        self._stop_hotkey_listener()
        self.root.destroy()

    def _on_close(self):
        if self.tray is not None:
            self.root.withdraw()
            self.status_var.set("已最小化到托盘（右下角图标，右键可退出）")
            return
        self._stop_hotkey_listener()
        self.root.destroy()

    # ---------- 启动自检 / 补执行 ----------
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
            self.sch_panel.set_events(read_recent_events(3))
        except Exception:
            pass

    def _start_auto_checks(self):
        """窗口出来之后才跑：任务定义自检 + 今天错过档位的补执行（全后台）。"""
        if self._auto_checked:
            return
        self._auto_checked = True
        if not self.cfg.get("enabled"):
            self._refresh_events()
            return

        def job():
            out = {}
            try:
                out["task"] = ensure_task_registration(self.cfg)
            except Exception as e:
                out["task"] = (False, "任务自检失败：%s: %s" % (type(e).__name__, e))
            try:
                out["catchup"] = self._catch_up_worker()
            except Exception as e:
                out["catchup"] = {"due": True, "done": False,
                                  "line": "补执行失败：%s: %s" % (type(e).__name__, e)}
            return out

        self.run_bg(job, self._finish_auto_checks)

    def _catch_up_worker(self):
        """后台线程执行：只在今天该档错过且未执行时补一次（判定见 catch_up_decision）。"""
        cfg = load_config()
        decision = catch_up_decision(cfg)
        if not decision["due"]:
            return {"due": False, "reason": decision["reason"]}
        slot = dict(decision["slot"])
        level, screen, ok, errs = apply_slot(cfg, slot)
        now = datetime.now()
        if ok:
            line = "今天 %s 补执行 %s 档（错过 %d 分钟）" % (
                now.strftime("%H:%M"), slot["time"], decision["missed_min"])
        else:
            line = "补执行 %s 档失败（%d 台未写入）" % (
                slot["time"], len([e for e in errs if e[0] >= 0]))
        _write_event_log(line)
        return {"due": True, "done": bool(ok), "slot": slot,
                "missed_min": decision["missed_min"], "line": line, "errs": errs}

    def _finish_auto_checks(self, result):
        if isinstance(result, Exception):
            self.status_var.set("启动自检失败：%s" % result)
            self._refresh_events()
            return
        _task_ok, task_msg = result.get("task", (True, ""))
        cu = result.get("catchup") or {}
        if task_msg:
            self.status_var.set(task_msg)
        if cu.get("due"):
            self.status_var.set(cu.get("line", ""))
            if not cu.get("done"):
                errs = cu.get("errs") or []
                for i, why in errs[:2]:
                    _write_event_log("  %s" % why)
        # 补执行写盘后，把本进程手里的 last_fired 同步一下，避免后续保存领先
        if cu.get("done"):
            self.cfg["last_fired"] = coerce_setting(
                "last_fired", load_config().get("last_fired"))
        self._refresh_events()
        if task_msg or cu.get("due"):
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
            self.status_var.set("重写任务计划失败：%s" % out)
            self.msg.showerror("重写任务计划失败", out or "未知错误")
            return
        _write_event_log("时间段已更新，任务计划已重写")
        self.refresh_task_status()
        self.status_var.set("定时已更新")
        self._refresh_events()
        # 新加/改到"已经过去的时刻"时，今天这一档应当立即生效
        if self.cfg.get("enabled"):
            self.run_bg(self._catch_up_worker, self._finish_auto_checks)

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
        part = {"monitors": idx, "monitor_ids": ids, "enabled": True,
                "catch_up_on_logon": bool(self.cfg.get("catch_up_on_logon", True)),
                "slots": slots}
        self.cfg.update(part)
        ok, msg = save_config(self.cfg)
        if not ok:
            self.msg.showerror("保存失败", msg)
            return
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
            # 开关是"切换即注册"：注册失败要把开关拨回去，别让界面说谎
            self.cfg["enabled"] = False
            save_config(self.cfg)
            self.sch_panel.set_enabled_flag(False)
            return
        self.refresh_task_status()
        self.status_var.set("定时已启用")
        self.msg.showinfo("定时已保存", _saved_summary(part, out))
        # 启用时如果今天这一档已经过去（例如 12:08 才启用 08:30），立即补一次
        self.run_bg(self._catch_up_worker, self._finish_auto_checks)

    def cancel_schedule(self):
        self.cfg["enabled"] = False
        save_config(self.cfg)
        self.sch_panel.set_enabled_flag(False)
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
        self.refresh_task_status()
        self.status_var.set("已取消定时任务" if ok else "取消定时任务时出现问题：%s" % out)

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
    """熄灭/点亮选中的显示器（配置里的选择；空 = 全部），并记账屏幕状态。"""
    cfg = load_config()
    targets = resolve_monitors(cfg)
    if action == "off":
        ok, errs = screen_off_cfg(cfg, targets)
        _out("已熄灭显示器" if ok else "熄灭失败")
    else:
        ok, errs = screen_on_cfg(cfg, targets)
        _out("已点亮显示器" if ok else "点亮失败")
    if ok:
        mark_screen_state(cfg, action)
        save_config(cfg)
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
            arg = args[1].strip().lower()
            if arg == "status":
                # 与界面同一套结论（记账为主，附一行依据）
                _out(screen_verdict(load_config())["text"])
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
    enable_dpi_awareness()                      # 必须在创建任何 Tk 窗口之前
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
    App(root, start_minimized=("--minimized" in args))
    apply_dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
