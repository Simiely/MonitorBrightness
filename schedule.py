# -*- coding: utf-8 -*-
"""定时任务计划（v2.03.0 R3，从 monitor_brightness.py 逐字搬出）。

负责 PowerShell 脚本生成、注册/查询/自检与档位下发；界面控制器在上层。
"""
import html
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

from core.config import load_config
from core.logging import _write_event_log, dlog
from core.util import _short_dt
from domain.slots import _minutes, active_slot_dict
from domain.timeutil import (
    WEEKDAY_LABELS, WEEKDAY_NAMES_PS, normalize_days, parse_hhmm, slots_for_weekday,
)
from hw.ddc import set_brightness_many
from service import resolve_monitors, screen_off_cfg, screen_wake_entry
from schedule_report import (
    TASK_CATCHUP, TASK_SCHEDULE, TASK_SHORT, _LEGACY_TASKS,
    _as_ok_errs, _boundary_minutes_all, _boundary_to_minutes,
    _days_by_min_from_fp, _next_run_text, _runtime_fields,
    _task_info_next, _task_result_text, task_summary,
)





def apply_schedule(cfg=None):
    """按当前时间套用对应档位（亮度 + 该档的屏幕动作）。

    返回 (level, screen, ok, errs)；screen 为该档屏幕动作（""/off/on）。
    屏幕动作失败的错误以序号 -1 记入 errs（界面按原文显示）。

    v1.9.4：恢复 v1.8.5 的语义（不再写 last_fired、不再从 GUI 启动自检调用）。
    v2.01.0：不再把"屏幕现在是什么状态"记进配置——定时任务只负责"到点把
    这几个动作做掉"，是纯粹的一次性执行，不维护状态。
    本函数只由计划任务（--apply-schedule）与用户显式操作触发。
    """
    cfg = cfg or load_config()
    # v2.02.0：按星期过滤档位。`days` 空 = 每天（旧配置行为不变）。
    # 触发器层已经按星期注册，这里再判一次是防"登录校准"或残留任务
    # 在非指定星期被唤起——那两条路径不经过 Weekly 触发器。
    _wd = datetime.now().isoweekday()
    _slots = slots_for_weekday(cfg.get("slots", []), _wd)
    slot = active_slot_dict(_slots, _minutes())
    level = slot.get("level", 50)
    screen = slot.get("screen", "")
    targets = resolve_monitors(cfg)
    dlog("sch.decide", slot=slot, level=level, screen=screen, targets=targets,
         method=cfg.get("screen_off_method"), weekday=_wd,
         slot_days=slot.get("days") or [])
    # D3：拓扑模式的 off 跳过亮度（inactive 时 DDC 读不到/写不进）；
    # on 的亮度由 screen_on_cfg 在「按当前拓扑接回 → 回读」之后写。
    topo = cfg.get("screen_off_method") == "topology"
    if topo and screen in ("off", "on"):
        ok, errs = [], []
    else:
        ok, errs = set_brightness_many(targets, level)
    if screen == "off":
        sok, serrs = screen_off_cfg(cfg, targets)
    elif screen == "on":
        sok, serrs = screen_wake_entry(cfg, targets)
    else:
        sok, serrs = [], []
    ok = ok + sok
    errs = errs + [(-1, "屏幕 %d: %s" % (i, e)) for i, e in serrs]
    dlog("sch.result", level=level, screen=screen, ok=ok, errs=errs)
    # v2.01.0：不再记账屏幕状态。程序只负责"到点把这几个动作做掉"，
    # 做完就结束；屏幕现在是什么状态，下次需要时现场读（screen_verdict）。
    return level, screen, ok, errs

# ============================================================
# 四、任务计划（PowerShell Register-ScheduledTask）
#    ⚠️ 关键：schtasks.exe 命令行**没有**「错过就尽快补跑」的开关，
#       必须走 PowerShell 的 -StartWhenAvailable，
#       否则电脑关机 / 睡眠错过时间点后任务不会补跑（v1.0.0 的定时 bug 根因）。
# ============================================================
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

