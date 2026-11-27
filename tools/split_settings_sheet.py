# -*- coding: utf-8 -*-
"""R4-1：把 SettingsSheet 从主文件逐字搬到 ui/sheets.py，并拆 __init__。

拆分只做机械分区：__init__ 里「窗口 / 启动 / 熄屏 / 亮度预设 / 关于」五段
原样成为 _build_* 方法，__init__ 改为按序调用；所有语句用 AST dump 对比，
不一致即抛错，不落盘。
"""
import ast
from pathlib import Path

MAIN = Path("monitor_brightness.py")
SHEETS = Path("ui/sheets.py")

main_text = MAIN.read_text(encoding="utf-8")
sheets_text = SHEETS.read_text(encoding="utf-8")
main_lines = main_text.splitlines(keepends=True)
old_tree = ast.parse(main_text)

cls = next(n for n in old_tree.body
           if isinstance(n, ast.ClassDef) and n.name == "SettingsSheet")
init = next(m for m in cls.body
            if isinstance(m, ast.FunctionDef) and m.name == "__init__")
btn = next(m for m in cls.body
           if isinstance(m, ast.FunctionDef) and m.name == "_build_remember_btn")


def line_start(prefix, lo, hi):
    """在 [lo, hi) 行里找第一条 strip 后以 prefix 开头的行，返回 0-based 下标。"""
    for i in range(lo, hi):
        if main_lines[i].strip().startswith(prefix):
            return i
    raise ValueError("找不到标记行：%s" % prefix)


c0, c1 = cls.lineno - 1, cls.end_lineno          # [c0, c1)
i0, i1 = init.lineno - 1, init.end_lineno
win = line_start('win = self.section("窗口")', i0, i1)
start = line_start('start = self.section("启动")', i0, i1)
off = line_start('off = self.section("熄屏")', i0, i1)
pre = line_start('pre = self.section("亮度预设")', i0, i1)
about = line_start('about = self.section("关于")', i0, i1)
show = line_start("self.show()", about, i1)

SEGMENTS = [
    ("window", "app, cfg", win, start),
    ("startup", "app", start, off),
    ("off", "app, cfg", off, pre),
    ("presets", "app, cfg", pre, about),
    ("about", "app, cfg", about, show),
]

old_segments = {}
for name, _params, a, b in SEGMENTS:
    chunk = "".join(main_lines[a:b]).rstrip() + "\n"
    fake = ast.parse("def _tmp(self, app, cfg):\n" + chunk)
    old_segments[name] = fake.body[0].body

# ── 生成新类源码 ──────────────────────────────────────────────────────
out = []
out.append("class SettingsSheet(Sheet):\n")
out.append('    """设置：托盘 / 自启 / 熄灭方式 / 预设 / 关于（都是"不常改"的东西）。"""\n\n')
out.extend(main_lines[btn.lineno - 1:btn.end_lineno])
out.append("\n")
out.append("    def __init__(self, app):\n")
out.append("        super().__init__(app, \"设置\", \"不常用的开关都在这里，主界面只留日常操作\", width=480)\n")
out.append("        self.app = app\n")
out.append("        cfg = app.cfg\n")
for name, params, _a, _b in SEGMENTS:
    out.append("        self._build_%s(%s)\n" % (name, params))
out.append("        self.show()\n")
for name, params, a, b in SEGMENTS:
    out.append("\n    def _build_%s(self, %s):\n" % (name, params))
    out.extend(main_lines[a:b])

class_src = "".join(out).rstrip() + "\n"
new_tree = ast.parse(class_src)
new_cls = next(n for n in new_tree.body if isinstance(n, ast.ClassDef))
new_btn = next(m for m in new_cls.body
               if isinstance(m, ast.FunctionDef) and m.name == "_build_remember_btn")

def dump(node):
    if isinstance(node, list):
        return [dump(x) for x in node]
    return ast.dump(node, include_attributes=False)

assert dump(btn) == dump(new_btn), "_build_remember_btn 搬移不一致"
for name, _params, _a, _b in SEGMENTS:
    method = next(m for m in new_cls.body
                  if isinstance(m, ast.FunctionDef) and m.name == "_build_" + name)
    assert dump(old_segments[name]) == dump(method.body), "%s 分区搬移不一致" % name

# ── 写回主文件 ────────────────────────────────────────────────────────
main_lines = main_lines[:c0] + main_lines[c1:]
new_main = "".join(main_lines)
old_import = ("from ui.sheets import (             # noqa: E402\n"
              "    DarkBox, HelpSheet, HeirSheet, Sheet, SlotEditor,\n"
              ")\n")
new_import = ("from ui.sheets import (             # noqa: E402\n"
              "    DarkBox, HelpSheet, HeirSheet, SettingsSheet, Sheet, SlotEditor,\n"
              ")\n")
assert old_import in new_main, "主文件 ui.sheets import 块未找到"
new_main = new_main.replace(old_import, new_import, 1)

# ── 写回 ui/sheets.py ─────────────────────────────────────────────────
old_doc = ("SettingsSheet **不在这里** —— 它依赖配置表（SETTINGS / SETTING_BY_KEY /\n"
           "get_autostart），等 domain/config 层拆好再搬。\n")
new_doc = "SettingsSheet 已在 R4-1 从主文件搬入（配置层已就位），__init__ 按分区拆成 _build_*。\n"
assert old_doc in sheets_text, "ui/sheets.py 文档注释未找到"
sheets_text = sheets_text.replace(old_doc, new_doc, 1)

anchor = "from domain.screen_actions import (\n"
assert anchor in sheets_text, "ui/sheets.py import 锚点未找到"
extra_imports = ("from core.config import (\n"
                 "    APP_VERSION, OFF_METHODS, OFF_METHOD_LABEL, SETTING_BY_KEY,\n"
                 "    get_autostart,\n"
                 ")\n"
                 "from core.util import _safe_int\n\n")
sheets_text = sheets_text.replace(anchor, extra_imports + anchor, 1)

old_widgets = ("    Button, Card, Label, SectionTitle, Segmented, TimeField, Tooltip,\n"
               "    WeekdayPicker,\n")
new_widgets = ("    Button, Card, Label, SectionTitle, Segmented, Switch, TimeField, Tooltip,\n"
               "    WeekdayPicker,\n")
assert old_widgets in sheets_text, "ui/sheets.py widgets import 未找到"
sheets_text = sheets_text.replace(old_widgets, new_widgets, 1)
sheets_text = sheets_text.rstrip() + "\n\n\n" + class_src

# ── 落盘 ──────────────────────────────────────────────────────────────
MAIN.write_text(new_main, encoding="utf-8")
SHEETS.write_text(sheets_text, encoding="utf-8")

check_main = ast.parse(new_main)
check_sheets = ast.parse(sheets_text)
assert not any(isinstance(n, ast.ClassDef) and n.name == "SettingsSheet"
               for n in check_main.body), "主文件仍残留 SettingsSheet"
assert any(isinstance(n, ast.ClassDef) and n.name == "SettingsSheet"
           for n in check_sheets.body), "ui/sheets.py 未找到 SettingsSheet"

print("SettingsSheet 搬运完成：AST 分区逐字一致")
print("  monitor_brightness.py %d 行" % len(new_main.splitlines()))
print("  ui/sheets.py          %d 行" % len(sheets_text.splitlines()))
