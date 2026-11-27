# -*- coding: utf-8 -*-
"""只读验证「物理屏断开 → 接回」的提交形态（不碰桌面）。

副屏上通常开着窗口，真实断开会把窗口挤到主屏、接回后也不会搬回去。
所以这里在内存里**精确复现**断开后的状态，再用 SDC_VALIDATE 校验接回：

  1. 把该屏的 SOURCE mode 项从 modes 数组里**真的删掉**（Windows 断开后就这么干）；
  2. 重排下标，修正其它 path 的 sourceInfo.modeInfoIdx；
  3. 清掉该 path 的 ACTIVE、两个 modeInfoIdx 置 INVALID（复刻 topology_off）；
  4. 用产品代码 topology_plan_on → _ccd_fill_missing_source_modes →
     _ccd_apply_plan_on 生成接回提交；
  5. SDC_VALIDATE 试提交，看 rc。

和真实断开的差别只剩"Windows 是否还会保留别的东西"，比拿 VDD 当样本更贴近现场。

用法：python scripts/topology_simulate_reconnect.py
"""
import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import monitor_brightness as mb                                # noqa: E402

_SDC_VALIDATE = 0x00000040
_ERR = {0: "OK", 87: "ERROR_INVALID_PARAMETER", 31: "ERROR_GEN_FAILURE",
        1168: "ERROR_NOT_FOUND"}


def clone_paths(paths, n):
    out = (mb._CcdPathInfo * n)()
    ctypes.memmove(ctypes.byref(out), ctypes.byref(paths), ctypes.sizeof(out))
    return out


def drop_mode(modes, mc, drop_idx):
    """删掉 modes[drop_idx]，返回 (新数组, {旧下标: 新下标})。"""
    keep = [i for i in range(mc) if i != drop_idx]
    out = (mb._CcdModeInfo * len(keep))()
    for j, i in enumerate(keep):
        ctypes.memmove(ctypes.byref(out[j]), ctypes.byref(modes[i]),
                       ctypes.sizeof(mb._CcdModeInfo))
    return out, {old: new for new, old in enumerate(keep)}


