#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示器拓扑**只读**探针（A1-A3 调研用）。

只调用 Windows CCD 的查询 API + EnumDisplayMonitors + dxva2 读取，
**绝不调用 SetDisplayConfig / ChangeDisplaySettings / DisplaySwitch**，
也不会改任何显示设置。可以在任意 Windows 10/11 + Python 3.10+ 上运行，
无第三方依赖。

用法：
    python scripts/topology_probe.py               # 人读表格
    python scripts/topology_probe.py --json        # 机器可读
    python scripts/topology_probe.py --out x.md    # 同时存一份（只写指定文件）

输出分四段：
    1. 环境 / 结构体尺寸自检（跨机器对齐用）
    2. CCD path（按"同一块屏"折叠，可用 --all-paths 展开）
    3. EnumDisplayMonitors 与 PnP DeviceID
    4. 汇总表 + 建议的稳定锚点（供 D1 决策）

字段口径（已与官方文档对齐）：
    ACTIVE           = path.flags & 0x1，表示 Windows 正在这条 path 上输出
    targetAvailable  = 1 表示设备仍连接（被禁用也仍是 1）；0 才是真断开/拔线
    edidMfr/edidProd = DISPLAYCONFIG_TARGET_DEVICE_NAME 的 EDID 标识
    connectorInstance= 同一显卡同类接口的第几个口（1-based，唯一才为非 0）
