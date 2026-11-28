# -*- coding: utf-8 -*-
"""任务状态与确认文案（v2.03.0 R3，从 schedule.py 逐字拆出）。

只做文本解析/格式化；任务注册与脚本生成在 schedule.py，依赖方向单向。
"""
import re
from datetime import datetime, timezone

from core.util import _short_dt
from domain.timeutil import (
    WEEKDAY_LABELS, WEEKDAY_NAMES_PS, normalize_days, parse_hhmm,
)

TASK_SCHEDULE = "MonitorBrightness_Schedule"

TASK_CATCHUP = "MonitorBrightness_CatchUp"

# v1.2.x 及更早的分档任务名：注册前先清掉，避免残留
_LEGACY_TASKS = ("MonitorBrightness_Day", "MonitorBrightness_Night")

# 任务名 → 界面短名（顺序即显示顺序）
TASK_SHORT = {TASK_SCHEDULE: "定时", TASK_CATCHUP: "登录校准"}

def _boundary_to_minutes(b):
    """'2026-11-27T08:30:00' → 510（当日分钟）；解析失败返回 None。"""
    b = (b or "").strip()
    parts = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", b)
    if not parts:
        return None
    y, mo, d, h, mi, sec = (int(v) for v in parts.groups())
    try:
        dt = (datetime(y, mo, d, h, mi, sec, tzinfo=timezone.utc).astimezone()
              if b.endswith("Z") else datetime(y, mo, d, h, mi, sec))
    except Exception:
        return None
    return dt.hour * 60 + dt.minute

def _boundary_minutes_all(xml_text):
    """任务 XML 里全部触发器的 StartBoundary（本地当日分钟列表）。"""
    out = []
    for m in re.finditer(r"<StartBoundary>([^<]+)</StartBoundary>", xml_text or ""):
        mins = _boundary_to_minutes(m.group(1))
        if mins is not None:
            out.append(mins)
    return out

