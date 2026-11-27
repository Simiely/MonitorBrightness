# -*- coding: utf-8 -*-
"""R4-2：把托盘文案函数、_SettingsActions、_DiagActions 逐字搬到 app/ 包。

app/settings.py 只在模块级补 import 与可选 pystray 守卫；节点本体用 AST dump
核对，不一致即抛错，不落盘。主文件保留再导出垫片。
"""
import ast
from pathlib import Path

MAIN = Path("monitor_brightness.py")
APP = Path("app")
DIAG = APP / "diag.py"
SETTINGS = APP / "settings.py"

src = MAIN.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src, filename=str(MAIN))


def find_func(name):
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


def find_class(name):
    return next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == name)


tray_func = find_func("tray_tooltip_text")
settings_cls = find_class("_SettingsActions")
diag_cls = find_class("_DiagActions")


def source(node):
    return "".join(lines[node.lineno - 1:node.end_lineno])


def dump(node):
    return ast.dump(node, include_attributes=False)


# ── 新模块源码 ────────────────────────────────────────────────────────
diag_header = '''# -*- coding: utf-8 -*-
"""诊断动作（v2.03.0 R4-2，从 monitor_brightness.py 逐字搬出）。"""
import os
import subprocess

from core.logging import diag_export
from core.paths import LOG_DIR


'''

settings_header = '''# -*- coding: utf-8 -*-
"""设置写回与托盘控制器（v2.03.0 R4-2，从 monitor_brightness.py 逐字搬出）。

缺 pystray 时 GUI 照常工作，仅没有托盘/最小化到托盘能力。
"""
import threading

try:
    import pystray
    from pystray import Icon as _TrayIcon, Menu as _TrayMenu, MenuItem as _TrayItem
    TRAY_OK = True
except Exception:
    _TrayIcon = _TrayMenu = _TrayItem = None
    TRAY_OK = False

from core.config import APP_TITLE, coerce_setting, save_config, set_autostart
from core.logging import dlog, diag_set_enabled
from domain.timeutil import parse_hhmm
from schedule import (
    _days_by_min_from_fp, _next_run_text, _slot_trigger_fingerprint, task_target,
)


'''

diag_src = (diag_header + source(diag_cls).rstrip() + "\n")
settings_src = (settings_header + source(tray_func).rstrip() + "\n\n\n"
                + source(settings_cls).rstrip() + "\n")

# ── 校验新模块节点逐字一致 ────────────────────────────────────────────
diag_tree = ast.parse(diag_src)
settings_tree = ast.parse(settings_src)
new_diag_cls = next(n for n in diag_tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "_DiagActions")
new_settings_cls = next(n for n in settings_tree.body
                        if isinstance(n, ast.ClassDef) and n.name == "_SettingsActions")
new_tray_func = next(n for n in settings_tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == "tray_tooltip_text")
assert dump(diag_cls) == dump(new_diag_cls), "_DiagActions 搬移不一致"
assert dump(settings_cls) == dump(new_settings_cls), "_SettingsActions 搬移不一致"
assert dump(tray_func) == dump(new_tray_func), "tray_tooltip_text 搬移不一致"

# ── 从主文件删除三个节点（倒序删，避免行号漂移） ──────────────────────
for node in sorted((tray_func, settings_cls, diag_cls),
                   key=lambda n: n.lineno, reverse=True):
    del lines[node.lineno - 1:node.end_lineno]
new_main = "".join(lines)

old_ui = ("from ui.panels import (             # noqa: E402\n"
          "    BrightnessPanel, MonitorPanel, SchedulePanel, StatusCard,\n"
          ")\n")
new_ui = old_ui + (
    "from app.diag import _DiagActions      # noqa: E402\n"
    "from app.settings import (             # noqa: E402\n"
    "    TRAY_OK, _SettingsActions, _TrayIcon, _TrayItem, _TrayMenu,\n"
    "    tray_tooltip_text,\n"
    ")\n")
assert old_ui in new_main, "主文件 ui.panels import 锚点未找到"
new_main = new_main.replace(old_ui, new_ui, 1)

old_tray = ("# 托盘（可选依赖）：缺 pystray 时 GUI 照常工作，仅没有托盘/最小化到托盘能力\n"
            "try:\n"
            "    import pystray\n"
            "    from pystray import Icon as _TrayIcon, Menu as _TrayMenu, MenuItem as _TrayItem\n"
            "    TRAY_OK = True\n"
            "except Exception:\n"
            "    _TrayIcon = _TrayMenu = _TrayItem = None\n"
            "    TRAY_OK = False\n")
new_tray = ("# 托盘（可选依赖）已随 _SettingsActions 沉到 app/settings.py；\n"
            "# 上方 from app.settings import 再导出，主文件旧引用保持不变。\n")
assert old_tray in new_main, "主文件托盘守卫块未找到"
new_main = new_main.replace(old_tray, new_tray, 1)

# ── 落盘 ──────────────────────────────────────────────────────────────
APP.mkdir(exist_ok=True)
(APP / "__init__.py").write_text(
    '# -*- coding: utf-8 -*-\n"""应用控制器层：只做编排，硬件细节在 hw/service。"""\n',
    encoding="utf-8")
DIAG.write_text(diag_src, encoding="utf-8")
SETTINGS.write_text(settings_src, encoding="utf-8")
MAIN.write_text(new_main, encoding="utf-8")

check_main = ast.parse(new_main)
for name in ("tray_tooltip_text",):
    assert not any(isinstance(n, ast.FunctionDef) and n.name == name
                   for n in check_main.body), "主文件残留 %s" % name
for name in ("_SettingsActions", "_DiagActions"):
    assert not any(isinstance(n, ast.ClassDef) and n.name == name
                   for n in check_main.body), "主文件残留 %s" % name

print("app/settings.py + app/diag.py 搬运完成：3 个节点 AST 逐字一致")
print("  monitor_brightness.py %d 行" % len(new_main.splitlines()))
print("  app/settings.py       %d 行" % len(settings_src.splitlines()))
print("  app/diag.py           %d 行" % len(diag_src.splitlines()))
