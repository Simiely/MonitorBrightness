# -*- coding: utf-8 -*-
"""R4-3：拆分 _ScreenActions，并把 4 个界面纯函数沉到 app/common.py。

- app/brightness.py  : _BrightnessActions（选择/亮度/预设/探测，15 方法）
- app/screen_power.py: _ScreenPowerActions（开关屏/排队/回调，12 方法）
- app/common.py      : status_payload / row_action_blocker / auto_relight_minutes /
                       selection_payload

所有方法与函数用 AST dump 逐字核对；`_finish_apply` 实质是任务重建回调，
暂留 screen_power，R4-4 迁 _ScheduleActions 时再归 app/schedule.py。
"""
import ast
from pathlib import Path

MAIN = Path("monitor_brightness.py")
APP = Path("app")
COMMON = APP / "common.py"
BRIGHT = APP / "brightness.py"
POWER = APP / "screen_power.py"

src = MAIN.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src, filename=str(MAIN))


def find_func(name):
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "_ScreenActions")
methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
helpers = {n: find_func(n) for n in (
    "status_payload", "row_action_blocker", "auto_relight_minutes",
    "selection_payload")}

BRIGHTNESS = [
    "apply_presets_from_settings", "select_all", "select_none",
    "on_monitor_toggle", "refresh_monitors", "_set_actions_enabled",
    "read_current", "refresh_brightness", "apply_now", "_finish_apply_level",
    "apply_preset", "_finish_preset", "probe_screens",
    "_update_hint_brightness", "_status_payload",
]
POWER_METHODS = [
    "_row_action_blocker", "monitor_off", "monitor_on",
    "_queue_screen_action", "_dispatch_pending_screen", "screen_off",
    "_finish_screen_off", "screen_on", "_cancel_relight_timer",
    "_finish_screen_on", "_auto_relight_minutes", "_finish_apply",
]
assert set(BRIGHTNESS) | set(POWER_METHODS) == set(methods), "方法清单与类不一致"
assert not (set(BRIGHTNESS) & set(POWER_METHODS)), "方法重复分配"


def source(node):
    return "".join(lines[node.lineno - 1:node.end_lineno])


def dump(node):
    return ast.dump(node, include_attributes=False)


common_header = '''# -*- coding: utf-8 -*-
"""界面控制器共用的小纯函数（R4-3，从 monitor_brightness.py 逐字搬出）。"""
from core.config import normalize_indices
from hw.ddc import enumerate_targets, release_targets


'''

bright_header = '''# -*- coding: utf-8 -*-
"""亮度 / 显示器选择控制器（R4-3，从 _ScreenActions 逐字拆出）。

self.xxx 仍指向最终的 App 实例；跨 Mixin 回调由 App 多继承解析。
"""
from core.util import _safe_int
from domain.decisions import _short_dev
from domain.timeutil import clamp_level
from hw.ccd import ccd_probe_topology
from hw.ddc import (
    enumerate_targets, get_brightness_many, probe_states, release_targets,
    set_brightness_many,
)
from hw.topology import topology_inactive_rows
from schedule import _as_ok_errs
from ui.sheets import SettingsSheet
from app.common import status_payload


class _BrightnessActions:
    """显示器选择、亮度下发、快捷预设与状态探测。"""

'''

power_header = '''# -*- coding: utf-8 -*-
"""逐台开关屏 / 忙时排队控制器（R4-3，从 _ScreenActions 逐字拆出）。

`_finish_apply` 实质是任务重建完成回调，暂留本模块，R4-4 迁
_ScheduleActions 时一并归到 app/schedule.py。其它 self.xxx 仍由 App
多继承解析，跨 Mixin 调用不需要 import。
"""
import threading

from core.config import save_config
from core.logging import _write_event_log, dlog
from hw.power import take_wake_info
from schedule import _as_ok_errs
from service import screen_off_cfg, screen_on_row, screen_wake_entry, selection_anchors
from app.common import auto_relight_minutes, row_action_blocker


class _ScreenPowerActions:
    """熄灭 / 点亮、逐台动作、忙时请求排队与自动复亮。"""

'''

