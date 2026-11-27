# -*- coding: utf-8 -*-
"""R2 只读规划：按候选分组打印跨组引用与外部依赖。"""
import ast
from pathlib import Path

SRC = Path("monitor_brightness.py")
tree = ast.parse(SRC.read_text(encoding="utf-8"))

GROUPS = {
    "ddc": [
        "PHYSICAL_MONITOR", "_dxva2", "_read_vcp", "_write_vcp",
        "enumerate_targets", "release_targets", "_select", "_capabilities",
        "_DISPLAY_DEVICE", "_display_uid", "_display_key",
        "_capabilities_cached", "_caps_model", "_caps_inputs", "_caps_power",
        "monitor_model", "monitor_label", "_luminance_max", "_raw_from_percent",
        "set_brightness_many", "get_brightness_many", "probe_states",
        "_idle_seconds", "_human_gap",
    ],
    "ccd_structs": [
        "_CcdLuid", "_CcdRational", "_CcdSourceInfo", "_CcdTargetInfo",
        "_CcdPathInfo", "_CcdPoint", "_CcdSourceMode", "_CcdModeInfo",
        "_CcdInfoHeader", "_CcdSourceName", "_CcdTargetName", "_Ccd2D",
        "_CcdVideoSignalInfo", "_CcdTargetPreferredMode",
    ],
    "ccd": [
        "_ccd_setup_probe_api", "_ccd_query_all_paths", "_ccd_rows_from_paths",
        "_ccd_merge_rows_by_anchor", "ccd_probe_topology", "topology_state_for",
        "screen_verdict", "_ccd_api", "_ccd_query_raw", "_ccd_slice_bytes",
        "_ccd_make_snapshot", "_ccd_apply", "_ccd_restore_from_bytes",
        "_ccd_wait_state", "_ccd_mode_entries", "_ccd_pick_mode",
    ],
    "topology": [
        "topology_plan_off", "_fold_raw_rows", "topology_plan_on",
        "_ccd_preferred_size", "_ccd_live_size", "_ccd_layout_rects",
        "_ccd_layout_by_anchor", "_ccd_free_origin", "_ccd_place",
        "_ccd_append_source_mode", "_ccd_capture_positions",
        "_ccd_fill_missing_source_modes", "_ccd_apply_plan_on",
        "_topo_off_handover_shift", "_topo_off_capture_positions",
        "_topo_off_activate_heir", "_topo_off_shift_other_modes",
        "_topo_off_detach", "_topo_off_apply_and_verify",
        "_topo_off_report_and_follow", "topology_off",
        "topology_inactive_rows", "topology_indices_for_anchors",
        "topology_wanted_anchors", "_ccd_query_raw_rows", "topology_on",
    ],
    "power": [
        "_broadcast_monitor_power", "screen_off_system", "screen_on_system",
        "_nudge_user_input", "_switch_input_roundtrip", "_wake_one",
        "_output_recycle", "screen_off_many", "screen_wake_many",
        "_monitor_count", "_system_ok_targets", "_wake_info",
        "reset_wake_info", "take_wake_info", "pick_flip_source",
        "_fast_flip_recent", "_mark_fast_flip", "_fast_flip_one",
        "screen_wake_flip",
    ],
    "service": [
        "screen_off_cfg", "screen_on_row", "screen_on_cfg", "screen_wake_entry",
        "topology_enable_anchor",
    ],
    "winfollow": [
        "_WinRect", "_user32", "_window_rect", "iter_app_windows",
        "win_snapshot", "win_follow",
    ],
}

owner = {}
for g, names in GROUPS.items():
    for n in names:
        if n in owner:
            raise SystemExit("重复分组: %s" % n)
        owner[n] = g

top = {}
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
        top[n.name] = n
    elif isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                top[t.id] = n

stdlib = set()
for n in tree.body:
    if isinstance(n, ast.Import):
        stdlib.update(a.asname or a.name.split(".")[0] for a in n.names)
    elif isinstance(n, ast.ImportFrom) and n.module:
        stdlib.add(n.module.split(".")[0])

for g, names in GROUPS.items():
    cross, outside, imports = set(), set(), set()
    for nm in names:
        node = top.get(nm)
        if node is None:
            print("!! 缺节点", g, nm)
            continue
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
            elif f in top and f not in owner:
                outside.add((nm, f))
            elif f in stdlib:
                imports.add(f)
    print("\n=== %s (%d nodes) ===" % (g, len(names)))
    print("跨组:", sorted(cross))
    print("主文件未分组依赖:", sorted(outside))
    print("stdlib:", sorted(imports))
