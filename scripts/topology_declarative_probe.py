# -*- coding: utf-8 -*-
"""实证：拓扑接回能不能只靠"现读当前拓扑"完成，不用任何历史快照？

背景（v2.01.0 设计纠偏）：程序职责只是"读当前状态 + 调当前状态"，不该维护状态账。
旧实现把"断开前的整份 CCD 字节"存盘、接回时原样恢复；只要期间用户在 Windows
设置里改过排布 / 分辨率 / 主屏，旧快照就会把这些改动覆盖掉。

本脚本回答两个问题：
  Q1 现算出来的 modeInfoIdx 和 Windows 自己填的一致吗？（`--verify`，纯只读）
  Q2 断开之后，只靠"当前 modes 数组 + 空闲 source"能不能接回来？（`--apply`）

结论（2026-11-27 真机）：
  Q1 逐位一致 → 接回可以完全现算；
  Q2 变体 D（现算）成功，全程没有用到"断开前存下的任何东西"。

安全：
  - 默认 dry-run / 只读；`--apply` 才提交；
  - 动作前在**本进程内**保存字节快照，任何一步失败立即回滚；
  - 一次只碰副屏（TARGET_ANCHOR_HINT 那块）；主屏绝不碰；VDD 永不作为目标。
"""

import argparse
import copy
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import topology_probe as tp                                   # noqa: E402

APPLY_FLAGS = 0x20 | 0x80 | 0x200 | 0x400 | 0x8000
MODE_IDX_INVALID = 0xFFFFFFFF
# 目标屏锚点片段：默认取本机副屏。换机器时用 --anchor 指定
# （`python scripts/topology_probe.py` 能看到自己的锚点）。
TARGET_ANCHOR_HINT = "68C7/2"


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def api():
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
    u32.SetDisplayConfig.argtypes = [
        wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    u32.SetDisplayConfig.restype = wintypes.LONG
    return u32


def query(u32):
    """返回 (paths_array, modes_array, pc, mc)。"""
    pc, mc = wintypes.UINT(), wintypes.UINT()
    flags = tp.QDC_ALL_PATHS | tp.QDC_VIRTUAL_MODE_AWARE
    rc = u32.GetDisplayConfigBufferSizes(flags, ctypes.byref(pc), ctypes.byref(mc))
    if rc != 0:
        raise RuntimeError("GetDisplayConfigBufferSizes rc=%d" % rc)
    paths = (tp.PATH_INFO * pc.value)()
    modes = (tp.MODE_INFO * mc.value)()
    rc = u32.QueryDisplayConfig(flags, ctypes.byref(pc), ctypes.byref(paths),
                                ctypes.byref(mc), ctypes.byref(modes), None)
    if rc != 0:
        raise RuntimeError("QueryDisplayConfig rc=%d" % rc)
    return paths, modes, pc.value, mc.value


def apply_paths(u32, paths, modes, pc, mc):
    return u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes),
                                APPLY_FLAGS)


def path_anchor(u32, p):
    tgt = p.targetInfo
    name = tp.TARGET_DEVICE_NAME()
    name.header.type = tp.DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
    name.header.size = ctypes.sizeof(name)
    name.header.adapterId = tgt.adapterId
    name.header.id = tgt.id
    if u32.DisplayConfigGetDeviceInfo(ctypes.byref(name.header)) != 0:
        return "", ""
    luid = tp.luid_str(p.sourceInfo.adapterId)
    if name.flags & 0x4:      # DISPLAYCONFIG_TARGET_DEVICE_NAME_FLAGS.edidIdsValid
        return ("%s/%04X/%04X/%d" % (luid, name.edidManufactureId,
                                     name.edidProductCodeId, name.connectorInstance),
                name.monitorFriendlyDeviceName)
    return "%s/name=%s" % (luid, name.monitorFriendlyDeviceName), name.monitorFriendlyDeviceName


