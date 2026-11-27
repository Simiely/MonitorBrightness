# -*- coding: utf-8 -*-
"""显示器拓扑策略（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

断开/接回/主屏交接/位置摆放。状态机时序壳里的长函数按治理条例登记例外。
"""
import ctypes
from ctypes import wintypes

from core.config import _coerce_positions, normalize_indices, save_config
from core.logging import _write_event_log, dlog
from domain.decisions import _short_dev
from hw.ccd import (
    _ccd_api, _ccd_apply, _ccd_make_snapshot, _ccd_mode_entries,
    _ccd_pick_mode, _ccd_query_raw, _ccd_restore_from_bytes,
    _ccd_wait_state, ccd_probe_topology,
)
from hw.ccd_structs import (
    _CCD_APPLY_FLAGS, _CCD_MODE_IDX_INVALID, _CCD_PATH_ACTIVE,
    _CCD_PIXELFORMAT_NONGDI, _CCD_TYPE_DESKTOP, _CCD_TYPE_SOURCE,
    _CCD_TYPE_TARGET, _CcdModeInfo, _CcdTargetPreferredMode,
    _DC_GET_TARGET_PREFERRED_MODE,
)
from hw.ddc import enumerate_targets, release_targets
from hw.winfollow import win_follow, win_snapshot
from hw.geometry import (
    _ccd_append_source_mode, _ccd_apply_plan_on, _ccd_capture_positions,
    _ccd_fill_missing_source_modes, _ccd_free_origin, _ccd_live_size,
    _ccd_layout_by_anchor, _ccd_layout_rects, _ccd_place,
    _ccd_preferred_size, _fold_raw_rows,
)

def topology_plan_off(indices, rows, primary_gdis=None, dev_to_index=None, heir=None):
    """纯函数：算出把这些屏从拓扑里摘掉要动哪些 path，**必要时交接主屏**。

    Windows 没有"设置主显示器"的 API —— 主屏就是桌面原点 (0,0) 所在的那块屏，
    而且不允许把它从拓扑里摘掉（实测 rc=87）。所以想断开主屏，只能先把 (0,0)
    让给别人：接班屏平移到 (0,0)、其余屏跟着平移（相对布局不变），再摘原主屏。
    这条路径在真机上验过（scripts/topology_handover_probe.py，SDC_VALIDATE 只读）：

      | 做法 | rc |
      |---|---|
      | 只改 source mode 的 position | 0 |
      | 接班屏移到 (0,0) + 摘掉原主屏 | 0 |
      | VDD 当主屏 + 物理屏全摘（串流形态） | 0 |

    护栏：VDD 永不作**断开目标**；断开后桌面必须还剩至少一块屏（否则黑屏）。

    返回 (plan, errs, noop)，plan =
      {"off": [锚点...],                 # 要摘掉的（可能包含当前主屏）
       "heir": 锚点 or None,             # 接班当主屏的那块；None = 主屏不动
       "heir_active": bool}              # False 表示它当前断着，需要先接回
    平移量不在这里算 —— 要读 source mode，由 topology_off 按 hire 的位置算。
    """
    primary = {_short_dev(g) for g in (primary_gdis or [])}
    idx = set(normalize_indices(indices))
    wanted_names = None
    if dev_to_index is not None:
        wanted_names = {_short_dev(name) for name, i in dev_to_index.items()
                        if i in idx} if idx else None
    selected = []
    for r in rows:
        if r.get("vdd") or not r.get("anchor"):
            continue
        if wanted_names is not None:
            # 只认 ACTIVE 的 source：aliases 里两块屏会互相混进 DISPLAYn，
            # 拿它做选择匹配会把「只勾主屏」误判成「勾了副屏」。
            if _short_dev(r.get("gdi")) not in wanted_names:
                continue
        elif idx and r.get("index") not in idx:
            continue
        selected.append(r)

    off = []
    for r in selected:
        if r.get("active") and r["anchor"] not in off:
            off.append(r["anchor"])
    if not off:
        if selected:
            return {"off": [], "heir": None, "heir_active": False}, [], True
        return ({"off": [], "heir": None, "heir_active": False},
                ["没有可断开的屏（VDD 不作目标，未连接的屏也断不了）"], False)

    # 当前主屏（摆在 0,0 的那块）在不在这次要摘的名单里？
    cur_primary = [r["anchor"] for r in rows
                   if r.get("active") and r.get("connected")
                   and _short_dev(r.get("gdi")) in primary]
    must_handover = any(a in off for a in cur_primary)
    if not must_handover:
        return {"off": off, "heir": None, "heir_active": False}, [], False

    # 候选接班屏：连着、且不在这次要摘的名单里
    cands = [r for r in rows
             if r.get("connected") and r.get("anchor")
             and r["anchor"] not in off]
    if heir:
        if not any(r["anchor"] == heir for r in cands):
            return ({"off": [], "heir": None, "heir_active": False},
                    ["指定接班的屏不在了（可能刚被拔掉），请重新选择"], False)
        pick = next(r for r in cands if r["anchor"] == heir)
    else:
        if not cands:
            return ({"off": [], "heir": None, "heir_active": False},
                    ["断开后桌面上就一块屏都不剩（会黑屏），Windows 不允许。\n"
                     "先留一块屏当主显示器，或把虚拟显示器接上再试。"], False)
        # 自动选：优先物理屏（用户看得见），再考虑虚拟屏
        phys = [r for r in cands if not r.get("vdd")]
        pick = (phys or cands)[0]

    heir_active = bool(pick.get("active"))
    # 平移量要读 source mode，纯函数里拿不到 —— 由 topology_off 按 hire 的位置算
    return ({"off": off, "heir": pick["anchor"], "heir_active": heir_active},
            [], False)


