# -*- coding: utf-8 -*-
"""把 App 的 77 个方法按职责切成 5 个 mixin（v2.03.0 U5-2）。

一次性的重构脚本，跑完可删。方法体一字不改：`self.xxx` 仍指向最终
App 实例，所以零行为变化。
"""
import ast

SRC = "monitor_brightness.py"

GROUPS = {
    "_ScreenActions": [
        "apply_presets_from_settings", "refresh_brightness", "apply_now",
        "apply_preset", "_finish_preset", "_update_hint_brightness",
        "screen_off", "_finish_screen_off", "screen_on", "_cancel_relight_timer",
        "_finish_screen_on", "_auto_relight_minutes", "select_all", "select_none",
        "on_monitor_toggle", "refresh_monitors", "read_current", "probe_screens",
        "_status_payload", "monitor_off", "monitor_on", "_queue_screen_action",
        "_dispatch_pending_screen", "_set_actions_enabled", "_row_action_blocker",
        "_finish_apply", "_finish_apply_level",
    ],
    "_ScheduleActions": [
        "_tray_toggle_schedule", "on_slots_changed", "save_schedule",
        "_save_schedule_inner", "rebuild_schedule_tasks", "_set_schedule_enabled",
        "_rollback_schedule", "cancel_schedule", "schedule_toggled",
        "refresh_task_status", "_apply_task_status", "_start_auto_checks",
        "_finish_auto_checks", "_register_after_edit", "_finish_edit_register",
        "_finish_register", "_finish_unregister", "_announce_rebuilt",
        "_refresh_events",
    ],
    "_LayoutActions": [
        "remember_layout", "_flash_remember_btn", "_restore_remember_btn",
        "ask_restore_default", "_tick_restore", "cancel_restore_default",
        "_do_restore_default", "_restore_selection", "_remember_selection",
        "_ask_heir", "topology_enable", "_finish_topology_enable",
        "_first_run_detect", "is_vdd", "ccd_friendly", "_apply_models",
        "_fetch_models", "_render_states",
    ],
    "_SettingsActions": [
        "apply_setting", "apply_autostart", "apply_off_method", "_stop_tray",
        "_tray_image", "_tray_tooltip_text", "_tray_update_tooltip",
        "_create_tray", "_tray_show_main", "_finish_tray_register", "_tray_quit",
    ],
    "_DiagActions": ["open_log_dir", "export_diag"],
}

DOCS = {
    "_ScreenActions": "屏幕动作：亮度下发、逐台亮灭、探测与状态刷新。",
    "_ScheduleActions": "定时：档位保存、任务重建、启动自检。",
    "_LayoutActions": "布局：拓扑断开/接回、主屏交接、位置记忆、显示器数据。",
    "_SettingsActions": "设置与托盘：设置项写回、开机自启、托盘菜单。",
    "_DiagActions": "诊断：打开日志目录、导出诊断包。",
}

NOTE = ("v2.03.0 U5-2：从 App 里按职责切出。方法体一字未改（`self.xxx`\n"
        "    仍指向最终的 App 实例），因此零行为变化。将来的物理分离只需\n"
        "    把这些类搬到独立文件再 import —— 那时才算真正的模块化。\n"
        "    ")


def main():
    src = open(SRC, encoding="utf-8").read()
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "App")
    meth = {m.name: m for m in app.body if isinstance(m, ast.FunctionDef)}

    # 按原文件顺序收集每组源码（带紧邻注释）
    group_chunks, ranges = {}, []
    for g, names in GROUPS.items():
        picked = []
        for nm in names:
            node = meth.get(nm)
            if node is None:
                raise SystemExit("缺方法: %s" % nm)
            start = node.lineno - 1
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            picked.append((node.lineno, "".join(lines[start:node.end_lineno])))
            ranges.append((start, node.end_lineno))
        picked.sort()
        group_chunks[g] = picked

    # 生成 mixin 文本（用列表拼接，避免格式化转义问题）
    out = []
    for g in ("_ScreenActions", "_ScheduleActions", "_LayoutActions",
              "_SettingsActions", "_DiagActions"):
        out.append("class " + g + ":\n")
        out.append('    """' + DOCS[g] + "\n\n    " + NOTE + '"""\n\n')
        for _, chunk in group_chunks[g]:
            out.append(chunk.rstrip("\n") + "\n\n")
        out.append("\n")
    mixin_text = "".join(out)

    # 删除 App 里的这些方法（从后往前）
    ranges.sort(reverse=True)
    for a, b in ranges:
        del lines[a:b]

    # 改 class App 定义并插入 mixin
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("class App:"))
    lines[idx] = ("class App(_ScreenActions, _ScheduleActions, _LayoutActions,\n"
                  "          _SettingsActions, _DiagActions):\n")
    lines.insert(idx, mixin_text)

    open(SRC, "w", encoding="utf-8", newline="\n").write("".join(lines))
    print("已生成 5 个 mixin，搬移 %d 个方法" % sum(len(v) for v in GROUPS.values()))


if __name__ == "__main__":
    main()