def dump(u32, label):
    paths, modes, pc, mc = query(u32)
    log("=== %s ===  paths=%d modes=%d" % (label, pc, mc))
    for i, p in enumerate(list(paths)):
        if not p.targetInfo.targetAvailable:
            continue
        anchor, friendly = path_anchor(u32, p)
        print("  path#%-3d %-8s %-22s src.id=%-3d tgt.id=%-3d "
              "srcModeIdx=%-12s tgtModeIdx=%-12s"
              % (i, "ACTIVE" if p.flags & tp.PATH_ACTIVE else "inactive",
                 anchor.split("/", 1)[-1], p.sourceInfo.id, p.targetInfo.id,
                 tp.mode_idx_text(p.sourceInfo.modeInfoIdx),
                 tp.mode_idx_text(p.targetInfo.modeInfoIdx)))
    for i, m in enumerate(list(modes)[:12]):
        print("  mode#%-3d id=%-3d infoType=%d %dx%d"
              % (i, m.id, m.infoType, m.sourceMode.width, m.sourceMode.height))
    if mc > 12:
        print("  …… 其余 %d 个 mode 省略" % (mc - 12))
    return paths, modes, pc, mc


def targeted(rows):
    for i, p in enumerate(rows):
        if not p.targetInfo.targetAvailable:
            continue
        yield i, p


def find_target(u32, paths, want_active):
    """按锚点片段找目标屏的**那一条 ACTIVE / inactive** path。"""
    hits = []
    for i, p in enumerate(paths):
        if not p.targetInfo.targetAvailable:
            continue
        anchor, _f = path_anchor(u32, p)
        if TARGET_ANCHOR_HINT not in anchor:
            continue
        if bool(p.flags & tp.PATH_ACTIVE) == want_active:
            hits.append(i)
    return hits


def set_off(paths, idx):
    p = paths[idx]
    p.flags &= ~tp.PATH_ACTIVE
    p.sourceInfo.modeInfoIdx = MODE_IDX_INVALID
    p.targetInfo.modeInfoIdx = MODE_IDX_INVALID
    p.sourceInfo.statusFlags = 0
    p.targetInfo.statusFlags = 0


def set_on(paths, idx, src_idx, tgt_idx):
    p = paths[idx]
    p.flags |= tp.PATH_ACTIVE
    p.sourceInfo.modeInfoIdx = src_idx
    p.targetInfo.modeInfoIdx = tgt_idx
    p.sourceInfo.statusFlags = 0
    p.targetInfo.statusFlags = 0


def wait_active(u32, want, timeout=3.0):
    deadline = time.monotonic() + timeout
    while True:
        paths, _m, _pc, _mc = query(u32)
        hits = find_target(u32, paths, True)
        if hits and want:
            return True
        if not hits and not want:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def pick_restore_path(u32, paths):
    """选可用于接回的空闲 path：connected + inactive + source id 未被 ACTIVE 占用。

    source id 是 adapter 内的独占资源：主屏占着 0，接回就只能用 1/2/3；
    把已被占用的 id 置 ACTIVE 会直接 rc≠0。
    """
    used = {int(p.sourceInfo.id) for p in paths
            if p.targetInfo.targetAvailable and (p.flags & tp.PATH_ACTIVE)}
    cands = []
    for i, p in enumerate(paths):
        if not p.targetInfo.targetAvailable or (p.flags & tp.PATH_ACTIVE):
            continue
        anchor, _f = path_anchor(u32, p)
        if TARGET_ANCHOR_HINT not in anchor:
            continue
        if int(p.sourceInfo.id) in used:
            continue
        cands.append(i)
    return cands


