# -*- coding: utf-8 -*-
"""时间与星期的纯工具函数（v2.03.0 U2 前置，从 monitor_brightness.py 逐字搬出）。

为什么单独成模块：`TimeField` / `WeekdayPicker` / `_SlotRow` 这些**控件**
要用它们。控件搬到 `ui/widgets.py` 后若还 `from monitor_brightness import ...`，
就形成 ui → 主文件 → ui 的循环。放到 domain 这一层，依赖方向才单向。
"""
import re


WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")


WEEKDAY_PRESETS = (("每天", []), ("工作日", [1, 2, 3, 4, 5]),
                   ("周末", [6, 7]))


# 星期：ISO 编号 1=周一 … 7=周日。
# `days` 空列表 = 每天（兼容没有这个字段的旧配置）。
WEEKDAY_NAMES_PS = ("Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday")


def normalize_days(value):
    """把各种写法规整成 1-7 的有序去重列表；选满 7 天归一成 []（=每天）。"""
    out = set()
    if isinstance(value, (list, tuple, set)):
        for d in value:
            try:
                n = int(d)
            except Exception:
                continue
            if 1 <= n <= 7:
                out.add(n)
    return [] if len(out) >= 7 else sorted(out)


def days_label(days):
    """给界面看的一句话：每天 / 工作日 / 周末 / 周一,三,五。"""
    days = normalize_days(days)
    if not days:
        return "每天"
    if days == [1, 2, 3, 4, 5]:
        return "工作日"
    if days == [6, 7]:
        return "周末"
    return "周" + ",".join(WEEKDAY_LABELS[d - 1] for d in days)


def slot_matches_weekday(slot, weekday):
    """这一档在指定星期（1=周一）该不该执行；days 空 = 每天。"""
    days = normalize_days(slot.get("days"))
    return not days or weekday in days


def slots_for_weekday(slots, weekday):
    """只留下今天生效的档位；万一全都不生效，退回全部（避免无档可判）。"""
    keep = [s for s in slots if slot_matches_weekday(s, weekday)]
    return keep or list(slots)


def normalize_time_input(text):
    """把用户键入的各种写法规整成 'HH:MM'；非法返回 None。

    接受 `9:5` / `09：05`（全角冒号）/ `0905` 这类写法；
    这是新时间输入框的"可键入"能力，单测直接覆盖。
    """
    s = str(text or "").strip().replace("：", ":").replace(" ", "")
    m = re.match(r"^(\d{1,2}):(\d{1,2})$", s)
    if not m:
        m = re.match(r"^(\d{1,2})(\d{2})$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return "%02d:%02d" % (h, mi)


def shift_time(text, delta_min):
    """'HH:MM' ± N 分钟（跨午夜回绕），返回 'HH:MM'。"""
    base = normalize_time_input(text) or "12:00"
    h, mi = int(base[:2]), int(base[3:])
    total = (h * 60 + mi + delta_min) % (24 * 60)
    return "%02d:%02d" % (total // 60, total % 60)


def clamp_level(value):
    try:
        return max(0, min(100, int(float(value))))
    except Exception:
        return 0


# ============================================================
# 三、按时间判定白天 / 晚上
#    所有计划任务都执行 --apply-schedule，由程序按「执行时刻」自行判定该用哪一档。
#    这样即便白天、晚上两个任务被同时补跑，结果也一致（幂等）。
# ============================================================
def parse_hhmm(text, default=(9, 0)):
    """'HH:MM' → 当日分钟数；解析失败返回 default。"""
    try:
        parts = str(text).strip().split(":")
        if len(parts) != 2:
            raise ValueError
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return default[0] * 60 + default[1]

