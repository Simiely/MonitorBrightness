#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示器亮度定时调节 - 深色主题 GUI 工具 (DDC/CI) [tkinter 轻量版]

功能:
  - 深色主题界面（含 Windows 标题栏沉浸式深色）
  - 显示器多选列表：可一次对多台显示器套用同一下发亮度，带「刷新」重新枚举
  - 设置白天/晚上两个时间点与目标亮度，写入 Windows 任务计划程序
  - 定时任务不依赖常驻进程：到点由系统调用本程序的命令行模式执行
  - 支持命令行模式:
      MonitorBrightness.exe --set <序号|all|0,1> <亮度>   立即调整
      MonitorBrightness.exe --apply-schedule              按「当前时间」判定档位并下发
      MonitorBrightness.exe --list                        列出显示器
      MonitorBrightness.exe --probe                       实测每台显示器的亮灭
      MonitorBrightness.exe --diag                        只读诊断报告（排查点不亮）
      MonitorBrightness.exe --diag-log                    打印最近的诊断日志（排障用）
      MonitorBrightness.exe --diag-export                 导出诊断包 zip（日志+配置）
      MonitorBrightness.exe --screen off|on|status        熄灭 / 点亮选中的显示器
      MonitorBrightness.exe --selftest                    自检（打印时间分档判定结果）

v1.1.0 相比 v1.0.0：
  1. 深色主题（ttk clam 自定义配色 + DWM 标题栏深色）
  2. 显示器由单选下拉改为多选列表 + 刷新
  3. 定时修复：改用 PowerShell 的 Register-ScheduledTask 并开启 StartWhenAvailable
     （schtasks.exe 命令行没有这个开关，这是 v1.0.0 定时会漏跑的根本原因）；
     任务动作统一改为 --apply-schedule，由程序按执行时刻自行判定该用哪一档，
     因此「白天/晚上两个任务都被补跑」时结果依然一致（幂等）；
     另加一条登录触发任务，保证开机时间已晚于切换点时也能立刻补上。
