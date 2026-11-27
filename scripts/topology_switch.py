#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示器拓扑**受控**断开 / 接回实验脚本（阶段 0 C2/C4 用）。

安全设计：
  1. 默认 dry-run，必须显式 --apply 才会真的提交拓扑；
  2. 动作前把完整 CCD 快照存到仓库外（含原始字节，可原样恢复）；
  3. 一次只操作一块屏；目标按稳定锚点定位，绝不按 DISPLAYn；
  4. 虚拟屏永不作为目标；活动物理屏只剩 1 块时拒绝；
  5. 动作后回读验证，任何失败自动回滚快照；
  6. 提供 --restore 用最近快照救援。

用法：
    python scripts/topology_switch.py --status
    python scripts/topology_switch.py --off <锚点片段>            # dry-run
    python scripts/topology_switch.py --off <锚点片段> --apply     # 真执行
    python scripts/topology_switch.py --on                         # 恢复最近快照
    python scripts/topology_switch.py --restore <快照.json>
"""

import argparse
import ctypes
import datetime
import json
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topology_probe as tp

SDC_USE_SUPPLIED = 0x00000020
SDC_APPLY = 0x00000080
SDC_SAVE_TO_DATABASE = 0x00000200
SDC_ALLOW_CHANGES = 0x00000400
SDC_VIRTUAL_MODE_AWARE = 0x00008000
APPLY_FLAGS = (SDC_USE_SUPPLIED | SDC_APPLY | SDC_SAVE_TO_DATABASE |
               SDC_ALLOW_CHANGES | SDC_VIRTUAL_MODE_AWARE)

WORK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SNAPSHOT_GLOB = "topology-snapshot-*.json"


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def new_snapshot(u32):
    """返回 (paths_array, modes_array, pc, mc, snapshot_dict)。"""
    import glob
    pc, mc = wintypes.UINT(), wintypes.UINT()
    flags = tp.QDC_ALL_PATHS | tp.QDC_VIRTUAL_MODE_AWARE
    rc = u32.GetDisplayConfigBufferSizes(flags, ctypes.byref(pc), ctypes.byref(mc))
    if rc != tp.ERROR_SUCCESS:
        raise RuntimeError("GetDisplayConfigBufferSizes rc=%d" % rc)
    paths = (tp.PATH_INFO * pc.value)()
    modes = (tp.MODE_INFO * mc.value)()
    rc = u32.QueryDisplayConfig(flags, ctypes.byref(pc), ctypes.byref(paths),
                                ctypes.byref(mc), ctypes.byref(modes), None)
    if rc != tp.ERROR_SUCCESS:
        raise RuntimeError("QueryDisplayConfig rc=%d" % rc)
    rc, human = tp.collect_paths(u32)
    snap = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": "CCD 快照，pathsHex/modesHex 为原始 ctypes 字节，可原样恢复",
        "pathCount": pc.value,
        "modeCount": mc.value,
        "pathsHex": bytes(paths).hex(),
        "modesHex": bytes(modes).hex(),
        "human": human,
        "previous": sorted(glob.glob(os.path.join(WORK_DIR, SNAPSHOT_GLOB)))[-1]
                    if glob.glob(os.path.join(WORK_DIR, SNAPSHOT_GLOB)) else None,
    }
    return paths, modes, pc.value, mc.value, snap


def save_snapshot(snap):
    path = os.path.join(WORK_DIR, "topology-snapshot-%s.json" %
                        datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return path


def load_snapshot(path):
    with open(path, encoding="utf-8") as f:
        snap = json.load(f)
    paths = (tp.PATH_INFO * snap["pathCount"])()
    modes = (tp.MODE_INFO * snap["modeCount"])()
    ctypes.memmove(ctypes.byref(paths), bytes.fromhex(snap["pathsHex"]), len(snap["pathsHex"]) // 2)
    ctypes.memmove(ctypes.byref(modes), bytes.fromhex(snap["modesHex"]), len(snap["modesHex"]) // 2)
    return snap, paths, modes


def apply(u32, paths, modes, pc, mc):
    u32.SetDisplayConfig.argtypes = [wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
                                     ctypes.c_void_p, wintypes.UINT]
    u32.SetDisplayConfig.restype = wintypes.LONG
    rc = u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes), APPLY_FLAGS)
    return rc


def restore(u32, snapshot_path):
    snap, paths, modes = load_snapshot(snapshot_path)
    rc = apply(u32, paths, modes, snap["pathCount"], snap["modeCount"])
    if rc != tp.ERROR_SUCCESS:
        raise RuntimeError("恢复失败 rc=%d" % rc)
    log("已从 %s 恢复" % os.path.basename(snapshot_path))


def describe_target(u32, paths, count, needle):
    """按稳定锚点找唯一目标；返回 (index, info, active_physical_count)。"""
    items = []
    for i in range(count):
        p = paths[i]
        info = {
            "active": bool(p.flags & tp.PATH_ACTIVE),
            "targetAvailable": bool(p.targetInfo.targetAvailable),
            "sourceId": p.sourceInfo.id,
            "targetId": p.targetInfo.id,
            "adapterLuid": tp.luid_str(p.sourceInfo.adapterId),
            "gdi": "",
            "friendly": "",
            "edid": (0, 0),
            "connector": 0,
        }
        rc, sb = tp.dev_info(u32, tp.DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME,
                             p.sourceInfo.adapterId, p.sourceInfo.id,
                             tp.SOURCE_DEVICE_NAME)
        if rc == tp.ERROR_SUCCESS:
            info["gdi"] = sb.viewGdiDeviceName
        rc, tb = tp.dev_info(u32, tp.DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME,
                             p.targetInfo.adapterId, p.targetInfo.id,
                             tp.TARGET_DEVICE_NAME)
        if rc == tp.ERROR_SUCCESS:
            info["friendly"] = tb.monitorFriendlyDeviceName
            info["edid"] = (tb.edidManufactureId, tb.edidProductCodeId)
            info["connector"] = tb.connectorInstance
        anchor = "%s/%04X/%04X/%d" % (info["adapterLuid"], info["edid"][0],
                                      info["edid"][1], info["connector"])
        info["anchor"] = anchor
        info["vdd"] = any(k in info["friendly"].lower() for k in ("vdd", "virtual", "iddcx"))
        items.append((i, p, info))

    hits = [(i, p, inf) for i, p, inf in items
            if needle.lower() in inf["anchor"].lower() and inf["targetAvailable"]]
    if not hits:
        raise RuntimeError("锚点片段 %r 未匹配到已连接设备" % needle)
    if len({h[2]["anchor"] for h in hits}) != 1:
        raise RuntimeError("锚点片段 %r 匹配到多块屏，请给更完整的值" % needle)
    active_physical = len({inf["anchor"] for i, p, inf in items
                           if inf["active"] and inf["targetAvailable"] and not inf["vdd"]})
    return hits[0], items, active_physical


def verify(u32, anchor, expect_active):
    _p, _m, _pc, _mc, snap = new_snapshot(u32)
    ok = False
    for p in snap["human"]:
        if p["stableAnchor"].lower() == anchor.lower() and p["targetAvailable"]:
            ok = p["active"] == expect_active
            break
    return ok


def main():
    ap = argparse.ArgumentParser(description="显示器拓扑受控 off/on 实验（默认 dry-run）")
    ap.add_argument("--status", action="store_true", help="只打印当前折叠状态")
    ap.add_argument("--off", metavar="锚点片段", help="断开目标屏")
    ap.add_argument("--on", action="store_true", help="恢复最近快照")
    ap.add_argument("--restore", metavar="快照.json", help="恢复指定快照")
    ap.add_argument("--apply", action="store_true", help="真正提交（不加则只预演）")
    args = ap.parse_args()

    if os.name != "nt":
        print("仅支持 Windows")
        return 2
    u32, _dx = tp._api()

    if args.status:
        _pa, _mo, _pc, _mc, snap = new_snapshot(u32)
        print(tp.render({"environment": {"system": "local", "python": sys.version.split()[0],
                                        "arch": "64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "32",
                                        "structSizes": {}},
                         "queryError": tp.ERROR_SUCCESS,
                         "paths": snap["human"], "monitors": []}))
        return 0

    if args.restore or args.on:
        path = args.restore
        if not path:
            import glob
            candidates = sorted(glob.glob(os.path.join(WORK_DIR, SNAPSHOT_GLOB)))
            if not candidates:
                raise RuntimeError("没有快照可恢复")
            path = candidates[-1]
        if not args.apply:
            log("dry-run：将恢复 %s（加 --apply 才执行）" % path)
            return 0
        restore(u32, path)
        return 0

    if not args.off:
        ap.print_help()
        return 2

    paths, modes, pc, mc, snap = new_snapshot(u32)
    (idx, path, info), items, active_physical = describe_target(u32, paths, pc, args.off)

    log("目标：%s [%s] gdi=%s active=%s" %
        (info["friendly"], info["anchor"], info["gdi"], info["active"]))
    log("当前 ACTIVE 物理屏数=%d" % active_physical)

    if info["vdd"]:
        raise RuntimeError("目标是虚拟屏，拒绝操作")
    # 实测 2026-11-27：断开主屏会返回 SetDisplayConfig rc=87
    # （ERROR_INVALID_PARAMETER，Windows 不允许直接把主屏从拓扑里移除）。
    primary = [m for m in tp.enum_monitors(u32, None, read_ddc=False) if m["primary"]]
    if any(m["gdiName"].lower() == info["gdi"].lower() for m in primary):
        raise RuntimeError("目标是主屏：实测 SetDisplayConfig rc=87，Windows 不允许直接断开主屏")
    if not info["active"]:
        log("目标已经是 inactive，无需重复断开")
        return 0
    if active_physical <= 1:
        raise RuntimeError("只剩 1 块活动物理屏，拒绝断开（会黑屏）")

    snap_path = save_snapshot(snap)
    log("快照已存：%s" % snap_path)

    if not args.apply:
        log("dry-run：将断开上述目标（加 --apply 才执行）；快照不会删除")
        return 0

    # 清 ACTIVE + 两个 modeInfoIdx 置 invalid + statusFlags 清零（与 Hide-Vdd 同口径）
    path.flags &= ~tp.PATH_ACTIVE
    path.sourceInfo.modeInfoIdx = tp.MODE_IDX_INVALID
    path.targetInfo.modeInfoIdx = tp.MODE_IDX_INVALID
    path.sourceInfo.statusFlags = 0
    path.targetInfo.statusFlags = 0

    rc = apply(u32, paths, modes, pc, mc)
    if rc != tp.ERROR_SUCCESS:
        raise RuntimeError("SetDisplayConfig 失败 rc=%d" % rc)
    log("SetDisplayConfig 返回成功，回读验证中…")

    ok = verify(u32, info["anchor"], expect_active=False)
    if not ok:
        restore(u32, snap_path)
        raise RuntimeError("回读未确认断开，已回滚")
    log("✅ 已确认断开；快照保留，用 --on 恢复")
    return 0


if __name__ == "__main__":
    sys.exit(main())
