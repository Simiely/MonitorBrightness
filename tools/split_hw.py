# -*- coding: utf-8 -*-
"""R2：把硬件层定义从主文件逐字搬到 hw/ 与 service.py。

一次性 AST 脚本：只删除顶层节点并插入再导出 import；函数体/类体/常量值
逐字写进新模块，不做任何改写。依赖的 import 头在 HEADERS 里显式声明，
搬完用 AST 对比校验函数体一致。
"""
import ast
from pathlib import Path

SRC = Path("monitor_brightness.py")

DDC_FUNCS = [
    "PHYSICAL_MONITOR", "_dxva2", "_read_vcp", "_write_vcp",
    "enumerate_targets", "release_targets", "_select", "_capabilities",
    "_DISPLAY_DEVICE", "_display_uid", "_display_key", "_capabilities_cached",
    "_caps_model", "_caps_inputs", "_caps_power", "monitor_model",
    "monitor_label", "_luminance_max", "_raw_from_percent",
    "set_brightness_many", "get_brightness_many", "probe_states",
    "_idle_seconds", "_human_gap",
]
DDC_CONSTS = [
    "NO_MONITOR_MSG", "VCP_BRIGHTNESS", "VCP_INPUT_SOURCE", "VCP_POWER_MODE",
    "POWER_ON", "POWER_STANDBY", "POWER_SUSPEND", "POWER_SOFT_OFF",
    "POWER_OFF", "POWER_LABEL", "_DXVA2_READY", "_EDID_CACHE", "_MAX_LUM",
]

CCD_STRUCTS = [
    "_CcdLuid", "_CcdRational", "_CcdSourceInfo", "_CcdTargetInfo",
    "_CcdPathInfo", "_CcdPoint", "_CcdSourceMode", "_CcdModeInfo",
    "_CcdInfoHeader", "_CcdSourceName", "_CcdTargetName", "_Ccd2D",
    "_CcdVideoSignalInfo", "_CcdTargetPreferredMode",
]
CCD_CONSTS = [
    "_QDC_ALL_PATHS", "_QDC_VIRTUAL_MODE_AWARE", "_CCD_PATH_ACTIVE",
    "_CCD_GET_SOURCE_NAME", "_CCD_GET_TARGET_NAME",
    "_DC_GET_TARGET_PREFERRED_MODE", "_CCD_PIXELFORMAT_NONGDI",
    "_CCD_EDID_IDS_VALID", "_CCD_TARGET_IN_USE", "_SDC_USE_SUPPLIED",
    "_SDC_APPLY", "_SDC_SAVE_TO_DATABASE", "_SDC_ALLOW_CHANGES",
    "_SDC_VIRTUAL_MODE_AWARE", "_CCD_APPLY_FLAGS", "_CCD_MODE_IDX_INVALID",
    "TOPO_VERIFY_TIMEOUT", "_CCD_TYPE_SOURCE", "_CCD_TYPE_TARGET",
    "_CCD_TYPE_DESKTOP",
]

CCD_FUNCS = [
    "_ccd_setup_probe_api", "_ccd_query_all_paths", "_ccd_rows_from_paths",
    "_ccd_merge_rows_by_anchor", "ccd_probe_topology", "topology_state_for",
    "screen_verdict", "_ccd_api", "_ccd_query_raw", "_ccd_slice_bytes",
    "_ccd_make_snapshot", "_ccd_apply", "_ccd_restore_from_bytes",
    "_ccd_wait_state", "_ccd_mode_entries", "_ccd_pick_mode",
]

TOPOLOGY_FUNCS = [
    "topology_plan_off", "_fold_raw_rows", "topology_plan_on",
    "_ccd_preferred_size", "_ccd_live_size", "_ccd_layout_rects",
    "_ccd_layout_by_anchor", "_ccd_free_origin", "_ccd_place",
    "_ccd_append_source_mode", "_ccd_capture_positions",
    "_ccd_fill_missing_source_modes", "_ccd_apply_plan_on",
    "_topo_off_handover_shift", "_topo_off_capture_positions",
    "_topo_off_activate_heir", "_topo_off_shift_other_modes",
    "_topo_off_detach", "_topo_off_apply_and_verify",
    "_topo_off_report_and_follow", "topology_off", "topology_inactive_rows",
    "topology_indices_for_anchors", "topology_wanted_anchors",
    "_ccd_query_raw_rows", "topology_on",
]