def topology_plan_on(anchors, rows, mode_entries, allow_vdd=False):
    """纯函数：算出「把 anchors 里的屏加回**当前**拓扑」要动哪几条 path。

    全部输入都是本次现读的：rows=_ccd_query_raw 的逐 path 行，
    mode_entries=当前 modes 数组摘要。不读快照、不记历史。

    护栏：
      - 只认 connected 的 path（拔了线的接不回来）；
      - VDD 永不作目标；
      - 已 ACTIVE 的锚点直接跳过（幂等：接两次和接一次结果一样）；
      - source id 是 **adapter 内**独占资源，只挑没被同 adapter 的 ACTIVE path 占用的；
      - source mode 缺失**不算错误**：真实断开后 Windows 一定会把它从 modes 数组
        里摘掉，这时标记 `makeSourceMode`，由调用方现造一条补进去
        （`_ccd_append_source_mode`；置 INVALID 必 rc=87）。

    返回 (plan, errs, noop)。plan 每项含 pathIndex/anchor/srcId/tgtId/三个 modeIdx
    以及 `makeSourceMode`（True 表示 sourceModeIdx 为 None，需要现造）。
    target/desktop 可能为 None，表示交给 Windows 定。

    `allow_vdd=True` 只给验证脚本用：VDD 是"连着但整块不在拓扑里"的现成样本，
    拿它跑一次真接回就能验完整条链路，而不会碰到任何物理屏。产品路径永不传。
    """
    wanted = [str(a) for a in (anchors or []) if a]
    rows = list(rows or [])
    used = {(int(r.get("luidLow", 0)), int(r["srcId"])) for r in rows
            if r.get("active") and r.get("connected") and r.get("srcId") is not None}
    plan, errs = [], []
    for anchor in wanted:
        mine = [r for r in rows if r.get("anchor") == anchor and r.get("connected")]
        if not mine:
            errs.append("锚点 %s 在拓扑里没有已连接的 path（可能已拔线）" % anchor)
            continue
        if any(r.get("vdd") for r in mine) and not allow_vdd:
            errs.append("虚拟屏 %s 不作接回目标" % anchor)
            continue
        if any(r.get("active") for r in mine):
            continue                       # 已经亮着：no-op
        key = lambda r: (int(r.get("luidLow", 0)), int(r["srcId"]))
        free = [r for r in mine if key(r) not in used]
        if not free:
            errs.append("锚点 %s 没有空闲 source（全被其它屏占用）" % anchor)
            continue
        # 确定性：source id 最小 → pathIndex 最小。不依赖"上次用的是哪条"。
        pick = min(free, key=lambda r: (int(r["srcId"]), int(r["pathIndex"])))
        smode = _ccd_pick_mode(mode_entries, _CCD_TYPE_SOURCE, pick["srcId"],
                               pick.get("luidLow"))
        plan.append({
            "pathIndex": int(pick["pathIndex"]), "anchor": anchor,
            "srcId": int(pick["srcId"]), "tgtId": int(pick["tgtId"]),
            "luidLow": int(pick.get("luidLow", 0)),
            "luidHigh": int(pick.get("luidHigh", 0)),
            "tgtAdapterLow": int(pick.get("tgtAdapterLow", 0)),
            "tgtAdapterHigh": int(pick.get("tgtAdapterHigh", 0)),
            "sourceModeIdx": smode,
            # 真实断开后 source mode 必然不在 modes 数组里 → 由调用方现造一条
            "makeSourceMode": smode is None,
            "targetModeIdx": _ccd_pick_mode(mode_entries, _CCD_TYPE_TARGET,
                                            pick["tgtId"], pick.get("tgtAdapterLow")),
            "desktopModeIdx": _ccd_pick_mode(mode_entries, _CCD_TYPE_DESKTOP,
                                             pick["tgtId"], pick.get("tgtAdapterLow")),
        })
        used.add((int(pick.get("luidLow", 0)), int(pick["srcId"])))
    return plan, errs, (not plan and not errs)











