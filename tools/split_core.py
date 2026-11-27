# -*- coding: utf-8 -*-
"""R1：把 core 层函数从主文件逐字搬到 core/{util,logging,config}.py。

一次性脚本（v2.03.0 R1）。只删除主文件里的顶层函数/赋值节点并插入再导出
import；函数体本身由人工逐字复制到新模块，脚本不改写任何函数体。
"""
import ast
from pathlib import Path

SRC = Path("monitor_brightness.py")

DROP_FUNCS = {
    # → core/config.py
    "coerce_setting", "_coerce_positions", "_coerce_levels", "_coerce_ids",
    "_valid_hhmm", "_to_index", "_split_spec", "_as_iterable",
    "normalize_indices", "_coerce_slots", "default_config", "_load_model_cache",
    "_backup_bad_config", "load_config", "_atomic_write_text", "save_config",
    "old_config_present", "_autostart_path", "get_autostart", "set_autostart",
    # → core/logging.py
    "_write_event_log", "read_session_events", "read_recent_events",
    "_diag_file", "diag_set_enabled", "diag_enabled", "dlog", "diag_dump",
    "diag_export",
}

DROP_ASSIGNS = {
    "APP_TITLE", "APP_VERSION", "DEFAULT_SLOTS", "OFF_METHODS",
    "OFF_METHOD_LABEL", "SETTINGS", "SETTING_BY_KEY", "CONFIG_VERSION",
    "_EVENT_LOCK", "_SESSION_EVENTS", "CONFIG_LOAD_ERROR",
    "_DIAG_LOCK", "_DIAG_ON", "_DIAG_LIMIT", "_CAPS_CACHE",
    "AUTOSTART_RUN_NAME",
}

IMPORT_BLOCK = '''# v2.03.0 R1：配置/日志/工具已沉到 core/ 三层。下面统一再导出，
# 主文件内部调用与测试里的 `mb.xxx` 写法保持不变（patch 目标除外）。
import core.config as _config                       # noqa: E402
from core.config import (                          # noqa: E402
    APP_TITLE, APP_VERSION, AUTOSTART_RUN_NAME, CONFIG_LOAD_ERROR, CONFIG_VERSION,
    DEFAULT_SLOTS, OFF_METHODS, OFF_METHOD_LABEL, SETTINGS, SETTING_BY_KEY,
    _CAPS_CACHE, _as_iterable, _atomic_write_text, _autostart_path,
    _backup_bad_config, _coerce_ids, _coerce_levels, _coerce_positions,
    _coerce_slots, _load_model_cache, _split_spec, _to_index, _valid_hhmm,
    coerce_setting, default_config, get_autostart, load_config,
    normalize_indices, old_config_present, save_config, set_autostart,
)
from core.logging import (                         # noqa: E402
    _DIAG_LIMIT, _DIAG_ON, _EVENT_LOCK, _SESSION_EVENTS, _diag_file,
    _write_event_log, dlog, diag_dump, diag_enabled, diag_export,
    diag_set_enabled, read_recent_events, read_session_events,
)
'''


def main():
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    ranges = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in DROP_FUNCS:
            ranges.append((node.lineno - 1, node.end_lineno))
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id in DROP_ASSIGNS
                   for t in node.targets):
                ranges.append((node.lineno - 1, node.end_lineno))

    seen = set()
    for name in DROP_FUNCS:
        if not any(isinstance(n, ast.FunctionDef) and n.name == name
                   for n in tree.body):
            seen.add(name)
    for name in DROP_ASSIGNS:
        if not any(isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == name
                           for t in n.targets) for n in tree.body):
            seen.add(name)
    if seen:
        raise SystemExit("主文件缺少待搬移名字: %s" % sorted(seen))

    for a, b in sorted(ranges, reverse=True):
        del lines[a:b]

    idx = next(i for i, ln in enumerate(lines)
               if ln.startswith("from core.util import ("))
    while not lines[idx].rstrip().endswith(")"):
        idx += 1
    lines.insert(idx + 1, IMPORT_BLOCK)

    SRC.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("R1：删除 %d 个顶层节点，插入 core 再导出 import" % len(ranges))


if __name__ == "__main__":
    main()