POWER_FUNCS = [
    "_broadcast_monitor_power", "screen_off_system", "screen_on_system",
    "_nudge_user_input", "_switch_input_roundtrip", "_wake_one",
    "_output_recycle", "screen_off_many", "screen_wake_many",
    "_monitor_count", "_system_ok_targets", "_wake_info",
    "reset_wake_info", "take_wake_info", "pick_flip_source",
    "_fast_flip_recent", "_mark_fast_flip", "_fast_flip_one",
    "screen_wake_flip",
]
POWER_CONSTS = [
    "SC_MONITORPOWER", "SC_OFF", "SC_ON", "_WM_SYSCOMMAND", "_HWND_BROADCAST",
    "WAKE_STAGE_LABEL", "FAST_FLIP_WINDOW_S", "_FAST_FLIP_AT",
    "_FAST_FLIP_LOCK", "_WAKE_INFO", "_WAKE_INFO_LOCK",
]

SERVICE_FUNCS = [
    "screen_off_cfg", "screen_on_row", "screen_on_cfg", "screen_wake_entry",
    "topology_enable_anchor", "current_level", "wake_options",
    "selection_anchors", "_minutes", "_slot_minutes", "active_slot",
    "active_slot_dict",
]

WINFOLLOW_FUNCS = [
    "_WinRect", "_user32", "_window_rect", "iter_app_windows",
    "win_snapshot", "win_follow",
]
WINFOLLOW_CONSTS = [
    "_SWP_NOSIZE", "_SWP_NOZORDER", "_SWP_NOACTIVATE", "_GWL_EXSTYLE",
    "_WS_EX_TOOLWINDOW", "_WS_EX_NOACTIVATE",
]

HEADERS = {
    "hw/ddc.py": '''# -*- coding: utf-8 -*-
"""DDC/CI 原语（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

只做枚举/能力串/亮度读写/探测；不碰配置策略、不碰拓扑、不碰 Tk。
"""
import ctypes
import hashlib
import os
import re
from ctypes import wintypes

from core.config import _CAPS_CACHE
from domain.timeutil import clamp_level

''',
    "hw/ccd_structs.py": '''# -*- coding: utf-8 -*-
"""SetDisplayConfig 的 ctypes 结构体与常量（v2.03.0 R2）。

纯声明，零依赖；所有 CCD 相关模块共用这一份定义。
"""
import ctypes
from ctypes import wintypes

''',
    "hw/ccd.py": '''# -*- coding: utf-8 -*-
"""CCD 查询/提交原语（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

只做 SetDisplayConfig 的查询、快照、应用、回读；拓扑策略在上层 hw/topology.py。
"""
import ctypes
import os
import time
from ctypes import wintypes
from datetime import datetime

from core.logging import dlog
from hw.ccd_structs import (
    _CCD_APPLY_FLAGS, _CCD_EDID_IDS_VALID, _CCD_GET_SOURCE_NAME,
    _CCD_GET_TARGET_NAME, _CCD_PATH_ACTIVE, _CCD_TARGET_IN_USE,
    _CcdModeInfo, _CcdPathInfo, _CcdSourceName, _CcdTargetName,
    _QDC_ALL_PATHS, _QDC_VIRTUAL_MODE_AWARE, TOPO_VERIFY_TIMEOUT,
)
from hw.ddc import _human_gap, _idle_seconds, probe_states

''',
    "hw/topology.py": '''# -*- coding: utf-8 -*-
"""显示器拓扑策略（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

断开/接回/主屏交接/位置摆放。状态机时序壳里的长函数按治理条例登记例外。
"""
import ctypes
from ctypes import wintypes

from core.config import _coerce_positions, normalize_indices, save_config
from core.logging import _write_event_log, dlog
from domain.decisions import _short_dev
from hw.ccd import (
    _ccd_api, _ccd_apply, _ccd_make_snapshot, _ccd_mode_entries,
    _ccd_pick_mode, _ccd_query_raw, _ccd_restore_from_bytes,
    _ccd_wait_state, ccd_probe_topology,
)
from hw.ccd_structs import (
    _CCD_APPLY_FLAGS, _CCD_MODE_IDX_INVALID, _CCD_PATH_ACTIVE,
    _CCD_PIXELFORMAT_NONGDI, _CCD_TYPE_DESKTOP, _CCD_TYPE_SOURCE,
    _CCD_TYPE_TARGET, _CcdModeInfo, _CcdTargetPreferredMode,
    _DC_GET_TARGET_PREFERRED_MODE,
)
from hw.ddc import enumerate_targets, release_targets
from hw.winfollow import win_follow, win_snapshot

''',
    "hw/power.py": '''# -*- coding: utf-8 -*-
"""开关屏与点亮原语（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

系统级 BroadcastSystemMessage、DDC 待机、切源/快速翻转等纯动作；
按配置分发的策略在 service.py。
"""
import ctypes
import threading
import time
from ctypes import wintypes

from core.config import normalize_indices
from hw.ddc import (
    NO_MONITOR_MSG, POWER_ON, POWER_STANDBY, VCP_BRIGHTNESS,
    VCP_INPUT_SOURCE, VCP_POWER_MODE, _capabilities_cached, _caps_inputs,
    _caps_power, _display_key, _read_vcp, _select, _write_vcp,
    enumerate_targets, release_targets,
)

''',
    "service.py": '''# -*- coding: utf-8 -*-
"""策略分发层（v2.03.0 R2）：按配置把动作路由到 hw/ 的具体实现。

放在 hw/ 之上、App 之下：hw.power 与 hw.topology 因此互不反向依赖，
环②（topology_enable_anchor ↔ screen_on_cfg）在此消失。
"""
from datetime import datetime

from core.config import load_config, normalize_indices
from core.logging import dlog
from domain.decisions import _short_dev
from domain.timeutil import clamp_level, parse_hhmm
from hw.ccd import ccd_probe_topology
from hw.ddc import enumerate_targets, probe_states, release_targets, set_brightness_many
from hw.power import (
    _system_ok_targets, reset_wake_info, screen_off_many, screen_off_system,
    screen_on_system, screen_wake_flip, screen_wake_many,
)
from hw.topology import (
    topology_indices_for_anchors, topology_off, topology_on,
    topology_wanted_anchors,
)

''',
    "hw/winfollow.py": '''# -*- coding: utf-8 -*-
"""窗口跟随（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

交接主屏后把应用窗口搬回它原本所在的屏；只认 Win32，不碰 Tk。
"""
import ctypes
import os
from ctypes import wintypes

''',
}