def _topo_off_handover_shift(paths, modes, mc, raw_rows, heir_anchor, heir_active):
    """算「把接班屏挪到 (0,0)」需要的平移量；不需要交接时返回 (0,0)。

    主屏就是 (0,0) 那块，所以平移接班屏到 (0,0) 就等于把主屏角色交给它；
    其余屏跟着平移，相对布局完全不变（真机验证见 topology_plan_off 注释）。
    """
    shift = (0, 0)
    if heir_anchor and heir_active:
        row = next((r for r in raw_rows
                    if r["anchor"] == heir_anchor and r.get("active")), None)
        if row is not None:
            idx = int(paths[row["pathIndex"]].sourceInfo.modeInfoIdx)
            if idx != _CCD_MODE_IDX_INVALID:
                s = (idx >> 16) & 0xFFFF
                if s < mc:
                    shift = (-int(modes[s].sourceMode.position.x),
                             -int(modes[s].sourceMode.position.y))
        dlog("topo.off.handover", heir=heir_anchor, heir_active=True, shift=list(shift))
    return shift

def _topo_off_capture_positions(cfg, shift, anchors, paths, modes, mc, raw_rows):
    """断开前记下这些屏此刻的位置（平移后的坐标），已有记录不覆盖。

    断开之后 Windows 会把这块屏的 mode 项摘掉，位置就再也读不到了，
    所以必须趁现在记。记平移后的坐标，接回时正好落在新主屏旁边。
    """
    if cfg is None:
        return
    pos = _ccd_capture_positions(anchors, paths, modes, mc, raw_rows)
    if not pos:
        return
    pos = {a: [v[0] + shift[0], v[1] + shift[1], v[2], v[3]]
           for a, v in pos.items()}
    merged = dict(cfg.get("monitor_positions") or {})
    # **已有记录不覆盖**（v2.01.11）。交接主屏会把接班屏挪到桌面原点，
    # 于是"断开那一刻的位置"已经不是用户摆的位置了；照它记下来，
    # 下次接回就会把副屏丢到右边去（实测：两块屏的记录都被写成 (0,0)）。
    # 用户真想更新布局，用设置里的「记住当前显示器布局」。
    for a, v in pos.items():
        merged.setdefault(a, v)
    cfg["monitor_positions"] = _coerce_positions(merged)
    save_config(cfg)
    dlog("topo.off.positions", captured=pos,
         kept=list(cfg["monitor_positions"]))

def _topo_off_activate_heir(u32, heir_anchor, heir_active, raw_rows, paths,
                            modes, mc):
    """接班屏断着就先接回并放到 (0,0)。

    返回 (cur_modes, cur_mc, heir_active, errs)：
      - 不用交接 / 它其实已亮 → 原样返回；
      - 接不回来 → errs 非空，调用方直接失败返回。
    """
    if not heir_anchor or heir_active:
        return modes, mc, heir_active, []
    p_on, e_on, n_on = topology_plan_on(
        [heir_anchor], raw_rows, _ccd_mode_entries(modes, mc), allow_vdd=True)
    if e_on:
        dlog("topo.off.handover", heir=heir_anchor, errs=e_on)
        return modes, mc, heir_active, [(-1, e) for e in e_on]
    if n_on:                       # 它其实已经亮着，按"在拓扑里"处理
        return modes, mc, True, []
    cur_modes, cur_mc = _ccd_fill_missing_source_modes(
        u32, paths, modes, mc, raw_rows, p_on, want={})
    midx = p_on[0]["sourceModeIdx"]
    cur_modes[midx].sourceMode.position.x = 0
    cur_modes[midx].sourceMode.position.y = 0
    _ccd_apply_plan_on(paths, p_on)
    dlog("topo.off.handover", heir=heir_anchor, heir_active=False,
         synthMode=midx, size=[int(cur_modes[midx].sourceMode.width),
                               int(cur_modes[midx].sourceMode.height)])
    return cur_modes, cur_mc, heir_active, []

