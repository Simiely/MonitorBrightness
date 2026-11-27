# -*- coding: utf-8 -*-
"""R4-5：把 App 装配、单实例、滚轮路由与 main() 搬到 app/base.py。

主入口只保留 DPI 声明、兼容再导出和 `main()` 调用。所有被搬节点 AST dump
一致才落盘；import 用 ast.unparse 生成，避免手抄漏名字。
"""
import ast
from pathlib import Path

MAIN = Path("monitor_brightness.py")
BASE = Path("app/base.py")

src = MAIN.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src, filename=str(MAIN))

MOVE_NAMES = [
    "_migrate_old_logs", "_raster_line", "_route_wheel", "install_wheel_router",
    "App", "_MUTEX_NAME", "_mutex_handle", "acquire_single_instance",
    "_activate_existing_window", "main",
]

moved = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in MOVE_NAMES:
        moved.append(node)
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in MOVE_NAMES:
                moved.append(node)
                break
assert len(moved) == len(MOVE_NAMES), "搬移节点数量不符：%d" % len(moved)

imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
import_text = "\n".join(ast.unparse(n) for n in imports)

base_header = '''# -*- coding: utf-8 -*-
"""应用装配与入口编排（R4-5，从 monitor_brightness.py 逐字搬出）。"""

'''
base_src = base_header + import_text + "\n\n\n" + "\n\n\n".join(
    "".join(lines[n.lineno - 1:n.end_lineno]).rstrip() for n in moved) + "\n"

base_tree = ast.parse(base_src)
for node in moved:
    name = node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else None
    if name:
        new = next(n for n in base_tree.body
                   if isinstance(n, type(node)) and n.name == name)
        assert ast.dump(node, include_attributes=False) == ast.dump(
            new, include_attributes=False), "%s 搬移不一致" % name
    else:
        target = next(t.id for t in node.targets if isinstance(t, ast.Name)
                      and t.id in MOVE_NAMES)
        new = next(n for n in base_tree.body
                   if isinstance(n, ast.Assign) and any(
                       isinstance(t, ast.Name) and t.id == target for t in n.targets))
        assert ast.dump(node, include_attributes=False) == ast.dump(
            new, include_attributes=False), "%s 搬移不一致" % target

# ── 重建主入口 ────────────────────────────────────────────────────────
dpi = next(n for n in tree.body if isinstance(n, ast.Try)
           and 39 <= n.lineno <= 46)
dpi_src = "".join(lines[dpi.lineno - 1:dpi.end_lineno]).rstrip()

main_src = (
    '#!/usr/bin/env python3\n'
    '# -*- coding: utf-8 -*-\n'
    '"""MonitorBrightness 入口 shim（v2.03.0 R4-5）。\n\n'
    '真正的装配/CLI 分流在 app/base.py；本文件只负责 DPI 声明、兼容再导出和调用。\n'
    '"""\n'
    '# ── DPI 声明：必须在 import tkinter 之前 ────────────────────────────────\n'
    + dpi_src + '\n\n'
    + 'from app.base import (\n'
    '    App, _activate_existing_window, _migrate_old_logs, _raster_line,\n'
    '    _route_wheel, acquire_single_instance, install_wheel_router, main,\n'
    ')\n\n'
    '# 兼容再导出：旧代码/测试仍按 monitor_brightness.xxx 访问这些名字。\n'
    + import_text + '\n\n\n'
    'if __name__ == "__main__":\n'
    '    main()\n'
)

# 用重建版覆盖；主文件不再保留任何业务节点，只留 shim。
BASE.write_text(base_src, encoding="utf-8")
MAIN.write_text(main_src, encoding="utf-8")

check_main = ast.parse(main_src)
assert not any(isinstance(n, ast.ClassDef) and n.name == "App"
               for n in check_main.body), "主文件残留 App"
for name in ("main", "_migrate_old_logs", "acquire_single_instance"):
    assert not any(isinstance(n, ast.FunctionDef) and n.name == name
                   for n in check_main.body), "主文件残留 %s" % name

print("R4-5 搬运完成：%d 个节点 AST 逐字一致" % len(moved))
print("  monitor_brightness.py %d 行" % len(main_src.splitlines()))
print("  app/base.py           %d 行" % len(base_src.splitlines()))