GROUPS = {
    "hw/ddc.py": DDC_FUNCS + DDC_CONSTS,
    "hw/ccd_structs.py": CCD_STRUCTS + CCD_CONSTS,
    "hw/ccd.py": CCD_FUNCS,
    "hw/topology.py": TOPOLOGY_FUNCS,
    "hw/power.py": POWER_FUNCS + POWER_CONSTS,
    "service.py": SERVICE_FUNCS,
    "hw/winfollow.py": WINFOLLOW_FUNCS + WINFOLLOW_CONSTS,
}


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

    seen = set()
    ranges = []
    for module, names in GROUPS.items():
        picked = []
        for nm in names:
            node = nodes.get(nm)
            if node is None:
                raise SystemExit("缺节点: %s (%s)" % (nm, module))
            if nm in seen:
                raise SystemExit("重复分组: %s" % nm)
            seen.add(nm)
            start = node.lineno - 1
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            picked.append((node.lineno, "".join(lines[start:node.end_lineno])))
            ranges.append((start, node.end_lineno))
        picked.sort()
        Path(module).write_text(
            HEADERS[module] + "\n\n".join(chunk.rstrip("\n") for _, chunk in picked) + "\n",
            encoding="utf-8", newline="\n")

    for a, b in sorted(ranges, reverse=True):
        del lines[a:b]

    out = ["# v2.03.0 R2：硬件层已沉到 hw/ 与 service.py，统一再导出。\n"]
    for module, names in GROUPS.items():
        mod = module[:-3].replace("/", ".")
        out.append("from %s import (                                # noqa: E402\n" % mod)
        for i in range(0, len(names), 6):
            out.append("    " + ", ".join(names[i:i + 6]) + ",\n")
        out.append(")\n")
    block = "".join(out)

    idx = next(i for i, ln in enumerate(lines)
               if ln.startswith("from core.logging import ("))
    while not lines[idx].rstrip().endswith(")"):
        idx += 1
    lines.insert(idx + 1, block)

    SRC.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("R2：删除 %d 个顶层节点，生成 %d 个模块" % (len(ranges), len(GROUPS)))


if __name__ == "__main__":
    main()
