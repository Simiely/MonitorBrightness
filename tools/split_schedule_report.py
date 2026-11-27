# -*- coding: utf-8 -*-
"""R3：从 schedule.py 拆出状态/格式化辅助到 schedule_report.py。"""
import ast
from pathlib import Path

SRC = Path("schedule.py")
DST = Path("schedule_report.py")

NAMES = [
    "TASK_SCHEDULE", "TASK_CATCHUP", "_LEGACY_TASKS", "TASK_SHORT",
    "_boundary_to_minutes", "_boundary_minutes_all", "_next_run_text",
    "_days_by_min_from_fp", "_as_ok_errs", "_task_result_text",
    "_runtime_fields", "_task_info_next", "task_summary",
]

HEADER = '''# -*- coding: utf-8 -*-
"""任务状态与确认文案（v2.03.0 R3，从 schedule.py 逐字拆出）。

只做文本解析/格式化；任务注册与脚本生成在 schedule.py，依赖方向单向。
"""
import re
from datetime import datetime, timezone

from core.util import _short_dt
from domain.timeutil import (
    WEEKDAY_LABELS, WEEKDAY_NAMES_PS, normalize_days, parse_hhmm,
)

'''

IMPORT = '''from schedule_report import (
    TASK_CATCHUP, TASK_SCHEDULE, TASK_SHORT, _LEGACY_TASKS,
    _as_ok_errs, _boundary_minutes_all, _boundary_to_minutes,
    _days_by_min_from_fp, _next_run_text, _runtime_fields,
    _task_info_next, _task_result_text, task_summary,
)
'''


def main():
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    nodes = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            nodes[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    nodes[t.id] = n

    picked, ranges = [], []
    for nm in NAMES:
        n = nodes.get(nm)
        if n is None:
            raise SystemExit("缺节点: %s" % nm)
        start = n.lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        picked.append((n.lineno, "".join(lines[start:n.end_lineno])))
        ranges.append((start, n.end_lineno))
    picked.sort()
    DST.write_text(HEADER + "\n\n".join(c.rstrip("\n") for _, c in picked) + "\n",
                   encoding="utf-8", newline="\n")

    for a, b in sorted(ranges, reverse=True):
        del lines[a:b]
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("from service import "))
    lines.insert(idx + 1, IMPORT)
    SRC.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("schedule_report：搬移 %d 个节点" % len(NAMES))


if __name__ == "__main__":
    main()