def _topo_off_shift_other_modes(cur_modes, cur_mc, raw_rows, paths, anchors, shift):
    """交接主屏后，其余屏跟着平移（按 mode 下标去重，避免同一块屏被移两次）。"""
    if shift == (0, 0):
        return
    moved = set()
    for r in raw_rows:
        if not (r.get("active") and r.get("connected")):
            continue
        if r["anchor"] in anchors:
            continue
        idx = int(paths[r["pathIndex"]].sourceInfo.modeInfoIdx)
        if idx == _CCD_MODE_IDX_INVALID:
            continue
        s = (idx >> 16) & 0xFFFF
        if s in moved or s >= cur_mc:
            continue
        moved.add(s)
        cur_modes[s].sourceMode.position.x = \
            int(cur_modes[s].sourceMode.position.x) + shift[0]
        cur_modes[s].sourceMode.position.y = \
            int(cur_modes[s].sourceMode.position.y) + shift[1]

def _topo_off_detach(paths, raw_rows, anchors):
    """把目标屏的 ACTIVE path 置为断开。

    与 topology_switch.py 的 C2/C4 实测口径一致：只清 ACTIVE 的那条 path，
    同一块屏的其它 source 原样保留，快照负责兜底恢复。
    """
    for i in range(len(paths)):
        info = next((x for x in raw_rows if x["pathIndex"] == i), None)
        if (not info or info["anchor"] not in anchors
                or not info["connected"] or not info["active"]):
            continue
        p = paths[i]
        p.flags &= ~_CCD_PATH_ACTIVE
        p.sourceInfo.modeInfoIdx = _CCD_MODE_IDX_INVALID
        p.targetInfo.modeInfoIdx = _CCD_MODE_IDX_INVALID
        p.sourceInfo.statusFlags = 0
        p.targetInfo.statusFlags = 0

def _topo_off_apply_and_verify(u32, paths, cur_modes, pc, cur_mc, anchors,
                               heir_anchor, snap):
    """提交拓扑 → 回读验证；任一项不过就按快照回滚一次。返回 (成功?, errs)。"""
    rc = _ccd_apply(u32, paths, cur_modes, pc, cur_mc)
    if rc != 0:
        dlog("topo.off.fail", stage="apply", rc=rc)
        return False, [(-1, "SetDisplayConfig 失败 rc=%d（拓扑未改动）" % rc)]
    ok_off = _ccd_wait_state(anchors, False)
    ok_heir = (not heir_anchor) or _ccd_wait_state([heir_anchor], True, allow_vdd=True)
    if ok_off and ok_heir:
        return True, []
    back = _ccd_restore_from_bytes(u32, snap)
    why = "回读未确认断开，已回滚"
    if not ok_heir:
        why = "主屏接班没生效，已回滚"
    if back != 0:
        why += "；回滚失败 rc=%d（请用 --screen on 重试）" % back
    dlog("topo.off.fail", stage="verify", rollback=back,
         ok_off=ok_off, ok_heir=ok_heir)
    return False, [(-1, why)]

def _topo_off_report_and_follow(raw_rows, anchors, dev_to_index, indices,
                                heir_anchor, win_snap, shift):
    """算出"真正被断开"的下标、记完成日志，并把窗口搬回跟着它的屏。"""
    done_devs = {_short_dev(r.get("gdi")) for r in raw_rows
                 if r["anchor"] in anchors and r["connected"] and r["active"]}
    done = sorted({i for name, i in dev_to_index.items()
                   if _short_dev(name) in done_devs})
    dlog("topo.off.done", anchors=anchors, indices=done, heir=heir_anchor)
    # 拓扑已经稳定，再把窗口搬回"跟着它的屏"的位置
    if win_snap:
        moved = win_follow(win_snap, shift, set(anchors))
        dlog("topo.off.windows", moved=moved, total=len(win_snap),
             shift=list(shift), dropped=len(anchors))
    return done or list(normalize_indices(indices))

