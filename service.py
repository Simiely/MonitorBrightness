# -*- coding: utf-8 -*-
"""策略分发层（v2.03.0 R2）：按配置把动作路由到 hw/ 的具体实现。

放在 hw/ 之上、App 之下：v2.04 起开关屏只走 hw.topology，
DDC 只用于亮度读写；策略集中在 service.py。
"""
from core.config import load_config, normalize_indices
from core.logging import dlog
from domain.decisions import _short_dev
from domain.slots import _minutes, _slot_minutes, active_slot
from hw.ccd import ccd_probe_topology
from hw.ddc import enumerate_targets, release_targets, set_brightness_many
from hw.topology import (
    topology_indices_for_anchors, topology_off, topology_on,
    topology_wanted_anchors,
)

def topology_enable_anchor(cfg, anchor):
    """GUI「启用」按钮：把这一块屏加回**当前**拓扑（不再查历史快照）。

    v2.01.0 起不再要求"断开时留下的快照里得有它"——那等于要求程序维护状态账。
    现在只问当前拓扑：这块屏 connected 且 inactive 吗？是就直接接回。
    校验由 topology_plan_on 现读现判，这里不做重复预检。
    """
    anchor = str(anchor or "")
    if not anchor:
        return [], [(-1, "缺少显示器锚点")]
    return screen_on_cfg(cfg, [], anchors=[anchor])

def screen_off_cfg(cfg, indices):
    """熄灭显示器：v2.04 起只走 CCD 拓扑断开。

    返回 (成功序号列表, [(序号, 错误), ...])，与逐台接口保持同一形状。
    """
    dlog("screen.off.enter", indices=normalize_indices(indices))
    out = topology_off(indices, cfg)
    dlog("screen.off.exit", ok=out[0], errs=out[1])
    return out

def screen_on_row(cfg, index):
    """行内「显示」：只把这一块按拓扑接回，再写亮度。

    v2.04：不再用 DDC 唤醒兜底——程序已不写 0xD6 待机，面板若被外部待机，
    请由显示器物理按键或系统自身的电源管理恢复。
    """
    anchors = selection_anchors([index])
    if not anchors:
        return [], [(-1, "拿不到这块屏的锚点，无法单独点亮")]
    ok, errs, restored = topology_on(anchors, cfg)
    if errs:
        return ok, errs
    ids = topology_indices_for_anchors(restored) or [index]
    bok, berrs = set_brightness_many(ids, current_level(cfg))
    dlog("screen.on.row", index=index, via="topology", ids=ids, restored=restored)
    return ok + bok, errs + berrs

def screen_on_cfg(cfg, indices, anchors=None):
    """点亮显示器：现读现算把拓扑接回，再写亮度。"""
    dlog("screen.on.enter", indices=normalize_indices(indices), anchors=anchors)
    want = [str(a) for a in anchors] if anchors else topology_wanted_anchors(cfg)
    ok, errs, restored = topology_on(want, cfg)
    if errs:
        return ok, errs
    # 断开的屏在接回后才回到 EnumDisplayMonitors；用「接回后」的下标写亮度，
    # 否则副屏接回来了却拿不到亮度（D3）。
    write_to = sorted(set(normalize_indices(indices))
                      | set(topology_indices_for_anchors(restored)))
    if not write_to:
        return ok, errs
    bok, berrs = set_brightness_many(write_to, current_level(cfg))
    return ok + bok, errs + berrs

def current_level(cfg=None, now=None):
    """当前时刻应处的亮度（纯逻辑；定时任务与「测试当前档」共用）。"""
    cfg = cfg or load_config()
    return active_slot(_slot_minutes(cfg), _minutes(now))[1]

def selection_anchors(indices, targets=None):
    """给勾选的下标生成 CCD 稳定锚点（E2）。

    锚点来自 `ccd_probe_topology()`：显卡 LUID + EDID mfr/prod + connectorInstance。
    **只按 `gdi`（当前 ACTIVE 的 source 名）匹配，绝不用 `aliases`**：
    QDC_ALL_PATHS 下每块屏的 aliases 都会列出 DISPLAY1~4 全部四个 source 名，
    用它匹配等于"任何选中都命中所有屏"——v2.01.8 实测：只点一块屏的「显示」，
    实际拿到了两块屏的锚点，于是"点亮这一块"变成了"接回全部"。
    取不到就返回 []，绝不把不确定的锚点写进配置。
    """
    idx = normalize_indices(indices)
    if not idx:
        return []                          # 空选择 = 全部，不锚死
    owned = targets is None
    if owned:
        targets = enumerate_targets()
    try:
        wanted = {_short_dev(t["dev"]) for t in targets if t["index"] in idx}
        rows = ccd_probe_topology()
        out = []
        for r in rows:
            if r["vdd"]:
                continue
            # v2.01.18：只认 ACTIVE path 的 gdi（微软文档 DISPLAYCONFIG_PATH_ACTIVE
            # = "路径处于活动状态且是桌面的一部分"）。断开屏没有 active path，
            # 折叠后残留的 gdi 会和当前主屏撞名（都是 DISPLAY1）；不排除它，
            # selection_anchors 会把主屏和断开屏一起命中 → 交接弹窗 skip 掉
            # 两块 → 候选只剩 VDD（用户 19:46 截图实证）。
            if not r.get("active"):
                continue
            if _short_dev(r.get("gdi")) in wanted and r["anchor"] not in out:
                out.append(r["anchor"])
        return out
    finally:
        if owned:
            release_targets(targets)

def resolve_monitors(cfg):
    """把"用户选过的显示器"解析成**当前**的下标列表。

    优先级（新 → 旧）：
      1. `monitor_anchors`：CCD 稳定锚点，跨 `DISPLAYn` 重排仍认得人；
      2. `monitor_ids`：旧的设备名锚点（DISPLAY1 这类），兼容旧配置；
      3. `monitors`：纯下标，最后兜底。

    任一层"没命中"都继续往下走，绝不静默把用户选择清空。
    返回 [] 表示"全部显示器"（与界面上"一个都不勾 = 全部"一致）。
    """
    anchors = cfg.get("monitor_anchors") or []
    ids = cfg.get("monitor_ids") or []
    if anchors or ids:
        targets = enumerate_targets()
        try:
            by_dev = {_short_dev(t["dev"]): t["index"] for t in targets}
        finally:
            release_targets(targets)      # 只借一瞬，别把句柄漏在这
        hit = set()
        if anchors:
            for r in ccd_probe_topology():
                if r["vdd"] or r["anchor"] not in anchors:
                    continue
                names = [_short_dev(r.get("gdi"))] + [
                    _short_dev(a) for a in r.get("aliases", [])]
                hit.update(by_dev[n] for n in names if n in by_dev)
        if not hit and ids:
            hit.update(by_dev[d] for d in (_short_dev(i) for i in ids) if d in by_dev)
        if hit:
            return sorted(hit)
    return normalize_indices(cfg.get("monitors"))