def _ps(script, timeout=90):
    """执行一段 PowerShell，返回 (returncode, 输出文本)。"""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(exe):
        exe = "powershell"
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        # 计划任务被安全软件/组策略卡住时，不能让 TimeoutExpired 冒到 CLI
        # 入口变成 traceback；返回统一的 124（与 timeout(1) 同口径）。
        return 124, "PowerShell 超时（%ss）" % timeout
    except OSError as e:
        return 127, "无法启动 PowerShell：%s" % e
    out = proc.stdout.decode("mbcs", "replace") if proc.stdout else ""
    return proc.returncode, out.strip()

def _ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"

def _ps_script(lines):
    """把 PowerShell 语句拼成一段脚本。

    ⚠️ 必须用**换行**拼接，绝不能用 '; '：
    `foreach (...) { ... ; else { ... } }` 这种把分号插在 `}` 和 `else` 之间的写法
    在 PowerShell 里是**语法错误**（实测报「无法将"else"项识别为 cmdlet」），
    整段解析失败 → 退出码非 0 → 界面显示「任务计划状态：读取失败」。
    """
    return "\n".join(lines)

def task_target():
    """返回 (要执行的程序, 需要前置的参数)。

    打包成 exe 时 sys.executable 就是本程序；
    直接从源码跑时 sys.executable 是 python 解释器，必须把脚本路径带上
    —— v1.0.0 漏了这一步，源码模式下注册出来的任务根本跑不起来。
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    script = os.path.abspath(__file__)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    runner = pyw if os.path.exists(pyw) else sys.executable
    return runner, '"%s"' % script

def _task_action(cfg):
    """当前进程应写进任务的 (程序路径, 参数)。"""
    exe, prefix = task_target()
    return exe, (prefix + " --apply-schedule").strip()

def task_signature(cfg):
    """任务"指纹"：程序路径 + 参数 + 全部触发时间。用于启动自检与日志。"""
    exe, arg = _task_action(cfg)
    times = ",".join(str(s.get("time", "")) for s in cfg.get("slots", []))
    return "%s|%s|%s" % (os.path.normcase(os.path.abspath(exe)), arg, times)

def _ps_trigger_line(slot):
    """一档 → 一条 PowerShell 触发器语句。

    v2.02.0：`days` 非空且不是全 7 天时用 `-Weekly`（任务计划程序里能直接看到
    "只在周一、三、五"）；空 = 每天，仍用 `-Daily`，与旧版注册结果完全一致。
    """
    days = normalize_days(slot.get("days"))
    at = _ps_quote(slot["time"])
    if not days:
        return "  (New-ScheduledTaskTrigger -Daily -At %s)" % at
    names = ",".join(WEEKDAY_NAMES_PS[d - 1] for d in days)
    return ("  (New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 "
            "-DaysOfWeek %s -At %s)" % (names, at))

def _register_header_lines(exe, arg, slots):
    """PowerShell 头：执行策略、动作、设置、清遗留任务、触发器数组。"""
    lines = [
        "$ErrorActionPreference = 'Stop'",
        # -AtLogOn 必须带 -User，否则会被当成"任意用户登录"、非管理员注册直接拒绝访问
        "$me = $env:USERNAME; if (-not $me) "
        "{ $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name }",
        "$action = New-ScheduledTaskAction -Execute %s -Argument %s" % (_ps_quote(exe), _ps_quote(arg)),
        # StartWhenAvailable = 错过计划时间（关机/睡眠）后尽快补跑；schtasks.exe 无此开关
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)",
        # 只清历史遗留任务名；当前任务不再先删后建
        "foreach ($n in @(%s)) { Unregister-ScheduledTask -TaskName $n "
        "-Confirm:$false -ErrorAction SilentlyContinue }"
        % ", ".join(_ps_quote(x) for x in _LEGACY_TASKS),
        "$triggers = @(",
    ]
    for i, x in enumerate(slots):
        lines.append(_ps_trigger_line(x))
    return lines

def _register_task_lines(slots):
    """主定时任务：原地更新优先，失败回退删后建。"""
    return [
        ")",
        "$old = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % TASK_SCHEDULE,
        "if ($old) {",
        "  try {",
        "    Set-ScheduledTask -TaskName '%s' -Action $action -Trigger $triggers "
        "-Settings $settings -Description %s -ErrorAction Stop | Out-Null"
        % (TASK_SCHEDULE, _ps_quote("MonitorBrightness · %d 个时间段（到点切换亮度）" % len(slots))),
        "    Write-Output 'REBUILT=UPDATED'",
        "  } catch {",
        "    Unregister-ScheduledTask -TaskName '%s' -Confirm:$false" % TASK_SCHEDULE,
        "    Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $triggers "
        "-Settings $settings -Description %s | Out-Null"
        % (TASK_SCHEDULE, _ps_quote("MonitorBrightness · %d 个时间段（到点切换亮度）" % len(slots))),
        "    Write-Output ('REBUILT=FALLBACK ' + $_.Exception.Message)",
        "  }",
        "} else {",
        "  Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $triggers "
        "-Settings $settings -Description %s | Out-Null"
        % (TASK_SCHEDULE, _ps_quote("MonitorBrightness · %d 个时间段（到点切换亮度）" % len(slots))),
        "  Write-Output 'REBUILT=NEW'",
        "}",
    ]

def _register_catchup_lines(enabled):
    """登录补跑任务：独立 try/catch，失败不影响主任务。"""
    lines = []
    if enabled:
        # 单独 try/catch：这条失败也不能拖垮定时任务，失败原因要能带回界面
        lines += [
            "$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $me",
            "$tLogon.Delay = 'PT30S'",
            "$g = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % TASK_CATCHUP,
            "try {",
            "  if ($g) {",
            "    Set-ScheduledTask -TaskName '%s' -Action $action -Trigger $tLogon -Settings $settings "
            "-Description 'MonitorBrightness: 登录后按当前时间校准亮度（补上错过的切换点）' "
            "-ErrorAction Stop | Out-Null" % TASK_CATCHUP,
            "  } else {",
            "    Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $tLogon -Settings $settings "
            "-Description 'MonitorBrightness: 登录后按当前时间校准亮度（补上错过的切换点）' "
            "-ErrorAction Stop | Out-Null" % TASK_CATCHUP,
            "  }",
            "  Write-Output 'CATCHUP=OK'",
            "} catch { Write-Output ('CATCHUP=FAIL ' + $_.Exception.Message) }",
        ]
    else:
        lines.append("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false "
                     "-ErrorAction SilentlyContinue" % TASK_CATCHUP)
    return lines

def _register_report_lines():
    """回读段：SWA / 触发点数 / 上次下次运行 / 补跑任务状态。"""
    lines = [
        "$t = Get-ScheduledTask -TaskName '%s'" % TASK_SCHEDULE,
        "Write-Output ('StartWhenAvailable=' + $t.Settings.StartWhenAvailable)",
        "Write-Output ('Triggers=' + $t.Triggers.Count)",
        "$info = Get-ScheduledTaskInfo -TaskName '%s'" % TASK_SCHEDULE,
        "if ($info.LastRunTime.Year -lt 2000) { $lr = 'NEVER' } "
        "else { $lr = $info.LastRunTime.ToString('yyyy-MM-dd HH:mm') }",
        "if ($info.NextRunTime) { $nx = $info.NextRunTime.ToString('yyyy-MM-dd HH:mm') } "
        "else { $nx = 'NONE' }",
        "Write-Output ('TaskInfo=' + $lr + '|' + [int64]$info.LastTaskResult + '|' + $nx)",
        "$g2 = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % TASK_CATCHUP,
        "if ($g2) { Write-Output ('CatchUpTask=' + $g2.State) } else { Write-Output 'CatchUpTask=MISSING' }",
        "Write-Output 'REGISTER_OK'",
    ]
    return lines

def build_register_script(cfg):
    """生成注册脚本：一条定时任务挂 N 个触发器（每档一个）、可选登录补跑任务。

    v1.9.0 起：任务已存在时优先 Set-ScheduledTask **原地更新**（保留上次运行记录），
    失败才回退到 Unregister + Register。旧实现每次都先删后建，会把 LastRunTime
    重置成"从未运行"，也让用户误以为定时没生效。
    """
    exe, arg = _task_action(cfg)
    slots = cfg.get("slots", [])
    lines = _register_header_lines(exe, arg, slots)
    lines += _register_task_lines(slots)
    lines += _register_catchup_lines(bool(cfg.get("catch_up_on_logon")))
    lines += _register_report_lines()
    return _ps_script(lines)


def register_tasks(cfg):
    """注册/更新定时任务。返回 (是否成功, 说明文本)。"""
    code, out = _ps(build_register_script(cfg))
    if code == 0 and "REGISTER_OK" in out:
        return True, out
    return False, out or ("注册子进程退出码 %s" % code)

# ------------------------------------------------------------
# 任务定义校验：启动时静默自愈
#   v1.8.5 之前只有用户手动点「应用」才会重建任务。exe 换目录/改名（每版构建目录
#   dist_vXXX 都在变）后，任务 action 仍指旧路径：任务"跑了"，但找不到程序，
#   LastTaskResult 0x80070002，用户侧就是"今天没有准时执行"。
#   现在每次启动读一次落盘的任务 XML，路径/参数/触发点/设置任一项和当前进程不一致
#   就静默重建，并写一条事件日志（界面可见）。
# ------------------------------------------------------------
TASKS_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Tasks")

def _read_task_xml(name=TASK_SCHEDULE):
    """读任务计划落盘的 XML；读不到返回 ""。"""
    path = os.path.join(TASKS_DIR, name)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return ""
    return (raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
            else raw.decode("utf-8", "replace"))

def _task_definition(xml_text):
    """从任务 XML 取 (Command, Arguments)。"""
    m = re.search(r"<Actions[^>]*>(.*?)</Actions>", xml_text or "", re.S)
    block = m.group(1) if m else ""
    cm = re.search(r"<Command>(.*?)</Command>", block, re.S)
    am = re.search(r"<Arguments>(.*?)</Arguments>", block, re.S)
    return (html.unescape(cm.group(1).strip()) if cm else "",
            html.unescape(am.group(1).strip()) if am else "")

def _same_path(a, b):
    try:
        return os.path.normcase(os.path.abspath(str(a).strip().strip('"'))) == \
            os.path.normcase(os.path.abspath(str(b).strip().strip('"')))
    except Exception:
        return str(a).strip().strip('"').lower() == str(b).strip().strip('"').lower()

def _slot_trigger_fingerprint(slots):
    """配置侧触发器指纹：[(当日分钟, 星期frozenset)]，全 7 天归成空集。"""
    out = []
    for s in slots or []:
        days = frozenset(normalize_days(s.get("days")))
        out.append((parse_hhmm(s.get("time"), (0, 0)), days))
    return sorted(out)

def _xml_trigger_fingerprint(xml_text):
    """任务 XML 侧触发器指纹。

    一个 `<CalendarTrigger>` 里若有 `<DaysOfWeek>` 就是 Weekly，否则是 Daily
    （空集）。选满 7 天与"每天"等价，统一归成空集，避免每次启动都误判要重建。
    """
    out = []
    for block in re.finditer(r"<CalendarTrigger>(.*?)</CalendarTrigger>",
                             xml_text or "", re.S):
        body = block.group(1)
        m = re.search(r"<StartBoundary>([^<]+)</StartBoundary>", body)
        if not m:
            continue
        mins = _boundary_to_minutes(m.group(1).strip())
        if mins is None:
            continue
        days = set()
        dw = re.search(r"<DaysOfWeek>(.*?)</DaysOfWeek>", body, re.S)
        if dw:
            for i, name in enumerate(WEEKDAY_NAMES_PS, start=1):
                if re.search(r"<%s\s*/>" % name, dw.group(1)):
                    days.add(i)
        out.append((mins, frozenset() if len(days) >= 7 else frozenset(days)))
    return sorted(out)

def _fp_text(fp):
    """指纹 → 人话，只用于不一致时的事件/状态文案。"""
    if not fp:
        return "无触发器"
    parts = []
    for mins, days in fp:
        d = ("每天" if not days
             else "周" + ",".join(WEEKDAY_LABELS[n - 1] for n in sorted(days)))
        parts.append("%02d:%02d(%s)" % (mins // 60, mins % 60, d))
    return " ".join(parts)

def task_match_reason(cfg, xml_text=None):
    """任务是否与当前进程/配置一致；一致返回空串，否则返回人话原因。"""
    if xml_text is None:
        xml_text = _read_task_xml()
    if not xml_text:
        return "任务未注册"
    exe, arg = _task_action(cfg)
    command, arguments = _task_definition(xml_text)
    if not command:
        return "任务定义里没有 action"
    if not _same_path(command, exe):
        return "程序路径不一致（任务：%s，当前：%s）" % (command, exe)
    if arguments.strip() != arg.strip():
        return "启动参数不一致（任务：%s，当前：%s）" % (arguments, arg)
    block = _settings_block(xml_text)
    if _xml_flag(block, "Enabled") is False:
        return "任务已被禁用"
    if _xml_flag(block, "StartWhenAvailable") is False:
        return "未开启「错过补跑」(StartWhenAvailable)"
    bounds = sorted(set(_boundary_minutes_all(xml_text)))
    wanted = sorted({parse_hhmm(s.get("time"), (0, 0)) for s in cfg.get("slots", [])})
    if bounds != wanted:
        have = ",".join("%02d:%02d" % (m // 60, m % 60) for m in bounds) or "无"
        want = ",".join("%02d:%02d" % (m // 60, m % 60) for m in wanted) or "无"
        return "触发时间不一致（任务：%s，配置：%s）" % (have, want)
    # v2.02.0：星期也要比对，否则用户改了「只在工作日」不会重建任务
    have_fp = _xml_trigger_fingerprint(xml_text)
    want_fp = _slot_trigger_fingerprint(cfg.get("slots", []))
    if have_fp != want_fp:
        return "触发星期不一致（任务：%s，配置：%s）" % (
            _fp_text(have_fp), _fp_text(want_fp))
    return ""

def ensure_task_registration(cfg, force=False):
    """让"计划任务"与"用户当前配置"保持一致。返回 (是否可用, 说明)。

    ⚠️ v2.01.0 修走查 E2-04：原来 `enabled=False` 时直接 return，
    连 force=True（`--force`）也被吞掉——用户显式要求"不管怎样对齐一次"，
    程序却因为记住的开关而什么都不做。现在 force 的语义是"对齐"，两个方向都做：
      enabled=True  → 任务不一致（或 force）就重建
      enabled=False → force 时**反向对齐**：把残留任务清掉
    在后台线程调用，不阻塞窗口显示；结果写 events.log。
    """
    if not cfg.get("enabled"):
        if not force:
            return True, "定时未启用，跳过任务校验"
        ok, out = unregister_tasks()
        event = ("定时未启用：残余计划任务已清理" if ok
                 else "定时未启用，但残余任务清理失败：%s"
                      % ((out or "").strip().splitlines() or ["未知原因"])[-1])
        _write_event_log(event)
        return ok, event
    reason = task_match_reason(cfg)
    if not reason and not force:
        return True, ""
    ok, out = register_tasks(cfg)
    if ok:
        event = "计划任务已按当前程序重建（%s）" % (reason or "手动强制")
        nxt = _task_info_next(out)
        if nxt:
            # 启动自检不弹窗（每次开机都弹会糊脸），但事件日志要给足细节
            event += "，%d 档，下次 %s" % (len(cfg.get("slots", [])), _short_dt(nxt))
        _write_event_log(event)
        return True, event
    detail = (out or "").strip().splitlines()
    event = "计划任务重建失败：%s" % (detail[-1] if detail else "未知原因")
    _write_event_log(event)
    return False, event

def build_unregister_script():
    return _ps_script([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "foreach ($n in @(%s)) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }"
        % ", ".join(_ps_quote(x) for x in (TASK_SCHEDULE, TASK_CATCHUP) + _LEGACY_TASKS),
        "Write-Output 'UNREGISTER_OK'",
    ])

def unregister_tasks():
    code, out = _ps(build_unregister_script())
    return code == 0 and "UNREGISTER_OK" in out, out

def build_query_script():
    """生成「查询三条任务状态」的脚本（抽成函数是为了能被自测覆盖）。

    注意：这是**兜底路径**。正常走 query_task_status_local() 直接读盘上的任务 XML，
    只有读不出来（权限等）才会启动 PowerShell —— 实测冷启动要 ~2.7 秒。
    """
    return _ps_script([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "foreach ($n in @('%s','%s')) {" % (TASK_SCHEDULE, TASK_CATCHUP),
        "  $t = Get-ScheduledTask -TaskName $n",
        "  if ($t) {",
        "    $i = Get-ScheduledTaskInfo -TaskName $n",
        "    Write-Output ($n + '=' + $t.State + '|' + $i.NextRunTime + '|SWA=' + $t.Settings.StartWhenAvailable)",
        "  } else {",
        "    Write-Output ($n + '=MISSING')",
        "  }",
        "}",
    ])

def _xml_flag(block, tag):
    """从 XML 片段里取布尔标记；取不到返回 None。"""
    m = re.search(r"<%s>(true|false)</%s>" % (tag, tag), block or "", re.I)
    return (m.group(1).lower() == "true") if m else None

def _settings_block(xml_text):
    """只取 <Settings>…</Settings>：<Enabled> 在触发器里也有，必须限定作用域。"""
    m = re.search(r"<Settings>(.*?)</Settings>", xml_text or "", re.S)
    return m.group(1) if m else ""





def query_task_status_local():
    """直接读任务计划落盘的 XML 得到任务状态（实测 ~1ms，不启动子进程）。

    返回格式与 query_task_status() 一致；若任务文件**存在但读不出来**（权限等），
    返回 None 表示"请走兜底"，不要误报成 MISSING。
    """
    out = []
    for name in (TASK_SCHEDULE, TASK_CATCHUP):
        path = os.path.join(TASKS_DIR, name)
        if not os.path.exists(path):
            out.append(name + "=MISSING")
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception:
            return None
        txt = (raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
               else raw.decode("utf-8", "replace"))
        block = _settings_block(txt)
        state = "Disabled" if _xml_flag(block, "Enabled") is False else "Ready"
        swa = _xml_flag(block, "StartWhenAvailable")
        if name == TASK_CATCHUP:
            out.append("%s=%s||SWA=%s" % (name, state, swa))
        else:
            bounds = _boundary_minutes_all(txt)
            nxt = _next_run_text(
                bounds, days_by_min=_days_by_min_from_fp(
                    _xml_trigger_fingerprint(txt)))
            out.append("%s=%s|%s|SWA=%s|TRIG=%s"
                       % (name, state, nxt, swa,
                          ",".join(str(m) for m in sorted(bounds))))
    return "\n".join(out)

def query_task_status():
    """查询三条例行任务的状态/下次运行时间，用于界面展示。

    输出每行形如 `任务名=State|NextRunTime|SWA=True`；任务不存在则为 `任务名=MISSING`。

    快路径：读本地任务 XML（实测 ~1ms）。
    兜底：启动 PowerShell 查询（实测 ~2.7s 冷启动），只在 XML 读不出来时走。
    """
    local = query_task_status_local()
    if local is not None:
        return local
    code, out = _ps(build_query_script())
    return out if code == 0 else ""


def build_runtime_query_script():
    """查询"上次运行时间 / 上次结果 / 下次运行时间"——任务计划 API 才拿得到。

    XML 里只有定义，没有 LastRunTime/LastTaskResult，所以要单独一次 PowerShell。
    界面在后台线程调用，2~3 秒，不影响窗口显示。
    """
    return _ps_script([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "$i = Get-ScheduledTaskInfo -TaskName '%s'" % TASK_SCHEDULE,
        "$t = Get-ScheduledTask -TaskName '%s'" % TASK_SCHEDULE,
        "if (-not $i) { Write-Output 'RUNTIME=MISSING'; exit 0 }",
        "if ($i.LastRunTime.Year -lt 2000) { $lr = 'NEVER' } "
        "else { $lr = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') }",
        "if ($i.NextRunTime) { $nx = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') } "
        "else { $nx = 'NONE' }",
        "Write-Output ('RUNTIME=' + $lr + '|' + [int64]$i.LastTaskResult + '|' + $nx "
        "+ '|' + $t.State)",
    ])

def query_task_runtime():
    """返回一行 `RUNTIME=上次运行|上次结果|下次运行|状态`；失败返回 ""。"""
    code, out = _ps(build_runtime_query_script())
    return out if code == 0 else ""




def _rebuild_changes(before_xml, cfg):
    """重建前后任务定义差异：[] / ["任务路径"] / ["触发点"] / 两者。"""
    if not before_xml:
        return ["任务路径", "触发点"]         # 原本没有任务，路径+触发点都是新写的
    command, arguments = _task_definition(before_xml)
    exe, arg = _task_action(cfg)
    changed = []
    if not _same_path(command, exe) or arguments.strip() != arg.strip():
        changed.append("任务路径")
    old_bounds = sorted(set(_boundary_minutes_all(before_xml)))
    new_bounds = sorted({parse_hhmm(s.get("time"), (0, 0))
                         for s in cfg.get("slots", [])})
    if old_bounds != new_bounds:
        changed.append("触发点")
    return changed

def _rebuild_report(cfg, out, before_xml):
    """重建成功后的确认正文：档位数量、下次运行、改了什么。"""
    slots = sorted(cfg.get("slots", []),
                   key=lambda s: parse_hhmm(s.get("time"), (0, 0)))
    times = [s.get("time") for s in slots]
    lines = ["任务计划已重建成功。", "",
             "• 触发档位：%d 档" % len(slots)]
    if times:
        # 用空格分隔，Tk Label 只在空格处换行；顿号连写会整行被裁掉
        lines.append("   " + "  ".join(times))
    nxt = _task_info_next(out)
    if not nxt:
        minutes = sorted({parse_hhmm(t, (0, 0)) for t in times})
        if minutes:
            nxt = _next_run_text(
                minutes, days_by_min=_days_by_min_from_fp(
                    _slot_trigger_fingerprint(slots)))
    lines.append("• 下次运行：%s" % (_short_dt(nxt) if nxt
                                    else "未取到（点「检测」刷新）"))
    changed = _rebuild_changes(before_xml, cfg)
    if changed:
        lines.append("• 已更新%s" % "/".join(changed))
    if cfg.get("catch_up_on_logon", True):
        lines.append("• 登录后自动校准：%s"
                     % ("注册失败" if "CATCHUP=FAIL" in (out or "") else "已注册"))
    else:
        lines.append("• 登录后自动校准：未开启")
    return "\n".join(lines)