def topology_off(indices, cfg=None, heir=None):
    """拓扑断开：先快照，再提交，再回读；失败自动回滚一次（D2/D4/D6）。

    如果这次要摘的屏里包含**当前主屏**，会先把主屏角色交接给 `heir`
    （不指定就自动选：优先物理屏，其次虚拟屏）：接班屏平移到 (0,0)、
    其余屏跟着平移（相对布局不变），再摘原主屏。交接的技术依据见
    `topology_plan_off` 的注释与 scripts/topology_handover_probe.py。

    这样"禁用所有物理屏、只留虚拟显示器"（串流场景）也能走纯拓扑操作，
    全程不碰面板电源，没有 DDC 待机唤不回的风险。

    `cfg` 给了就把这些屏**此刻**的桌面位置写进 `monitor_positions`，供下次
    接回摆回原位（记录的是平移后的坐标，接回时正好落在新主屏旁边）。

    返回 (成功下标列表, [(下标, 错误), ...])，与 screen_off_many 同形状。
    """
    u32 = _ccd_api()
    if u32 is None:
        return [], [(-1, "拓扑模式仅支持 Windows")]
    raw = _ccd_query_raw(u32)
    if raw is None:
        return [], [(-1, "CCD 查询失败，未做任何改动")]
    paths, modes, pc, mc, raw_rows = raw
    dlog("topo.off", indices=normalize_indices(indices), pc=pc, mc=mc, heir=heir,
         active=[r["anchor"] for r in raw_rows if r.get("active")],
         connected=[r["anchor"] for r in raw_rows if r.get("connected")])
    try:
        targets = enumerate_targets()
    except Exception:
        targets = []
    primary = [t.get("dev") for t in targets if t.get("primary")]
    dev_to_index = {t.get("dev"): t.get("index") for t in targets}
    release_targets(targets)
    plan, errs, noop = topology_plan_off(
        indices, _fold_raw_rows(raw_rows), primary, dev_to_index, heir)
    dlog("topo.off.plan", plan=plan, errs=errs, noop=noop, primary=primary)
    if errs:
        return [], [(-1, e) for e in errs]
    if noop:
        return list(normalize_indices(indices)), []
    anchors = plan["off"]
    heir_anchor = plan.get("heir")
    heir_active = bool(plan.get("heir_active"))

    # D6：动作前拍一份**本进程内**的原始字节快照，只用于"这次操作失败就原样撤回"。
    # v2.01.0 起不再落盘：快照一旦落盘就会被当成"状态"，接回时恢复旧快照
    # 会把用户其后在 Windows 设置里做的改动一起覆盖掉（设计纠偏，见 topology_plan_on）。
    snap = _ccd_make_snapshot(paths, modes, pc, mc, anchors)

    # 接班屏已在拓扑里 → 算出"把它挪到 (0,0)"需要的平移量。
    shift = _topo_off_handover_shift(paths, modes, mc, raw_rows,
                                     heir_anchor, heir_active)

    # 趁断开前把位置记下来（上面已说明为什么必须现在记）
    _topo_off_capture_positions(cfg, shift, anchors, paths, modes, mc, raw_rows)

    cur_modes, cur_mc = modes, mc

    # 动作前记下"每个应用窗口在哪块屏上"：交接会让所有屏平移，Windows 这时
    # 会把窗口挤到一起且不会搬回来。只在 shift≠0（真会平移）时才记。
    win_snap = []
    if shift != (0, 0) and bool((cfg or {}).get("window_follow", True)):
        win_snap = win_snapshot(_ccd_layout_by_anchor(raw_rows, paths, modes))
        dlog("topo.off.windows.snap", count=len(win_snap))

    # 接班屏当前是断着的（虚拟显示器）→ 先把它接回，并直接放到 (0,0)
    cur_modes, cur_mc, heir_active, h_errs = _topo_off_activate_heir(
        u32, heir_anchor, heir_active, raw_rows, paths, cur_modes, cur_mc)
    if h_errs:
        return [], h_errs

    # 其余屏跟着平移（按 mode 下标去重：同一块屏的多条 path 共用一条 mode 项）
    _topo_off_shift_other_modes(cur_modes, cur_mc, raw_rows, paths, anchors, shift)

    _topo_off_detach(paths, raw_rows, anchors)

    ok, v_errs = _topo_off_apply_and_verify(
        u32, paths, cur_modes, pc, cur_mc, anchors, heir_anchor, snap)
    if not ok:
        return [], v_errs
    return _topo_off_report_and_follow(raw_rows, anchors, dev_to_index, indices,
                                       heir_anchor, win_snap, shift), []