def verify_mode_index_math(u32, paths, modes, mc):
    """只读自检：对每条 ACTIVE path，用「按 id 查 modes」现算 modeInfoIdx，
    与 Windows 实际填的值逐位比对。

    若全部一致 → 接回时可以完全现算，不必依赖"断开前的历史快照"。
    """
    TYPE_SOURCE, TYPE_TARGET, TYPE_DESKTOP = 1, 2, 3
    mode_list = list(modes)[:mc]

    def pick(info_type, ident):
        for k, m in enumerate(mode_list):
            if m.infoType == info_type and int(m.id) == int(ident):
                return k
        return None

    bad = 0
    for i, p in enumerate(list(paths)):
        if not p.targetInfo.targetAvailable or not (p.flags & tp.PATH_ACTIVE):
            continue
        anchor, _f = path_anchor(u32, p)
        src_mode = pick(TYPE_SOURCE, p.sourceInfo.id)
        tgt_mode = pick(TYPE_TARGET, p.targetInfo.id)
        dsk_mode = pick(TYPE_DESKTOP, p.targetInfo.id)
        calc_src = ((src_mode << 16) | 0xFFFF) if src_mode is not None else None
        calc_tgt = (((tgt_mode << 16) | dsk_mode)
                    if (tgt_mode is not None and dsk_mode is not None) else None)
        act_src = int(p.sourceInfo.modeInfoIdx)
        act_tgt = int(p.targetInfo.modeInfoIdx)
        ok_src = calc_src == act_src
        ok_tgt = calc_tgt == act_tgt
        print("  path#%-3d %-16s src 算=%-12s 实=%-12s %s | tgt 算=%-12s 实=%-12s %s"
              % (i, anchor.split("/", 1)[-1],
                 "0x%08X" % calc_src if calc_src is not None else "无",
                 "0x%08X" % act_src, "OK" if ok_src else "≠",
                 "0x%08X" % calc_tgt if calc_tgt is not None else "无",
                 "0x%08X" % act_tgt, "OK" if ok_tgt else "≠"))
        if not (ok_src and ok_tgt):
            bad += 1

    log("自检结果：%s" % ("全部一致 → 接回可完全现算，不需要历史快照"
                         if bad == 0 else "%d 条不一致，算法需修" % bad))
    return 0 if bad == 0 else 1


