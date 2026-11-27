# -*- coding: utf-8 -*-
"""接回 rc=87 的证据脚本：只读，全程用 SDC_VALIDATE，不改一个像素。

`SDC_VALIDATE` 让 Windows 只校验"这份配置能不能设"，校验通过返回 0，
不通过返回错误码，**不会真的改拓扑**。所以可以放心地在这里"模拟"断开与接回。

本脚本钉住的三个结论（v2.01.1 修 rc=87 的全部依据）：
  C1 整份 QDC_ALL_PATHS（233 条，含 224 条未连接组合）全量提交 → rc=0。
     → "未连接组合导致 87" 是错的，部分提交（SDC_TOPOLOGY_SUPPLIED）实测才 87。
  C2 sourceInfo.modeInfoIdx=INVALID → 必 rc=87，加 ALLOW_CHANGES /
     PATH_PERSIST_IF_REQUIRED / FORCE_MODE_ENUMERATION / ALLOW_PATH_ORDER_CHANGES
     都救不回来。
  C3 targetInfo.modeInfoIdx=INVALID → rc=0，可以交给 Windows 定。

用法：python scripts/topology_repro87.py
返回：0 = 结论与代码一致；1 = 与本机实测不符（别按这个结论改代码）
"""
import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import monitor_brightness as mb                                # noqa: E402

_SDC_VALIDATE = 0x00000040
_SDC_PATH_PERSIST = 0x00000800
_SDC_FORCE_MODE_ENUM = 0x00001000
_SDC_ALLOW_ORDER = 0x00002000
_ERR = {0: "OK", 87: "ERROR_INVALID_PARAMETER", 31: "ERROR_GEN_FAILURE",
        5: "ERROR_ACCESS_DENIED", 1168: "ERROR_NOT_FOUND"}


def check(u32, paths, modes, pc, mc, label, flags=None):
    """用 VALIDATE 试提交：APPLY/SAVE 换成 VALIDATE，其余旗标不动。"""
    flags = mb._CCD_APPLY_FLAGS if flags is None else flags
    flags = (flags | _SDC_VALIDATE) & ~(mb._SDC_APPLY | mb._SDC_SAVE_TO_DATABASE)
    rc = u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes),
                              flags)
    print("  %-40s pc=%-4d rc=%-4d %s"
          % (label, pc, rc, _ERR.get(rc, "")))
    return rc


def clone(paths, n):
    out = (mb._CcdPathInfo * n)()
    ctypes.memmove(ctypes.byref(out), ctypes.byref(paths), ctypes.sizeof(out))
    return out


