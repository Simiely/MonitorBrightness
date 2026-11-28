# -*- coding: utf-8 -*-
"""P1-5：把 app/base.py 的 _run_cli 逐字搬到 app/cli_entry.py。"""
import ast
from pathlib import Path

BASE = Path("app/base.py")
OUT = Path("app/cli_entry.py")

src = BASE.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src, filename=str(BASE))
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_cli")
block = "".join(lines[fn.lineno - 1:fn.end_lineno]).rstrip()

header = '''# -*- coding: utf-8 -*-
"""CLI 分流（P1-5，从 app/base.py 逐字搬出）。

只处理 `--set/--list/--probe/--diag/--screen/--selftest/--ensure-schedule`
这些无界面命令；GUI 启动仍在 app/base.py。
"""

import sys

from cli import (
    cli_apply_schedule, cli_diag, cli_ensure_schedule, cli_list, cli_probe,
    cli_screen, cli_selftest, cli_set, parse_screen_action,
)
from core.config import load_config
from core.logging import _diag_file, diag_dump, diag_export
from core.util import _out
from hw.ccd import screen_verdict


'''
out_src = header + block + "\n"
OUT.write_text(out_src, encoding="utf-8")

out_tree = ast.parse(out_src, filename=str(OUT))
new = next(n for n in out_tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_cli")
assert ast.dump(fn, include_attributes=False) == ast.dump(
    new, include_attributes=False), "_run_cli 搬移不一致"

# 从 base 删除；base 顶部改为 import，保证 app_base_mod._run_cli 仍可用
del lines[fn.lineno - 1:fn.end_lineno]
new_base = "".join(lines).rstrip() + "\n"
new_base = new_base.replace(
    "from app.layout import _LayoutActions\n",
    "from app.layout import _LayoutActions\nfrom app.cli_entry import _run_cli\n",
    1,
)
BASE.write_text(new_base, encoding="utf-8")
print("moved _run_cli (%d lines)" % (fn.end_lineno - fn.lineno + 1))
