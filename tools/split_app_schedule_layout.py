# -*- coding: utf-8 -*-
"""R4-4：把 _ScheduleActions / _LayoutActions 与布局辅助函数逐字搬到 app/。

- app/schedule.py：_ScheduleActions（原 19 方法 + 从 screen_power 归位的 _finish_apply）
- app/layout.py  ：5 个布局/拓扑辅助函数 + _LayoutActions（18 方法）

节点本体全部 AST dump 核对；`_finish_apply` 从 app/screen_power.py 删除，
主文件只保留 import 再导出。
"""
import ast
from pathlib import Path

MAIN = Path("monitor_brightness.py")
SCHEDULE = Path("app/schedule.py")
LAYOUT = Path("app/layout.py")
POWER = Path("app/screen_power.py")

main_text = MAIN.read_text(encoding="utf-8")
power_text = POWER.read_text(encoding="utf-8")
main_lines = main_text.splitlines(keepends=True)
power_lines = power_text.splitlines(keepends=True)
main_tree = ast.parse(main_text, filename=str(MAIN))
power_tree = ast.parse(power_text, filename=str(POWER))


def find_class(tree, name):
    return next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == name)


def find_func(tree, name):
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


schedule_cls = find_class(main_tree, "_ScheduleActions")
layout_cls = find_class(main_tree, "_LayoutActions")
layout_helpers = [find_func(main_tree, n) for n in (
    "topology_heir_candidates", "remember_layout_snapshot",
    "save_default_layout", "load_default_layout", "restore_default_layout",
)]
power_cls = find_class(power_tree, "_ScreenPowerActions")
finish_apply = next(m for m in power_cls.body
                    if isinstance(m, ast.FunctionDef) and m.name == "_finish_apply")


def source(node, lines):
    return "".join(lines[node.lineno - 1:node.end_lineno])


def dump(node):
    return ast.dump(node, include_attributes=False)


schedule_header = '''# -*- coding: utf-8 -*-
"""定时控制器（R4-4，从 monitor_brightness.py 逐字搬出）。

`_finish_apply` 原本误放在 _ScreenActions；R4-3 暂留 screen_power，
这里按职责归位到任务重建完成回调。
"""
from core.config import save_config
from core.logging import _write_event_log, read_session_events
from domain.timeutil import parse_hhmm
from schedule import (
    _days_by_min_from_fp, _next_run_text, _read_task_xml, _rebuild_report,
    _slot_trigger_fingerprint, ensure_task_registration, query_task_runtime,
    query_task_status, register_tasks, task_summary, unregister_tasks,
)
from service import selection_anchors
from app.common import selection_payload


'''

layout_header = '''# -*- coding: utf-8 -*-
"""布局 / 拓扑控制器（R4-4，从 monitor_brightness.py 逐字搬出）。"""
import ctypes
import json
import threading
import time
from ctypes import wintypes

from core.config import _atomic_write_text, save_config
from core.logging import dlog
from domain.decisions import (
    ccd_friendly_name, is_vdd_index, plan_model_updates, restore_selection,
)
from hw.ccd import (
    _ccd_api, _ccd_apply, _ccd_make_snapshot, _ccd_query_raw, _ccd_wait_state,
    ccd_probe_topology, screen_verdict,
)
from hw.ccd_structs import _CcdModeInfo, _CcdPathInfo
from hw.ddc import _display_key, _dxva2, enumerate_targets, monitor_model, release_targets
from hw.geometry import _ccd_layout_by_anchor
from hw.topology import topology_off
from schedule_report import _as_ok_errs
from service import (
    resolve_monitors, selection_anchors, topology_enable_anchor,
)
from ui.sheets import HeirSheet
from app.common import selection_payload


'''

schedule_src = (schedule_header + source(schedule_cls, main_lines).rstrip() + "\n\n"
                + source(finish_apply, power_lines).rstrip() + "\n")
layout_src = (layout_header + "\n\n".join(
    source(n, main_lines).rstrip() for n in layout_helpers) + "\n\n\n"
    + source(layout_cls, main_lines).rstrip() + "\n")