def dump_table(paths, rows):
    print("原始 path 表（只列有锚点的）")
    print("  %-4s %-6s %-4s %-5s %-5s %-12s %-12s %s"
          % ("idx", "ACTIVE", "avail", "src", "tgt", "srcModeIdx", "tgtModeIdx",
             "anchor / gdi"))
    for r in rows:
        if not r.get("anchor"):
            continue
        p = paths[r["pathIndex"]]
        print("  %-4d %-6s %-4s %-5d %-5d 0x%08X   0x%08X   %s %s%s"
              % (r["pathIndex"], "A" if r["active"] else "-",
                 "y" if r["connected"] else "n", r["srcId"], r["tgtId"],
                 int(p.sourceInfo.modeInfoIdx), int(p.targetInfo.modeInfoIdx),
                 r["anchor"], r["gdi"] or "-", "  [VDD]" if r["vdd"] else ""))


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
    print("当前拓扑 pc=%d mc=%d（QDC_ALL_PATHS）" % (pc, mc))
    dump_table(paths, rows)

    # 挑副屏：ACTIVE + connected + 非 VDD + 非主屏（主屏断开 Windows 直接拒）
    targets = mb.enumerate_targets()
    try:
        primary = {mb._short_dev(t.get("dev")) for t in targets if t.get("primary")}
    finally:
        mb.release_targets(targets)
    actives = [r for r in rows if r.get("active") and r.get("connected")
               and not r.get("vdd") and r.get("anchor")
               and mb._short_dev(r.get("gdi")) not in primary]
    if not actives:
        print("\n没有可模拟的副屏（当前只有主屏 ACTIVE）")
        return 1
    victim = actives[0]
    print("\n模拟对象（副屏）：%s gdi=%s pathIndex=%d"
          % (victim["anchor"], victim["gdi"], victim["pathIndex"]))

    print("\nC1) 全量提交本身合不合法")
    rc_c1 = check(u32, clone(paths, pc), modes, pc, mc,
                  "C1 全量 / 现状原样重交")

    print("\n模拟【断开】（复刻 topology_off 的改法）")
    off = clone(paths, pc)
    op = off[victim["pathIndex"]]
    op.flags &= ~mb._CCD_PATH_ACTIVE
    op.sourceInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
    op.targetInfo.modeInfoIdx = mb._CCD_MODE_IDX_INVALID
    op.sourceInfo.statusFlags = 0
    op.targetInfo.statusFlags = 0
    rc_off = check(u32, clone(off, pc), modes, pc, mc, "断开副屏")
    if rc_off != 0:
        print("  ⚠️ 连断开都过不了校验，后面的模拟没意义")
        return 1

    # 从"已断开"的行现算接回计划（与产品同一条路径）
    rows_off = [dict(r) for r in rows]
    for r in rows_off:
        if r["pathIndex"] == victim["pathIndex"]:
            r["active"] = False
            r["gdi"] = ""
    plan, errs, noop = mb.topology_plan_on(
        [victim["anchor"]], rows_off, mb._ccd_mode_entries(modes, mc))
    print("  接回计划：%s  错误：%s  noop=%s" % (plan, errs, noop))
    if not plan:
        print("  ⚠️ 现算不出计划，无法继续")
        return 1
    it = plan[0]
    print("    → pathIndex=%d src=%s tgt=%s smode=%s tmode=%s dmode=%s"
          % (it["pathIndex"], it["srcId"], it["tgtId"], it["sourceModeIdx"],
             it["targetModeIdx"], it["desktopModeIdx"]))

    def build(s, td):
        arr = clone(off, pc)
        p = arr[victim["pathIndex"]]
        p.flags |= mb._CCD_PATH_ACTIVE
        p.sourceInfo.modeInfoIdx = (mb._CCD_MODE_IDX_INVALID if s is None
                                   else ((int(s) << 16) | 0xFFFF))
        p.targetInfo.modeInfoIdx = (
            mb._CCD_MODE_IDX_INVALID if td is None
            else ((int(td[0]) << 16) | int(td[1])))
        p.sourceInfo.statusFlags = 0
        p.targetInfo.statusFlags = 0
        return arr

    good_src = it["sourceModeIdx"]
    good_tgt = (it["targetModeIdx"], it["desktopModeIdx"])
    print("\nC2/C3) 接回时 modeInfoIdx 的四种组合")
    rc_ok = check(u32, build(good_src, good_tgt), modes, pc, mc,
                  "source=现算 target=现算")
    rc_c2 = check(u32, build(None, good_tgt), modes, pc, mc,
                  "source=INVALID target=现算  ← 元凶")
    rc_c3 = check(u32, build(good_src, None), modes, pc, mc,
                  "source=现算 target=INVALID")
    check(u32, build(None, None), modes, pc, mc, "source=INVALID target=INVALID")

    print("\nC2 补：给 INVALID 那一档加旗标，能不能救活")
    bad = build(None, None)
    for lab, extra in (("+PATH_PERSIST", _SDC_PATH_PERSIST),
                       ("+FORCE_MODE_ENUM", _SDC_FORCE_MODE_ENUM),
                       ("+ALLOW_PATH_ORDER", _SDC_ALLOW_ORDER),
                       ("+三者全加", _SDC_PATH_PERSIST | _SDC_FORCE_MODE_ENUM
                        | _SDC_ALLOW_ORDER)):
        check(u32, clone(bad, pc), modes, pc, mc, lab, mb._CCD_APPLY_FLAGS | extra)

    print("\n参考：只交 ACTIVE + 当前目标（不采用，仅记录 Windows 也认）")
    keep = sorted({r["pathIndex"] for r in rows
                   if r.get("active") and r.get("connected")}
                  | {victim["pathIndex"]})
    small = (mb._CcdPathInfo * len(keep))()
    for j, i in enumerate(keep):
        ctypes.memmove(ctypes.byref(small[j]), ctypes.byref(off[i]),
                       ctypes.sizeof(mb._CcdPathInfo))
    p = small[keep.index(victim["pathIndex"])]
    p.flags |= mb._CCD_PATH_ACTIVE
    p.sourceInfo.modeInfoIdx = ((int(good_src) << 16) | 0xFFFF)
    p.targetInfo.modeInfoIdx = ((int(good_tgt[0]) << 16) | int(good_tgt[1]))
    p.sourceInfo.statusFlags = 0
    p.targetInfo.statusFlags = 0
    check(u32, small, modes, len(small), mc, "只交 %d 条 / 现算 mode" % len(keep))

    print("\n结论")
    ok = (rc_c1 == 0 and rc_ok == 0 and rc_c2 == 87 and rc_c3 == 0)
    print("  C1 全量提交合法            rc=%d   期望 0" % rc_c1)
    print("  C2 source=INVALID 必失败   rc=%d   期望 87" % rc_c2)
    print("  C3 target=INVALID 可以     rc=%d   期望 0" % rc_c3)
    print("  %s" % ("✅ 三条都成立：产品按「source 必须有效，target 可交 Windows」实现是对的。"
                    if ok else "⚠️ 与本机实测不符，别按这个结论改代码。"))
    print("\n本脚本只用 SDC_VALIDATE，未做任何拓扑改动。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