def topology_inactive_rows(rows=None):
    """给 GUI 用：拓扑里「设备还连着但 ACTIVE=0」的屏——**全部**列出来。

    对齐 Windows 设置 → 屏幕：那里会把"断开但还接在机器上"的屏画出来，
    虚拟显示器（VDD）也画。所以这里**不再排除 VDD**，只把 `vdd` 标记带出去，
    由界面决定怎么呈现（VDD 不给「启用」按钮：它归串流软件管，
    本程序断开/接回它是没有意义的，硬断还会让串流黑屏）。

    纯函数，便于单测。VDD 仍然**永不作断开目标**——那是 topology_plan_off 的护栏，
    和"能不能看见"是两件事。
    """
    rows = rows if rows is not None else ccd_probe_topology()
    out = []
    for r in rows:
        if r.get("active") or not r.get("anchor"):
            continue
        if not r.get("connected", True):
            continue
        out.append({"anchor": r["anchor"],
                    "friendly": r.get("friendly") or "",
                    "gdi": r.get("gdi") or "",
                    "vdd": bool(r.get("vdd"))})
    return out

def topology_indices_for_anchors(anchors):
    """把恢复出来的锚点映射回**当前**显示器下标（只认 ACTIVE source 的 GDI）。

    不能走 resolve_monitors()：它的 aliases 会把同一块屏的 4 个 DISPLAYn 混在一起，
    只勾副屏时会误中主屏。接回后 enumeratetargets 才重新看到副屏，所以必须现算。
    """
    wanted = set(anchors or [])
    if not wanted:
        return []
    targets = enumerate_targets()
    try:
        by_dev = {_short_dev(t.get("dev")): t.get("index") for t in targets}
    finally:
        release_targets(targets)
    out = []
    for r in ccd_probe_topology():
        if r["anchor"] in wanted and not r["vdd"]:
            i = by_dev.get(_short_dev(r.get("gdi")))
            if i is not None and i not in out:
                out.append(i)
    return sorted(out)

def topology_wanted_anchors(cfg, rows=None):
    """拓扑模式「点亮」要接回哪些屏——**每次现算**，不读任何记账。

    优先用用户勾选时存下的锚点（monitor_anchors）；没存过就表示"全部接回"，
    取当前拓扑里所有 connected + inactive 的物理屏（VDD 除外）。
    """
    saved = [str(a) for a in (cfg.get("monitor_anchors") or []) if a]
    if saved:
        return saved
    rows = rows if rows is not None else _ccd_query_raw_rows()
    return sorted({r["anchor"] for r in rows
                   if r.get("connected") and not r.get("active")
                   and not r.get("vdd") and r.get("anchor")})

def _ccd_query_raw_rows():
    """只读：当前拓扑的逐 path 行（失败返回空列表）。"""
    raw = _ccd_query_raw()
    return list(raw[4]) if raw else []

