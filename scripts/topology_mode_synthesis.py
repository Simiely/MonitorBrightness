# -*- coding: utf-8 -*-
"""侦察：真实断开后 source mode 没了，接回该怎么提交？（全程 SDC_VALIDATE）

背景：v2.01.1 在真机上点「点亮屏幕」弹的错是
  「锚点 … 的 source mode 不在当前 modes 数组里」

也就是说：一块屏被断开之后，Windows 就不再把它的 SOURCE mode 放进
`QueryDisplayConfig` 返回的 modes 数组了，于是没有任何下标可引用。
（target 侧也一样变成 INVALID，但那一侧可以交 Windows 定。）

**不需要真断开就能研究这件事**：`QDC_ALL_PATHS` 里本来就有"inactive、
target 还连着、且 source id 空闲"的 path —— 那正是断开后的样子。
拿它当样本，用 `SDC_VALIDATE` 试提交，一个像素都不会变。

要回答的问题：能不能**自己往 modes 数组里补一条 SOURCE mode 项**，
让 `sourceInfo.modeInfoIdx` 指向它？这条项的内容（宽/高/pixelFormat/
桌面位置）该从哪儿来？

用法：python scripts/topology_mode_synthesis.py
"""
import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import monitor_brightness as mb                                # noqa: E402

_SDC_VALIDATE = 0x00000040
_DC_GET_TARGET_PREFERRED = 3          # DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_PREFERRED_MODE
_PIXELFORMAT_32BPP = 3
_PIXELFORMAT_NONGDI = 4
_ERR = {0: "OK", 87: "ERROR_INVALID_PARAMETER", 31: "ERROR_GEN_FAILURE",
        5: "ERROR_ACCESS_DENIED", 1168: "ERROR_NOT_FOUND"}


class _Ccd2D(ctypes.Structure):
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]


class _CcdVideoSignal(ctypes.Structure):
    _fields_ = [("pixelRate", ctypes.c_uint64),
                ("hSyncFreq", mb._CcdRational), ("vSyncFreq", mb._CcdRational),
                ("activeSize", _Ccd2D), ("totalSize", _Ccd2D),
                ("videoStandard", wintypes.UINT),
                ("scanLineOrdering", wintypes.UINT)]


class _CcdTargetPreferred(ctypes.Structure):
    _fields_ = [("header", mb._CcdInfoHeader),
                ("width", wintypes.UINT), ("height", wintypes.UINT),
                ("targetMode", _CcdVideoSignal)]


def vflags(f):
    return (f | _SDC_VALIDATE) & ~(mb._SDC_APPLY | mb._SDC_SAVE_TO_DATABASE)


def check(u32, paths, modes, pc, mc, label):
    rc = u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes),
                              vflags(mb._CCD_APPLY_FLAGS))
    print("  %-52s rc=%-4d %s" % (label, rc, _ERR.get(rc, "")))
    return rc


def clone_paths(paths, n):
    out = (mb._CcdPathInfo * n)()
    ctypes.memmove(ctypes.byref(out), ctypes.byref(paths), ctypes.sizeof(out))
    return out


def grow_modes(modes, mc, extra):
    """在 modes 数组末尾追加一条，返回 (新数组, 新下标)。"""
    out = (mb._CcdModeInfo * (mc + 1))()
    ctypes.memmove(ctypes.byref(out), ctypes.byref(modes),
                   ctypes.sizeof(mb._CcdModeInfo) * mc)
    i = mc
    out[i].infoType = extra["infoType"]
    out[i].id = extra["id"]
    out[i].adapterId.LowPart = extra["luid"][0]
    out[i].adapterId.HighPart = extra["luid"][1]
    if extra["infoType"] == 1:
        out[i].sourceMode.width = extra["w"]
        out[i].sourceMode.height = extra["h"]
        out[i].sourceMode.pixelFormat = extra.get("pf", _PIXELFORMAT_32BPP)
        out[i].sourceMode.position.x = extra["x"]
        out[i].sourceMode.position.y = extra["y"]
    return out, i