"""
import sys

# ── DPI 声明：必须在 import tkinter 之前 ────────────────────────────────
# v2.02.1：本程序原先没有 DPI 感知声明，Windows 会把整个窗口当"低 DPI 老程序"
# 做位图拉伸 —— 在 125%/150% 缩放下字体发虚、边框毛糙（多来源一致结论）。
# 这里用「系统级感知」(1)：单屏/同缩放多屏完全正确，代价是窗口拖到
# 缩放比例不同的屏时需要重启才会重新适配。
# 不用 (2) Per-Monitor：Tk 8.6 不响应 WM_DPICHANGED，跨屏会错乱甚至崩溃，
# 多个来源都明确建议 Tk 程序不要开 2。
try:
    import ctypes as _dpi_ctypes
    try:
        _dpi_ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                    # Win7 没有 shcore
        _dpi_ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import os
import re
import math
import json
import html
import queue
import shutil
import zipfile
import hashlib
import tempfile
import threading
import subprocess
import time
import traceback
import ctypes.wintypes
from ctypes import wintypes
from datetime import datetime, timezone

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# v2.03.0 P3 试点：纯决策函数已搬到 domain/decisions.py（不碰 Tk / Win32）。
# 这里重新导出，主文件内 19 处 `_short_dev` 引用和测试里的 `mb.xxx` 都不用改。
from domain.decisions import (                       # noqa: E402
    _short_dev, ccd_friendly_name, is_vdd_index, plan_model_updates,
    restore_selection, short_dev,
    # v2.03.0 U4：显示名判定随 MonitorPanel 一起下沉
    _GENERIC_DESC, display_name_for_row, is_vdd_row,
)
# v2.03.0 U2 前置：时间/星期工具搬到 domain/timeutil.py。
# 控件（TimeField / WeekdayPicker / _SlotRow）要用它们，控件再搬到 ui/ 时
# 才不至于反向依赖主文件。这里重新导出，主文件内引用一行都不用改。
from domain.timeutil import (                        # noqa: E402
    WEEKDAY_LABELS, WEEKDAY_NAMES_PS, WEEKDAY_PRESETS, clamp_level, days_label,
    normalize_days, normalize_time_input, parse_hhmm, shift_time,
    slot_matches_weekday, slots_for_weekday,
)
# v2.03.0 U5 前置：路径常量搬到 core/paths.py（零依赖层）。
# 用 from ... import 把名字绑到本模块，因此主文件内所有引用、以及测试里
# `mb.CONFIG_FILE = ...` 的覆盖写法都照旧有效（Python 在本模块命名空间查找）。
from core.paths import (                             # noqa: E402
    APP_DIR, CONFIG_DIR, CONFIG_FILE, ERROR_FILE, EVENTS_FILE, LOG_DIR,
    _LEGACY_CONFIG_DIR, _app_dir, _dir_writable, _pick_log_dir,
)
# v2.03.0 R1 前置：纯工具函数搬到 core/util.py。重新导出，主文件与测试里的
# `mb._err` / `mb._short_dt` 等引用照旧有效。
from core.util import (                              # noqa: E402
    _err, _out, _safe_int, _short_dt,
)
# v2.03.0 R1：配置/日志/工具已沉到 core/ 三层。下面统一再导出，
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
# v2.03.0 R2：硬件层已沉到 hw/ 与 service.py，统一再导出。
from hw.ddc import (                                # noqa: E402
    PHYSICAL_MONITOR, _dxva2, _read_vcp, _write_vcp, enumerate_targets, release_targets,
    _select, _capabilities, _DISPLAY_DEVICE, _display_uid, _display_key, _capabilities_cached,
    _caps_model, _caps_inputs, _caps_power, monitor_model, monitor_label, _luminance_max,
    _raw_from_percent, set_brightness_many, get_brightness_many, probe_states, _idle_seconds, _human_gap,
    NO_MONITOR_MSG, VCP_BRIGHTNESS, VCP_INPUT_SOURCE, VCP_POWER_MODE, POWER_ON, POWER_STANDBY,
    POWER_SUSPEND, POWER_SOFT_OFF, POWER_OFF, POWER_LABEL, _DXVA2_READY, _EDID_CACHE,
    _MAX_LUM,
)
from hw.ccd_structs import (                                # noqa: E402
    _CcdLuid, _CcdRational, _CcdSourceInfo, _CcdTargetInfo, _CcdPathInfo, _CcdPoint,
    _CcdSourceMode, _CcdModeInfo, _CcdInfoHeader, _CcdSourceName, _CcdTargetName, _Ccd2D,
    _CcdVideoSignalInfo, _CcdTargetPreferredMode, _QDC_ALL_PATHS, _QDC_VIRTUAL_MODE_AWARE, _CCD_PATH_ACTIVE, _CCD_GET_SOURCE_NAME,
    _CCD_GET_TARGET_NAME, _DC_GET_TARGET_PREFERRED_MODE, _CCD_PIXELFORMAT_NONGDI, _CCD_EDID_IDS_VALID, _CCD_TARGET_IN_USE, _SDC_USE_SUPPLIED,
    _SDC_APPLY, _SDC_SAVE_TO_DATABASE, _SDC_ALLOW_CHANGES, _SDC_VIRTUAL_MODE_AWARE, _CCD_APPLY_FLAGS, _CCD_MODE_IDX_INVALID,
    TOPO_VERIFY_TIMEOUT, _CCD_TYPE_SOURCE, _CCD_TYPE_TARGET, _CCD_TYPE_DESKTOP,
)
from hw.ccd import (                                # noqa: E402
    _ccd_setup_probe_api, _ccd_query_all_paths, _ccd_rows_from_paths, _ccd_merge_rows_by_anchor, ccd_probe_topology, topology_state_for,
    screen_verdict, _ccd_api, _ccd_query_raw, _ccd_slice_bytes, _ccd_make_snapshot, _ccd_apply,
    _ccd_restore_from_bytes, _ccd_wait_state, _ccd_mode_entries, _ccd_pick_mode,
)
from hw.topology import (                                # noqa: E402
    topology_plan_off, _fold_raw_rows, topology_plan_on, _ccd_preferred_size, _ccd_live_size, _ccd_layout_rects,
    _ccd_layout_by_anchor, _ccd_free_origin, _ccd_place, _ccd_append_source_mode, _ccd_capture_positions, _ccd_fill_missing_source_modes,
    _ccd_apply_plan_on, _topo_off_handover_shift, _topo_off_capture_positions, _topo_off_activate_heir, _topo_off_shift_other_modes, _topo_off_detach,
    _topo_off_apply_and_verify, _topo_off_report_and_follow, topology_off, topology_inactive_rows, topology_indices_for_anchors, topology_wanted_anchors,
    _ccd_query_raw_rows, topology_on,
)
from hw.power import (                                # noqa: E402
    _broadcast_monitor_power, screen_off_system, screen_on_system, _nudge_user_input, _switch_input_roundtrip, _wake_one,
    _output_recycle, screen_off_many, screen_wake_many, _monitor_count, _system_ok_targets, _wake_info,
    reset_wake_info, take_wake_info, pick_flip_source, _fast_flip_recent, _mark_fast_flip, _fast_flip_one,
    screen_wake_flip, SC_MONITORPOWER, SC_OFF, SC_ON, _WM_SYSCOMMAND, _HWND_BROADCAST,
    WAKE_STAGE_LABEL, FAST_FLIP_WINDOW_S, _FAST_FLIP_AT, _FAST_FLIP_LOCK, _WAKE_INFO, _WAKE_INFO_LOCK,
)
from service import (                                # noqa: E402
    screen_off_cfg, screen_on_row, screen_on_cfg, screen_wake_entry, topology_enable_anchor,
    current_level, wake_options, selection_anchors, resolve_monitors,
)
from schedule import (                              # noqa: E402
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
from cli import (                                  # noqa: E402
    parse_level_arg, parse_index_arg, cli_set, cli_apply_schedule, cli_list, cli_probe, cli_diag, cli_ensure_schedule, cli_selftest, parse_screen_action, cli_screen, INPUT_LABEL,
)
from domain.slots import (                           # noqa: E402
    _minutes, _slot_minutes, active_slot, active_slot_dict,
)
from hw.winfollow import (                                # noqa: E402
    _WinRect, _user32, _window_rect, iter_app_windows, win_snapshot, win_follow,
    _SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE, _GWL_EXSTYLE, _WS_EX_TOOLWINDOW, _WS_EX_NOACTIVATE,
)

# Pillow 只有 GUI 需要（抗锯齿圆角贴图）。**必须有守卫**：
# 缺 Pillow 时 CLI（--set / --apply-schedule / --diag / --selftest）照常工作，
# GUI 在 main() 里给一句明确报错后退出，不能因为 import 失败把整个程序拖死。
# 外观层（配色 / 字体 / 位图 / ttk 样式）已搬到 ui/theme.py（v2.03.0 U1）。
# Pillow 的守卫在 theme 内部实现，主文件不再直接 import PIL —— 历史上
# 正是"第二句模块级 from PIL import"击穿过守卫，让缺 Pillow 时 CLI 也起不来。
from ui.theme import (              # noqa: E402
    PIL_OK, UI, DARK, FONT_UI, _ui_font, apply_dark_theme, apply_tk_scaling,
    install_dark_tickbox, _aa_rounded, _aa_circle, _hex_rgba,
    _aa_sync_epoch, _tickbox_image, _AA_CACHE, _AA_REFS, _AA_EPOCH,
    _TICKBOX_IMAGES, apply_dark_titlebar,
)
# v2.03.0 U3 前置：屏幕动作枚举下沉 domain（SlotEditor 等控件要用）
from domain.screen_actions import (                  # noqa: E402
    SCREEN_ACTIONS, SCREEN_LABEL_BY_VALUE, SCREEN_VALUE_BY_LABEL,
)
# v2.03.0 U2：12 个自绘控件搬到 ui/widgets.py。重新导出，主文件其余
# 2000+ 行 UI 代码的引用（Label/Button/Switch/...）一行都不用改。
from ui.widgets import (            # noqa: E402
    Button, Card, Label, ScrollArea, SectionTitle, Segmented, Slider, Switch,
    TimeField, Tooltip, WeekdayPicker, _SlotRow,
)
# v2.03.0 U3：弹层搬到 ui/sheets.py；R4-1 起 SettingsSheet 也在该模块，
# 主文件只保留再导出；它继承的 Sheet 从下面这行来。
from ui.sheets import (             # noqa: E402
    DarkBox, HelpSheet, HeirSheet, SettingsSheet, Sheet, SlotEditor,
)
# v2.03.0 U4：4 个面板搬到 ui/panels.py；显示名判定下沉 domain.decisions
from ui.panels import (             # noqa: E402
    BrightnessPanel, MonitorPanel, SchedulePanel, StatusCard,
)
from app.diag import _DiagActions      # noqa: E402
from app.settings import (             # noqa: E402
    TRAY_OK, _SettingsActions, _TrayIcon, _TrayItem, _TrayMenu,
    tray_tooltip_text,
)
from app.common import (             # noqa: E402
    auto_relight_minutes, row_action_blocker, selection_payload, status_payload,
)
from app.brightness import _BrightnessActions   # noqa: E402
from app.screen_power import _ScreenPowerActions  # noqa: E402

# 托盘（可选依赖）已随 _SettingsActions 沉到 app/settings.py；
# 上方 from app.settings import 再导出，主文件旧引用保持不变。

# v1.7.0 起不再需要 monitorcontrol：DDC/CI 直接走 dxva2（见「二、显示器枚举与亮度控制」）。
# 留这个开关只是为了让"缺依赖"的旧提示不再误报——真正的枚举不看它。













def _migrate_old_logs():
    """老版本把日志写在 APPDATA，换到程序目录后把现有的几份搬一次过来。

    v2.01.5 之前日志在 `%APPDATA%\\MonitorBrightness`；现在优先放程序目录，
    这样出问题时整个目录打包发出去就行。搬一次、不覆盖已有的。
    **只在 main() 里调用**：单元测试不该有这个副作用。
    """
    if os.path.abspath(LOG_DIR) == os.path.abspath(CONFIG_DIR):
        return
    for name in ("events.log", "events.log.1", "error.log", "diag.log"):
        src = os.path.join(CONFIG_DIR, name)
        dst = os.path.join(LOG_DIR, name)
        try:
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
        except Exception:
            pass

# v1.9.4：删除启动补执行与 12 小时补检阈值。启动自检只比对任务定义
# （路径/参数/触发点/StartWhenAvailable），绝不按「今天错过了哪一档」执行业务动作，
# 因此本程序启动时不会主动熄屏，也不会主动改亮度。


# 触发时刻前后允许的判定容差（分钟）：任务到点执行时可能早/晚几秒，
# 靠这个窗口保证 09:00 的白天任务不会被判成晚上（反之亦然）。
# (v1.3.0 起为 N 档调度：生效规则=「time<=now 的最后一档」，无需容差常量)




# ============================================================
# 一、配置读写（设置项的唯一声明表在此，其余全部由它派生）
# ============================================================
# 为什么要有这张表：实测这些键在源码里触点 5~9 处（Fowler 的 Shotgun Surgery），
# 加一个设置要同时改 默认值表 / 校验 / 建控件 / 读表单 / 拼注册脚本 五六处。
# 现在只有这里声明一次，其余地方一律派生。
#   kind: indices 显示器序号列表 | hhmm 时间 | level 亮度 0-100 | flag 开关











# 熄灭方式：system   = 系统级整组熄灭（一定唤得回来）
#           ddc      = 逐台 DDC 0xD6=2 待机（本机实测可能唤不回，仅作高级选项）
#           topology = CCD 断开副屏（串流隐藏物理屏用；默认不是它）























# ============================================================================
# 诊断日志（临时模块，v2.01.4 引入）
# ----------------------------------------------------------------------------
# 目的：趁问题还没收敛，把"当时到底读到了什么、算了什么、提交了什么、回来什么"
# 原样落盘，省掉来回猜。**问题收敛后整块删掉**，删除清单只有四条：
#   1) 删掉本节（DIAG_FILE ~ diag_dump，含 _DIAG_* 全局量）；
#   2) 删掉 SETTINGS 里的 "diag_log" 一项（grep diag_log）；
#   3) 删掉界面里两处（设置页的开关行、日志卡片的「打开目录」按钮）；
#   4) `grep -n "dlog(" monitor_brightness.py` 把埋点行整行删掉。
# 为了好删，这里只暴露两个函数、埋点一律写成单行 dlog(...)，不写多行调用。
#
# 格式是 JSON Lines：一行一个 JSON，字段固定 t/pid/th/lv/tag + 自定义键。
# 排查时 `findstr /c:"\"tag\": \"topo.on\"" diag.log` 就能把一次接回的全过程串起来。
# ============================================================================














# ============================================================================
# 诊断日志结束（以上整块可删）
# ============================================================================








# 配置读取失败时的说明（GUI 启动后显式提示；CLI 不受影响）





















































# 能力串缓存：display key -> 能力串。能力串是面板的静态固件属性（实测 3.1s/台），
# 每次熄灭前都重读一遍曾让 DDC 熄灭耗时 6.3s/2 台 —— 缓存后只剩真正的写入
# (~62ms/台)。随配置持久化（load_config 播种 / save_config 写回），
# 换显示器后 EDID 变化 → key 不命中 → 首次实测刷新。



































































































































































































































# _short_dev 已搬到 domain/decisions.py（v2.03.0 P3），顶部 import 重新导出。




def topology_heir_candidates(off_anchors=(), rows=None):
    """能接班当主屏的屏：连着、且不在这次要断开的名单里。

    物理屏排前面（用户看得见），虚拟屏排后面 —— 只有"全部物理屏都要断"
    的时候才会轮到它，那正是串流场景。
    """
    rows = rows if rows is not None else ccd_probe_topology()
    skip = set(off_anchors or ())
    out, seen = [], set()
    for r in rows:
        anchor = r.get("anchor")
        if not r.get("connected") or not anchor or anchor in skip or anchor in seen:
            continue
        seen.add(anchor)
        out.append({"anchor": anchor,
                    "friendly": r.get("friendly") or anchor,
                    "vdd": bool(r.get("vdd")),
                    "active": bool(r.get("active")),
                    "gdi": r.get("gdi") or ""})
    out.sort(key=lambda c: (c["vdd"], not c["active"]))
    return out


def remember_layout_snapshot(cfg):
    """把"当前拓扑"记成用户的显示器布局（v2.01.11）。

    位置记录**不会自动覆盖**：交接主屏会把接班屏挪到桌面原点，那一刻的
    "真实位置"不是用户摆的。这条是唯一的主动更新入口，放主线程同步做
    （一次 CCD 查询 ~50ms，不值得异步）。
    返回 (布局, None) 或 (None, 错误文案)。
    """
    u32 = _ccd_api()
    raw = _ccd_query_raw(u32) if u32 is not None else None
    if raw is None:
        return None, "读不到当前显示器拓扑"
    paths, modes, pc, mc, rows = raw
    layout = _ccd_layout_by_anchor(rows, paths, modes)
    if not layout:
        return None, "当前没有活动的显示器"
    cfg["monitor_positions"] = {a: list(r) for a, r in layout.items()}
    ok, err = save_config(cfg)
    if not ok:
        return None, err or "配置保存失败"
    dlog("app.remember_layout", layout=cfg["monitor_positions"])
    return cfg["monitor_positions"], None


# ---- 默认双屏布局快照（v2.01.12） ----------------------------------------
# 「恢复双屏」按钮要回到的是"用户记住的那一版默认布局"。整份 CCD 字节写回
# 是唯一同时做到「接回物理屏 + 让 VDD 退场 + 摆回原位」的办法（普通 topology_on
# 在 VDD 占着 (0,0) 时会把物理屏挤到桌面最右，参见 17:40 的 diag.log）。
# 快照跟 monitor_positions 一起记：**只在用户主动点「记住布局」时写**，
# 不自动覆盖（交接主屏那一刻的布局不是用户摆的，见 v2.01.11）。
DEFAULT_LAYOUT_FILE = os.path.join(CONFIG_DIR, "default_layout.json")
_SDC_VALIDATE = 0x00000040
# 必须与 _CCD_APPLY_FLAGS 完全同一套旗标，只把 APPLY 换成 VALIDATE，
# 否则会 rc=87 假失败（真机 18:5x 实测：漏掉 SAVE_TO_DATABASE 就拒）。
# 口径与 scripts/topology_handover_probe.py / topology_repro87.py 一致。
_VALIDATE_FLAGS = ((_CCD_APPLY_FLAGS | _SDC_VALIDATE)
                   & ~(_SDC_APPLY | _SDC_SAVE_TO_DATABASE))


def save_default_layout():
    """把当前完整 CCD 布局存成"默认双屏"快照。

    返回 (简述, 错误文案)。简述里带屏数与友好名，给状态卡显示。
    快照里的 bytes 已按 count 截断（_ccd_slice_bytes），不会越界。
    """
    u32 = _ccd_api()
    raw = _ccd_query_raw(u32) if u32 is not None else None
    if raw is None:
        return None, "读不到当前显示器拓扑"
    paths, modes, pc, mc, rows = raw
    visible = [r for r in rows
               if r.get("active") and r.get("connected") and r.get("anchor")]
    # 同一块屏在 QDC_ALL_PATHS 下有多条 path，按锚点去重
    anchors = list(dict.fromkeys(r["anchor"] for r in visible))
    if not anchors:
        return None, "当前没有活动的显示器"
    snap = _ccd_make_snapshot(paths, modes, pc, mc, anchors)
    snap["note"] = "用户记住的默认双屏布局；「恢复双屏」按这份整份写回"
    try:
        _atomic_write_text(DEFAULT_LAYOUT_FILE,
                           json.dumps(snap, ensure_ascii=False, indent=2))
    except Exception as e:
        return None, str(e)
    dlog("app.save_default_layout", anchors=anchors, pc=pc, mc=mc)
    return "%d 块屏（%s）" % (len(anchors), ", ".join(
        r["friendly"] or "显示器" for r in
        {a: next(x for x in visible if x["anchor"] == a) for a in anchors}.values())), None


def load_default_layout():
    """读回默认布局快照；文件不存在/坏掉返回 (None, 原因)。"""
    try:
        with open(DEFAULT_LAYOUT_FILE, "r", encoding="utf-8") as f:
            snap = json.load(f)
        if not all(k in snap for k in ("pathCount", "modeCount",
                                       "pathsHex", "modesHex")):
            return None, "默认布局快照格式不对"
        return snap, None
    except FileNotFoundError:
        return None, "还没记住默认布局"
    except Exception as e:
        return None, "读默认布局失败：%s" % e


def restore_default_layout(validate_only=False, snap=None):
    """把默认双屏布局整份写回。

    先 SDC_VALIDATE 校验提交形态（只读、不碰桌面），rc=0 才真正 APPLY。
    长度对不上/校验失败一律不写。返回 (错误文案 或 None)。

    `validate_only=True` 只做校验，给按钮预检用；`snap` 可注入，便于单测。
    """
    if snap is None:
        snap, err = load_default_layout()
        if err:
            return err
    u32 = _ccd_api()
    if u32 is None:
        return "恢复双屏仅支持 Windows"
    try:
        u32.SetDisplayConfig.argtypes = [
            wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        u32.SetDisplayConfig.restype = wintypes.LONG
    except (AttributeError, TypeError):
        pass                      # 测试假对象没有 ctypes 的 argtypes

    def build():
        paths = (_CcdPathInfo * int(snap["pathCount"]))()
        modes = (_CcdModeInfo * int(snap["modeCount"]))()
        pb = bytes.fromhex(snap["pathsHex"])
        mb_ = bytes.fromhex(snap["modesHex"])
        if len(pb) != ctypes.sizeof(paths) or len(mb_) != ctypes.sizeof(modes):
            return None, None, None, "快照与当前显示器结构不符，拒绝写回"
        if len(pb):
            ctypes.memmove(ctypes.byref(paths), pb, len(pb))
        if len(mb_):
            ctypes.memmove(ctypes.byref(modes), mb_, len(mb_))
        return paths, modes, (snap["pathCount"], snap["modeCount"]), None

    paths, modes, counts, err = build()
    if err:
        dlog("app.restore_default.fail", stage="shape", err=err)
        return err
    pc, mc = counts
    rc = u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes),
                              _VALIDATE_FLAGS)
    dlog("app.restore_default.validate", pc=pc, mc=mc,
         flags="0x%08X" % _VALIDATE_FLAGS, rc=rc)
    if rc != 0:
        return "默认布局已和当前硬件不匹配（校验 rc=%d），请重新「记住布局」" % rc
    if validate_only:
        return None
    # 校验通过 → 用带 SAVE_TO_DATABASE 的正式 flags 提交
    rc = _ccd_apply(u32, paths, modes, pc, mc)
    if rc != 0:
        dlog("app.restore_default.fail", stage="apply", rc=rc)
        return "恢复失败：SetDisplayConfig rc=%d（拓扑未改动）" % rc
    anchors = list(snap.get("targetAnchors") or [])
    # v2.01.19：快照可能是在串流形态（VDD 当主屏）下记的，此时 targetAnchors
    # 含 VDD。_ccd_wait_state 的 allow_vdd=False 会把 VDD 过滤掉，导致
    # wanted 里的 VDD 永远等不到 → 误报"2 秒内没读到确认"（实际已写回）。
    # 所以只拿物理屏锚点做回读验证。
    try:
        vdd_anchors = {r["anchor"] for r in ccd_probe_topology()
                       if r.get("vdd") and r.get("anchor")}
    except Exception:
        vdd_anchors = set()
    phys = [a for a in anchors if a not in vdd_anchors]
    ok = _ccd_wait_state(phys, True) if phys else True
    dlog("app.restore_default.done", anchors=anchors, verified=ok,
         vdd_skipped=sorted(vdd_anchors & set(anchors)))
    return None if ok else "布局已写回，但 2 秒内没读到确认，请「检测」看结果"




























































# 任务计划把每个任务的定义落盘成 XML：<SystemRoot>\System32\Tasks\<任务名>




























































def _raster_line(x0, y0, x1, y1, thick=2):
    """把一条线段栅格化成像素集合（用于画对勾，避免手工列像素）。"""
    pts = set()
    steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * 3))
    for i in range(steps + 1):
        t = i / float(steps)
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        for dx in range(thick):
            for dy in range(thick):
                pts.add((int(round(x)) + dx, int(round(y)) + dy))
    return pts








def _route_wheel(event):
    """把滚轮事件交给鼠标下最近的「可滚动容器」。

    为什么不再让各个容器自己 bind_all：Tk 的 `unbind_all("<MouseWheel>")`
    会清掉**所有**绑定。P-27 那次修复（Sheet 里 bind_all + Destroy 时
    unbind_all）就引入了这个副作用 —— **开一次设置页再关掉，主界面滚轮
    永久失效**（用户实测：滚轮控制不了上下滚动）。

    现在的做法：整个进程只绑一次、永不 unbind；容器在自身上挂
    `_wheel_target` 标记，由本函数沿 master 链往上找。
    容器返回 False 表示"我不需要滚"，继续往上找。
    """
    w = getattr(event, "widget", None)
    while w is not None:
        target = getattr(w, "_wheel_target", None)
        if target is not None and target(event) is not False:
            return "break"
        w = getattr(w, "master", None)
    return None


def install_wheel_router(root):
    """进程级滚轮路由：只装一次（main 里调），谁都不许 unbind_all。"""
    root.bind_all("<MouseWheel>", _route_wheel, add="+")






# ---- 抗锯齿圆角渲染管线 ----------------------------------------------------
# Tk Canvas 在 Windows 上没有抗锯齿（CustomTkinter 官方文档明说只有 macOS 有，
# smooth=True 多边形只能改善曲率、消除不了锯齿）。所以所有圆角一律用 Pillow
# 以 4 倍超采样渲染、LANCZOS 缩小，作为带透明通道的 PhotoImage 贴到 Canvas 上
# —— Tk 8.6+ 的 Canvas 原生支持图片 alpha。已知坑只有两个，都已处理：
# PhotoImage 必须持有引用（_AA_REFS），渲染结果按参数缓存避免重复开销。











# 走查 E2-01：此处曾有第二句模块级 `from PIL import ...`，击穿了文件顶部的
# PIL_OK 守卫——缺 Pillow 时连 CLI（--set / --apply-schedule）都在 import 阶段
# 崩掉。图片符号统一由顶部 try/except 提供，缺依赖时 GUI 在 main() 里报错退出。










































# ============================================================
# 十、界面决策纯函数（v2.03.0 P2：从 App 里抽出的业务判断）
#
# 这些原先都是 App 的方法，但只依赖 cfg / 少量入参，不碰任何控件。
# 抽出来的目的：能单独测（不需要 Tk），且 App 只留一行转发。
# **不改变任何行为**——逻辑逐字搬移。
# ============================================================


















class _ScheduleActions:
    """定时：档位保存、任务重建、启动自检。

    v2.03.0 U5-2：从 App 里按职责切出。方法体一字未改（`self.xxx`
    仍指向最终的 App 实例），因此零行为变化。将来的物理分离只需
    把这些类搬到独立文件再 import —— 那时才算真正的模块化。
    """

    def _tray_toggle_schedule(self):
        if self.sch_panel.schedule_enabled():
            self.cancel_schedule()
        else:
            if not self._set_schedule_enabled(True, persist=True):
                return
            self.status_var.set("正在写入任务计划…")
            self.run_bg(lambda: register_tasks(self.cfg),
                        self._finish_tray_register, busy=True)

    def _refresh_events(self):
        try:
            self.sch_panel.set_events(read_session_events(3))
        except Exception:
            pass

    def _start_auto_checks(self):
        """窗口出来之后才跑：只校验计划任务是否已正确配置（全后台）。

        ⚠️ 本程序启动时不会主动熄屏：这里只读任务 XML / 必要时原地重建任务，
        绝不按「今天错过了哪一档」补执行，也不会改亮度或屏幕状态。
        """
        if self._auto_checked:
            return
        self._auto_checked = True
        if not self.cfg.get("enabled"):
            self._refresh_events()
            return

        def job():
            try:
                return {"task": ensure_task_registration(self.cfg)}
            except Exception as e:
                return {"task": (False, "任务自检失败：%s: %s" % (type(e).__name__, e))}

        self.run_bg(job, self._finish_auto_checks)

    def _finish_auto_checks(self, result):
        if isinstance(result, Exception):
            self.status_var.set("启动自检失败：%s" % result)
            self._refresh_events()
            return
        _task_ok, task_msg = result.get("task", (True, ""))
        # 启动自愈成功时不弹「确认」窗：每次启动都可能发生（换 exe 路径后尤其），
        # 开机即弹会糊脸；反馈走状态卡 + 事件日志（task_msg 已含档位数/下次运行）。
        if task_msg:
            self.status_var.set(task_msg)
        else:
            # 配置正确：只报「已就绪 + 下次运行」，不做任何业务动作
            _slots_now = self.cfg.get("slots") or []
            nxt = _next_run_text([parse_hhmm(s.get("time"), (0, 0))
                                  for s in _slots_now],
                                 days_by_min=_days_by_min_from_fp(
                                     _slot_trigger_fingerprint(_slots_now)))
            self.status_var.set(("定时任务已就绪，下次运行 %s" % nxt) if nxt
                                else "定时任务已就绪")
        self._refresh_events()
        if task_msg:
            self.refresh_task_status()

    # ---------- 定时 ----------
    def on_slots_changed(self, slots):
        """面板里增删改时间段后的统一出口：立即落盘；已启用则重写任务。"""
        self.cfg["slots"] = [dict(s) for s in slots]
        ok, msg = save_config(self.cfg)
        if not ok:
            self.msg.showerror("保存时间段失败", msg)
            return
        if not self.cfg.get("enabled"):
            self.status_var.set("时间段已保存（开启每日定时后生效）")
            return
        self.status_var.set("时间段已保存，正在重写任务计划…")
        if self._schedule_reg_timer:
            try:
                self.root.after_cancel(self._schedule_reg_timer)
            except Exception:
                pass
        # 500ms 防抖：连续改几档只重建一次任务（每次 PowerShell 约 2~3 秒）
        self._schedule_reg_timer = self.root.after(500, self._register_after_edit)

    def _register_after_edit(self):
        self._schedule_reg_timer = None
        self.run_bg(lambda: register_tasks(self.cfg),
                    self._finish_edit_register, busy=True)

    def _finish_edit_register(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            # 走查 P-04：与面板/托盘失败统一回滚策略（原来只提示不回滚）
            self._rollback_schedule(False, "重写任务计划失败", out)
            return
        _write_event_log("时间段已更新，任务计划已重写")
        self.refresh_task_status()
        self.status_var.set("定时已更新")
        self._refresh_events()

    def save_schedule(self):
        try:
            self._save_schedule_inner()
        except Exception as e:
            self.msg.showerror("保存定时出错", "%s: %s" % (type(e).__name__, e))
            self.status_var.set("保存定时出错")

    def _save_schedule_inner(self):
        if not self.sch_panel.schedule_enabled():
            self.msg.showinfo("提示", "请先勾选「启用每日定时」")
            return
        slots = self.sch_panel.read_slots()
        if slots is None:
            return
        idx, ids = selection_payload(self.selected_indices())
        part = {"monitors": idx, "monitor_ids": ids,
                "monitor_anchors": selection_anchors(idx),
                "catch_up_on_logon": bool(self.cfg.get("catch_up_on_logon", True)),
                "slots": slots}
        self.cfg.update(part)
        if not self._set_schedule_enabled(True, persist=True):
            return
        self.status_var.set("正在写入任务计划…")
        before_xml = _read_task_xml()          # 留一份重建前定义，用于比对变化
        self.run_bg(lambda: register_tasks(self.cfg),
                    lambda r: self._finish_register(r, part, before_xml), busy=True)

    def rebuild_schedule_tasks(self):
        """「应用」按钮：按当前面板内容强制重建任务计划（成功弹确认窗）。

        走查 P-05：原名 apply_schedule 与模块级同名函数（按当前时间套档位）
        混淆——按钮点的是"重建任务"，CLI --apply-schedule 跑的是"套档位"。
        """
        if not self.sch_panel.schedule_enabled():
            self.msg.showinfo("提示", "请先勾选「启用每日定时」")
            return
        slots = self.sch_panel.read_slots()
        if slots is None:
            return
        part = {"slots": slots, "enabled": True,
                "catch_up_on_logon": bool(self.cfg.get("catch_up_on_logon", True))}
        self.cfg["slots"] = [dict(s) for s in slots]
        ok, msg = save_config(self.cfg)
        if not ok:
            self.msg.showerror("保存失败", msg)
            return
        self.status_var.set("正在重建任务计划…")
        before_xml = _read_task_xml()
        self.run_bg(lambda: register_tasks(self.cfg),
                    lambda r: self._finish_apply(r, part, before_xml), busy=True)

    def _announce_rebuilt(self, part, out, before_xml, title):
        """重建成功后的「确认」弹窗（失败路径绝不调用这里）。

        run_bg 的完成回调本来就由 _poll_read_queue 在 Tk 线程执行；再 after(0)
        一次是为了让弹窗与回调栈彻底分开，也满足"后台线程结果必须回 Tk 线程"。
        """
        message = _rebuild_report(part, out, before_xml)
        self.root.after(0, lambda: self.msg.shownotice(title, message))

    def _finish_register(self, result, part, before_xml=""):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            # 开关是"切换即注册"：注册失败统一回滚（改状态 + 落盘 + 界面）
            self._rollback_schedule(False, "注册任务失败", out)
            return
        self.refresh_task_status()
        self.status_var.set("定时已启用")
        self._announce_rebuilt(part, out, before_xml, "定时已保存")

    def _set_schedule_enabled(self, enabled, persist=True):
        """定时开关状态的**唯一**写入者：改内存 + 落盘 + 同步界面开关。

        走查 P-01/P-02/P-04：以前 6 个入口各自写 cfg["enabled"]，
        有的落盘有的不落盘，失败回滚策略四种不同。所有入口统一走这里。
        返回 (是否成功)。落盘失败时回滚内存，不让界面说谎。
        """
        want = bool(enabled)
        old = bool(self.cfg.get("enabled"))
        self.cfg["enabled"] = want
        if persist:
            ok, msg = save_config(self.cfg)
            if not ok:
                self.cfg["enabled"] = old
                self.msg.showerror("保存失败", msg)
                return False
        self.sch_panel.set_enabled_flag(want)
        return True

    def _rollback_schedule(self, to_enabled, title, out):
        """任务写入失败时的**唯一**回滚者：改状态 + 落盘 + 提示。"""
        self._set_schedule_enabled(to_enabled, persist=True)
        self.msg.showerror(title, out or "未知错误")
        self.status_var.set("%s（已恢复开关）" % title)

    def cancel_schedule(self):
        self._set_schedule_enabled(False, persist=True)
        self.status_var.set("正在取消定时任务…")
        self.run_bg(unregister_tasks, self._finish_unregister, busy=True)

    def schedule_toggled(self, on):
        """面板上的「启用每日定时」开关：开 = 注册，关 = 注销。"""
        if on:
            self.save_schedule()
        else:
            self.cancel_schedule()

    def _finish_unregister(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            # 走查 P-02：注销失败必须把开关恢复为 True 并落盘，
            # 否则磁盘说"已关闭"但任务残留、继续熄屏（cli_apply_schedule
            # 现在也会因 enabled=false 拒绝执行，双保险）。
            self._rollback_schedule(True, "取消定时任务失败", out)
            self.refresh_task_status()
            return
        self.refresh_task_status()
        self.status_var.set("已取消定时任务")

    def refresh_task_status(self):
        if self._task_querying:
            return
        self._task_querying = True
        self.sch_panel.set_task_status({"state": "loading", "title": "任务计划：读取中…"})

        def job():
            try:
                return (query_task_status(), query_task_runtime())
            except Exception:
                return ("", "")

        def done(result):
            self._task_querying = False
            if isinstance(result, Exception):
                result = ("", "")
            text, runtime = result
            self._apply_task_status(text, runtime)

        self.run_bg(job, done)

    def _apply_task_status(self, text, runtime=""):
        info = task_summary(text, self.cfg.get("slots", []), runtime)
        self.sch_panel.set_task_status(info)
        self.scroll.refresh()
        self._tray_update_tooltip()


    # 兼容旧名（托盘/tray 里调用）
    _refresh_task_status = refresh_task_status


class _LayoutActions:
    """布局：拓扑断开/接回、主屏交接、位置记忆、显示器数据。

    v2.03.0 U5-2：从 App 里按职责切出。方法体一字未改（`self.xxx`
    仍指向最终的 App 实例），因此零行为变化。将来的物理分离只需
    把这些类搬到独立文件再 import —— 那时才算真正的模块化。
    """

    def remember_layout(self):
        """把现在的显示器摆法记成"用户的布局"，断开/接回都按这份来。"""
        layout, err = remember_layout_snapshot(self.cfg)
        if err:
            self.status_var.set("记不住布局：%s" % err)
            self.msg.showerror("记住布局失败", err)
            return
        dlog("app.action", what="remember_layout", count=len(layout))
        # v2.01.12：位置与完整 CCD 快照一起记，供「恢复双屏」整份写回
        desc, snap_err = save_default_layout()
        if snap_err:
            self.status_var.set("位置已记住，但默认布局快照没存上：%s" % snap_err)
            self._flash_remember_btn("快照失败", err=True)
            return
        self.status_var.set("已记住 %d 块屏的位置和布局（%s）" % (len(layout), desc))
        self._flash_remember_btn("已记住 ✓")

    def _flash_remember_btn(self, text, err=False):
        """在「记住」按钮上短暂显示结果（v2.01.25）。

        设置弹层是模态的，会挡住主窗口状态卡；不给就地回执，用户就以为没反应。
        2 秒后复原；弹层先被关掉时 widget 已销毁，静默跳过。
        """
        btn = getattr(self, "_remember_btn", None)
        if btn is None:
            return
        try:
            btn.set_text(text)
            if err:
                btn.after(2600, lambda: self._restore_remember_btn(btn))
            else:
                btn.after(1800, lambda: self._restore_remember_btn(btn))
        except Exception:
            pass

    def _restore_remember_btn(self, btn):
        try:
            if btn.winfo_exists():
                btn.set_text("记住")
        except Exception:
            pass

    # ---- 恢复双屏（倒计时） ------------------------------------------------
    def ask_restore_default(self):
        """点「恢复双屏」：先校验默认布局还在且与硬件匹配，再启动倒计时。"""
        if getattr(self, "_restore_after", None) is not None:
            return
        err = restore_default_layout(validate_only=True)
        if err:
            dlog("app.action", what="restore_default", stage="precheck", err=err)
            self.status_var.set(err)
            self.msg.showwarning("恢复双屏不可用", err)
            return
        secs = max(0, min(120, int(self.cfg.get("restore_delay", 10))))
        dlog("app.action", what="restore_default", stage="countdown", seconds=secs)
        self._restore_deadline = time.monotonic() + secs
        self._tick_restore()

    def _tick_restore(self):
        """每秒刷一次剩余秒数；到点执行恢复。"""
        left = int(self._restore_deadline - time.monotonic() + 0.999)
        if left > 0:
            self.status_card.set_restore_countdown(left)
            self._restore_after = self.root.after(200, self._tick_restore)
        else:
            self._restore_after = None
            self.status_card.set_restore_countdown(None)
            self._do_restore_default()

    def cancel_restore_default(self):
        if getattr(self, "_restore_after", None) is not None:
            self.root.after_cancel(self._restore_after)
            self._restore_after = None
        self.status_card.set_restore_countdown(None)
        dlog("app.action", what="restore_default", stage="cancel")
        self.status_var.set("已取消恢复双屏")

    def _do_restore_default(self):
        """倒计时到点：整份写回默认双屏布局（后台线程，不卡 UI）。"""
        # v2.01.20（走查 P-22）：到点时如果正有屏幕动作在跑（用户刚点了
        # 熄灭/点亮），两边都改拓扑会互相覆盖，_busy 的归属也会错乱
        # （先完成的那个把按钮启用了，另一个还在跑）。直接取消并说明。
        if self._busy:
            self.status_var.set("有屏幕操作正在进行，恢复双屏已取消，请稍后重试")
            dlog("app.action", what="restore_default", stage="cancel_busy",
                 busy_action=self._busy_action)
            return

        def job():
            return restore_default_layout()

        def done(err):
            if err:
                self.status_var.set(err)
                self.msg.showerror("恢复双屏失败", err)
            else:
                dlog("app.action", what="restore_default", stage="done")
                self.status_var.set("已恢复默认双屏布局")
                self.refresh_monitors(silent=True)
        self.run_bg(job, done, busy=True)

    def _restore_selection(self, prev):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return restore_selection(prev, len(self.monitors))

    def _first_run_detect(self):
        """首次启动补一次全量检测，并补写显示器锚点（v2.01.17，v2.01.19 修）。

        v2.01.19 修：原实现无条件 `list(range(len(self.monitors)))`，把老配置
        里"用户只勾了一块屏"的选择**静默改成全选**。现在按配置解析：
        `resolve_monitors()` 返回 [] 才表示"配置里确实没有选择信息"= 全选；
        有选择就按原选择补锚点，不动物理勾选语义。
        """
        if not self.monitors:
            return
        # 全量检测一次（启动链路里已有一次 probe_screens，这里再补一次是
        # 为了 after_idle 时窗口已可见、显示器句柄都稳定了，读数更可靠）
        self.probe_screens()
        idx = resolve_monitors(self.cfg) or list(range(len(self.monitors)))
        anchors = selection_anchors(idx)
        if not anchors:
            dlog("app.first_run_detect", err="拿不到锚点", idx=idx)
            return
        # v2.01.24（走查 R-02 转正）：只写 anchors，**不动 monitors/monitor_ids**。
        # 用户配的 `monitors: []` 是"全部"语义；写成 [0,1] 会把"全部"固化成
        # "这两块"，以后插新屏不会自动纳入。anchors 只用来让定时任务认得住屏，
        # 不承担"范围"语义。
        self.cfg["monitor_anchors"] = anchors
        ok, err = save_config(self.cfg)
        dlog("app.first_run_detect", idx=idx, anchors=anchors, saved=ok, err=err)
        if ok:
            self.status_var.set("已自动识别 %d 块显示器" % len(idx))

    def is_vdd(self, index):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return is_vdd_index(index, self.monitors,
                            getattr(self, "_vdd_devs", ()))

    def ccd_friendly(self, index):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return ccd_friendly_name(index, self.monitors,
                                 getattr(self, "_ccd_names", {}))

    def _apply_models(self, models):
        """把读到的型号刷到界面上（判断由 plan_model_updates 负责）。"""
        changed = False
        for i, name in plan_model_updates(models, self.mon_models,
                                          len(self.monitors)):
            self.mon_models[i] = name
            self.mon_panel.set_row_text(i, name)
            changed = True
        if changed:
            self.cfg["models"] = {str(k): v for k, v in self.mon_models.items() if v}
            save_config(self.cfg)

    def _fetch_models(self):
        """显示器型号缓存没有的补一次（写入行标题与配置）。"""
        targets = self.selected_indices() or list(range(len(self.monitors)))
        # VDD 不读型号：它没有真实 EDID，按 index 缓存下来的名字会串到
        # 物理屏行（或反过来，VDD 当主屏时套用物理屏名）—— v2.01.12 修
        missing = [i for i in targets
                   if i not in self.mon_models and 0 <= i < len(self.monitors)
                   and not self.is_vdd(i)]
        if not missing:
            return

        def job_models():
            out = {}
            targets_now = enumerate_targets()
            lib = _dxva2()
            used = [t for t in targets_now if t["index"] in missing]

            def one(t):
                try:
                    out[t["index"]] = monitor_model(t["handle"], _display_key(t))
                finally:
                    # 同 screen_wake_many：句柄由线程自己销毁，避免 join 超时后 UAF
                    try:
                        lib.DestroyPhysicalMonitor(t["handle"])
                    except Exception:
                        pass

            threads = [threading.Thread(target=one, args=(t,), daemon=True) for t in used]
            for th in threads:
                th.start()
            for th in threads:
                th.join(20)
            release_targets([t for t in targets_now if all(t is not u for u in used)])
            return {i: v for i, v in out.items() if v}

        self.run_bg(job_models, self._apply_models)

    def _render_states(self, result):
        self._probing = False
        self._brighting = False
        if isinstance(result, Exception):
            self.status_var.set("检测失败：%s" % result)
            return
        states, bright = result
        self._screen_states = {s["index"]: s["state"] for s in states
                               if isinstance(s, dict) and "index" in s and "state" in s}
        # 启动早期可能还没建面板；建了才让它按最新亮灭重画行内按钮
        panel = getattr(self, "mon_panel", None)
        if panel is not None:
            panel.refresh_buttons()
        if not states:
            # 轻量刷新（只读亮度）：不动亮灭结论，只更新提示行
            if bright and self._last_verdict:
                self._update_hint_brightness(bright)
            return
        v = screen_verdict(self.cfg, states)
        # v2.01.0：这里以前会在"被键鼠唤醒"时把状态回写落盘。程序不再记账，
        # 结论就是当次探测的结果，不需要也不应该往配置里写运行态。
        if v["state"] != "off":
            vals = [bright[i] for i in sorted(bright) if bright.get(i) is not None]
            if vals and len(set(vals)) == 1:
                hint = "当前 %d%% · %d 台显示器" % (vals[0], len(states))
            elif vals:
                hint = "当前 %s%% · %d 台显示器" % (
                    "/".join(str(x) for x in vals), len(states))
            else:
                hint = "%d 台显示器 · 亮度可调" % len(states)
        else:
            hint = "键鼠输入不会点亮它；点「点亮屏幕」恢复"
        # 状态未知时，把"为什么不知道"直接摆在提示行——否则用户点一次「检测」
        # 只看到一片空白，不知道程序到底读了什么。
        if v["state"] == "unknown" and v.get("evidence"):
            hint = hint + " · " + v["evidence"]
        self.status_card.render(self._status_payload(v["state"], hint))
        self._last_verdict = v
        self._fit_window()

    def _remember_selection(self):
        idx, ids = selection_payload(self.selected_indices())
        self.cfg["monitors"] = idx
        self.cfg["monitor_ids"] = ids
        self.cfg["monitor_anchors"] = selection_anchors(idx)

    def _ask_heir(self, index, off_anchors):
        """弹窗让用户挑一块屏接任主显示器。"""
        cands = topology_heir_candidates(off_anchors)
        # 诊断（v2.01.13）：候选数用户报过"该有 2 个却只显示 1 个"。
        # 这里把候选来源与每块屏的状态落日志，下次出现时一眼能看出
        # 是 targetAvailable=0 还是查询失败——只记录，不改任何行为。
        dlog("app.heir_prompt", off=list(off_anchors), count=len(cands),
             candidates=[{"anchor": c["anchor"], "vdd": c["vdd"],
                          "active": c["active"]} for c in cands])
        if not cands:
            self.msg.showwarning(
                "没有屏可以接任主显示器",
                "断开 %d 之后桌面上一块屏都不剩，Windows 不允许这样做（会黑屏）。\n\n"
                "先把另一块显示器的线接上，或让虚拟显示器（串流）先用起来，再试。"
                % index)
            return
        name = "显示器 %d" % index
        if index < len(self.mon_models) and self.mon_models.get(index):
            name = self.mon_models[index]

        def picked(anchor):
            self._busy_action = "off"
            self._row_action = True
            self.status_var.set("正在把主显示器交给另一块，然后断开 %s…" % name)
            self.run_bg(lambda: topology_off([index], self.cfg, heir=anchor),
                        self._finish_screen_off, busy=True)

        try:
            self._sheet = HeirSheet(self, cands, name, picked)
        except Exception as e:
            self.msg.showerror("打开选择框失败", "%s: %s" % (type(e).__name__, e))

    def topology_enable(self, anchor):
        """GUI「显示」按钮（断开的屏那一行）：后台接回指定锚点的屏（不碰 Tk）。"""
        dlog("app.action", what="topology_enable", anchor=anchor)
        if self._busy:
            self._queue_screen_action("on")
            return
        self._busy_action = "on"
        self.status_var.set("正在启用显示器…")

        def job():
            return topology_enable_anchor(self.cfg, anchor)

        self.run_bg(job, self._finish_topology_enable, busy=True)

    def _finish_topology_enable(self, result):
        ok, errs = _as_ok_errs(result)
        if ok:
            save_config(self.cfg)
            self.status_var.set("显示器已启用")
            self.status_card.render(self._status_payload("on", "亮度与定时均正常"))
            self.refresh_monitors(silent=True)
        for i, e in errs:
            self.status_var.set("启用失败：" + (e if i < 0 else "显示器 %d: %s" % (i, e)))
        if errs:
            self.msg.showwarning("启用失败", "\n".join(
                (e if i < 0 else "显示器 %d: %s" % (i, e)) for i, e in errs))
        self.probe_screens()






class App(_BrightnessActions, _ScreenPowerActions, _ScheduleActions,
          _LayoutActions,
          _SettingsActions, _DiagActions):
    """协调者：服务调用、异步编排、弹窗；界面细节都在上面的控件里。"""

    def __init__(self, root, start_minimized=False):
        """装配窗口。四段拆开是为了让每段能单独看懂（v2.03.0 U5）：
        窗口设置 → 状态初始化 → UI 装配 → 启动序列，顺序不能变。"""
        self.root = root
        self.tray = None
        self._init_window()
        self._init_state()
        self._build_ui()
        self._start(start_minimized)

    def _init_window(self):
        """窗口标题 / 尺寸策略 / 深色标题栏。"""
        self.root.title("%s v%s" % (APP_TITLE, APP_VERSION))
        self.root.resizable(False, False)
        apply_dark_theme(self.root)


    def _init_state(self):
        """配置、显示器数据、各种运行标志、状态变量。"""
        self.cfg = load_config()
        # R1：CONFIG_LOAD_ERROR 是 core.config 的模块级状态，必须读定义处的
        # 当前值；主文件里再导出的同名字符串只是 import 时的快照。
        self._config_error = _config.CONFIG_LOAD_ERROR
        self.monitors = []
        self.mon_info = []
        self.topo_inactive = []           # 拓扑模式下断开的物理屏（GUI 灰显行）
        self._row_action = False          # 本次动作来自行内逐台按钮（不改勾选）
        self._screen_states = {}          # 逐台亮灭（只有 DDC 模式需要，决定行内按钮）
        # 走查 E2-03：models 键被 _load_model_cache 统一 str() 存盘，但一个
        # 手工编辑/旧版残留的非数字键会让 int(k) 抛 ValueError，windowed 下
        # stderr=None → 用户只看到"程序打不开"。跳过坏键，不拖垮 GUI。
        self.mon_models = {}
        for k, v in self.cfg.get("models", {}).items():
            try:
                self.mon_models[int(k)] = v
            except (TypeError, ValueError):
                continue
        self._read_queue = queue.Queue()
        self._bg_tasks = 0
        self._busy = False
        self._busy_action = None      # 当前忙任务对应的屏幕动作（"on"/"off"/None）
        self._pending_screen = None   # 忙时最后一次点击，结束后补执行一次
        self._task_querying = False
        self._relight_pending = False
        self._relight_timer = None       # 走查 E1-05：自动复亮 timer 句柄（可取消）
        # 启动时**不猜**屏幕状态：unknown 直到第一次「检测」拿到真实读数。
        # v2.01.0：以前这里从配置里读 screen_state，等于把上次的记账当成当前事实。
        self._last_verdict = {"state": "unknown", "detail": ""}
        self.status_var = tk.StringVar(value="就绪")
        self.msg = DarkBox(self.root)
        # windowed 打包后 stderr 为 None，Tk 回调异常会彻底静默；
        # 落到 error.log + 状态栏，避免再出现「点了没反应」式黑洞。
        self.root.report_callback_exception = self._report_tk_error
        self._sheet = None
        self._editor = None
        self._probing = False
        self._brighting = False
        self._fit_pending = False
        self._fitted_h = -1
        self._schedule_reg_timer = None
        self._restore_after = None       # 恢复双屏倒计时 timer 句柄（可取消）
        self._restore_deadline = 0.0
        self._auto_checked = False


    def _build_ui(self):
        """滚动区、顶部入口、三个面板、状态回显与关窗钩子。"""
        self.scroll = ScrollArea(self.root, UI["bg"], on_fit=self._fit_window)
        self.scroll.pack(side="top", fill="both", expand=True)
        self.body = self.scroll.body

        # 顶部：右侧两个入口（标题由系统标题栏承担，避免重复）
        top = tk.Frame(self.body, bg=UI["bg"])
        top.pack(fill="x", padx=14, pady=(8, 0))
        Button(top, "设置", self.open_settings, kind="ghost", width=52,
               height=24, pad_bg=UI["bg"], font_size=8).pack(side="right")
        Button(top, "说明", self.open_help, kind="ghost", width=52,
               height=24, pad_bg=UI["bg"], font_size=8).pack(side="right", padx=(0, 6))

        # 显示器选择在最上面，屏幕控制（状态卡）移到其下（用户要求）
        self.mon_panel = MonitorPanel(self)
        self.status_card = StatusCard(self)
        self.status_card.pack(fill="x", padx=14, pady=(8, 0))
        self.bri_panel = BrightnessPanel(self, current_level(self.cfg),
                                         self.cfg.get("presets"))
        self.sch_panel = SchedulePanel(self, self.cfg)
        # 底部留白：最后一张卡（定时）的下缘不贴窗口下边（用户要求 10px）
        tk.Frame(self.body, bg=UI["bg"], height=10).pack(fill="x")

        # 没有底部状态栏：status_var.set() 的消息统一走状态卡的临时反馈行
        # （闪 3 秒回到常驻提示）。trace 必须在状态卡建好之后再挂。
        self.status_var.trace_add(
            "write", lambda *_: self.status_card.flash(self.status_var.get()))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)


    def _start(self, start_minimized):
        """启动序列：枚举 → 任务状态 → 探测 → 托盘 → 延迟自检。"""
        self.refresh_monitors(silent=True)
        self.refresh_task_status()
        self.probe_screens()
        if self.cfg.get("tray_enabled", True):
            self._create_tray()
        if start_minimized and self.tray is not None:
            self.root.withdraw()

        # 配置读坏的显式提示（不能静默吞掉：用户的设置还在坏文件里）
        self.root.after(150, self._warn_config_error)
        # 首次启动：自动把检测到的显示器锚点写进配置（v2.01.17）。
        # 原因：锚点只在用户手动勾选时才存，首次打开配置里 anchors 是空的，
        # 定时/熄灭逻辑只能靠纯 index 兜底，换屏/重排后就会认错屏。
        # 用 after_idle（Tk 官方推荐的"窗口显示后再初始化"做法，Bryan Oakley）：
        # 等窗口可见、显示器列表建好再做，不阻塞首屏绘制。
        # 只在"从未保存过锚点"时写一次，不覆盖用户后续的手动选择。
        if not (self.cfg.get("monitor_anchors") or []):
            self.root.after_idle(self._first_run_detect)

        # 启动后台自检：只校验任务定义与当前进程是否一致（不一致则原地重建）。
        # 不补执行、不改亮度、不熄屏；900ms 是给首屏探测让路，全程后台线程。
        self.root.after(900, self._start_auto_checks)

        # v2.01.20：发现旧 APPDATA 配置时提示一句（不自动迁移）。
        # 延迟到首屏探测之后，避免被启动期的其它状态文案盖掉。
        old_cfg = old_config_present()
        if old_cfg:
            _write_event_log("检测到旧版配置（未自动迁移）：%s" % old_cfg)
            self.root.after(1400, lambda p=old_cfg: self.status_var.set(
                "旧版配置在 %s；要沿用就手动复制 config.json 到程序目录" % p))

    def _fit_window(self):
        """内容晚到（任务状态行/显示器行）后按请求尺寸重排窗口。

        窗口 resizable(False) 时 WM 不会自动跟随内容增长，晚到的行会被
        底边裁掉（用户实测）。两个护栏：
        1) 防抖：合并 80ms 内的多次触发（探测/任务状态/渲染常常连环触发）；
        2) 高度没变就跳过 geometry("")——整窗重排不便宜，且会闪。
        超高部分仍由 ScrollArea 的封顶 + 滚动条兜底。
        """
        if self._fit_pending:
            return
        self._fit_pending = True
        self.root.after(80, self._fit_window_now)

    def _fit_window_now(self):
        self._fit_pending = False
        try:
            self.root.update_idletasks()
            need = self.scroll.body.winfo_reqheight()
            if need == self._fitted_h:
                return
            self._fitted_h = need
            self.root.geometry("")
        except Exception:
            pass

    # ---------- 顶部入口 ----------
    def open_settings(self):
        try:
            # 必须留一个引用：Toplevel 被 GC 时窗口会跟着消失（实测踩过）
            self._sheet = SettingsSheet(self)
        except Exception as e:
            self.msg.showerror("打开设置失败", "%s: %s" % (type(e).__name__, e))

    def open_help(self):
        try:
            self._sheet = HelpSheet(self)
        except Exception as e:
            self.msg.showerror("打开说明失败", "%s: %s" % (type(e).__name__, e))
















    def selected_indices(self):
        return self.mon_panel.selected()








    # ---------- 统一异步入口 ----------
    def run_bg(self, job, on_done=None, busy=False):
        if busy:
            self._busy = True
            self._set_actions_enabled(False)
        self._bg_tasks += 1

        def runner():
            try:
                result = job()
            except Exception as e:
                result = e
            self._read_queue.put(("done", (on_done, result, busy), None))

        threading.Thread(target=runner, daemon=True).start()
        self._poll_read_queue()

    def _poll_read_queue(self):
        # 走查 E1-03：screen_action 分支直接调 screen_on/off → run_bg 内又同步调
        # _poll_read_queue，每次重入都会多挂一条 after(150)，链数随调用次数
        # 线性增长且永不回落。用 _polling 护栏保证同时只有一条链。
        if getattr(self, "_polling", False):
            return
        self._polling = True
        try:
            self._poll_read_queue_inner()
        finally:
            self._polling = False

    def _poll_read_queue_inner(self):
        while True:
            try:
                kind, payload, _extra = self._read_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "screen_action":
                (self.screen_off if payload == "off" else self.screen_on)()
                continue
            if kind != "done":
                continue
            on_done, result, busy = payload
            self._bg_tasks = max(0, self._bg_tasks - 1)
            if busy:
                self._busy = False
                self._busy_action = None
                self._set_actions_enabled(True)
            try:
                if on_done is not None:
                    on_done(result)
            finally:
                # 忙时排队的屏幕请求：当前动作一结束就补执行（只一次）
                if busy:
                    self._dispatch_pending_screen()
        if self._bg_tasks > 0 or self._relight_pending:
            self.root.after(150, self._poll_read_queue)





































    def _on_close(self):
        if self.tray is not None:
            # 倒计时中进行时最小化：不让它在用户看不到界面时偷偷恢复
            if getattr(self, "_restore_after", None) is not None:
                self.cancel_restore_default()
            self.root.withdraw()
            self.status_var.set("已最小化到托盘（右下角图标，右键可退出）")
            return
        self.root.destroy()

    # ---------- 启动自检（只校验任务，不执行业务动作） ----------
    def _report_tk_error(self, exc, val, tb):
        """Tk 回调异常兜底：windowed 下 stderr 不可见，必须落盘并提示。"""
        detail = "".join(traceback.format_exception(exc, val, tb))
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(ERROR_FILE, "a", encoding="utf-8") as f:
                f.write("%s\n%s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), detail))
        except Exception:
            pass
        short = "%s: %s" % (getattr(exc, "__name__", "错误"), val)
        try:
            _write_event_log("界面异常 %s" % short)
        except Exception:
            pass
        try:
            self.status_var.set("界面异常：%s（详见 error.log）" % short)
        except Exception:
            pass

    def _warn_config_error(self):
        if self._config_error:
            self.status_var.set("配置文件读取失败，已使用默认值")
            self.msg.showwarning("配置文件有问题", self._config_error)
























# ============================================================
# 八、入口
# ============================================================
# ---- 开机自启动（HKCU Run 键，免管理员）------------------------------------








# ---- 单实例（命名互斥体；重复启动唤起已有窗口后退出）------------------------
# ⚠️ 只约束 GUI：CLI 模式（--apply-schedule 等计划任务动作）不获取互斥体，
#    否则托盘常驻时定时任务会被自己拦死。
_MUTEX_NAME = "MonitorBrightness_SingleInstance"
_mutex_handle = None


def acquire_single_instance(name=_MUTEX_NAME):
    """尝试获取单实例互斥体。返回 True=本进程是首个实例。

    已有实例时：尝试把它的主窗口恢复并置前，然后返回 False（调用方应退出）。
    name 参数供测试注入专用互斥体名（默认用全局单实例名）。
    互斥体随进程退出自动释放（崩溃残留的 abandoned 状态不影响下次创建）。
    """
    global _mutex_handle
    if os.name != "nt":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        _activate_existing_window()
        if handle:
            k32.CloseHandle(handle)
        return False
    _mutex_handle = handle                      # 保引用，防止句柄被 GC 关闭
    return True


def _activate_existing_window():
    """把已运行实例的主窗口（按标题前缀枚举，含托盘隐藏态）恢复并置前。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith(APP_TITLE):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, 9)              # SW_RESTORE
        user32.SetForegroundWindow(hwnd)






