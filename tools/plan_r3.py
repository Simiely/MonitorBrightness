# -*- coding: utf-8 -*-
"""R3 只读规划：打印候选分组的跨组引用与对已导入名字的依赖。"""
import ast
from pathlib import Path

MAIN = Path("monitor_brightness.py")
SERVICE = Path("service.py")
tree = ast.parse(MAIN.read_text(encoding="utf-8"))
stree = ast.parse(SERVICE.read_text(encoding="utf-8"))

GROUPS = {
    "slots": ["_minutes", "_slot_minutes", "active_slot", "active_slot_dict"],
    "schedule": [
        "apply_schedule", "_ps", "_ps_quote", "_ps_script", "task_target",
        "_task_action", "task_signature", "_ps_trigger_line",
        "build_register_script", "register_tasks", "_read_task_xml",
        "_task_definition", "_same_path", "_slot_trigger_fingerprint",
        "_xml_trigger_fingerprint", "_fp_text", "task_match_reason",
        "ensure_task_registration", "build_unregister_script", "unregister_tasks",
        "build_query_script", "_xml_flag", "_settings_block",
        "_boundary_to_minutes", "_boundary_minutes_all", "_next_run_text",
        "_days_by_min_from_fp", "query_task_status_local", "query_task_status",
        "_as_ok_errs", "build_runtime_query_script", "query_task_runtime",
        "_task_result_text", "_runtime_fields", "_task_info_next",
        "_rebuild_changes", "_rebuild_report", "task_summary",
    ],
    "cli": [
        "parse_level_arg", "parse_index_arg", "cli_set", "cli_apply_schedule",
        "cli_list", "cli_probe", "cli_diag", "cli_ensure_schedule",
        "cli_selftest", "parse_screen_action", "cli_screen",
    ],
}

CONSTS = {
    "schedule_consts": ["TASK_SCHEDULE", "TASK_CATCHUP", "_LEGACY_TASKS", "TASK_SHORT",
                 "TASKS_DIR", "_NO_WINDOW"],
}

owner = {}
for g, ns in {**GROUPS, **CONSTS}.items():
    for n in ns:
        owner[n] = g

trees = {"main": tree, "service": stree}
node_map = {}
for key, t in trees.items():
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            node_map[n.name] = (key, n)
        elif isinstance(n, ast.Assign):
            for x in n.targets:
                if isinstance(x, ast.Name):
                    node_map[x.id] = (key, n)

imports = {}
for key, t in trees.items():
    for n in t.body:
        if isinstance(n, ast.Import):
            for a in n.names:
                imports[a.asname or a.name.split(".")[0]] = (key, "import " + a.name)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                imports[a.asname or a.name] = (key, "from %s import %s" % (n.module, a.name))

for g, names in {**GROUPS, **CONSTS}.items():
    cross, outside, imported = set(), set(), set()
    for nm in names:
        entry = node_map.get(nm)
        if entry is None:
            print("!! 缺节点", g, nm)
            continue
        _, node = entry
        local = set()
        if isinstance(node, ast.FunctionDef):
            local.update(a.arg for a in node.args.args + node.args.kwonlyargs)
        for x in ast.walk(node):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                local.add(x.id)
            if isinstance(x, (ast.Import, ast.ImportFrom)):
                local.update(a.asname or a.name.split(".")[0] for a in x.names)
        free = {x.id for x in ast.walk(node)
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)} - local - {nm}
        for f in free:
            if f in owner and owner[f] != g:
                cross.add((nm, f, owner[f]))
            elif f in node_map and f not in owner:
                outside.add((nm, f))
            elif f in imports:
                imported.add((nm, f, imports[f][1]))
    print("\n=== %s (%d nodes) ===" % (g, len(names)))
    print("跨组:", sorted(cross))
    print("主文件/service 未分组依赖:", sorted(outside))
    print("import 依赖:", sorted(imported))
