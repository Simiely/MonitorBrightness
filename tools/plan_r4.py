# -*- coding: utf-8 -*-
"""R4 只读规划：打印 app/ 候选分组的跨组引用。

用法：python tools/plan_r4.py
不修改任何文件。`self.xxx` 的跨 Mixin 引用不会形成 import 依赖（最终都混进
App 实例），但会单独列出来，提醒拆类时别破坏回调链。
"""
import ast
from pathlib import Path

MAIN = Path("monitor_brightness.py")
tree = ast.parse(MAIN.read_text(encoding="utf-8"))

# 顶层节点归属（未列出的留在主入口）
GROUPS = {
    "ui_sheets": ["SettingsSheet"],
    "app_common": [
        "status_payload", "row_action_blocker", "auto_relight_minutes",
        "tray_tooltip_text", "selection_payload", "_raster_line",
        "_route_wheel", "install_wheel_router", "acquire_single_instance",
        "_activate_existing_window",
    ],
    "app_layout": [
        "topology_heir_candidates", "remember_layout_snapshot",
        "save_default_layout", "load_default_layout", "restore_default_layout",
        "_LayoutActions",
    ],
    "app_schedule": ["_ScheduleActions"],
    "app_settings": ["_SettingsActions"],
    "app_diag": ["_DiagActions"],
    "app_base": ["App", "main", "_migrate_old_logs"],
}

# _ScreenActions 按方法拆分（原类不再整体迁移）
METHOD_GROUPS = {
    "app_brightness": [
        "apply_presets_from_settings", "select_all", "select_none",
        "on_monitor_toggle", "refresh_monitors", "_set_actions_enabled",
        "read_current", "refresh_brightness", "apply_now",
        "_finish_apply_level", "apply_preset", "_finish_preset",
        "probe_screens", "_update_hint_brightness", "_status_payload",
    ],
    "app_screen_power": [
        "_row_action_blocker", "monitor_off", "monitor_on",
        "_queue_screen_action", "_dispatch_pending_screen", "screen_off",
        "_finish_screen_off", "screen_on", "_cancel_relight_timer",
        "_finish_screen_on", "_auto_relight_minutes",
    ],
    # _finish_apply 名字像屏幕回调，实质是任务重建后的完成回调。
    "app_schedule": ["_finish_apply"],
}

# ── 建立索引 ──────────────────────────────────────────────────────────
top_nodes = {}
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        top_nodes[node.name] = node

methods = {}
method_owner = {}
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[item.name] = item
                method_owner[item.name] = node.name

imports = {}
for node in tree.body:
    if isinstance(node, ast.Import):
        for a in node.names:
            imports[a.asname or a.name.split(".")[0]] = "import " + a.name
    elif isinstance(node, ast.ImportFrom):
        for a in node.names:
            imports[a.asname or a.name] = "from %s import %s" % (node.module, a.name)

owner = {}
for group, names in GROUPS.items():
    for name in names:
        if name in top_nodes:
            owner[name] = group
        else:
            print("!! 顶层节点缺失：%s.%s" % (group, name))
for group, names in METHOD_GROUPS.items():
    for name in names:
        if name in methods:
            owner[name] = group
        else:
            print("!! 方法缺失：%s.%s" % (group, name))

# ── 扫描引用 ──────────────────────────────────────────────────────────
repo = {g: {"cross": set(), "self": set(), "import": set()}
        for g in list(GROUPS) + list(METHOD_GROUPS)}

def scan(group, node, local_names=()):
    local = set(local_names)
    for sub in ast.walk(node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local.update(a.arg for a in sub.args.args + sub.args.kwonlyargs)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            local.add(sub.id)
        if isinstance(sub, (ast.Import, ast.ImportFrom)):
            local.update(a.asname or a.name.split(".")[0] for a in sub.names)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            name = sub.id
            if name in local:
                continue
            if name in owner:
                if owner[name] != group:
                    repo[group]["cross"].add((name, owner[name]))
            elif name in imports:
                repo[group]["import"].add((name, imports[name]))
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                and sub.value.id == "self" and sub.attr in method_owner:
            target = method_owner[sub.attr]
            target_group = owner.get(sub.attr)
            if target_group and target_group != group:
                repo[group]["self"].add((sub.attr, target, target_group))
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) \
                and sub.id in owner and owner[sub.id] != group:
            repo[group]["cross"].add((sub.id, owner[sub.id]))

for group, names in GROUPS.items():
    for name in names:
        scan(group, top_nodes[name])
for group, names in METHOD_GROUPS.items():
    for name in names:
        scan(group, methods[name], ("self",))

# ── 输出 ──────────────────────────────────────────────────────────────
for group in sorted(repo):
    info = repo[group]
    print("\n=== %s ===" % group)
    print("  顶层跨组:", sorted(info["cross"]))
    print("  self 跨组:", sorted(info["self"]))
    print("  外部 import:", sorted(n for n, _ in info["import"]))

graph = {}
for group, info in repo.items():
    for _name, dst in info["cross"]:
        graph.setdefault(group, set()).add(dst)
cycles = []
def dfs(node, path, seen):
    for nxt in graph.get(node, ()):
        if nxt == path[0] and len(path) > 1:
            cycles.append(path[:])
        elif nxt not in seen and nxt not in path:
            dfs(nxt, path + [nxt], seen | {nxt})
for start in graph:
    dfs(start, [start], {start})
print("\n环检测（只看顶层名字引用，self 引用不算 import 环）:")
if cycles:
    for c in cycles[:10]:
        print("  " + " -> ".join(c))
else:
    print("  ✅ 无环")
