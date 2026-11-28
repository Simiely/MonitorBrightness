# -*- coding: utf-8 -*-
"""验证候选模块划分的依赖方向（只读）。"""
import ast, sys, io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

src = (ROOT / "monitor_brightness.py").read_text(encoding="utf-8")
tree = ast.parse(src)

# 候选分组：模块级函数 + 类 → 归属模块
GROUPS = {}
def assign(module, names):
    for n in names:
        GROUPS[n] = module

assign("core.logging", ["_migrate_old_logs","_write_event_log","read_session_events","read_recent_events",
    "_diag_file","diag_set_enabled","diag_enabled","dlog","diag_dump","diag_export"])
assign("core.config", ["coerce_setting","_coerce_positions","_coerce_levels","_coerce_ids","_valid_hhmm",
    "_coerce_slots","default_config","_load_model_cache","_backup_bad_config","load_config",
    "_atomic_write_text","save_config","old_config_present","get_autostart","set_autostart","_autostart_path"])
assign("hw.ddc", ["_dxva2","_read_vcp","_write_vcp","enumerate_targets","release_targets","_select",
    "_capabilities","_display_uid","_display_key","_capabilities_cached","_caps_model","_caps_inputs",
    "_caps_power","monitor_model","monitor_label","_luminance_max","_raw_from_percent",
    "set_brightness_many","get_brightness_many","probe_states","_idle_seconds","_human_gap",
    "PHYSICAL_MONITOR","_DISPLAY_DEVICE"])
assign("hw.ccd", ["_ccd_setup_probe_api","_ccd_query_all_paths","_ccd_rows_from_paths","_ccd_merge_rows_by_anchor",
    "ccd_probe_topology","topology_state_for","_ccd_api","_ccd_query_raw","_ccd_slice_bytes","_ccd_make_snapshot",
    "_ccd_apply","_ccd_restore_from_bytes","_ccd_wait_state","_fold_raw_rows","_ccd_mode_entries","_ccd_pick_mode",
    "_ccd_preferred_size","_ccd_live_size","_ccd_layout_rects","_ccd_layout_by_anchor","_ccd_free_origin",
    "_ccd_place","_ccd_query_raw_rows","_ccd_append_source_mode","_ccd_capture_positions",
    "_ccd_fill_missing_source_modes","_ccd_apply_plan_on",
    "_CcdLuid","_CcdRational","_CcdSourceInfo","_CcdTargetInfo","_CcdPathInfo","_CcdPoint","_CcdSourceMode",
    "_CcdModeInfo","_CcdInfoHeader","_CcdSourceName","_CcdTargetName","_Ccd2D","_CcdVideoSignalInfo",
    "_CcdTargetPreferredMode","_WinRect"])
assign("hw.topology", ["topology_plan_off","topology_plan_on","_topo_off_handover_shift","_topo_off_capture_positions",
    "_topo_off_activate_heir","_topo_off_shift_other_modes","_topo_off_detach","_topo_off_apply_and_verify",
    "_topo_off_report_and_follow","topology_off","topology_inactive_rows","topology_enable_anchor",
    "topology_indices_for_anchors","topology_wanted_anchors","selection_anchors","topology_heir_candidates",
    "_user32","_window_rect","iter_app_windows","win_snapshot","win_follow","remember_layout_snapshot",
    "save_default_layout","load_default_layout","restore_default_layout","resolve_monitors"])
assign("hw.power", ["_broadcast_monitor_power","screen_off_system","screen_on_system","_nudge_user_input",
    "_switch_input_roundtrip","_wake_one","_output_recycle","screen_off_many","screen_wake_many",
    "_monitor_count","_system_ok_targets","screen_off_cfg","screen_on_row","screen_on_cfg","_wake_info",
    "reset_wake_info","take_wake_info","pick_flip_source","_fast_flip_recent","_mark_fast_flip",
    "_fast_flip_one","screen_wake_flip","screen_wake_entry","screen_verdict"])
