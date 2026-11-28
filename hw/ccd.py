# -*- coding: utf-8 -*-
"""CCD 查询/提交原语（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

只做 SetDisplayConfig 的查询、快照、应用、回读；拓扑策略在上层 hw/topology.py。
"""
import ctypes
import os
import time
from ctypes import wintypes
from datetime import datetime

from core.logging import dlog
from hw.ccd_structs import (
    _CCD_APPLY_FLAGS, _CCD_EDID_IDS_VALID, _CCD_GET_SOURCE_NAME,
    _CCD_GET_TARGET_NAME, _CCD_PATH_ACTIVE, _CCD_TARGET_IN_USE,
    _CcdModeInfo, _CcdPathInfo, _CcdSourceName, _CcdTargetName,
    _QDC_ALL_PATHS, _QDC_VIRTUAL_MODE_AWARE, TOPO_VERIFY_TIMEOUT,
)

def _ccd_setup_probe_api(u32):
    """声明只读探测用到的三个 API 签名（不调用）。"""
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

def _ccd_query_all_paths(u32, flags):
    """QDC_ALL_PATHS 查询，缓冲区不足（122）时最多重试 4 次。

    返回 (paths, modes, mc) 或 None。只读，失败一律 None。
    """
    paths = modes = None
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
            return paths, modes, mc.value
        if rc != 122:                 # ERROR_INSUFFICIENT_BUFFER 才重试
            return None
    return None

def _ccd_rows_from_paths(u32, paths, modes):
    """把每条 path 翻成一行（同一块屏可能有多条 path，由折叠步骤处理）。"""
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
    return rows

def _ccd_merge_rows_by_anchor(rows):
    """按锚点折叠：active 的那条胜出，其余 gdi 存进 aliases。

    QDC_ALL_PATHS 会为同一块屏返回多个 source（实测 233 条 path 只有 3 块屏）。
    """
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
        _ccd_setup_probe_api(u32)
        got = _ccd_query_all_paths(u32, _QDC_ALL_PATHS | _QDC_VIRTUAL_MODE_AWARE)
        if not got:
            return []
        paths, modes, mc = got
        return _ccd_merge_rows_by_anchor(
            _ccd_rows_from_paths(u32, paths, modes))
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
    """给出亮灭结论：v2.04 起只依据 CCD 拓扑的 ACTIVE flag。

    `probes` 参数保留是为了兼容旧调用方，已被忽略——程序不再用 0xD6 回读
    判断亮灭（它在面板已黑时仍可能报 1）。读不到就说"不知道"。
    """
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

def _ccd_query_raw_paths(u32):
    """取 QueryDisplayConfig 的原始缓冲；(paths, modes, pc, mc)，失败 None。"""
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
    return paths, modes, pc.value, mc.value

def _ccd_query_rows(u32, paths):
    """把 paths 解析成未折叠的 rows（anchor/active/gdi/friendly/vdd）。"""
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
    return rows

def _ccd_query_raw(u32=None):
    """返回 (paths, modes, pc, mc, rows)；失败返回 None。

    rows 每条 path 一项（不折叠）：anchor / active / connected / gdi /
    friendly / vdd / pathIndex。paths + modes 是原始字节快照的来源。
    """
    u32 = u32 if u32 is not None else _ccd_api()
    if u32 is None:
        return None
    got = _ccd_query_raw_paths(u32)
    if got is None:
        return None
    paths, modes, pc, mc = got
    return paths, modes, pc, mc, _ccd_query_rows(u32, paths)

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