"""

import argparse
import ctypes
import hashlib
import json
import os
import platform
import sys
from ctypes import wintypes

# ---------------------------------------------------------------- 常量
QDC_ALL_PATHS = 0x00000001
QDC_ONLY_ACTIVE_PATHS = 0x00000002
QDC_VIRTUAL_MODE_AWARE = 0x00000010
PATH_ACTIVE = 0x00000001
MODE_IDX_INVALID = 0xFFFFFFFF
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
DISPLAYCONFIG_DEVICE_INFO_GET_ADAPTER_NAME = 4
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004
CCHDEVICENAME = 32
ERROR_SUCCESS = 0
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ACCESS_DENIED = 5
ERROR_NOT_SUPPORTED = 50

VCP_BRIGHTNESS = 0x10
VCP_POWER_MODE = 0xD6


# ---------------------------------------------------------------- 结构体
# 布局逐字段照 Windows SDK（wingdi.h / winuser.h）声明；
# 与 sunshine-lan-streaming 的 Hide-Vdd.ps1 里已实机验证的 C# 版本一致。
class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.DWORD), ("Denominator", wintypes.DWORD)]


class SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]


class TARGET_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("outputTechnology", wintypes.UINT),
                ("rotation", wintypes.UINT), ("scaling", wintypes.UINT),
                ("refreshRate", RATIONAL), ("scanLineOrdering", wintypes.UINT),
                ("targetAvailable", ctypes.c_int), ("statusFlags", wintypes.UINT)]


class PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", SOURCE_INFO), ("targetInfo", TARGET_INFO),
                ("flags", wintypes.UINT)]


class POINTL(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SOURCE_MODE(ctypes.Structure):
    _fields_ = [("width", wintypes.UINT), ("height", wintypes.UINT),
                ("pixelFormat", wintypes.UINT), ("position", POINTL),
                ("reserved", wintypes.UINT)]


class MODE_INFO(ctypes.Structure):
    # infoType(4) + id(4) + adapterId(8) + union(48) = 64
    _fields_ = [("infoType", wintypes.UINT), ("id", wintypes.UINT),
                ("adapterId", LUID), ("sourceMode", SOURCE_MODE),
                ("tail", ctypes.c_uint64 * 3)]


class DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", LUID), ("id", wintypes.UINT)]


class SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DEVICE_INFO_HEADER),
                ("viewGdiDeviceName", wintypes.WCHAR * CCHDEVICENAME)]


class TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DEVICE_INFO_HEADER), ("flags", wintypes.UINT),
                ("outputTechnology", wintypes.UINT),
                ("edidManufactureId", wintypes.WORD),
                ("edidProductCodeId", wintypes.WORD),
                ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]


class ADAPTER_NAME(ctypes.Structure):
    _fields_ = [("header", DEVICE_INFO_HEADER),
                ("adapterDevicePath", wintypes.WCHAR * 128)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", wintypes.HANDLE),
                ("description", wintypes.WCHAR * 128)]


# ---------------------------------------------------------------- API 声明
def _api():
    u32 = ctypes.windll.user32
    u32.GetDisplayConfigBufferSizes.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT),
                                                 ctypes.POINTER(wintypes.UINT)]
    u32.GetDisplayConfigBufferSizes.restype = wintypes.LONG
    u32.QueryDisplayConfig.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT),
                                       ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                                       ctypes.c_void_p, ctypes.c_void_p]
    u32.QueryDisplayConfig.restype = wintypes.LONG
    u32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
    u32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG
    u32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    u32.EnumDisplayDevicesW.restype = wintypes.BOOL

    dx = ctypes.windll.dxva2
    dx.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR,
                                                           ctypes.POINTER(wintypes.DWORD)]
    dx.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
    dx.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, wintypes.DWORD,
                                                   ctypes.c_void_p]
    dx.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
    dx.GetVCPFeatureAndVCPFeatureReply.argtypes = [wintypes.HANDLE, wintypes.BYTE,
                                                   ctypes.c_void_p,
                                                   ctypes.POINTER(wintypes.DWORD),
                                                   ctypes.POINTER(wintypes.DWORD)]
    dx.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL
    dx.DestroyPhysicalMonitor.argtypes = [wintypes.HANDLE]
    dx.DestroyPhysicalMonitor.restype = wintypes.BOOL
    return u32, dx


# ---------------------------------------------------------------- 采集
def query_paths(u32, flags=QDC_ALL_PATHS | QDC_VIRTUAL_MODE_AWARE):
    """带 ERROR_INSUFFICIENT_BUFFER 重试的只读查询。"""
    for _ in range(5):
        pc, mc = wintypes.UINT(), wintypes.UINT()
        rc = u32.GetDisplayConfigBufferSizes(flags, ctypes.byref(pc), ctypes.byref(mc))
        if rc != ERROR_SUCCESS:
            return rc, [], []
        paths = (PATH_INFO * pc.value)()
        modes = (MODE_INFO * mc.value)()
        rc = u32.QueryDisplayConfig(flags, ctypes.byref(pc), ctypes.byref(paths),
                                    ctypes.byref(mc), ctypes.byref(modes), None)
        if rc == ERROR_SUCCESS:
            return rc, list(paths)[:pc.value], list(modes)[:mc.value]
        if rc != ERROR_INSUFFICIENT_BUFFER:
            return rc, [], []
    return ERROR_INSUFFICIENT_BUFFER, [], []


def dev_info(u32, kind, adapter_id, ident, cls):
    buf = cls()
    buf.header.type = kind
    buf.header.size = ctypes.sizeof(cls)
    buf.header.adapterId = adapter_id
    buf.header.id = ident
    rc = u32.DisplayConfigGetDeviceInfo(ctypes.byref(buf.header))
    return rc, buf


def luid_str(luid):
    return "%08X:%08X" % (luid.LowPart & 0xFFFFFFFF, luid.HighPart & 0xFFFFFFFF)


def mode_idx_text(value):
    return "invalid" if value == MODE_IDX_INVALID else \
        "clone=%d/src=%d" % (value & 0xFFFF, value >> 16)


def stable_anchor(info):
    """建议的稳定锚点：EDID 厂商+产品+接口实例+显卡 LUID。"""
    if info["edidIdsValid"]:
        return "%s/%04X/%04X/%d" % (info["adapterLuid"], info["edidManufactureId"],
                                    info["edidProductCodeId"], info["connectorInstance"])
    return "%s/name=%s" % (info["adapterLuid"], info["friendlyName"])


def collect_paths(u32):
    rc, paths, modes = query_paths(u32)
    if rc != ERROR_SUCCESS:
        return rc, []
    out = []
    for p in paths:
        src = p.sourceInfo
        tgt = p.targetInfo
        src_name = ""
        rc1, sb = dev_info(u32, DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME,
                           src.adapterId, src.id, SOURCE_DEVICE_NAME)
        if rc1 == ERROR_SUCCESS:
            src_name = sb.viewGdiDeviceName

        friendly = dev_path = adapter_path = ""
        mfr = prod = conn = 0
        rc2, tb = dev_info(u32, DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME,
                           tgt.adapterId, tgt.id, TARGET_DEVICE_NAME)
        if rc2 == ERROR_SUCCESS:
            friendly = tb.monitorFriendlyDeviceName
            dev_path = tb.monitorDevicePath
            mfr, prod, conn = (tb.edidManufactureId, tb.edidProductCodeId,
                               tb.connectorInstance)
        rc3, ab = dev_info(u32, DISPLAYCONFIG_DEVICE_INFO_GET_ADAPTER_NAME,
                           src.adapterId, src.id, ADAPTER_NAME)
        if rc3 == ERROR_SUCCESS:
            adapter_path = ab.adapterDevicePath

        pos = wh = ""
        smi = src.modeInfoIdx >> 16
        if smi != 0xFFFF and smi < len(modes):
            sm = modes[smi].sourceMode
            pos = "(%d,%d)" % (sm.position.x, sm.position.y)
            wh = "%dx%d" % (sm.width, sm.height)
        rr = tgt.refreshRate
        hz = round(rr.Numerator / rr.Denominator, 3) if rr.Denominator else 0

        item = {
            "active": bool(p.flags & PATH_ACTIVE),
            "targetAvailable": bool(tgt.targetAvailable),
            "sourceId": src.id,
            "targetId": tgt.id,
            "adapterLuid": luid_str(src.adapterId),
            "gdiName": src_name,
            "friendlyName": friendly,
            "monitorDevicePath": dev_path,
            "adapterDevicePath": adapter_path,
            "edidIdsValid": bool(mfr or prod),
            "edidManufactureId": mfr,
            "edidProductCodeId": prod,
            "connectorInstance": conn,
            "outputTechnology": tgt.outputTechnology,
            "sourceModeIdx": mode_idx_text(src.modeInfoIdx),
            "targetModeIdx": mode_idx_text(tgt.modeInfoIdx),
            "position": pos,
            "size": wh,
            "refreshHz": hz,
            "pathFlags": p.flags,
        }
        item["stableAnchor"] = stable_anchor(item)
        out.append(item)
    return ERROR_SUCCESS, out


def enum_monitors(u32, dx, read_ddc=True):
    """EnumDisplayMonitors + PnP DeviceID + （可选）DDC 只读回读。"""
    hmons = []

    class MI(MONITORINFOEXW):
        pass

    def cb(hmon, hdc, rect, lparam):
        mi = MI()
        mi.cbSize = ctypes.sizeof(mi)
        if u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            hmons.append((hmon, mi))
        return 1

    proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)
    u32.EnumDisplayMonitors(None, None, proto(cb), None)

    result = []
    for hmon, mi in hmons:
        dev = str(mi.szDevice)
        short = dev.split("\\")[-1]
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        ok = u32.EnumDisplayDevicesW(dev, 0, ctypes.byref(dd), 0)
        item = {
            "gdiName": dev,
            "shortName": short,
            "rect": [mi.rcMonitor.left, mi.rcMonitor.top,
                     mi.rcMonitor.right, mi.rcMonitor.bottom],
            "primary": bool(mi.dwFlags & 1),
            "deviceId": str(dd.DeviceID) if ok else "",
            "deviceIdHash": hashlib.sha1(str(dd.DeviceID).encode("utf-8")).hexdigest()[:8]
                            if ok else "",
            "stateFlags": dd.StateFlags if ok else 0,
            "ddc": None,
            "ddcError": "",
        }
        if read_ddc:
            try:
                count = wintypes.DWORD()
                if dx.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
                    arr = (PHYSICAL_MONITOR * max(1, count.value))()
                    if dx.GetPhysicalMonitorsFromHMONITOR(hmon, count.value,
                                                          ctypes.byref(arr)):
                        vals = {}
                        for pm in list(arr)[:count.value]:
                            for code, key in ((VCP_BRIGHTNESS, "brightness"),
                                              (VCP_POWER_MODE, "power")):
                                cur, mx = wintypes.DWORD(), wintypes.DWORD()
                                if dx.GetVCPFeatureAndVCPFeatureReply(
                                        pm.handle, wintypes.BYTE(code), None,
                                        ctypes.byref(cur), ctypes.byref(mx)):
                                    vals[key] = [cur.value, mx.value]
                                else:
                                    vals[key] = None
                            try:
                                dx.DestroyPhysicalMonitor(pm.handle)
                            except Exception:
                                pass
                        item["ddc"] = vals
                    else:
                        item["ddcError"] = "GetPhysicalMonitorsFromHMONITOR 失败"
                else:
                    item["ddcError"] = "GetNumberOfPhysicalMonitorsFromHMONITOR 失败"
            except Exception as e:
                item["ddcError"] = "%s: %s" % (type(e).__name__, e)
        result.append(item)
    return result


# ---------------------------------------------------------------- 输出
def group_connected(paths):
    """把 QDC_ALL_PATHS 的 source×target 组合按"同一块屏"折叠。

    实测：QDC_ALL_PATHS 会返回 233 条 path，但只有 2 块物理屏 + 1 块 VDD；
    同一块物理屏会对应多个 source（DISPLAY1/2/3/4 都指向它）。
    稳定锚点（显卡 LUID + EDID mfr/prod + 接口实例）才是"屏"的粒度。
    """
    groups = {}
    for p in paths:
        if not p["targetAvailable"]:
            continue
        key = p["stableAnchor"]
        g = groups.setdefault(key, {
            "anchor": key, "friendly": p["friendlyName"], "pathCount": 0,
            "targetIds": [], "gdis": [], "activeGdis": [],
            "vdd": False, "outputTech": p["outputTechnology"],
        })
        g["pathCount"] += 1
        tid = "%s/%d" % (p["adapterLuid"], p["targetId"])
        if tid not in g["targetIds"]:
            g["targetIds"].append(tid)
        if p["gdiName"] and p["gdiName"] not in g["gdis"]:
            g["gdis"].append(p["gdiName"])
        if p["active"] and p["gdiName"] and p["gdiName"] not in g["activeGdis"]:
            g["activeGdis"].append(p["gdiName"])
        if any(k in p["friendlyName"].lower() for k in ("vdd", "virtual", "iddcx")):
            g["vdd"] = True
    return groups


def render(data, as_json=False, all_paths=False):
    if as_json:
        return json.dumps(data, ensure_ascii=False, indent=2)

    L = []
    add = L.append
    grouped = group_connected(data["paths"])
    e = data["environment"]
    add("=== 环境 / 结构体尺寸自检 ===")
    add("系统: %s" % e["system"])
    add("Python: %s (%s)" % (e["python"], e["arch"]))
    for k, v in e["structSizes"].items():
        add("  sizeof(%-22s) = %d" % (k, v))
    add("")

    if data["queryError"] != ERROR_SUCCESS:
        add("!! QueryDisplayConfig 失败 rc=%d（%s）" %
            (data["queryError"],
             {ERROR_ACCESS_DENIED: "不在控制台/远程会话",
              ERROR_NOT_SUPPORTED: "驱动不支持 WDDM"}.get(data["queryError"], "见 Win32 错误码")))
    else:
        paths = data["paths"]
        grouped = group_connected(paths)
        inactive = [p for p in paths if not p["targetAvailable"]]
        add("=== CCD 查询汇总 ===")
        add("QDC_ALL_PATHS 共 %d 条 path：已连接 %d 条（折叠为 %d 块屏），未连接组合 %d 条（已折叠）" %
            (len(paths), len(paths) - len(inactive), len(grouped), len(inactive)))
        add("⚠ DISPLAYn 不是屏的标识：同一块屏可对应多个 source（见下表 DISPLAYn 列）")
        add("")
        for i, g in enumerate(sorted(grouped.values(), key=lambda x: (x["vdd"], x["friendly"])), 1):
            state = "ACTIVE" if g["activeGdis"] else "inactive(已断开)"
            tag = " ★虚拟屏" if g["vdd"] else ""
            add("#%d  %s%s  %s" % (i, state, tag, g["friendly"] or "(无友好名)"))
            add("   稳定锚点  %s" % g["anchor"])
            add("   所有 DISPLAYn  %s   ← ACTIVE: %s" %
                (", ".join(g["gdis"]) or "-", ", ".join(g["activeGdis"]) or "无"))
            add("   target id   %s    outputTechnology=%d" %
                (", ".join(g["targetIds"]) or "-", g["outputTech"]))
            add("   path 数     %d（多条 source 指向同一块屏）" % g["pathCount"])
            add("")
        add("--- 未连接的 source×target 组合（%d 条，仅统计不展开）---" % len(inactive))
        if inactive:
            sample = inactive[0]
            add("示例：adapter=%s sourceId=%d targetId=%d gdi=%s" %
                (sample["adapterLuid"], sample["sourceId"], sample["targetId"],
                 sample["gdiName"]))
        add("")

    add("=== EnumDisplayMonitors（桌面内可见） ===")
    for m in data["monitors"]:
        ddc = m["ddc"]
        ddc_text = ("brightness=%s power=%s" % (ddc.get("brightness"), ddc.get("power"))
                    if ddc else "未读到（%s）" % (m["ddcError"] or "无物理句柄"))
        add("%-12s rect=%s primary=%s deviceIdHash=%s" %
            (m["shortName"], m["rect"], m["primary"], m["deviceIdHash"]))
        add("    %s" % ddc_text)
    if not data["monitors"]:
        add("（无）")

    add("")
    add("=== 汇总：屏 ↔ DISPLAYn ↔ 稳定锚点 ↔ DDC ===")
    add("| # | 状态 | 类别 | 显示器 | 所有 DISPLAYn | DeviceID哈希 | 稳定锚点 | DDC |")
    add("|---|---|---|---|---|---|---|---|")
    for i, g in enumerate(sorted(grouped.values(), key=lambda x: (x["vdd"], x["friendly"])), 1):
        md = next((m for m in data["monitors"]
                   if m["gdiName"].lower() in [x.lower() for x in g["gdis"]]), None)
        ddc = ("%s/%s" % (md["ddc"]["brightness"][0], md["ddc"]["power"][0])
               if md and md["ddc"] and md["ddc"].get("brightness") and md["ddc"].get("power")
               else "-")
        add("| %d | %s | %s | %s | %s | %s | %s | %s |" %
            (i, "ACTIVE" if g["activeGdis"] else "inactive",
             "虚拟屏" if g["vdd"] else "物理屏",
             g["friendly"] or "-", ", ".join(g["gdis"]) or "-",
             md["deviceIdHash"] if md else "-", g["anchor"], ddc))
    if all_paths:
        add("")
        add("=== 原始全部 path（--all-paths） ===")
        for i, p in enumerate(paths, 1):
            add("#%d active=%s connected=%s adapter=%s src=%d(%s) tgt=%d(%s) anchor=%s" %
                (i, p["active"], p["targetAvailable"], p["adapterLuid"],
                 p["sourceId"], p["gdiName"], p["targetId"], p["outputTechnology"],
                 p["stableAnchor"]))
    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser(description="显示器拓扑只读探针（不改任何显示设置）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--out", help="同时把文本输出写到指定文件")
    parser.add_argument("--no-ddc", action="store_true", help="跳过 DDC 读取（更快）")
    parser.add_argument("--all-paths", action="store_true", help="额外展开全部原始 path（默认折叠）")
    args = parser.parse_args()

    if os.name != "nt":
        print("本探针仅支持 Windows")
        return 2

    u32, dx = _api()
    rc, paths = collect_paths(u32)
    data = {
        "environment": {
            "system": platform.platform(),
            "python": platform.python_version(),
            "arch": platform.architecture()[0],
            "structSizes": {
                "PATH_INFO": ctypes.sizeof(PATH_INFO),
                "SOURCE_INFO": ctypes.sizeof(SOURCE_INFO),
                "TARGET_INFO": ctypes.sizeof(TARGET_INFO),
                "MODE_INFO": ctypes.sizeof(MODE_INFO),
                "DEVICE_INFO_HEADER": ctypes.sizeof(DEVICE_INFO_HEADER),
                "TARGET_DEVICE_NAME": ctypes.sizeof(TARGET_DEVICE_NAME),
            },
        },
        "queryError": rc,
        "paths": paths,
        "monitors": enum_monitors(u32, dx, read_ddc=not args.no_ddc),
    }
    text = render(data, as_json=args.json, all_paths=args.all_paths)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("\n[已写入] %s" % args.out)
    return 0 if rc == ERROR_SUCCESS else 1


if __name__ == "__main__":
    sys.exit(main())