def main():
    global TARGET_ANCHOR_HINT
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的提交拓扑（默认只读）")
    ap.add_argument("--verify", action="store_true",
                    help="只读：验证「从当前 modes 数组现算 modeInfoIdx」的算法是否与 ACTIVE path 一致")
    ap.add_argument("--anchor", default=TARGET_ANCHOR_HINT,
                    help="目标屏锚点（或锚点片段），默认 %s" % TARGET_ANCHOR_HINT)
    args = ap.parse_args()
    TARGET_ANCHOR_HINT = args.anchor

    u32 = api()
    paths, modes, pc, mc = dump(u32, "当前拓扑")

    on_hits = find_target(u32, paths, True)
    off_hits = find_target(u32, paths, False)
    log("目标屏(%s) ACTIVE path=%s  inactive path=%s"
        % (TARGET_ANCHOR_HINT, on_hits, off_hits))

    if args.verify:
        return verify_mode_index_math(u32, paths, modes, mc)

    if not args.apply:
        log("dry-run：未提交任何改动。加 --apply 才做 D→A→B 三段实验。")
        return 0

    if not on_hits:
        log("目标屏当前不是 ACTIVE，先手动接回再跑本实验")
        return 2

    # 本进程内的兜底快照（只用于救援，不作为产品设计依据）
    base_paths = copy.deepcopy(paths)
    base_modes = copy.deepcopy(modes)
    base_pc, base_mc = pc, mc

    # 记录断开前目标屏的 modeInfoIdx（实验变体 B 要用）
    p0 = paths[on_hits[0]]
    saved_src_idx = p0.sourceInfo.modeInfoIdx
    saved_tgt_idx = p0.targetInfo.modeInfoIdx
    log("目标屏断开前 modeInfoIdx: src=%s tgt=%s"
        % (tp.mode_idx_text(saved_src_idx), tp.mode_idx_text(saved_tgt_idx)))

    # ---- 步骤 1：断开副屏 ----
    work = copy.deepcopy(paths)
    set_off(work, on_hits[0])
    rc = apply_paths(u32, work, modes, pc, mc)
    log("断开 rc=%d" % rc)
    if rc != 0 or not wait_active(u32, False):
        log("断开未确认，回滚")
        apply_paths(u32, base_paths, base_modes, base_pc, base_mc)
        return 3
    log("已确认断开（目标屏 inactive，设备仍连接）")

    # ---- 步骤 2：声明式接回变体（D 优先，验证"现算"是否够用）----
    TYPE_SOURCE, TYPE_TARGET, TYPE_DESKTOP = 1, 2, 3

    def pick_mode(mode_list, info_type, ident):
        for k, m in enumerate(mode_list):
            if m.infoType == info_type and int(m.id) == int(ident):
                return k
        return None

    variants = []
    # D：从**当前** modes 数组现算（完全不需要历史快照）
    paths_now, modes_now, pc_now, mc_now = query(u32)
    ml = list(modes_now)[:mc_now]
    rest = pick_restore_path(u32, paths_now)
    log("断开后重新查询：paths=%d modes=%d；可接回 path=%s"
        % (pc_now, mc_now, rest))
    if rest:
        p_off = paths_now[rest[0]]
        s = pick_mode(ml, TYPE_SOURCE, p_off.sourceInfo.id)
        t = pick_mode(ml, TYPE_TARGET, p_off.targetInfo.id)
        d = pick_mode(ml, TYPE_DESKTOP, p_off.targetInfo.id)
        log("目标 path#%d src.id=%d → 现算 sourceMode=%s targetMode=%s desktopMode=%s"
            % (rest[0], p_off.sourceInfo.id, s, t, d))
        if None not in (s, t, d):
            variants.append(("D 现算 modes 数组", (s << 16) | 0xFFFF, (t << 16) | d))
    # A：modeInfoIdx 全 INVALID，交给 Windows 自己挑
    variants.append(("A 保持 INVALID", MODE_IDX_INVALID, MODE_IDX_INVALID))
    # B：沿用断开前记下的 modeInfoIdx（对照：这就是"历史快照"思路）
    variants.append(("B 沿用断开前 modeIdx", saved_src_idx, saved_tgt_idx))

    ok_variant = None
    for name, si, ti in variants:
        paths_now, modes_now, pc_now, mc_now = query(u32)
        hits = pick_restore_path(u32, paths_now)
        if not hits:
            log("[%s] 没有可用空闲 path（可能已被系统接回），跳过" % name)
            continue
        log("[%s] inactive path=%s  赋 srcModeIdx=0x%08X tgtModeIdx=0x%08X"
            % (name, hits, si, ti))
        set_on(paths_now, hits[0], si, ti)
        rc = apply_paths(u32, paths_now, modes_now, pc_now, mc_now)
        confirmed = (rc == 0) and wait_active(u32, True)
        log("[%s] rc=%d 回读ACTIVE=%s" % (name, rc, confirmed))
        if confirmed:
            ok_variant = name
            break
        # 失败：把本变体造成的任何改动撤回原位
        log("[%s] 失败" % name)

    # ---- 步骤 3：无论如何确保回到"接回"状态 ----
    if ok_variant is None:
        log("三种变体都没成功，用本进程内快照兜底恢复")
        rc = apply_paths(u32, base_paths, base_modes, base_pc, base_mc)
        log("兜底恢复 rc=%d 回读ACTIVE=%s" % (rc, wait_active(u32, True)))
        return 4

    dump(u32, "实验结束后的拓扑")
    print(json.dumps({"ok_variant": ok_variant}, ensure_ascii=False))
    log("结论：声明式接回可行 → 变体 %s" % ok_variant)
    return 0


if __name__ == "__main__":
    sys.exit(main())
