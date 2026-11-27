# -*- coding: utf-8 -*-
"""只读验证：能不能"先把主屏角色交给别人，再断开原来的主屏"。

Windows 没有"设置主显示器"的 API —— 主屏就是桌面原点 (0,0) 所在的那块屏。
所以交接只能靠**改 source mode 的 position**：让接班屏占住 (0,0)，
其余屏跟着平移（相对布局不变），再把原主屏摘掉。

全程只用 SDC_VALIDATE，桌面一个像素都不动。

用法：python scripts/topology_handover_probe.py
"""
import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import monitor_brightness as mb                                # noqa: E402

_SDC_VALIDATE = 0x00000040
_ERR = {0: "OK", 87: "ERROR_INVALID_PARAMETER", 31: "ERROR_GEN_FAILURE"}


def clone(paths, n):
    out = (mb._CcdPathInfo * n)()
    ctypes.memmove(ctypes.byref(out), ctypes.byref(paths), ctypes.sizeof(out))
    return out


def clone_modes(modes, mc):
    out = (mb._CcdModeInfo * mc)()
    ctypes.memmove(ctypes.byref(out), ctypes.byref(modes),
                   ctypes.sizeof(mb._CcdModeInfo) * mc)
    return out


def validate(u32, paths, pc, modes, mc, label):
    rc = u32.SetDisplayConfig(
        pc, ctypes.byref(paths), mc, ctypes.byref(modes),
        (mb._CCD_APPLY_FLAGS | _SDC_VALIDATE)
        & ~(mb._SDC_APPLY | mb._SDC_SAVE_TO_DATABASE))
    print("  %-56s rc=%d %s" % (label, rc, _ERR.get(rc, "")))
    return rc


def active_source_modes(paths, modes, mc, rows):
    """{anchor: (mode 下标, x, y, w, h)}，只取 ACTIVE 且能算出 source mode 的。"""
    out = {}
    for r in rows:
        if not (r.get("active") and r.get("connected")):
            continue
        idx = int(paths[r["pathIndex"]].sourceInfo.modeInfoIdx)
        if idx == mb._CCD_MODE_IDX_INVALID:
            continue
        s = (idx >> 16) & 0xFFFF
        if s >= mc:
            continue
        sm = modes[s].sourceMode
        out[r["anchor"]] = (s, int(sm.position.x), int(sm.position.y),
                            int(sm.width), int(sm.height))
    return out


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
    live = active_source_modes(paths, modes, mc, rows)
    print("当前活动屏（anchor → mode 下标, 位置, 尺寸）：")
    for a, v in live.items():
        print("  %-40s mode[%d] pos=(%d,%d) %dx%d" % (a, v[0], v[1], v[2], v[3], v[4]))
    if len(live) < 2:
        print("活动屏不足两块，没法验证交接")
        return 1

    # 主屏 = 占据 (0,0) 的那块
    primary = next((a for a, v in live.items() if (v[1], v[2]) == (0, 0)), None)
    print("\n当前主屏（摆在 0,0）：%s" % primary)
    heir = next((a for a in live if a != primary), None)
    print("候选接班屏：%s" % heir)

    # ---- 实验 1：交接 —— 接班屏移到 (0,0)，其余平移，原主屏摘掉 ----
    hx, hy = live[heir][1], live[heir][2]
    print("\n① 交接：%s 平移到 (0,0)（offset=%d,%d），同时摘掉 %s"
          % (heir, -hx, -hy, primary))
    p1 = clone(paths, pc)
    m1 = clone_modes(modes, mc)
    for r in rows:
        if r.get("anchor") == primary and r.get("active"):
            p1[r["pathIndex"]].flags &= ~mb._CCD_PATH_ACTIVE
            p1[r["pathIndex"]].sourceInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
            p1[r["pathIndex"]].targetInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
            p1[r["pathIndex"]].sourceInfo.statusFlags = 0
            p1[r["pathIndex"]].targetInfo.statusFlags = 0
    for a, (mi, x, y, w, h) in live.items():
        if a == primary:
            continue
        m1[mi].sourceMode.position.x = x - hx
        m1[mi].sourceMode.position.y = y - hy
        print("     %s: (%d,%d) → (%d,%d)" % (a, x, y, x - hx, y - hy))
    rc1 = validate(u32, p1, pc, m1, mc, "交接并摘掉原主屏")

    # ---- 实验 2：只平移、不摘屏（验证 position 本身能不能改）----
    print("\n② 对照：只把 %s 平移 100px，不摘任何屏" % heir)
    p2 = clone(paths, pc)
    m2 = clone_modes(modes, mc)
    m2[live[heir][0]].sourceMode.position.x = hx - 100
    rc2 = validate(u32, p2, pc, m2, mc, "只改 position")

    # ---- 实验 3：交接给虚拟屏（VDD 当主屏），物理屏全摘 ----
    print("\n③ 交给虚拟显示器：先接回 VDD 放 (0,0)，物理屏全摘")
    vdd = next((r for r in rows if r.get("vdd") and r.get("connected")
                and not r.get("active")), None)
    if vdd is None:
        print("  没有可用的 VDD 样本")
    else:
        p3 = clone(paths, pc)
        plan, errs, noop = mb.topology_plan_on(
            [vdd["anchor"]], rows, mb._ccd_mode_entries(modes, mc), allow_vdd=True)
        if errs or noop:
            print("  VDD 计划失败：errs=%s noop=%s" % (errs, noop))
        else:
            m3, mc3 = mb._ccd_fill_missing_source_modes(
                u32, p3, clone_modes(modes, mc), mc, rows, plan, want={})
            idx = plan[0]["sourceModeIdx"]
            m3[idx].sourceMode.position.x = 0
            m3[idx].sourceMode.position.y = 0
            print("     VDD 合成于 mode[%d]：%dx%d @ (0,0)"
                  % (idx, m3[idx].sourceMode.width, m3[idx].sourceMode.height))
            for r in rows:
                if r.get("active") and not r.get("vdd"):
                    p3[r["pathIndex"]].flags &= ~mb._CCD_PATH_ACTIVE
                    p3[r["pathIndex"]].sourceInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
                    p3[r["pathIndex"]].targetInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
                    p3[r["pathIndex"]].sourceInfo.statusFlags = 0
                    p3[r["pathIndex"]].targetInfo.statusFlags = 0
            mb._ccd_apply_plan_on(p3, plan)
            rc3 = validate(u32, p3, pc, m3, mc3, "VDD 当主屏 + 物理屏全摘")
        print("  说明：这条真跑就是「串流只留虚拟显示器」的形态")

    print("\n结论")
    print("  只改 position 能不能过：%s" % ("能" if rc2 == 0 else "不能"))
    print("  交接并摘掉原主屏能不能过：%s" % ("能" if rc1 == 0 else "不能"))
    print("  全程 SDC_VALIDATE，桌面没动。")
    return 0 if rc1 == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