def main():
    args = sys.argv[1:]
    # v2.01.20：不自动迁移旧配置（用户决策：每台电脑各用各的）。
    # 旧 APPDATA 文件只在界面上提示一句，由用户决定是否手动拷贝。
    _migrate_old_logs()
    dlog("app.start", ver=APP_VERSION, argv=args,
         packaged=bool(getattr(sys, "frozen", False)),
         py="%d.%d.%d" % sys.version_info[:3])
    if args:
        cmd = args[0]
        if cmd == "--diag-log":
            # 诊断日志（临时命令，v2.01.4）：直接打印最近若干行
            lines = diag_dump(200)
            _out("diag.log：%s（%d 行）" % (_diag_file(), len(lines)))
            for line in lines:
                _out(line)
            return
        if cmd == "--diag-export":
            # 导出诊断包（临时命令）：日志 + 配置打成一个 zip
            path, err = diag_export()
            _out(err or ("已导出：%s" % path))
            return
        if cmd == "--set" and len(args) >= 3:
            cli_set(args[1], args[2])
            return
        if cmd == "--apply-schedule":
            cli_apply_schedule()
            return
        if cmd == "--screen" and len(args) >= 2:
            arg = args[1].strip().lower()
            if arg == "status":
                # 与界面同一套结论：当场读、不读任何记账（v2.01.0）
                v = screen_verdict(load_config())
                _out(v["text"])
                if v.get("detail"):
                    _out("  " + v["detail"])
                return
            action = parse_screen_action(args[1])
            if action is None:
                _out("--screen 参数需为 off 或 on")
                sys.exit(2)
            cli_screen(action)
            return
        if cmd == "--list":
            cli_list()
            return
        if cmd == "--probe":
            cli_probe()
            return
        if cmd == "--diag":
            cli_diag()
            return
        if cmd == "--selftest":
            cli_selftest()
            return
        if cmd == "--ensure-schedule":
            cli_ensure_schedule()
            return
    if not acquire_single_instance():
        return                                  # 已有实例：已唤起其窗口，直接退出
    if not PIL_OK:
        # GUI 明确报错退出；CLI 路径（--set/--apply-schedule/--diag/--selftest）不受影响
        from tkinter import messagebox
        messagebox.showerror(
            "缺少 Pillow",
            "图形界面需要 Pillow（用于圆角抗锯齿贴图），当前环境没有安装。\n\n"
            "命令行功能仍可正常使用：--set / --list / --probe / --diag / --selftest / "
            "--apply-schedule。\n安装后重试：pip install pillow")
        return
    root = tk.Tk()
    apply_tk_scaling(root)          # 必须在建控件之前：字体按新比例算
    install_wheel_router(root)      # 滚轮全局路由：只装这一次，谁都不许 unbind_all
    App(root, start_minimized=("--minimized" in args))
    apply_dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
