# -*- coding: utf-8 -*-
"""真机端到端验证「断开后接回」——用产品代码本身，但拿 VDD 当样本。

为什么是 VDD：一块屏被断开之后，Windows 会把它从 modes 数组里摘掉，接回时
必须自己补一条 source mode（`_ccd_append_source_mode`）。要验这条链路，
样本必须满足"连着、但整块不在当前拓扑里"。本机上只有 VDD 满足，而它正好
完全不会碰到物理屏 —— 断开它 / 接回它，物理屏一个像素都不动。

安全：
  - 只动 VDD 那一条 path，物理屏的 path 原样不动；
  - 动手前在本进程内拍整份字节快照，**无论成败** finally 里都原样写回；
  - 写回用的是已经验证过 rc=0 的"原样重交"，失败概率极低；
  - 不给 --yes 时只打印计划，一个 SetDisplayConfig 都不发。

用法：
    python scripts/topology_live_synthesis_check.py          # 只看计划
    python scripts/topology_live_synthesis_check.py --yes    # 真跑（含自动还原）
"""
import argparse
import ctypes
import os
import sys
import time

_SDC_VALIDATE = 0x00000040

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import monitor_brightness as mb                                # noqa: E402


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def vdd_anchor(rows):
    """挑一块"连着、但整块不在拓扑里"的 VDD 屏。"""
    active = {r["anchor"] for r in rows if r.get("active") and r.get("connected")}
    cand = [r for r in rows
            if r.get("vdd") and r.get("connected") and r.get("anchor")
            and r["anchor"] not in active]
    return cand[0]["anchor"] if cand else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真正提交（含自动还原）")
    args = ap.parse_args()

    u32 = mb._ccd_api()
    if u32 is None:
        log("非 Windows")
        return 2

    raw = mb._ccd_query_raw(u32)
    if raw is None:
        log("CCD 查询失败")
        return 2
    paths, modes, pc, mc, rows = raw
    snap = mb._ccd_make_snapshot(paths, modes, pc, mc, [])
    log("快照已拍在内存里：pc=%d mc=%d" % (pc, mc))

    anchor = vdd_anchor(rows)
    if not anchor:
        log("没有可用样本（没有连着但不在拓扑里的 VDD 屏）")
        return 1
    log("样本：%s" % anchor)

    plan, errs, noop = mb.topology_plan_on(
        [anchor], rows, mb._ccd_mode_entries(modes, mc), allow_vdd=True)
    if errs:
        log("计划被拒：%s" % errs)
        return 1
    log("计划：%s  noop=%s" % (plan, noop))
    if noop:
        log("样本已经 ACTIVE，没法验证接回")
        return 1

    # 这一步是本次修复的核心：source mode 缺失时现造一条补进 modes 数组
    cur_modes, cur_mc = mb._ccd_fill_missing_source_modes(
        u32, paths, modes, mc, rows, plan)
    log("补齐后 modes：%d → %d 条；plan.sourceModeIdx=%s"
        % (mc, cur_mc, [it["sourceModeIdx"] for it in plan]))

    done = mb._ccd_apply_plan_on(paths, plan)
    log("已就地把 %s 标为 ACTIVE，sourceModeIdx=0x%08X"
        % (done, int(paths[plan[0]["pathIndex"]].sourceInfo.modeInfoIdx)))

    if not args.yes:
        log("dry-run：不加 --yes 不提交。")
        rc = u32.SetDisplayConfig(
            pc, ctypes.byref(paths), cur_mc, ctypes.byref(cur_modes),
            (mb._CCD_APPLY_FLAGS | _SDC_VALIDATE)
            & ~(mb._SDC_APPLY | mb._SDC_SAVE_TO_DATABASE))
        log("SDC_VALIDATE 结果：rc=%d（0=能设）" % rc)
        return 0

    ok = False
    try:
        t0 = time.monotonic()
        rc = mb._ccd_apply(u32, paths, cur_modes, pc, cur_mc)
        log("SetDisplayConfig rc=%d（耗时 %dms）" % (rc, (time.monotonic() - t0) * 1000))
        if rc != 0:
            log("❌ 提交被拒")
            return 1
        ok = False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            # 不能直接用 _ccd_wait_state：它按产品口径过滤掉 VDD，
            # 而这次验证的样本恰好就是 VDD。
            if [r for r in mb.ccd_probe_topology()
                    if r.get("anchor") == anchor and r.get("active")]:
                ok = True
                break
            time.sleep(0.1)
        log("%s 回读：%s" % ("✅" if ok else "❌", "已 ACTIVE" if ok else "仍不是 ACTIVE"))
        for r in mb.ccd_probe_topology():
            log("   %-8s %s" % ("ACTIVE" if r["active"] else "inactive", r["anchor"]))
    finally:
        back = mb._ccd_restore_from_bytes(u32, snap)
        log("还原原始拓扑：rc=%d" % back)
        time.sleep(0.3)
        for r in mb.ccd_probe_topology():
            log("   %-8s %s" % ("ACTIVE" if r["active"] else "inactive", r["anchor"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