assign("schedule", ["_minutes","_slot_minutes","active_slot","active_slot_dict","current_level","wake_options",
    "apply_schedule","_ps","_ps_quote","_ps_script","task_target","_task_action","task_signature",
    "_ps_trigger_line","build_register_script","register_tasks","_read_task_xml","_task_definition",
    "_same_path","_slot_trigger_fingerprint","_xml_trigger_fingerprint","_fp_text","task_match_reason",
    "ensure_task_registration","build_unregister_script","unregister_tasks","build_query_script",
    "_xml_flag","_settings_block","_boundary_to_minutes","_boundary_minutes_all","_next_run_text",
    "_days_by_min_from_fp","query_task_status_local","query_task_status","_as_ok_errs",
    "build_runtime_query_script","query_task_runtime","_task_result_text","_runtime_fields",
    "_task_info_next","_rebuild_changes","_rebuild_report","task_summary"])
assign("cli", ["_out","_err","parse_level_arg","parse_index_arg","cli_set","cli_apply_schedule","cli_list",
    "cli_probe","cli_diag","cli_ensure_schedule","cli_selftest","parse_screen_action","cli_screen",
    "_safe_int","_short_dt"])
assign("app", ["status_payload","row_action_blocker","auto_relight_minutes","tray_tooltip_text",
    "_raster_line","_route_wheel","install_wheel_router","acquire_single_instance",
    "_activate_existing_window","main",
    "SettingsSheet","_ScreenActions","_ScheduleActions","_LayoutActions","_SettingsActions","_DiagActions","App"])

# 建立「名字→分组」后，统计每个组引用了哪些其他组的名字
owner = {}
for node in tree.body:
    names = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names = [node.name]
    elif isinstance(node, ast.Assign):
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
    elif isinstance(node, ast.ImportFrom):
        names = [a.name for a in node.names]  # from mb import x → 组内共享
    for n in names:
        if n in GROUPS:
            owner.setdefault(n, GROUPS[n])

defined = set(owner)
edges = {}
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        continue
    src_group = GROUPS.get(node.name)
    if not src_group:
        continue
    used = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id in GROUPS:
            used.add(GROUPS[sub.id])
    used.discard(src_group)
    for g in used:
        key = (src_group, g)
        edges.setdefault(key, set()).add(node.name)

print("候选模块划分的跨模块调用（方向: 调用方 -> 被调方，括号内是调用者函数数）")
print("=" * 72)
for (a, b) in sorted(edges):
    callers = edges[(a, b)]
    print(f"  {a:14} -> {b:14}  ({len(callers)} 个函数引用)  例: {sorted(callers)[:6]}")

# 找环
import itertools
graph = {}
for (a, b) in edges:
    graph.setdefault(a, set()).add(b)
cycles = []
def dfs(node, path, visited):
    for nxt in graph.get(node, ()):
        if nxt == path[0] and len(path) > 1:
            cycles.append(path[:])
        elif nxt not in visited and nxt not in path:
            dfs(nxt, path + [nxt], visited | {nxt})
for start in graph:
    dfs(start, [start], {start})
print()
if cycles:
    print("!! 发现循环依赖:")
    for c in cycles[:10]:
        print("   " + " -> ".join(c))
else:
    print("✅ 无循环依赖，候选分层 acyclic")

# 各组行数汇总
sizes = {}
for node in tree.body:
    g = GROUPS.get(node.name)
    if not g: continue
    a = getattr(node, "lineno", 0); b = getattr(node, "end_lineno", 0)
    sizes[g] = sizes.get(g, 0) + (b - a + 1)
print()
print("候选模块行数（仅函数/类本体，不含 import 和模块级常量）:")
for g, n in sorted(sizes.items(), key=lambda x: -x[1]):
    print(f"  {g:14} {n:6}")
# 模块级常量/赋值/import 的剩余量
rest = 0
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        a = getattr(node, "lineno", 0); b = getattr(node, "end_lineno", 0)
        rest += b - a + 1
print(f"  (模块级常量/imports/注释等) {rest:6}")