common_src = common_header + "\n\n\n".join(
    source(helpers[n]).rstrip() for n in (
        "status_payload", "row_action_blocker", "auto_relight_minutes",
        "selection_payload")) + "\n"
bright_src = bright_header + "\n\n".join(source(methods[n]).rstrip() for n in BRIGHTNESS) + "\n"
power_src = power_header + "\n\n".join(source(methods[n]).rstrip() for n in POWER_METHODS) + "\n"

# AST 逐字校验
common_tree = ast.parse(common_src)
bright_tree = ast.parse(bright_src)
power_tree = ast.parse(power_src)


def module_func(tree, name):
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


def module_cls(tree, name):
    return next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == name)


def cls_method(tree, cls_name, method_name):
    return next(m for m in module_cls(tree, cls_name).body
                if isinstance(m, ast.FunctionDef) and m.name == method_name)


for name, node in helpers.items():
    assert dump(node) == dump(module_func(common_tree, name)), "%s 搬移不一致" % name
for name in BRIGHTNESS:
    assert dump(methods[name]) == dump(
        cls_method(bright_tree, "_BrightnessActions", name)), "%s 搬移不一致" % name
for name in POWER_METHODS:
    assert dump(methods[name]) == dump(
        cls_method(power_tree, "_ScreenPowerActions", name)), "%s 搬移不一致" % name

# ── 主文件删节点（倒序） ──────────────────────────────────────────────
for node in sorted(list(helpers.values()) + [cls],
                   key=lambda n: n.lineno, reverse=True):
    del lines[node.lineno - 1:node.end_lineno]
new_main = "".join(lines)

old_import = ("from app.settings import (             # noqa: E402\n"
              "    TRAY_OK, _SettingsActions, _TrayIcon, _TrayItem, _TrayMenu,\n"
              "    tray_tooltip_text,\n"
              ")\n")
new_import = old_import + (
    "from app.common import (             # noqa: E402\n"
    "    auto_relight_minutes, row_action_blocker, selection_payload, status_payload,\n"
    ")\n"
    "from app.brightness import _BrightnessActions   # noqa: E402\n"
    "from app.screen_power import _ScreenPowerActions  # noqa: E402\n")
assert old_import in new_main, "app.settings import 锚点未找到"
new_main = new_main.replace(old_import, new_import, 1)

old_bases = "class App(_ScreenActions, _ScheduleActions, _LayoutActions,\n"
new_bases = "class App(_BrightnessActions, _ScreenPowerActions, _ScheduleActions,\n          _LayoutActions,\n"
assert old_bases in new_main, "App 基类行未找到"
new_main = new_main.replace(old_bases, new_bases, 1)

# ── 落盘 ──────────────────────────────────────────────────────────────
COMMON.write_text(common_src, encoding="utf-8")
BRIGHT.write_text(bright_src, encoding="utf-8")
POWER.write_text(power_src, encoding="utf-8")
MAIN.write_text(new_main, encoding="utf-8")

check = ast.parse(new_main)
assert not any(isinstance(n, ast.ClassDef) and n.name == "_ScreenActions"
               for n in check.body), "主文件残留 _ScreenActions"
for name in helpers:
    assert not any(isinstance(n, ast.FunctionDef) and n.name == name
                   for n in check.body), "主文件残留 %s" % name

print("R4-3 拆分完成：4 个纯函数 + 27 个方法 AST 逐字一致")
print("  monitor_brightness.py %d 行" % len(new_main.splitlines()))
print("  app/common.py         %d 行" % len(common_src.splitlines()))
print("  app/brightness.py     %d 行" % len(bright_src.splitlines()))
print("  app/screen_power.py   %d 行" % len(power_src.splitlines()))
