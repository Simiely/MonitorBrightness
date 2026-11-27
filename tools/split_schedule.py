# -*- coding: utf-8 -*-
"""R3：把调度/任务计划定义逐字搬到 schedule.py；resolve_monitors 挪进 service.py。"""
import ast
from pathlib import Path

SRC = Path("monitor_brightness.py")
SCHEDULE = Path("schedule.py")
SERVICE = Path("service.py")

FUNCS = [
    "apply_schedule", "_ps", "_ps_quote", "_ps_script", "task_target",
    "_task_action", "task_signature", "_ps_trigger_line", "build_register_script",
    "register_tasks", "_read_task_xml", "_task_definition", "_same_path",
    "_slot_trigger_fingerprint", "_xml_trigger_fingerprint", "_fp_text",
    "task_match_reason", "ensure_task_registration", "build_unregister_script",
    "unregister_tasks", "build_query_script", "_xml_flag", "_settings_block",
    "_boundary_to_minutes", "_boundary_minutes_all", "_next_run_text",
    "_days_by_min_from_fp", "query_task_status_local", "query_task_status",
    "_as_ok_errs", "build_runtime_query_script", "query_task_runtime",
    "_task_result_text", "_runtime_fields", "_task_info_next",
    "_rebuild_changes", "_rebuild_report", "task_summary",
]
CONSTS = ["TASK_SCHEDULE", "TASK_CATCHUP", "_LEGACY_TASKS", "TASK_SHORT",
          "TASKS_DIR", "_NO_WINDOW"]
NAMES = FUNCS + CONSTS

HEADER = '''# -*- coding: utf-8 -*-
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

'''

MAIN_IMPORT = '''from schedule import (                              # noqa: E402
    TASK_CATCHUP, TASK_SCHEDULE, TASK_SHORT, _LEGACY_TASKS, _NO_WINDOW, TASKS_DIR,
    _as_ok_errs, _boundary_minutes_all, _boundary_to_minutes, _days_by_min_from_fp,
    _fp_text, _next_run_text, _ps, _ps_quote, _ps_script, _ps_trigger_line,
    _read_task_xml, _rebuild_changes, _rebuild_report, _runtime_fields, _same_path,
    _settings_block, _slot_trigger_fingerprint, _task_action, _task_definition,
    _task_info_next, _task_result_text, _xml_flag, _xml_trigger_fingerprint,
    apply_schedule, build_query_script, build_register_script,
    build_runtime_query_script, build_unregister_script, ensure_task_registration,
    query_task_runtime, query_task_status, query_task_status_local, register_tasks,
    task_match_reason, task_signature, task_summary, task_target, unregister_tasks,
)
'''


def chunk(nodes, lines, nm):
    node = nodes[nm]
    start = node.lineno - 1
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return (node.lineno, "".join(lines[start:node.end_lineno])), (start, node.end_lineno)


def main():
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    nodes = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            nodes[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    nodes[t.id] = n

    picked, ranges = [], []
    for nm in NAMES:
        if nm not in nodes:
            raise SystemExit("缺节点: %s" % nm)
        c, r = chunk(nodes, lines, nm)
        picked.append(c)
        ranges.append(r)
    picked.sort()
    SCHEDULE.write_text(
        HEADER + "\n\n".join(c.rstrip("\n") for _, c in picked) + "\n",
        encoding="utf-8", newline="\n")

    # resolve_monitors → service.py（追加到末尾，函数体逐字）
    c, r = chunk(nodes, lines, "resolve_monitors")
    ranges.append(r)
    svc = SERVICE.read_text(encoding="utf-8").rstrip("\n")
    SERVICE.write_text(svc + "\n\n" + c[1].rstrip("\n") + "\n",
                       encoding="utf-8", newline="\n")

    for a, b in sorted(ranges, reverse=True):
        del lines[a:b]

    idx = next(i for i, ln in enumerate(lines) if ln.startswith("from service import ("))
    while not lines[idx].rstrip().endswith(")"):
        idx += 1
    lines[idx - 1] = lines[idx - 1].rstrip() + ", resolve_monitors\n"
    lines.insert(idx + 1, MAIN_IMPORT)

    SRC.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("schedule：搬移 %d 个节点，resolve_monitors → service" % len(NAMES))


if __name__ == "__main__":
    main()
