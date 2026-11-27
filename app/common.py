# -*- coding: utf-8 -*-
"""界面控制器共用的小纯函数（R4-3，从 monitor_brightness.py 逐字搬出）。"""
from core.config import normalize_indices
from hw.ddc import enumerate_targets, release_targets


def status_payload(cfg, state, hint):
    """动作刚结束时的即时显示。

    v2.01.0：只有拓扑模式"动作完成 = 拓扑回读确认"才算知道结果；
    ddc / system 读不到可信的亮灭，一律先显示未知，随后的「检测」
    会用真实读数覆盖。宁可短暂不显示，也不给假结论。
    """
    if cfg.get("screen_off_method") != "topology":
        return {"state": "unknown", "hint": hint}
    return {"state": state, "hint": hint}


def row_action_blocker(cfg, busy):
    """逐台按钮能不能用？不能用就返回一句原因。"""
    method = cfg.get("screen_off_method")
    if method not in ("ddc", "topology"):
        return "「系统级」熄灭是整组广播，不能只动一块；改成「DDC」或「拓扑」才有逐台按钮"
    if busy:
        return "正在处理上一个操作，请稍候"
    return ""


def auto_relight_minutes(cfg):
    """熄灭后自动点亮的分钟数；非法值一律当 0（不自动）。"""
    try:
        return max(0, min(120, int(cfg.get("screen_auto_relight_min", 0))))
    except Exception:
        return 0


def selection_payload(indices, targets=None):
    """把界面勾选结果变成 (monitors, monitor_ids) 两份配置值。

    下标给人看、也兼容旧配置；设备名给下次启动兜底。两者一起存才是稳的。
    空选择 = 全部显示器，此时锚点也留空（否则"全部"会被锚死成当时那几台）。
    """
    idx = normalize_indices(indices)
    if not idx:
        return [], []
    owned = targets is None
    if owned:
        targets = enumerate_targets()
    try:
        by_index = {t["index"]: t["dev"] for t in targets}
        return idx, [by_index[i] for i in idx if i in by_index]
    finally:
        if owned:
            release_targets(targets)
