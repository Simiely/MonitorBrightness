# -*- coding: utf-8 -*-
"""P1-5：把 _LayoutActions 里的 3 个拓扑动作逐字搬到 app/topology_actions.py。"""
import ast
from pathlib import Path

LAYOUT = Path("app/layout.py")
OUT = Path("app/topology_actions.py")
NAMES = ["_ask_heir", "topology_enable", "_finish_topology_enable"]

src = LAYOUT.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src, filename=str(LAYOUT))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
           and n.name == "_LayoutActions")
methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
assert len(methods) == len(NAMES), [m.name for m in methods]

blocks = ["".join(lines[m.lineno - 1:m.end_lineno]).rstrip() for m in methods]
header = '''# -*- coding: utf-8 -*-
"""拓扑动作（P1-5，从 app/layout.py 逐字搬出）。

只放“断开后接回 / 交接主屏”三个 GUI 动作；布局快照与型号读取仍归 app/layout.py。
"""

from core.config import save_config
from core.logging import dlog
from hw.topology import topology_off
from schedule_report import _as_ok_errs
from service import topology_enable_anchor
from ui.sheets import HeirSheet
from app.layout import topology_heir_candidates


class _TopologyActions:
    """断开屏的接回、主屏交接与完成回调。"""

'''
out_src = header + "\n\n".join(blocks) + "\n"
OUT.write_text(out_src, encoding="utf-8")

# 逐字校验（方法 AST dump 一致；缩进仍为类内 4 空格）
out_tree = ast.parse(out_src, filename=str(OUT))
out_cls = next(n for n in out_tree.body if isinstance(n, ast.ClassDef)
               and n.name == "_TopologyActions")
for old, new in zip(methods, [n for n in out_cls.body if isinstance(n, ast.FunctionDef)]):
    assert ast.dump(old, include_attributes=False) == ast.dump(
        new, include_attributes=False), old.name

# 从 layout 删除（倒序）
for m in sorted(methods, key=lambda n: n.lineno, reverse=True):
    del lines[m.lineno - 1:m.end_lineno]
LAYOUT.write_text("".join(lines).rstrip() + "\n", encoding="utf-8")
print("moved %d methods: %s" % (len(methods), ", ".join(NAMES)))
