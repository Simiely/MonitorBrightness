# -*- coding: utf-8 -*-
"""时间档位的纯选择逻辑（v2.03.0 R3，从 service.py 逐字搬出）。

只依赖 domain.timeutil；不碰配置读写、不碰硬件、不碰 Tk。
`current_level / wake_options / selection_anchors` 仍需读配置或硬件，
留在 service.py（见交接文档 R2 的偏差说明）。
"""
from datetime import datetime

from domain.timeutil import clamp_level, parse_hhmm


def _minutes(now=None):
    now = now or datetime.now()
    return now.hour * 60 + now.minute


def _slot_minutes(cfg):
    """cfg["slots"] -> [(当日分钟, 亮度)]，按时间排序。"""
    out = []
    for x in cfg.get("slots", []):
        out.append((parse_hhmm(x.get("time"), (0, 0)), clamp_level(x.get("level", 50))))
    return sorted(out)


def active_slot(slots, now_min):
    """N 档调度核心：取 time <= now 的最后一档；一个都没有 -> 最后一档（跨午夜回绕）。

    slots 为 [(当日分钟, 亮度)]，返回 (当日分钟, 亮度)。
    """
    best = None
    for m, lv in sorted(slots):
        if m <= now_min:
            best = (m, lv)
    if best is None:
        best = sorted(slots)[-1]
    return best


def active_slot_dict(slots, now_min):
    """同 active_slot 的选择规则，但作用于原始档位 dict（含 screen 动作）。

    取 time <= now_min 的最后一档；一个都没有 -> 时间最大的最后一档（跨午夜回绕）。
    """
    best = None
    best_m = None
    for s in slots:
        m = parse_hhmm(s.get("time"), (0, 0))
        if m <= now_min and (best is None or m >= best_m):
            best, best_m = s, m
    if best is None:
        best = max(slots, key=lambda x: parse_hhmm(x.get("time"), (0, 0)))
    return best