# ── AST 校验 ──────────────────────────────────────────────────────────
schedule_tree = ast.parse(schedule_src)
layout_tree = ast.parse(layout_src)
new_schedule_cls = find_class(schedule_tree, "_ScheduleActions")
new_layout_cls = find_class(layout_tree, "_LayoutActions")

for node in schedule_cls.body:
    if isinstance(node, ast.FunctionDef):
        new = next(m for m in new_schedule_cls.body
                   if isinstance(m, ast.FunctionDef) and m.name == node.name)
        assert dump(node) == dump(new), "%s 搬移不一致" % node.name
assert any(isinstance(n, ast.Assign) and any(
    isinstance(t, ast.Name) and t.id == "_refresh_task_status"
    for t in n.targets) for n in new_schedule_cls.body), "兼容别名未搬"
new_finish = next(m for m in new_schedule_cls.body
                  if isinstance(m, ast.FunctionDef) and m.name == "_finish_apply")
assert dump(finish_apply) == dump(new_finish), "_finish_apply 归位不一致"

for node in layout_helpers:
    assert dump(node) == dump(find_func(layout_tree, node.name)), "%s 搬移不一致" % node.name
for node in layout_cls.body:
    if isinstance(node, ast.FunctionDef):
        new = next(m for m in new_layout_cls.body
                   if isinstance(m, ast.FunctionDef) and m.name == node.name)
        assert dump(node) == dump(new), "%s 搬移不一致" % node.name

# ── 主文件删节点（倒序） ──────────────────────────────────────────────
for node in sorted([schedule_cls, layout_cls] + layout_helpers,
                   key=lambda n: n.lineno, reverse=True):
    del main_lines[node.lineno - 1:node.end_lineno]
new_main = "".join(main_lines)

old_import = "from app.screen_power import _ScreenPowerActions  # noqa: E402\n"
new_import = old_import + (
    "from app.schedule import _ScheduleActions      # noqa: E402\n"
    "from app.layout import (                       # noqa: E402\n"
    "    _LayoutActions, load_default_layout, remember_layout_snapshot,\n"
    "    restore_default_layout, save_default_layout, topology_heir_candidates,\n"
    ")\n")
assert old_import in new_main, "app.screen_power import 锚点未找到"
new_main = new_main.replace(old_import, new_import, 1)

# ── 从 screen_power.py 删除 _finish_apply ─────────────────────────────
del power_lines[finish_apply.lineno - 1:finish_apply.end_lineno]
new_power = "".join(power_lines).rstrip() + "\n"
new_power_tree = ast.parse(new_power)
new_power_cls = find_class(new_power_tree, "_ScreenPowerActions")
assert not any(isinstance(n, ast.FunctionDef) and n.name == "_finish_apply"
               for n in new_power_cls.body), "screen_power 仍残留 _finish_apply"

# ── 落盘 ──────────────────────────────────────────────────────────────
SCHEDULE.write_text(schedule_src, encoding="utf-8")
LAYOUT.write_text(layout_src, encoding="utf-8")
POWER.write_text(new_power, encoding="utf-8")
MAIN.write_text(new_main, encoding="utf-8")

check = ast.parse(new_main)
for name in ("_ScheduleActions", "_LayoutActions"):
    assert not any(isinstance(n, ast.ClassDef) and n.name == name
                   for n in check.body), "主文件残留 %s" % name
for name in ("topology_heir_candidates", "remember_layout_snapshot",
             "save_default_layout", "load_default_layout", "restore_default_layout"):
    assert not any(isinstance(n, ast.FunctionDef) and n.name == name
                   for n in check.body), "主文件残留 %s" % name

print("R4-4 搬运完成：2 个类 + 5 个辅助函数 AST 逐字一致")
print("  monitor_brightness.py %d 行" % len(new_main.splitlines()))
print("  app/schedule.py       %d 行" % len(schedule_src.splitlines()))
print("  app/layout.py         %d 行" % len(layout_src.splitlines()))
print("  app/screen_power.py   %d 行" % len(new_power.splitlines()))
