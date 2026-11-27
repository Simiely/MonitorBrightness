# -*- coding: utf-8 -*-
"""CCD 几何与布局辅助（v2.03.0 R2，从 hw/topology.py 逐字拆出）。

位置读写、source mode 合成、快照/应用等纯几何操作；拓扑状态机在 hw/topology.py。
"""
import ctypes
from ctypes import wintypes

from core.logging import dlog
from hw.ccd import _ccd_apply
from hw.ccd_structs import (
    _CCD_MODE_IDX_INVALID, _CCD_PATH_ACTIVE, _CCD_PIXELFORMAT_NONGDI,
    _CCD_TYPE_SOURCE, _CcdModeInfo, _CcdTargetPreferredMode,
    _DC_GET_TARGET_PREFERRED_MODE,
)

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