def _next_run_text(minutes_list, now=None, days_by_min=None):
    """多触发器取「离现在最近的下一次」。

    v2.02.0：`days_by_min` 可选，{当日分钟: 星期集合}。给了就按真实星期推算
    （周一档在周五要显示"周一 08:30"，不能再说"明天"）；空集合 = 每天，
    行为与旧版完全一致。
    """
    if not minutes_list:
        return ""
    now_dt = now if now is not None else datetime.now()
    today = now_dt.isoweekday()
    now_min = now_dt.hour * 60 + now_dt.minute
    best = None                      # (距今天数, 当日分钟)
    for m in minutes_list:
        days = frozenset((days_by_min or {}).get(m) or ())
        for delta in range(0, 8):    # 今天起 7 天内必有至少一次
            wd = (today - 1 + delta) % 7 + 1
            if days and wd not in days:
                continue
            if delta == 0 and m <= now_min:
                continue
            cand = (delta, m)
            if best is None or cand < best:
                best = cand
            break
    if best is None:
        return ""
    delta, m = best
    if delta == 0:
        day = "今天"
    elif delta == 1:
        day = "明天"
    else:
        day = "周" + WEEKDAY_LABELS[(today - 1 + delta) % 7]
    return "%s %02d:%02d" % (day, m // 60, m % 60)

def _days_by_min_from_fp(fingerprint):
    """[(分钟, 星期frozenset)] → {分钟: 星期集合}；"每天"给空集合。"""
    out = {}
    for mins, days in fingerprint or []:
        prev = out.get(mins)
        if prev is None:
            out[mins] = set(days)
        else:
            prev |= set(days)
            if not prev:
                out[mins] = set()
    return {k: frozenset(v) for k, v in out.items()}

def _as_ok_errs(result):
    """把 run_bg 的结果统一成 (成功列表, 失败列表)。

    job 抛异常时 run_bg 把异常对象原样传回，这里转成一条 (-1, 原因) 失败项，
    免得每个 on_done 都要写一遍 isinstance 判断。
    """
    if isinstance(result, Exception):
        return [], [(-1, str(result))]
    return result

def _task_result_text(code):
    """任务结果码 → 人话。0x41303 = 任务从未运行过（Windows 的哨兵值）。"""
    try:
        n = int(str(code).strip())
    except Exception:
        return str(code)
    if n == 0:
        return "成功"
    if n == 0x41303:
        return "从未运行"
    if n == 0x41301:
        return "正在运行"
    if n == 0x41302:
        return "被用户终止"
    return "0x%X" % (n & 0xFFFFFFFF)

def _runtime_fields(text):
    """从 RUNTIME= 行解析 (上次运行, 上次结果, 下次运行, 状态)。"""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("RUNTIME="):
            bits = stripped[8:].split("|")
            return (bits + ["", "", "", ""])[:4]
    return ("", "", "", "")

def _task_info_next(out):
    """从 register_tasks 的 TaskInfo= 行取下次运行时间；没有返回 ""。"""
    for line in (out or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("TaskInfo="):
            bits = stripped[9:].split("|")
            nxt = bits[2].strip() if len(bits) > 2 else ""
            return "" if nxt in ("NONE", "MISSING", "NEVER") else nxt
    return ""

def task_summary(text, slots, runtime=""):
    """把 query_task_status() / query_task_runtime() 的原始文本变成界面结论。

    返回 {"state": ok|warn|error|loading, "title": ..., "next": ..., "detail": ...}
    """
    by_name = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if "=" in stripped:
            name, rest = stripped.split("=", 1)
            if name in TASK_SHORT:
                by_name[name] = rest
    sched = by_name.get(TASK_SCHEDULE)
    catch = by_name.get(TASK_CATCHUP)
    if sched is None:
        return {"state": "error", "title": "任务计划：读不到状态",
                "next": "", "detail": "点「检测」重试；仍失败请用 --diag 排查"}
    if sched == "MISSING":
        return {"state": "warn", "title": "任务计划：未注册",
                "next": "", "detail": "打开「启用每日定时」开关即可注册"}

    last_run, last_code, rt_next, rt_state = _runtime_fields(runtime)
    bits = sched.split("|")
    state = "已启用" if bits[0] == "Ready" else bits[0]
    if rt_state:
        state = {"Ready": "已启用", "Running": "运行中",
                 "Disabled": "已禁用"}.get(rt_state, rt_state)
    nxt = (bits[1] if len(bits) > 1 else "").strip()
    if rt_next and rt_next not in ("NONE", "MISSING"):
        nxt = rt_next                       # 任务计划 API 的 NextRunTime 才是权威值
    swa = "补跑已开" if any("True" in b for b in bits[2:]) else "补跑未开"
    trig = ""
    for b in bits:
        if b.startswith("TRIG="):
            trig = b[5:]
    times = sorted(int(v) for v in trig.split(",") if v.strip().isdigit())
    by_time = {x.get("time"): x for x in slots}
    extra = [t for t in times
             if "%02d:%02d" % (t // 60, t % 60) not in by_time]
    detail = "%d 个触发点 · %s · 登录校准%s" % (
        len(times) or len(slots), swa,
        "已注册" if (catch and catch != "MISSING") else "未注册")
    if extra:
        detail += " · %d 个触发点不在当前配置" % len(extra)
    # 上次运行 / 上次结果：让"任务到底跑没跑"在界面里直接可见
    if last_run:
        last = "从未运行" if last_run == "NEVER" else \
            "上次 %s（%s）" % (_short_dt(last_run), _task_result_text(last_code))
        detail += " · " + last
    return {"state": "ok" if state == "已启用" and swa == "补跑已开" else "warn",
            "title": "任务计划：%s" % state,
            "next": ("下次 %s" % _short_dt(nxt)) if nxt else "",
            "detail": detail}