def preferred_mode(u32, adapter_id, target_id):
    """问 Windows：这块屏的首选模式是多少。"""
    info = _CcdTargetPreferred()
    info.header.type = _DC_GET_TARGET_PREFERRED
    info.header.size = ctypes.sizeof(info)
    info.header.adapterId.LowPart = adapter_id.LowPart
    info.header.adapterId.HighPart = adapter_id.HighPart
    info.header.id = target_id
    rc = u32.DisplayConfigGetDeviceInfo(ctypes.byref(info.header))
    if rc != 0:
        return None, rc
    return (int(info.width), int(info.height)), 0


def main():
    u32 = mb._ccd_api()
    if u32 is None:
        print("非 Windows")
        return 2
    u32.SetDisplayConfig.argtypes = [wintypes.UINT, ctypes.c_void_p,
                                     wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    u32.SetDisplayConfig.restype = wintypes.LONG

    raw = mb._ccd_query_raw(u32)
    if raw is None:
        print("CCD 查询失败")
        return 2
    paths, modes, pc, mc, rows = raw
    print("pc=%d mc=%d  sizeof(ModeInfo)=%d  sizeof(PreferredMode)=%d"
          % (pc, mc, ctypes.sizeof(mb._CcdModeInfo), ctypes.sizeof(_CcdTargetPreferred)))

    print("\nmodes 数组全量")
    print("  %-4s %-8s %-6s %-24s %s" % ("idx", "infoType", "id", "adapterId", "内容"))
    for i in range(mc):
        m = modes[i]
        lu = "%08X:%08X" % (m.adapterId.LowPart & 0xFFFFFFFF,
                            m.adapterId.HighPart & 0xFFFFFFFF)
        if int(m.infoType) == 1:
            body = "%dx%d pf=%d pos=(%d,%d)" % (
                m.sourceMode.width, m.sourceMode.height, m.sourceMode.pixelFormat,
                m.sourceMode.position.x, m.sourceMode.position.y)
        else:
            body = "（target/desktop，不展开）"
        print("  %-4d %-8d %-6d %-24s %s" % (i, m.infoType, m.id, lu, body))

    # ---- 样本：整块锚点都不在拓扑里（该 anchor 没有任何 ACTIVE path）----
    # 两个坑（本脚本第一版全踩了，全被判 87，白折腾）：
    #   1. 不能只看"source id 空不空"：同一块屏有多条 path（本机 4 条），
    #      inactive 那些的 source 虽然"没被占"，但把同一块屏的第二条 path
    #      置 ACTIVE 等于"这块屏接两次"，Windows 一律拒。
    #   2. source id 只在 **adapter 内** 唯一，不是全局：VDD 在另一个 adapter
    #      （1589A778），它的 src=0 和物理屏的 src=0 不冲突。
    def luid_of(r):
        return r["anchor"].split("/")[0] if r.get("anchor") else ""

    active_anchors = {r["anchor"] for r in rows if r.get("active") and r.get("connected")}
    used_src = {(luid_of(r), int(r["srcId"])) for r in rows
                if r.get("active") and r.get("connected")}
    cands = [r for r in rows
             if r.get("anchor") and r.get("connected") and not r.get("active")
             and r["anchor"] not in active_anchors
             and (luid_of(r), int(r["srcId"])) not in used_src]
    if not cands:
        print("\n没有可用样本（没有整块不在拓扑里的已连接屏）")
        return 1
    victim = min(cands, key=lambda r: (int(r["srcId"]), int(r["pathIndex"])))
    p0 = paths[victim["pathIndex"]]
    print("\n样本 path#%d anchor=%s src=%s tgt=%s（整块屏不在拓扑里）%s"
          % (victim["pathIndex"], victim["anchor"], victim["srcId"], victim["tgtId"],
             "  [VDD]" if victim.get("vdd") else ""))
    print("  当前 srcModeIdx=0x%08X tgtModeIdx=0x%08X"
          % (int(p0.sourceInfo.modeInfoIdx), int(p0.targetInfo.modeInfoIdx)))

    pm, rc_pm = preferred_mode(u32, p0.targetInfo.adapterId, p0.targetInfo.id)
    print("  首选模式查询：rc=%d %s" % (rc_pm, pm))

    # 现算 target/desktop mode（来源：仍活着的同锚点 path）
    live_same = [r for r in rows if r["anchor"] == victim["anchor"] and r["active"]
                 and r.get("connected")]
    tgt_mode = tgt_desk = None
    if live_same:
        lp = paths[live_same[0]["pathIndex"]]
        v = int(lp.targetInfo.modeInfoIdx)
        if v != 0xFFFFFFFF:
            tgt_mode, tgt_desk = (v >> 16) & 0xFFFF, v & 0xFFFF
    print("  现算 target/desktop：tgt=%s desk=%s" % (tgt_mode, tgt_desk))

    def build(src_idx=None, tgt=None, desk=None, src_invalid=False):
        arr = clone_paths(paths, pc)
        p = arr[victim["pathIndex"]]
        p.flags |= mb._CCD_PATH_ACTIVE
        p.sourceInfo.modeInfoIdx = (mb._CCD_MODE_IDX_INVALID if src_invalid
                                   else ((int(src_idx) << 16) | 0xFFFF))
        p.targetInfo.modeInfoIdx = (mb._CCD_MODE_IDX_INVALID
                                   if tgt is None or desk is None
                                   else ((int(tgt) << 16) | int(desk)))
        p.sourceInfo.statusFlags = 0
        p.targetInfo.statusFlags = 0
        return arr

    print("\nA) 复现现场：source 交 INVALID")
    check(u32, build(src_invalid=True, tgt=tgt_mode, desk=tgt_desk),
          modes, pc, mc, "source=INVALID  target=%s" % (tgt_mode,))

    print("\nB) 借用 modes 里已有的 source mode 项（id 对不上）")
    other = next((i for i in range(mc) if int(modes[i].infoType) == 1
                  and int(modes[i].id) != int(victim["srcId"])), None)
    if other is None:
        print("  没有别的 source mode 项可借")
    else:
        check(u32, build(src_idx=other, tgt=tgt_mode, desk=tgt_desk),
              modes, pc, mc, "借用 modes[%d]（id=%d）" % (other, modes[other].id))

    print("\nC) 自己往 modes 末尾补一条 SOURCE mode 项")
    if not pm:
        print("  拿不到首选模式，改用 2560x1440 兜底")
        pm = (2560, 1440)
    w, h = pm
    # 桌面位置：放到现有活动屏的右边，避免与主屏重叠
    max_right, max_bottom = 0, 0
    for r in rows:
        if not (r.get("active") and r.get("connected")):
            continue
        mm = int(paths[r["pathIndex"]].sourceInfo.modeInfoIdx)
        if mm == 0xFFFFFFFF:
            continue
        sm = modes[(mm >> 16) & 0xFFFF].sourceMode
        max_right = max(max_right, int(sm.position.x) + int(sm.width))
        max_bottom = max(max_bottom, int(sm.position.y) + int(sm.height))
    print("  现有桌面右边=%d 下边=%d" % (max_right, max_bottom))
    for lab, pf, (x, y) in (
            ("pf=32BPP 贴右侧", _PIXELFORMAT_32BPP, (max_right, 0)),
            ("pf=NONGDI 贴右侧", _PIXELFORMAT_NONGDI, (max_right, 0)),
            ("pf=NONGDI 原地(0,0)", _PIXELFORMAT_NONGDI, (0, 0)),
            ("pf=NONGDI 贴下方", _PIXELFORMAT_NONGDI, (0, max_bottom))):
        grown, idx = grow_modes(modes, mc, {
            "infoType": 1, "id": int(victim["srcId"]),
            "luid": (p0.sourceInfo.adapterId.LowPart,
                     p0.sourceInfo.adapterId.HighPart),
            "w": w, "h": h, "x": x, "y": y, "pf": pf})
        check(u32, build(src_idx=idx, tgt=tgt_mode, desk=tgt_desk),
              grown, pc, mc + 1, "补 modes[%d] %dx%d %s" % (idx, w, h, lab))
        print("      ↳ 若这条 OK，说明「自己补 source mode」这条路成立")

    print("\nD) 补 source mode，但 target/desktop 交 INVALID")
    grown, idx = grow_modes(modes, mc, {
        "infoType": 1, "id": int(victim["srcId"]),
        "luid": (p0.sourceInfo.adapterId.LowPart, p0.sourceInfo.adapterId.HighPart),
        "w": w, "h": h, "x": max_right, "y": 0, "pf": _PIXELFORMAT_NONGDI})
    check(u32, build(src_idx=idx, tgt=None, desk=None), grown, pc, mc + 1,
          "source=补 modes[%d]  target=INVALID" % idx)

    print("\nE) 交给 Windows 自己扩展（不带 paths，SDC_TOPOLOGY_EXTEND）")
    _SDC_TOPOLOGY_EXTEND = 0x00000004
    rc = u32.SetDisplayConfig(0, None, 0, None,
                              _SDC_TOPOLOGY_EXTEND | _SDC_VALIDATE |
                              mb._SDC_ALLOW_CHANGES | mb._SDC_VIRTUAL_MODE_AWARE)
    print("  %-52s rc=%-4d %s" % ("SDC_TOPOLOGY_EXTEND | SDC_VALIDATE", rc,
                                  _ERR.get(rc, "")))
    rc = u32.SetDisplayConfig(0, None, 0, None,
                              _SDC_TOPOLOGY_EXTEND | _SDC_VALIDATE |
                              mb._SDC_ALLOW_CHANGES)
    print("  %-52s rc=%-4d %s" % ("同上但不带 VIRTUAL_MODE_AWARE", rc,
                                  _ERR.get(rc, "")))

    # ---- F) QDC_DATABASE_CURRENT：能不能现读到那块屏"原来的位置" ----
    # 如果能，接回时就不用猜位置（猜=每次都被挤到最右边），也不必记账。
    print("\nF) QDC_DATABASE_CURRENT（Windows 显示数据库里保存的拓扑）")
    _QDC_DATABASE_CURRENT = 0x2
    for lab, flags in (("DATABASE|VIRTUAL", _QDC_DATABASE_CURRENT | mb._QDC_VIRTUAL_MODE_AWARE),
                       ("DATABASE 裸", _QDC_DATABASE_CURRENT),
                       ("DATABASE|ALL_PATHS",
                        _QDC_DATABASE_CURRENT | mb._QDC_ALL_PATHS)):
        pcn, mcn = wintypes.UINT(), wintypes.UINT()
        rc = u32.GetDisplayConfigBufferSizes(flags, ctypes.byref(pcn),
                                             ctypes.byref(mcn))
        if rc != 0:
            print("  [%s] buffer rc=%d" % (lab, rc))
            continue
        dpaths = (mb._CcdPathInfo * pcn.value)()
        dmodes = (mb._CcdModeInfo * mcn.value)()
        tid = wintypes.UINT()
        rc = u32.QueryDisplayConfig(flags, ctypes.byref(pcn), ctypes.byref(dpaths),
                                    ctypes.byref(mcn), ctypes.byref(dmodes),
                                    ctypes.byref(tid))
        print("  [%s] pc=%d mc=%d rc=%d topologyId=%d"
              % (lab, pcn.value, mcn.value, rc, tid.value))
        if rc != 0:
            continue
        hit = None
        for i in range(pcn.value):
            p = dpaths[i]
            if (int(p.targetInfo.adapterId.LowPart) == int(p0.targetInfo.adapterId.LowPart)
                    and int(p.targetInfo.id) == int(p0.targetInfo.id)):
                hit = i
                break
        if hit is None:
            print("    目标 target 不在这个拓扑里")
            continue
        hp = dpaths[hit]
        smi, tmi = int(hp.sourceInfo.modeInfoIdx), int(hp.targetInfo.modeInfoIdx)
        print("    path#%d active=%s srcIdx=0x%08X tgtIdx=0x%08X"
              % (hit, bool(hp.flags & 1), smi, tmi))
        if smi != 0xFFFFFFFF:
            sm = dmodes[(smi >> 16) & 0xFFFF].sourceMode
            print("    ✅ 有 source mode：%dx%d pf=%d pos=(%d,%d)"
                  % (sm.width, sm.height, sm.pixelFormat,
                     sm.position.x, sm.position.y))
            print("       → 接回可以复用它，位置不用猜")
        else:
            print("    ✗ 也是 INVALID，位置只能自己算")

    print("\n本脚本只用 SDC_VALIDATE，未做任何拓扑改动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