def remap_source_idx(paths, pc, remap):
    """mode 项被删之后，修正所有 path 的 sourceInfo.modeInfoIdx。"""
    for i in range(pc):
        v = int(paths[i].sourceInfo.modeInfoIdx)
        if v == mb._CCD_MODE_IDX_INVALID:
            continue
        s = (v >> 16) & 0xFFFF
        g = v & 0xFFFF
        paths[i].sourceInfo.modeInfoIdx = (
            mb._CCD_MODE_IDX_INVALID if s not in remap else ((remap[s] << 16) | g))


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

    targets = mb.enumerate_targets()
    try:
        primary = {mb._short_dev(t.get("dev")) for t in targets if t.get("primary")}
    finally:
        mb.release_targets(targets)
    cand = [r for r in rows if r.get("active") and r.get("connected")
            and not r.get("vdd") and r.get("anchor")
            and mb._short_dev(r.get("gdi")) not in primary]
    if not cand:
        print("没有副屏可模拟")
        return 1
    victim = cand[0]
    p_idx = victim["pathIndex"]
    print("模拟对象：%s gdi=%s pathIndex=%d"
          % (victim["anchor"], victim["gdi"], p_idx))

    # ---- 1) 删掉这块屏的 SOURCE mode 项（真实断开后 Windows 就这么干）----
    smi = int(paths[p_idx].sourceInfo.modeInfoIdx)
    print("该 path 当前 sourceInfo.modeInfoIdx=0x%08X" % smi)
    if smi == mb._CCD_MODE_IDX_INVALID:
        print("它现在就没有 source mode，直接进入模拟")
        sim_modes, sim_mc, remap = modes, mc, {}
    else:
        drop = (smi >> 16) & 0xFFFF
        sim_modes, remap = drop_mode(modes, mc, drop)
        sim_mc = mc - 1
        print("删掉 modes[%d]（SOURCE id=%d）→ %d → %d 条"
              % (drop, int(modes[drop].id), mc, sim_mc))

    # ---- 2) 复刻 topology_off：清 ACTIVE + mode 置 INVALID ----
    sim_paths = clone_paths(paths, pc)
    remap_source_idx(sim_paths, pc, remap)
    p = sim_paths[p_idx]
    p.flags &= ~mb._CCD_PATH_ACTIVE
    p.sourceInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
    p.targetInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
    p.sourceInfo.statusFlags = 0
    p.targetInfo.statusFlags = 0

    sim_rows = [dict(r) for r in rows]
    for r in sim_rows:
        if r["pathIndex"] == p_idx:
            r["active"] = False
            r["gdi"] = ""

    print("\n① 校验「断开后」状态本身")
    rc = u32.SetDisplayConfig(pc, ctypes.byref(clone_paths(sim_paths, pc)),
                              sim_mc, ctypes.byref(sim_modes),
                              (mb._CCD_APPLY_FLAGS | _SDC_VALIDATE)
                              & ~(mb._SDC_APPLY | mb._SDC_SAVE_TO_DATABASE))
    print("   rc=%d %s（期望 0，说明断开合法）" % (rc, _ERR.get(rc, "")))

    # ---- 3) 用产品代码生成接回提交 ----
    plan, errs, noop = mb.topology_plan_on(
        [victim["anchor"]], sim_rows, mb._ccd_mode_entries(sim_modes, sim_mc))
    print("\n② 接回计划：%s" % plan)
    print("   errs=%s noop=%s" % (errs, noop))
    if errs or noop:
        print("   ⚠️ 计划没生成，无法继续")
        return 1
    print("   makeSourceMode=%s（应为 True：source mode 已被摘掉）"
          % plan[0].get("makeSourceMode"))
    if not plan[0].get("makeSourceMode"):
        print("   ⚠️ 没走到合成路径，本次验证无效")
        return 1

    cur_modes, cur_mc = mb._ccd_fill_missing_source_modes(
        u32, sim_paths, sim_modes, sim_mc, sim_rows, plan)
    print("③ 补齐后 modes：%d → %d 条，sourceModeIdx=%s"
          % (sim_mc, cur_mc, plan[0]["sourceModeIdx"]))
    sm = cur_modes[plan[0]["sourceModeIdx"]].sourceMode
    print("   合成内容：%dx%d pf=%d pos=(%d,%d)  ← 没记位置：落在桌面最右边"
          % (sm.width, sm.height, sm.pixelFormat, sm.position.x, sm.position.y))

    done = mb._ccd_apply_plan_on(sim_paths, plan)
    print("④ 已标记 ACTIVE：%s  sourceInfo.modeInfoIdx=0x%08X"
          % (done, int(sim_paths[p_idx].sourceInfo.modeInfoIdx)))

    print("\n⑤ 校验「接回」提交")
    rc2 = u32.SetDisplayConfig(pc, ctypes.byref(clone_paths(sim_paths, pc)),
                               cur_mc, ctypes.byref(cur_modes),
                               (mb._CCD_APPLY_FLAGS | _SDC_VALIDATE)
                               & ~(mb._SDC_APPLY | mb._SDC_SAVE_TO_DATABASE))
    print("   rc=%d %s" % (rc2, _ERR.get(rc2, "")))

    # ---- ⑥ 带上"断开前的位置"，看会不会摆回原处 ----
    # 真实的 topology_off 会在断开前把位置写进 cfg["monitor_positions"]，
    # 这里用**当前**位置当那份记录（它就是断开前那一刻的值）。
    print("\n⑥ 再算一次：这次带着「断开前的位置」")
    want = mb._ccd_capture_positions([victim["anchor"]], paths, modes, mc, rows)
    print("   记录到的位置：%s" % want)
    plan2, _e2, _n2 = mb.topology_plan_on(
        [victim["anchor"]], sim_rows, mb._ccd_mode_entries(sim_modes, sim_mc))
    sim2 = clone_paths(sim_paths, pc)          # 还没被 ④ 改过
    p2 = sim2[p_idx]
    p2.flags &= ~mb._CCD_PATH_ACTIVE
    p2.sourceInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
    p2.targetInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
    cur2, cur2_mc = mb._ccd_fill_missing_source_modes(
        u32, sim2, sim_modes, sim_mc, sim_rows, plan2, want=want)
    sm2 = cur2[plan2[0]["sourceModeIdx"]].sourceMode
    got = (int(sm2.position.x), int(sm2.position.y))
    exp = (want.get(victim["anchor"]) or [None, None])[:2]
    print("   合成位置：%s   期望（原位置）：%s  %s"
          % (got, tuple(exp), "✅ 一致" if got == tuple(exp) else "❌ 不一致"))
    mb._ccd_apply_plan_on(sim2, plan2)
    rc3 = u32.SetDisplayConfig(pc, ctypes.byref(clone_paths(sim2, pc)),
                               cur2_mc, ctypes.byref(cur2),
                               (mb._CCD_APPLY_FLAGS | _SDC_VALIDATE)
                               & ~(mb._SDC_APPLY | mb._SDC_SAVE_TO_DATABASE))
    print("   校验 rc=%d %s（摆回原位也要 Windows 认）" % (rc3, _ERR.get(rc3, "")))

    print("\n结论")
    pos_ok = got == tuple(exp)
    if rc2 == 0 and rc3 == 0 and pos_ok:
        print("  ✅ 物理屏断开后接回的提交形态，Windows 认；并且位置能摆回原处。"
              "（全程 SDC_VALIDATE，桌面一个像素都没动）")
        return 0
    print("  ❌ 有问题：断开=%d 接回=%d 带位置接回=%d 位置一致=%s"
          % (rc, rc2, rc3, pos_ok))
    return 1


if __name__ == "__main__":
    sys.exit(main())
