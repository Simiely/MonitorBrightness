#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MonitorBrightness 入口 shim（v2.03.0 R4-5）。

真正的装配/CLI 分流在 app/base.py；本文件只负责 DPI 声明、兼容再导出和调用。
"""
# ── DPI 声明：必须在 import tkinter 之前 ────────────────────────────────
try:
    import ctypes as _dpi_ctypes
    try:
        _dpi_ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                    # Win7 没有 shcore
        _dpi_ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

from app.base import (
    App, _activate_existing_window, _migrate_old_logs, _raster_line,
    _route_wheel, acquire_single_instance, install_wheel_router, main,
)

# 兼容再导出：旧代码/测试仍按 monitor_brightness.xxx 访问这些名字。
import sys
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
from domain.decisions import _short_dev, ccd_friendly_name, is_vdd_index, plan_model_updates, restore_selection, short_dev, _GENERIC_DESC, display_name_for_row, is_vdd_row
from domain.timeutil import WEEKDAY_LABELS, WEEKDAY_NAMES_PS, WEEKDAY_PRESETS, clamp_level, days_label, normalize_days, normalize_time_input, parse_hhmm, shift_time, slot_matches_weekday, slots_for_weekday
from core.paths import APP_DIR, CONFIG_DIR, CONFIG_FILE, ERROR_FILE, EVENTS_FILE, LOG_DIR, _LEGACY_CONFIG_DIR, _app_dir, _dir_writable, _pick_log_dir
from core.util import _err, _out, _safe_int, _short_dt
import core.config as _config
from core import state as _core_state
from core.config import APP_TITLE, APP_VERSION, AUTOSTART_RUN_NAME, CONFIG_VERSION, DEFAULT_SLOTS, OFF_METHODS, OFF_METHOD_LABEL, SETTINGS, SETTING_BY_KEY, _as_iterable, _atomic_write_text, _autostart_path, _backup_bad_config, _coerce_ids, _coerce_levels, _coerce_positions, _coerce_slots, _load_model_cache, _split_spec, _to_index, _valid_hhmm, coerce_setting, default_config, get_autostart, load_config, normalize_indices, old_config_present, save_config, set_autostart
from core.logging import _DIAG_LIMIT, _EVENT_LOCK, _diag_file, _write_event_log, dlog, diag_dump, diag_enabled, diag_export, diag_set_enabled, read_recent_events, read_session_events
from hw.ddc import PHYSICAL_MONITOR, _dxva2, _read_vcp, _write_vcp, enumerate_targets, release_targets, _select, _capabilities, _DISPLAY_DEVICE, _display_uid, _display_key, _capabilities_cached, _caps_model, _caps_inputs, _caps_power, monitor_model, monitor_label, _luminance_max, _raw_from_percent, set_brightness_many, get_brightness_many, probe_states, _idle_seconds, _human_gap, NO_MONITOR_MSG, VCP_BRIGHTNESS, VCP_INPUT_SOURCE, VCP_POWER_MODE, POWER_ON, POWER_STANDBY, POWER_SUSPEND, POWER_SOFT_OFF, POWER_OFF, POWER_LABEL, _DXVA2_READY, _EDID_CACHE, _MAX_LUM
from hw.ccd_structs import _CcdLuid, _CcdRational, _CcdSourceInfo, _CcdTargetInfo, _CcdPathInfo, _CcdPoint, _CcdSourceMode, _CcdModeInfo, _CcdInfoHeader, _CcdSourceName, _CcdTargetName, _Ccd2D, _CcdVideoSignalInfo, _CcdTargetPreferredMode, _QDC_ALL_PATHS, _QDC_VIRTUAL_MODE_AWARE, _CCD_PATH_ACTIVE, _CCD_GET_SOURCE_NAME, _CCD_GET_TARGET_NAME, _DC_GET_TARGET_PREFERRED_MODE, _CCD_PIXELFORMAT_NONGDI, _CCD_EDID_IDS_VALID, _CCD_TARGET_IN_USE, _SDC_USE_SUPPLIED, _SDC_APPLY, _SDC_SAVE_TO_DATABASE, _SDC_ALLOW_CHANGES, _SDC_VIRTUAL_MODE_AWARE, _CCD_APPLY_FLAGS, _CCD_MODE_IDX_INVALID, TOPO_VERIFY_TIMEOUT, _CCD_TYPE_SOURCE, _CCD_TYPE_TARGET, _CCD_TYPE_DESKTOP
from hw.ccd import _ccd_setup_probe_api, _ccd_query_all_paths, _ccd_rows_from_paths, _ccd_merge_rows_by_anchor, ccd_probe_topology, topology_state_for, screen_verdict, _ccd_api, _ccd_query_raw, _ccd_slice_bytes, _ccd_make_snapshot, _ccd_apply, _ccd_restore_from_bytes, _ccd_wait_state, _ccd_mode_entries, _ccd_pick_mode
from hw.topology import topology_plan_off, _fold_raw_rows, topology_plan_on, _ccd_preferred_size, _ccd_live_size, _ccd_layout_rects, _ccd_layout_by_anchor, _ccd_free_origin, _ccd_place, _ccd_append_source_mode, _ccd_capture_positions, _ccd_fill_missing_source_modes, _ccd_apply_plan_on, _topo_off_handover_shift, _topo_off_capture_positions, _topo_off_activate_heir, _topo_off_shift_other_modes, _topo_off_detach, _topo_off_apply_and_verify, _topo_off_report_and_follow, topology_off, topology_inactive_rows, topology_indices_for_anchors, topology_wanted_anchors, _ccd_query_raw_rows, topology_on
from hw.power import _broadcast_monitor_power, screen_off_system, screen_on_system, _nudge_user_input, _switch_input_roundtrip, _wake_one, _output_recycle, screen_off_many, screen_wake_many, _monitor_count, _system_ok_targets, _wake_info, reset_wake_info, take_wake_info, pick_flip_source, _fast_flip_recent, _mark_fast_flip, _fast_flip_one, screen_wake_flip, SC_MONITORPOWER, SC_OFF, SC_ON, _WM_SYSCOMMAND, _HWND_BROADCAST, WAKE_STAGE_LABEL, FAST_FLIP_WINDOW_S, _FAST_FLIP_AT, _FAST_FLIP_LOCK, _WAKE_INFO, _WAKE_INFO_LOCK
from service import screen_off_cfg, screen_on_row, screen_on_cfg, screen_wake_entry, topology_enable_anchor, current_level, wake_options, selection_anchors, resolve_monitors
from schedule import TASK_CATCHUP, TASK_SCHEDULE, TASK_SHORT, _LEGACY_TASKS, _NO_WINDOW, TASKS_DIR, _as_ok_errs, _boundary_minutes_all, _boundary_to_minutes, _days_by_min_from_fp, _fp_text, _next_run_text, _ps, _ps_quote, _ps_script, _ps_trigger_line, _read_task_xml, _rebuild_changes, _rebuild_report, _runtime_fields, _same_path, _settings_block, _slot_trigger_fingerprint, _task_action, _task_definition, _task_info_next, _task_result_text, _xml_flag, _xml_trigger_fingerprint, apply_schedule, build_query_script, build_register_script, build_runtime_query_script, build_unregister_script, ensure_task_registration, query_task_runtime, query_task_status, query_task_status_local, register_tasks, task_match_reason, task_signature, task_summary, task_target, unregister_tasks
from cli import parse_level_arg, parse_index_arg, cli_set, cli_apply_schedule, cli_list, cli_probe, cli_diag, cli_ensure_schedule, cli_selftest, parse_screen_action, cli_screen, INPUT_LABEL
from domain.slots import _minutes, _slot_minutes, active_slot, active_slot_dict
from hw.winfollow import _WinRect, _user32, _window_rect, iter_app_windows, win_snapshot, win_follow, _SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE, _GWL_EXSTYLE, _WS_EX_TOOLWINDOW, _WS_EX_NOACTIVATE
from ui.theme import PIL_OK, UI, DARK, FONT_UI, _ui_font, apply_dark_theme, apply_tk_scaling, install_dark_tickbox, _aa_rounded, _aa_circle, _hex_rgba, _aa_sync_epoch, _tickbox_image, _AA_CACHE, _AA_REFS, _AA_EPOCH, _TICKBOX_IMAGES, apply_dark_titlebar
from domain.screen_actions import SCREEN_ACTIONS, SCREEN_LABEL_BY_VALUE, SCREEN_VALUE_BY_LABEL
from ui.widgets import Button, Card, Label, ScrollArea, SectionTitle, Segmented, Slider, Switch, TimeField, Tooltip, WeekdayPicker, _SlotRow
from ui.sheets import DarkBox, HelpSheet, HeirSheet, SettingsSheet, Sheet, SlotEditor
from ui.panels import BrightnessPanel, MonitorPanel, SchedulePanel, StatusCard
from app.diag import _DiagActions
from app.settings import TRAY_OK, _SettingsActions, _TrayIcon, _TrayItem, _TrayMenu, tray_tooltip_text
from app.common import auto_relight_minutes, row_action_blocker, selection_payload, status_payload
from app.brightness import _BrightnessActions
from app.screen_power import _ScreenPowerActions
from app.schedule import _ScheduleActions
from app.layout import DEFAULT_LAYOUT_FILE, _LayoutActions, _SDC_VALIDATE, load_default_layout, remember_layout_snapshot, restore_default_layout, save_default_layout, topology_heir_candidates
from app.topology_actions import _TopologyActions


def __getattr__(name):
    """兼容再导出：R6 后这些名字的可变状态在 core.state，按访问器实时读取。"""
    if name == "CONFIG_LOAD_ERROR":
        return _core_state.get_load_error()
    if name == "_CAPS_CACHE":
        return _core_state.caps_cache()
    if name == "_SESSION_EVENTS":
        return _core_state.session_events()
    if name == "_DIAG_ON":
        return _core_state.get_diag_on()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


if __name__ == "__main__":
    main()