def topology_on(anchors, cfg=None):
    """拓扑接回：把 anchors 里的屏加回**当前**拓扑（声明式，无历史快照）。

    步骤：现读拓扑 → 现算要动哪几条 path / 哪个 mode → 提交 → 有界回读。
    同一块屏接两次与接一次结果相同（已 ACTIVE 的锚点直接跳过）。

    两档，每档都重新现读、绝不复用被改过的数组；**只有上一档被拒才降级**，
    所以 events.log 里每一句"改用…重试"都是真的发生过（v2.01.1 修：原来那句
    提示写在第一档里无条件输出，一次成功也被写成了"降级后成功"）：
      1) 现算 source/target/desktop 三个 modeInfoIdx —— 忠于这块屏原来的
         分辨率和桌面位置（`--verify` 证明与 Windows 自己填的逐位一致）。
      2) source 仍用现算值，target/desktop 交 INVALID 让 Windows 定
         （真机验证过：只把 target 置 INVALID 能过校验，rc=0）。

    ⚠️ 两条真机定论（都用 SDC_VALIDATE 验的，没有一次改动拓扑；
      scripts/topology_repro87.py 可复跑）：
      - **sourceInfo.modeInfoIdx 一旦置 INVALID 就必然 rc=87**，加多少
        ALLOW_CHANGES / PATH_PERSIST / FORCE_MODE_ENUMERATION 都救不回来。
        所以"两个都交 Windows 挑"这条老路是死的，source mode 必须有有效值。
      - **全量提交（整份 QDC_ALL_PATHS 233 条）本身是合法的，rc=0**。
        曾经怀疑"224 条未连接组合导致 87"并改成 SDC_TOPOLOGY_SUPPLIED 部分
        提交——实测部分提交才是 87，那个方向是错的，已撤销。

    而 source mode "算不出来"是**常态**，不是异常：一块屏被断开之后，Windows
    就把它从 modes 数组里摘掉了，接回时没有任何下标可引用。这时现造一条补进
    数组末尾（`_ccd_append_source_mode`），尺寸取该屏首选分辨率、位置取桌面
    最右边。真机矩阵见 scripts/topology_mode_synthesis.py。

    返回 (成功下标列表, [(下标, 错误), ...], restored_anchors)。
    亮度不在这里写：断开源在接回后才重新出现在 EnumDisplayMonitors 里，
    必须由 screen_on_cfg 用接回后的下标写，否则刚接回的屏拿不到亮度。
    """
    u32 = _ccd_api()
    if u32 is None:
        return [], [(-1, "拓扑模式仅支持 Windows")], []
    tries = ("full", "windows-picks-target")
    last_err = "CCD 查询失败，未做任何改动"
    dlog("topo.on", anchors=list(anchors or []),
         saved_positions=(cfg or {}).get("monitor_positions"))
    for n, mode in enumerate(tries):
        raw = _ccd_query_raw(u32)
        if raw is None:
            return [], [(-1, last_err)], []
        paths, modes, pc, mc, raw_rows = raw
        plan, errs, noop = topology_plan_on(anchors, raw_rows,
                                            _ccd_mode_entries(modes, mc))
        dlog("topo.on.plan", tier=mode, pc=pc, mc=mc, plan=plan,
             errs=errs, noop=noop)
        if errs:
            return [], [(-1, e) for e in errs], []
        if noop:                       # 已经全亮：不提交、不写日志
            return [], [], []
        if mode != "full":
            for item in plan:
                item["targetModeIdx"] = item["desktopModeIdx"] = None
        # 真实断开后 Windows 会把这块屏的 SOURCE mode 从 modes 数组里摘掉，
        # 空手引用必然 rc=87；这里现造一条补进数组末尾。
        # 位置优先摆回"断开前那一刻"的原位（cfg.monitor_positions），
        # 没记过、或会和现有屏重叠，才退回桌面最右边。
        cur_modes, cur_mc = _ccd_fill_missing_source_modes(
            u32, paths, modes, mc, raw_rows, plan,
            want=(cfg or {}).get("monitor_positions"))
        restored = _ccd_apply_plan_on(paths, plan)
        rc = _ccd_apply(u32, paths, cur_modes, pc, cur_mc)
        if rc == 0:
            if not _ccd_wait_state(restored, True):
                dlog("topo.on.fail", tier=mode, stage="verify", restored=restored)
                return [], [(-1, "接回后 2 秒内回读未确认 ACTIVE；亮度未写")], []
            dlog("topo.on.done", tier=mode, restored=restored, mc=cur_mc)
            return topology_indices_for_anchors(restored), [], restored
        last_err = "SetDisplayConfig 失败 rc=%d（拓扑未改动）" % rc
        dlog("topo.on.fail", tier=mode, stage="apply", rc=rc, mc=cur_mc, pc=pc)
        if n + 1 < len(tries):
            # 降级是用户能感知的（结果一样但过程换了路），说人话，别甩参数名
            _write_event_log("接回显示器：第一套参数被 Windows 拒绝（rc=%d），"
                             "已自动换一种方式重试" % rc)
    return [], [(-1, last_err)], []
