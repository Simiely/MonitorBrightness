# -*- coding: utf-8 -*-
"""真机跑一遍「拓扑断开 → 声明式接回」，用的是**产品代码**本身。

验证目标（v2.01.0 改造的核心未知项）：
  断开之后，还能不能只靠"现读当前拓扑 + 现算 mode"把屏接回来，
  而完全不依赖"断开前存下的历史快照"。

⚠️ 这个脚本会**真的改动显示拓扑**（副屏会消失 1~2 秒，桌面窗口可能被挤回主屏）。
  必须显式加 --yes 才会执行；不给参数时只打印将要做什么。

安全：
  - 只碰副屏（非 primary 的那块），主屏绝不触碰；VDD 永不作为目标；
  - 动作前在本进程内拍一份字节快照，接回失败立即原样恢复；
  - 全程打印每一步的实际返回值，失败也能看到卡在哪一步。

用法：
    python scripts/topology_live_check.py            # 只看计划，不动拓扑
    python scripts/topology_live_check.py --yes      # 真跑
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import monitor_brightness as mb                                # noqa: E402


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def dump(label):
    rows = mb.ccd_probe_topology()
    log("%s：" % label)
    for r in rows:
        print("    %-8s %-30s gdi=%s"
              % ("ACTIVE" if r.get("active") else "inactive", r.get("anchor"),
                 r.get("gdi") or "-"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true",
                    help="真的执行断开/接回（默认只打印计划）")
    args = ap.parse_args()

    u32 = mb._ccd_api()
    raw = mb._ccd_query_raw(u32)
    if raw is None:
        log("CCD 查询失败，放弃")
        return 1
    paths, modes, pc, mc, raw_rows = raw
    rescue = mb._ccd_make_snapshot(paths, modes, pc, mc, [])
    log("救援快照已拍（内存内，%d paths / %d modes）" % (pc, mc))

    dump("当前拓扑")

    # 副屏 = 非 primary 的那块物理屏
    targets = mb.enumerate_targets()
    try:
        subs = [t for t in targets if not t.get("primary")]
        sub_idx = [t["index"] for t in subs]
        sub_anchor = None
        by_dev = {mb._short_dev(t["dev"]): t["index"] for t in targets}
        for r in mb.ccd_probe_topology():
            if r.get("active") and not r.get("vdd") and \
                    by_dev.get(mb._short_dev(r.get("gdi"))) in set(sub_idx):
                sub_anchor = r["anchor"]
                break
    finally:
        mb.release_targets(targets)

    if not sub_anchor:
        log("找不到副屏锚点（可能只剩一块屏），放弃")
        return 2
    log("目标副屏锚点 = %s，下标 = %s" % (sub_anchor, sub_idx))

    if not args.yes:
        log("未加 --yes：以上为计划，未改动任何拓扑。"
            "确认可以短暂断一下副屏后再加 --yes 执行。")
        return 0

    # ---- 步骤 1：产品代码断开 ----
    t0 = time.monotonic()
    ok, errs = mb.topology_off(sub_idx)
    log("topology_off → ok=%s errs=%s（%.0f ms）" % (ok, errs, (time.monotonic() - t0) * 1000))
    if errs:
        log("断开失败，不继续")
        return 3
    dump("断开后")

    # 断开后现读一次，确认这块屏的 path / mode 是否还查得到
    raw2 = mb._ccd_query_raw(u32)
    entries2 = mb._ccd_mode_entries(raw2[1], raw2[3]) if raw2 else []
    log("断开后 modes=%s" % (entries2,))
    plan, perrs, noop = mb.topology_plan_on([sub_anchor], raw2[4], entries2)
    log("接回计划：errs=%s noop=%s" % (perrs, noop))
    for item in plan:
        log("    path#%d src=%d tgt=%d srcMode=%s tgtMode=%s dskMode=%s"
            % (item["pathIndex"], item["srcId"], item["tgtId"],
               item["sourceModeIdx"], item["targetModeIdx"], item["desktopModeIdx"]))

    # ---- 步骤 2：产品代码声明式接回 ----
    t0 = time.monotonic()
    ok2, errs2, restored = mb.topology_on([sub_anchor])
    log("topology_on → ok=%s errs=%s restored=%s（%.0f ms）"
        % (ok2, errs2, restored, (time.monotonic() - t0) * 1000))
    rows = dump("接回后")

    alive = [r for r in rows if r.get("active") and not r.get("vdd")]
    if len(alive) >= 2:
        log("✅ 声明式接回成功：%d 块物理屏重新 ACTIVE，全程没用历史快照"
            % len(alive))
        return 0

    log("❌ 接回未成功，用救援快照原样恢复")
    rc = mb._ccd_restore_from_bytes(u32, rescue)
    log("救援恢复 rc=%d" % rc)
    time.sleep(1)
    dump("救援后")
    return 4


if __name__ == "__main__":
    sys.exit(main())
