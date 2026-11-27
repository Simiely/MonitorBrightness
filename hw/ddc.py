# -*- coding: utf-8 -*-
"""DDC/CI 原语（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

只做枚举/能力串/亮度读写/探测；不碰配置策略、不碰拓扑、不碰 Tk。
"""
import ctypes
import hashlib
import os
import re
from ctypes import wintypes

from core.config import _CAPS_CACHE
from domain.timeutil import clamp_level

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
